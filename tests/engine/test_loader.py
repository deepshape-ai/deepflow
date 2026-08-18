"""ComponentLoader 解析规则单测：外部组件 / builtin 插件 / 错误路径。"""

from __future__ import annotations

import pytest

from deepflow.engine.loader import ComponentLoader
from deepflow.models.manifest import RetryConfig

ECHO_SRC = """\
from deepflow import CasewiseComponent, CasewiseOutput


class EchoGen(CasewiseComponent):
    def execute(self, ctx):
        return CasewiseOutput()
"""

CUSTOM_SRC = """\
from deepflow import CasewiseComponent, CasewiseOutput


class Whatever(CasewiseComponent):
    def execute(self, ctx):
        return CasewiseOutput()
"""


class TestExternalComponents:
    def test_auto_class_name_from_snake_case(self, tmp_path):
        (tmp_path / "echo_gen.py").write_text(ECHO_SRC, encoding="utf-8")

        cls = ComponentLoader.resolve_class("./echo_gen.py", tmp_path)

        assert cls.__name__ == "EchoGen"

    def test_explicit_class_name(self, tmp_path):
        (tmp_path / "custom.py").write_text(CUSTOM_SRC, encoding="utf-8")

        cls = ComponentLoader.resolve_class("./custom.py:Whatever", tmp_path)

        assert cls.__name__ == "Whatever"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="组件文件未找到"):
            ComponentLoader.load("./nope.py", manifest_dir=tmp_path)

    def test_missing_class_raises(self, tmp_path):
        (tmp_path / "custom.py").write_text(CUSTOM_SRC, encoding="utf-8")

        with pytest.raises(AttributeError):
            ComponentLoader.resolve_class("./custom.py:NotDefined", tmp_path)


class TestPluginComponents:
    def test_builtin_resolution(self):
        cls = ComponentLoader.resolve_class("builtin:clean_workspace")

        assert cls.__name__ == "CleanWorkspace"

    def test_builtin_load_applies_config_and_retry(self):
        comp = ComponentLoader.load(
            "builtin:clean_workspace",
            retry=RetryConfig(max_attempts=4, delay=2.0),
        )

        assert comp._retry.max_attempts == 4
        assert comp._retry.delay == 2.0

    def test_unknown_plugin_raises(self):
        with pytest.raises(ValueError, match="未找到"):
            ComponentLoader.load("builtin:no_such_thing")


class TestSrcValidation:
    def test_invalid_src_format_raises(self):
        with pytest.raises(ValueError, match="无效的组件引用"):
            ComponentLoader.load("no-prefix.py")
