import asyncio

from ..sessions import BusyError
from ..workflows.executor import WorkflowExecutor
from ..workflows.models import WorkflowDefinition
from .control import TaskStopped
from .repository import TERMINAL
from ..recovery.agent import RecoveryAgent
from ..recovery.repository import RepairRepository


class TaskService:
    def __init__(self, session, repository, bus, command_runner):
        self.session = session
        self.repository = repository
        self.bus = bus
        self.command_runner = command_runner
        self.authoring = False
        self.active_id = None
        self.dispatcher = None
        self.changed = asyncio.Event()
        self.closing = False
        self.recovery = RecoveryAgent(session.settings)
        self.repairs = RepairRepository(repository)
        self.fault = None

    async def publish(self):
        await self.bus.publish({'type': 'tasks_changed'})

    def wake(self):
        self.changed.set()
        if not self.closing and (self.dispatcher is None or self.dispatcher.done()):
            self.dispatcher = asyncio.create_task(self._dispatch())

    async def submit(self, flow_id, version, rows):
        record = self.repository.workflows.get(flow_id, version)
        task = self.repository.create(record, rows)
        self.wake()
        await self.publish()
        return task['id']

    async def release_authoring(self):
        if self.session.busy:
            raise BusyError('请等待当前浏览器操作完成')
        self.authoring = False
        self.fault = None
        self.command_runner.agent.history = []
        self.wake()
        await self.publish()

    async def control(self, identity, action):
        task = self.repository.get(identity)
        status = task['status']
        active = self.active_id == identity
        if status in TERMINAL:
            raise BusyError('任务已结束，不能再控制')
        if action == 'pause':
            if status == 'queued':
                self.repository.update_task(identity, 'paused')
            elif status == 'running':
                self.repository.update_task(identity, 'pausing')
            elif status not in ('paused', 'pausing'):
                raise BusyError('当前状态不能暂停')
        elif action == 'resume':
            if status not in ('paused', 'pausing'):
                raise BusyError('任务没有暂停')
            self.repository.update_task(identity, 'running' if active else 'queued')
        elif action == 'stop':
            if active:
                self.repository.update_task(identity, 'stopping')
            else:
                self.repository.update_task(identity, 'stopped')
                self.repository.finish_remaining(identity, 'skipped')
        else:
            raise ValueError('未知控制操作')
        self.wake()
        await self.publish()
        return self.repository.get(identity)

    async def _checkpoint(self, identity):
        while True:
            self.changed.clear()
            status = self.repository.status(identity)
            if status == 'stopping':
                raise TaskStopped()
            if status == 'pausing':
                self.repository.update_task(identity, 'paused')
                await self.publish()
                status = 'paused'
            if status != 'paused':
                return
            await self.changed.wait()

    async def _dispatch(self):
        while not self.closing:
            identity = self.repository.next_queued()
            if not identity:
                return
            if self.authoring or self.session.busy or self.fault:
                self.changed.clear()
                try:
                    await asyncio.wait_for(self.changed.wait(), .2)
                except TimeoutError:
                    pass
                continue
            # No await between the checks and reservation acquisition.
            async with self.session.reserve():
                self.active_id = identity
                self.command_runner.agent.history = []
                self.repository.update_task(identity, 'running')
                try:
                    await self._run(identity)
                except asyncio.CancelledError:
                    self.repository.update_task(identity, 'interrupted')
                    self.repository.finish_remaining(identity, 'interrupted')
                    raise
                except Exception:
                    self.repository.update_task(identity, 'failed')
                    self.repository.finish_remaining(identity, 'skipped')
                finally:
                    self.session.downloads.task_context = (None, None)
                    self.active_id = None
            await self.publish()

    async def _run(self, identity):
        task = self.repository.get(identity)
        definition = WorkflowDefinition.model_validate(task['definition'])
        overrides = {}
        await self.publish()
        for row in task['rows']:
            index = row['row_index']
            try:
                await self._checkpoint(identity)
            except TaskStopped:
                self.repository.update_task(identity, 'stopped')
                self.repository.finish_remaining(identity, 'skipped')
                return
            self.repository.update_row(identity, index, status='running')
            self.session.downloads.task_context = (identity, index)

            async def emit(event):
                if event['type'] == 'repair_suggested':
                    self.repairs.record(identity, index, self.session.settings.scrub({k: v for k, v in event.items() if k != 'type'}))
                    await self.publish()
                    return
                record = {key: value for key, value in event.items() if key != 'type'}
                record['kind'] = event['type']
                record = self.session.settings.scrub(record)
                self.repository.add_step(identity, index, record)
                await self.publish()

            async def checkpoint():
                await self._checkpoint(identity)

            result = await WorkflowExecutor(self.session, recovery=self.recovery).execute(definition, row['parameters'], emit, checkpoint, overrides)
            self.session.downloads.task_context = (None, None)
            self.repository.update_row(identity, index, status=result.status, result=self.session.settings.scrub(result.model_dump()))
            await self.publish()
            if result.fatal:
                self.fault = result.message
                self.repository.update_task(identity, 'failed')
                self.repository.finish_remaining(identity, 'skipped')
                return
            if result.status == 'stopped':
                self.repository.update_task(identity, 'stopped')
                self.repository.finish_remaining(identity, 'skipped')
                return
        self.repository.update_task(identity, 'completed')

    async def shutdown(self):
        self.closing = True
        self.changed.set()
        if self.dispatcher and not self.dispatcher.done():
            self.dispatcher.cancel()
            try:
                await self.dispatcher
            except asyncio.CancelledError:
                pass
        self.repository.interrupt_unfinished()
