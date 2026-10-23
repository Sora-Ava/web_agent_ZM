import pytest
from app.contracts import LocatorSpec
from app.credential_intents import parse_credential_intent
from app.credential_actions import authorize_credentials, execute_credential_fill

async def prepare(session):
    await session.page.goto(session.settings.site_url)
    intent = parse_credential_intent('账号 "synthetic-user" 密码 "synthetic-secret"')
    observation = await session.observe(generic=True)
    authorize_credentials(session, intent, observation)
    class Store:
        def secrets(self): return []
        def __init__(self): self.calls=[]
        def save_field(self, ref, scope, field, value):
            self.calls.append((ref, scope, field, value)); return 'saved-' + str(len(self.calls))
    store=Store(); session.settings.__dict__['credentials']=store
    return intent, store

async def test_password_metadata_and_label_locator(session):
    intent, store=await prepare(session)
    observation=await session.observe(generic=True)
    password=next(e for e in observation.elements if e.get('input_type')=='password')
    assert password['role'] is None
    assert 'value' not in password
    assert password['selector']=='#password'
    result=await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))
    assert result.status=='success'
    assert await session.page.locator('#password').input_value()=='synthetic-secret'
    assert store.calls[0][3]=='synthetic-secret'
    assert result.credential_record['credential_ref']=='saved-1'
    assert 'credential_record' not in result.model_dump()

@pytest.mark.parametrize('selector', ['#missing', '#username', 'input'])
async def test_invalid_password_targets_do_not_write(session, selector):
    intent, store=await prepare(session)
    result=await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value=selector))
    assert result.status!='success'
    assert not store.calls

async def test_stale_node_and_manual_revision_reject(session):
    intent, store=await prepare(session)
    await session.page.locator('#password').evaluate('(el)=>el.replaceWith(el.cloneNode())')
    result=await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))
    assert result.status!='success'
    session.control_revision+=1
    result=await execute_credential_fill(session,intent,'username',LocatorSpec(kind='css',value='#username'))
    assert result.status!='success'
    assert not store.calls

async def test_save_failure_never_records_or_retries(session):
    intent, store=await prepare(session)
    def fail(*args): raise ValueError('synthetic-secret')
    store.save_field=fail
    result=await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))
    assert result.status=='failed' and result.effect_started
    assert '未保存' in result.message and result.credential_record is None
    assert await session.page.locator('#password').input_value()=='synthetic-secret'

async def test_label_fill_association_and_returned_ref(session):
    intent, store=await prepare(session)
    first=await execute_credential_fill(session,intent,'username',LocatorSpec(kind='label',value='账号'))
    assert first.status=='success'
    second=await execute_credential_fill(session,intent,'password',LocatorSpec(kind='label',value='密码'))
    assert second.status=='success'
    assert store.calls[1][0]=='saved-1'
    assert session.credential_binding['credential_ref']=='saved-2'

async def test_origin_and_hidden_and_multiple_password_reject(session):
    intent, store=await prepare(session)
    await session.page.locator('#password').evaluate('(el)=>el.style.display="none"')
    assert (await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))).status!='success'
    assert not any(e.get('input_type')=='password' for e in (await session.observe(generic=True)).elements)
    await session.page.locator('#password').evaluate('(el)=>{el.style.display="";el.after(el.cloneNode())}')
    assert (await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))).status!='success'
    await session.page.goto('about:blank')
    assert (await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))).status!='success'
    assert not store.calls

async def test_agent_substitutes_private_value_and_public_events_safe(session):
    import httpx, json
    from app.agent import StepAgent
    from test_agent import tool
    intent, store=await prepare(session)
    session.settings.llm_base_url='http://model.test/v1'; session.settings.llm_model='synthetic'
    requests=[]; events=[]
    def handler(request):
        requests.append(json.loads(request.content))
        message=tool('fill',{'kind':'css','value':'#password'},value='attacker-replacement') if len(requests)==1 else {'role':'assistant','content':'完成'}
        return httpx.Response(200,json={'choices':[{'message':message}]})
    async def emit(event): events.append(event)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await StepAgent(session.settings,client).run(intent.display_instruction,session,emit=emit,credential_intent=intent)
    assert result.status=='success'
    assert await session.page.locator('#password').input_value()=='synthetic-secret'
    assert 'synthetic-secret' not in json.dumps([requests,events,result.model_dump()])
    assert 'attacker-replacement' not in json.dumps(requests[1])

async def test_submit_requires_explicit_intent_and_same_form(session):
    from app.credential_actions import execute_credential_submit
    intent, store=await prepare(session)
    assert (await execute_credential_submit(session,intent,LocatorSpec(kind='css',value='#login'))).status!='success'
    intent=parse_credential_intent('密码 "synthetic-secret" 点击登录')
    authorize_credentials(session,intent,await session.observe(generic=True))
    assert (await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))).status=='success'
    await session.page.evaluate('()=>{const f=document.createElement("form");f.innerHTML="<button id=other>登录</button>";document.body.append(f)}')
    assert (await execute_credential_submit(session,intent,LocatorSpec(kind='css',value='#other'))).status!='success'

async def test_separate_commands_share_only_exact_draft_origin_form(session):
    intent, store=await prepare(session)
    first=await execute_credential_fill(session,intent,'username',LocatorSpec(kind='css',value='#username'))
    intent=parse_credential_intent('密码 "synthetic-secret"')
    authorize_credentials(session,intent,await session.observe(generic=True))
    second=await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))
    assert store.calls[-1][0]==first.credential_record['credential_ref']
    session.credential_draft_id='other-draft'
    authorize_credentials(session,intent,await session.observe(generic=True))
    await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))
    assert store.calls[-1][0] is None

@pytest.mark.parametrize('mode',['action','redirect'])
async def test_submit_blocks_cross_origin_action_and_redirect(session, mode):
    from app.credential_actions import execute_credential_submit
    intent, store=await prepare(session)
    intent=parse_credential_intent('密码 "synthetic-secret" 点击登录')
    authorize_credentials(session,intent,await session.observe(generic=True))
    await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))
    if mode=='action':
        await session.page.locator('#login').evaluate('(el)=>el.form.action="https://outside.test/leak"')
    else:
        await session.page.locator('#login').evaluate('(el)=>{el.form.removeAttribute("onsubmit");el.form.action="/synthetic-redirect";el.form.method="post"}')
        await session.page.route('**/synthetic-redirect',lambda route:route.fulfill(status=302,headers={'location':'https://outside.test/leak'}))
    escaped=[]
    session.page.on('request',lambda req:escaped.append(req.url) if req.url.startswith('https://outside.test') else None)
    result=await execute_credential_submit(session,intent,LocatorSpec(kind='css',value='#login'))
    assert result.status!='success'
    assert not escaped

async def test_ordinary_fill_cannot_write_password(session):
    from app.contracts import BrowserAction
    await session.page.goto(session.settings.site_url)
    result=await session.execute(BrowserAction(kind='fill',target=LocatorSpec(kind='css',value='#password'),value='ordinary'),generic=True)
    assert result.status=='failed'
    assert await session.page.locator('#password').input_value()==''

async def test_default_https_port_origin_normalization(session):
    await session.page.route('https://credential.test/**',lambda route:route.fulfill(content_type='text/html',body='<form><label>账号<input id="username"></label><label>密码<input id="password" type="password"></label><button>登录</button></form>'))
    intent, store=await prepare(session)
    await session.page.goto('https://credential.test/')
    authorize_credentials(session,intent,await session.observe(generic=True))
    result=await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))
    assert result.status=='success'
    assert result.credential_record['origin']=='https://credential.test:443'

async def test_successful_submit_emits_internal_metadata_and_cannot_repeat(session):
    from app.credential_actions import execute_credential_submit
    intent, store=await prepare(session)
    intent=parse_credential_intent('账号 "synthetic-user" 密码 "synthetic-secret" 点击登录')
    authorize_credentials(session,intent,await session.observe(generic=True))
    await execute_credential_fill(session,intent,'username',LocatorSpec(kind='css',value='#username'))
    await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))
    result=await execute_credential_submit(session,intent,LocatorSpec(kind='css',value='#login'))
    assert result.status=='success' and '待确认' in result.message
    assert result.credential_record['field']=='submit'
    assert result.credential_record['credential_ref']=='saved-2'
    assert 'credential_record' not in result.model_dump()
    assert (await execute_credential_submit(session,intent,LocatorSpec(kind='css',value='#login'))).status!='success'

async def test_runner_strips_internal_records_from_public_bus(session):
    import httpx
    from app.events import CommandRunner, EventBus
    from test_agent import tool
    intent, store=await prepare(session)
    bus=EventBus(); runner=CommandRunner(session,bus)
    session.settings.llm_base_url='http://model.test/v1'; session.settings.llm_model='synthetic'
    calls=[]
    def handler(request):
        calls.append(request)
        message=tool('fill',{'kind':'css','value':'#password'},value='model-value') if len(calls)==1 else {'role':'assistant','content':'完成'}
        return httpx.Response(200,json={'choices':[{'message':message}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runner.agent.client=client
        await runner.submit(intent.display_instruction,credential_intent=intent)
        await runner.task
    assert any(e['type']=='step_result' for e in bus.history)
    assert all('credential_record' not in e for e in bus.history)
    assert 'synthetic-secret' not in str(list(bus.history))

async def test_origin_is_bound_before_first_model_roundtrip(session):
    import httpx
    from app.agent import StepAgent
    from test_agent import tool
    intent, store=await prepare(session)
    await session.page.route('https://other-origin.test/**',lambda route:route.fulfill(content_type='text/html',body='<form><input id="password" type="password"></form>'))
    session.settings.llm_base_url='http://model.test/v1';session.settings.llm_model='synthetic'
    async def handler(request):
        await session.page.goto('https://other-origin.test/')
        return httpx.Response(200,json={'choices':[{'message':tool('fill',{'kind':'css','value':'#password'},value='[REDACTED]')}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await StepAgent(session.settings,client).run(intent.display_instruction,session,credential_intent=intent)
    assert result.status!='success'
    assert await session.page.locator('#password').input_value()==''
    assert not store.calls

async def test_same_origin_different_form_cannot_share_reference(session):
    intent, store=await prepare(session)
    await execute_credential_fill(session,intent,'username',LocatorSpec(kind='css',value='#username'))
    await session.page.evaluate('()=>{let f=document.createElement("form");f.innerHTML="<input id=username2 autocomplete=username><input id=password2 type=password>";document.body.append(f)}')
    authorize_credentials(session,intent,await session.observe(generic=True))
    result=await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password2'))
    assert result.status=='success'
    assert store.calls[-1][0] is None

async def test_real_dpapi_fill_persists_synthetic_pair_without_plaintext(session):
    intent, store=await prepare(session)
    session.settings.__dict__.pop('credentials')
    first=await execute_credential_fill(session,intent,'username',LocatorSpec(kind='css',value='#username'))
    second=await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))
    assert first.status==second.status=='success'
    ref=second.credential_record['credential_ref']
    resolved=session.settings.credentials.resolve(ref,session.page.url)
    assert resolved.username.get_secret_value()=='synthetic-user'
    assert resolved.password.get_secret_value()=='synthetic-secret'
    data=session.settings.database_path.read_bytes()
    assert b'synthetic-user' not in data and b'synthetic-secret' not in data

async def test_manual_return_invalidates_private_authorization_and_binding(session):
    intent, store=await prepare(session)
    await execute_credential_fill(session,intent,'username',LocatorSpec(kind='css',value='#username'))
    await session.request_takeover()
    await session.return_control()
    assert session.credential_authorization is None
    assert session.credential_binding is None
    assert (await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))).status!='success'

async def test_observed_input_cannot_move_to_other_observed_form(session):
    intent, store=await prepare(session)
    await session.page.evaluate('()=>{const f=document.createElement("form");f.id="other-form";f.innerHTML="<input id=other-username autocomplete=username>";document.body.append(f)}')
    authorize_credentials(session,intent,await session.observe(generic=True))
    await session.page.locator('#password').evaluate('(el)=>document.querySelector("#other-form").append(el)')
    result=await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))
    assert result.status!='success'
    assert await session.page.locator('#password').input_value()==''
    assert store.calls==[]
    assert result.credential_record is None

async def test_observed_submit_cannot_move_into_authorized_form(session):
    from app.credential_actions import execute_credential_submit
    intent, store=await prepare(session)
    await session.page.evaluate('()=>{const f=document.createElement("form");f.innerHTML="<button id=other-login type=button>登录</button>";document.body.append(f)}')
    intent=parse_credential_intent('密码 "synthetic-secret" 点击登录')
    authorize_credentials(session,intent,await session.observe(generic=True))
    await execute_credential_fill(session,intent,'password',LocatorSpec(kind='css',value='#password'))
    await session.page.locator('#other-login').evaluate('(el)=>document.querySelector("#password").form.append(el)')
    result=await execute_credential_submit(session,intent,LocatorSpec(kind='css',value='#other-login'))
    assert result.status!='success'
    assert result.credential_record is None
