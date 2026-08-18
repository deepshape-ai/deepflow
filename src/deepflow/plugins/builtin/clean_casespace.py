# -*- coding: utf-8 -*-

from __future__ import annotations

import logging
import shutil

from pydantic import BaseModel

from deepflow.core.component import CasewiseComponent, CasewiseOutput
from deepflow.core.context import CaseContext

logger = logging.getLogger(__name__)


class CleanCasespace(CasewiseComponent):
    """清理当前 case 的 casespace 目录

    删除 casespace 中的所有文件，用于在 casewise 阶段开始前
    或结束后释放磁盘空间。

    Config: 无需配置
    """

    class Config(BaseModel):
        pass

    def execute(self, ctx: CaseContext) -> CasewiseOutput:
        casespace_dir = ctx.casespace
        if casespace_dir.exists() and casespace_dir.is_dir():
            shutil.rmtree(casespace_dir)
            logger.debug(f"已清理casespace目录: {casespace_dir}")
        else:
            logger.warning(f"casespace目录不存在或不是一个目录: {casespace_dir}")

        return CasewiseOutput(message=f"casespace目录已清理: {casespace_dir}")
