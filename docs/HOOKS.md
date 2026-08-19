# Hook 开发指南

Hook 是 deepflow 的**统一生命周期扩展机制**：在 run / stage / step / case 各层级的关键
挂点插入自定义逻辑（日志、追踪、指标、通知、上报），而**无需修改任何组件代码**。

框架自身的两个可观测通道——CLI 进度面板（`PipelineRenderer`）与 server WebSocket 事件
（`EventEmitter`）——就是两个内置 hook。你写的 hook 与它们跑在同一条总线上。

设计基调：**纯观察**。hook 只读、不返回值、不改变主流程；任何异常被框架吞掉记日志，
绝不影响 pipeline。

## 快速开始

```python
# hooks/notify.py
from deepflow import Hook, HookContext


class Notify(Hook):
    def on_run_finish(self, ctx: HookContext, status: str, error) -> None:
        send_feishu(f"Pipeline {ctx.run_id} 结束: {status}", timeout=3)

    def on_case_finish(self, ctx, status, duration_ms, completed, total) -> None:
        if status == "failed":
            logger.warning("case %s 失败 (%d/%d)", ctx.case.id, completed, total)
```

```yaml
# manifest.yaml
hooks:
  - src: ./hooks/notify.py
    config: { webhook: ${FEISHU_BOT_URL} }
```

Hook 引用同一 manifest 内的辅助模块时使用相对导入，例如
`from .notify_client import send_feishu`。本地源码按 manifest 隔离，不支持裸模块导入。

或编程式注册（库集成 / 测试）：

```python
orch = Orchestrator(manifest, manifest_dir)
orch.add_hook(Notify(config={"webhook": "..."}))
orch.run()
```

## 九个挂点

所有挂点方法第一个参数都是 `HookContext`，其余为关键字参数。只覆盖你关心的。

| 挂点 | 额外参数 | 线程 | 触发时机 |
|------|----------|------|----------|
| `on_run_start` | — | 主 | run 开始 |
| `on_run_finish` | `status`, `error` | 主 | run 结束。`status ∈ {completed, failed, cancelled}` |
| `on_stage_start` | `stage` | 主 | 每阶段（preprocess/casewise/postprocess）开始 |
| `on_stage_finish` | `stage` | 主 | 每阶段完成 |
| `on_cases_ready` | `total` | 主 | case 列表就绪、casewise 开始前 |
| `on_step_start` | `stage`, `step` | 主 / worker * | 单个组件执行前 |
| `on_step_finish` | `stage`, `step`, `result` | 主 / worker * | 单个组件执行后（含 skipped/failed） |
| `on_case_start` | `index`, `total` | worker | 单个 case 开始。`index` 为提交序（0-based） |
| `on_case_finish` | `status`, `duration_ms`, `completed`, `total` | 主 | 单个 case 完成，按完成顺序分发 |

\* `on_step_*` 在 preprocess / postprocess 于**主线程**触发，在 casewise 于 **worker 线程**触发。

**case 排位（进度）**:`on_case_finish` 的 `completed` 是「第几个完成」（1-based，已含本
case）,`total` 是总数——直接组成 `completed/total` 进度。被取消的 case 不触发
`on_case_finish`、不占排位。兜底还可读 `ctx.metrics_collector.completed_count`。

## HookContext（信封）

`HookContext` 不是新的数据 context——它的 `.ctx` 就是**组件拿到的同一个**
`PipelineContext` / `CaseContext` 对象，只是额外补了 `run_id` 和 pipeline 级
`metrics_collector`:

| 属性 | 类型 | 说明 |
|------|------|------|
| `run_id` | `str` | 本次运行 ID |
| `ctx` | `PipelineContext \| CaseContext` | 当前阶段的执行上下文（同一对象） |
| `case` | `DatasetItem \| None` | 当前 case（case 级挂点非 None） |
| `vars` | `dict` | manifest 的 `vars` |
| `store` | `ContextStore` | 当前阶段的共享键值存储 |
| `workspace` | `Path` | 工作目录 |
| `metrics_collector` | `MetricsCollector` | 指标收集器（case 级 hook 也能拿到） |

判断当前是 pipeline 级还是 case 级：`if ctx.case is not None: ...`。

## 类属性

```python
class MyHook(Hook):
    Config = MyConfigModel      # 可选:pydantic BaseModel,校验 config(对齐组件体系)
    thread_safe = False         # 见「线程安全」
```

## 执行顺序

同一挂点有多个 hook 时：

1. **内置 hook 优先**(CLI 渲染、server 事件），保证框架自身状态先更新；
2. **用户 hook 随后**，按注册顺序（manifest 声明序 / `add_hook` 调用序）先进先出。

## 线程安全

- **主线程挂点**(`on_run_*` / `on_stage_*` / `on_cases_ready` / preprocess、postprocess
  的 `on_step_*`)：单线程串行，无需考虑并发。
- **worker 线程挂点**(`on_case_start` + casewise 的 `on_step_*`):`concurrency` 个线程可能
  **同时**进入同一个 hook 实例。两种应对：
  - `thread_safe = False`（默认）——框架为你的 hook 单独配一把锁，把它的并发挂点串行化
    （不同 hook 之间不互斥）。安全，适合有共享可变状态的 hook（如自己累加计数）。
  - `thread_safe = True`——框架不加锁，你自己保证线程安全（用锁 / 用线程安全结构）。
    无并发开销，适合无状态或自带同步的 hook。内置 hook 都是这类。

## IO 与交付语义

Hook 同步执行且 fail-open。DeepFlow 不内置消息队列，也不承诺远程通知可靠送达。远程 IO
必须设置明确超时；需要可靠交付时，hook 只把事件写入 durable outbox，再由独立 worker
发送。这样 pipeline 生命周期与消息系统保持解耦。

## 失败语义（fail-open）

hook 里抛出的**任何**异常都被框架捕获、记 `DEBUG` 日志，既不影响同挂点的其他 hook,
也不影响 pipeline 主流程。hook 应当纯粹观察；需要中断流程的逻辑请用组件 + `FatalError`
表达，不要用 hook。

## 完整示例：失败聚合上报

```python
# hooks/failure_report.py
import collections
import threading

from deepflow import Hook, HookContext


class FailureReport(Hook):
    """case 失败实时计数,run 结束写入 durable outbox。"""

    def __init__(self, config=None):
        super().__init__(config)
        self._lock = threading.Lock()
        self._errors = collections.Counter()

    def on_case_finish(self, ctx: HookContext, status, duration_ms, completed, total) -> None:
        if status == "failed":
            with self._lock:
                self._errors[type(ctx).__name__] += 1   # 示意:按需要聚合

    def on_run_finish(self, ctx: HookContext, status, error) -> None:
        outbox.write(ctx.run_id, dict(self._errors), ctx.metrics_collector.completed_count)
```

## 内置 hook 参考

| hook | 位置 | thread_safe | 作用 |
|------|------|-------------|------|
| `CliRendererHook` | `deepflow.cli.hooks` | True | CLI Rich 进度面板 |
| `EventEmitterHook` | `deepflow.server.hooks` | True | server WebSocket 事件广播 |

`Orchestrator` 是一次性对象。`dry_run()` 或 `run()` 调用一次后，如需再次执行必须创建新实例。
`dry_run()` 不触发生命周期 hook，避免通知或外部写入等观察副作用。hook 注册在执行开始时冻结，
此后调用 `add_hook()` 会直接报错。
