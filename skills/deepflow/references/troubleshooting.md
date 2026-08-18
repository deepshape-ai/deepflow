# 常见错误与排查

错误信息 → 根因 → 修法。按出现频率排序。

## "preprocess 阶段必须恰好有一个组件提交 iterator，但没有组件提交"

**根因**：preprocess 里所有组件都没有返回 `PreprocessOutput(iterator=...)`。

**修法**：检查 preprocess 列表里至少有一个组件 return 时带 `iterator`。`builtin:clean_workspace` 这类副作用组件不算。

```python
return PreprocessOutput(iterator=MemoryIterator(items))   # 必须带 iterator
```

## "多个 preprocess 组件提交了 iterator: A 和 B，只允许一个组件提交 iterator"

**根因**：两个 preprocess 组件都 return 了 iterator。

**修法**：合并成一个组件，或者把"提供 iterator"这步专门放最后一个 preprocess 组件，前面的全部不带 iterator。

## "组件 X 是 CasewiseComponent，但放在了 preprocess 阶段"

**根因**：阶段位置和组件类型不匹配。

**修法**：组件类型决定它能放的阶段，和 manifest 里的位置必须对应：

| 阶段 | 必须继承 |
|------|---------|
| `preprocess:` 下 | `PreprocessComponent` |
| `casewise:` 下 | `CasewiseComponent` |
| `postprocess:` 下 | `PostprocessComponent` |

`deepflow check` 会提前发现这类问题。

## "组件文件未找到: /path/to/component.py"

**根因**：相对路径解析错。框架以 manifest.yaml 所在目录为基。

**修法**：要么改成相对 manifest 的正确路径，要么用绝对路径。`./components/foo.py` 在 manifest 同目录有 `components/foo.py` 时正确。

## "Plugin 模块 X 中未找到类 Y"

**根因**：类名推断失败。文件名 stem 必须能 snake_case → PascalCase 推出来 manifest 里要的类名。

**修法**：

- 检查文件名：`my_thing.py` → 找 `MyThing` 类
- 类名不匹配自动推断时显式写：`./my_thing.py:CustomClassName`

## "Plugin 组件未找到: namespace:name"

**根因**：`deepflow.plugins.<namespace>` 不是已安装的包，或包里没有 `<name>.py`。

**修法**：

- 自定义 plugin 先 `pip install` 对应的包。
- 拼写检查 `<namespace>` 和 `<name>`。
- 不需要 plugin 时，改用本地组件 `./path/to/comp.py`。

## "环境变量 X 未设置"（来自 `deepflow check`）

**根因**：manifest 里 `${X}` 引用了，但 X 不在 `os.environ` 里。

**修法**：`export X=value` 或在调用前 `X=value deepflow run -c ...`。注意：未设置的 `${X}` 在 run 时**不会报错**，会以字面量 `${X}` 传给组件——很容易跑出奇怪 bug。所以上线前一定跑 `check`。

## "ModuleNotFoundError: No module named 'xxx'" 在组件里

可能根因有几个：

1. **shared 目录没声明**：组件想 `from llm_client import ...`，但 manifest 里没写 `shared:`。
2. **shared 模块名和系统包冲突**：`_shared/json.py` 会被遮蔽（其实导入到的是标准库 `json`，但内容不对）。改名 `my_json.py`。
3. **第三方依赖没装**：组件 import 了 `requests`、`openai` 等，但环境里没装。`pip install` 即可。
4. **多个 manifest 共享同一 Python 进程**：API server 模式下加载多个 pipeline 时，shared 目录可能互相污染。给每个 shared 模块都用项目前缀避免冲突。

## casewise 跑得超慢，concurrency 调高也没用

可能根因：

- **CPU 密集 + GIL**：deepflow 用线程池，CPU 重活只能用一个核。改 `concurrency=1`，组件内部用 `multiprocessing.Pool` 或 `subprocess` 跑子进程。
- **下游限流**：API 限流、数据库连接池满。降 `concurrency`，加重试。
- **共享锁**：组件内对全局资源加锁（数据库 client、文件 handle）。
- **`MemoryIterator(items)` 的 `items` 构造太慢**：例如在 preprocess 里下载所有文件。把 IO 推迟到 casewise 阶段。

## "TypeError: '<' not supported between instances of 'NoneType' and 'NoneType'" 之类的奇怪错误

**根因**：组件代码里访问了不存在的 `DatasetItem` 字段，Pydantic 返回 `None`。

**修法**：在 preprocess 里给所有 case 都填齐字段，或在 casewise 用 `getattr(ctx.case, "field", default)`。`DatasetItem` 用了 `extra="allow"`，访问不存在字段不会 AttributeError，会返回字段默认值（通常 `None`）。

## case 失败但没看到 metrics

**根因**：默认情况下 case 抛 Exception 后，框架只记 status / error_type / error_message，metrics 是空的。

**修法**：让 Exception 带 `metrics` 属性：

```python
class QualityError(Exception):
    def __init__(self, msg, metrics):
        super().__init__(msg); self.metrics = metrics

raise QualityError("blur too high", metrics={"blur": 0.8, "stage": "extract"})
```

框架会通过 `getattr(error, "metrics", {})` 提取。

## postprocess 拿不到 metrics

**根因**：用错了 API。

**修法**：

```python
def execute(self, ctx: PipelineContext) -> PostprocessOutput:
    data = ctx.metrics_collector.to_dict()
    # 不要去 read workspace/metrics.json，那个文件 postprocess 完才会写
```

`ctx.metrics_collector.to_dict()` 拿到的是内存快照，结构和 `metrics.json` 一致：

```python
{
  "summary": str,
  "cases": {
    "<case-id>": {
      "metrics": {...},
      "status": "success" | "failed" | "skipped",
      "duration_ms": float,
      "error_type": str,        # 仅 failed
      "error_message": str,     # 仅 failed
      "failed_step": str,       # 仅 failed
    }
  }
}
```

## 整条 pipeline 直接终止，casewise 没跑完

**根因 #1**：某个 casewise 组件抛了 `FatalError`。`FatalError` 设计就是不重试 + 立即终止。

**修法**：确认是真的不可恢复（认证失败、关键资源缺失）；如果只是某 case 不该处理，应该用普通 Exception（被 retry 或 fail-isolate）或 `check_skip` 跳过。

**根因 #2**：preprocess / postprocess 任一 step 失败。这两个阶段不容忍失败。

**修法**：preprocess 应该尽量幂等且无副作用，把可能失败的事情（API 调用）推到 casewise。

## 要让 case 失败就停整条 pipeline

**根因**：默认 case 失败被隔离，整条 pipeline 继续。

**修法**：在 casewise 组件里抛 `FatalError`，框架立即终止。

```python
from deepflow import FatalError

if not ctx.case.source:
    raise FatalError("source 必填，pipeline 配置错误")
```

只在"配置错误 / 认证失败 / 关键资源不可用"等场景用 `FatalError`，不要拿来当"严格模式 fail-fast"——丢失失败隔离的好处。
