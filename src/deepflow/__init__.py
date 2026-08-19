"""DeepFlow — runner

声明式三阶段 Pipeline 执行框架。

快速开始:
    >>> from deepflow import PreprocessComponent, MemoryIterator, DatasetItem
    >>> class MyPreprocess(PreprocessComponent):
    ...     def execute(self, ctx):
    ...         items = [DatasetItem(id="1"), DatasetItem(id="2")]
    ...         return PreprocessOutput(iterator=MemoryIterator(items))
"""

__version__ = "0.3.0"

# Core components - 组件基类
# Output types - 输出类型
# Context types - 上下文类型
# Iterator types - 迭代器类型
from deepflow.core import (
    BaseComponent,
    BaseIterator,
    CaseContext,
    CasewiseComponent,
    CasewiseOutput,
    ContextStore,
    FatalError,
    Hook,
    HookContext,
    MemoryIterator,
    PipelineContext,
    PostprocessComponent,
    PostprocessOutput,
    PreprocessComponent,
    PreprocessOutput,
)

# Data models - 数据模型
from deepflow.models import DatasetItem

__all__ = [
    # Version
    "__version__",
    # Component base classes
    "BaseComponent",
    "PreprocessComponent",
    "CasewiseComponent",
    "PostprocessComponent",
    # Output types (component-facing)
    "PreprocessOutput",
    "CasewiseOutput",
    "PostprocessOutput",
    # Context types
    "PipelineContext",
    "CaseContext",
    # Iterator types
    "BaseIterator",
    "MemoryIterator",
    # Error types
    "FatalError",
    # Store
    "ContextStore",
    # Hook 总线
    "Hook",
    "HookContext",
    # Data models
    "DatasetItem",
]
