from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.accounts.routes import router as accounts_router
from app.admin.routes import router as admin_router
from app.auth.factor_routes import router as factor_router
from app.auth.middleware import AuthenticationMiddleware
from app.auth.routes import router as auth_router
from app.catalog.routes import router as catalog_router
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.crew.routes import router as crew_router
from app.employee.routes import router as employee_router
from app.hours.routes import router as hours_router
from app.notifications.routes import router as notifications_router
from app.open_positions.routes import router as open_positions_router
from app.rostering.routes import router as rostering_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="On Track Rostering",
    version=get_settings().app_version,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)
app.add_middleware(AuthenticationMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(get_settings().allowed_hosts))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(auth_router)
app.include_router(factor_router)
app.include_router(employee_router)
app.include_router(hours_router)
app.include_router(rostering_router)
app.include_router(open_positions_router)
app.include_router(notifications_router)
app.include_router(crew_router)
app.include_router(admin_router)
app.include_router(accounts_router)
app.include_router(catalog_router)


@app.get("/health/live", include_in_schema=False)
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
def ready() -> dict[str, str]:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> Response:
    return Response(
        (Path(__file__).parent / "static" / "manifest.webmanifest").read_text(),
        media_type="application/manifest+json",
    )


@app.get("/service-worker.js", include_in_schema=False)
def service_worker() -> Response:
    return Response(
        (Path(__file__).parent / "static" / "service-worker.js").read_text(),
        media_type="application/javascript",
    )
