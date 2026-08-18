"""Orchestrator 三阶段编排、失败隔离、取消与事件序列的端到端单测。

组件以真实文件 + ComponentLoader 加载，覆盖 loader → 组件 → 引擎全链路。
"""

from __future__ import annotations

import json
import threading

import pytest

from deepflow import FatalError
from deepflow.engine.orchestrator import CancellationError, Orchestrator
from deepflow.server.events import Event

# ── 组件源码 ──────────────────────────────────────────────────

FETCH_3 = """\
from deepflow import DatasetItem, MemoryIterator, PreprocessComponent, PreprocessOutput


class Fetch(PreprocessComponent):
    def execute(self, ctx):
        items = [DatasetItem(id=f"case-{i}") for i in range(3)]
        return PreprocessOutput(iterator=MemoryIterator(items))
"""

ECHO = """\
from deepflow import CasewiseComponent, CasewiseOutput


class Echo(CasewiseComponent):
    def execute(self, ctx):
        return CasewiseOutput(metrics={"n": 1})
"""

# 失败消息刻意含 ": "，锁定错误字段不从格式化字符串反解析（回归 #4）
FAIL_CASE_1 = """\
from deepflow import CasewiseComponent, CasewiseOutput


class FailCase1(CasewiseComponent):
    def execute(self, ctx):
        if ctx.case.id == "case-1":
            raise RuntimeError("ratio 0.3: below threshold")
        return CasewiseOutput(metrics={"n": 1})
"""

PARTIAL_METRICS = """\
from deepflow import CasewiseComponent, CasewiseOutput


class PartialFail(CasewiseComponent):
    def execute(self, ctx):
        if ctx.case.id == "case-1":
            err = RuntimeError("partial failure")
            err.metrics = {"score": 0.3}
            raise err
        return CasewiseOutput(metrics={"score": 0.9})
"""

FATAL_ALL = """\
from deepflow import CasewiseComponent, FatalError


class FatalAll(CasewiseComponent):
    def execute(self, ctx):
        raise FatalError("credentials expired")
"""

FAIL_PREPROCESS = """\
from deepflow import PreprocessComponent, PreprocessOutput


class BrokenFetch(PreprocessComponent):
    def execute(self, ctx):
        raise ConnectionError("dataset unavailable")
"""

NO_ITERATOR = """\
from deepflow import PreprocessComponent, PreprocessOutput


class SideEffectOnly(PreprocessComponent):
    def execute(self, ctx):
        return PreprocessOutput(message="side effect only, no iterator")
"""

REPORT = """\
import json

from deepflow import PostprocessComponent, PostprocessOutput


class Report(PostprocessComponent):
    def execute(self, ctx):
        data = ctx.metrics_collector.to_dict()
        (ctx.workspace / "report.json").write_text(json.dumps(data))
        return PostprocessOutput(message="report written")
"""


# ── 测试辅助 ──────────────────────────────────────────────────


class RecordingEmitter:
    """Duck-typed EventEmitter：记录事件序列，供断言。"""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)

    @property
    def types(self) -> list[str]:
        return [e.type.value for e in self.events]


class CancelOnFirstCase(RecordingEmitter):
    """收到首个 case.started 即触发取消，模拟运行中取消。"""

    def __init__(self, cancel_event: threading.Event) -> None:
        super().__init__()
        self._cancel = cancel_event

    def emit(self, event: Event) -> None:
        super().emit(event)
        if event.type.value == "case.started":
            self._cancel.set()


def read_run_state(workspace) -> dict:
    return json.loads((workspace / "run_state.json").read_text(encoding="utf-8"))


# ── 正常路径 ──────────────────────────────────────────────────


class TestHappyPath:
    def test_full_pipeline_writes_metrics_and_state(
        self, write_component, make_manifest, tmp_path
    ):
        fetch = write_component("fetch.py", FETCH_3)
        echo = write_component("echo.py", ECHO)
        report = write_component("report.py", REPORT)
        manifest = make_manifest(preprocess=[fetch], casewise=[echo], postprocess=[report])

        Orchestrator(manifest, tmp_path).run()

        ws = tmp_path / "workspace"
        state = read_run_state(ws)
        assert state["status"] == "completed"
        assert state["completed"] == 3

        # 每个 case 完成即落盘
        for i in range(3):
            entry = json.loads((ws / "metrics" / f"case-case-{i}.json").read_text())
            assert entry["status"] == "success"
            assert entry["metrics"] == {"n": 1}

        # 汇总文件
        summary = json.loads((ws / "metrics.json").read_text())
        assert set(summary["cases"]) == {"case-0", "case-1", "case-2"}

        # postprocess 产物
        assert (ws / "report.json").exists()

    def test_dry_run_executes_preprocess_only(
        self, write_component, make_manifest, tmp_path
    ):
        fetch = write_component("fetch.py", FETCH_3)
        echo = write_component("echo.py", ECHO)
        manifest = make_manifest(preprocess=[fetch], casewise=[echo])

        plan = Orchestrator(manifest, tmp_path).dry_run()

        assert plan["total_cases"] == 3
        assert plan["concurrency"] == 2
        assert plan["casewise_steps"] == [{"src": echo, "class_name": "Echo"}]
        assert plan["estimated_batches"] == 2
        # 未进入 casewise：无 case 指标落盘
        assert list((tmp_path / "workspace" / "metrics").glob("case-*.json")) == []


# ── 失败隔离与错误语义 ────────────────────────────────────────


class TestFailureIsolation:
    def test_failed_case_isolated_and_error_fields_exact(
        self, write_component, make_manifest, tmp_path
    ):
        fetch = write_component("fetch.py", FETCH_3)
        failer = write_component("fail_case_1.py", FAIL_CASE_1)
        manifest = make_manifest(preprocess=[fetch], casewise=[failer])
        emitter = RecordingEmitter()

        orch = Orchestrator(manifest, tmp_path, event_emitter=emitter)
        orch.run()  # 单 case 失败不终止整条 pipeline

        cases = orch.metrics_collector.cases
        assert cases["case-1"].status == "failed"
        # 锁定 #4：类型来自异常类，消息不被 ": " 截断/污染
        assert cases["case-1"].error_type == "RuntimeError"
        assert cases["case-1"].error_message == "ratio 0.3: below threshold"
        assert cases["case-1"].failed_step == failer
        assert cases["case-0"].status == "success"
        assert cases["case-2"].status == "success"

        # 错误聚合
        groups = orch.metrics_collector.aggregate_errors()
        assert len(groups) == 1
        assert groups[0]["count"] == 1
        assert groups[0]["case_ids"] == ["case-1"]

        # 汇总文件中失败条目携带错误字段
        summary = json.loads((tmp_path / "workspace" / "metrics.json").read_text())
        failed_entry = summary["cases"]["case-1"]
        assert failed_entry["error_type"] == "RuntimeError"
        assert failed_entry["error_message"] == "ratio 0.3: below threshold"

        assert emitter.types.count("case.failed") == 1
        assert emitter.types.count("case.completed") == 2

    def test_partial_metrics_on_exception_preserved(
        self, write_component, make_manifest, tmp_path
    ):
        fetch = write_component("fetch.py", FETCH_3)
        partial = write_component("partial_fail.py", PARTIAL_METRICS)
        manifest = make_manifest(preprocess=[fetch], casewise=[partial])

        orch = Orchestrator(manifest, tmp_path)
        orch.run()

        cases = orch.metrics_collector.cases
        assert cases["case-1"].status == "failed"
        assert cases["case-1"].metrics == {"score": 0.3}
        assert cases["case-0"].metrics == {"score": 0.9}

    def test_fatal_error_terminates_pipeline(
        self, write_component, make_manifest, tmp_path
    ):
        fetch = write_component("fetch.py", FETCH_3)
        fatal = write_component("fatal_all.py", FATAL_ALL)
        manifest = make_manifest(preprocess=[fetch], casewise=[fatal], concurrency=1)

        orch = Orchestrator(manifest, tmp_path)
        with pytest.raises(FatalError, match="credentials expired"):
            orch.run()

        assert read_run_state(tmp_path / "workspace")["status"] == "failed"
        # 致命 case 落盘，便于事后诊断。
        # 注意记录数量非确定（shutdown 前工人线程可能已领取下一个 case），
        # 确定性的是：至少一个 FatalError case，且 pipeline 提前终止。
        failed = [c for c in orch.metrics_collector.cases.values() if c.status == "failed"]
        assert len(failed) >= 1
        assert all(c.error_type == "FatalError" for c in failed)
        assert len(orch.metrics_collector.cases) < 3

    def test_preprocess_failure_terminates(self, write_component, make_manifest, tmp_path):
        broken = write_component("broken_fetch.py", FAIL_PREPROCESS)
        manifest = make_manifest(preprocess=[broken], casewise=[])

        with pytest.raises(RuntimeError):
            Orchestrator(manifest, tmp_path).run()

        assert read_run_state(tmp_path / "workspace")["status"] == "failed"

    def test_missing_iterator_raises(self, write_component, make_manifest, tmp_path):
        side = write_component("side_effect_only.py", NO_ITERATOR)
        echo = write_component("echo.py", ECHO)
        manifest = make_manifest(preprocess=[side], casewise=[echo])

        with pytest.raises(RuntimeError, match="iterator"):
            Orchestrator(manifest, tmp_path).run()

    def test_duplicate_iterator_raises(self, write_component, make_manifest, tmp_path):
        fetch = write_component("fetch.py", FETCH_3)
        manifest = make_manifest(preprocess=[fetch, fetch], casewise=[])

        with pytest.raises(RuntimeError, match="只允许一个组件提交 iterator"):
            Orchestrator(manifest, tmp_path).run()


# ── 取消 ──────────────────────────────────────────────────────


class TestCancellation:
    def test_cancel_before_start(self, write_component, make_manifest, tmp_path):
        fetch = write_component("fetch.py", FETCH_3)
        echo = write_component("echo.py", ECHO)
        manifest = make_manifest(preprocess=[fetch], casewise=[echo])

        cancel = threading.Event()
        cancel.set()
        orch = Orchestrator(manifest, tmp_path, cancel_event=cancel)

        with pytest.raises(CancellationError):
            orch.run()

        assert read_run_state(tmp_path / "workspace")["status"] == "cancelled"

    def test_cancel_mid_casewise(self, write_component, make_manifest, tmp_path):
        fetch = write_component("fetch.py", FETCH_3)
        echo = write_component("echo.py", ECHO)
        manifest = make_manifest(preprocess=[fetch], casewise=[echo], concurrency=1)

        cancel = threading.Event()
        emitter = CancelOnFirstCase(cancel)
        orch = Orchestrator(manifest, tmp_path, event_emitter=emitter, cancel_event=cancel)

        with pytest.raises(CancellationError):
            orch.run()

        assert read_run_state(tmp_path / "workspace")["status"] == "cancelled"


# ── 事件序列 ──────────────────────────────────────────────────


class TestEventSequence:
    def test_event_order_on_success(self, write_component, make_manifest, tmp_path):
        fetch = write_component("fetch.py", FETCH_3)
        echo = write_component("echo.py", ECHO)
        report = write_component("report.py", REPORT)
        manifest = make_manifest(preprocess=[fetch], casewise=[echo], postprocess=[report])
        emitter = RecordingEmitter()

        Orchestrator(manifest, tmp_path, event_emitter=emitter).run()

        types = emitter.types
        assert types[0] == "run.started"
        assert types[-1] == "run.completed"

        # 三阶段 started/completed 成对且有序
        stage_events = [t for t in types if t.startswith("stage.")]
        assert stage_events == [
            "stage.started", "stage.completed",
            "stage.started", "stage.completed",
            "stage.started", "stage.completed",
        ]
        assert types.count("case.started") == 3
        assert types.count("case.completed") == 3
        assert "case.failed" not in types
        assert "run.failed" not in types
