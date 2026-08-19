# API 服务参考

`deepflow serve` 启动 RESTful API + Web 控制台 + WebSocket 实时事件。

## 启动

```bash
pip install -e '.[server]'                    # 安装 server 依赖
deepflow serve                                # 默认 0.0.0.0:8000，数据存 .deepflow-server/
deepflow serve -p 9000 -d ./data              # 自定义端口和数据目录
deepflow serve -H 127.0.0.1 -p 8080 -v        # 指定 host + verbose
```

入口：

```
API:     http://localhost:8000/api/v1/
Console: http://localhost:8000
Docs:    http://localhost:8000/docs    (Swagger UI)
```

## Pipeline 管理

```
POST   /api/v1/pipelines              创建
GET    /api/v1/pipelines              列表
GET    /api/v1/pipelines/{id}         详情
PUT    /api/v1/pipelines/{id}         更新
DELETE /api/v1/pipelines/{id}         删除
```

## 组件文件管理

```
POST   /api/v1/pipelines/{id}/components            上传 .py
GET    /api/v1/pipelines/{id}/components            列出
GET    /api/v1/pipelines/{id}/components/{name}     读取
PUT    /api/v1/pipelines/{id}/components/{name}     更新
DELETE /api/v1/pipelines/{id}/components/{name}     删除
```

## Hook 文件管理

```
POST   /api/v1/pipelines/{id}/hooks                 上传 .py
GET    /api/v1/pipelines/{id}/hooks                 列出
GET    /api/v1/pipelines/{id}/hooks/{name}          读取
PUT    /api/v1/pipelines/{id}/hooks/{name}          更新
DELETE /api/v1/pipelines/{id}/hooks/{name}          删除
```

## Run 管理

```
POST   /api/v1/pipelines/{id}/runs                  触发运行
GET    /api/v1/runs/{id}                            状态 + 进度
GET    /api/v1/pipelines/{id}/runs                  运行历史
POST   /api/v1/runs/{id}/cancel                     取消
DELETE /api/v1/runs/{id}                            删除
```

## 运行结果查询

```
GET    /api/v1/runs/{id}/cases                      用例列表
GET    /api/v1/runs/{id}/cases/{case_id}            用例详情
GET    /api/v1/runs/{id}/metrics                    指标汇总
GET    /api/v1/runs/{id}/logs                       完整日志
WS     /api/v1/runs/{id}/ws                         实时事件流
```

## 工具端点

```
GET    /api/v1/components                           内置组件元信息（含 config schema）
POST   /api/v1/validate                             校验 manifest
GET    /api/v1/health                               健康检查
```

`GET /api/v1/components` 返回的每条组件包含 `name`（如 `builtin:clean_workspace`）、`stage`、`description`、`config_schema`（来自 Pydantic `Config` 内部类的 `model_json_schema()`）。前端可以用 `config_schema` 自动渲染表单。

## WebSocket 事件

订阅 `WS /api/v1/runs/{id}/ws`，按时序收到的事件类型：

| 事件 | 触发时机 | data 字段 |
|------|----------|-----------|
| `run.started` | run 开始 | `name` |
| `stage.started` | 阶段开始 | `stage`（preprocess / casewise / postprocess）|
| `step.started` | 单 step 开始 | `stage`, `step`，casewise 还有 `case_id` |
| `step.completed` | 单 step 结束 | `stage`, `step`, `status`，casewise 还有 `case_id` |
| `case.started` | case 开始 | `case_id` |
| `case.completed` | case 全部 step 成功 | `case_id`, `status`, `duration_ms` |
| `case.failed` | case 失败 | `case_id`, `status`, `duration_ms` |
| `stage.completed` | 阶段结束 | `stage` |
| `run.completed` | run 全部成功结束 | — |
| `run.failed` | run 异常终止 | — |
| `run.cancelled` | 用户取消 | — |

注意事件顺序：`step.started` 早于 `case.started`（preprocess / postprocess 阶段没有 `case_*` 事件，只有 `step.*`）。

这些事件由内置 hook `EventEmitterHook`（`deepflow.server.hooks`）产生。要在各生命周期挂点插入自定义观察逻辑（通知、上报、自定义指标），见 `docs/HOOKS.md`。

## 数据目录

```
.deepflow-server/                             # -d 指定的目录（默认）
  pipelines/{pipeline_id}/
    pipeline.json                             # manifest 元数据
    components/                               # 上传的 .py 文件
    hooks/                                    # 上传的 Hook .py 文件
  runs/{run_id}/
    run.json                                  # run 元数据
    run.log                                   # 完整日志
    workspace/                                # pipeline 的 workspace
      cases/{case_id}/                        # 每个 case 的 casespace
      metrics/case-*.json                     # 单 case metrics（崩溃安全）
      metrics.json                            # 全量汇总
      run_state.json
```

## CLI 跑 vs Server 跑的差异

| 维度 | `deepflow run` | `deepflow serve` + API |
|------|----------------|------------------------|
| 触发方式 | 本地 CLI | HTTP / Web UI |
| 工作目录 | manifest 同目录 | server 数据目录 |
| 多 run 并存 | 不支持（手动跑多次会覆盖 workspace） | 每个 run 独立 workspace |
| 实时事件 | Rich 终端面板 | WebSocket + Web UI |
| 取消 | Ctrl-C | `POST /runs/{id}/cancel` |
| 适合 | 开发、本地一次性跑 | 多人协作、定时调度、长跑任务 |

**最佳实践**：开发调试用 CLI 加 `--dry-run`；上生产或要给非工程师用就跑 server。
