# manifest.yaml 参考

## 顶层结构

```yaml
version: "2.0"            # 当前固定 "2.0"
name: my-pipeline         # Pipeline 名称（必填）

workspace: ./workspace    # 工作目录，相对 manifest.yaml 解析。默认 ./workspace
concurrency: 4            # casewise 并发数，1-100。默认 1
vars: {}                  # 自定义变量，组件通过 ctx.vars 访问

shared:                   # 可选：共享代码目录列表
  - ./components/_shared

pipeline:
  preprocess: [...]       # list[StepConfig]
  casewise:   [...]
  postprocess:[...]

hooks: [...]              # 可选:list[HookConfig],生命周期观察 hook
```

## StepConfig

```yaml
- src: <component reference>     # 必填
  config:                        # 可选，传给组件的 dict
    key: value
  retry:                         # 可选，仅对该 step 生效
    max_attempts: 3              # >= 1，1 表示不重试
    delay: 2                     # 秒，> 0
    backoff: exponential         # fixed | exponential
```

`backoff: exponential` 时，第 N 次重试间隔 = `delay * 2^(N-1)`（N 从 1 起）。

## HookConfig

`hooks` 声明生命周期观察 hook,在 run/stage/step/case 各挂点插入自定义逻辑(通知、上报、自定义指标)。完整开发指南见 [HOOKS.md](../../../docs/HOOKS.md):

```yaml
hooks:
  - src: ./hooks/notify.py        # hook 引用,格式同组件引用
    config:                        # 可选,传给 hook 的 dict
      webhook: ${FEISHU_BOT_URL}
```

`src` 的解析规则与组件引用完全相同(`builtin:` / `namespace:name` / `./path.py` / 显式类名),只是加载的类需继承 `deepflow.Hook`。hook 纯观察、fail-open,无 `retry` 字段。

## 组件引用格式

| 格式 | 例子 | 解析为 |
|------|------|--------|
| `builtin:<name>` | `builtin:clean_workspace` | `deepflow.plugins.builtin.clean_workspace` 模块的 `CleanWorkspace` 类 |
| `<namespace>:<name>` | `myorg:quality` | `deepflow.plugins.myorg.quality` 模块的 `Quality` 类（需 pip 安装该 plugin） |
| `<path>.py` | `./components/score.py` | 文件 `./components/score.py`，类名自动推断为 `Score` |
| `<path>.py:<Class>` | `./components/score.py:V2Score` | 文件 + 显式类名 |

**类名自动推断规则**：文件名 stem 的 snake_case → PascalCase。`my_thing.py` → `MyThing`。

外部组件路径以 `./` 或 `/` 开头，相对路径相对于 manifest.yaml 所在目录。框架同时把 manifest 所在目录加入 `sys.path`，所以组件之间可以互相 import。

## vars

`vars` 里的内容传给所有阶段的 `ctx.vars`，是只读 dict：

```yaml
vars:
  model_name: resnet50
  threshold: 0.85
  storage:
    bucket: my-bucket
    prefix: 2026-Q2
```

```python
class MyComponent(CasewiseComponent):
    def execute(self, ctx):
        model = ctx.vars["model_name"]
        bucket = ctx.vars["storage"]["bucket"]
```

`vars` vs `config` 的区别：
- `vars` 是**整条 pipeline 共享**的，定义在 manifest 顶层。
- `config` 是**单个 step 私有**的，定义在 step 下。
- 两者都在组件代码里可访问，但语义不同：把"流水线常量"（`model_name`、`storage` 配置）放 `vars`，把"组件本地参数"（`threshold`、`batch_size`）放 `config`。

## 环境变量

YAML 的任何位置可以用 `${VAR_NAME}` 引用环境变量，框架在 schema 校验前递归替换：

```yaml
vars:
  api_key: ${OPENAI_API_KEY}
  endpoint: https://api.${REGION}.example.com
```

未设置的环境变量保留原字面量（`${OPENAI_API_KEY}`），**不会报错**——但 `deepflow check` / `--dry-run` 会列出 missing。在生产 pipeline 里强烈建议每个 `${...}` 引用都先用 `check` 验证。

## shared

多个组件复用代码（client、常量、工具）：

```yaml
shared:
  - ./components/_shared
```

工作机制：

本地源码使用 manifest 专属命名空间。组件之间必须写包内相对导入，避免并发 run
通过 Python 全局模块缓存串用代码。

例子：

```
my-pipeline/
  manifest.yaml
  components/
    _shared/
      llm_client.py
      stages.py
    quality_check.py   # 内部: from ._shared.llm_client import call
```

本地源码必须位于 manifest 目录内。不要使用 `import helper` 这类裸导入。

## 完整示例

```yaml
version: "2.0"
name: video-quality-pipeline
workspace: ./workspace
concurrency: 8

vars:
  model_path: ${MODEL_PATH}
  threshold: 0.85
  feishu:
    bot_url: ${FEISHU_BOT_URL}

shared:
  - ./components/_shared

pipeline:
  preprocess:
    - src: builtin:clean_workspace
    - src: ./components/data_fetch.py
      config:
        source: oss://bucket/dataset/2026-Q2
        limit: 100

  casewise:
    - src: ./components/download.py
      retry:
        max_attempts: 3
        delay: 2
        backoff: exponential
    - src: ./components/quality_check.py
      config:
        threshold: ${THRESHOLD_OVERRIDE}    # 优先用环境变量覆盖
    - src: ./components/score_record.py
    - src: builtin:clean_casespace            # 释放磁盘

  postprocess:
    - src: ./components/aggregate.py
    - src: ./components/feishu_notify.py
      config:
        only_on_failure: true

hooks:
  - src: ./hooks/run_report.py        # 生命周期观察 hook(可选),见 HOOKS.md
```

## 校验 / 预演

```bash
deepflow check -c manifest.yaml      # 校验 schema、组件能加载、阶段匹配、env vars 就绪
deepflow run --dry-run -c manifest.yaml  # 真实跑 preprocess 看 case 数 + 执行计划，不进 casewise
```

`check` 会校验组件、hook 类与 hook 的 Pydantic `Config`，但不真实跑 preprocess（不会触发副作用，例如外部 API 拉数据），所以快但不能验数据集本身。`--dry-run` 跑 preprocess + 列计划，对真实环境最有信心。
