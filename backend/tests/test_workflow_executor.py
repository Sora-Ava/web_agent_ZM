import pytest

from app.agent import StepAgent
from app.workflows.executor import WorkflowExecutor
from app.workflows.models import validate_parameters
from workflow_samples import sample_workflow


async def test_replay_two_parameter_sets_without_llm_and_reuses_login(session, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Replay must not call LLM')
    monkeypatch.setattr(StepAgent, 'run', forbidden)
    definition = sample_workflow(session.settings.site_url.split('/demo')[0])
    executor = WorkflowExecutor(session)
    login_requests = []
    session.page.on('request', lambda r: login_requests.append(r.url) if r.url.endswith('/demo/auth') else None)
    for batch in ['000123', '${unchanged}']:
        result = await executor.execute(definition, {'batch': batch, 'level': '2'})
        assert result.status == 'success'
        assert result.model_calls == 0
        assert len(result.file_ids) == 1
        file = session.downloads.get(result.file_ids[0])
        assert batch + ',2,120' in file.path.read_text(encoding='utf-8-sig')
        assert batch in file.name
    assert not login_requests


async def test_no_data_branch_prevents_export(session):
    result = await WorkflowExecutor(session).execute(sample_workflow(session.settings.site_url.split('/demo')[0]), {'batch': 'NONE', 'level': '2'})
    assert result.status == 'no_data'
    assert result.model_calls == 0 and not result.file_ids
    assert not session.downloads.list()


async def test_wrong_site_and_ambiguous_target_fail_without_guessing(session):
    before = session.page.url
    wrong = sample_workflow('http://wrong.test')
    result = await WorkflowExecutor(session).execute(wrong, {'batch': '1', 'level': '2'})
    assert result.status == 'failed' and session.page.url == before
    definition = sample_workflow(session.settings.site_url.split('/demo')[0])
    data = definition.model_dump()
    data['steps'][1]['action'] = {'kind': 'click', 'target': {'kind': 'role', 'role': 'button', 'value': '重复按钮'}}
    data['steps'][1]['value_parameter'] = None
    result = await WorkflowExecutor(session).execute(type(definition).model_validate(data), {'batch': '1', 'level': '2'})
    assert result.status == 'failed'
    assert await session.page.locator('#click-count').inner_text() == '0'
    assert not session.downloads.list()


def test_parameters_validate_before_browser_use_and_keep_leading_zero():
    flow = sample_workflow('http://example.test')
    flow.parameters[0].default = None
    with pytest.raises(ValueError):
        validate_parameters(flow, {'level': '2'})
    with pytest.raises(ValueError):
        validate_parameters(flow, {'batch': '1', 'unexpected': '2'})
    assert validate_parameters(flow, {'batch': '0001'})['batch'] == '0001'
    flow.parameters[1].kind = 'number'
    with pytest.raises(ValueError):
        validate_parameters(flow, {'batch': '1', 'level': 'NaN'})
