from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import create_tables, SessionLocal
from app import crud
from app.templating import templates
from app.routes import api, setup, student, teacher, websocket


# ---------------------------------------------------------------------------
# Startup / Shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Переконуємось, що директорія для БД існує
    db_path = Path(settings.DATABASE_URL.replace("sqlite:///", ""))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Створюємо таблиці
    create_tables()

    # Оновлюємо існуючу БД новими колонками (sqlite ALTER TABLE)
    db = SessionLocal()
    try:
        from sqlalchemy import text
        db.execute(text("ALTER TABLE teachers ADD COLUMN subject VARCHAR(100)"))
        db.commit()
    except Exception:
        pass
    try:
        from sqlalchemy import text
        db.execute(text("ALTER TABLE teachers ADD COLUMN classes VARCHAR(255)"))
        db.commit()
    except Exception:
        pass
    try:
        from sqlalchemy import text
        db.execute(text("ALTER TABLE tests ADD COLUMN use_fuzzy_matching BOOLEAN DEFAULT 0"))
        db.commit()
    except Exception:
        pass
    finally:
        db.close()

    yield


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
    lifespan=lifespan,
)

# Gzip compression middleware to speed up loading on poor/busy connections
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Cache-Control middleware for static files (CSS, JS, test images) to reduce server load
@app.middleware("http")
async def add_cache_control_header(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    return response

# Static files
app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(Path(settings.STATIC_DIR) / "favicon.ico")

# Routers
app.include_router(setup.router)
app.include_router(teacher.router)
app.include_router(student.router)
app.include_router(api.router)
app.include_router(websocket.router)


# ---------------------------------------------------------------------------
# Global error handlers
# ---------------------------------------------------------------------------

@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc):
    from fastapi.responses import JSONResponse, RedirectResponse
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(url="/teacher/login", status_code=303)
    return JSONResponse(status_code=401, content={"detail": getattr(exc, "detail", "Необхідна авторизація")})


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"code": 404, "message": "Сторінку не знайдено"},
        status_code=404,
    )


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"code": 403, "message": "Доступ заборонено"},
        status_code=403,
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    return templates.TemplateResponse(
        request,
        "error.html",
        {"code": 500, "message": "Внутрішня помилка сервера"},
        status_code=500,
    )
