import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api import capture_screen, router
from .config import ROOT, Settings
from .demo import demo_app
from .events import CommandRunner, EventBus
from .sessions import BrowserSession


def create_app(settings=None, session=None):
    settings = settings or Settings()
    session = session or BrowserSession(settings)

    @asynccontextmanager
    async def lifespan(app):
        capture = asyncio.create_task(capture_screen(app))
        try:
            yield
        finally:
            capture.cancel()
            with suppress(asyncio.CancelledError):
                await capture
            task = app.state.runner.task
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await session.close()

    app = FastAPI(title='Web Agent ZM', lifespan=lifespan)
    app.state.settings = settings
    app.state.session = session
    app.state.bus = EventBus()
    app.state.runner = CommandRunner(session, app.state.bus)
    app.state.latest_frame = None
    app.state.screen_clients = 0
    origins = [settings.frontend_origin, 'http://127.0.0.1:8000', 'http://localhost:8000']
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type'])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'test', 'testserver'])

    @app.middleware('http')
    async def origin_check(request: Request, call_next):
        origin = request.headers.get('origin')
        if origin and origin not in origins:
            return JSONResponse({'detail': '不允许外部网页访问本地服务'}, status_code=403)
        return await call_next(request)

    app.include_router(router)
    app.include_router(demo_app.router)
    dist = ROOT / 'frontend' / 'dist'
    if dist.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
    return app


app = create_app()
