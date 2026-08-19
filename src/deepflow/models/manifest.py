from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(data: Any) -> Any:
    """递归解析数据结构中的 ${VAR_NAME} 环境变量引用"""
    if isinstance(data, str):
        return _ENV_VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(0)), data)
    if isinstance(data, dict):
        return {k: _resolve_env_vars(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_resolve_env_vars(item) for item in data]
    return data


def extract_env_refs(data: Any) -> set[str]:
    """提取数据结构中所有 ${VAR_NAME} 引用的变量名"""
    refs: set[str] = set()
    if isinstance(data, str):
        refs.update(m.group(1) for m in _ENV_VAR_PATTERN.finditer(data))
    elif isinstance(data, dict):
        for v in data.values():
            refs.update(extract_env_refs(v))
    elif isinstance(data, list):
        for item in data:
            refs.update(extract_env_refs(item))
    return refs


class RetryConfig(BaseModel):
    """步骤重试配置"""

    max_attempts: int = Field(default=1, ge=1, description="最大尝试次数，1 表示不重试")
    delay: float = Field(default=1.0, gt=0, description="重试间隔（秒）")
    backoff: Literal["fixed", "exponential"] = Field(
        default="fixed", description="退避策略：fixed 固定间隔，exponential 指数退避"
    )


class StepConfig(BaseModel):
    """单个步骤配置"""

    src: str = Field(description="组件源：namespace:name 或 ./path.py")
    config: dict[str, Any] = Field(default_factory=dict, description="组件业务配置")
    retry: RetryConfig = Field(default_factory=RetryConfig, description="重试配置")


class HookConfig(BaseModel):
    """单个 hook 配置"""

    src: str = Field(description="hook 源:namespace:name 或 ./path.py")
    config: dict[str, Any] = Field(default_factory=dict, description="hook 配置")


class PipelineConfig(BaseModel):
    """Pipeline 配置：三个阶段"""

    preprocess: list[StepConfig] = Field(default_factory=list)
    casewise: list[StepConfig] = Field(default_factory=list)
    postprocess: list[StepConfig] = Field(default_factory=list)


class Manifest(BaseModel):
    """Manifest 顶层配置

    框架参数（workspace, concurrency）在根级声明。
    用户自定义变量放在 vars 中，所有组件通过 ctx.vars 访问。
    """

    version: str = Field(default="2.0")
    name: str = Field(description="Pipeline 名称")
    workspace: Path = Field(default=Path("./workspace"), description="工作目录")
    concurrency: int = Field(default=1, ge=1, le=100, description="并发数")
    vars: dict[str, Any] = Field(default_factory=dict, description="用户自定义变量，所有组件通过 ctx.vars 访问")
    pipeline: PipelineConfig
    hooks: list[HookConfig] = Field(default_factory=list, description="生命周期观察 hook")

    @model_validator(mode="before")
    @classmethod
    def resolve_env_variables(cls, data: Any) -> Any:
        """在模型校验前递归解析所有 ${VAR_NAME} 环境变量引用"""
        return _resolve_env_vars(data)
