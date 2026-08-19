---
name: deepflow
description: 用 deepflow 框架（声明式三阶段 pipeline）构建批量自动化工作流：YAML 描述 manifest，Python 写组件，框架管并发 / 重试 / 状态 / 指标。当用户要把"对一批独立 case 重复做同一组操作"的脚本工程化（视频质检、模型推理评测、数据清洗、批量调用 API、A/B 对比 等），或提到 "deepflow"、"manifest.yaml + pipeline"、"preprocess/casewise/postprocess"、"PreprocessComponent"、"CasewiseComponent"、"PostprocessComponent"、"DatasetItem"、"批量并发执行 + 收 metrics" 时使用。即使没明说 deepflow，只要任务形态是"准备一份数据集 → 对每条独立处理 → 汇总出报告"且需要重试 / 并发 / 崩溃可恢复，可以用本 skill 给方案。
---

# deepflow

声明式三阶段 pipeline 执行框架。**核心模型**：

```
Preprocess (1 次)  →  Casewise (per-case, 线程池并发)  →  Postprocess (1 次)
   产出 Iterator         每 case 收 metrics                  汇总 metrics
```

YAML 写 manifest，Python 写组件，框架管 并发 / 重试 / 跳过 / 失败隔离 / 指标持久化。

## 何时该用 deepflow

deepflow 的甜点场景是 **"对 N 条独立 case 跑同一段流程，收每条的 metrics，最后出一份汇总报告"**。三个判断标准：

1. **Case 之间独立**——任意两个 case 的执行不互相依赖（不能用 deepflow 实现"case B 必须等 case A 完成"）。
2. **流程是线性的三段式**——不是复杂分支 DAG。"准备 → 处理 → 汇总" 能套上去。
3. **要并发 + 重试 + 可观测**——纯单线程一次过，写 `for` 循环就够了，不必上 deepflow。

典型场景：视频 / 图片质量评估、模型批量推理 + 评分、批量调用 LLM / 第三方 API、数据集清洗校验、数据标注比对、A/B 模型输出对比、回归测试每个 case 单独打分。

## 何时 *不* 该用

- **复杂分支 DAG / case 之间有依赖**：用 Airflow、Prefect、Dagster。
- **长期运行的调度任务**：deepflow 是 run-once，不带 cron / scheduler。
- **CPU 密集型并行**：casewise 用的是 `ThreadPoolExecutor`，受 GIL 限制。CPU 重活该用 multiprocessing 或在组件内 spawn 子进程。IO 密集（HTTP / 文件 / 子进程）才是甜区。
- **跨 case 共享内存状态**：`ctx.store` 是 per-case 隔离的，不是全局共享。要全局共享只能写文件到 `workspace/`，且要自己加锁。
- **极简一次性脚本**：50 行能搞定的事，上框架是负担。

## 工作流：从需求到可跑的 pipeline

把用户的需求装进 deepflow 的三段式：

| 阶段 | 干什么 | 输出 | 执行方式 |
|------|--------|------|----------|
| **preprocess** | 准备数据集，返回 `Iterator` | `PreprocessOutput(iterator=...)` | 全局一次 |
| **casewise** | 处理单个 case，返回 metrics | `CasewiseOutput(metrics=...)` | 线程池并发 per case |
| **postprocess** | 汇总全量 metrics | `PostprocessOutput` | 全局一次 |

**整条 pipeline 必须恰好一个 preprocess 组件返回 `iterator`**——多个会报错，零个也报错。其余 preprocess 组件可以做副作用（清目录、拉配置）但不能再提交 iterator。

### 推荐目录结构

```
my-pipeline/
  manifest.yaml
  components/
    _shared/              # 多组件复用代码，框架自动加 sys.path
      stages.py
      llm_client.py
    data_fetch.py         # PreprocessComponent
    quality_check.py      # CasewiseComponent
    score_record.py       # CasewiseComponent
    report.py             # PostprocessComponent
```

文件名 → 类名是 **snake_case → PascalCase 自动推断**：`quality_check.py` 默认找 `QualityCheck` 类。要显式指定：`./quality_check.py:MyClass`。

### manifest.yaml 骨架

```yaml
version: "2.0"
name: my-pipeline
workspace: ./workspace
concurrency: 4              # 1-100，IO 密集可拉到 16-32

vars:                       # 组件通过 ctx.vars 访问（只读）
  threshold: 0.85

pipeline:
  preprocess:
    - src: builtin:clean_workspace      # 内置组件，每次从干净状态开始
    - src: ./components/data_fetch.py   # 必须有一个返回 iterator

  casewise:
    - src: ./components/quality_check.py
      config:
        model: resnet50
      retry:
        max_attempts: 3
        delay: 2
        backoff: exponential            # fixed | exponential
    - src: ./components/score_record.py

  postprocess:
    - src: ./components/report.py
```

环境变量 `${VAR_NAME}` 在 YAML 任意位置可用，框架在 schema 校验前递归替换。`deepflow check` / `--dry-run` 都会列出未设置的环境变量。

### 组件骨架（最关键的样板）

```python
from deepflow import (
    PreprocessComponent, CasewiseComponent, PostprocessComponent,
    PreprocessOutput,   CasewiseOutput,   PostprocessOutput,
    PipelineContext,    CaseContext,
    MemoryIterator,     DatasetItem,
    FatalError,
)
from pydantic import BaseModel

class QualityCheck(CasewiseComponent):
    """对单个 case 跑质量评估，返回 score。"""

    class Config(BaseModel):                    # 强烈推荐：声明式 + 自动校验
        model: str
        threshold: float = 0.8

    def __init__(self, config=None):
        super().__init__(config)
        self.cfg = self.Config(**self.config)

    def check_skip(self, ctx: CaseContext) -> bool:
        return (ctx.casespace / "score.json").exists()    # 已跑过就跳过

    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        score = run_model(self.cfg.model, ctx.case.source)
        if score < self.cfg.threshold:
            raise RuntimeError(f"score {score} below {self.cfg.threshold}")
        return CasewiseOutput(metrics={"score": score})
```

**组件规范**（4 条硬约束）：

1. 继承 `PreprocessComponent` / `CasewiseComponent` / `PostprocessComponent`，**stage 类型必须匹配 manifest 的阶段位置**（`deepflow check` 会校验）。
2. `execute()` 必须返回对应的 `*Output`；不要手动构造 `StageResult`。
3. 失败抛 `Exception`，框架按 `retry` 配置重试；抛 `FatalError` 则**不重试且终止整条 pipeline**（认证失败、关键资源不可用才用）。
4. 组件配置走 `manifest.yaml` 的 `config:` 字段，组件内通过 `self.config` 拿到 dict；推荐用内嵌 `Config(BaseModel)` 校验 + 默认值。

完整组件 / Context / 生命周期参考：`references/components.md`。

## 状态怎么传——4 个存储层别搞混

这是 deepflow 用户最常踩的坑：

| 存储 | 作用域 | 持久化 | 用法 | 何时用 |
|------|--------|--------|------|--------|
| `ctx.workspace` | 整条 pipeline | 文件 | `Path` | 跨 stage 传文件、最终产物 |
| `ctx.casespace` | 单个 case 的所有 casewise step | 文件 | `Path`，自动建 | 同一 case 不同 step 之间传中间结果 |
| `ctx.store` | preprocess 内 / 单 case casewise 内 / postprocess 内（**三段独立**） | 内存 | `set/get/has` | 同 stage 内非文件型小对象（DataFrame、handle） |
| `ctx.vars` | 整条 pipeline | 内存 | dict 只读 | manifest 里声明的全局参数 |

**踩坑 #1**：`ctx.store` 在 casewise 阶段是 **per-case 实例**，不能用来跨 case 共享！跨 case 共享必须写 `workspace/` 文件 + 自己加锁。

**踩坑 #2**：跨 stage（preprocess → casewise → postprocess）传数据**只能用文件**（`workspace/`）。`ctx.store` 在三个阶段是三个不同实例。

**踩坑 #3**：postprocess 想读所有 case 的 metrics，**别去解析 `metrics.json` 文件**——直接用 `ctx.metrics_collector.to_dict()` 拿内存快照，结构一致。

## 指标系统

casewise `execute()` 返回 `CasewiseOutput(metrics={...})`，框架做三件事：

1. **当 case 完成立即原子写** `workspace/metrics/case-{id}.json`（崩溃也不丢）
2. **postprocess 阶段** `ctx.metrics_collector.to_dict()` 拿全量
3. **整条结束** 写 `workspace/metrics.json` 汇总

失败的 case 也想保留 partial metrics？给 Exception 挂 `metrics` 属性：

```python
class QualityError(Exception):
    def __init__(self, msg, metrics):
        super().__init__(msg); self.metrics = metrics

raise QualityError("score too low", metrics={"score": 0.3, "reason": "blur"})
```

框架通过 `getattr(error, "metrics", {})` 提取，写进 case 记录。

## 失败行为速查

| 情况 | 框架行为 |
|------|----------|
| `execute()` 正常返回 | SUCCESS |
| `check_skip()` 返回 True | SKIPPED（不算失败） |
| `execute()` 抛普通 Exception | 按 retry 配置重试，耗尽则 **FAILED**，**继续下一个 case** |
| `execute()` 抛 `FatalError` | 不重试，**整条 pipeline 立即终止** |
| preprocess 任一 step FAILED | 整条 pipeline 终止（preprocess 不容忍失败） |
| casewise 某个 case FAILED | 隔离，其他 case 照跑 |

## CLI 速查

```bash
deepflow init -o manifest.yaml          # 生成模板
deepflow check -c manifest.yaml         # 校验 manifest + 组件 + 环境变量
deepflow run   -c manifest.yaml         # 跑（默认 Rich 进度面板）
deepflow run   -c manifest.yaml -v      # 详细日志（带 run_id / case_id 标记）
deepflow run   -c manifest.yaml --dry-run  # 跑完 preprocess 列计划，不进 casewise
deepflow serve -p 8000 -d ./data        # 启动 REST + WebSocket + Web 控制台
```

**最佳实践**：交付前必跑 `check` + `--dry-run`。`check` 验组件能加载、阶段匹配、env vars 就绪；`--dry-run` 真实跑一遍 preprocess 看 case 数量和执行计划。

API 服务 / WebSocket 事件 / 数据目录布局：`references/api.md`。

## 关键设计决策（写组件前先想清楚）

**1. 一个 case 拆几个 casewise step？**
- 步骤之间能独立缓存 / 重试 → 拆开（每个 step 用 `check_skip` 看 casespace 文件存在）。
- 步骤强耦合、中间状态没价值 → 一个 step 内做完。

**2. 数据怎么传给下一步？**
- 大对象（视频、模型输出）→ 写 `casespace/` 文件，下一步读。
- 小对象 + 同 stage → `ctx.store.set/get`。
- 调试便利性优先 → 永远用文件（可以 cat 出来看）。

**3. concurrency 设多少？**
- 纯 IO（API 调用、网络）→ 16-32 起步，看下游限流。
- 调子进程 / 大模型推理 → 等于 GPU/进程数。
- CPU 密集 Python 代码 → concurrency=1 用 multiprocessing 自己开（GIL）。

**4. 重试 backoff 怎么选？**
- 第三方 API / 网络抖动 → `exponential`，`max_attempts: 3-5`。
- 本地子进程偶发 OOM → `fixed`，`max_attempts: 2`。
- 决定性失败（参数错误、文件不存在）→ 不重试（`max_attempts: 1`），靠 `check` 提前拦下。

## 常见组合模式

**Pattern A：清场 → 拉数据 → 处理 → 汇总**

```yaml
preprocess:
  - src: builtin:clean_workspace
  - src: ./components/data_fetch.py
casewise:
  - src: ./components/process.py
postprocess:
  - src: ./components/report.py
```

**Pattern B：缓存友好的多步 casewise**

```yaml
casewise:
  - src: ./components/download.py    # check_skip 看 casespace/raw.bin
  - src: ./components/extract.py     # check_skip 看 casespace/features.json
  - src: ./components/score.py       # 总是跑
```

每步独立缓存，中途崩溃重跑成本低。

**Pattern C：失败 case 也保留中间产物**

casewise 抛带 `metrics` 属性的 Exception，postprocess 用 `metrics_collector.aggregate_errors()` 按错误类型分组。

## 提示词应答风格

用户描述需求时，你应该：

1. **先确认是不是 deepflow 适用形态**（独立 case + 三段式 + 需要并发/重试/可观测），不适合就坦白讲，别硬塞。
2. **先画 stage 划分**：哪些是 preprocess（一次性），哪些是 casewise（per-case），哪些是 postprocess（汇总）。
3. **再给 manifest 骨架 + 组件签名**，组件实现细节按需展开。
4. **最后提醒可观测性 + 失败处理**：`metrics` 字段、`check_skip` 缓存、`FatalError` vs 普通 Exception 的边界。

## 参考文档

- `references/components.md` — 组件基类、Context、Iterator、生命周期、共享模块完整参考
- `references/manifest.md` — manifest.yaml 全字段参考、env var、shared、retry
- `references/api.md` — `deepflow serve` REST API、WebSocket 事件、Web 控制台
- `references/troubleshooting.md` — 常见错误信息映射到根因
- 仓库根目录的 `README.md`、`docs/COMPONENT_DEVELOPMENT.md`、`src/deepflow/` 是事实来源；本 skill 与之冲突时以源码为准。
