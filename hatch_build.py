"""
Hatch custom build hook：在构建 wheel 时自动编译 web UI。

执行 pip wheel . / pip install . / python -m build 时自动触发，
无需手动 cd web && pnpm install && pnpm run build。

包管理器优先级：pnpm > npm（根据 lockfile 自动检测）。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

WEB_DIR = Path(__file__).parent / "web"
STATIC_DIR = Path(__file__).parent / "src" / "deepflow" / "server" / "static"


def _detect_pm(web_dir: Path) -> tuple[str, list[str], list[str]] | None:
    """根据 lockfile 检测包管理器，返回 (binary, install_args, build_args) 或 None。"""
    # pnpm（优先：项目已有 pnpm-lock.yaml）
    if (web_dir / "pnpm-lock.yaml").exists():
        pnpm = shutil.which("pnpm")
        if pnpm:
            return pnpm, ["install", "--frozen-lockfile"], ["run", "build"]

    # npm
    npm = shutil.which("npm")
    if npm:
        if (web_dir / "package-lock.json").exists():
            return npm, ["ci", "--prefer-offline"], ["run", "build"]
        return npm, ["install"], ["run", "build"]

    return None


class WebBuildHook(BuildHookInterface):
    PLUGIN_NAME = "web"

    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel":
            return

        if not (WEB_DIR / "package.json").exists():
            self._warn("web/package.json not found, skipping web build")
            return

        pm = _detect_pm(WEB_DIR)
        if pm is None:
            self._warn("No package manager found, skipping web build (install pnpm or npm)")
            return

        binary, install_args, build_args = pm
        pm_name = Path(binary).stem
        self._info(f"Building web UI with {pm_name}...")

        self._run(binary, *install_args)
        self._run(binary, *build_args)
        self._info(f"Web UI built -> {STATIC_DIR.relative_to(Path.cwd())}")

    def _run(self, binary: str, *args: str) -> None:
        result = subprocess.run(
            [binary, *args],
            cwd=WEB_DIR,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pm_name = Path(binary).stem
            raise RuntimeError(
                f"{pm_name} {' '.join(args)} failed:\n{result.stderr.strip()}"
            )

    def _info(self, msg: str) -> None:
        self.app.display_info(f"[web] {msg}")

    def _warn(self, msg: str) -> None:
        self.app.display_warning(f"[web] {msg}")
