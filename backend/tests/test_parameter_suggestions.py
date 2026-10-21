import json

import httpx
from pydantic import SecretStr

from app.config import Settings
from app.workflows.suggestions import ParameterSuggester
from workflow_samples import sample_workflow


async def test_suggestions_can_only_change_existing_parameter_metadata():
    definition = sample_workflow('http://example.test')
    settings = Settings(_env_file=None, llm_base_url='http://model.test/v1', llm_model='test', llm_api_key=SecretStr('SECRET_KEY'))
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'tool_calls': [{'function': {'name': 'suggest_parameters',
             'arguments': json.dumps({'parameters': [{'key': 'batch', 'label': '批次号', 'kind': 'text', 'required': True},
                                                    {'key': 'level', 'label': '层级', 'kind': 'number', 'required': True}]})}}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        suggested, used = await ParameterSuggester(settings, client).suggest(definition)
    assert used == 1
    assert suggested.parameters[0].label == '批次号'
    assert suggested.parameters[0].default == '001'
    assert suggested.steps == definition.steps
    assert 'SECRET_KEY' not in json.dumps(sent)


async def test_bad_suggestion_falls_back_without_mutating_definition():
    definition = sample_workflow('http://example.test')
    settings = Settings(_env_file=None, llm_base_url='http://model.test/v1', llm_model='test')
    def handler(request):
        return httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        suggested, used = await ParameterSuggester(settings, client).suggest(definition)
    assert suggested == definition
    assert used == 1
