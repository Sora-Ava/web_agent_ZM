import asyncio
from collections import deque
from contextlib import asynccontextmanager
from uuid import uuid4

from .agent import StepAgent


class EventBus:
    def __init__(self):
        self.subscribers = set()
        self.history = deque(maxlen=200)
        self.sequence = 0

    async def publish(self, event):
        self.sequence += 1
        event = {**event, 'seq': self.sequence}
        self.history.append(event)
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self):
        queue = asyncio.Queue(maxsize=100)
        self.subscribers.add(queue)
        try:
            yield queue
        finally:
            self.subscribers.discard(queue)


class CommandRunner:
    def __init__(self, session, bus, repository=None):
        self.session = session
        self.bus = bus
        self.agent = StepAgent(session.settings)
        self.commands = deque(maxlen=100)
        self.task = None
        self.repository = repository
        self.workflows = None
        self.tasks = None

    async def submit(self, instruction):
        lease = self.session.reserve()
        await lease.__aenter__()
        if self.tasks:
            self.tasks.authoring = True
        command = {'id': uuid4().hex, 'instruction': self.session.settings.redact(instruction), 'status': 'running', 'result': None}
        self.commands.append(command)
        self.task = asyncio.create_task(self._run(command, instruction, lease))
        return command['id']

    async def _run(self, command, instruction, lease):
        from .contracts import ActionResult
        async def emit(event):
            if self.repository and event.get('record_action') and event.get('result', {}).get('status') == 'success':
                from .contracts import BrowserAction
                try:
                    self.repository.record(BrowserAction.model_validate(event['record_action']), command['id'], command['instruction'], event['url_before'], event.get('url_after'))
                except ValueError:
                    self.repository.warn('部分操作未记录：流程仅支持单个来源，切换网站请新建录制，并检查草稿完整性')
            await self.bus.publish({key: value for key, value in event.items() if key not in ('record_action', 'url_before', 'url_after')})
        try:
            await self.bus.publish({'type': 'command_started', 'command': dict(command)})
            result = await self.agent.run(instruction, self.session, emit=emit)
            command.update(status='finished', result=result.model_dump())
            if self.repository and result.status != 'success':
                self.repository.warn('存在未完成指令：仅保留已成功动作，请检查后确认保存')
            if result.status == 'ambiguous':
                await self.bus.publish({'type': 'needs_clarification', 'message': result.message})
        except asyncio.CancelledError:
            command.update(status='interrupted', result=None)
            raise
        except Exception:
            command.update(status='finished', result=ActionResult(status='failed', message='指令执行异常，请查看当前页面').model_dump())
        finally:
            await lease.__aexit__(None, None, None)
            await self.bus.publish({'type': 'command_finished', 'command': dict(command)})

    def snapshot(self):
        draft = self.repository.draft() if self.repository else None
        return {'session': self.session.state().model_dump(), 'commands': list(self.commands),
                'workflows': self.repository.list() if self.repository else [],
                'recording': {'revision': draft['revision'], 'step_count': len(draft['steps']), 'warnings': draft['warnings']} if draft else None,
                'run': self.workflows.current if self.workflows else None,
                'tasks': self.tasks.repository.list() if self.tasks else [],
                'queue': {'authoring': self.tasks.authoring, 'active_id': self.tasks.active_id, 'fault': self.tasks.fault} if self.tasks else None,
                'files': self.session.downloads.list(), 'events': list(self.bus.history)}
