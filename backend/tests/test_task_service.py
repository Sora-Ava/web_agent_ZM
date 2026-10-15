import asyncio
from contextlib import contextmanager

from app.events import CommandRunner, EventBus
from app.workflows.repository import WorkflowRepository
from app.workflows.models import site_key
from app.workflows.executor import WorkflowExecutor
from app.tasks.repository import TaskRepository
from app.tasks.service import TaskService
from workflow_samples import sample_workflow


def setup(session):
    flows = WorkflowRepository(session.settings.database_path, site_key(session.settings.site_url))
    flow = flows.create(sample_workflow(session.settings.site_url.split('/demo')[0]), True)
    repo = TaskRepository(flows)
    session.downloads.bind_repository(repo)
    bus = EventBus()
    runner = CommandRunner(session, bus, flows)
    service = TaskService(session, repo, bus, runner)
    return flows, flow, repo, service


async def until(predicate):
    async with asyncio.timeout(15):
        while not predicate():
            await asyncio.sleep(.02)


async def test_mixed_rows_queue_and_snapshot(session, monkeypatch):
    flows, flow, repo, service = setup(session)
    original = session.execute
    async def execute(action):
        if action.kind == 'fill' and action.value == 'FAIL':
            from app.contracts import ActionResult
            return ActionResult(status='failed', message='模拟单行业务失败')
        return await original(action)
    monkeypatch.setattr(session, 'execute', execute)
    service.authoring = True
    first = await service.submit(flow['id'], 1, [{'batch': v, 'level': '2'} for v in ['0001', 'NONE', 'FAIL', 'AFTER']])
    second = await service.submit(flow['id'], 1, [{'batch': '0001', 'level': '2'}])
    await asyncio.sleep(.1)
    assert repo.get(first)['status'] == 'queued'
    definition = sample_workflow(session.settings.site_url.split('/demo')[0])
    definition.steps[0].action.target.value = '不存在'
    flows.update(flow['id'], definition, True, 1)
    await service.release_authoring()
    await until(lambda: repo.get(second)['status'] == 'completed')
    saved = repo.get(first)
    assert [r['status'] for r in saved['rows']] == ['success', 'no_data', 'failed', 'success']
    assert saved['version'] == 1 and saved['model_calls'] == 0
    assert len(repo.files()) == 3
    assert len({f['name'] for f in repo.files()}) == 3
    assert repo.get(second)['rows'][0]['status'] == 'success'
    await service.shutdown()


async def test_pause_download_resume_and_stop_at_step_boundary(session, monkeypatch):
    _, flow, repo, service = setup(session)
    entered, release = asyncio.Event(), asyncio.Event()
    original = session.execute
    async def execute(action):
        if action.kind == 'download':
            entered.set()
            await release.wait()
        return await original(action)
    monkeypatch.setattr(session, 'execute', execute)
    identity = await service.submit(flow['id'], 1, [{'batch': 'P1', 'level': '1'}, {'batch': 'P2', 'level': '2'}])
    await asyncio.wait_for(entered.wait(), 10)
    await service.control(identity, 'pause')
    assert repo.get(identity)['status'] == 'pausing'
    assert session.busy
    release.set()
    await until(lambda: repo.get(identity)['status'] == 'paused')
    assert len(repo.files()) == 1
    assert len(repo.get(identity)['rows'][0]['steps']) == len(flow['definition']['steps'])
    await service.control(identity, 'resume')
    await until(lambda: repo.get(identity)['status'] == 'completed')
    assert len(repo.files()) == 2
    entered.clear(); release.clear()
    stopped = await service.submit(flow['id'], 1, [{'batch': 'STOP', 'level': '1'}, {'batch': 'SKIP', 'level': '1'}])
    await asyncio.wait_for(entered.wait(), 10)
    await service.control(stopped, 'stop')
    assert repo.get(stopped)['status'] == 'stopping'
    release.set()
    await until(lambda: repo.get(stopped)['status'] == 'stopped')
    result = repo.get(stopped)
    assert result['rows'][1]['status'] == 'skipped'
    assert len(repo.files()) == 3 and not session.busy
    await service.shutdown()


async def test_stop_queued_and_paused_holds_browser(session):
    _, flow, repo, service = setup(session)
    service.authoring = True
    identity = await service.submit(flow['id'], 1, [{'batch': 'P1', 'level': '1'}])
    await service.control(identity, 'pause')
    assert repo.get(identity)['status'] == 'paused'
    await service.release_authoring()
    await asyncio.sleep(.1)
    assert not session.busy
    await service.control(identity, 'stop')
    assert repo.get(identity)['status'] == 'stopped'
    assert not repo.files()
    await service.shutdown()


async def test_checkpoint_query_count_does_not_grow_with_batch_size(session, monkeypatch):
    _, flow, repo, service = setup(session)
    task = repo.create(flow, [{'batch': str(i), 'level': '1'} for i in range(500)])
    repo.update_task(task['id'], 'running')
    statements = []
    original = repo.connection
    @contextmanager
    def traced():
        with original() as db:
            db.set_trace_callback(statements.append)
            yield db
    monkeypatch.setattr(repo, 'connection', traced)
    await service._checkpoint(task['id'])
    assert len([sql for sql in statements if sql.startswith('SELECT')]) <= 2
