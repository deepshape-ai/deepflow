"""Core 模块 - 基类与接口"""

from deepflow.core.component import (
    BaseComponent,
    CasewiseComponent,
    CasewiseOutput,
    PostprocessComponent,
    PostprocessOutput,
    PreprocessComponent,
    PreprocessOutput,
)
from deepflow.core.context import CaseContext, PipelineContext
from deepflow.core.errors import FatalError
from deepflow.core.hook import Hook, HookContext, HookDispatcher
from deepflow.core.iterator import BaseIterator, MemoryIterator
from deepflow.core.store import ContextStore

__all__ = [
    # Component base classes
    "BaseComponent",
    "PreprocessComponent",
    "CasewiseComponent",
    "PostprocessComponent",
    # Output types (component-facing)
    "PreprocessOutput",
    "CasewiseOutput",
    "PostprocessOutput",
    # Error types
    "FatalError",
    # Context types
    "PipelineContext",
    "CaseContext",
    # Iterator types
    "BaseIterator",
    "MemoryIterator",
    # Store
    "ContextStore",
    # Hook 总线
    "Hook",
    "HookContext",
    "HookDispatcher",
]
