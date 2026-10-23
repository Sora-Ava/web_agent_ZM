from contextlib import closing

import pytest
from app.login import LoginController
from app.workflows.executor import WorkflowExecutor
from app.workflows.models import WorkflowDefinition, site_key
from test_explicit_login import login_spec


def unavailable(session, monkeypatch):
    calls = []
    def resolve(*args):
        calls.append(args)
        raise ValueError('本机凭据无法解密，请重新录入')
    monkeypatch.setattr(session.settings.credentials, 'resolve', resolve)
    return calls


@pytest.mark.parametrize('identity', [None, 'other'])
async def test_current_success_reuses_any_identity_without_resolve(session, monkeypatch, identity):
    scope = site_key(session.settings.site_url)
    session.authenticated.clear()
    if identity:
        session.authenticated[scope] = identity
    calls = unavailable(session, monkeypatch)
    before = session.page.url
    spec = login_spec(session.settings.site_url)
    spec.credential_ref = 'missing-ref'
    result = await LoginController(session).execute(spec, {}, scope)
    assert result.status == 'success', result.message
    assert session.page.url == before
    assert calls == []
    assert session.authenticated.get(scope) == identity


async def test_redirect_success_reuses_unknown_session_without_resolve(session, monkeypatch):
    url = session.settings.site_url
    base = url.split('/demo')[0]
    await session.page.goto(base + '/demo/business')
    await session.page.locator('#logged-in').evaluate('(el)=>el.remove()')
    session.authenticated.clear()
    await session.context.route(url, lambda r: r.fulfill(status=302, headers={'location':base+'/demo/home'}))
    calls = unavailable(session, monkeypatch)
    result = await LoginController(session).execute(login_spec(url), {}, url)
    assert result.status == 'success', result.message
    assert calls == []
    assert session.authenticated == {}


@pytest.mark.parametrize('force,reuse', [(True, True), (False, False)])
async def test_fresh_login_requires_form_on_authenticated_redirect(session, monkeypatch, force, reuse):
    url = session.settings.site_url
    base = url.split('/demo')[0]
    await session.context.route(url, lambda r: r.fulfill(status=302, headers={'location':base+'/demo/home'}))
    calls = unavailable(session, monkeypatch)
    spec = login_spec(url); spec.reuse = reuse
    result = await LoginController(session).execute(spec, {}, url, force=force)
    assert result.status == 'failed'
    assert '重新登录' in result.message
    assert calls == []


async def test_real_form_resolves_and_fails_without_credentials(session, monkeypatch):
    await session.context.clear_cookies()
    await session.page.goto(session.settings.site_url)
    calls = unavailable(session, monkeypatch)
    result = await LoginController(session).execute(login_spec(session.settings.site_url), {}, session.settings.site_url)
    assert result.status == 'failed'
    assert len(calls) == 1
    assert await session.page.locator('#password').input_value() == ''


async def test_workflow_prevalidation_does_not_resolve(session, monkeypatch):
    url = session.settings.site_url
    calls = unavailable(session, monkeypatch)
    spec = login_spec(url); spec.credential_ref = 'missing-ref'
    definition = WorkflowDefinition(name='reuse', site_key=site_key(url), entry_url=url,
        steps=[{'id':'login','name':'登录','login':spec}])
    result = await WorkflowExecutor(session).execute(definition, {})
    assert result.status == 'success', result.message
    assert calls == []


async def test_success_on_foreign_page_is_not_reused(session, monkeypatch):
    url = session.settings.site_url
    await session.context.route('https://foreign.test/**', lambda r: r.fulfill(body='<div id="logged-in">Ready</div>', content_type='text/html'))
    await session.page.goto('https://foreign.test/')
    await session.context.clear_cookies()
    calls = unavailable(session, monkeypatch)
    result = await LoginController(session).execute(login_spec(url), {}, url)
    assert result.status == 'failed'
    assert len(calls) == 1
    assert session.page.url == url


async def test_known_foreign_credential_metadata_still_rejected(session, monkeypatch):
    calls = unavailable(session, monkeypatch)
    monkeypatch.setattr(session.settings.credentials, 'metadata', lambda: [{'name':'test','origin':'https://foreign.test','ready':True}])
    result = await LoginController(session).execute(login_spec(session.settings.site_url), {}, session.settings.site_url)
    assert result.status == 'failed'
    assert '来源' in result.message
    assert calls == []


async def test_password_disappearance_without_success_marker_is_not_login(session):
    url = session.settings.site_url
    await session.context.clear_cookies()
    await session.page.goto(url)
    await session.context.route(url, lambda r: r.fulfill(body='<p>Loading</p>', content_type='text/html'))
    result = await LoginController(session).execute(login_spec(url), {}, url)
    assert result.status == 'failed'


async def test_partial_metadata_does_not_prevent_session_reuse(session, monkeypatch):
    calls = unavailable(session, monkeypatch)
    monkeypatch.setattr(session.settings.credentials, 'metadata', lambda: [{'name':'test','origin':site_key(session.settings.site_url),'ready':False}])
    result = await LoginController(session).execute(login_spec(session.settings.site_url), {}, session.settings.site_url)
    assert result.status == 'success'
    assert calls == []


@pytest.mark.parametrize('ref', ['x'*81, '   '])
async def test_bound_reference_shape_is_checked_before_reuse(session, monkeypatch, ref):
    from app.workflows.models import validate_parameters
    url = session.settings.site_url
    spec = login_spec(url); spec.credential_ref = None; spec.credential_parameter = 'credential'
    definition = WorkflowDefinition(name='bound',site_key=site_key(url),entry_url=url,
        parameters=[{'key':'credential','label':'凭据','kind':'credential'}],
        steps=[{'id':'login','name':'登录','login':spec}])
    calls = unavailable(session, monkeypatch)
    with pytest.raises(ValueError):
        validate_parameters(definition, {'credential':ref}, session.settings.credentials)
    result = await LoginController(session).execute(spec, {'credential':ref}, url)
    assert result.status == 'failed'
    assert calls == []


async def test_login_api_reuse_does_not_resolve_missing_ref(session, monkeypatch):
    import httpx
    from app.main import create_app
    app = create_app(session.settings, session)
    calls = unavailable(session, monkeypatch)
    session.authenticated.clear()
    spec = login_spec(session.settings.site_url); spec.credential_ref = 'missing-ref'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response = await client.post('/api/login',json={'login':spec.model_dump(),'parameters':{}})
    assert response.status_code == 200, response.text
    assert response.json()['result']['status'] == 'success'
    assert session.authenticated == {}
    assert calls == []
    await app.state.tasks.shutdown()


async def test_task_api_preflight_does_not_resolve_stored_workflow_credentials(session, monkeypatch):
    import httpx
    from app.main import create_app
    app = create_app(session.settings, session)
    url = session.settings.site_url
    definition = WorkflowDefinition(name='queued reuse',site_key=site_key(url),entry_url=url,
        parameters=[{'key':'credential','label':'凭据','kind':'credential','default':'test'}],
        steps=[{'id':'login','name':'登录','login':{**login_spec(url).model_dump(),'credential_ref':None,'credential_parameter':'credential'}}])
    saved = app.state.repository.create(definition, True)
    calls = unavailable(session, monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response = await client.post('/api/tasks', json={'flow_id':saved['id'],'rows':[{'credential':'missing-ref'}]})
        assert response.status_code == 202, response.text
        identity = response.json()['id']
        await app.state.tasks.dispatcher
        task = (await client.get('/api/tasks/'+identity)).json()
        assert task['status'] == 'completed', task
        assert task['rows'][0]['status'] == 'success', task
    assert calls == []
    await app.state.tasks.shutdown()


async def test_corrupt_local_credential_reuse_controller_and_api(session, monkeypatch):
    import httpx
    import app.local_credentials as local
    from app.main import create_app
    url = session.settings.site_url
    store = session.settings.credentials
    ref = 'local-corrupt-synthetic'
    with closing(store._connection()) as db, db:
        db.execute('INSERT INTO local_credentials(id,origin,payload,created_at,updated_at,field_mask) VALUES(?,?,?,?,?,?)',
            (ref, site_key(url), b'broken-encryption-synthetic', 'now', 'now', 3))
    calls = []
    def unprotect(payload):
        calls.append(payload)
        raise OSError('synthetic decryption unavailable')
    monkeypatch.setattr(local, 'unprotect', unprotect)
    spec = login_spec(url); spec.credential_ref = ref
    session.authenticated.clear()
    result = await LoginController(session).execute(spec, {}, url)
    assert result.status == 'success', result.message
    assert calls == []  # Controller reuse does not attempt DPAPI.
    app = create_app(session.settings, session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response = await client.post('/api/login',json={'login':spec.model_dump(),'parameters':{}})
    assert response.status_code == 200, response.text
    assert response.json()['result']['status'] == 'success'
    assert calls  # Best-effort leak checks may decrypt, but failure cannot block reuse.
    assert session.authenticated == {}
    await session.context.clear_cookies()
    await session.page.goto(url)
    result = await LoginController(session).execute(spec, {}, url)
    assert result.status == 'failed'
    assert await session.page.locator('#password').input_value() == ''
    await app.state.tasks.shutdown()


async def test_missing_reference_fails_only_when_form_needs_fill(session):
    url = session.settings.site_url
    spec = login_spec(url); spec.credential_ref = 'missing-ref'
    assert (await LoginController(session).execute(spec, {}, url)).status == 'success'
    await session.context.clear_cookies()
    await session.page.goto(url)
    result = await LoginController(session).execute(spec, {}, url)
    assert result.status == 'failed'
    assert '凭据引用不存在' in result.message
    assert await session.page.locator('#username').input_value() == ''
    assert await session.page.locator('#password').input_value() == ''


async def test_workflow_run_api_preflight_does_not_resolve_credentials(session, monkeypatch):
    import httpx
    from app.main import create_app
    app = create_app(session.settings, session)
    url = session.settings.site_url
    definition = WorkflowDefinition(name='API reuse', site_key=site_key(url), entry_url=url,
        steps=[{'id':'login','name':'登录','login':login_spec(url)}])
    saved = app.state.repository.create(definition, True)
    calls = unavailable(session, monkeypatch)
    session.authenticated.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response = await client.post('/api/workflows/'+saved['id']+'/runs',json={'parameters':{}})
        assert response.status_code == 202, response.text
        await app.state.workflows.task
        state = (await client.get('/api/session')).json()
        assert state['run']['status'] == 'success', state['run']
    assert session.authenticated == {}
    assert calls == []


@pytest.fixture
def login_post_redirect_server():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    outside_posts = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<form action="/forward" method="post"><input id="username" name="username"><input id="password" name="password" type="password"><button id="login">Login</button></form>')
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            if self.path == '/forward':
                self.send_response(307)
                self.send_header('Location', f'http://localhost:{self.server.server_port}/outside')
                self.end_headers()
            else:
                outside_posts.append(bool(body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'<div id="ready">Ready</div>')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', outside_posts
    server.shutdown(); thread.join(); server.server_close()


async def test_login_post_redirect_never_sends_credentials_to_foreign_origin(session, login_post_redirect_server):
    from test_explicit_login import credentials
    from app.contracts import LocatorSpec
    base, outside_posts = login_post_redirect_server
    credentials(session.settings, base)
    spec = login_spec(base+'/login')
    spec.success = LocatorSpec(kind='css',value='#ready')
    previous_scope = session.flow_scope
    result = await LoginController(session).execute(spec, {}, base)
    assert result.status == 'failed'
    assert outside_posts == []
    assert session.flow_scope == previous_scope
