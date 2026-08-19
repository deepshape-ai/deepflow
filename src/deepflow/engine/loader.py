from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

from deepflow.core.component import (
    BaseComponent,
)
from deepflow.models.manifest import RetryConfig

_STAGE_MAP: dict[str, str] = {
    "PreprocessComponent": "preprocess",
    "CasewiseComponent": "casewise",
    "PostprocessComponent": "postprocess",
}
_IMPORT_LOCK = threading.RLock()


class ComponentLoader:
    """
    组件加载器。

    支持两种格式：
    1. Plugin 组件：namespace:name → deepflow.plugins.{namespace}.{name}:{Name}
    2. 外部组件：./path/to/file.py[:ClassName]
    """

    @classmethod
    def load(
        cls,
        src: str,
        config: dict[str, Any] | None = None,
        retry: RetryConfig | None = None,
        manifest_dir: Path | None = None,
    ) -> BaseComponent:
        if src.startswith(("./", "/")):
            component = cls._load_external(src, config, manifest_dir or Path.cwd())
        elif ":" in src:
            component = cls._load_plugin(src, config)
        else:
            raise ValueError(
                f"无效的组件引用: {src}\n"
                f"格式: namespace:name（plugin 组件）或 ./path.py（本地组件）"
            )

        if retry:
            component.with_retry(retry)

        return component

    @classmethod
    def resolve_class(
        cls,
        src: str,
        manifest_dir: Path | None = None,
    ) -> type[BaseComponent]:
        """仅解析组件类，不实例化。用于 check 命令校验。"""
        if src.startswith(("./", "/")):
            file_path, class_name = cls._parse_external_src(src, manifest_dir or Path.cwd())
            module = cls._load_module_from_file(file_path, manifest_dir)
            return getattr(module, class_name)
        elif ":" in src:
            namespace, name = src.split(":", 1)
            module_path = f"deepflow.plugins.{namespace}.{name}"
            class_name = "".join(w.capitalize() for w in name.split("_"))
            module = importlib.import_module(module_path)
            return getattr(module, class_name)
        else:
            raise ValueError(f"无效的组件引用: {src}")

    @classmethod
    def get_plugin_metadata(cls) -> list[dict[str, Any]]:
        """扫描所有已安装 plugin 的组件元信息。"""
        import pkgutil

        import deepflow.plugins as plugins_pkg

        result = []
        for _importer, namespace, is_pkg in pkgutil.iter_modules(plugins_pkg.__path__):
            if not is_pkg:
                continue
            ns_module = importlib.import_module(f"deepflow.plugins.{namespace}")
            for _, mod_name, _ in pkgutil.iter_modules(ns_module.__path__):
                if mod_name.startswith("_"):
                    continue
                try:
                    module = importlib.import_module(f"deepflow.plugins.{namespace}.{mod_name}")
                    class_name = "".join(w.capitalize() for w in mod_name.split("_"))
                    comp_class = getattr(module, class_name, None)
                    if comp_class is None or not issubclass(comp_class, BaseComponent):
                        continue

                    stage = "unknown"
                    for base in comp_class.__mro__:
                        if base.__name__ in _STAGE_MAP:
                            stage = _STAGE_MAP[base.__name__]
                            break

                    config_schema: dict[str, Any] = {}
                    if hasattr(comp_class, "Config") and comp_class.Config is not None:
                        config_schema = comp_class.Config.model_json_schema()

                    result.append({
                        "name": f"{namespace}:{mod_name}",
                        "description": (getattr(comp_class, "__doc__", "") or "").strip(),
                        "stage": stage,
                        "config_schema": config_schema,
                    })
                except Exception:
                    continue
        return result

    @classmethod
    def _load_plugin(cls, src: str, config: dict[str, Any] | None) -> BaseComponent:
        namespace, name = src.split(":", 1)
        module_path = f"deepflow.plugins.{namespace}.{name}"
        class_name = "".join(w.capitalize() for w in name.split("_"))

        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError:
            raise ValueError(
                f"Plugin 组件未找到: {src}\n"
                f"请确认 deepflow.plugins.{namespace} 包存在且包含 {name}.py"
            ) from None

        comp_class = getattr(module, class_name, None)
        if comp_class is None:
            raise ValueError(
                f"Plugin 模块 {module_path} 中未找到类 {class_name}"
            )

        return comp_class(config=config)

    @classmethod
    def _parse_external_src(
        cls, src: str, manifest_dir: Path
    ) -> tuple[Path, str]:
        if ":" in src:
            path_part, class_name = src.rsplit(":", 1)
            file_path = (manifest_dir / path_part).absolute()
        else:
            file_path = (manifest_dir / src).absolute()
            class_name = "".join(word.capitalize() for word in file_path.stem.split("_"))
        return file_path, class_name

    @classmethod
    def _load_external(
        cls, src: str, config: dict[str, Any] | None, manifest_dir: Path
    ) -> BaseComponent:
        file_path, class_name = cls._parse_external_src(src, manifest_dir)

        if not file_path.exists():
            raise FileNotFoundError(f"组件文件未找到: {file_path}")

        module = cls._load_module_from_file(file_path, manifest_dir)
        return getattr(module, class_name)(config=config)

    @classmethod
    def _load_module_from_file(
        cls, file_path: Path, import_root: Path | None = None
    ) -> ModuleType:
        """Load local source in a manifest-unique namespace package.

        Local source-to-source imports must be relative, for example
        ``from .helper import VALUE``. This prevents concurrent server runs from
        sharing bare module names through process-global ``sys.modules``.
        """
        absolute_file = file_path.absolute()
        root = (import_root or file_path.parent).absolute()
        try:
            relative = absolute_file.relative_to(root).with_suffix("")
        except ValueError as error:
            raise ValueError(f"本地源码必须位于 manifest 目录内: {file_path}") from error

        digest = hashlib.sha256(str(root).encode()).hexdigest()[:16]
        package_name = f"_deepflow_manifest_{digest}"
        module_name = ".".join((package_name, *relative.parts))

        with _IMPORT_LOCK:
            cached = sys.modules.get(module_name)
            if cached is not None:
                return cached

            cls._ensure_namespace_package(package_name, root)
            current_name = package_name
            current_path = root
            for part in relative.parts[:-1]:
                current_name = f"{current_name}.{part}"
                current_path /= part
                cls._ensure_namespace_package(current_name, current_path)

            spec = importlib.util.spec_from_file_location(module_name, absolute_file)
            if spec is None or spec.loader is None:
                raise ImportError(f"无法加载模块: {file_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(module_name, None)
                raise
        return module

    @staticmethod
    def _ensure_namespace_package(name: str, path: Path) -> None:
        if name in sys.modules:
            return
        package = ModuleType(name)
        package.__package__ = name
        package.__path__ = [str(path)]  # type: ignore[attr-defined]
        sys.modules[name] = package
