import asyncio

import httpx

from app.main import create_app
from app.contracts import LocatorSpec, ActionResult
from workflow_samples import sample_workflow
from test_recovery_executor import Proposer


async def test_recovered_task_audit_approval_and_zero_call_new_version(session, monkeypatch):
    app = create_app(session.settings, session)
    app.state.tasks.recovery = Proposer('下载报表')
    original = session.execute
    async def execute(action):
        if action.kind == 'download':
            await session.page.locator('#export').evaluate("el=>el.textContent='下载报表'")
        return await original(action)
    monkeypatch.setattr(session, 'execute', execute)
    flow = app.state.repository.create(sample_workflow(session.settings.site_url.split('/demo')[0]), True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        submitted = await c.post('/api/tasks', json={'flow_id': flow['id'], 'rows': [{'batch': 'R1'}, {'batch': 'R2'}]})
        identity = submitted.json()['id']
        await app.state.tasks.dispatcher
        task = (await c.get('/api/tasks/' + identity)).json()
        assert [r['status'] for r in task['rows']] == ['success', 'success']
        assert task['model_calls'] == 1 and task['rows'][1]['result']['model_calls'] == 0
        repairs = (await c.get(f'/api/tasks/{identity}/repairs')).json()
        assert len(repairs) == 1 and app.state.repository.get(flow['id'])['version'] == 1
        body = {'ids': [repairs[0]['id']], 'expected_version': 1, 'confirmed': False}
        assert (await c.post(f'/api/tasks/{identity}/repairs/approve', json=body)).status_code == 422
        body['confirmed'] = True
        approved = await c.post(f'/api/tasks/{identity}/repairs/approve', json=body)
        assert approved.status_code == 201 and approved.json()['version'] == 2
        assert app.state.repository.get(flow['id'], 1)['definition'] == flow['definition']
        replay = await c.post('/api/tasks', json={'flow_id': flow['id'], 'version': 2, 'rows': [{'batch': 'V2'}]})
        await app.state.tasks.dispatcher
        assert (await c.get('/api/tasks/' + replay.json()['id'])).json()['model_calls'] == 0
    await app.state.tasks.shutdown()


async def test_fatal_login_blocks_next_queued_task(session, monkeypatch):
    app = create_app(session.settings, session)
    flow = app.state.repository.create(sample_workflow(session.settings.site_url.split('/demo')[0]), True)
    app.state.tasks.authoring = True
    async def fail(action):
        await session.context.clear_cookies()
        await session.page.goto(session.settings.site_url)
        return ActionResult(status='failed', message='过期')
    async def login():
        return ActionResult(status='failed', message='登录失败')
    monkeypatch.setattr(session, 'execute', fail)
    monkeypatch.setattr(session, 'login', login)
    first = await app.state.tasks.submit(flow['id'], 1, [{'batch': 'F1'}, {'batch': 'F2'}])
    second = await app.state.tasks.submit(flow['id'], 1, [{'batch': 'NEXT'}])
    await app.state.tasks.release_authoring()
    async with asyncio.timeout(10):
        while app.state.tasks.repository.status(first) != 'failed':
            await asyncio.sleep(.02)
    assert app.state.tasks.fault
    assert app.state.tasks.repository.get(first)['rows'][1]['status'] == 'skipped'
    assert app.state.tasks.repository.status(second) == 'queued'
    assert not session.busy
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        response = await c.post(f'/api/workflows/{flow["id"]}/runs', json={'parameters': {'batch': 'NEW'}})
        assert response.status_code == 409 and app.state.tasks.fault
    await app.state.tasks.shutdown()


async def test_browser_loss_after_second_recovery_stops_remaining_dispatch(session, monkeypatch):
    app = create_app(session.settings, session)
    app.state.tasks.recovery = Proposer('生产查询')
    flow = app.state.repository.create(sample_workflow(session.settings.site_url.split('/demo')[0]), True)
    app.state.tasks.authoring = True
    async def execute(action):
        if app.state.tasks.recovery.calls == 2:
            await session.browser.close()
        return ActionResult(status='failed', message='浏览器中断')
    monkeypatch.setattr(session, 'execute', execute)
    first = await app.state.tasks.submit(flow['id'], 1, [{'batch': 'BROKEN'}, {'batch': 'SKIP'}])
    second = await app.state.tasks.submit(flow['id'], 1, [{'batch': 'QUEUED'}])
    await app.state.tasks.release_authoring()
    async with asyncio.timeout(10):
        while app.state.tasks.repository.status(first) not in ('completed', 'failed'):
            await asyncio.sleep(.02)
    task = app.state.tasks.repository.get(first)
    assert task['status'] == 'failed' and task['model_calls'] == 2
    assert task['rows'][1]['status'] == 'skipped'
    assert app.state.tasks.repository.status(second) == 'queued' and app.state.tasks.fault
    await app.state.tasks.shutdown()
