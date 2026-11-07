"""OpsMind AI — FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import SessionLocal, init_db
from .routers import dashboard, demo, health, incidents, memory
from .services.errors import ServiceError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("opsmind")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.demo_mode:
        try:
            with SessionLocal() as db:
                from .models import Incident
                from .services.seeding import seed_database

                if db.query(Incident).count() == 0:
                    result = seed_database(db)
                    logger.info("Demo data: %s", result.get("message"))
                    logger.info(
                        "Verified demo knowledge is NOT yet in Hindsight; run "
                        "`python -m app.seed --memory-only` or POST /api/memory/sync to retain it."
                    )
        except Exception:  # noqa: BLE001
            logger.exception("Demo seeding failed; the API will still start.")
    yield


app = FastAPI(
    title="OpsMind AI",
    description=(
        "Self-learning incident intelligence and autonomous-response agent. "
        "Incident memory is powered by Hindsight (Vectorize); investigation reasoning by Groq."
    ),
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ServiceError)
async def service_error_handler(_request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code}
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    first = errors[0] if errors else {}
    location = ".".join(str(part) for part in first.get("loc", []) if part != "body")
    message = first.get("msg", "Invalid request")
    detail = f"{location}: {message}" if location else message
    return JSONResponse(
        status_code=422,
        content={
            "detail": detail,
            "code": "validation_error",
            "errors": jsonable_encoder(errors),
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Unexpected server error. Check the backend logs for the traceback.",
            "code": "internal_error",
        },
    )


app.include_router(health.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(incidents.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(demo.router, prefix="/api")


@app.get("/api", tags=["meta"])
def api_info() -> dict:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "memory_provider": "Hindsight (Vectorize)",
        "llm_provider": "Groq",
        "demo_mode": settings.demo_mode,
        "safety": (
            "Human-in-the-loop only. OpsMind never executes commands, restarts services or "
            "changes infrastructure."
        ),
    }


# ---------------------------------------------------------------------------
# Serve the built frontend (single-service deployment) when it exists.
# ---------------------------------------------------------------------------
frontend_dist = Path(settings.frontend_dist)
if frontend_dist.exists():
    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        resolved_root = frontend_dist.resolve()
        candidate = (frontend_dist / full_path).resolve()
        if candidate.is_file() and resolved_root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(
            frontend_dist / "index.html",
            headers={"Cache-Control": "no-cache"},
        )

else:

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "name": settings.app_name,
            "message": "Backend is running. Build the frontend or use the Vite dev server.",
            "docs": "/docs",
        }
