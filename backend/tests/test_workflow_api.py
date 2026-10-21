import asyncio

import httpx

from app.contracts import BrowserAction
from app.main import create_app
from app.workflows.models import site_key
from workflow_samples import sample_workflow


async def test_confirmed_save_version_conflict_and_replay_without_recording(session, monkeypatch):
    app = create_app(session.settings, session=session)
    def forbidden(*args, **kwargs):
        raise AssertionError('LLM must not run')
    monkeypatch.setattr(app.state.runner.agent, 'run', forbidden)
    definition = sample_workflow(session.settings.site_url.split('/demo')[0]).model_dump()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/api/workflows', json={'definition': definition, 'confirmed': False})).status_code == 422
        saved = await client.post('/api/workflows', json={'definition': definition, 'confirmed': True})
        assert saved.status_code == 201
        identity = saved.json()['id']
        updated = await client.post(f'/api/workflows/{identity}/versions', json={'definition': definition, 'confirmed': True, 'expected_version': 1})
        assert updated.status_code == 201 and updated.json()['version'] == 2
        assert (await client.post(f'/api/workflows/{identity}/versions', json={'definition': definition, 'confirmed': True, 'expected_version': 1})).status_code == 409
        before = (await client.get('/api/recording')).json()['revision']
        response = await client.post(f'/api/workflows/{identity}/runs', json={'parameters': {'batch': 'API001', 'level': '2'}, 'version': 1})
        assert response.status_code == 202
        assert (await client.post('/api/commands', json={'instruction': '点击'})).status_code == 409
        assert (await client.post('/api/recording/reset')).status_code == 409
        await app.state.workflows.task
        state = (await client.get('/api/session')).json()
        assert state['run']['status'] == 'success'
        assert state['run']['version'] == 1
        assert state['run']['result']['model_calls'] == 0
        assert state['run']['result']['file_ids']
        assert (await client.get('/api/recording')).json()['revision'] == before


async def test_successful_agent_actions_recorded_and_failed_command_warns(session):
    app = create_app(session.settings, session=session)
    class PartialAgent:
        async def run(self, instruction, session, emit=None):
            from app.contracts import ActionResult
            action = BrowserAction(kind='wait', target={'kind': 'role', 'role': 'button', 'value': '生产查询'})
            await emit({'type': 'step_result', 'action': 'wait', 'result': {'status': 'success', 'message': '完成'},
                        'record_action': action.model_dump(), 'url_before': session.page.url})
            await emit({'type': 'step_result', 'action': 'click', 'result': {'status': 'failed', 'message': '失败'},
                        'record_action': action.model_dump(), 'url_before': session.page.url})
            return ActionResult(status='failed', message='未完成')
    app.state.runner.agent = PartialAgent()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        await client.post('/api/commands', json={'instruction': '操作'})
        await app.state.runner.task
        draft = (await client.get('/api/recording')).json()
        assert len(draft['steps']) == 1
        assert draft['warnings']
        proposal = (await client.post('/api/recording/proposal', json={'use_model': False})).json()
        assert len(proposal['definition']['steps']) == 1


async def test_unknown_parameters_rejected_before_browser_operation(session):
    app = create_app(session.settings, session=session)
    saved = app.state.repository.create(sample_workflow(session.settings.site_url.split('/demo')[0]), confirmed=True)
    before = session.page.url
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(f'/api/workflows/{saved["id"]}/runs', json={'parameters': {'unknown': '1'}})
        assert response.status_code == 422
        assert session.page.url == before and not session.busy
