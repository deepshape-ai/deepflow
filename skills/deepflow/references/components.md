# 组件参考

deepflow 的所有公开类型从 `deepflow` 顶层包导入：

```python
from deepflow import (
    PreprocessComponent, CasewiseComponent, PostprocessComponent,
    PreprocessOutput,    CasewiseOutput,    PostprocessOutput,
    PipelineContext,     CaseContext,
    BaseIterator,        MemoryIterator,    DatasetItem,
    ContextStore,        FatalError,
)
```

## 三阶段对照

| 阶段 | 基类 | Context | Output（含什么） | 执行方式 | 失败影响 |
|------|------|---------|-----------------|----------|----------|
| preprocess | `PreprocessComponent` | `PipelineContext` | `PreprocessOutput`（含 `iterator`） | 全局一次 | 终止整条 pipeline |
| casewise | `CasewiseComponent` | `CaseContext` | `CasewiseOutput`（含 `metrics`） | ThreadPool per case | 隔离，其他 case 照跑 |
| postprocess | `PostprocessComponent` | `PipelineContext` | `PostprocessOutput` | 全局一次 | 终止整条 pipeline |

## Preprocess 组件

**核心责任**：准备数据集，构造 Iterator 提交给框架。

```python
from deepflow import PreprocessComponent, PreprocessOutput, MemoryIterator, DatasetItem, PipelineContext

class DataFetch(PreprocessComponent):
    def execute(self, ctx: PipelineContext) -> PreprocessOutput:
        items = [
            DatasetItem(id="case-1", source="data/1.mp4", fps=30),
            DatasetItem(id="case-2", source="data/2.mp4", fps=25),
        ]
        return PreprocessOutput(
            message=f"loaded {len(items)} cases",
            iterator=MemoryIterator(items),
        )
```

**约束**：

- 一条 pipeline 必须**恰好一个** preprocess 组件返回非空 `iterator`，多个或零个都会抛 `RuntimeError`。
- 其他 preprocess 组件可以做副作用（清目录、拉远端配置、warm cache），返回 `PreprocessOutput()`（不带 iterator）。
- preprocess 内任一 step 失败都会终止 pipeline——这是和 casewise 不同的设计。

## Casewise 组件

**核心责任**：处理单个 case，返回 metrics。每个 case 在独立线程里跑，同一 case 的所有 step **共享同一个 `casespace` 目录**。

```python
class QualityCheck(CasewiseComponent):
    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        score = analyze(ctx.case.source)
        (ctx.casespace / "result.json").write_text(json.dumps({"score": score}))
        return CasewiseOutput(metrics={"score": score})
```

**约束**：

- 必须线程安全：同一 case 的 steps 串行跑，但**不同 case 并发**，不能用全局可变状态。
- `ctx.store` 是 **per-case** 实例，绝不能用来跨 case 共享。
- 失败抛 `Exception` → 按 retry 重试 → 重试耗尽则该 case FAILED，**继续下一个 case**。
- 抛 `FatalError` → 不重试，**整条 pipeline 立即终止**。

## Postprocess 组件

**核心责任**：读全量 metrics，出汇总产物。

```python
class Report(PostprocessComponent):
    def execute(self, ctx: PipelineContext) -> PostprocessOutput:
        data = ctx.metrics_collector.to_dict()
        # data["cases"]: dict[case_id, {"metrics": ..., "status": ..., "duration_ms": ...}]
        (ctx.workspace / "report.json").write_text(json.dumps(data, indent=2))
        return PostprocessOutput(message="report generated")
```

**注意**：postprocess 任一 step 失败也会终止整条 pipeline（和 preprocess 一致）。

## Context

### `PipelineContext`（preprocess / postprocess）

```python
ctx.workspace            # Path: 工作目录
ctx.vars                 # dict: manifest.yaml 的 vars，所有组件可见，只读
ctx.store                # ContextStore: 内存 KV，preprocess 内 / postprocess 内各自共享
ctx.metrics_collector    # MetricsCollector: 全量指标（postprocess 主用）
```

### `CaseContext`（casewise）

```python
ctx.case                 # DatasetItem: 当前用例（id + 任意自定义字段）
ctx.casespace            # Path: 用例工作目录（自动建好），同 case 所有 step 共享
ctx.vars                 # dict: 同 PipelineContext
ctx.store                # ContextStore: 同一 case 的 casewise step 共享
```

## DatasetItem

只有 `id` 是必填，**其他任意字段可自由扩展**（底层 `extra="allow"`）。

```python
item = DatasetItem(id="001", source="video/1.mp4", fps=30, tags=["smoke"])
item.id      # "001"
item.source  # "video/1.mp4"
item.fps     # 30
item.tags    # ["smoke"]
```

casewise 组件通过 `ctx.case.<field>` 访问。注意：因为是 Pydantic model，如果定义了 `Config(BaseModel)` 强类型 schema，自定义字段也会经 Pydantic 校验。

## 组件配置（强烈推荐 Pydantic）

`self.config` 是从 manifest `config:` 拿到的 dict。建议用内嵌 `Config(BaseModel)` 校验：

```python
import pydantic

class ScoreFilter(CasewiseComponent):
    """根据阈值过滤低分 case。"""

    class Config(pydantic.BaseModel):
        threshold: float = 0.8
        model_name: str

    def __init__(self, config=None):
        super().__init__(config)
        self.cfg = self.Config(**self.config)

    def execute(self, ctx):
        ...
        if score < self.cfg.threshold:
            raise RuntimeError(f"below {self.cfg.threshold}")
```

```yaml
casewise:
  - src: ./components/score_filter.py
    config:
      threshold: 0.85
      model_name: resnet50
```

好处：

- 启动时就能拦下配置错误（少打字、类型错）。
- API 服务通过 `model_json_schema()` 拿到组件的 config schema 自动渲染表单。
- 文档化配置项。

## 生命周期钩子

```python
class MyComponent(CasewiseComponent):
    def check_skip(self, ctx: CaseContext) -> bool:
        """执行前判断是否跳过。返回 True 即 SKIPPED，不算失败。"""
        return (ctx.casespace / ".cache").exists()

    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        ...

    def _on_success(self, ctx: CaseContext, result) -> None:
        """成功回调，无返回值，框架不使用其返回。"""

    def _on_failure(self, ctx: CaseContext, error: Exception):
        """失败回调，可返回 StageResult 覆盖默认 FAILED 行为（罕用）。"""
        return super()._on_failure(ctx, error)
```

执行顺序：`check_skip` → `execute`（含重试循环）→ `_on_success` 或 `_on_failure`。

`check_skip` 是断点续跑的关键：在 casewise step 里检查 casespace 中是否已有产物，存在则 skip，让 pipeline 可重入。

## 自定义 Iterator

`MemoryIterator` 把 list 包成 Iterator。需要懒加载（数据库、流式 API、超大数据集）就继承 `BaseIterator`：

```python
from deepflow import BaseIterator, DatasetItem

class DatabaseIterator(BaseIterator):
    def __init__(self, db, batch_size=100):
        self.db = db
        self.batch_size = batch_size

    def __iter__(self):
        offset = 0
        while True:
            rows = self.db.query(f"SELECT * LIMIT {self.batch_size} OFFSET {offset}")
            if not rows:
                break
            for row in rows:
                yield DatasetItem(id=row["id"], source=row["path"])
            offset += self.batch_size
```

注意：当前实现里 `Orchestrator._run_casewise` 会 `cases = list(iter(iterator))` 一次性物化到 list 来分发线程池。所以"懒加载"省的是构造时的内存（比如不一次性把 100GB SQL 结果拉进内存），但启动 casewise 之前会先迭代到底。如果数据集真的很大 ，应该考虑分批跑多次 pipeline。

## 共享模块

多组件复用代码（client、常量、工具）放独立目录，manifest 里声明 `shared`：

```yaml
pipeline:
  casewise:
    - src: ./components/quality_check.py
    # quality_check.py 里：from ._shared.llm_client import call_llm
```

注意事项：

- 本地源码必须位于 manifest 目录内，并使用包内相对导入。
- 每个 manifest 使用独立命名空间，并发 run 不共享同名本地模块。

## 失败时保留 metrics

抛异常时想保留中间产出的 metrics（部分跑成功的指标），给 Exception 挂 `metrics` 属性：

```python
class QualityError(Exception):
    def __init__(self, message, metrics):
        super().__init__(message)
        self.metrics = metrics

raise QualityError("score too low", metrics={"score": 0.3, "stage": "extract"})
```

框架在 `_on_failure` / 错误格式化处会通过 `getattr(error, "metrics", {})` 提取。

## 内置插件

| 组件 | 阶段 | 作用 | 配置 |
|------|------|------|------|
| `builtin:clean_workspace` | preprocess | 删除并重建 workspace（含 `metrics/` 子目录） | 无 |
| `builtin:clean_casespace` | casewise | 删除当前 case 的 casespace | 无 |

最常用模式：每条 pipeline 第一个 preprocess 用 `builtin:clean_workspace`，让每次 run 从干净状态开始（除非你要靠 `check_skip` 做断点续跑——那就别清场）。

## 组件加载规则速查

| 格式 | 解析为 |
|------|-------|
| `builtin:clean_workspace` | `deepflow.plugins.builtin.clean_workspace.CleanWorkspace` |
| `myorg:quality_check` | `deepflow.plugins.myorg.quality_check.QualityCheck`（要 pip 安装该 plugin） |
| `./components/quality_check.py` | 同名 PascalCase 类：`QualityCheck` |
| `./components/quality_check.py:MyClass` | 显式指定类名 |

类名推断规则：文件名 stem 的 snake_case → PascalCase。`my_thing.py` → `MyThing`，`thing.py` → `Thing`。
