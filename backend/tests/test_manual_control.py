import asyncio

import httpx
import pytest

from app.main import create_app
from app.sessions import BusyError


async def test_manual_takeover_blocks_new_work_and_preserves_page(session):
    app = create_app(session.settings, session)
    page = session.page
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/api/session/takeover')
        assert response.status_code == 200
        assert response.json()['session']['control_mode'] == 'manual'
        assert (await client.post('/api/commands', json={'instruction':'test'})).status_code == 409
        with pytest.raises(BusyError):
            async with session.reserve():
                pytest.fail('manual control must exclude agent')
        await page.locator('#open-business').count()  # same live page stays usable
        response = await client.post('/api/session/return-control')
        assert response.status_code == 200
        assert response.json()['session']['control_mode'] == 'agent'
        assert session.page is page
    await app.state.tasks.shutdown()


async def test_takeover_waits_for_operation_boundary(session):
    async with session.reserve():
        await session.request_takeover()
        assert session.control_mode == 'requesting'
    assert session.control_mode == 'manual'
    await session.return_control()
    assert session.control_mode == 'agent'


async def test_task_checkpoint_waits_for_return_without_resuming_paused_task(session):
    app = create_app(session.settings, session)
    await session.request_takeover()
    waiter = asyncio.create_task(session.manual_checkpoint())
    await asyncio.sleep(.02)
    assert not waiter.done()
    await session.return_control()
    await asyncio.wait_for(waiter, 1)
    assert session.control_revision == 1
    await app.state.tasks.shutdown()


async def test_return_before_safe_boundary_is_rejected(session):
    async with session.reserve():
        await session.request_takeover()
        with pytest.raises(BusyError):
            await session.return_control()
    await session.return_control()


async def test_takeover_requires_live_browser(session):
    await session.close()
    with pytest.raises(ValueError):
        await session.request_takeover()


async def test_start_requests_visible_window(session, monkeypatch):
    app = create_app(session.settings, session)
    start = session.start
    requested = []
    async def capture(**kwargs):
        requested.append(kwargs)
        return await start(**kwargs)
    monkeypatch.setattr(session, 'start', capture)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/api/session/start')).status_code == 200
    assert requested == [{'visible': True}]
    await app.state.tasks.shutdown()


@pytest.mark.parametrize('stop', [True, False])
async def test_active_task_waits_and_stop_remains_available(session, monkeypatch, stop):
    from test_task_service import setup, until
    _, flow, repo, service = setup(session)
    entered, release = asyncio.Event(), asyncio.Event()
    original = session.execute
    calls = []
    async def held(action):
        calls.append(action.kind)
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return await original(action)
    monkeypatch.setattr(session, 'execute', held)
    identity = await service.submit(flow['id'], 1, [{'batch':'MANUAL','level':'1'}])
    try:
        await asyncio.wait_for(entered.wait(), 10)
        await session.request_takeover()
        assert session.control_mode == 'requesting'
        release.set()
        await until(lambda: session.control_mode == 'manual')
        count = len(calls)
        await asyncio.sleep(.1)
        assert len(calls) == count
        if stop:
            await service.control(identity, 'stop')
            await until(lambda: repo.status(identity) == 'stopped')
            assert session.control_mode == 'manual'
        await session.return_control()
        service.wake()
        if not stop:
            await until(lambda: repo.status(identity) == 'completed')
            assert len(repo.files()) == 1
            assert repo.get(identity)['rows'][0]['status'] == 'success'
    finally:
        release.set()
        await service.shutdown()


async def test_return_does_not_resume_independently_paused_task(session):
    from test_task_service import setup
    _, flow, repo, service = setup(session)
    service.authoring = True
    identity = await service.submit(flow['id'], 1, [{'batch':'PAUSED'}])
    try:
        await service.control(identity, 'pause')
        await session.request_takeover()
        await session.return_control()
        service.wake()
        await asyncio.sleep(.05)
        assert repo.status(identity) == 'paused'
    finally:
        await service.shutdown()


async def test_agent_discards_proposal_from_before_manual_browsing(session):
    import json
    from app.agent import StepAgent
    from test_agent import tool
    entered, respond = asyncio.Event(), asyncio.Event()
    requests = []
    async def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            entered.set()
            await respond.wait()
            message = tool('click', {'kind':'css','value':'#must-not-click'})
        else:
            message = {'role':'assistant','content':'已重新观察，请给出下一步'}
        return httpx.Response(200, json={'choices':[{'message':message}]})
    session.settings.llm_base_url = 'http://model.test/v1'
    session.settings.llm_model = 'test'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        async with session.reserve():
            task = asyncio.create_task(StepAgent(session.settings, client).run('操作页面',session))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                await session.request_takeover()
                respond.set()
                async with asyncio.timeout(5):
                    while session.control_mode != 'manual': await asyncio.sleep(.01)
                await session.page.goto(session.settings.site_url.split('/demo')[0]+'/demo/business')
                await session.return_control()
                await asyncio.wait_for(task, 5)
            finally:
                task.cancel()
    assert len(requests) == 2
    assert '人工操作后重新观察' in requests[1]['messages'][-1]['content']
    assert '/demo/business' in requests[1]['messages'][-1]['content']


async def test_recovery_discards_locator_proposed_before_takeover(session, monkeypatch):
    from types import SimpleNamespace
    from app.contracts import ActionResult, BrowserAction, LocatorSpec
    from app.recovery.runtime import RecoveryRuntime
    action = BrowserAction(kind='click', target={'kind':'css','value':'#missing'})
    executed = []
    async def execute(candidate):
        executed.append(candidate.target.value)
        return ActionResult(status='failed' if len(executed)==1 else 'success', message='test')
    class Proposer:
        available = True
        calls = 0
        async def propose(self, *args):
            self.calls += 1
            if self.calls == 1:
                await session.request_takeover()
                await session.return_control()
                return LocatorSpec(kind='css', value='#stale')
            return LocatorSpec(kind='css', value='#fresh')
    monkeypatch.setattr(session, 'execute', execute)
    proposer = Proposer()
    runtime = RecoveryRuntime(session, proposer, {}, None, None, None)
    step = SimpleNamespace(id='step',name='Step',action=action)
    result = await runtime.execute(action, step, 0, 1)
    assert result.status == 'success'
    assert executed == ['#missing', '#fresh']
