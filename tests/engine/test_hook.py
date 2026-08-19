"""Hook 总线单元测试:HookContext 壳、HookDispatcher(顺序/fail-open/并发/双模)、HookLoader。

只用 core/engine 层,不依赖 server(EventEmitterHook 的回归见 test_orchestrator.py)。
"""

from __future__ import annotations

import threading
import time

import pytest
from pydantic import BaseModel, ValidationError

from deepflow.core.context import CaseContext, PipelineContext
from deepflow.core.hook import Hook, HookContext, HookDispatcher
from deepflow.core.store import ContextStore
from deepflow.engine.hook_loader import HookLoader
from deepflow.metrics import MetricsCollector
from deepflow.models.dataset import DatasetItem

# ── 测试辅助 ──────────────────────────────────────────────────


def make_pipeline_ctx(tmp_path) -> HookContext:
    ws = tmp_path / "ws"
    mc = MetricsCollector(ws)
    ctx = PipelineContext(workspace=ws, metrics_collector=mc, vars={"k": "v"}, store=ContextStore())
    return HookContext(run_id="r1", ctx=ctx, metrics_collector=mc)


def make_case_ctx(tmp_path, case_id: str = "c1") -> HookContext:
    ws = tmp_path / "ws"
    mc = MetricsCollector(ws)
    ctx = CaseContext(
        case=DatasetItem(id=case_id),
        casespace=ws / "cases" / case_id,
        vars={"k": "v"},
        store=ContextStore(),
    )
    return HookContext(run_id="r1", ctx=ctx, metrics_collector=mc)


class FullRecorder(Hook):
    """记录所有挂点调用为 (name, point)。"""

    def __init__(self, log: list, name: str = ""):
        super().__init__()
        self._log, self._name = log, name

    def _rec(self, point: str) -> None:
        self._log.append((self._name, point))

    def on_run_start(self, ctx): self._rec("on_run_start")
    def on_run_finish(self, ctx, status, error): self._rec("on_run_finish")
    def on_stage_start(self, ctx, stage): self._rec("on_stage_start")
    def on_stage_finish(self, ctx, stage): self._rec("on_stage_finish")
    def on_cases_ready(self, ctx, total): self._rec("on_cases_ready")
    def on_step_start(self, ctx, stage, step): self._rec("on_step_start")
    def on_step_finish(self, ctx, stage, step, result): self._rec("on_step_finish")
    def on_case_start(self, ctx, index, total): self._rec("on_case_start")
    def on_case_finish(self, ctx, status, duration_ms, completed, total): self._rec("on_case_finish")


# ── HookContext 壳 ────────────────────────────────────────────


class TestHookContext:
    def test_pipeline_level(self, tmp_path):
        hctx = make_pipeline_ctx(tmp_path)
        assert hctx.run_id == "r1"
        assert hctx.case is None                 # pipeline 级无 case
        assert hctx.vars == {"k": "v"}
        assert isinstance(hctx.store, ContextStore)
        assert hctx.workspace == tmp_path / "ws"  # 来自 PipelineContext.workspace
        assert hctx.metrics_collector is not None

    def test_case_level(self, tmp_path):
        hctx = make_case_ctx(tmp_path, "c9")
        assert hctx.case is not None and hctx.case.id == "c9"
        assert hctx.vars == {"k": "v"}
        # case 级 workspace 回退到 metrics_collector.workspace
        assert hctx.workspace == tmp_path / "ws"

    def test_ctx_is_same_object_as_component(self, tmp_path):
        """信封的 ctx 就是组件拿到的同一个对象,不是副本。"""
        ws = tmp_path / "ws"
        mc = MetricsCollector(ws)
        ctx = CaseContext(case=DatasetItem(id="c1"), casespace=ws / "cases" / "c1")
        hctx = HookContext(run_id="r", ctx=ctx, metrics_collector=mc)
        assert hctx.ctx is ctx


# ── 执行顺序 ──────────────────────────────────────────────────


class TestDispatchOrder:
    def test_builtin_before_user_and_fifo(self, tmp_path):
        log: list = []
        d = HookDispatcher()
        d.add(FullRecorder(log, "userA"))
        d.add(FullRecorder(log, "builtinX"), builtin=True)
        d.add(FullRecorder(log, "userB"))

        d.dispatch("on_run_start", ctx=make_pipeline_ctx(tmp_path))

        assert log == [
            ("builtinX", "on_run_start"),  # 内置段优先
            ("userA", "on_run_start"),     # 用户段按注册 FIFO
            ("userB", "on_run_start"),
        ]

    def test_only_overridden_points_called(self, tmp_path):
        """未覆盖的挂点不参与记录(基类空实现被调用但无副作用)。"""
        log: list = []
        d = HookDispatcher()
        d.add(FullRecorder(log, "r"))
        ctx = make_pipeline_ctx(tmp_path)
        d.dispatch("on_stage_start", ctx=ctx, stage="preprocess")
        d.dispatch("on_cases_ready", ctx=ctx, total=5)
        assert log == [("r", "on_stage_start"), ("r", "on_cases_ready")]


# ── fail-open ─────────────────────────────────────────────────


class TestFailOpen:
    def test_boom_hook_does_not_affect_others_or_caller(self, tmp_path):
        log: list = []

        class Boom(Hook):
            def on_run_start(self, ctx):
                raise RuntimeError("boom")

        d = HookDispatcher()
        d.add(Boom())
        d.add(FullRecorder(log, "good"))

        # 不抛异常,后续 hook 仍被调用
        d.dispatch("on_run_start", ctx=make_pipeline_ctx(tmp_path))
        assert log == [("good", "on_run_start")]


class TestHookConfig:
    def test_pydantic_config_is_validated(self):
        class Configured(Hook):
            class Config(BaseModel):
                endpoint: str

        hook = Configured({"endpoint": "https://example.test"})
        assert hook.config.endpoint == "https://example.test"
        with pytest.raises(ValidationError):
            Configured({})


# ── 并发与线程安全 ────────────────────────────────────────────


class PeakHook(Hook):
    """记录在 hook 体内的最大并发数。框架应对 thread_safe=False 串行化使 peak==1。"""

    thread_safe = False

    def __init__(self):
        super().__init__()
        self.cur = 0
        self.peak = 0

    def on_case_finish(self, ctx, status, duration_ms, completed, total):
        self.cur += 1
        self.peak = max(self.peak, self.cur)
        time.sleep(0.005)  # 放大并发窗口
        self.cur -= 1


class BarrierHook(Hook):
    """用 barrier(2) 探测是否允许两个线程同时进入 hook 体。"""

    thread_safe = True

    def __init__(self):
        super().__init__()
        self.barrier = threading.Barrier(2)
        self.both_entered = False

    def on_case_finish(self, ctx, status, duration_ms, completed, total):
        try:
            self.barrier.wait(timeout=2)
            self.both_entered = True
        except threading.BrokenBarrierError:
            pass


class TestConcurrency:
    def test_locks_only_for_non_thread_safe(self):
        """结构性断言:只有 thread_safe=False 的 hook 才分配锁。"""
        d = HookDispatcher()
        safe = FullRecorder([], "s")
        safe.thread_safe = True
        unsafe = FullRecorder([], "u")  # 默认 thread_safe=False
        d.add(safe)
        d.add(unsafe)

        assert unsafe in d._locks
        assert safe not in d._locks

    def test_concurrent_serializes_non_thread_safe(self, tmp_path):
        d = HookDispatcher()
        hook = PeakHook()
        d.add(hook)
        ctx = make_case_ctx(tmp_path)

        def worker():
            for _ in range(5):
                d.dispatch("on_case_finish", ctx=ctx, status="success",
                           duration_ms=1.0, completed=1, total=1, concurrent=True)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert hook.peak == 1  # 串行化生效:任一时刻至多一个线程在 hook 内

    def test_concurrent_allows_thread_safe(self, tmp_path):
        d = HookDispatcher()
        hook = BarrierHook()  # thread_safe=True
        d.add(hook)
        ctx = make_case_ctx(tmp_path)

        def call():
            d.dispatch("on_case_finish", ctx=ctx, status="success",
                       duration_ms=1.0, completed=1, total=1, concurrent=True)

        t1, t2 = threading.Thread(target=call), threading.Thread(target=call)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert hook.both_entered  # 不加锁:两线程同时进入,barrier 达成

    def test_concurrent_blocks_non_thread_safe(self, tmp_path):
        d = HookDispatcher()
        hook = BarrierHook()
        hook.thread_safe = False  # 实例覆盖 → 框架加锁
        d.add(hook)
        ctx = make_case_ctx(tmp_path)

        def call():
            d.dispatch("on_case_finish", ctx=ctx, status="success",
                       duration_ms=1.0, completed=1, total=1, concurrent=True)

        t1, t2 = threading.Thread(target=call), threading.Thread(target=call)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not hook.both_entered  # 加锁:第二个线程进不来,barrier 超时

    def test_main_thread_points_not_locked(self, tmp_path):
        """concurrent=False(主线程挂点)时不加锁,直接顺序执行。"""
        log: list = []
        d = HookDispatcher()
        d.add(FullRecorder(log, "r"))  # thread_safe=False 但主线程不加锁
        d.dispatch("on_stage_start", ctx=make_pipeline_ctx(tmp_path), stage="preprocess")
        assert log == [("r", "on_stage_start")]

# ── HookLoader ────────────────────────────────────────────────


class TestHookLoader:
    def test_load_external(self, tmp_path):
        (tmp_path / "my_hook.py").write_text(
            "from deepflow import Hook\n\n\nclass MyHook(Hook):\n    pass\n",
            encoding="utf-8",
        )
        hook = HookLoader.load("./my_hook.py", config={"a": 1}, manifest_dir=tmp_path)
        assert isinstance(hook, Hook)
        assert hook.config == {"a": 1}

    def test_rejects_non_hook(self, tmp_path):
        (tmp_path / "plain_thing.py").write_text(
            "class PlainThing:\n    def __init__(self, config=None):\n        pass\n",
            encoding="utf-8",
        )
        with pytest.raises(TypeError, match="不是 Hook 子类"):
            HookLoader.load("./plain_thing.py", manifest_dir=tmp_path)

    def test_invalid_src(self, tmp_path):
        with pytest.raises(ValueError, match="无效的 hook 引用"):
            HookLoader.load("plain_name", manifest_dir=tmp_path)

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            HookLoader.load("./missing.py", manifest_dir=tmp_path)

    def test_missing_plugin(self):
        with pytest.raises(ValueError, match="Plugin hook 未找到"):
            HookLoader.load("nope:missing_hook_xyz")

    def test_manifest_local_imports_are_isolated(self, tmp_path):
        loaded = []
        for label in ("alpha", "beta"):
            root = tmp_path / label
            root.mkdir()
            (root / "helper.py").write_text(f"VALUE = {label!r}\n", encoding="utf-8")
            (root / "local_hook.py").write_text(
                "from deepflow import Hook\n"
                "from .helper import VALUE\n\n"
                "class LocalHook(Hook):\n"
                "    marker = VALUE\n",
                encoding="utf-8",
            )
            loaded.append(HookLoader.load("./local_hook.py", manifest_dir=root))

        assert [hook.marker for hook in loaded] == ["alpha", "beta"]
