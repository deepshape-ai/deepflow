"""
Pipeline 配置存储 — 目录级 CRUD。

遵循 deepflow 的 "文件即接口" 哲学：
每条 Pipeline 拥有独立目录，包含配置 JSON、组件与 hook 源码。

目录结构：
    pipelines/{id}/
    ├── pipeline.json        # 配置元数据
    ├── components/          # 自定义组件 .py 文件
    └── hooks/               # 自定义 hook .py 文件
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from deepflow.server.models import PipelineResponse

_EXPORT_VERSION = "2.0"


class PipelineStore:
    """Pipeline 配置的目录存储，一个目录一条记录。"""

    def __init__(self, store_dir: Path) -> None:
        self._dir = store_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── Pipeline CRUD ──────────────────────────────────────────

    def create(self, name: str, manifest: dict[str, Any]) -> PipelineResponse:
        pipeline_id = uuid.uuid4().hex[:8]
        now = datetime.now(timezone.utc)

        pipeline_dir = self._pipeline_dir(pipeline_id)
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        (pipeline_dir / "components").mkdir(exist_ok=True)
        (pipeline_dir / "hooks").mkdir(exist_ok=True)

        data = {
            "id": pipeline_id,
            "name": name,
            "manifest": manifest,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        self._write_json(pipeline_id, data)
        return PipelineResponse(**data)

    def get(self, pipeline_id: str) -> PipelineResponse | None:
        path = self._json_path(pipeline_id)
        if not path.exists():
            return None
        return PipelineResponse(**json.loads(path.read_text(encoding="utf-8")))

    def list_all(self) -> list[PipelineResponse]:
        results = []
        for pipeline_dir in sorted(self._dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if pipeline_dir.name.startswith("."):
                continue
            json_path = pipeline_dir / "pipeline.json"
            if json_path.exists():
                data = json.loads(json_path.read_text(encoding="utf-8"))
                results.append(PipelineResponse(**data))
        return results

    def update(
        self,
        pipeline_id: str,
        name: str | None = None,
        manifest: dict[str, Any] | None = None,
    ) -> PipelineResponse | None:
        path = self._json_path(pipeline_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if name is not None:
            data["name"] = name
        if manifest is not None:
            data["manifest"] = manifest
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_json(pipeline_id, data)
        return PipelineResponse(**data)

    def delete(self, pipeline_id: str) -> bool:
        pipeline_dir = self._pipeline_dir(pipeline_id)
        if not pipeline_dir.exists():
            return False
        shutil.rmtree(pipeline_dir)
        return True

    # ── 组件文件管理 ───────────────────────────────────────────

    def get_pipeline_dir(self, pipeline_id: str) -> Path | None:
        """返回 pipeline 根目录，用作 Orchestrator 的 manifest_dir。"""
        pipeline_dir = self._pipeline_dir(pipeline_id)
        return pipeline_dir if pipeline_dir.exists() else None

    def save_component(self, pipeline_id: str, filename: str, content: bytes) -> Path:
        """保存组件文件到 pipeline 的 components 目录。"""
        filename = self._validate_python_filename(filename)
        comp_dir = self._pipeline_dir(pipeline_id) / "components"
        comp_dir.mkdir(parents=True, exist_ok=True)
        path = comp_dir / filename
        self._write_bytes_atomic(path, content)
        return path

    def list_components(self, pipeline_id: str) -> list[str]:
        """列出 pipeline 的所有组件文件名。"""
        comp_dir = self._pipeline_dir(pipeline_id) / "components"
        if not comp_dir.exists():
            return []
        return sorted(p.name for p in comp_dir.glob("*.py"))

    def list_component_paths(self, pipeline_id: str) -> list[Path]:
        """列出 pipeline 的所有组件文件完整路径。"""
        comp_dir = self._pipeline_dir(pipeline_id) / "components"
        if not comp_dir.exists():
            return []
        return sorted(comp_dir.glob("*.py"))

    def read_component(self, pipeline_id: str, filename: str) -> str | None:
        """读取组件文件内容，不存在返回 None。"""
        filename = self._validate_python_filename(filename)
        path = self._pipeline_dir(pipeline_id) / "components" / filename
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def delete_component(self, pipeline_id: str, filename: str) -> bool:
        """删除指定组件文件。"""
        filename = self._validate_python_filename(filename)
        path = self._pipeline_dir(pipeline_id) / "components" / filename
        if not path.exists():
            return False
        path.unlink()
        return True

    # ── Hook 文件管理 ──────────────────────────────────────────

    def save_hook(self, pipeline_id: str, filename: str, content: bytes) -> Path:
        filename = self._validate_python_filename(filename)
        hook_dir = self._pipeline_dir(pipeline_id) / "hooks"
        hook_dir.mkdir(parents=True, exist_ok=True)
        path = hook_dir / filename
        self._write_bytes_atomic(path, content)
        return path

    def list_hook_paths(self, pipeline_id: str) -> list[Path]:
        hook_dir = self._pipeline_dir(pipeline_id) / "hooks"
        return sorted(hook_dir.glob("*.py")) if hook_dir.exists() else []

    def read_hook(self, pipeline_id: str, filename: str) -> str | None:
        filename = self._validate_python_filename(filename)
        path = self._pipeline_dir(pipeline_id) / "hooks" / filename
        return path.read_text(encoding="utf-8") if path.exists() else None

    def delete_hook(self, pipeline_id: str, filename: str) -> bool:
        filename = self._validate_python_filename(filename)
        path = self._pipeline_dir(pipeline_id) / "hooks" / filename
        if not path.exists():
            return False
        path.unlink()
        return True

    # ── 导出/导入 ──────────────────────────────────────────────

    def export_yaml(self, pipeline_id: str) -> str | None:
        """导出 pipeline 为 YAML 文本（manifest + 组件与 hook 源码）。

        返回单个可读 YAML 字符串，包含 manifest 和所有组件文件内容。
        pipeline 不存在时返回 None。
        """
        path = self._json_path(pipeline_id)
        if not path.exists():
            return None

        data = json.loads(path.read_text(encoding="utf-8"))
        components: dict[str, str] = {}
        for py_path in self.list_component_paths(pipeline_id):
            components[py_path.name] = py_path.read_text(encoding="utf-8")
        hooks = {
            py_path.name: py_path.read_text(encoding="utf-8")
            for py_path in self.list_hook_paths(pipeline_id)
        }

        export_data: dict[str, Any] = {
            "deepflow_export": _EXPORT_VERSION,
            "name": data["name"],
            "manifest": data["manifest"],
        }
        if components:
            export_data["components"] = components
        if hooks:
            export_data["hooks"] = hooks

        return yaml.dump(export_data, allow_unicode=True, sort_keys=False, default_flow_style=False)

    def import_yaml(self, yaml_text: str) -> PipelineResponse:
        """从 YAML 文本创建 pipeline（分配新 ID）。

        期望格式：
            deepflow_export: "2.0"
            name: ...
            manifest: { ... }
            components:           # 可选
              filename.py: |
                source code ...

        Raises:
            ValueError: YAML 无效或缺少必要字段。
        """
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError as e:
            raise ValueError(f"YAML 解析失败: {e}") from e

        if not isinstance(data, dict) or "manifest" not in data:
            raise ValueError("缺少 manifest 字段")
        if data.get("deepflow_export") != _EXPORT_VERSION:
            raise ValueError(
                f"不支持的 deepflow_export 版本: {data.get('deepflow_export')!r}; "
                f"仅支持 {_EXPORT_VERSION}"
            )

        manifest = data["manifest"]
        if not isinstance(manifest, dict):
            raise ValueError("manifest 必须是对象")
        name = data.get("name") or manifest.get("name", "imported-pipeline")
        if not isinstance(name, str):
            raise ValueError("name 必须是字符串")
        components: dict[str, str] = data.get("components", {})
        hooks: dict[str, str] = data.get("hooks", {})
        if not isinstance(components, dict) or not isinstance(hooks, dict):
            raise ValueError("components 与 hooks 必须是文件名到源码的对象")
        for filename, source in (*components.items(), *hooks.items()):
            self._validate_python_filename(filename)
            if not isinstance(source, str):
                raise ValueError(f"源码必须是字符串: {filename}")

        pipeline_id = uuid.uuid4().hex[:8]
        now = datetime.now(timezone.utc)

        record = {
            "id": pipeline_id,
            "name": name,
            "manifest": manifest,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        response = PipelineResponse(**record)
        pipeline_dir = self._pipeline_dir(pipeline_id)
        staging_dir = self._dir / f".import-{pipeline_id}"
        try:
            (staging_dir / "components").mkdir(parents=True)
            (staging_dir / "hooks").mkdir()
            for filename, source in components.items():
                (staging_dir / "components" / filename).write_text(source, encoding="utf-8")
            for filename, source in hooks.items():
                (staging_dir / "hooks" / filename).write_text(source, encoding="utf-8")
            (staging_dir / "pipeline.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            staging_dir.replace(pipeline_dir)
        except Exception:
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            raise

        return response

    # ── 内部方法 ───────────────────────────────────────────────

    def _pipeline_dir(self, pipeline_id: str) -> Path:
        return self._dir / pipeline_id

    def _json_path(self, pipeline_id: str) -> Path:
        return self._dir / pipeline_id / "pipeline.json"

    @staticmethod
    def _validate_python_filename(filename: str) -> str:
        if not filename or Path(filename).name != filename or not filename.endswith(".py"):
            raise ValueError(f"非法 Python 文件名: {filename!r}")
        return filename

    def _write_json(self, pipeline_id: str, data: dict[str, Any]) -> None:
        """原子写入：先写临时文件，再 rename，避免写到一半崩溃。"""
        path = self._json_path(pipeline_id)
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp.replace(path)

    @staticmethod
    def _write_bytes_atomic(path: Path, content: bytes) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(content)
        temp.replace(path)
