import asyncio
from contextlib import suppress

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from .sessions import BusyError
from .contracts import DeleteConfirmed
from .connections import until_disconnected

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
    return {'ok': True, 'phase': 5, 'app': 'web-agent-zm'}


@router.get('/session')
async def session_state(request: Request):
    return request.app.state.runner.snapshot()


@router.post('/session/start')
async def start(request: Request):
    session = request.app.state.session
    try:
        async with session.reserve():
            request.app.state.tasks.authoring = True
            await session.start(visible=True)
            request.app.state.runner.agent.history = []
            session.status = 'ready'
            await request.app.state.bus.publish({'type': 'session', 'message': '浏览器已就绪，请指定要访问的网址'})
        return request.app.state.runner.snapshot()
    except BusyError:
        raise HTTPException(409, '浏览器忙碌，请等待当前指令完成')
    except Exception:
        raise HTTPException(503, '无法启动浏览器，请确认已安装 Playwright Chromium')


@router.delete('/session')
async def close(request: Request):
    try:
        async with request.app.state.session.reserve(allow_manual=True):
            await request.app.state.session.close()
        return request.app.state.runner.snapshot()
    except BusyError:
        raise HTTPException(409, '浏览器忙碌，请等待当前指令完成')


@router.post('/session/takeover')
async def takeover(request: Request):
    try:
        await request.app.state.session.request_takeover()
        request.app.state.tasks.wake()
        return request.app.state.runner.snapshot()
    except ValueError:
        raise HTTPException(409, '请先启动浏览器')


@router.post('/session/return-control')
async def return_control(request: Request):
    state = request.app.state
    try:
        await state.session.return_control()
        state.runner.agent.history = []
        state.repository.warn('人工操作未录制，请检查草稿完整性后再保存标准流程')
        state.tasks.wake()
        return state.runner.snapshot()
    except (BusyError, ValueError) as exc:
        raise HTTPException(409, str(exc))


@router.post('/commands', status_code=202)
async def command(body: CommandInput, request: Request):
    session = request.app.state.session
    if session.status != 'ready':
        raise HTTPException(409, '请先启动浏览器')
    try:
        return {'id': await request.app.state.runner.submit(body.instruction)}
    except BusyError:
        raise HTTPException(409, '已有指令正在执行')


@router.get('/files')
async def files(request: Request):
    return request.app.state.session.downloads.list()


@router.delete('/files/{identity}')
async def delete_file(identity: str, body: DeleteConfirmed, request: Request):
    from .workflows.routes import failure
    try:
        if request.app.state.session.busy:
            raise BusyError('浏览器执行或暂停中，不能删除文件')
        request.app.state.session.downloads.delete(identity)
        await request.app.state.bus.publish({'type':'files_changed'})
        return {'ok': True, 'recycled': True}
    except OSError:
        raise HTTPException(409, '文件可能被占用或回收目录不可写，请关闭占用程序并检查目录权限后重试')
    except (ValueError, KeyError, BusyError) as exc:
        failure(exc)


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
            async def produce():
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), 15)
                    except TimeoutError:
                        event = {'type': 'heartbeat'}
                    await asyncio.wait_for(websocket.send_json(event), 5)
            await until_disconnected(websocket, produce)
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
        async def produce():
            while True:
                if state.latest_frame:
                    await asyncio.wait_for(websocket.send_bytes(state.latest_frame), 3)
                await asyncio.sleep(.5)
        await until_disconnected(websocket, produce)
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
