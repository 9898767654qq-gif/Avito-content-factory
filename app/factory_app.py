"""Application factory. Import this from tests; app.main builds the runtime app."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings
from app.database import init_db, make_engine, make_session_factory
from app.domain import DomainError
from app.api import router as api_router
from app.services.avito_client import build_avito_client
from app.services.generator import build_generator
from app.services.listings import process_factory_queue
from app.templating import build_templates, page_context
from app.web import router as web_router

log = logging.getLogger("parnaya.factory")


def run_factory_loop(session_factory, settings: Settings, stop: threading.Event) -> None:
    generator = build_generator(settings)
    while not stop.is_set():
        processed = False
        try:
            with session_factory() as session:
                processed = process_factory_queue(session, generator)
        except Exception:
            log.exception("factory loop failed")
        if not processed:
            stop.wait(settings.factory_poll_seconds)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    session_factory = make_session_factory(engine)
    templates = build_templates()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        thread = None
        if app.state.settings.run_factory_loop:
            thread = threading.Thread(
                target=run_factory_loop,
                args=(app.state.session_factory, app.state.settings, stop),
                name="factory-loop",
                daemon=True,
            )
            thread.start()
        yield
        stop.set()
        if thread is not None:
            thread.join(timeout=2)

    app = FastAPI(
        title="Парная",
        description="Фабрика объявлений для публикатора Авито. Публикация только после согласования.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.templates = templates
    app.state.generator = build_generator(settings)
    app.state.avito_client = build_avito_client(settings)

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.include_router(web_router)
    app.include_router(api_router)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        if request.url.path.startswith("/api"):
            return JSONResponse({"detail": exc.message}, status_code=400)
        return templates.TemplateResponse(
            request,
            "error.html",
            page_context(request, message=exc.message),
            status_code=400,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api"):
            detail = exc.detail if isinstance(exc.detail, str) else "Ошибка"
            return JSONResponse({"detail": detail}, status_code=exc.status_code)
        message = "Такой страницы нет." if exc.status_code == 404 else str(exc.detail)
        return templates.TemplateResponse(
            request,
            "error.html",
            page_context(request, message=message),
            status_code=exc.status_code,
        )

    return app
