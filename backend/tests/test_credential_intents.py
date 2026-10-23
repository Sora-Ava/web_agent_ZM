import json

import httpx
import pytest

from app.credential_intents import CredentialClarification, parse_credential_intent
from app.main import create_app


@pytest.mark.parametrize('text,username,password,submit', [
    ('账号是 alice', 'alice', None, None),
    ('口令是 synthetic-pass', None, 'synthetic-pass', None),
    ('口令为 "  !@#$，; 密。码  "', None, '  !@#$，; 密。码  ', None),
    ('账号 「张 三」，口令 「a,b; c!?」，然后点击登录', '张 三', 'a,b; c!?', '登录'),
    ('在口令框输入 ‘什么？’。', None, '什么？', None),
    ('密码为 "  密 码，x  "', None, '  密 码，x  ', None),
    ('账号 "张 三"，密码 "a,b; c"，然后点击登录', '张 三', 'a,b; c', '登录'),
    ('username=alice password="hello world"', 'alice', 'hello world', None),
])
def test_parse_values_and_safe_serialization(text, username, password, submit):
    intent = parse_credential_intent(text)
    assert intent.username.get_secret_value() == username if username is not None else intent.username is None
    assert intent.password.get_secret_value() == password if password is not None else intent.password is None
    assert intent.submit == submit
    for secret in (username, password):
        if secret:
            assert secret not in intent.display_instruction
            assert secret not in repr(intent)
            assert secret not in intent.model_dump_json()
            assert secret not in str(intent.model_dump())


@pytest.mark.parametrize('text', ['口令 "oops', '口令是 什么', '口令在哪里输入', '我的口令是 synthetic-secret', '口令secret', '口令 a; DROP', '密码 a 口令 b', '账号 a,b', '密码 "oops', '账号 a 账号 b', '输入密码 hello world', '把 hunter2 填入密码框', '密码 a; DROP', '我的密码是 secret', '点击登录，密码 secret', '点击登录 password=secret'])
def test_ambiguous_credentials_clarify(text):
    with pytest.raises(CredentialClarification):
        parse_credential_intent(text)


@pytest.mark.parametrize('text', ['点击修改口令按钮', '打开口令管理', 'https://example.test/口令', '点击登录按钮', '打开 https://example.test/login', '点击修改密码按钮', '搜索天气', 'https://account.example.test/login/password', '打开 https://account.example.test/login/password', '点击密码登录', '打开账号管理'])
def test_normal_commands(text):
    assert parse_credential_intent(text) is None


@pytest.mark.parametrize("instruction", ['口令是 "  synthetic-password  "', '账号 「synthetic-account」，口令是 「  synthetic-password  」，填写后点击登录', '账号 "synthetic-account" 密码 "  synthetic-password  "', '账号是 synthetic-account，密码是 「  synthetic-password  」，填写后点击登录'])
async def test_api_private_context_and_no_secret_model_events_history(session, instruction):
    app = create_app(session.settings, session=session)
    session.settings.llm_base_url = 'http://model.test/v1'
    session.settings.llm_model = 'synthetic'
    app.state.runner.agent.history = [{'role': 'user', 'content': 'prior   synthetic-password  '}]
    requests = []
    def handler(request):
        assert session.settings.redact('  synthetic-password  ') == '[REDACTED]'
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': '请明确目标'}}]})
    seen = []
    original = app.state.runner.agent.run
    async def spy(instruction, session, emit=None, credential_intent=None):
        seen.append((instruction, credential_intent))
        return await original(instruction, session, emit=emit, credential_intent=credential_intent)
    app.state.runner.agent.run = spy
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as model:
        app.state.runner.agent.client = model
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/commands', json={'instruction': instruction})
            assert response.status_code == 202
            await app.state.runner.task
            assert seen[0][1] is not None
            assert seen[0][1].username is None if instruction.startswith('口令') else seen[0][1].username.get_secret_value() == 'synthetic-account'
            assert seen[0][1].password.get_secret_value() == '  synthetic-password  '
            assert requests
            serialized = json.dumps([requests, app.state.runner.snapshot(), app.state.runner.agent.history, list(app.state.bus.history), response.json()], ensure_ascii=False)
            assert 'synthetic-account' not in serialized
            assert 'synthetic-password' not in serialized
            count = len(requests)
            response = await client.post('/api/commands', json={'instruction': '密码 "synthetic-unclosed'})
            assert response.status_code == 422
            assert 'synthetic-unclosed' not in response.text
            assert len(requests) == count

async def test_private_intent_cannot_use_ordinary_fill(session):
    from app.agent import StepAgent
    from test_agent import tool
    intent = parse_credential_intent('密码 "synthetic-private-value"')
    executed = []
    async def execute(*args, **kwargs):
        executed.append(args)
        raise AssertionError('private credentials must not use ordinary execute')
    session.execute = execute
    session.settings.llm_base_url = 'http://model.test/v1'
    session.settings.llm_model = 'synthetic'
    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': tool('fill', {'kind': 'css', 'value': '#password'}, value='[REDACTED]')}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await StepAgent(session.settings, client).run(intent.display_instruction, session, credential_intent=intent)
    assert result.status == 'ambiguous'
    assert executed == []

@pytest.mark.parametrize('text,username,password,submit', [
    ('在账号框输入 operator。', 'operator', None, None),
    ('在密码框输入 「示例密码」。', None, '示例密码', None),
    ('账号是 operator，密码是 「示例密码」，填写后点击登录', 'operator', '示例密码', '登录'),
    ('账号是 operator。', 'operator', None, None),
    ('在密码框输入 「  示例，密。码；  」。', None, '  示例，密。码；  ', None),
    ('密码是 "value。"。', None, 'value。', None),
])
def test_approved_natural_language_wrappers(text, username, password, submit):
    intent = parse_credential_intent(text)
    assert (intent.username.get_secret_value() if intent.username else None) == username
    assert (intent.password.get_secret_value() if intent.password else None) == password
    assert intent.submit == submit


@pytest.mark.parametrize('text', ['在密码框输入 「unclosed。', '在账号框输入 one two。', '在账号框输入 one，two。', '在密码框输入 「one」extra。', '账号是 one。密码是 two'])
def test_malformed_wrappers_clarify(text):
    with pytest.raises(CredentialClarification):
        parse_credential_intent(text)

@pytest.mark.parametrize('text', ['密码在哪里输入', '账号是什么', '密码忘记了怎么办', '密码secret', '账号operator', '密码是 什么', '密码为哪里输入'])
def test_questions_and_implicit_values_do_not_authorize_fill(text):
    with pytest.raises(CredentialClarification):
        parse_credential_intent(text)


@pytest.mark.parametrize('value', ['密码在哪里输入', '账号是什么', '密码忘记了怎么办', '什么'])
def test_quoted_question_words_are_literal_credentials(value):
    intent = parse_credential_intent('密码 「' + value + '」')
    assert intent.password.get_secret_value() == value


async def test_api_question_never_submits_or_calls_model(session):
    app = create_app(session.settings, session=session)
    submitted = []
    async def submit(*args, **kwargs):
        submitted.append((args, kwargs))
        raise AssertionError('question must not be submitted')
    app.state.runner.submit = submit
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for text in ('口令在哪里输入', '口令是 什么', '口令 "synthetic-unclosed', '我的口令是 synthetic-private', '密码在哪里输入', '账号是什么', '密码忘记了怎么办'):
            response = await client.post('/api/commands', json={'instruction': text})
            assert response.status_code == 422
    assert submitted == []
    assert not app.state.runner.commands
    assert not app.state.bus.history
