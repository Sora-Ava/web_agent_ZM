import asyncio

from ..sessions import BusyError
from .models import WorkflowDefinition, validate_parameters


class WorkflowRunService:
    """Compatibility view: single runs now use the persistent task queue."""

    def __init__(self, tasks):
        self.tasks = tasks
        self.identity = None

    @property
    def task(self):
        return self.tasks.dispatcher

    @property
    def current(self):
        if not self.identity:
            return None
        task = self.tasks.repository.get(self.identity)
        row = task['rows'][0]
        status = row['status'] if task['status'] == 'completed' else task['status']
        return {'id': task['id'], 'flow_id': task['flow_id'], 'name': task['name'], 'version': task['version'],
                'parameters': row['parameters'], 'status': status, 'steps': row['steps'], 'result': row['result'], 'model_calls': task['model_calls']}

    async def submit(self, flow_id, version, parameters):
        if self.tasks.fault:
            raise BusyError('队列因故障停止派发，请检查浏览器并显式重新启动队列')
        if self.tasks.session.busy:
            raise BusyError('浏览器正在执行操作')
        record = self.tasks.repository.workflows.get(flow_id, version)
        definition = WorkflowDefinition.model_validate(record['definition'])
        validate_parameters(definition, parameters, self.tasks.session.settings.credentials)
        self.identity = await self.tasks.submit(flow_id, version, [parameters])
        await self.tasks.release_authoring()
        await asyncio.sleep(0)
        return self.identity
