from typing import Literal

from pydantic import BaseModel, Field

from ..contracts import BrowserAction
from .models import WorkflowDefinition, site_key, validate_parameters
from ..tasks.control import TaskStopped
from ..recovery.agent import RecoveryAgent
from ..recovery.runtime import RecoveryRuntime, FatalRecovery
from ..recovery.evidence import browser_alive, login_page


class RunResult(BaseModel):
    status: Literal['success', 'no_data', 'failed', 'stopped']
    message: str
    steps: list[dict] = Field(default_factory=list)
    file_ids: list[str] = Field(default_factory=list)
    model_calls: int = 0
    repairs: list[dict] = Field(default_factory=list)
    fatal: bool = False


class WorkflowExecutor:
    def __init__(self, session, recovery=None):
        self.session = session
        self.recovery = recovery if recovery is not None else RecoveryAgent(session.settings)

    async def _logged_in(self):
        session = self.session
        if site_key(session.page.url) != site_key(session.settings.site_url):
            return False
        locator = session.page.locator(session.settings.logged_in_selector)
        return await locator.count() == 1 and await locator.is_visible()

    async def execute(self, definition: WorkflowDefinition, values: dict[str, str], emit=None, checkpoint=None, overrides=None) -> RunResult:
        steps, file_ids = [], []
        overrides = overrides if overrides is not None else {}
        runtime = None
        started = False
        initial_files = set(self.session.downloads.files)
        def result(**kwargs):
            for identity in set(self.session.downloads.files) - initial_files:
                entry = self.session.downloads.get(identity)
                if identity not in file_ids and entry.path.is_file() and entry.path.stat().st_size > 0:
                    file_ids.append(identity)
            return RunResult(steps=steps, file_ids=file_ids, model_calls=runtime.calls if runtime else 0,
                repairs=runtime.repairs if runtime else [], **kwargs)
        async def failure(message):
            fatal = started and (not await browser_alive(self.session) or await login_page(self.session))
            return result(status='failed', message=message, fatal=fatal)
        try:
            definition = WorkflowDefinition.model_validate(definition.model_dump())
            values = validate_parameters(definition, values)
            if definition.site_key != site_key(self.session.settings.site_url):
                raise ValueError('当前网站与流程所属系统不一致，请检查 SITE_URL')
            if checkpoint:
                await checkpoint()
            started = True
            await self.session.start()
            await self.session.page.goto(definition.entry_url, wait_until='domcontentloaded')
            if not await self._logged_in():
                login = await self.session.login()
                if login.status != 'success':
                    raise FatalRecovery('登录失败，请检查配置后重新启动队列')
                await self.session.page.goto(definition.entry_url, wait_until='domcontentloaded')
                if not await self._logged_in():
                    raise FatalRecovery('重新登录后流程入口仍未登录，已停止后续派发')
            self.session.status = 'ready'
            def bind(step):
                data = step.action.model_dump()
                if step.value_parameter:
                    data['value'] = values[step.value_parameter]
                if step.id in overrides:
                    data['target'] = overrides[step.id].model_dump()
                if step.action.kind == 'download':
                    data['name_parts'] = ([definition.name] + [f'{p.label}-{values[p.key]}' for p in definition.parameters])[:6]
                return BrowserAction.model_validate(data)
            async def restore(index):
                if any(s.action and s.action.kind == 'download' for s in definition.steps[:index]):
                    return False
                await self.session.page.goto(definition.entry_url, wait_until='domcontentloaded')
                for previous in definition.steps[:index]:
                    if checkpoint:
                        await checkpoint()
                    if previous.condition:
                        locator = self.session.locator(previous.condition.target)
                        count = await locator.count()
                        if count > 1:
                            return False
                        visible = count == 1 and await locator.is_visible()
                        matched = visible if previous.condition.state == 'visible' else not visible
                        outcome = previous.condition.when_true if matched else previous.condition.when_false
                        if outcome != 'continue':
                            return False
                        continue
                    restored = await self.session.execute(bind(previous))
                    if restored.status != 'success':
                        return False
                return True
            runtime = RecoveryRuntime(self.session, self.recovery, overrides, emit, checkpoint, restore)
            for index, step in enumerate(definition.steps):
                if checkpoint:
                    await checkpoint()
                outcome = 'continue'
                if step.condition:
                    locator = self.session.locator(step.condition.target)
                    count = await locator.count()
                    if count > 1:
                        raise ValueError('条件匹配多个元素，请修正定位')
                    visible = count == 1 and await locator.is_visible()
                    matched = visible if step.condition.state == 'visible' else not visible
                    outcome = step.condition.when_true if matched else step.condition.when_false
                else:
                    action = bind(step)
                    executed = await runtime.execute(action, step, index, len(definition.steps))
                    result_data = executed.model_dump()
                    if executed.file_id:
                        file_ids.append(executed.file_id)
                if step.condition:
                    result_data = {'status': 'success', 'message': '条件成立' if matched else '条件不成立', 'outcome': outcome}
                record = {'id': step.id, 'name': step.name, 'index': index, 'total': len(definition.steps), 'result': result_data}
                steps.append(record)
                if emit:
                    await emit({'type': 'workflow_step', **record})
                if checkpoint:
                    await checkpoint()
                if result_data['status'] != 'success':
                    return await failure(f'步骤“{step.name}”失败：{result_data["message"]}')
                if outcome == 'no_data':
                    return result(status='no_data', message='暂无数据，未导出文件' if not file_ids else '暂无数据，已保留此前导出文件')
                if outcome == 'finish':
                    return result(status='success', message='条件分支已结束流程')
            return result(status='success', message='标准流程执行完成')
        except TaskStopped:
            return result(status='stopped', message='已在当前操作结束后停止，保留已有结果和文件')
        except FatalRecovery as exc:
            return result(status='failed', message=str(exc), fatal=True)
        except ValueError as exc:
            return await failure(self.session.settings.redact(str(exc)))
        except Exception as exc:
            return await failure(f'标准流程执行失败（{type(exc).__name__}），请检查当前页面及流程定位')
