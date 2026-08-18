from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepflow.core.store import ContextStore

if TYPE_CHECKING:
    from deepflow.metrics import MetricsCollector
    from deepflow.models.dataset import DatasetItem


@dataclass
class CaseContext:
    """casewise 阶段使用，每个 case 独立实例"""

    case: DatasetItem
    casespace: Path
    vars: dict[str, Any] = field(default_factory=dict)
    store: ContextStore = field(default_factory=ContextStore)

    def ensure_dirs(self) -> None:
        self.casespace.mkdir(parents=True, exist_ok=True)


@dataclass
class PipelineContext:
    """preprocess / postprocess 阶段使用"""

    workspace: Path
    metrics_collector: MetricsCollector
    vars: dict[str, Any] = field(default_factory=dict)
    store: ContextStore = field(default_factory=ContextStore)
