from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DatasetItem(BaseModel):
    """数据集中的单个用例

    字段说明：
        id: 用例唯一标识（必需，不可重复）

    除 id 外，所有字段均为自定义字段，可直接在构造时传入任意键值对。

    示例::

        DatasetItem(id="case-1", source="data/1.mp4", fps=30, duration=120)

    访问自定义字段::

        item.source   # "data/1.mp4"
        item.fps      # 30
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="用例唯一标识")
