from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeAlias

from deepflow.core.context import CaseContext, PipelineContext
from deepflow.core.error_formatter import ErrorFormatter
from deepflow.core.errors import FatalError
from deepflow.core.iterator import BaseIterator
from deepflow.models.manifest import RetryConfig

logger = logging.getLogger(__name__)

ComponentContext: TypeAlias = CaseContext | PipelineContext


# --- 组件面向：execute() 返回类型 ---


@dataclass
class StageOutput:
    """execute() 返回的基础类型，不含 status。"""

    message: str = ""


@dataclass
class PreprocessOutput(StageOutput):
    """preprocess execute() 返回类型，携带 iterator。"""

    iterator: BaseIterator | None = None


@dataclass
class CasewiseOutput(StageOutput):
    """casewise execute() 返回类型，携带 metrics。"""

    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class PostprocessOutput(StageOutput):
    """postprocess execute() 返回类型，继承 StageOutput。"""
    pass


# --- 框架内部：run() 返回类型 ---


class StageStatus(Enum):
    SKIPPED = "skipped"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class StageResult:
    """框架内部结果，组合 status + output。

    error 携带导致 FAILED 的原始异常，供上层直接读取错误类型与信息，
    避免从格式化后的 message 字符串反向解析。
    """

    status: StageStatus
    output: StageOutput = field(default_factory=StageOutput)
    error: Exception | None = None


class BaseComponent(ABC):
    """
    所有 Pipeline 组件的抽象基类。

    使用模板方法模式：check_skip -> execute -> callback。
    重试逻辑由框架在 run() 中处理，组件只需实现 execute()。

    组件规范：
    - 继承 PreprocessComponent / CasewiseComponent / PostprocessComponent
    - 定义 Config 内部类（继承 pydantic.BaseModel）声明配置项
    - 编写类 docstring 作为组件描述
    """

    Config: type | None = None  # 子类用 Pydantic BaseModel 覆盖

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self._retry = RetryConfig()

    def with_retry(self, retry: RetryConfig) -> BaseComponent:
        """链式设置重试配置，由 loader 调用"""
        self._retry = retry
        return self

    def run(self, ctx: ComponentContext) -> StageResult:
        """模板方法：check_skip -> 重试循环(execute) -> callback"""
        if self.check_skip(ctx):
            return self._on_skip(ctx)

        last_error: Exception | None = None
        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                output = self.execute(ctx)
                result = self._to_result(output)
                self._on_success(ctx, result)
                return result
            except FatalError:
                raise  # 不重试，直接向上抛出
            except Exception as e:
                last_error = e
                if attempt < self._retry.max_attempts:
                    delay = self._calculate_delay(attempt)
                    warning_msg = ErrorFormatter.format_retry_warning(
                        component_name=self.__class__.__name__,
                        attempt=attempt,
                        max_attempts=self._retry.max_attempts,
                        error=e,
                        delay=delay,
                    )
                    logger.warning(warning_msg)
                    time.sleep(delay)

        return self._on_failure(ctx, last_error)  # type: ignore[arg-type]

    def check_skip(self, ctx: ComponentContext) -> bool:
        """覆写此方法实现跳过逻辑"""
        return False

    @abstractmethod
    def execute(self, ctx: ComponentContext) -> StageOutput:
        """核心执行逻辑，子类必须实现"""
        ...

    @staticmethod
    def _to_result(output: StageOutput) -> StageResult:
        return StageResult(status=StageStatus.SUCCESS, output=output)

    def _on_skip(self, ctx: ComponentContext) -> StageResult:
        return StageResult(
            status=StageStatus.SKIPPED,
            output=StageOutput(message="Skipped by check_skip"),
        )

    def _on_success(self, ctx: ComponentContext, result: StageResult) -> None:
        pass

    def _on_failure(self, ctx: ComponentContext, error: Exception) -> StageResult:
        log_message, result_message = ErrorFormatter.format_component_error(
            component_name=self.__class__.__name__,
            error=error,
        )

        logger.error(log_message)

        return StageResult(
            status=StageStatus.FAILED,
            output=StageOutput(
                message=result_message,
            ),
            error=error,
        )

    def _calculate_delay(self, attempt: int) -> float:
        match self._retry.backoff:
            case "exponential":
                return self._retry.delay * (2 ** (attempt - 1))
            case _:
                return self._retry.delay


class PreprocessComponent(BaseComponent):
    """preprocess 阶段组件基类"""

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> PreprocessOutput: ...


class CasewiseComponent(BaseComponent):
    """casewise 阶段组件基类"""

    @abstractmethod
    def execute(self, ctx: CaseContext) -> CasewiseOutput: ...


class PostprocessComponent(BaseComponent):
    """postprocess 阶段组件基类"""

    @abstractmethod
    def execute(self, ctx: PipelineContext) -> StageOutput: ...
