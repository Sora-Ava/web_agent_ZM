from typing import Literal

from pydantic import BaseModel, Field

from ..contracts import BrowserAction
from .models import WorkflowDefinition, site_key, validate_parameters
from ..tasks.control import TaskStopped


class RunResult(BaseModel):
    status: Literal['success', 'no_data', 'failed', 'stopped']
    message: str
    steps: list[dict] = Field(default_factory=list)
    file_ids: list[str] = Field(default_factory=list)
    model_calls: int = 0


class WorkflowExecutor:
    def __init__(self, session):
        self.session = session

    async def _logged_in(self):
        session = self.session
        if site_key(session.page.url) != site_key(session.settings.site_url):
            return False
        locator = session.page.locator(session.settings.logged_in_selector)
        return await locator.count() == 1 and await locator.is_visible()

    async def execute(self, definition: WorkflowDefinition, values: dict[str, str], emit=None, checkpoint=None) -> RunResult:
        steps, file_ids = [], []
        try:
            definition = WorkflowDefinition.model_validate(definition.model_dump())
            values = validate_parameters(definition, values)
            if definition.site_key != site_key(self.session.settings.site_url):
                raise ValueError('当前网站与流程所属系统不一致，请检查 SITE_URL')
            if checkpoint:
                await checkpoint()
            await self.session.start()
            await self.session.page.goto(definition.entry_url, wait_until='domcontentloaded')
            if not await self._logged_in():
                login = await self.session.login()
                if login.status != 'success':
                    raise ValueError('登录失败，请检查配置')
                await self.session.page.goto(definition.entry_url, wait_until='domcontentloaded')
                if not await self._logged_in():
                    raise ValueError('流程入口未处于已登录状态')
            self.session.status = 'ready'
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
                    result = {'status': 'success', 'message': '条件成立' if matched else '条件不成立', 'outcome': outcome}
                else:
                    action_data = step.action.model_dump()
                    if step.value_parameter:
                        action_data['value'] = values[step.value_parameter]
                    if step.action.kind == 'download':
                        action_data['name_parts'] = [definition.name] + [f'{p.label}-{values[p.key]}' for p in definition.parameters]
                        action_data['name_parts'] = action_data['name_parts'][:6]
                    action = BrowserAction.model_validate(action_data)
                    executed = await self.session.execute(action)
                    result = executed.model_dump()
                    if executed.file_id:
                        file_ids.append(executed.file_id)
                record = {'id': step.id, 'name': step.name, 'index': index, 'total': len(definition.steps), 'result': result}
                steps.append(record)
                if emit:
                    await emit({'type': 'workflow_step', **record})
                if checkpoint:
                    await checkpoint()
                if result['status'] != 'success':
                    return RunResult(status='failed', message=f'步骤“{step.name}”失败：{result["message"]}', steps=steps, file_ids=file_ids)
                if outcome == 'no_data':
                    return RunResult(status='no_data', message='暂无数据，未导出文件', steps=steps, file_ids=file_ids)
                if outcome == 'finish':
                    return RunResult(status='success', message='条件分支已结束流程', steps=steps, file_ids=file_ids)
            return RunResult(status='success', message='标准流程执行完成', steps=steps, file_ids=file_ids)
        except TaskStopped:
            return RunResult(status='stopped', message='已在当前操作结束后停止，保留已有结果和文件', steps=steps, file_ids=file_ids)
        except ValueError as exc:
            return RunResult(status='failed', message=self.session.settings.redact(str(exc)), steps=steps, file_ids=file_ids)
        except Exception as exc:
            return RunResult(status='failed', message=f'标准流程执行失败（{type(exc).__name__}），请检查当前页面及流程定位', steps=steps, file_ids=file_ids)
