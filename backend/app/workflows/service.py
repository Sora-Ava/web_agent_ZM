import asyncio
from uuid import uuid4

from .executor import RunResult, WorkflowExecutor
from .models import WorkflowDefinition, site_key, validate_parameters


class WorkflowRunService:
    def __init__(self, session, repository, bus, command_runner):
        self.session = session
        self.repository = repository
        self.bus = bus
        self.command_runner = command_runner
        self.current = None
        self.task = None

    async def submit(self, flow_id, version, parameters):
        record = self.repository.get(flow_id, version)
        definition = WorkflowDefinition.model_validate(record['definition'])
        values = validate_parameters(definition, parameters)
        if definition.site_key != site_key(self.session.settings.site_url):
            raise ValueError('当前网站与流程所属系统不一致')
        lease = self.session.reserve()
        await lease.__aenter__()
        self.command_runner.agent.history = []
        self.current = {'id': uuid4().hex, 'flow_id': flow_id, 'name': definition.name, 'version': record['version'],
                        'parameters': values, 'status': 'running', 'steps': [], 'result': None, 'model_calls': 0}
        self.task = asyncio.create_task(self._run(definition, values, lease, self.current))
        return self.current['id']

    async def _run(self, definition, values, lease, run):
        async def emit(event):
            run['steps'].append({key: value for key, value in event.items() if key != 'type'})
            await self.bus.publish(event)
        try:
            await self.bus.publish({'type': 'workflow_started', 'run_id': run['id']})
            result = await WorkflowExecutor(self.session).execute(definition, values, emit)
            run.update(status=result.status, result=result.model_dump())
        except asyncio.CancelledError:
            run.update(status='interrupted')
            raise
        except Exception:
            result = RunResult(status='failed', message='标准流程执行异常')
            run.update(status='failed', result=result.model_dump())
        finally:
            await lease.__aexit__(None, None, None)
            await self.bus.publish({'type': 'workflow_finished', 'run_id': run['id']})
