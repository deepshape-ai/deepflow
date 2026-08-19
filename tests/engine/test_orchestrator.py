"""Orchestrator 三阶段编排、失败隔离、取消与事件序列的端到端单测。

组件以真实文件 + ComponentLoader 加载，覆盖 loader → 组件 → 引擎全链路。
"""

from __future__ import annotations

import json
import threading

import pytest

from deepflow import FatalError, Hook
from deepflow.cli.hooks import CliRendererHook
from deepflow.engine.orchestrator import CancellationError, Orchestrator
from deepflow.server.events import Event
from deepflow.server.hooks import EventEmitterHook

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


def with_emitter(orch: Orchestrator, emitter: RecordingEmitter) -> Orchestrator:
    orch.add_hook(
        EventEmitterHook(emitter, run_id=orch.run_id, pipeline_name=orch.manifest.name),
        builtin=True,
    )
    return orch


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

    def test_orchestrator_is_single_use(self, write_component, make_manifest, tmp_path):
        fetch = write_component("fetch.py", FETCH_3)
        manifest = make_manifest(preprocess=[fetch])
        orch = Orchestrator(manifest, tmp_path)
        orch.dry_run()

        with pytest.raises(RuntimeError, match="只能执行一次"):
            orch.run()


# ── 失败隔离与错误语义 ────────────────────────────────────────


class TestFailureIsolation:
    def test_failed_case_isolated_and_error_fields_exact(
        self, write_component, make_manifest, tmp_path
    ):
        fetch = write_component("fetch.py", FETCH_3)
        failer = write_component("fail_case_1.py", FAIL_CASE_1)
        manifest = make_manifest(preprocess=[fetch], casewise=[failer])
        emitter = RecordingEmitter()

        orch = with_emitter(Orchestrator(manifest, tmp_path), emitter)
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
        orch = with_emitter(Orchestrator(manifest, tmp_path, cancel_event=cancel), emitter)

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

        with_emitter(Orchestrator(manifest, tmp_path), emitter).run()

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


# ── Hook 集成 ─────────────────────────────────────────────────

RUN_MARKER_HOOK = """\
from pathlib import Path

from deepflow import Hook


class RunMarker(Hook):
    def on_run_finish(self, ctx, status, error):
        Path(ctx.workspace / "hook_marker.txt").write_text(status)
"""


class RecordingHook(Hook):
    """记录全部挂点调用为 (point, data)。thread_safe=False,依赖框架串行化。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, dict]] = []

    def _rec(self, point: str, **data) -> None:
        self.calls.append((point, data))

    def on_run_start(self, ctx): self._rec("on_run_start", run_id=ctx.run_id)
    def on_run_finish(self, ctx, status, error): self._rec("on_run_finish", status=status)
    def on_stage_start(self, ctx, stage): self._rec("on_stage_start", stage=stage)
    def on_stage_finish(self, ctx, stage): self._rec("on_stage_finish", stage=stage)
    def on_cases_ready(self, ctx, total): self._rec("on_cases_ready", total=total)
    def on_step_start(self, ctx, stage, step):
        self._rec("on_step_start", stage=stage, case=ctx.case.id if ctx.case else None)
    def on_step_finish(self, ctx, stage, step, result):
        self._rec("on_step_finish", stage=stage, status=result.status.value)
    def on_case_start(self, ctx, index, total):
        self._rec("on_case_start", case=ctx.case.id, index=index, total=total)
    def on_case_finish(self, ctx, status, duration_ms, completed, total):
        self._rec("on_case_finish", case=ctx.case.id, status=status, completed=completed, total=total)

    def points(self, name: str) -> list[dict]:
        return [d for p, d in self.calls if p == name]


class FakeRenderer:
    """Duck-typed PipelineRenderer:记录回调,验证 CliRendererHook 忠实映射。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def on_stage_started(self, stage): self.calls.append(("stage_started", stage))
    def on_stage_completed(self, stage): self.calls.append(("stage_completed", stage))
    def on_step_completed(self, stage, **kw): self.calls.append(("step_completed", stage))
    def on_preprocess_iterator(self, total): self.calls.append(("iterator", total))
    def on_case_completed(self, **kw): self.calls.append(("case_completed", None))
    def on_case_failed(self, case_id="", **kw): self.calls.append(("case_failed", case_id))


class TestHookIntegration:
    def test_all_points_fire_in_order(self, write_component, make_manifest, tmp_path):
        fetch = write_component("fetch.py", FETCH_3)
        echo = write_component("echo.py", ECHO)
        report = write_component("report.py", REPORT)
        manifest = make_manifest(preprocess=[fetch], casewise=[echo], postprocess=[report])
        hook = RecordingHook()
        orch = Orchestrator(manifest, tmp_path)
        orch.add_hook(hook)
        orch.run()

        points = [p for p, _ in hook.calls]
        assert points[0] == "on_run_start"
        assert points[-1] == "on_run_finish"

        # 三阶段 start/finish 成对且按序
        stage_seq = [(p, d["stage"]) for p, d in hook.calls if p.startswith("on_stage_")]
        assert stage_seq == [
            ("on_stage_start", "preprocess"), ("on_stage_finish", "preprocess"),
            ("on_stage_start", "casewise"), ("on_stage_finish", "casewise"),
            ("on_stage_start", "postprocess"), ("on_stage_finish", "postprocess"),
        ]

        # cases_ready 恰好一次,total=3
        assert hook.points("on_cases_ready") == [{"total": 3}]

        # 每个 case start/finish 各一次
        assert len(hook.points("on_case_start")) == 3
        assert len(hook.points("on_case_finish")) == 3

        # run_finish 成功
        assert hook.points("on_run_finish") == [{"status": "completed"}]

    def test_case_ordinal_and_total(self, write_component, make_manifest, tmp_path):
        fetch = write_component("fetch.py", FETCH_3)
        echo = write_component("echo.py", ECHO)
        manifest = make_manifest(preprocess=[fetch], casewise=[echo])
        hook = RecordingHook()
        orch = Orchestrator(manifest, tmp_path)
        orch.add_hook(hook)
        orch.run()

        starts = hook.points("on_case_start")
        assert sorted(d["index"] for d in starts) == [0, 1, 2]  # 提交序
        assert all(d["total"] == 3 for d in starts)

        finishes = hook.points("on_case_finish")
        assert [d["completed"] for d in finishes] == [1, 2, 3]  # 观察端收到单调完成序
        assert all(d["total"] == 3 for d in finishes)

    def test_manifest_hooks_loaded(self, write_component, make_manifest, tmp_path):
        fetch = write_component("fetch.py", FETCH_3)
        echo = write_component("echo.py", ECHO)
        hook_src = write_component("run_marker.py", RUN_MARKER_HOOK)
        manifest = make_manifest(preprocess=[fetch], casewise=[echo], hooks=[{"src": hook_src}])

        Orchestrator(manifest, tmp_path).run()

        assert (tmp_path / "workspace" / "hook_marker.txt").read_text() == "completed"

    def test_renderer_mapping_matches_legacy(self, write_component, make_manifest, tmp_path):
        """CliRendererHook 回归:casewise 的 step 不渲染,case/iterator 计数正确。"""
        fetch = write_component("fetch.py", FETCH_3)
        echo = write_component("echo.py", ECHO)
        report = write_component("report.py", REPORT)
        manifest = make_manifest(preprocess=[fetch], casewise=[echo], postprocess=[report])
        renderer = FakeRenderer()
        orch = Orchestrator(manifest, tmp_path)
        orch.add_hook(CliRendererHook(renderer), builtin=True)
        orch.run()

        # step 计数仅 preprocess + postprocess(casewise 的 3 次不渲染)
        assert [c for c in renderer.calls if c[0] == "step_completed"] == [
            ("step_completed", "preprocess"),
            ("step_completed", "postprocess"),
        ]
        assert ("iterator", 3) in renderer.calls
        assert len([c for c in renderer.calls if c[0] == "case_completed"]) == 3
        assert [c for c in renderer.calls if c[0] == "stage_started"] == [
            ("stage_started", "preprocess"),
            ("stage_started", "casewise"),
            ("stage_started", "postprocess"),
        ]

    def test_cancel_cases_not_counted(self, write_component, make_manifest, tmp_path):
        """取消的 case 不触发 on_case_finish、不计入完成排位。"""
        fetch = write_component("fetch.py", FETCH_3)
        echo = write_component("echo.py", ECHO)
        manifest = make_manifest(preprocess=[fetch], casewise=[echo], concurrency=1)
        cancel = threading.Event()
        emitter = CancelOnFirstCase(cancel)
        hook = RecordingHook()
        orch = with_emitter(Orchestrator(manifest, tmp_path, cancel_event=cancel), emitter)
        orch.add_hook(hook)

        with pytest.raises(CancellationError):
            orch.run()

        finishes = hook.points("on_case_finish")
        assert len(finishes) < 3                                   # 取消:未完成全部
        assert len({d["completed"] for d in finishes}) == len(finishes)  # 排位无重复
        assert hook.points("on_run_finish") == [{"status": "cancelled"}]
