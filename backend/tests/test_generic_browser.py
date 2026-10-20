"""Generic browser commands use real Playwright with deterministic model responses."""
import json
import httpx
import pytest
from app.main import create_app
from app.contracts import BrowserAction
from app.agent import StepAgent

async def test_start_opens_blank_without_login_and_repeated_start_preserves_page(session):
    await session.close()
    app = create_app(session.settings, session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        response=await c.post('/api/session/start')
        assert response.status_code==200
        assert response.json()['session']['current_url']=='about:blank'
        assert response.json()['session']['status']=='ready'
        observation=await session.observe(generic=True)
        assert observation.elements==[]
        await session.page.goto(session.settings.site_url)
        await c.post('/api/session/start')
        assert session.page.url.endswith('/demo/login')

async def test_agent_can_navigate_fill_submit_and_follow_other_origin(session):
    await session.page.goto('about:blank')
    # Different origin from configured site; no external network needed.
    async def route(route):
        if route.request.url.startswith('https://search.test'):
            await route.fulfill(content_type='text/html; charset=utf-8',body='<form action="https://results.test/find"><input id="kw" name="q" type="search"><button>搜索</button></form>')
        else:
            await route.fulfill(content_type='text/html; charset=utf-8',body='<h1>搜索结果：今日黄金价格</h1>')
    await session.context.route('https://search.test/**',route)
    await session.context.route('https://results.test/**',route)
    actions=[{'kind':'navigate','value':'https://search.test/'},
             {'kind':'fill','target':{'kind':'css','value':'#kw'},'value':'今日黄金价格'},
             {'kind':'press','target':{'kind':'css','value':'#kw'},'value':'Enter'}]
    payloads=[]
    def handler(request):
        payloads.append(json.loads(request.content))
        if actions:
            message={'role':'assistant','tool_calls':[{'id':str(len(actions)),'type':'function','function':{'name':'browser_action','arguments':json.dumps(actions.pop(0))}}]}
        else: message={'role':'assistant','content':'已完成搜索'}
        return httpx.Response(200,json={'choices':[{'message':message}]})
    session.settings.llm_base_url='http://model.test/v1';session.settings.llm_model='test'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await StepAgent(session.settings,client).run('访问 search.test 搜索今日黄金价格',session)
    assert result.status=='success',result.message
    assert session.page.url.startswith('https://results.test/find?q=')
    assert '今日黄金价格' in (await session.observe(generic=True)).text
    observation=json.loads(payloads[1]['messages'][-1]['content'])['page_observation']
    assert any(e.get('selector')=='#kw' and e['role']=='searchbox' for e in observation['elements'])
    # Standard-flow operations must remain scoped even after a generic command.
    assert (await session.execute(BrowserAction(kind='navigate',value='https://search.test/'))).status=='failed'

@pytest.mark.parametrize('url',['file:///C:/Windows/win.ini','javascript:alert(1)','chrome://settings','data:text/html,hello','https://name:secret@example.com'])
async def test_generic_rejects_non_web_urls_and_embedded_credentials(session,url):
    before=session.page.url
    result=await session.execute(BrowserAction(kind='navigate',value=url),generic=True)
    assert result.status=='failed'
    assert session.page.url==before

async def test_generic_does_not_follow_non_web_link(session):
    await session.page.set_content('<a href="file:///C:/Windows/win.ini">local file</a>')
    result=await session.execute(BrowserAction(kind='click',target={'kind':'text','value':'local file'}),generic=True)
    assert result.status=='failed'

async def test_cross_site_action_is_not_added_to_legacy_draft(session):
    app=create_app(session.settings,session)
    repo=app.state.repository
    before=repo.draft()['revision']
    with pytest.raises(ValueError):
        repo.record(BrowserAction(kind='navigate',value='https://search.test/'),'test','visit',session.page.url)
    assert repo.draft()['revision']==before

async def test_cross_site_link_is_not_silently_recorded_as_legacy_flow(session):
    await session.page.set_content('<a href="https://other.test/">Other site</a>')
    await session.context.route('https://other.test/**',lambda r:r.fulfill(body='<h1>Other site</h1>',content_type='text/html'))
    app=create_app(session.settings,session)
    session.settings.llm_base_url='http://model.test/v1';session.settings.llm_model='test'
    messages=[{'role':'assistant','tool_calls':[{'id':'click','type':'function','function':{'name':'browser_action','arguments':json.dumps({'kind':'click','target':{'kind':'role','role':'link','value':'Other site'}})}}]}, {'role':'assistant','content':'Opened'}]
    def handler(request):
        return httpx.Response(200,json={'choices':[{'message':messages.pop(0)}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        app.state.runner.agent=StepAgent(session.settings,client)
        await app.state.runner.submit('Open other site')
        await app.state.runner.task
    assert session.page.url.startswith('https://other.test')
    assert app.state.repository.draft()['steps']==[]
    assert app.state.repository.draft()['warnings']
