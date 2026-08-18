# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import shutil

from pydantic import BaseModel

from deepflow.core.component import PreprocessComponent, PreprocessOutput
from deepflow.core.context import PipelineContext

logger = logging.getLogger(__name__)


class CleanWorkspace(PreprocessComponent):
    """清理并重建 workspace 目录

    在 pipeline 开始前清空 workspace，确保每次运行从干净状态开始。
    自动重建 workspace 和 metrics 子目录。

    Config: 无需配置
    """

    class Config(BaseModel):
        pass

    def execute(self, ctx: PipelineContext) -> PreprocessOutput:
        workspace_dir = ctx.workspace
        if workspace_dir.exists() and workspace_dir.is_dir():
            shutil.rmtree(workspace_dir)
            logger.debug(f"已清理workspace目录: {workspace_dir}")

        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "metrics").mkdir(parents=True, exist_ok=True)

        return PreprocessOutput(message=f"workspace目录已清理: {workspace_dir}")
