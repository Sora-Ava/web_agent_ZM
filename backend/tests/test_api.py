import asyncio

import httpx

from app.main import create_app
from app.contracts import ActionResult


class SlowAgent:
    def __init__(self):
        self.release = asyncio.Event()

    async def run(self, instruction, session, emit=None):
        await self.release.wait()
        return ActionResult(status='success', message='完成')


async def test_busy_command_and_close_rejected_and_result_persisted(session):
    app = create_app(session.settings, session=session)
    agent = SlowAgent()
    app.state.runner.agent = agent
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/commands', json={'instruction': '点击查询'})
        assert response.status_code == 202
        identity = response.json()['id']
        assert (await client.post('/api/commands', json={'instruction': '再点一次'})).status_code == 409
        assert (await client.delete('/api/session')).status_code == 409
        assert (await client.post('/api/commands', json={'instruction': '   '})).status_code == 422
        agent.release.set()
        await app.state.runner.task
        state = (await client.get('/api/session')).json()
        assert state['commands'][-1]['id'] == identity
        assert state['commands'][-1]['result']['status'] == 'success'


async def test_files_only_registered_ids_and_unknown_file_rejected(session):
    from test_browser import enter_business, action
    await enter_business(session)
    # Empty-file button is separate; this download returns a real CSV.
    result = await session.execute(action('download', '导出空文件'))
    assert result.status == 'failed'
    app = create_app(session.settings, session=session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/files')).json() == []
        assert (await client.get('/api/files/not-registered/download')).status_code == 404


async def test_external_origin_cannot_control_local_browser(session):
    app = create_app(session.settings, session=session)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/commands', json={'instruction': '点击'}, headers={'Origin': 'https://evil.example'})
        assert response.status_code == 403
