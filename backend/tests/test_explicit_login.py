import json
import pytest
from pydantic import SecretStr
from app.workflows.models import LoginSpec, WorkflowDefinition
from app.contracts import BrowserAction


def login_spec(url):
    return LoginSpec(url=url,credential_ref='test',username={'kind':'css','value':'#username'},password={'kind':'css','value':'#password'},submit={'kind':'css','value':'#login'},success={'kind':'css','value':'#logged-in'})


def credentials(settings,url):
    settings.credentials_json=SecretStr(json.dumps({'test':{'origin':url,'username':'operator','password':'demo-password'},'other':{'origin':url,'username':'other-user','password':'other-password'}}))
    settings.__dict__.pop('credentials',None)


async def test_explicit_login_reuses_current_account_for_different_reference(session):
    from app.login import LoginController
    await session.close();await session.start()
    credentials(session.settings,session.settings.site_url)
    login=LoginController(session);spec=login_spec(session.settings.site_url)
    assert (await login.execute(spec,{},spec.url)).status=='success'
    before=session.page.url
    assert (await login.execute(spec,{},spec.url)).message=='复用当前已登录会话'
    assert session.page.url==before
    spec.credential_ref='other'
    assert (await login.execute(spec,{},spec.url)).status=='success'
    assert await session.page.locator('#logged-in').is_visible()


async def test_no_login_step_never_calls_implicit_login(session):
    from app.workflows.executor import WorkflowExecutor
    await session.close();await session.start()
    url=session.settings.site_url
    d=WorkflowDefinition(name='public',site_key=__import__('app.workflows.models',fromlist=['site_key']).site_key(url),entry_url=url,steps=[{'id':'wait','name':'等待登录页','action':{'kind':'wait','target':{'kind':'css','value':'#login'}}}])
    result=await WorkflowExecutor(session).execute(d,{})
    assert result.status=='success'
    assert session.page.url.endswith('/demo/login')
    assert await session.page.locator('#username').input_value()==''


async def test_login_rejects_redirect_before_filling(session):
    from app.login import LoginController
    credentials(session.settings,'https://login.test')
    await session.context.route('https://login.test/**',lambda r:r.fulfill(status=302,headers={'location':'https://other.test/'}))
    await session.context.route('https://other.test/**',lambda r:r.fulfill(body='<input id="username"><input id="password" type="password"><button id="login">登录</button>',content_type='text/html'))
    result=await LoginController(session).execute(login_spec('https://login.test/'),{},'https://login.test')
    assert result.status=='failed'
    assert not session.page.url.startswith('https://other.test') or await session.page.locator('#password').input_value()==''

async def test_flow_login_expiry_recovers_using_its_own_step(session):
    from app.workflows.models import site_key
    from app.workflows.executor import WorkflowExecutor
    credentials(session.settings,session.settings.site_url)
    await session.close();await session.start()
    url=session.settings.site_url;base=url.split('/demo')[0]
    d=WorkflowDefinition(name='explicit',site_key=site_key(url),entry_url=url,steps=[{'id':'login','name':'登录','login':login_spec(url)},{'id':'business','name':'业务页','action':{'kind':'navigate','value':base+'/demo/business'}},{'id':'fill','name':'填写','action':{'kind':'fill','value':'EXPLICIT','target':{'kind':'css','value':'#batch'}}}])
    events=[]
    async def emit(e):
        events.append(e)
        if e.get('id')=='business' and e['type']=='workflow_step':
            await session.context.clear_cookies();await session.page.goto(url)
    result=await WorkflowExecutor(session).execute(d,{},emit)
    assert result.status=='success',result.message
    assert result.model_calls==0
    assert any(e['type']=='login_recovery' for e in events)
    assert await session.page.locator('#batch').input_value()=='EXPLICIT'

async def test_executor_does_not_navigate_away_from_reusable_login(session):
    from app.workflows.models import site_key
    from app.workflows.executor import WorkflowExecutor
    credentials(session.settings,session.settings.site_url)
    await session.close();await session.start()
    requests=[]
    session.page.on('request',lambda r:requests.append(r.url) if r.url.endswith('/demo/auth') else None)
    url=session.settings.site_url
    d=WorkflowDefinition(name='reuse',site_key=site_key(url),entry_url=url,steps=[{'id':'login','name':'登录','login':login_spec(url)}])
    for _ in range(2):
        assert (await WorkflowExecutor(session).execute(d,{})).status=='success'
    assert len(requests)==1

async def test_missing_success_marker_still_requires_a_real_verified_login(session):
    from app.login import LoginController
    credentials(session.settings,session.settings.site_url)
    spec=login_spec(session.settings.site_url);spec.credential_ref='other'
    await session.page.goto(session.settings.site_url.split('/demo')[0]+'/demo/business')
    spec.success={'kind':'css','value':'#another-ready'}
    from app.contracts import LocatorSpec
    spec.success=LocatorSpec.model_validate(spec.success)
    calls=[];session.page.on('request',lambda r:calls.append(1) if r.url.endswith('/demo/auth') else None)
    result=await LoginController(session).execute(spec,{},spec.url)
    assert result.status=='failed'
    assert calls==[1]
    assert session.authenticated=={}

async def test_uncertain_export_does_not_trigger_login_retry(session,monkeypatch):
    from workflow_samples import sample_with_login
    from app.workflows.executor import WorkflowExecutor
    from app.contracts import ActionResult
    calls=[];original=session.execute
    async def execute(action):
        if action.kind=='download':
            calls.append(1);await session.context.clear_cookies();await session.page.goto(session.settings.site_url)
            return ActionResult(status='failed',message='started then expired',effect_started=True)
        return await original(action)
    monkeypatch.setattr(session,'execute',execute)
    result=await WorkflowExecutor(session).execute(sample_with_login(session.settings.site_url.split('/demo')[0]),{'batch':'ONCE'})
    assert result.status=='failed'
    assert len(calls)==1

async def test_failed_password_never_marks_session_verified(session):
    from app.login import LoginController
    await session.close();await session.start()
    session.settings.credentials_json=SecretStr(json.dumps({'bad':{'origin':session.settings.site_url,'username':'operator','password':'wrong-synthetic-password'}}))
    session.settings.__dict__.pop('credentials',None)
    spec=login_spec(session.settings.site_url);spec.credential_ref='bad'
    result=await LoginController(session).execute(spec,{},spec.url)
    assert result.status=='failed'
    assert session.authenticated=={}
    assert 'wrong-synthetic-password' not in result.message
    assert 'wrong-synthetic-password' not in (await session.observe(generic=True)).model_dump_json()
