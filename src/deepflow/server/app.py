"""
FastAPI 应用工厂。

遵循 "一个入口，清晰的组装" 原则：
    create_app() 是唯一的应用构建点，
    lifespan 管理服务生命周期，
    路由通过 include_router 显式挂载。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from deepflow.server.dependencies import init_services
from deepflow.server.routes import components, pipelines, runs, utilities
from deepflow.server.services.pipeline_store import PipelineStore
from deepflow.server.services.run_manager import RunManager

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化服务，关闭时优雅退出。"""
    data_dir = Path(app.state.data_dir)

    run_manager = RunManager(data_dir / "runs")
    pipeline_store = PipelineStore(data_dir / "pipelines")
    init_services(run_manager, pipeline_store)

    yield

    await run_manager.shutdown()


def create_app(data_dir: str | Path = ".deepflow-server") -> FastAPI:
    """应用工厂，返回配置完成的 FastAPI 实例。"""
    app = FastAPI(
        title="DeepFlow API",
        description="RESTful execution platform for deepflow pipelines",
        version="0.3.0",
        lifespan=lifespan,
    )

    app.state.data_dir = str(data_dir)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(pipelines.router)
    app.include_router(runs.router)
    app.include_router(components.router)
    app.include_router(utilities.router)

    # 前端静态文件：仅在构建产物存在时挂载
    if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").exists():
        # SPA fallback：非 API、非静态资源的请求返回 index.html
        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            file = STATIC_DIR / full_path
            if file.is_file():
                return FileResponse(file)
            return FileResponse(STATIC_DIR / "index.html")

        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="static")

    return app
