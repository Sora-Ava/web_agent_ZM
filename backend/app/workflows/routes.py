from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, StrictStr

from ..sessions import BusyError
from .models import WorkflowDefinition
from .repository import VersionConflict
from .suggestions import ParameterSuggester
from ..contracts import DeleteConfirmed

router = APIRouter(prefix='/api')


class SaveInput(BaseModel):
    definition: WorkflowDefinition
    confirmed: Literal[True]
    source_revision: str | None = None


class VersionInput(SaveInput):
    expected_version: int = Field(ge=1)


class ProposalInput(BaseModel):
    use_model: bool = True


class RunInput(BaseModel):
    parameters: dict[str, StrictStr] = Field(default_factory=dict)
    version: int | None = Field(default=None, ge=1)


class DeleteWorkflowInput(DeleteConfirmed):
    expected_version: int = Field(ge=1)


@router.delete('/workflows/{flow_id}')
async def delete_workflow(flow_id: str, body: DeleteWorkflowInput, request: Request):
    try:
        request.app.state.repository.delete(flow_id, body.expected_version)
        await request.app.state.bus.publish({'type':'workflows_changed'})
        return {'ok': True}
    except (ValueError, KeyError, BusyError) as exc:
        failure(exc)


def failure(exc):
    if isinstance(exc, (VersionConflict, BusyError)):
        raise HTTPException(409, str(exc))
    if isinstance(exc, KeyError):
        raise HTTPException(404, '流程或版本不存在')
    if isinstance(exc, ValueError):
        raise HTTPException(422, str(exc))
    raise exc


@router.get('/recording')
async def recording(request: Request):
    return request.app.state.repository.draft()


@router.post('/recording/reset')
async def reset_recording(request: Request):
    if request.app.state.session.busy:
        raise HTTPException(409, '浏览器忙碌，不能重置录制')
    request.app.state.runner.agent.history = []
    request.app.state.runner.commands.clear()
    request.app.state.tasks.authoring = True
    return request.app.state.repository.reset_draft()


@router.post('/recording/proposal')
async def proposal(body: ProposalInput, request: Request):
    state = request.app.state
    if state.session.busy:
        raise HTTPException(409, '请等待当前操作完成')
    try:
        result = state.repository.proposal()
        if body.use_model:
            suggester = ParameterSuggester(state.settings)
            definition, calls = await suggester.suggest(WorkflowDefinition.model_validate(result['definition']))
            result.update(definition=definition.model_dump(), suggestion_source=suggester.source, model_calls=calls)
        return result
    except ValueError as exc:
        failure(exc)


@router.get('/workflows')
async def list_workflows(request: Request):
    return request.app.state.repository.list()


@router.post('/workflows', status_code=201)
async def create_workflow(body: SaveInput, request: Request):
    try:
        result = request.app.state.repository.create(body.definition, body.confirmed, body.source_revision)
        await request.app.state.bus.publish({'type': 'workflows_changed'})
        return result
    except (ValueError, KeyError) as exc:
        failure(exc)


@router.get('/workflows/{flow_id}')
async def get_workflow(flow_id: str, request: Request, version: int | None = None):
    try:
        return request.app.state.repository.get(flow_id, version)
    except KeyError as exc:
        failure(exc)


@router.post('/workflows/{flow_id}/versions', status_code=201)
async def update_workflow(flow_id: str, body: VersionInput, request: Request):
    try:
        result = request.app.state.repository.update(flow_id, body.definition, body.confirmed, body.expected_version)
        await request.app.state.bus.publish({'type': 'workflows_changed'})
        return result
    except (ValueError, KeyError) as exc:
        failure(exc)


@router.post('/workflows/{flow_id}/runs', status_code=202)
async def run_workflow(flow_id: str, body: RunInput, request: Request):
    try:
        return {'id': await request.app.state.workflows.submit(flow_id, body.version, body.parameters)}
    except (ValueError, KeyError, BusyError) as exc:
        failure(exc)
