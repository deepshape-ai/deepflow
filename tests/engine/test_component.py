"""BaseComponent 模板方法与重试引擎单测。

直接实例化组件（不经 loader），聚焦 check_skip / 重试循环 / 退避 / FatalError 语义。
"""

from __future__ import annotations

import types

import pytest

from deepflow import CaseContext, CasewiseComponent, CasewiseOutput, DatasetItem, FatalError
from deepflow.core.component import StageStatus
from deepflow.models.manifest import RetryConfig

# ── 测试组件 ──────────────────────────────────────────────────


class CountingEcho(CasewiseComponent):
    """成功组件，记录 execute 调用次数。"""

    def __init__(self, config=None):
        super().__init__(config)
        self.calls = 0

    def execute(self, ctx) -> CasewiseOutput:
        self.calls += 1
        return CasewiseOutput(metrics={"calls": self.calls})


class Flaky(CasewiseComponent):
    """前 fail_times 次抛普通异常，之后成功。"""

    def __init__(self, config=None, fail_times: int = 2):
        super().__init__(config)
        self.calls = 0
        self.fail_times = fail_times

    def execute(self, ctx) -> CasewiseOutput:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError(f"transient failure #{self.calls}")
        return CasewiseOutput(metrics={"calls": self.calls})


class AlwaysFail(CasewiseComponent):
    """永远失败，消息刻意含 ': '。"""

    def execute(self, ctx) -> CasewiseOutput:
        raise ValueError("boom: with a colon")


class SkipAll(CasewiseComponent):
    def check_skip(self, ctx) -> bool:
        return True

    def execute(self, ctx) -> CasewiseOutput:
        raise AssertionError("skipped component must not execute")


class FatalOnce(CasewiseComponent):
    def __init__(self, config=None):
        super().__init__(config)
        self.calls = 0

    def execute(self, ctx) -> CasewiseOutput:
        self.calls += 1
        raise FatalError("auth expired")


# ── fixtures ──────────────────────────────────────────────────


@pytest.fixture
def ctx(tmp_path) -> CaseContext:
    return CaseContext(case=DatasetItem(id="case-1"), casespace=tmp_path / "cases" / "case-1")


@pytest.fixture
def sleep_log(monkeypatch) -> list[float]:
    """拦截 component 模块内的 time.sleep：记录重试间隔且不真实休眠。"""
    calls: list[float] = []

    def fake_sleep(delay: float) -> None:
        calls.append(delay)

    monkeypatch.setattr("deepflow.core.component.time", types.SimpleNamespace(sleep=fake_sleep))
    return calls


# ── 成功 / 跳过 ───────────────────────────────────────────────


class TestTemplateMethod:
    def test_success_returns_success_result(self, ctx):
        result = CountingEcho().run(ctx)

        assert result.status is StageStatus.SUCCESS
        assert result.error is None
        assert result.output.metrics == {"calls": 1}

    def test_skip_short_circuits_execute(self, ctx):
        result = SkipAll().run(ctx)

        assert result.status is StageStatus.SKIPPED

    def test_with_retry_is_chainable(self, ctx):
        comp = CountingEcho().with_retry(RetryConfig(max_attempts=3))

        assert comp.run(ctx).status is StageStatus.SUCCESS
        assert comp._retry.max_attempts == 3


# ── 重试 / 退避 ───────────────────────────────────────────────


class TestRetryEngine:
    def test_transient_failure_retried_then_succeeds(self, ctx, sleep_log):
        comp = Flaky(fail_times=2).with_retry(RetryConfig(max_attempts=3, delay=1.0))

        result = comp.run(ctx)

        assert result.status is StageStatus.SUCCESS
        assert comp.calls == 3
        assert sleep_log == [1.0, 1.0]

    def test_fixed_backoff_intervals(self, ctx, sleep_log):
        comp = Flaky(fail_times=3).with_retry(
            RetryConfig(max_attempts=4, delay=2.0, backoff="fixed"))

        assert comp.run(ctx).status is StageStatus.SUCCESS
        assert sleep_log == [2.0, 2.0, 2.0]

    def test_exponential_backoff_intervals(self, ctx, sleep_log):
        comp = Flaky(fail_times=3).with_retry(
            RetryConfig(max_attempts=4, delay=1.0, backoff="exponential"))

        assert comp.run(ctx).status is StageStatus.SUCCESS
        assert sleep_log == [1.0, 2.0, 4.0]

    def test_retry_exhaustion_returns_failed_with_original_error(self, ctx, sleep_log):
        comp = AlwaysFail().with_retry(RetryConfig(max_attempts=2, delay=1.0))

        result = comp.run(ctx)

        assert result.status is StageStatus.FAILED
        assert isinstance(result.error, ValueError)
        assert str(result.error) == "boom: with a colon"
        assert "ValueError" in result.output.message
        assert "boom" in result.output.message
        assert sleep_log == [1.0]  # 最后一次失败后不再休眠

    def test_no_retry_by_default(self, ctx, sleep_log):
        result = AlwaysFail().run(ctx)  # RetryConfig 默认 max_attempts=1

        assert result.status is StageStatus.FAILED
        assert sleep_log == []


# ── FatalError ────────────────────────────────────────────────


class TestFatalError:
    def test_fatal_error_propagates_without_retry(self, ctx, sleep_log):
        comp = FatalOnce().with_retry(RetryConfig(max_attempts=5, delay=1.0))

        with pytest.raises(FatalError, match="auth expired"):
            comp.run(ctx)

        assert comp.calls == 1       # 未进入重试循环
        assert sleep_log == []       # 未休眠

    def test_fatal_error_subclass_not_retried(self, ctx):
        class SpecificFatal(FatalError):
            pass

        class RaiseSpecific(CasewiseComponent):
            def execute(self, ctx) -> CasewiseOutput:
                raise SpecificFatal("irrecoverable")

        with pytest.raises(SpecificFatal):
            RaiseSpecific().with_retry(RetryConfig(max_attempts=3, delay=1.0)).run(ctx)
