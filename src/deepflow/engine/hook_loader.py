"""Hook 加载器:复用组件加载的模块解析机制。

src 格式与组件一致:
    1. Plugin:namespace:name → deepflow.plugins.{namespace}.{name}:{Name}
    2. 外部:./path/to/file.py[:ClassName]
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from deepflow.core.hook import Hook
from deepflow.engine.loader import ComponentLoader


class HookLoader:
    """加载 manifest 声明的 hook,返回 Hook 实例。"""

    @classmethod
    def resolve_class(cls, src: str, manifest_dir: Path | None = None) -> type[Hook]:
        """解析并校验 Hook 类,不实例化。供 check 与运行时共用。"""
        if src.startswith(("./", "/")):
            file_path, class_name = ComponentLoader._parse_external_src(
                src, manifest_dir or Path.cwd()
            )
            if not file_path.exists():
                raise FileNotFoundError(f"Hook 文件未找到: {file_path}")
            module = ComponentLoader._load_module_from_file(file_path, manifest_dir)
        elif ":" in src:
            namespace, name = src.split(":", 1)
            module_path = f"deepflow.plugins.{namespace}.{name}"
            class_name = "".join(word.capitalize() for word in name.split("_"))
            try:
                module = importlib.import_module(module_path)
            except ModuleNotFoundError:
                raise ValueError(
                    f"Plugin hook 未找到: {src}\n"
                    f"请确认 deepflow.plugins.{namespace} 包存在且包含 {name}.py"
                ) from None
        else:
            raise ValueError(
                f"无效的 hook 引用: {src}\n"
                f"格式: namespace:name(plugin hook)或 ./path.py(本地 hook)"
            )

        hook_class = getattr(module, class_name, None)
        if hook_class is None:
            raise ValueError(f"Hook 来源 {src} 中未找到类 {class_name}")
        if not isinstance(hook_class, type) or not issubclass(hook_class, Hook):
            raise TypeError(f"hook {src} 的 {class_name} 不是 Hook 子类")
        return hook_class

    @classmethod
    def load(
        cls,
        src: str,
        config: dict[str, Any] | None = None,
        manifest_dir: Path | None = None,
    ) -> Hook:
        hook_class = cls.resolve_class(src, manifest_dir)
        return hook_class(config=config)
