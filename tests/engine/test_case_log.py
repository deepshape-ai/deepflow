"""per-case 日志路由（CaseLogHandler）与 CLI run.log 的单测。"""

from __future__ import annotations

import logging

import pytest
import yaml
from click.testing import CliRunner

from deepflow.cli import cli
from deepflow.engine.orchestrator import Orchestrator

FETCH_3 = """\
from deepflow import DatasetItem, MemoryIterator, PreprocessComponent, PreprocessOutput


class Fetch(PreprocessComponent):
    def execute(self, ctx):
        items = [DatasetItem(id=f"case-{i}") for i in range(3)]
        return PreprocessOutput(iterator=MemoryIterator(items))
"""

# 组件内通过标准 logging 打日志，验证框架路由
NOISY = """\
import logging

from deepflow import CasewiseComponent, CasewiseOutput

log = logging.getLogger("noisy_component")

class Noisy(CasewiseComponent):
    def execute(self, ctx):
        log.info("processing %s", ctx.case.id)
        log.warning("finished %s", ctx.case.id)
        return CasewiseOutput(metrics={"n": 1})
"""

# 首次执行抛错触发框架重试：重试 WARNING 应落在该 case 的 log.txt
RETRY_ONCE = """\
from deepflow import CasewiseComponent, CasewiseOutput

class RetryOnce(CasewiseComponent):
    def execute(self, ctx):
        marker = ctx.casespace / "marker"
        if not marker.exists():
            marker.touch()
            raise ConnectionError("flaky upstream")
        return CasewiseOutput(metrics={"n": 1})
"""


@pytest.fixture
def root_info():
    """测试进程 root logger 默认 WARNING，路由 INFO 记录需临时提升。"""
    root = logging.getLogger()
    old_level = root.level
    root.setLevel(logging.INFO)
    yield
    root.setLevel(old_level)


class TestCaseLogRouting:
    def test_component_logs_routed_to_own_casespace(
        self, write_component, make_manifest, tmp_path, root_info
    ):
        fetch = write_component("fetch.py", FETCH_3)
        noisy = write_component("noisy.py", NOISY)
        manifest = make_manifest(preprocess=[fetch], casewise=[noisy])

        Orchestrator(manifest, tmp_path).run()

        # 精确断言：case-0 的日志只含 case-0
        log_0 = (tmp_path / "workspace" / "cases" / "case-0" / "log.txt").read_text()
        assert "processing case-0" in log_0
        assert "finished case-0" in log_0
        assert "case-1" not in log_0
        assert "case-2" not in log_0

    def test_framework_retry_warning_in_case_log(
        self, write_component, make_manifest, tmp_path, root_info
    ):
        fetch = write_component("fetch.py", FETCH_3)
        flaky = write_component("retry_once.py", RETRY_ONCE)
        manifest = make_manifest(
            preprocess=[fetch],
            casewise=[{"src": flaky, "retry": {"max_attempts": 2, "delay": 0.01}}],
        )

        orch = Orchestrator(manifest, tmp_path)
        orch.run()

        # 全部经一次重试后成功
        assert all(c.status == "success" for c in orch.metrics_collector.cases.values())
        cases_root = tmp_path / "workspace" / "cases"
        for i in range(3):
            content = (cases_root / f"case-{i}" / "log.txt").read_text()
            assert "RETRY" in content          # 框架重试告警
            assert "flaky upstream" in content  # 原始错误

    def test_non_case_records_not_routed(
        self, write_component, make_manifest, tmp_path, root_info
    ):
        fetch = write_component("fetch.py", FETCH_3)
        noisy = write_component("noisy.py", NOISY)
        manifest = make_manifest(preprocess=[fetch], casewise=[noisy])

        Orchestrator(manifest, tmp_path).run()

        cases_root = tmp_path / "workspace" / "cases"
        # cases 下只有 case 目录，各含一个 log.txt
        assert sorted(p.name for p in cases_root.iterdir()) == ["case-0", "case-1", "case-2"]
        for case_dir in cases_root.iterdir():
            assert sorted(p.name for p in case_dir.iterdir()) == ["log.txt"]
        # 非 casewise 记录（pipeline 级）不落到 workspace 根
        assert not (tmp_path / "workspace" / "log.txt").exists()


class TestCliRunLog:
    @staticmethod
    def _write_manifest(write_component, tmp_path, *, extra_steps: dict | None = None):
        fetch = write_component("fetch.py", FETCH_3)
        noisy = write_component("noisy.py", NOISY)
        casewise = [{"src": noisy, **(extra_steps or {})}]
        manifest_data = {
            "version": "2.0",
            "name": "cli-log-test",
            "workspace": str(tmp_path / "workspace"),
            "concurrency": 2,
            "pipeline": {
                "preprocess": [{"src": fetch}],
                "casewise": casewise,
                "postprocess": [],
            },
        }
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(yaml.safe_dump(manifest_data), encoding="utf-8")
        return manifest_path

    def test_run_writes_per_run_log(self, write_component, tmp_path):
        manifest_path = self._write_manifest(write_component, tmp_path)

        result = CliRunner().invoke(cli, ["run", "-c", str(manifest_path), "-v"])

        assert result.exit_code == 0, result.output
        assert "Total cases: 3" in result.output

        # run.log 落在 manifest 目录 .deepflow/logs/ 下（避开 clean_workspace）
        log_dir = tmp_path / ".deepflow" / "logs"
        log_files = list(log_dir.glob("*.log"))
        assert len(log_files) == 1
        content = log_files[0].read_text(encoding="utf-8")
        assert "Starting pipeline" in content
        assert "Pipeline completed" in content
        assert "[run_id/" not in content  # contextvars 已注入真实 run_id

    def test_run_log_and_case_logs_survive_rich_renderer(self, write_component, tmp_path):
        """非 verbose（Rich 面板）模式：面板不被日志穿透，文件日志仍完整收集。"""
        manifest_path = self._write_manifest(
            write_component, tmp_path, extra_steps={"retry": {"max_attempts": 2, "delay": 0.01}}
        )

        result = CliRunner().invoke(cli, ["run", "-c", str(manifest_path)])

        assert result.exit_code == 0, result.output
        # 面板输出中不出现组件日志/traceback
        assert "processing case-" not in result.output
        assert "Traceback" not in result.output

        log_files = list((tmp_path / ".deepflow" / "logs").glob("*.log"))
        assert len(log_files) == 1
        content = log_files[0].read_text(encoding="utf-8")
        assert "Starting pipeline" in content
        assert "Pipeline completed" in content

        # per-case 日志在面板模式下同样落盘
        case_log = (tmp_path / "workspace" / "cases" / "case-0" / "log.txt").read_text()
        assert "processing case-0" in case_log
