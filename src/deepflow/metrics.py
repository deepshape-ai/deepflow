from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any


@dataclass
class CaseResult:
    """单个 case 的执行结果"""

    metrics: dict[str, Any]
    status: str
    duration_ms: float
    error_type: str = ""
    error_message: str = ""
    failed_step: str = ""


class MetricsCollector:
    """
    收集和存储 metrics，支持实时输出。

    每个 case 完成后立即写入 metrics/case-{id}.json（崩溃安全），
    所有 case 结束后生成汇总文件 metrics.json。
    """

    def __init__(self, workspace: Path, *, on_record: Callable[..., Any] | None = None) -> None:
        self.workspace = workspace
        self.metrics_dir = workspace / "metrics"
        self.metrics_dir.mkdir(parents=True, exist_ok=True)

        self._lock = Lock()
        self._summary: str = ""
        self._completed_cases: dict[str, CaseResult] = {}
        self._on_record = on_record

    def record(
        self,
        case_id: str,
        *,
        metrics: dict[str, Any],
        status: str,
        duration_ms: float,
        error_type: str = "",
        error_message: str = "",
        failed_step: str = "",
    ) -> None:
        """收集单个 case 的 metrics，立即持久化到磁盘"""
        case_data = {
            "case_id": case_id,
            "metrics": metrics,
            "status": status,
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        }
        if error_type:
            case_data["error_type"] = error_type
            case_data["error_message"] = error_message
            case_data["failed_step"] = failed_step

        with self._lock:
            self._completed_cases[case_id] = CaseResult(
                metrics=metrics,
                status=status,
                duration_ms=duration_ms,
                error_type=error_type,
                error_message=error_message,
                failed_step=failed_step,
            )

        # 每个 case 独立文件，无需锁
        self._atomic_write(self.metrics_dir / f"case-{case_id}.json", case_data)

        if self._on_record is not None:
            self._on_record(case_id=case_id, metrics=metrics, status=status, duration_ms=duration_ms)

    def set_summary(self, summary: str) -> None:
        """设置全局 summary，由 postprocess 阶段调用"""
        with self._lock:
            self._summary = summary

    def save_summary(self) -> None:
        """生成汇总 metrics.json"""
        with self._lock:
            summary = self._summary
            cases_snapshot = dict(self._completed_cases)

        cases: dict[str, dict[str, Any]] = {}
        for case_id, result in cases_snapshot.items():
            entry: dict[str, Any] = {
                "metrics": result.metrics,
                "status": result.status,
                "duration_ms": result.duration_ms,
            }
            if result.error_type:
                entry["error_type"] = result.error_type
                entry["error_message"] = result.error_message
                entry["failed_step"] = result.failed_step
            cases[case_id] = entry

        data = {
            "summary": summary,
            "cases": cases,
        }
        self._atomic_write(self.workspace / "metrics.json", data)

    @property
    def completed_count(self) -> int:
        with self._lock:
            return len(self._completed_cases)

    @property
    def cases(self) -> dict[str, CaseResult]:
        """获取所有 case 结果的线程安全快照"""
        with self._lock:
            return dict(self._completed_cases)

    def to_dict(self) -> dict[str, Any]:
        """返回与 metrics.json 相同结构的内存快照，供 postprocess 组件直接使用"""
        with self._lock:
            summary = self._summary
            cases_snapshot = dict(self._completed_cases)

        cases: dict[str, dict[str, Any]] = {}
        for case_id, result in cases_snapshot.items():
            entry: dict[str, Any] = {
                "metrics": result.metrics,
                "status": result.status,
                "duration_ms": result.duration_ms,
            }
            if result.error_type:
                entry["error_type"] = result.error_type
                entry["error_message"] = result.error_message
                entry["failed_step"] = result.failed_step
            cases[case_id] = entry

        return {
            "summary": summary,
            "cases": cases,
        }

    def aggregate_errors(self) -> list[dict[str, Any]]:
        """按错误类型聚合失败 case，返回分组列表。"""
        with self._lock:
            cases_snapshot = dict(self._completed_cases)

        groups: dict[str, list[str]] = {}
        for case_id, result in cases_snapshot.items():
            if result.status != "failed" or not result.error_type:
                continue
            key = f"{result.error_type}: {result.error_message}"
            groups.setdefault(key, []).append(case_id)

        return [
            {"error": key, "count": len(case_ids), "case_ids": case_ids}
            for key, case_ids in sorted(groups.items(), key=lambda x: -len(x[1]))
        ]

    @staticmethod
    def _atomic_write(path: Path, data: dict[str, Any]) -> None:
        """原子写入 JSON 文件"""
        temp_path = path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_path.replace(path)
