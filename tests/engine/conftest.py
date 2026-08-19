"""引擎测试共享 fixtures：组件文件写入 + Manifest 构建。

组件以真实 .py 文件落在 tmp_path，经 ComponentLoader 加载，
使 orchestrator 测试覆盖完整的 loader → 组件 → 引擎链路。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from deepflow.models.manifest import Manifest


@pytest.fixture
def write_component(tmp_path: Path):
    """将组件源码写入临时目录，返回 manifest 可用的 src 引用。"""

    def _write(filename: str, source: str) -> str:
        path = tmp_path / filename
        path.write_text(dedent(source), encoding="utf-8")
        return f"./{filename}"

    return _write


@pytest.fixture
def make_manifest(tmp_path: Path):
    """构建 workspace 固定在 tmp_path/workspace 的 Manifest。"""

    def _make(
        *,
        preprocess: list[str],
        casewise: list[str | dict[str, Any]] | None = None,
        postprocess: list[str] | None = None,
        concurrency: int = 2,
        hooks: list[dict[str, Any]] | None = None,
    ) -> Manifest:
        def step(entry: str | dict[str, Any]) -> dict[str, Any]:
            return entry if isinstance(entry, dict) else {"src": entry}

        data: dict[str, Any] = {
            "version": "2.0",
            "name": "engine-test",
            "workspace": str(tmp_path / "workspace"),
            "concurrency": concurrency,
            "pipeline": {
                "preprocess": [step(s) for s in preprocess],
                "casewise": [step(s) for s in (casewise or [])],
                "postprocess": [step(s) for s in (postprocess or [])],
            },
        }
        if hooks:
            data["hooks"] = hooks
        return Manifest.model_validate(data)

    return _make
