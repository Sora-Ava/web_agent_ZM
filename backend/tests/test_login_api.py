import json
import httpx
from app.main import create_app
from app.workflows.models import site_key
from test_explicit_login import login_spec,credentials

async def test_login_api_records_parameterized_flow_without_secrets(session):
    credentials(session.settings,session.settings.site_url)
    await session.close()
    app=create_app(session.settings,session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        metadata=await c.get('/api/credentials')
        assert metadata.status_code==200
        assert 'demo-password' not in metadata.text
        spec=login_spec(session.settings.site_url)
        response=await c.post('/api/login',json={'login':spec.model_dump(),'parameters':{}})
        assert response.status_code==200,response.text
        assert response.json()['result']['status']=='success'
        proposal=(await c.post('/api/recording/proposal',json={'use_model':False})).json()
        d=proposal['definition'];assert d['steps'][0]['login']
        assert {p['kind'] for p in d['parameters']}=={'url','credential'}
        saved=await c.post('/api/workflows',json={'definition':d,'confirmed':True})
        assert saved.status_code==201,saved.text
        response=await c.post('/api/tasks',json={'flow_id':saved.json()['id'],'rows':[{}]})
        assert response.status_code==202,response.text
        identity=response.json()['id']
        await c.post('/api/queue/release-authoring');await app.state.tasks.dispatcher
        task=(await c.get('/api/tasks/'+identity)).json()
        assert task['status']=='completed'
        assert task['rows'][0]['status']=='success'
        assert 'demo-password' not in json.dumps(task)
    await app.state.tasks.shutdown()

async def test_other_site_workflow_is_not_tied_to_settings(session):
    app=create_app(session.settings,session)
    from app.workflows.models import WorkflowDefinition
    d=WorkflowDefinition(name='other',site_key=site_key('https://other.test'),entry_url='https://other.test/',steps=[{'id':'w','name':'Wait','action':{'kind':'wait','target':{'kind':'text','value':'Other'}}}])
    saved=app.state.repository.create(d,True)
    assert saved['definition']['site_key']==site_key('https://other.test')
    await session.context.route('https://other.test/**',lambda r:r.fulfill(body='<h1>Other</h1>',content_type='text/html'))
    from app.workflows.executor import WorkflowExecutor
    assert (await WorkflowExecutor(session).execute(d,{})).status=='success'

async def test_account_substrings_cannot_enter_recordings(session):
    credentials(session.settings,session.settings.site_url)
    app=create_app(session.settings,session)
    from app.contracts import BrowserAction
    import pytest
    with pytest.raises(ValueError):
        app.state.repository.record(BrowserAction(kind='fill',value='prefix-operator',target={'kind':'css','value':'#batch'}),'test','查询 operator 的数据',session.page.url)

async def test_escaped_account_values_cannot_enter_recordings(session):
    from pydantic import SecretStr
    session.settings.credentials_json=SecretStr(json.dumps({'private':{'origin':session.settings.site_url,'username':'synthetic"account\\value','password':'separate-password'}}))
    session.settings.__dict__.pop('credentials',None)
    app=create_app(session.settings,session)
    from app.contracts import BrowserAction
    import pytest
    encoded='synthetic"account\\value'
    for _ in range(2): encoded=json.dumps(encoded)
    with pytest.raises(ValueError):
        app.state.repository.record(BrowserAction(kind='fill',value=encoded,target={'kind':'css','value':'#batch'}),'test','record',session.page.url)
