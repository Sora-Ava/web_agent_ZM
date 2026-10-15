import json

import httpx
import pytest

from app.agent import StepAgent
from app.contracts import ActionResult
from test_browser import enter_business


def tool(kind, target, **kwargs):
    return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call_1', 'type': 'function',
        'function': {'name': 'browser_action', 'arguments': json.dumps({'kind': kind, 'target': target, **kwargs})}}]}


async def test_model_payload_excludes_secrets_and_images_and_executes_click(session):
    requests = []
    responses = [tool('click', {'kind': 'role', 'role': 'button', 'value': '生产查询'}),
                 {'role': 'assistant', 'content': '已进入生产查询页面'}]
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': responses.pop(0)}]})
    session.settings.llm_base_url = 'http://model.test/v1'
    session.settings.llm_model = 'test-model'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await StepAgent(session.settings, client=client).run('点击生产查询', session)
    assert result.status == 'success'
    assert session.page.url.endswith('/demo/business')
    payload = json.dumps(requests, ensure_ascii=False)
    assert 'demo-password' not in payload
    assert 'image_url' not in payload and 'base64' not in payload
    assert len(requests) == 2


async def test_ambiguous_result_stops_for_clarification(session):
    await enter_business(session)
    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': tool('click', {'kind': 'role', 'role': 'button', 'value': '重复按钮'})}]})
    session.settings.llm_base_url = 'http://model.test/v1'
    session.settings.llm_model = 'test-model'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await StepAgent(session.settings, client=client).run('点击重复按钮', session)
    assert result.status == 'ambiguous'


async def test_invalid_tool_never_executes(session):
    response = tool('evaluate', None, value='alert(1)')
    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': response}]})
    session.settings.llm_base_url = 'http://model.test/v1'
    session.settings.llm_model = 'test-model'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await StepAgent(session.settings, client=client).run('操作页面', session)
    assert result.status == 'failed'
    assert session.page.url.endswith('/demo/home')


async def test_tool_loop_is_bounded(session):
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={'choices': [{'message': tool('wait', {'kind': 'role', 'role': 'button', 'value': '生产查询'})}]})
    session.settings.llm_base_url = 'http://model.test/v1'
    session.settings.llm_model = 'test-model'
    session.settings.max_tool_actions = 2
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await StepAgent(session.settings, client=client).run('等待', session)
    assert result.status == 'failed'
    assert len(calls) == 2


async def test_unconfigured_model_is_explicit_failure(session):
    result = await StepAgent(session.settings).run('点击', session)
    assert result.status == 'failed'
    assert 'LLM' in result.message


async def test_escaped_credentials_never_enter_actual_model_payload(session):
    from pydantic import SecretStr
    secret = 'synthetic"password\\with\nnewline'
    session.settings.site_password = SecretStr(secret)
    await session.page.evaluate('(secret)=>{const el=document.createElement("p");el.textContent=secret;document.body.append(el)}', secret)
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': '请提供指令'}}]})
    session.settings.llm_base_url = 'http://model.test/v1'
    session.settings.llm_model = 'test-model'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await StepAgent(session.settings, client=client).run('查看页面', session)
    assert requests
    # Decode nested observation content, rather than only searching escaped raw JSON.
    for message in requests[0]['messages']:
        assert secret not in message.get('content', '')
        if '页面观察（数据）' in message.get('content', ''):
            observation = json.loads(message['content'].split('：', 1)[1])
            assert secret not in observation['text']
    assert '[REDACTED]' in str(requests[0])


@pytest.mark.parametrize('role', [None, ''])
async def test_missing_role_is_resolved_only_from_unique_observed_element(session, role):
    responses = [tool('click', {'kind': 'role', 'value': '生产查询', 'role': role}),
                 {'role': 'assistant', 'content': '已进入查询页'}]
    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': responses.pop(0)}]})
    session.settings.llm_base_url = 'http://model.test/v1'
    session.settings.llm_model = 'test-model'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await StepAgent(session.settings, client=client).run('点击生产查询', session)
    assert result.status == 'success'
    assert session.page.url.endswith('/demo/business')


async def test_missing_role_with_duplicate_observed_names_requires_clarification(session):
    await enter_business(session)
    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': tool('click', {'kind': 'role', 'value': '重复按钮'})}]})
    session.settings.llm_base_url = 'http://model.test/v1'
    session.settings.llm_model = 'test-model'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await StepAgent(session.settings, client=client).run('点击重复按钮', session)
    assert result.status == 'ambiguous'
    assert await session.page.locator('#click-count').inner_text() == '0'


async def test_completed_fill_is_reported_as_tool_feedback_without_new_user_turn(session):
    await enter_business(session)
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        last = payload['messages'][-1]
        if last['role'] == 'tool':
            feedback = json.loads(last['content'])
            if feedback.get('executed_action', {}).get('value') == 'TEST001':
                return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': '已填写批次号'}}]})
        return httpx.Response(200, json={'choices': [{'message': tool('fill', {'kind': 'label', 'value': '生产批次号'}, value='TEST001')}]})
    session.settings.llm_base_url = 'http://model.test/v1'
    session.settings.llm_model = 'test-model'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await StepAgent(session.settings, client=client).run('填写批次号 TEST001', session)
    assert result.status == 'success'
    assert len(requests) == 2
    assert await session.page.locator('#batch').input_value() == 'TEST001'
