# deepflow

声明式三阶段 Pipeline 执行框架。用 YAML 定义流水线，用 Python 编写组件。

## 架构

deepflow 将批量处理任务抽象为三阶段流水线：

```
Preprocess
    |  准备数据集，提交 Iterator
    v
Casewise  (线程池并发)
    |  每个 case 独立执行，收集 metrics
    v
Postprocess
    |  读取全量 metrics，汇总输出
    v
  完成
```

一个 `manifest.yaml` 描述整条流水线。组件继承基类、实现 `execute()` 方法，框架管理并发、重试、状态和指标收集。

## 安装

```bash
pip install -e .
```

带 Web 控制台（需要 FastAPI + uvicorn）：

```bash
pip install -e '.[server]'
```

## 快速开始

### 1. 编写组件

三个阶段各继承对应的基类，实现 `execute()` 返回结果。

Preprocess -- 准备数据集，返回 Iterator：

```python
from deepflow import PreprocessComponent, PreprocessOutput, MemoryIterator, DatasetItem, PipelineContext

class DataFetch(PreprocessComponent):
    def execute(self, ctx: PipelineContext) -> PreprocessOutput:
        items = [
            DatasetItem(id="case-1", source="video/1.mp4", fps=30),
            DatasetItem(id="case-2", source="video/2.mp4", fps=25),
        ]
        return PreprocessOutput(iterator=MemoryIterator(items))
```

Casewise -- 处理单个 case，返回 metrics：

```python
from deepflow import CasewiseComponent, CasewiseOutput, CaseContext

class QualityCheck(CasewiseComponent):
    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        score = self._analyze(ctx.case.source)
        if score < self.config.get("threshold", 0.8):
            raise RuntimeError(f"quality score {score} below threshold")
        return CasewiseOutput(metrics={"score": score})
```

Postprocess -- 汇总全量 metrics：

```python
from deepflow import PostprocessComponent, PostprocessOutput, PipelineContext

class Report(PostprocessComponent):
    def execute(self, ctx: PipelineContext) -> PostprocessOutput:
        data = ctx.metrics_collector.to_dict()
        (ctx.workspace / "report.json").write_text(json.dumps(data, indent=2))
        return PostprocessOutput(message="report generated")
```

### 2. 编写 manifest.yaml

```yaml
version: "2.0"
name: video-qa

workspace: ./workspace
concurrency: 4

pipeline:
  preprocess:
    - src: builtin:clean_workspace
    - src: ./components/data_fetch.py

  casewise:
    - src: ./components/quality_check.py
      config:
        threshold: 0.85
      retry:
        max_attempts: 2
        delay: 1

  postprocess:
    - src: ./components/report.py
```

### 3. 运行

```bash
deepflow run -c manifest.yaml
```

`-v` 输出详细日志，`--dry-run` 仅执行 Preprocess 并展示执行计划。

## manifest.yaml 参考

### 顶层结构

```yaml
version: "2.0"
name: pipeline-name

workspace: ./workspace      # 工作目录
concurrency: 4              # casewise 并发数 (1-100)
vars: {}                    # 自定义变量，组件通过 ctx.vars 访问

pipeline:
  preprocess:
    - src: builtin:clean_workspace
    - src: ./components/my_component.py
      config:
        key: value
      retry:
        max_attempts: 3      # 最大尝试次数
        delay: 2              # 重试间隔 (秒)
        backoff: exponential  # fixed | exponential
  casewise: [...]
  postprocess: [...]
```

环境变量语法 `${VAR_NAME}` 可在 YAML 任意位置使用，框架在解析前递归替换。

### 组件引用

| 格式 | 示例 | 解析规则 |
|------|------|----------|
| 内置插件 | `builtin:clean_workspace` | `deepflow.plugins.builtin.clean_workspace` |
| 文件路径 + 类名 | `./my.py:MyClass` | 从指定文件加载指定类 |
| 文件路径（自动推断） | `./my_component.py` | `my_component` -> `MyComponent` |

### 共享模块

多个组件复用的代码放在 manifest 目录内，并使用包内相对导入：

```yaml
pipeline:
  casewise:
    - src: ./components/step_a.py
    # step_a.py: from ._shared.stages import SEMANTIC_STAGES
```

每个 manifest 使用独立模块命名空间，并发运行不会串用同名本地模块。不支持 `import stages`
这类裸模块导入。

### 内置组件

| 组件 | 阶段 | 作用 |
|------|------|------|
| `builtin:clean_workspace` | preprocess | 删除并重建 workspace 目录 |
| `builtin:clean_casespace` | casewise | 删除当前 case 的 casespace 目录 |

## CLI

```bash
deepflow run -c manifest.yaml        # 执行流水线
deepflow run -c manifest.yaml -v     # 详细日志
deepflow run -c manifest.yaml --dry-run  # 预览执行计划
deepflow check -c manifest.yaml      # 校验 manifest 和组件
deepflow init -o manifest.yaml       # 生成示例 manifest
deepflow serve                       # 启动 API 服务
deepflow serve -p 9000 -d ./data     # 自定义端口和数据目录
```

## 日志

CLI 每次运行在控制台之外产生两层落盘日志：

- `manifest 目录/.deepflow/logs/{run_id}.log` — 整运行日志（框架 + 组件），带 `[run_id/case_id]` 标记
- `workspace/cases/{case_id}/log.txt` — 单个 case 的完整诊断日志（组件日志、重试告警、失败 traceback）

run.log 不放在 workspace 内：`builtin:clean_workspace` 会在 preprocess 阶段清空 workspace。

## API 服务

`deepflow serve` 启动 RESTful API + Web 控制台。

```
API:     http://localhost:8000/api/v1/
Console: http://localhost:8000
Docs:    http://localhost:8000/docs
```

### Pipeline 管理

`POST /api/v1/pipelines` -- 创建
`GET /api/v1/pipelines` -- 列表
`GET /api/v1/pipelines/{id}` -- 详情
`PUT /api/v1/pipelines/{id}` -- 更新
`DELETE /api/v1/pipelines/{id}` -- 删除

### 组件文件

`POST /api/v1/pipelines/{id}/components` -- 上传 .py
`GET /api/v1/pipelines/{id}/components` -- 列表
`GET /api/v1/pipelines/{id}/components/{name}` -- 读取
`PUT /api/v1/pipelines/{id}/components/{name}` -- 更新
`DELETE /api/v1/pipelines/{id}/components/{name}` -- 删除

### 运行

`POST /api/v1/pipelines/{id}/runs` -- 触发运行
`GET /api/v1/runs/{id}` -- 状态和进度
`GET /api/v1/pipelines/{id}/runs` -- 运行历史
`POST /api/v1/runs/{id}/cancel` -- 取消
`DELETE /api/v1/runs/{id}` -- 删除

### 运行结果

`GET /api/v1/runs/{id}/cases` -- 用例列表
`GET /api/v1/runs/{id}/cases/{case_id}` -- 用例详情
`GET /api/v1/runs/{id}/metrics` -- 指标汇总
`GET /api/v1/runs/{id}/logs` -- 日志
`WS /api/v1/runs/{id}/ws` -- 实时事件流

### 工具

`GET /api/v1/components` -- 内置组件元信息
`POST /api/v1/validate` -- 校验 manifest
`GET /api/v1/health` -- 健康检查

### WebSocket 事件

| 事件 | 说明 |
|------|------|
| `run.started` | 运行开始 |
| `run.completed` | 运行完成 |
| `run.failed` | 运行失败 |
| `run.cancelled` | 运行取消 |
| `stage.started` | 阶段开始 |
| `stage.completed` | 阶段完成 |
| `case.started` | 用例开始 |
| `case.completed` | 用例完成 |
| `case.failed` | 用例失败 |

### 数据目录

```
.deepflow-server/
  pipelines/{id}/
    pipeline.json
    components/
  runs/{id}/
    run.json
    run.log
    workspace/
```

## 文档

- [组件开发指南](docs/COMPONENT_DEVELOPMENT.md) -- 基类、Context、指标、生命周期钩子的完整参考
- [构建与分发](docs/BUILD.md) -- wheel 构建、前端打包、离线部署
- [前端开发](web/README.md) -- Web 控制台的技术栈和开发流程
