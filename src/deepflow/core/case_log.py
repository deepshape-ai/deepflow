"""日志基础设施：contextvars 定义 + per-case 日志路由。

run_ctx / case_ctx 原定义在 engine.orchestrator，为避免 orchestrator ↔
case_log 循环依赖移至 core.context，orchestrator 侧保持 re-export。

CaseLogHandler 以单个 Handler 挂在 root logger 上，按 case_ctx 将记录
惰性路由到 casespace/log.txt：
    - 句柄按 case 惰性打开，case 结束即关闭，同时打开的句柄数 ≤ concurrency
    - 非 casewise 记录（case_id 为空）不路由
"""

from __future__ import annotations

import logging
import threading
from contextvars import ContextVar
from pathlib import Path

# 日志隔离：通过 contextvars 标记当前 run/case，供 logging.Filter / Handler 读取
run_ctx: ContextVar[str] = ContextVar("deepflow_run", default="")
case_ctx: ContextVar[str] = ContextVar("deepflow_case", default="")


class ContextInjectFilter(logging.Filter):
    """将 contextvars 中的 run_id / case_id 注入 LogRecord，供 Formatter 使用。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = run_ctx.get("")  # type: ignore[attr-defined]
        record.case_id = case_ctx.get("")  # type: ignore[attr-defined]
        return True


class CaseLogHandler(logging.Handler):
    """将 casewise 阶段的日志按 case 路由到 {cases_root}/{case_id}/log.txt。"""

    def __init__(self, cases_root: Path, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._cases_root = cases_root
        self._handlers: dict[str, logging.FileHandler] = {}
        self._mutex = threading.Lock()  # 保护 _handlers 字典

    def emit(self, record: logging.LogRecord) -> None:
        case_id = case_ctx.get("")
        if not case_id:
            return  # 非 casewise 记录不路由
        handler = self._get_or_open(case_id)
        if handler is not None:
            handler.emit(record)

    def close_case(self, case_id: str) -> None:
        """case 结束时关闭其句柄；调用方需先重置 case_ctx。"""
        with self._mutex:
            handler = self._handlers.pop(case_id, None)
        if handler is not None:
            handler.close()

    def close(self) -> None:
        with self._mutex:
            handlers = list(self._handlers.values())
            self._handlers.clear()
        for handler in handlers:
            handler.close()
        super().close()

    def _get_or_open(self, case_id: str) -> logging.FileHandler | None:
        with self._mutex:
            handler = self._handlers.get(case_id)
            if handler is not None:
                return handler
            try:
                case_dir = self._cases_root / case_id
                case_dir.mkdir(parents=True, exist_ok=True)
                handler = logging.FileHandler(case_dir / "log.txt", encoding="utf-8")
                handler.setFormatter(logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s - %(message)s"
                ))
                self._handlers[case_id] = handler
                return handler
            except OSError:
                # 日志落盘失败不应影响 pipeline 执行
                return None


def attach_run_log(log_path: Path, *, level: int = logging.INFO) -> logging.Handler:
    """为本次运行挂载整运行日志文件 handler（CLI 使用）。

    log_path 的父目录需已存在。返回 handler，结束时由调用方 removeHandler + close。
    """
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(level)
    handler.addFilter(ContextInjectFilter())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(run_id)s/%(case_id)s] %(name)s %(levelname)s - %(message)s"
    ))
    logging.getLogger().addHandler(handler)
    return handler


def detach_run_log(handler: logging.Handler) -> None:
    logging.getLogger().removeHandler(handler)
    handler.close()
