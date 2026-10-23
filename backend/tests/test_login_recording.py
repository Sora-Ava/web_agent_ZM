import pytest

from app.contracts import BrowserAction, LocatorSpec
from app.workflows.repository import WorkflowRepository


def record(repo, field, ref='local-ref', form='doc:form'):
    data = dict(field=field, target={'kind':'css', 'value':'#'+field},
                origin='https://example.test:443', form_id=form,
                draft_id=repo.draft()['id'], credential_ref=ref)
    repo.login_recording.record(data, 'command', 'https://example.test/login')
    return data


def test_pending_is_not_runnable_and_credentials_not_parameters(tmp_path):
    repo = WorkflowRepository(tmp_path/'db')
    record(repo, 'username')
    record(repo, 'password')
    record(repo, 'submit')
    result = repo.proposal()
    assert result['definition'] is None
    assert result['login_confirmation']['attempts'][0]['status'] == 'awaiting_confirmation'


def test_complete_fold_keeps_position_and_business_actions_across_restart(tmp_path):
    from app.workflows.login_recording import build_login_proposal
    repo = WorkflowRepository(tmp_path/'db')
    record(repo, 'username')
    repo.record(BrowserAction(kind='fill', target={'kind':'css','value':'#batch'}, value='PC001'), 'business', '填写批次', 'https://example.test/login')
    record(repo, 'password')
    record(repo, 'submit')
    attempt = repo.login_recording.attempts()[0]
    spec = build_login_proposal(attempt['records'], LocatorSpec(kind='css', value='#home'))
    assert spec.credential_ref == 'local-ref'
    repo.login_recording.complete(attempt['id'], spec, repo.draft()['revision'])
    reopened = WorkflowRepository(tmp_path/'db')
    proposal = reopened.proposal()
    assert proposal['definition']['steps'][0]['login']['success']['value'] == '#home'
    assert proposal['definition']['steps'][1]['action']['value'] == 'PC001'
    assert [p['kind'] for p in proposal['definition']['parameters']] == ['url','credential','text']
    assert len(proposal['definition']['steps']) == 2


def test_incomplete_multiform_reset_and_no_marker_never_compile(tmp_path):
    from app.workflows.login_recording import build_login_proposal
    repo = WorkflowRepository(tmp_path/'db')
    old = record(repo, 'username')
    record(repo, 'password', ref='other', form='doc:other')
    record(repo, 'submit', ref='other', form='doc:other')
    for attempt in repo.login_recording.attempts():
        assert build_login_proposal(attempt['records'], LocatorSpec(kind='css',value='#home')) is None
        assert build_login_proposal(attempt['records'], None) is None
    repo.reset_draft()
    with pytest.raises(ValueError):
        repo.login_recording.record(old, 'stale', 'https://example.test/login')
    assert not repo.draft()['steps']


async def prepared_attempt(session, tmp_path):
    from app.workflows.models import site_key
    repo = WorkflowRepository(tmp_path/'recording.db')
    await session.page.goto(session.settings.site_url)
    observation = await session.observe(generic=True)
    form = next(e['form_id'] for e in observation.elements if e.get('input_type') == 'password')
    for field in ('username','password','submit'):
        repo.login_recording.record(dict(field=field, target={'kind':'css','value':'#'+field},
            origin=site_key(session.page.url), form_id=form, draft_id=repo.draft()['id'], credential_ref='safe-ref'), 'command', session.page.url)
    return repo


async def test_confirmation_rejects_login_form_hidden_duplicate_and_cross_origin(session, tmp_path):
    repo = await prepared_attempt(session, tmp_path)
    tracker = repo.login_recording
    attempt = tracker.attempts()[0]
    target = LocatorSpec(kind='css', value='#home')
    await session.page.evaluate("document.body.insertAdjacentHTML('beforeend','<nav id=home>业务首页</nav>')")
    with pytest.raises(ValueError, match='登录表单'):
        await tracker.confirm(session, attempt['id'], target, repo.draft()['revision'])
    await session.page.set_content('<nav id=home hidden>业务首页</nav>')
    with pytest.raises(ValueError, match='唯一且可见'):
        await tracker.confirm(session, attempt['id'], target, repo.draft()['revision'])
    await session.page.set_content('<nav id=home>首页</nav><nav id=home>首页</nav>')
    with pytest.raises(ValueError, match='唯一且可见'):
        await tracker.confirm(session, attempt['id'], target, repo.draft()['revision'])
    await session.page.goto('about:blank')
    with pytest.raises(ValueError):
        await tracker.confirm(session, attempt['id'], target, repo.draft()['revision'])
    assert repo.proposal()['definition'] is None


async def test_live_candidates_and_confirm_survive_restart(session, tmp_path):
    repo = await prepared_attempt(session, tmp_path)
    await session.page.set_content('<a href="/home">业务首页</a>')
    meta = await repo.login_recording.candidates(session)
    candidate = meta['attempts'][0]['candidates'][0]
    await repo.login_recording.confirm(session, meta['attempts'][0]['id'], LocatorSpec.model_validate(candidate['target']), repo.draft()['revision'])
    assert WorkflowRepository(repo.path).proposal()['definition']['schema_version'] == 2


@pytest.mark.parametrize('combined', [False, True])
@pytest.mark.parametrize('submission', ['delayed_navigation', 'spa'])
async def test_runner_records_private_fills_and_preclick_submit_pending_summary(session, tmp_path, combined, submission):
    from app.events import CommandRunner, EventBus
    from app.agent import StepAgent
    from app.credential_intents import parse_credential_intent
    import httpx, json
    await session.page.goto(session.settings.site_url)
    posts = []
    session.page.on('request', lambda request: posts.append(1) if request.url.endswith('/demo/auth') else None)
    if submission == 'delayed_navigation':
        await session.page.locator('form').evaluate("el=>el.onsubmit=e=>{e.preventDefault();setTimeout(()=>handleLogin({preventDefault(){}}),150)}")
    else:
        session.settings.action_timeout_ms = 300
        await session.page.locator('form').evaluate("el=>el.onsubmit=e=>{e.preventDefault();window.submitCount=(window.submitCount||0)+1}")
    class Store:
        def secrets(self): return []
        def save_field(self, ref, *args): return ref or 'safe-ref'
    session.settings.__dict__['credentials'] = Store()
    session.settings.llm_base_url = 'http://model.test/v1'
    session.settings.llm_model = 'test'
    responses = []
    def model(request):
        return httpx.Response(200, json={'choices':[{'message':responses.pop(0)}]})
    def call(kind, selector, value=None):
        return {'role':'assistant','tool_calls':[{'id':'t','type':'function','function':{'name':'browser_action','arguments':json.dumps({'kind':kind,'target':{'kind':'css','value':selector},'value':value})}}]}
    repo = WorkflowRepository(tmp_path/'runner.db')
    bus = EventBus()
    runner = CommandRunner(session, bus, repo)
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
        runner.agent = StepAgent(session.settings, client)
        if combined:
            instruction = '账号 "operator" 密码 "demo-password"，填写后点击登录'
            responses[:] = [call('fill','#username','model-value'), call('fill','#password','model-value'), call('click','#login'), {'content':'登录成功'}]
            await runner.submit(instruction)
            await runner.task
        else:
            for instruction, field in [('账号 "operator"','username'), ('密码 "demo-password"','password')]:
                responses[:] = [call('fill','#'+field,'model-value'), {'content':'已填写'}]
                await runner.submit(instruction, parse_credential_intent(instruction))
                await runner.task
            responses[:] = [call('click','#login'), {'content':'登录成功'}]
            await runner.submit('点击登录')
            await runner.task
    proposal = repo.proposal()
    assert proposal['definition'] is None
    assert proposal['login_confirmation']['attempts'][-1]['status'] == 'awaiting_confirmation'
    assert runner.commands[-1]['result']['message'] == '登录提交已执行，结果待确认'
    assert all('credential_record' not in event and 'record_action' not in event for event in bus.history)
    assert session.credential_binding is None
    if submission == 'delayed_navigation':
        assert session.page.url.endswith('/demo/home') and posts == [1]
    else:
        assert await session.page.evaluate('window.submitCount') == 1


async def test_confirmation_api_revision_manual_control_and_regular_proposal(session, tmp_path):
    import httpx
    from app.main import create_app
    repo = await prepared_attempt(session, tmp_path)
    app = create_app(session.settings, session=session)
    app.state.repository = repo
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        pending = (await client.post('/api/recording/proposal', json={'use_model':True})).json()
        assert pending['definition'] is None and pending['model_calls'] == 0
        assert '仍然可见' in pending['login_confirmation']['message']
        await session.page.set_content('<a href="/home">业务首页</a>')
        pending = (await client.post('/api/recording/proposal', json={'use_model':False})).json()
        attempt = pending['login_confirmation']['attempts'][0]
        body = dict(attempt_id=attempt['id'], revision=pending['revision'], target=attempt['candidates'][0]['target'], confirmed=True)
        assert (await client.post('/api/recording/confirm-login', json={**body,'confirmed':False})).status_code == 422
        await session.request_takeover()
        assert (await client.post('/api/recording/confirm-login', json=body)).status_code == 409
        await session.return_control()
        assert (await client.post('/api/recording/confirm-login', json={**body,'revision':'stale'})).status_code == 409
        response = await client.post('/api/recording/confirm-login', json=body)
        assert response.status_code == 200
        assert response.json()['definition']['steps'][0]['login']['credential_parameter']


def test_retry_repeated_field_starts_fresh_pair_without_resurrecting_binding(tmp_path):
    from types import SimpleNamespace
    repo = WorkflowRepository(tmp_path/'db')
    first = record(repo, 'username')
    key = (first['draft_id'], first['origin'], first['form_id'])
    session = SimpleNamespace(credential_binding={'key': key, 'credential_ref':'local-ref'})
    repo.login_recording.prepare_field(session, 'password', key)
    assert session.credential_binding
    repo.login_recording.prepare_field(session, 'username', key)
    assert session.credential_binding is None
    record(repo, 'username', ref='fresh-ref')
    assert [a['status'] for a in repo.login_recording.attempts()] == ['superseded','incomplete']
    repo.login_recording.prepare_field(session, 'password', key)
    assert session.credential_binding is None


async def test_business_button_inside_login_form_is_not_submit(session, tmp_path):
    repo = await prepared_attempt(session, tmp_path)
    attempt = repo.login_recording.attempts()[0]
    session.credential_binding = {'key': attempt['key'][:3], 'credential_ref': attempt['key'][3]}
    await session.page.locator('form').evaluate("el=>el.insertAdjacentHTML('beforeend','<button type=button id=help>帮助</button>')")
    action = BrowserAction(kind='click',target={'kind':'css','value':'#help'})
    assert await repo.login_recording.before_click(session, action) is None


def test_navigation_interrupts_incomplete_pair(tmp_path):
    repo = WorkflowRepository(tmp_path/'db')
    record(repo, 'username')
    repo.record(BrowserAction(kind='navigate',value='https://example.test/other'), 'nav', '访问页面', 'https://example.test/login')
    record(repo, 'password')
    record(repo, 'submit')
    assert repo.login_recording.attempts()[0]['status'] == 'incomplete'
    record(repo, 'username', ref='fresh-ref')
    assert [a['status'] for a in repo.login_recording.attempts()] == ['superseded','incomplete']
    record(repo, 'password', ref='fresh-ref')
    record(repo, 'submit', ref='fresh-ref')
    from app.workflows.login_recording import build_login_proposal
    attempt = repo.login_recording.attempts()[-1]
    spec = build_login_proposal(attempt['records'], LocatorSpec(kind='css',value='#home'))
    repo.login_recording.complete(attempt['id'], spec, repo.draft()['revision'])
    result = repo.proposal()
    assert result['definition']['steps'][0]['action']['kind'] == 'navigate'
    assert result['definition']['steps'][1]['login']
    assert result['warnings']


async def test_real_dpapi_recording_compiles_and_resolves_after_restart(session, tmp_path):
    from app.credential_actions import authorize_credentials, execute_credential_fill, execute_credential_submit
    from app.credential_intents import parse_credential_intent
    from app.workflows.models import site_key
    await session.page.goto(session.settings.site_url)
    repo = WorkflowRepository(tmp_path/'durable.db', credentials=session.settings.credentials, secrets=session.settings.credentials.secrets)
    session.login_recording = repo.login_recording
    session.credential_draft_id = repo.draft()['id']
    intent = parse_credential_intent('账号 "operator" 密码 "demo-password"，填写后点击登录')
    session.settings.register_secrets(intent.secret_values())
    authorize_credentials(session, intent, await session.observe(generic=True))
    url = session.page.url
    for field in ('username','password'):
        result = await execute_credential_fill(session, intent, field, LocatorSpec(kind='css',value='#'+field))
        assert result.status == 'success', result.message
        repo.login_recording.record(result.credential_record, 'synthetic', url)
    result = await execute_credential_submit(session, intent, LocatorSpec(kind='css',value='#login'))
    assert result.status == 'success', result.message
    repo.login_recording.record(result.credential_record, 'synthetic', url)
    attempt = repo.login_recording.attempts()[0]
    await repo.login_recording.confirm(session, attempt['id'], LocatorSpec(kind='css',value='#logged-in'), repo.draft()['revision'])
    proposal = WorkflowRepository(repo.path).proposal()
    ref = next(p['default'] for p in proposal['definition']['parameters'] if p['kind'] == 'credential')
    credentials = session.settings.credentials.resolve(ref, site_key(url))
    assert credentials.username.get_secret_value() == 'operator'
    assert credentials.password.get_secret_value() == 'demo-password'
    assert b'operator' not in repo.path.read_bytes() and b'demo-password' not in repo.path.read_bytes()


def test_duplicate_field_records_are_not_silently_combined(tmp_path):
    from app.workflows.login_recording import build_login_proposal
    repo = WorkflowRepository(tmp_path/'db')
    for field in ('username','password','username','submit'):
        record(repo, field)
    attempt = repo.login_recording.attempts()[0]
    assert build_login_proposal(attempt['records'], LocatorSpec(kind='css',value='#home')) is None
    assert attempt['status'] == 'incomplete'


async def test_submit_summary_stays_unverified_even_if_recording_fails(session, tmp_path, monkeypatch):
    from app.events import CommandRunner, EventBus
    from app.contracts import ActionResult
    from app.workflows.models import site_key
    repo = WorkflowRepository(tmp_path/'db')
    runner = CommandRunner(session, EventBus(), repo)
    class Agent:
        async def run(self, instruction, session, emit=None):
            await emit({'type':'step_result', 'result':{'status':'success','message':'登录成功'},
                        'credential_record':{'field':'submit','origin':site_key(session.page.url)},
                        'url_before':session.page.url})
            return ActionResult(status='success',message='登录成功')
    runner.agent = Agent()
    def fail(*args): raise ValueError('recording unavailable')
    monkeypatch.setattr(repo.login_recording, 'record', fail)
    await runner.submit('点击登录')
    await runner.task
    assert runner.commands[-1]['result']['message'] == '登录提交已执行，结果待确认'
    assert next(e for e in runner.bus.history if e['type']=='step_result')['result']['message'] == '登录提交已执行，结果待确认'
