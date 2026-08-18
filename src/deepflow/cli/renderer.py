"""Rich CLI 渲染层 — 进度面板与运行摘要。

设计要点：
- 单一 Table.grid 管理所有行，列自动对齐
- braille spinner 动画
- stdout/stderr 重定向拦截组件 print
- threading.Lock 保护计数器，避免并发竞态
- 渲染节流：_refresh 最多 refresh_per_second 次/秒，避免高并发时刷屏
"""
from __future__ import annotations

import io
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.text import Text


@dataclass
class StageState:
    """单个阶段的渲染状态。"""
    name: str
    label: str
    status: str = "pending"  # pending | running | done | failed
    steps: int = 0
    total: int = 0
    completed: int = 0
    failed: int = 0


class _NullStream(io.TextIOBase):
    """吞掉所有写入，防止组件 print 穿透 Live 面板。"""
    def write(self, s: str) -> int:
        return len(s)

    def flush(self) -> None:
        pass

class PipelineRenderer:
    """非 verbose 模式下的 Rich 实时面板。

    实现 __rich__() 协议，Live 每次 auto-refresh 都调用它重新构建显示。
    spinner 动画、计时器更新都由 Live 的 refresh 周期驱动，无需手动 update。
    线程安全：所有计数器操作通过 _lock 保护。
    """

    def __init__(self, pipeline_name: str, console: Console | None = None) -> None:
        self.pipeline_name = pipeline_name
        self._start_time = time.time()

        self.preprocess = StageState(name="preprocess", label="预处理")
        self.casewise = StageState(name="casewise", label="逐案例")
        self.postprocess = StageState(name="postprocess", label="后处理")

        self._lock = threading.Lock()

        # 保存原始 stdout/stderr — Console 必须绑定到原始 stderr，
        # 否则后续 sys.stderr 被替换后 Live 输出也会被吞掉
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self.console = console or Console(stderr=True, file=self._orig_stderr)
        self._null_stream = _NullStream()

        self._live: Live | None = None
        self._parked_handlers: list[logging.Handler] = []

    @staticmethod
    def _is_console_handler(handler: logging.Handler) -> bool:
        """是否为写向当前 stdout/stderr 的控制台 handler（文件 handler 不算）。"""
        return (
            isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.FileHandler)
            and getattr(handler, "stream", None) in (sys.stdout, sys.stderr)
        )

    def start(self) -> None:
        """启动 Live 面板并劫持 stdout/stderr。"""
        self._live = Live(
            self,  # __rich__() 协议：Live 每次 refresh 调用 self.__rich__()
            console=self.console,
            refresh_per_second=8,
            transient=True,
        )
        self._live.start()
        # 先停靠控制台 handler（此时 sys.stderr 尚未被替换，stream 引用仍可识别），
        # 再劫持 stdout/stderr，防止组件 print / 日志穿透面板；
        # 文件 handler（run.log / per-case log）不停靠，继续收集
        self._parked_handlers = [h for h in logging.root.handlers if self._is_console_handler(h)]
        if self._parked_handlers:
            logging.root.handlers = [
                h for h in logging.root.handlers if not self._is_console_handler(h)
            ]
        sys.stdout = self._null_stream  # type: ignore[assignment]
        sys.stderr = self._null_stream  # type: ignore[assignment]

    def stop(self) -> None:
        """恢复 stdout/stderr 并停止 Live 面板。"""
        sys.stdout = self._orig_stdout
        sys.stderr = self._orig_stderr
        for handler in self._parked_handlers:
            if handler not in logging.root.handlers:
                logging.root.handlers.append(handler)
        self._parked_handlers = []
        if self._live:
            self._live.stop()
            self._live = None

    def __rich__(self) -> Table:
        """Rich 协议：Live 每次 refresh 调用此方法获取最新显示内容。"""
        return self._build_display()

    # ── Event callbacks（线程安全，只更新状态，渲染由 Live auto-refresh 驱动） ──

    def on_stage_started(self, stage: str) -> None:
        with self._lock:
            self._get_stage(stage).status = "running"

    def on_stage_completed(self, stage: str) -> None:
        with self._lock:
            self._get_stage(stage).status = "done"

    def on_step_completed(self, stage: str, **kwargs: Any) -> None:
        with self._lock:
            self._get_stage(stage).steps += 1

    def on_preprocess_iterator(self, total: int) -> None:
        with self._lock:
            self.casewise.total = total

    def on_case_completed(self, **kwargs: Any) -> None:
        with self._lock:
            self.casewise.completed += 1

    def on_case_failed(self, case_id: str = "", **kwargs: Any) -> None:
        with self._lock:
            self.casewise.completed += 1
            self.casewise.failed += 1

    # ── Summary ──

    def print_summary(self, error_groups: list[dict[str, Any]] | None = None) -> None:
        """运行结束后打印最终摘要。"""
        elapsed = time.time() - self._start_time
        c = self.console

        # 标题
        c.print()
        title = Text()
        title.append("  ◆ ", style="bold cyan")
        title.append(self.pipeline_name, style="bold")
        title.append(f"  {self._fmt_duration(elapsed)}", style="dim")
        c.print(title)
        c.print()

        # 各阶段
        for s in [self.preprocess, self.casewise, self.postprocess]:
            if s.status == "done":
                icon, color = "✓", "green"
            elif s.status == "failed":
                icon, color = "✗", "red"
            else:
                icon, color = "○", "dim"

            line = Text()
            line.append(f"  {icon} ", style=color)
            line.append(f"{s.label:<8}", style=f"bold {color}")

            if s.name == "casewise" and s.total > 0:
                ok = s.completed - s.failed
                line.append(f"  {s.total} cases", style="dim")
                line.append(f"  ✓ {ok}", style="green")
                if s.failed > 0:
                    line.append(f"  ✗ {s.failed}", style="red")
            elif s.steps > 0:
                line.append(f"  {s.steps} steps", style="dim")

            c.print(line)

        # 错误聚合
        if error_groups:
            c.print()
            total_failed = sum(g["count"] for g in error_groups)
            c.print(f"  [bold red]失败汇总 ({total_failed} cases):[/bold red]")
            for g in error_groups[:10]:
                ids = ", ".join(g["case_ids"][:3])
                more = f" +{len(g['case_ids']) - 3}" if len(g["case_ids"]) > 3 else ""
                c.print(f"    [red]×{g['count']}[/red]  {g['error']}")
                c.print(f"           [dim]{ids}{more}[/dim]")

        c.print()

    # ── Internal ──

    def _get_stage(self, name: str) -> StageState:
        return {
            "preprocess": self.preprocess,
            "casewise": self.casewise,
            "postprocess": self.postprocess,
        }[name]

    def _build_display(self) -> Text:
        """构建实时面板。每行用 Text 拼接，不用 Table 避免列宽撑开。"""
        with self._lock:
            stages = [
                (s.name, s.label, s.status, s.steps, s.total, s.completed, s.failed)
                for s in [self.preprocess, self.casewise, self.postprocess]
            ]
        elapsed = time.time() - self._start_time

        lines = Text()

        # 标题行
        lines.append("  ◆ ", style="bold cyan")
        lines.append(self.pipeline_name, style="bold")
        lines.append(f"  {self._fmt_duration(elapsed)}", style="dim")
        lines.append("\n\n")

        for i, (name, label, status, steps, total, completed, failed) in enumerate(stages):
            icon, icon_style = self._stage_icon_from(status)

            lines.append(f"  {icon} ", style=icon_style)
            label_style = f"bold {icon_style}" if status != "pending" else "dim"
            lines.append(label, style=label_style)

            if name == "casewise" and status == "running":
                lines.append("  ", style="dim")
                self._append_casewise_bar(lines, total, completed, failed)
            elif name == "casewise" and status == "done":
                ok = completed - failed
                lines.append(f"  {total} cases", style="dim")
                lines.append(f"  ✓ {ok}", style="green")
                if failed > 0:
                    lines.append(f"  ✗ {failed}", style="red")
            elif steps > 0:
                lines.append(f"  {steps} steps", style="dim")

            if i < len(stages) - 1:
                lines.append("\n\n")
            else:
                lines.append("\n")

        return lines

    @staticmethod
    def _append_casewise_bar(text: Text, total: int, completed: int, failed: int) -> None:
        """将进度条直接追加到 Text 对象。"""
        total = max(total, 1)
        bar_width = 20
        filled = int(bar_width * completed / total)

        ok = completed - failed
        pct = int(100 * completed / total)

        # 进度条
        text.append("━" * filled, style="bold cyan")
        remaining = bar_width - filled
        if remaining > 0:
            text.append("╺", style="dim")
            text.append("─" * (remaining - 1), style="dim")

        text.append(f"  {completed}/{total}", style="bold")
        text.append(f"  {pct}%", style="dim")
        text.append(f"  ✓{ok}", style="green")
        text.append(f" ✗{failed}", style="red" if failed > 0 else "dim")

    @staticmethod
    def _stage_icon_from(status: str) -> tuple[str, str]:
        """返回 (icon, style)。running 状态用 braille spinner 字符。"""
        if status == "done":
            return "✓", "green"
        if status == "failed":
            return "✗", "red"
        if status == "running":
            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            idx = int(time.time() * 8) % len(frames)
            return frames[idx], "cyan"
        return "○", "dim"

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.1f}s"
        m, s = divmod(int(seconds), 60)
        if m < 60:
            return f"{m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m{s:02d}s"
