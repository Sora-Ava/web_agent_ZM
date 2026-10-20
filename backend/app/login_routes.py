from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .login import LoginController
from .workflows.models import LoginSpec, site_key
from .sessions import BusyError

router = APIRouter(prefix='/api')


class LoginInput(BaseModel):
    login: LoginSpec
    parameters: dict[str, str] = Field(default_factory=dict)


@router.get('/credentials')
async def credentials(request: Request):
    return request.app.state.settings.credentials.metadata()


@router.post('/login')
async def login(body: LoginInput, request: Request):
    state = request.app.state
    spec = body.login.model_copy(deep=True)
    try:
        if spec.url_parameter:
            spec.url = body.parameters[spec.url_parameter]
            spec.url_parameter = None
        if spec.credential_parameter:
            spec.credential_ref = body.parameters[spec.credential_parameter]
            spec.credential_parameter = None
        spec = LoginSpec.model_validate(spec.model_dump())
        state.settings.credentials.resolve(spec.credential_ref, spec.url)
        state.repository._no_secrets(spec.model_dump())
        if state.repository.scope and state.repository.scope != site_key(spec.url):
            raise ValueError('来源变化，请先新建录制')
        async with state.session.reserve():
            state.tasks.authoring = True
            result = await LoginController(state.session).execute(spec, {}, spec.url)
            state.session.status = 'ready'
            if result.status == 'success':
                state.repository.record_login(spec)
                state.runner.agent.history = []
            await state.bus.publish({'type':'login_result','result':result.model_dump()})
        return {'result':result.model_dump(), **state.runner.snapshot()}
    except BusyError:
        raise HTTPException(409, '浏览器忙碌，请等待当前操作结束')
    except (KeyError, ValueError):
        raise HTTPException(422, '登录配置、凭据引用或录制来源不正确，请检查配置并按需新建录制')
