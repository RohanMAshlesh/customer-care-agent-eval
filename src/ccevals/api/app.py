"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ccevals import __version__
from ccevals.api.routes import evidence, governance, operations
from ccevals.config import Settings, get_settings
from ccevals.db import new_session
from ccevals.evaluation.semantic import Judge, build_judge
from ccevals.security import RateLimiter

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, judge: Judge | None = None) -> FastAPI:
    settings = settings or get_settings()
    problems = settings.validate_for_production()
    if problems:
        raise RuntimeError("unsafe production configuration: " + "; ".join(problems))

    app = FastAPI(
        title="Customer Care Agent Evaluation",
        version=__version__,
        description="Evidence-backed evaluation, verdict revisions and release decisions for customer-care AI agents.",
    )
    app.state.judge = judge or build_judge(settings.judge_provider, settings.judge_model, settings.judge_api_key,
                                           settings.judge_base_url, settings.judge_timeout_seconds)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_request_bytes:
            return JSONResponse({"detail": f"request body exceeds {settings.max_request_bytes} bytes"}, status_code=413)
        return await call_next(request)

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["health"])
    def readyz() -> JSONResponse:
        try:
            with new_session() as session:
                session.execute(text("SELECT 1"))
        except Exception as exc:  # report, do not crash the probe
            log.warning("readiness check failed: %s", exc)
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready", "judge": app.state.judge.bundle_id})

    app.include_router(evidence.router)
    app.include_router(governance.router)
    app.include_router(operations.router)
    return app
