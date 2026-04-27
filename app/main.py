"""FastAPI application factory + lifespan + route registration."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from .config import settings
from .database import Base, engine, SessionLocal
from .routes import admin as admin_routes
from .routes import auth as auth_routes
from .routes import mailboxes as mailboxes_routes
from .routes import messages as messages_routes
from .routes import settings as settings_routes
from .routes import webhook as webhook_routes

logger = logging.getLogger("softrise")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _init_db() -> None:
    """Create tables (idempotent) and ensure required indexes/extensions exist."""
    # Make sure the partial unique index works (no extension required in Neon).
    Base.metadata.create_all(bind=engine)
    # Best-effort: attach a fulltext-friendly trigram extension for nicer search.
    with engine.connect() as conn:
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            conn.commit()
        except Exception as exc:  # pragma: no cover
            logger.info("pg_trgm extension not enabled (skipping): %s", exc)
            conn.rollback()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info(
        "Starting Softrise Mail backend on port %s (env=%s)",
        settings.PORT,
        settings.APP_ENV,
    )
    _init_db()
    yield
    logger.info("Shutting down Softrise Mail backend")


app = FastAPI(
    title="Softrise Mail",
    version="1.0.0",
    description="Email receiving service for @softrise.app addresses.",
    lifespan=lifespan,
)


# ---------- Static files / templates ----------

BASE_DIR = settings.BASE_DIR
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR, html=False), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _render(request: Request, name: str, context: dict | None = None) -> HTMLResponse:
    """Render a Jinja template with no-store headers for dev safety.

    Templates are tiny shells; the browser SPA fetches /api/auth/me on load
    to discover the current user.  We don't want a stale "demo" build cached
    anywhere in the chain, so every HTML page sets ``Cache-Control: no-store``.
    """
    target = TEMPLATES_DIR / name
    if not target.exists():
        raise HTTPException(status_code=500, detail=f"{name} missing.")
    response = templates.TemplateResponse(request, name, context or {})
    for k, v in _NO_STORE_HEADERS.items():
        response.headers[k] = v
    return response


@app.get("/", include_in_schema=False)
def serve_index(request: Request):
    return _render(request, "index.html")


@app.get("/starred", include_in_schema=False)
def serve_starred(request: Request):
    return _render(request, "starred.html")


@app.get("/archive", include_in_schema=False)
def serve_archive(request: Request):
    return _render(request, "archive.html")


@app.get("/trash", include_in_schema=False)
def serve_trash(request: Request):
    return _render(request, "trash.html")


@app.get("/mailboxes", include_in_schema=False)
def serve_mailboxes(request: Request):
    return _render(request, "mailboxes.html")


@app.get("/settings", include_in_schema=False)
def serve_settings(request: Request):
    return _render(request, "settings.html")


@app.get("/login", include_in_schema=False)
def serve_login(request: Request):
    return _render(request, "login.html")


@app.get("/register", include_in_schema=False)
def serve_register(request: Request):
    return _render(request, "register.html")


@app.get("/message/{message_id}", include_in_schema=False)
def serve_message(request: Request, message_id: str):
    """Dedicated email-detail page (replaces the in-page modal).

    The template is a thin SPA shell: ``static/message.js`` reads the id from
    ``window.location.pathname``, calls ``/api/messages/{id}`` (which enforces
    ownership + login), and redirects to ``/login`` on 401 or shows a clean
    not-found panel on 404.
    """
    return _render(request, "message.html", {"message_id": message_id})


@app.get("/admin", include_in_schema=False)
def serve_admin():
    admin_html = BASE_DIR / "admin.html"
    if admin_html.exists():
        return FileResponse(admin_html, media_type="text/html", headers=_NO_STORE_HEADERS)
    raise HTTPException(status_code=404, detail="Admin page not found.")


@app.get("/health")
def health():
    """Cheap liveness probe; checks DB connectivity."""
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok", "version": app.version}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "error", "error": str(exc)},
        )
    finally:
        db.close()


# ---------- Routers ----------

app.include_router(auth_routes.router)
app.include_router(mailboxes_routes.router)
app.include_router(messages_routes.router)
app.include_router(settings_routes.router)
app.include_router(webhook_routes.router)
app.include_router(admin_routes.router)


# ---------- Error handlers ----------


@app.exception_handler(HTTPException)
async def http_exc_handler(_request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status": exc.status_code},
        headers=exc.headers or {},
    )


@app.exception_handler(RequestValidationError)
async def validation_exc_handler(_request: Request, exc: RequestValidationError):
    errors = []
    for err in exc.errors():
        errors.append(
            {
                "field": ".".join(str(p) for p in err.get("loc", [])),
                "message": err.get("msg"),
                "type": err.get("type"),
            }
        )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": "Validation failed.", "errors": errors, "status": 422},
    )


@app.exception_handler(Exception)
async def unhandled_exc_handler(_request: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    payload = {"error": "Internal server error.", "status": 500}
    if not settings.is_production:
        payload["detail"] = repr(exc)
    return JSONResponse(status_code=500, content=payload)
