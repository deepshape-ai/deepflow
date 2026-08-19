from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click
import yaml

from deepflow.cli.hooks import CliRendererHook
from deepflow.core.case_log import ContextInjectFilter, attach_run_log, detach_run_log
from deepflow.engine.orchestrator import Orchestrator
from deepflow.models.manifest import Manifest, extract_env_refs


def setup_logging(verbose: bool) -> None:
    """CLI 日志配置。

    root logger 设为 INFO（verbose 时 DEBUG），使 INFO 记录能到达文件
    handler（run.log / per-case log.txt）；console handler 单独按级别过滤，
    非 verbose 时控制台仅输出 WARNING 及以上（与原行为一致，走 stderr）。
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler(sys.stdout if verbose else sys.stderr)
    handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    handler.addFilter(ContextInjectFilter())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(run_id)s/%(case_id)s] %(name)s %(levelname)s - %(message)s",
    ))
    root.addHandler(handler)


@click.group()
@click.version_option()
def cli() -> None:
    """deepflow runner"""


@cli.command()
@click.option("-c", "--config", type=click.Path(exists=True, path_type=Path), required=True, help="manifest.yaml 路径")
@click.option("-v", "--verbose", is_flag=True, help="启用详细日志")
@click.option("--dry-run", is_flag=True, help="执行 preprocess 后展示执行计划，不实际运行")
def run(config: Path, verbose: bool, dry_run: bool) -> None:
    """执行 Pipeline"""
    setup_logging(verbose)
    log = logging.getLogger(__name__)

    log.info("Loading manifest: %s", config)
    with config.open() as f:
        manifest = Manifest.model_validate(yaml.safe_load(f))

    orchestrator = Orchestrator(manifest, config.parent)

    if dry_run:
        _render_dry_run(orchestrator, config)
        return

    # 整运行日志：落在 manifest 目录下 .deepflow/logs/{run_id}.log。
    # 不放 workspace —— builtin:clean_workspace 会在 preprocess 阶段清空 workspace，
    # 已打开的句柄会指向被删除的文件。
    log_dir = config.parent / ".deepflow" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_log = attach_run_log(
        log_dir / f"{orchestrator.run_id}.log",
        level=logging.DEBUG if verbose else logging.INFO,
    )

    try:
        if verbose:
            # verbose 模式：传统日志输出
            orchestrator.run()
            click.echo(f"\nTotal cases: {orchestrator.metrics_collector.completed_count}")
        else:
            # 非 verbose：Rich 进度面板
            from deepflow.cli.renderer import PipelineRenderer

            renderer = PipelineRenderer(manifest.name)
            orchestrator.add_hook(CliRendererHook(renderer), builtin=True)
            renderer.start()
            try:
                orchestrator.run()
            finally:
                renderer.stop()
            renderer.print_summary(orchestrator.metrics_collector.aggregate_errors())
    finally:
        detach_run_log(run_log)


def _render_dry_run(orchestrator: Orchestrator, config: Path) -> None:
    """执行 preprocess 并用 Rich 渲染执行计划。"""
    from rich.console import Console

    from deepflow.models.manifest import extract_env_refs

    console = Console()
    plan = orchestrator.dry_run()

    console.print()
    console.print(f"[bold]Pipeline:[/bold] {plan['pipeline_name']}")
    console.print(f"[bold]Workspace:[/bold] {plan['workspace']}")
    console.print()

    # preprocess 结果
    console.print(f"  [green]✓[/green]  preprocess  {plan['preprocess_steps']} steps")
    console.print()

    # 执行计划
    console.print("[bold]  Execution Plan:[/bold]")
    console.print(f"    Cases:        {plan['total_cases']}")
    console.print(f"    Concurrency:  {plan['concurrency']}")
    console.print(f"    Casewise steps per case: {len(plan['casewise_steps'])}")
    for i, step in enumerate(plan["casewise_steps"], 1):
        console.print(f"      {i}. {step['class_name']}")
    console.print(f"    Estimated batches: {plan['estimated_batches']}")
    console.print()

    # postprocess
    console.print(f"  [dim]○[/dim]  postprocess ({len(plan['postprocess_steps'])} steps, will not run):")
    for i, step in enumerate(plan["postprocess_steps"], 1):
        console.print(f"      {i}. {step['class_name']}")
    console.print()

    # 环境变量检查
    with config.open() as f:
        raw_data = yaml.safe_load(f)
    env_refs = extract_env_refs(raw_data)
    if env_refs:
        console.print("  [bold]Environment:[/bold]")
        for var_name in sorted(env_refs):
            if var_name in os.environ:
                console.print(f"    [green]✓[/green] {var_name}")
            else:
                console.print(f"    [red]✗[/red] {var_name} [dim](not set)[/dim]")


@cli.command()
@click.option("-o", "--output", type=click.Path(path_type=Path), default=Path("manifest.yaml"), help="输出文件路径")
def init(output: Path) -> None:
    """生成示例 manifest.yaml"""
    template = '''\
version: "2.0"
name: sample-pipeline
workspace: ./workspace
concurrency: 1

vars: {}
  # my_var: "value"

pipeline:
  preprocess:
    - src: builtin:clean_workspace
    # - src: ./components/my_preprocess.py

  casewise: []
    # - src: ./components/my_casewise.py
    #   config:
    #     threshold: 0.8
    #   retry:
    #     max_attempts: 3
    #     delay: 2
    #     backoff: exponential

  postprocess: []

# 生命周期观察 hook(可选):在 run/stage/step/case 各层级插入自定义逻辑
# hooks:
#   - src: ./hooks/my_hook.py
#     config:
#       some_option: "value"
'''
    output.write_text(template)
    click.echo(f"Generated manifest at {output}")


@cli.command()
@click.option("-c", "--config", type=click.Path(exists=True, path_type=Path), required=True, help="manifest.yaml 路径")
def check(config: Path) -> None:
    """校验 manifest 配置：组件可加载、阶段匹配、环境变量就绪。"""
    from deepflow.core.component import (
        CasewiseComponent,
        PostprocessComponent,
        PreprocessComponent,
    )
    from deepflow.engine.hook_loader import HookLoader
    from deepflow.engine.loader import ComponentLoader

    errors: list[str] = []
    warnings: list[str] = []

    # 1. 解析 manifest（在环境变量解析前提取引用）
    with config.open() as f:
        raw_data = yaml.safe_load(f)

    # 检查环境变量
    env_refs = extract_env_refs(raw_data)
    for var_name in sorted(env_refs):
        if var_name in os.environ:
            click.echo(f"  \u2713 环境变量 {var_name}")
        else:
            errors.append(f"环境变量 {var_name} 未设置")
            click.echo(f"  \u2717 环境变量 {var_name} 未设置")

    # 解析 manifest
    try:
        manifest = Manifest.model_validate(raw_data)
        click.echo("  \u2713 manifest 结构合法")
    except Exception as e:
        click.echo(f"  \u2717 manifest 结构错误: {e}")
        raise SystemExit(1) from None

    manifest_dir = config.parent

    # 2. 校验组件
    stage_base_map = {
        "preprocess": PreprocessComponent,
        "casewise": CasewiseComponent,
        "postprocess": PostprocessComponent,
    }

    for stage_name, steps in [
        ("preprocess", manifest.pipeline.preprocess),
        ("casewise", manifest.pipeline.casewise),
        ("postprocess", manifest.pipeline.postprocess),
    ]:
        for step in steps:
            try:
                comp_class = ComponentLoader.resolve_class(step.src, manifest_dir)
                # 检查阶段匹配
                expected_base = stage_base_map[stage_name]
                if not issubclass(comp_class, expected_base):
                    actual = comp_class.__mro__[1].__name__
                    errors.append(
                        f"{step.src} 是 {actual}，但放在了 {stage_name} 阶段"
                    )
                    click.echo(f"  \u2717 {step.src} — 阶段不匹配 ({actual} in {stage_name})")
                else:
                    click.echo(f"  \u2713 {step.src} — {stage_name}")
            except Exception as e:
                errors.append(f"{step.src}: {e}")
                click.echo(f"  \u2717 {step.src} — {e}")

    # 3. 校验 hook 类与声明配置
    for hook in manifest.hooks:
        try:
            hook_class = HookLoader.resolve_class(hook.src, manifest_dir)
            if hook_class.Config is not None:
                hook_class.Config.model_validate(hook.config)
            click.echo(f"  \u2713 {hook.src} — hook")
        except Exception as e:
            errors.append(f"{hook.src}: {e}")
            click.echo(f"  \u2717 {hook.src} — {e}")

    # 4. 汇总
    click.echo()
    if errors:
        click.echo(f"  结果: {len(errors)} error(s), {len(warnings)} warning(s)")
        raise SystemExit(1)
    else:
        click.echo("  结果: 全部通过")


@cli.command()
@click.option("-H", "--host", default="0.0.0.0", help="绑定地址")
@click.option("-p", "--port", default=8000, type=int, help="绑定端口")
@click.option(
    "-d", "--data-dir",
    default=".deepflow-server",
    type=click.Path(path_type=Path),
    help="数据存储目录",
)
@click.option("-v", "--verbose", is_flag=True, help="启用详细日志")
def serve(host: str, port: int, data_dir: Path, verbose: bool) -> None:
    """启动 deepflow API 服务"""
    setup_logging(verbose)

    try:
        import uvicorn

        from deepflow.server.app import create_app
    except ImportError:
        click.echo(
            "Server dependencies not installed.\n"
            "Run: pip install 'deepflow[server]'"
        )
        raise SystemExit(1) from None

    app = create_app(data_dir)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    cli()
