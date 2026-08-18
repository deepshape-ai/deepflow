"""从自定义组件 .py 文件中提取 Config schema 元信息。"""

from __future__ import annotations

import importlib.util
import logging
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepflow.core.component import (
    BaseComponent,
    CasewiseComponent,
    PostprocessComponent,
    PreprocessComponent,
)

logger = logging.getLogger(__name__)

_STAGE_MAP: dict[type, str] = {
    PreprocessComponent: "preprocess",
    CasewiseComponent: "casewise",
    PostprocessComponent: "postprocess",
}


@dataclass
class ComponentMeta:
    """自定义组件提取出的元信息。"""
    filename: str
    class_name: str
    stage: str | None
    description: str
    config_schema: dict[str, Any] | None


def extract_from_file(file_path: Path) -> ComponentMeta | None:
    """从 .py 文件中 import 并提取组件元信息。失败时返回 None。"""
    module_name = f"_deepflow_extract_{uuid.uuid4().hex[:8]}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # 查找第一个继承自 BaseComponent 的具体类
        comp_class = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseComponent)
                and attr is not BaseComponent
                and attr not in (PreprocessComponent, CasewiseComponent, PostprocessComponent)
            ):
                comp_class = attr
                break

        if comp_class is None:
            return None

        # 推导 stage
        stage = None
        for base_cls, stage_name in _STAGE_MAP.items():
            if issubclass(comp_class, base_cls):
                stage = stage_name
                break

        # 提取 config_schema
        config_schema = None
        if hasattr(comp_class, "Config") and comp_class.Config is not None:
            try:
                config_schema = comp_class.Config.model_json_schema()
            except Exception:
                pass

        return ComponentMeta(
            filename=file_path.name,
            class_name=comp_class.__name__,
            stage=stage,
            description=(getattr(comp_class, "__doc__", "") or "").strip(),
            config_schema=config_schema,
        )
    except Exception as e:
        logger.warning(f"组件元信息提取失败 ({file_path.name}): {e}")
        return None
    finally:
        sys.modules.pop(module_name, None)
