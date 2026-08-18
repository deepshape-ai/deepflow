"""
API 请求/响应模型。

与 deepflow.models 分离，避免引擎模型与 API 表示层耦合。
引擎模型面向 Pipeline 配置，API 模型面向 HTTP 交互。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# ── 状态枚举 ──────────────────────────────────────────────────


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CaseStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


# ── 请求模型 ──────────────────────────────────────────────────


class PipelineCreateRequest(BaseModel):
    """POST /api/v1/pipelines"""

    name: str = Field(description="Pipeline 名称")
    manifest: dict[str, Any] = Field(description="完整 manifest 配置")


class PipelineUpdateRequest(BaseModel):
    """PUT /api/v1/pipelines/{pipeline_id}"""

    name: str | None = None
    manifest: dict[str, Any] | None = None


class PipelineRunCreateRequest(BaseModel):
    """POST /api/v1/pipelines/{pipeline_id}/runs — 覆盖参数"""

    overrides: dict[str, Any] | None = Field(
        default=None,
        description="覆盖 manifest 中的根级配置字段或 vars",
    )


# ── 响应模型 ──────────────────────────────────────────────────


class PipelineResponse(BaseModel):
    id: str
    name: str
    manifest: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProgressInfo(BaseModel):
    """运行进度摘要"""

    total_cases: int = 0
    completed_cases: int = 0
    failed_cases: int = 0


class RunResponse(BaseModel):
    id: str
    pipeline_id: str | None = None
    status: RunStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    progress: ProgressInfo = Field(default_factory=ProgressInfo)
    error: str | None = None


class CaseResponse(BaseModel):
    case_id: str
    status: CaseStatus
    metrics: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float | None = None
    error_type: str = ""
    error_message: str = ""
    failed_step: str = ""


class MetricsResponse(BaseModel):
    run_id: str
    summary: str = ""
    total_cases: int = 0
    completed_cases: int = 0
    failed_cases: int = 0
    cases: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ComponentInfo(BaseModel):
    name: str
    description: str
    stage: str
    config_schema: dict[str, Any] = Field(default_factory=dict)


class CustomComponentInfo(BaseModel):
    filename: str
    class_name: str | None = None
    stage: str | None = None
    description: str = ""
    config_schema: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    detail: str


class ValidateRequest(BaseModel):
    """POST /api/v1/validate"""

    manifest: dict[str, Any] = Field(description="待校验的 manifest 配置")


class ValidateResponse(BaseModel):
    valid: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)


class ComponentContentResponse(BaseModel):
    filename: str
    content: str


class ComponentUpdateRequest(BaseModel):
    content: str = Field(description="Python 源码文本")
