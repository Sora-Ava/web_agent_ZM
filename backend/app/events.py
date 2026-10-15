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
    def __init__(self, session, bus):
        self.session = session
        self.bus = bus
        self.agent = StepAgent(session.settings)
        self.commands = deque(maxlen=100)
        self.task = None

    async def submit(self, instruction):
        lease = self.session.reserve()
        await lease.__aenter__()
        command = {'id': uuid4().hex, 'instruction': self.session.settings.redact(instruction), 'status': 'running', 'result': None}
        self.commands.append(command)
        self.task = asyncio.create_task(self._run(command, instruction, lease))
        return command['id']

    async def _run(self, command, instruction, lease):
        from .contracts import ActionResult
        try:
            await self.bus.publish({'type': 'command_started', 'command': dict(command)})
            result = await self.agent.run(instruction, self.session, emit=self.bus.publish)
            command.update(status='finished', result=result.model_dump())
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
        return {'session': self.session.state().model_dump(), 'commands': list(self.commands),
                'files': self.session.downloads.list(), 'events': list(self.bus.history)}
