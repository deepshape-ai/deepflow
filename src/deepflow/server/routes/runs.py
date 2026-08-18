"""
运行生命周期端点 + WebSocket 实时事件流。

路由结构：
    POST   /api/v1/pipelines/{pid}/runs            从已存储 pipeline 触发
    GET    /api/v1/runs/{run_id}                   运行状态 + 进度
    GET    /api/v1/pipelines/{pid}/runs            运行历史
    POST   /api/v1/runs/{run_id}/cancel            取消
    DELETE /api/v1/runs/{run_id}                   删除运行记录
    GET    /api/v1/runs/{run_id}/cases             用例列表
    GET    /api/v1/runs/{run_id}/cases/{case_id}   用例详情
    GET    /api/v1/runs/{run_id}/metrics           指标汇总
    GET    /api/v1/runs/{run_id}/logs              运行日志
    WS     /api/v1/runs/{run_id}/ws                实时事件流
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import PlainTextResponse

from deepflow.server.dependencies import get_pipeline_store, get_run_manager
from deepflow.server.events import EventType
from deepflow.server.models import (
    CaseResponse,
    CaseStatus,
    MetricsResponse,
    PipelineRunCreateRequest,
    RunResponse,
)
from deepflow.server.services.pipeline_store import PipelineStore
from deepflow.server.services.run_manager import RunManager

router = APIRouter(prefix="/api/v1", tags=["runs"])

# 终态事件类型集合，WebSocket 收到后断开
_TERMINAL_EVENTS = frozenset({
    EventType.RUN_COMPLETED,
    EventType.RUN_FAILED,
    EventType.RUN_CANCELLED,
})


# ── 运行创建 ──────────────────────────────────────────────────


@router.post(
    "/pipelines/{pipeline_id}/runs",
    response_model=RunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_pipeline_run(
    pipeline_id: str,
    body: PipelineRunCreateRequest | None = None,
    store: PipelineStore = Depends(get_pipeline_store),
    manager: RunManager = Depends(get_run_manager),
) -> RunResponse:
    """从已存储的 Pipeline 配置触发运行，可选覆盖参数。"""
    pipeline = store.get(pipeline_id)
    if pipeline is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Pipeline {pipeline_id} not found")

    pipeline_dir = store.get_pipeline_dir(pipeline_id)

    try:
        state = await manager.create_run(
            pipeline.manifest,
            manifest_dir=pipeline_dir,
            pipeline_id=pipeline_id,
            overrides=body.overrides if body else None,
        )
    except Exception as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return state.to_response()


# ── 运行查询 ──────────────────────────────────────────────────


@router.get("/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    manager: RunManager = Depends(get_run_manager),
) -> RunResponse:
    state = manager.get_run(run_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run {run_id} not found")
    return state.to_response()


@router.get("/pipelines/{pipeline_id}/runs", response_model=list[RunResponse])
async def list_pipeline_runs(
    pipeline_id: str,
    manager: RunManager = Depends(get_run_manager),
) -> list[RunResponse]:
    return [s.to_response() for s in manager.list_runs(pipeline_id)]


# ── 运行控制 ──────────────────────────────────────────────────


@router.post("/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: str,
    manager: RunManager = Depends(get_run_manager),
) -> RunResponse:
    if not manager.cancel_run(run_id):
        state = manager.get_run(run_id)
        if state is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run {run_id} not found")
        raise HTTPException(status.HTTP_409_CONFLICT, "Run is not in a cancellable state")
    state = manager.get_run(run_id)
    return state.to_response()  # type: ignore[union-attr]


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_run(
    run_id: str,
    manager: RunManager = Depends(get_run_manager),
):
    """删除已完成的运行记录（内存 + 磁盘）。运行中的不可删除。"""
    state = manager.get_run(run_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run {run_id} not found")
    if not manager.delete_run(run_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot delete a running or pending run")


# ── Case 查询 ─────────────────────────────────────────────────


@router.get("/runs/{run_id}/cases", response_model=list[CaseResponse])
async def list_cases(
    run_id: str,
    status_filter: CaseStatus | None = None,
    manager: RunManager = Depends(get_run_manager),
) -> list[CaseResponse]:
    state = manager.get_run(run_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run {run_id} not found")
    if state.orchestrator is None:
        return []

    cases = state.orchestrator.metrics_collector.cases
    results = [
        CaseResponse(
            case_id=case_id,
            status=CaseStatus.SUCCESS if result.status == "success" else CaseStatus.FAILED,
            metrics=result.metrics,
            duration_ms=result.duration_ms,
            error_type=result.error_type,
            error_message=result.error_message,
            failed_step=result.failed_step,
        )
        for case_id, result in cases.items()
    ]

    if status_filter is not None:
        results = [r for r in results if r.status == status_filter]
    return results


@router.get("/runs/{run_id}/cases/{case_id}", response_model=CaseResponse)
async def get_case(
    run_id: str,
    case_id: str,
    manager: RunManager = Depends(get_run_manager),
) -> CaseResponse:
    state = manager.get_run(run_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run {run_id} not found")
    if state.orchestrator is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Case {case_id} not found")

    cases = state.orchestrator.metrics_collector.cases
    if case_id not in cases:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Case {case_id} not found")

    result = cases[case_id]
    return CaseResponse(
        case_id=case_id,
        status=CaseStatus.SUCCESS if result.status == "success" else CaseStatus.FAILED,
        metrics=result.metrics,
        duration_ms=result.duration_ms,
        error_type=result.error_type,
        error_message=result.error_message,
        failed_step=result.failed_step,
    )


# ── Metrics ───────────────────────────────────────────────────


@router.get("/runs/{run_id}/metrics", response_model=MetricsResponse)
async def get_metrics(
    run_id: str,
    manager: RunManager = Depends(get_run_manager),
) -> MetricsResponse:
    state = manager.get_run(run_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run {run_id} not found")
    if state.orchestrator is None:
        return MetricsResponse(run_id=run_id)

    mc = state.orchestrator.metrics_collector
    data = mc.to_dict()
    cases = mc.cases
    return MetricsResponse(
        run_id=run_id,
        summary=data.get("summary", ""),
        total_cases=len(cases),
        completed_cases=len(cases),
        failed_cases=sum(1 for c in cases.values() if c.status == "failed"),
        cases=data.get("cases", {}),
    )


# ── Logs ──────────────────────────────────────────────────────


@router.get("/runs/{run_id}/logs")
async def get_run_logs(
    run_id: str,
    manager: RunManager = Depends(get_run_manager),
) -> PlainTextResponse:
    """获取运行日志（纯文本）。"""
    state = manager.get_run(run_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Run {run_id} not found")

    log_path = manager.get_run_log_path(run_id)
    if log_path is None:
        return PlainTextResponse("")

    return PlainTextResponse(log_path.read_text(encoding="utf-8"))


# ── WebSocket ─────────────────────────────────────────────────


@router.websocket("/runs/{run_id}/ws")
async def run_websocket(websocket: WebSocket, run_id: str) -> None:
    """实时事件流。

    连接后推送该 run 的所有事件，直到运行结束或客户端断开。
    事件格式：{ "type": "stage.started", "run_id": "...", "timestamp": ..., "data": {...} }
    """
    # 手动获取依赖（WebSocket 不支持 Depends 的 response_model）
    manager = get_run_manager()
    state = manager.get_run(run_id)

    if state is None or state.event_bridge is None:
        await websocket.close(code=4004, reason="Run not found")
        return

    await websocket.accept()
    queue = state.event_bridge.subscribe()

    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event.to_dict())

            if event.type in _TERMINAL_EVENTS:
                break
    except WebSocketDisconnect:
        pass
    finally:
        state.event_bridge.unsubscribe(queue)
