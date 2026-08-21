"""FastAPI application entry point.

Run from the repository root:

    uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agents import registry
from .api.routes import router
from .api.service import DebateService, ValidationProblem
from .config import get_settings
from .models import db as dbm
from .services.events import bus
from .services.logging import get_logger, setup_logging
from .services.rate_limit import SlidingWindowRateLimiter
from .services.storage import DebateRepository

log = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)

    sessionmaker = dbm.init_engine(settings.database_url)
    await dbm.create_all()

    repo = DebateRepository(sessionmaker)
    app.state.repo = repo
    app.state.bus = bus
    app.state.service = DebateService(repo, bus, settings)
    app.state.limiter = SlidingWindowRateLimiter(
        settings.rate_limit_requests, settings.rate_limit_window_seconds
    )

    configured = [a.value for a in registry.configured_agents(settings)]
    log.info("AI Council backend ready. Configured providers: %s", configured or "NONE")
    if not configured:
        log.warning(
            "No provider API keys found. Copy .env.example to .env and fill in "
            "at least two keys before starting a debate."
        )

    try:
        yield
    finally:
        await app.state.service.shutdown()
        await dbm.dispose_engine()
        log.info("AI Council backend stopped.")


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title="AI Council",
        description=(
            "Collective reasoning across five independent AI models: "
            "independent answers, multi-round debate, deterministic consensus "
            "detection and a neutral final synthesis."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )

    @app.exception_handler(ValidationProblem)
    async def _validation_handler(_r: Request, exc: ValidationProblem) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.include_router(router)

    # Optional: serve a production frontend build from backend/static
    # (the Docker image copies the Vite build there).
    if STATIC_DIR.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=STATIC_DIR / "assets", check_dir=False),
            name="assets",
        )

        @app.get("/", include_in_schema=False)
        async def _index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _spa(full_path: str) -> FileResponse:
            candidate = STATIC_DIR / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    else:

        @app.get("/", include_in_schema=False)
        async def _root() -> dict[str, str]:
            return {
                "name": "AI Council",
                "docs": "/docs",
                "health": "/api/health",
                "note": "Frontend not bundled; run the Vite dev server separately.",
            }

    return app


app = create_app()
