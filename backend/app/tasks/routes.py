from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, StrictStr

from ..sessions import BusyError
from ..workflows.routes import failure

router = APIRouter(prefix='/api')


class TaskInput(BaseModel):
    flow_id: str = Field(min_length=1, max_length=80)
    version: int | None = Field(default=None, ge=1)
    rows: list[dict[str, StrictStr]] = Field(min_length=1, max_length=500)


@router.get('/tasks')
async def list_tasks(request: Request):
    return request.app.state.tasks.repository.list()


@router.post('/tasks', status_code=202)
async def submit_task(body: TaskInput, request: Request):
    try:
        identity = await request.app.state.tasks.submit(body.flow_id, body.version, body.rows)
        return {'id': identity}
    except (ValueError, KeyError) as exc:
        failure(exc)


@router.get('/tasks/{identity}')
async def get_task(identity: str, request: Request):
    try:
        return request.app.state.tasks.repository.get(identity)
    except KeyError as exc:
        failure(exc)


@router.post('/tasks/{identity}/{action}')
async def control_task(identity: str, action: Literal['pause', 'resume', 'stop'], request: Request):
    try:
        return await request.app.state.tasks.control(identity, action)
    except (ValueError, KeyError, BusyError) as exc:
        failure(exc)


@router.post('/queue/release-authoring')
async def release_authoring(request: Request):
    try:
        await request.app.state.tasks.release_authoring()
        return request.app.state.runner.snapshot()
    except BusyError as exc:
        failure(exc)
