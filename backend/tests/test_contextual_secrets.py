from app.config import Settings
from app.workflows.repository import WorkflowRepository
from app.contracts import BrowserAction
import pytest


def test_short_credentials_preserve_observation_structure(tmp_path):
    c = Settings(_env_file=None, database_path=tmp_path/'db')
    c.register_secrets(['a', 'password'])
    observation = {'text':'账号 a', 'elements':[{'input_type':'password','role':None,'tag':'input','selector':'#password','node_id':'abca:1','form_id':'abca:2','name':'密码','label':'密码'}]}
    safe = c.scrub(observation)
    assert safe['elements'] == observation['elements']
    assert safe['text'] == '账号 [REDACTED]'
    assert c.scrub({'file_ids':['a'*32]}) == {'file_ids':['a'*32]}
    assert c.scrub({'file_id':'password'}) == {'file_id':'[REDACTED]'}


def test_short_credentials_allow_protocol_but_reject_business_values(tmp_path):
    repo = WorkflowRepository(tmp_path/'db', secrets=['a', 'password'])
    repo.record(BrowserAction(kind='navigate', value='http://example.test/path'), 'abc', '打开页面', 'about:blank')
    repo.record(BrowserAction(kind='click', target={'kind':'css','value':'#password'}), 'abc', '点击', 'http://example.test/path')
    for value in ['a', 'password']:
        with pytest.raises(ValueError, match='凭据'):
            repo.record(BrowserAction(kind='fill',target={'kind':'css','value':'#batch'},value=value),'abc','填写','http://example.test/path')
    with pytest.raises(ValueError, match='凭据'):
        repo._no_secrets({'target':{'kind':'css','value':'[data-user="a"]'}})


async def test_short_credentials_leave_model_protocol_usable(session):
    import json
    import httpx
    from app.agent import StepAgent
    session.settings.register_secrets(['a', 'password'])
    session.settings.llm_base_url = 'http://fixture/v1'
    session.settings.llm_model = 'a-model-password'
    await session.page.set_content('<button onclick="window.clickCount=(window.clickCount||0)+1">Search</button><p>account a</p>')
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload['model'] == 'a-model-password'
        assert payload['tools'][0]['function']['name'] == 'browser_action'
        assert 'fill' in payload['tools'][0]['function']['parameters']['properties']['kind']['enum']
        assert payload['messages'][0]['role'] == 'system'
        if len(requests) == 1:
            observation = json.loads(payload['messages'][-1]['content'].split('：',1)[1])
            assert observation['elements'][0]['name'] == 'Search'
            assert 'account a' not in observation['text']
            return httpx.Response(200,json={'choices':[{'message':{'tool_calls':[{'id':'a-call','type':'function',
                'function':{'name':'browser_action','arguments':json.dumps({'kind':'click','target':{'kind':'role','role':'button','value':'Search'}})}}]}}]})
        return httpx.Response(200, json={'choices':[{'message':{'content':'完成'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await StepAgent(session.settings, client=client).run('点击 Search', session)
    assert result.status == 'success' and len(requests) == 2
    assert await session.page.evaluate('window.clickCount') == 1


@pytest.mark.parametrize('value', ['http://example.test/a', '{"payload":"a"}', '[data-user="password"]', '#synthetic-secret'])
def test_contextual_checks_reject_actual_credential_data(tmp_path, value):
    repo = WorkflowRepository(tmp_path/'db', secrets=['a', 'password', 'synthetic-secret'])
    with pytest.raises(ValueError, match='凭据'):
        repo._no_secrets({'kind':'css','value':value})


def test_generic_locators_and_url_data_are_scrubbed_contextually(tmp_path):
    c = Settings(_env_file=None,database_path=tmp_path/'db')
    c.register_secrets(['a','password','synthetic-secret'])
    assert c.scrub({'kind':'css','value':'#batch'})['value'] == '#batch'
    assert c.scrub({'kind':'css','value':'#synthetic-secret'})['value'] == '#[REDACTED]'
    observation = {'url':'http://example.test/a','text':'','elements':[]}
    assert c.scrub(observation)['url'] == 'http://example.test/[REDACTED]'
    assert c.scrub({'kind':'role','role':'synthetic-secret','value':'safe'})['role'] == '[REDACTED]'
    repo = WorkflowRepository(tmp_path/'recording',secrets=['synthetic-secret'])
    with pytest.raises(ValueError, match='凭据'):
        repo._no_secrets({'kind':'role','role':'synthetic-secret','value':'safe'})


def test_generated_identity_and_credential_defaults_are_not_raw_data(tmp_path):
    repo = WorkflowRepository(tmp_path/'db',secrets=['dead','login'])
    repo._no_secrets({'id':'login_1','parameters':[{'kind':'credential','default':'deadbeef'}]})
    with pytest.raises(ValueError,match='凭据'):
        repo._no_secrets({'kind':'text','default':'deadbeef'})


async def test_new_secret_registrations_rescrub_outgoing_and_retained_history(tmp_path):
    import json
    from types import SimpleNamespace
    import httpx
    from app.agent import StepAgent
    from app.contracts import PageObservation
    settings = Settings(_env_file=None,database_path=tmp_path/'db',
        llm_base_url='http://fixture/v1',llm_model='password-model')
    async def checkpoint():
        pass
    async def observe(**kwargs):
        return PageObservation(url='http://example.test',text='',elements=[])
    session = SimpleNamespace(manual_checkpoint=checkpoint,observe=observe,control_revision=0)
    requests = []
    secrets = ['synthetic-first-secret','synthetic-second-secret']
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200,json={'choices':[{'message':{'content':
            'prior assistant ' + ' '.join(secrets) if len(requests)==1 else 'done'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = StepAgent(settings,client)
        await agent.run('prior user '+' '.join(secrets),session)
        assert all(secret in str(agent.history) for secret in secrets)
        for index,secret in enumerate(secrets):
            settings.register_secrets([secret])
            await agent.run('benign command',session)
            payload = requests[-1]
            assert payload['model'] == 'password-model'
            assert payload['tools'][0]['function']['name'] == 'browser_action'
            assert 'fill' in payload['tools'][0]['function']['parameters']['properties']['kind']['enum']
            assert [message['role'] for message in payload['messages'][:3]] == ['system','user','assistant']
            for protected in secrets[:index+1]:
                assert protected not in json.dumps(payload,ensure_ascii=False)
                assert protected not in json.dumps(agent.history,ensure_ascii=False)
