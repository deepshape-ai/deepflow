# 组件开发指南

## 导入

所有公开类型从 `deepflow` 顶层包导入：

```python
from deepflow import (
    PreprocessComponent, CasewiseComponent, PostprocessComponent,
    PreprocessOutput, CasewiseOutput, PostprocessOutput,
    PipelineContext, CaseContext,
    MemoryIterator, DatasetItem,
    FatalError,
)
```

## 三阶段组件

| 阶段 | 基类 | Context | 输出类型 | 执行方式 |
|------|------|---------|----------|----------|
| preprocess | `PreprocessComponent` | `PipelineContext` | `PreprocessOutput` (含 iterator) | 全局一次 |
| casewise | `CasewiseComponent` | `CaseContext` | `CasewiseOutput` (含 metrics) | 线程池并发 per case |
| postprocess | `PostprocessComponent` | `PipelineContext` | `PostprocessOutput` | 全局一次 |

### Preprocess

负责准备数据集。返回的 Iterator 供 casewise 阶段遍历。整条 pipeline 恰好需要一个 Preprocess 组件提交 Iterator。

```python
class DataFetch(PreprocessComponent):
    def execute(self, ctx: PipelineContext) -> PreprocessOutput:
        items = [
            DatasetItem(id="case-1", source="data/1.mp4"),
            DatasetItem(id="case-2", source="data/2.mp4"),
        ]
        return PreprocessOutput(
            message=f"loaded {len(items)} cases",
            iterator=MemoryIterator(items),
        )
```

### Casewise

处理单个 case。每个 case 在独立线程中执行，共享同一个 casespace 目录。返回的 metrics 自动收集。

```python
class QualityCheck(CasewiseComponent):
    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        score = self._analyze(ctx.case.source)
        (ctx.casespace / "result.json").write_text(f'{{"score": {score}}}')
        return CasewiseOutput(metrics={"score": score})
```

### Postprocess

汇总分析。通过 `ctx.metrics_collector` 访问所有 case 的 metrics。

```python
class Report(PostprocessComponent):
    def execute(self, ctx: PipelineContext) -> PostprocessOutput:
        data = ctx.metrics_collector.to_dict()
        # data["cases"] -- dict[str, CaseResult]，包含每个 case 的 metrics、status、duration_ms
        return PostprocessOutput(message="report generated")
```

## Context

### PipelineContext

Preprocess 和 Postprocess 阶段的上下文。

```python
ctx.workspace              # Path: 工作目录
ctx.vars                   # dict: manifest.yaml 中 vars 的内容
ctx.store                  # ContextStore: 同阶段组件间的内存键值存储
ctx.metrics_collector      # MetricsCollector: 指标收集器
```

### CaseContext

Casewise 阶段的上下文。每个 case 独立实例。

```python
ctx.case                   # DatasetItem: 当前用例 (id + 自定义字段)
ctx.casespace              # Path: 用例工作目录，同一 case 的所有 step 共享
ctx.vars                   # dict: manifest.yaml 中 vars 的内容
ctx.store                  # ContextStore: 同一 case 的所有 casewise step 共享
```

## DatasetItem

仅 `id` 必填，其余字段自由扩展。字段通过属性直接访问。

```python
item = DatasetItem(id="001", source="video/1.mp4", fps=30, tags=["smoke"])
item.id      # "001"
item.source  # "video/1.mp4"
item.fps     # 30
```

底层是 Pydantic model，`model_config = ConfigDict(extra="allow")` 允许任意字段。

## 指标收集

Casewise 组件通过 `CasewiseOutput(metrics={...})` 返回的 metrics 会自动收集。框架在每个 case 完成后原子写入 `workspace/metrics/case-{id}.json`，确保崩溃不丢失数据。

Postprocess 阶段通过 `ctx.metrics_collector` 读取内存中的全量指标：

```python
ctx.metrics_collector.to_dict()       # 与 metrics.json 相同结构
ctx.metrics_collector.cases           # dict[str, CaseResult]
ctx.metrics_collector.completed_count # int
ctx.metrics_collector.aggregate_errors()  # 按错误类型分组
```

## 组件配置

通过 `self.config` 访问 manifest.yaml 中的 config 字段。推荐在 `__init__` 中解析一次：

```python
class MyComponent(CasewiseComponent):
    def __init__(self, config=None):
        super().__init__(config)
        self.threshold = self.config.get("threshold", 0.8)
```

也可以用 Pydantic BaseModel 声明 Config 内部类，获得类型校验和文档：

```python
class MyComponent(CasewiseComponent):
    class Config(pydantic.BaseModel):
        threshold: float = 0.8
        model_name: str

    def __init__(self, config=None):
        super().__init__(config)
        cfg = self.Config(**self.config)
        self.threshold = cfg.threshold
```

```yaml
casewise:
  - src: ./components/my_component.py
    config:
      threshold: 0.85
      model_name: resnet50
```

## 组件间数据传递

同一 case 的多个 casewise step 共享 casespace 目录，通过文件传递数据。这是推荐的跨 step 通信方式：显式、可调试、无隐式状态。

```yaml
casewise:
  - src: ./step_a.py
    config: { output_file: result.json }
  - src: ./step_b.py
    config: { input_file: result.json }
```

```python
# step_a.py -- 写入
(ctx.casespace / self.config["output_file"]).write_text(json.dumps(data))

# step_b.py -- 读取
data = json.loads((ctx.casespace / self.config["input_file"]).read_text())
```

### ContextStore

同阶段的多个 step 之间可以通过 `ctx.store` 进行内存数据传递，无需写文件：

```python
# step_a.py -- 写入
class StepA(CasewiseComponent):
    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        ctx.store.set("analysis_result", {"score": 0.95})
        return CasewiseOutput()

# step_b.py -- 读取
class StepB(CasewiseComponent):
    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        result = ctx.store.get("analysis_result")
        return CasewiseOutput(metrics=result)
```

作用域：同一 case 的所有 casewise step 共享一个实例（casewise 阶段），同一 pipeline 的所有 step 共享一个实例（preprocess / postprocess 阶段）。跨阶段传递仍使用文件。

## 失败处理

框架管理组件状态，组件不需要手动处理。行为规则：

- 正常返回 -- SUCCESS
- 抛出异常 -- FAILED，按 retry 配置重试，重试耗尽后跳过该 case 继续处理下一个
- `check_skip` 返回 True -- SKIPPED
- 抛出 `FatalError` -- 不重试，立即终止整条 pipeline

### FatalError

用于认证失败、关键资源不可用等不可恢复场景：

```python
from deepflow import FatalError

class MyComponent(CasewiseComponent):
    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        if not ctx.case.source:
            raise FatalError("data source is empty, cannot continue")
```

### 失败时保留 metrics

在异常上附加 `metrics` 属性，框架通过 `getattr(error, "metrics", {})` 提取：

```python
class QualityError(Exception):
    def __init__(self, message, metrics):
        super().__init__(message)
        self.metrics = metrics

raise QualityError("score too low", metrics={"score": 0.3})
```

## 生命周期钩子

```python
class MyComponent(CasewiseComponent):
    def check_skip(self, ctx: CaseContext) -> bool:
        """执行前判断是否跳过。返回 True 则跳过。"""
        return (ctx.casespace / ".cache").exists()

    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        ...

    def _on_success(self, ctx: CaseContext, result) -> None:
        """成功后回调"""

    def _on_failure(self, ctx: CaseContext, error: Exception):
        """失败后回调。返回 StageResult 可覆盖默认行为。"""
        return super()._on_failure(ctx, error)
```

执行顺序：`check_skip` -> `execute` (含重试循环) -> `_on_success` 或 `_on_failure`。

## 自定义 Iterator

`MemoryIterator` 适用于数据已在内存中的场景。需要懒加载时继承 `BaseIterator`：

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

## 共享模块

当多个组件需要复用代码（常量、工具函数、客户端封装），放在 manifest 目录内，
并使用包内相对导入。每个 manifest 使用独立命名空间，并发运行不会串用模块。

```yaml
pipeline:
  casewise:
    - src: ./components/seg_evaluator.py
    # seg_evaluator.py: from ._shared.gt_seg import find_gt_seg
```

注意事项：

- 本地源码必须位于 manifest 目录内
- 本地模块之间必须使用相对导入，不使用裸模块名
- 同一 manifest 内的模块按需加载并复用

典型目录结构：

```
my-project/
  manifest.yaml
  components/
    _shared/
      stages.py          # 常量和工具函数
      feishu_client.py   # 第三方 API 封装
      gt_seg.py          # 数据处理工具
    step_a.py
    step_b.py
```
