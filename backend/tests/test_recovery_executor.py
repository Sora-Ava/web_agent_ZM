import asyncio
import pytest

from app.contracts import ActionResult, LocatorSpec
from app.workflows.executor import WorkflowExecutor
from app.tasks.control import TaskStopped
from workflow_samples import sample_workflow


class Proposer:
    available = True

    def __init__(self, value='下载报表'):
        self.calls = 0
        self.value = value

    async def propose(self, *args):
        self.calls += 1
        return LocatorSpec(kind='role', role='button', value=self.value)


@pytest.mark.parametrize('feedback_failure', [False, True])
async def test_renamed_download_recovery_and_task_override_reuse(session, monkeypatch, feedback_failure):
    original = session.execute
    async def execute(action):
        if action.kind == 'download':
            await session.page.locator('#export').evaluate("e=>e.textContent='下载报表'")
        result = await original(action)
        if feedback_failure and action.kind == 'download' and result.status == 'success':
            return ActionResult(status='failed', message='保存后的反馈失败')
        return result
    monkeypatch.setattr(session, 'execute', execute)
    agent, overrides = Proposer(), {}
    flow = sample_workflow(session.settings.site_url.split('/demo')[0])
    first = await WorkflowExecutor(session, recovery=agent).execute(flow, {'batch': 'R001', 'level': '1'}, overrides=overrides)
    assert first.status == 'success' and first.model_calls == 1 and len(first.repairs) == 1
    assert overrides['export'].value == '下载报表' and flow.steps[-1].action.target.value == '导出'
    second = await WorkflowExecutor(session, recovery=agent).execute(flow, {'batch': 'R002', 'level': '2'}, overrides=overrides)
    assert second.status == 'success' and second.model_calls == 0 and agent.calls == 1


async def test_maximum_two_attempts_then_failure(session):
    flow = sample_workflow(session.settings.site_url.split('/demo')[0])
    flow.steps[0].action.target.value = '不存在'
    agent = Proposer('同样不存在')
    result = await WorkflowExecutor(session, recovery=agent).execute(flow, {'batch': 'FAIL', 'level': '1'})
    assert result.status == 'failed' and result.model_calls == agent.calls == 2
    assert not result.file_ids and not result.repairs


async def test_saved_download_evidence_prevents_duplicate_export(session, monkeypatch):
    original, calls = session.execute, []
    async def execute(action):
        result = await original(action)
        if action.kind == 'download':
            calls.append(1)
            assert result.status == 'success'
            return ActionResult(status='failed', message='下载反馈失败')
        return result
    monkeypatch.setattr(session, 'execute', execute)
    agent = Proposer()
    result = await WorkflowExecutor(session, recovery=agent).execute(sample_workflow(session.settings.site_url.split('/demo')[0]), {'batch': 'ONCE', 'level': '1'})
    assert result.status == 'success' and len(calls) == 1 and agent.calls == 0
    assert len(result.file_ids) == 1 and session.downloads.get(result.file_ids[0]).size > 0


@pytest.mark.parametrize('expire_kind', ['query', 'download'])
async def test_login_expiry_rebuilds_query_without_model(session, monkeypatch, expire_kind):
    original, expired, logins = session.execute, [], []
    login = session.login
    async def relogin():
        logins.append(1)
        return await login()
    async def execute(action):
        if ((expire_kind == 'query' and action.wait_for_query) or (expire_kind == 'download' and action.kind == 'download')) and not expired:
            expired.append(1)
            await session.context.clear_cookies()
            await session.page.goto(session.settings.site_url)
            return ActionResult(status='failed', message='会话已过期')
        return await original(action)
    monkeypatch.setattr(session, 'execute', execute)
    monkeypatch.setattr(session, 'login', relogin)
    agent = Proposer()
    result = await WorkflowExecutor(session, recovery=agent).execute(sample_workflow(session.settings.site_url.split('/demo')[0]), {'batch': 'EXPIRE', 'level': '2'})
    assert result.status == 'success' and len(logins) == 1 and agent.calls == 0
    assert 'EXPIRE,2,120' in session.downloads.get(result.file_ids[0]).path.read_text(encoding='utf-8-sig')


async def test_stop_during_recovery_preserves_call_count_without_retry_action(session):
    entered, release, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
    class Slow(Proposer):
        async def propose(self, *args):
            self.calls += 1
            entered.set()
            await release.wait()
            return LocatorSpec(kind='role', role='button', value='生产查询')
    async def checkpoint():
        if stop.is_set():
            raise TaskStopped()
    flow = sample_workflow(session.settings.site_url.split('/demo')[0])
    flow.steps[0].action.target.value = '旧入口'
    task = asyncio.create_task(WorkflowExecutor(session, recovery=Slow()).execute(flow, {'batch': 'STOP', 'level': '1'}, checkpoint=checkpoint))
    await asyncio.wait_for(entered.wait(), 10)
    stop.set(); release.set()
    result = await task
    assert result.status == 'stopped' and result.model_calls == 1
    assert not result.file_ids and session.page.url.endswith('/demo/home')


async def test_failed_relogin_is_fatal_and_does_not_use_llm(session, monkeypatch):
    async def fail(action):
        await session.context.clear_cookies()
        await session.page.goto(session.settings.site_url)
        return ActionResult(status='failed', message='过期')
    async def login():
        return ActionResult(status='failed', message='登录失败')
    monkeypatch.setattr(session, 'execute', fail)
    monkeypatch.setattr(session, 'login', login)
    agent = Proposer()
    result = await WorkflowExecutor(session, recovery=agent).execute(sample_workflow(session.settings.site_url.split('/demo')[0]), {'batch': 'FAIL', 'level': '1'})
    assert result.status == 'failed' and result.fatal and agent.calls == 0


async def test_fill_completed_before_error_does_not_invoke_model(session, monkeypatch):
    original = session.execute
    async def execute(action):
        result = await original(action)
        return ActionResult(status='failed', message='填写反馈失败') if action.kind == 'fill' else result
    monkeypatch.setattr(session, 'execute', execute)
    agent = Proposer()
    result = await WorkflowExecutor(session, recovery=agent).execute(sample_workflow(session.settings.site_url.split('/demo')[0]), {'batch': 'FILLED', 'level': '1'})
    assert result.status == 'success' and agent.calls == result.model_calls == 0


async def test_old_file_does_not_count_as_current_download_evidence(session, monkeypatch):
    flow = sample_workflow(session.settings.site_url.split('/demo')[0])
    agent = Proposer('导出')
    first = await WorkflowExecutor(session, recovery=agent).execute(flow, {'batch': 'OLD', 'level': '1'})
    assert first.status == 'success'
    original = session.execute
    async def execute(action):
        if action.kind == 'download':
            return ActionResult(status='failed', message='本次导出失败')
        return await original(action)
    monkeypatch.setattr(session, 'execute', execute)
    second = await WorkflowExecutor(session, recovery=agent).execute(flow, {'batch': 'NEW', 'level': '2'})
    assert second.status == 'failed' and not second.file_ids and second.model_calls == 2
    assert len(session.downloads.files) == 1


async def test_triggered_download_without_completion_is_not_clicked_again(session, monkeypatch):
    original = session.execute
    async def execute(action):
        if action.kind == 'download':
            await session.page.evaluate('()=>{window.downloadFile=()=>{window.exportClicks=(window.exportClicks||0)+1}}')
        return await original(action)
    monkeypatch.setattr(session, 'execute', execute)
    agent = Proposer('导出')
    result = await WorkflowExecutor(session, recovery=agent).execute(sample_workflow(session.settings.site_url.split('/demo')[0]), {'batch': 'UNCERTAIN', 'level': '1'})
    assert result.status == 'failed'
    assert await session.page.evaluate('window.exportClicks') == 1
    assert agent.calls == 0 and not result.file_ids


@pytest.mark.parametrize('failure_path', ['last_candidate', 'last_proposal', 'entry_navigation'])
async def test_browser_loss_at_all_failure_exits_is_fatal(session, monkeypatch, failure_path):
    class Closing(Proposer):
        async def propose(self, *args):
            self.calls += 1
            if failure_path == 'last_proposal':
                if self.calls == 2:
                    await session.browser.close()
                return None
            return LocatorSpec(kind='role', role='button', value='生产查询')
    agent = Closing()
    async def execute(action):
        if failure_path == 'last_candidate' and agent.calls == 2:
            await session.browser.close()
        return ActionResult(status='failed', message='定位或浏览器失败')
    monkeypatch.setattr(session, 'execute', execute)
    if failure_path == 'entry_navigation':
        async def goto(*args, **kwargs):
            await session.browser.close()
            raise RuntimeError('browser disconnected')
        monkeypatch.setattr(session.page, 'goto', goto)
    result = await WorkflowExecutor(session, recovery=agent).execute(sample_workflow(session.settings.site_url.split('/demo')[0]), {'batch': 'FATAL', 'level': '1'})
    assert result.status == 'failed' and result.fatal
    assert result.model_calls == (0 if failure_path == 'entry_navigation' else 2)
