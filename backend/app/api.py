import asyncio
from contextlib import suppress

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from .sessions import BusyError

router = APIRouter(prefix='/api')


class CommandInput(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)

    @field_validator('instruction')
    @classmethod
    def strip_instruction(cls, value):
        if not value.strip():
            raise ValueError('指令不能为空')
        return value.strip()


@router.get('/health')
async def health():
    return {'ok': True, 'phase': 1}


@router.get('/session')
async def session_state(request: Request):
    return request.app.state.runner.snapshot()


@router.post('/session/start')
async def start(request: Request):
    session = request.app.state.session
    try:
        async with session.reserve():
            await session.start()
            login = await session.login()
            if login.status != 'success':
                session.status = 'failed'
            else:
                request.app.state.runner.agent.history = []
                session.status = 'ready'
            await request.app.state.bus.publish({'type': 'session', 'message': login.message})
        return {**request.app.state.runner.snapshot(), 'login': login.model_dump()}
    except BusyError:
        raise HTTPException(409, '浏览器忙碌，请等待当前指令完成')
    except Exception:
        raise HTTPException(503, '无法启动浏览器，请确认已安装 Playwright Chromium')


@router.delete('/session')
async def close(request: Request):
    try:
        async with request.app.state.session.reserve():
            await request.app.state.session.close()
        return request.app.state.runner.snapshot()
    except BusyError:
        raise HTTPException(409, '浏览器忙碌，请等待当前指令完成')


@router.post('/commands', status_code=202)
async def command(body: CommandInput, request: Request):
    session = request.app.state.session
    if session.status != 'ready':
        raise HTTPException(409, '请先启动浏览器并成功登录')
    try:
        return {'id': await request.app.state.runner.submit(body.instruction)}
    except BusyError:
        raise HTTPException(409, '已有指令正在执行')


@router.get('/files')
async def files(request: Request):
    return request.app.state.session.downloads.list()


@router.get('/files/{identity}/download')
async def download(identity: str, request: Request):
    item = request.app.state.session.downloads.get(identity)
    if not item or not item.path.is_file() or item.path.resolve().parent != request.app.state.session.downloads.directory:
        raise HTTPException(404, '文件不存在')
    return FileResponse(item.path, filename=item.name, media_type='application/octet-stream')


def valid_origin(websocket):
    origin = websocket.headers.get('origin')
    return not origin or origin in {websocket.app.state.settings.frontend_origin, 'http://127.0.0.1:8000', 'http://localhost:8000'}


@router.websocket('/events')
async def events(websocket: WebSocket):
    if not valid_origin(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        async with websocket.app.state.bus.subscribe() as queue:
            await websocket.send_json({'type': 'snapshot', **websocket.app.state.runner.snapshot()})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), 15)
                except TimeoutError:
                    event = {'type': 'heartbeat'}
                await asyncio.wait_for(websocket.send_json(event), 5)
    except (WebSocketDisconnect, RuntimeError, TimeoutError):
        pass


@router.websocket('/screen')
async def screen(websocket: WebSocket):
    if not valid_origin(websocket):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    state = websocket.app.state
    state.screen_clients += 1
    try:
        while True:
            if state.latest_frame:
                await asyncio.wait_for(websocket.send_bytes(state.latest_frame), 3)
            await asyncio.sleep(.5)
    except (WebSocketDisconnect, RuntimeError, TimeoutError):
        pass
    finally:
        state.screen_clients -= 1


async def capture_screen(app):
    while True:
        page = app.state.session.page
        if app.state.screen_clients and page and not page.is_closed():
            with suppress(Exception):
                app.state.latest_frame = await page.screenshot(type='jpeg', quality=60, timeout=2000)
        else:
            app.state.latest_frame = None
        await asyncio.sleep(.5)
