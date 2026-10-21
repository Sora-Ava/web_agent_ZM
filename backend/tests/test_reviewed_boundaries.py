"""Regression tests for reviewed session reuse and flow navigation boundaries."""
import pytest
from app.contracts import BrowserAction
from app.login import LoginController
from app.workflows.models import WorkflowDefinition,site_key
from app.workflows.executor import WorkflowExecutor
from test_explicit_login import login_spec

async def test_known_identity_survives_login_redirect_from_business_page(session):
    url=session.settings.site_url;base=url.split('/demo')[0]
    await session.page.goto(base+'/demo/business')
    await session.page.locator('#logged-in').evaluate('(el)=>el.remove()')
    await session.context.route(url,lambda r:r.fulfill(status=302,headers={'location':base+'/demo/home'}))
    posts=[];session.page.on('request',lambda r:posts.append(r) if r.url.endswith('/demo/auth') else None)
    result=await LoginController(session).execute(login_spec(url),{},url)
    assert result.status=='success',result.message
    assert session.authenticated[site_key(url)]=='test'
    assert posts==[]

async def test_redirected_success_does_not_trust_unknown_identity(session):
    url=session.settings.site_url;base=url.split('/demo')[0]
    await session.page.goto(base+'/demo/business')
    await session.page.locator('#logged-in').evaluate('(el)=>el.remove()')
    session.authenticated.clear()
    await session.context.route(url,lambda r:r.fulfill(status=302,headers={'location':base+'/demo/home'}))
    result=await LoginController(session).execute(login_spec(url),{},url)
    assert result.status=='failed'
    assert session.authenticated=={}

@pytest.fixture
def redirect_server():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path in ('/redirect', '/same-origin'):
                self.send_response(302)
                self.send_header('Location', '/start' if self.path == '/same-origin' else f'http://localhost:{self.server.server_port}/target')
                self.end_headers()
            else:
                if self.path == '/target': hits.append(self.path)
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(b'<a id="leave" href="/redirect">Leave</a>')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', hits
    server.shutdown(); thread.join(); server.server_close()

async def test_standard_navigation_blocks_cross_origin_redirect(session, redirect_server):
    base, foreign = redirect_server
    session.flow_scope = base
    result = await session.execute(BrowserAction(kind='navigate', value=base+'/redirect'))
    assert result.status == 'failed'
    assert foreign == []

async def test_last_navigation_cannot_make_workflow_success_after_leaving_origin(session, redirect_server):
    base, foreign = redirect_server
    definition = WorkflowDefinition(name='redirect', site_key=site_key(base), entry_url=base+'/start', steps=[{'id':'last','name':'最后导航','action':{'kind':'navigate','value':base+'/redirect'}}])
    result = await WorkflowExecutor(session).execute(definition,{})
    assert result.status == 'failed'
    assert foreign == []

async def test_workflow_entry_redirect_is_blocked_before_steps(session, redirect_server):
    base, foreign = redirect_server
    definition = WorkflowDefinition(name='entry', site_key=site_key(base), entry_url=base+'/redirect', steps=[{'id':'never','name':'禁止执行','action':{'kind':'navigate','value':base+'/start'}}])
    result = await WorkflowExecutor(session).execute(definition,{})
    assert result.status == 'failed' and result.steps == []
    assert foreign == []

async def test_generic_navigation_still_allows_cross_origin_redirect(session, redirect_server):
    base, foreign = redirect_server
    result = await session.execute(BrowserAction(kind='navigate', value=base+'/redirect'), generic=True)
    assert result.status == 'success'
    assert len(foreign) == 1

async def test_click_redirect_is_also_blocked(session, redirect_server):
    base, foreign = redirect_server
    session.flow_scope = base
    await session.page.goto(base+'/start')
    result = await session.execute(BrowserAction(kind='click', target={'kind':'css','value':'#leave'}))
    assert result.status == 'failed'
    assert foreign == []

async def test_same_origin_redirect_still_succeeds(session, redirect_server):
    base, foreign = redirect_server
    session.flow_scope = base
    result = await session.execute(BrowserAction(kind='navigate', value=base+'/same-origin'))
    assert result.status == 'success'
    assert session.page.url == base+'/start'
    assert foreign == []

async def test_final_origin_is_checked_even_without_interception(session, redirect_server, monkeypatch):
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def bypass():
        yield {'value': False}
    monkeypatch.setattr(session, 'navigation_scope', bypass)
    base, foreign = redirect_server
    session.flow_scope = base
    result = await session.execute(BrowserAction(kind='navigate', value=base+'/redirect'))
    assert len(foreign) == 1
    assert result.status == 'failed'

async def test_scope_failure_cannot_be_overridden_by_download_evidence(session, monkeypatch):
    from app.contracts import ActionResult
    from app.recovery.runtime import RecoveryRuntime
    from app.recovery import runtime as runtime_module
    async def rejected(action):
        return ActionResult(status='failed', scope_violation=True, effect_started=True, message='scope')
    async def evidence(*args):
        pytest.fail('Boundary violations must stop before evidence or recovery')
    monkeypatch.setattr(session, 'execute', rejected)
    monkeypatch.setattr(runtime_module, 'completed', evidence)
    runtime = RecoveryRuntime(session, None, {}, None, None, None)
    action = BrowserAction(kind='download', target={'kind':'css','value':'#export'})
    result = await runtime.execute(action, None, 0, 1)
    assert result.status == 'failed' and result.scope_violation
    assert result.effect_started
