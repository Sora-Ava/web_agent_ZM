import time

from ..contracts import ActionResult
from .evidence import browser_alive, completed, login_page, snapshot


class FatalRecovery(Exception):
    pass


class RecoveryRuntime:
    def __init__(self, session, agent, overrides, emit, checkpoint, restore, values=None, scope=None):
        self.session = session
        self.agent = agent
        self.overrides = overrides
        self.emit = emit
        self.checkpoint = checkpoint
        self.restore = restore
        self.calls = 0
        self.repairs = []
        self.login_attempts = 0
        self.login_spec = None
        self.values = values or {}
        self.scope = scope

    async def boundary(self):
        if self.checkpoint:
            await self.checkpoint()

    async def execute(self, action, step, index, total):
        before = await snapshot(self.session)
        result = await self.session.execute(action)
        if result.scope_violation:
            return result
        if result.status == 'success':
            return result
        await self.boundary()
        evidence = await completed(self.session, action, before)
        if evidence:
            return evidence
        if action.kind == 'download' and result.effect_started:
            return ActionResult(status='failed', message='导出已触发但完成结果不确定，请人工检查；未再次点击导出', effect_started=True)
        if not await browser_alive(self.session):
            raise FatalRecovery('浏览器不可用，已停止后续派发，请重启浏览器后启动队列')
        if await login_page(self.session, self.login_spec):
            if not self.login_spec:
                raise FatalRecovery('当前流程没有登录步骤，请先运行登录流程或添加登录步骤')
            if self.login_attempts:
                raise FatalRecovery('重新登录后再次失效，已停止后续派发')
            self.login_attempts += 1
            await self.boundary()
            from ..login import LoginController
            login = await LoginController(self.session).execute(self.login_spec, self.values, self.scope, force=True)
            if self.emit:
                await self.emit({'type': 'login_recovery', 'id': f'{step.id}:login', 'name': '登录过期恢复', 'index': index, 'total': total,
                    'model_calls': 0, 'result': {'status': login.status, 'message': '程序重新登录成功' if login.status == 'success' else '程序重新登录失败'}})
            if login.status != 'success':
                raise FatalRecovery('重新登录失败，已停止后续派发，请检查登录配置')
            await self.boundary()
            restored = await self.restore(index)
            if not restored:
                return ActionResult(status='failed', message='重新登录后无法安全重建业务状态，请人工检查，未重复导出')
            await self.boundary()
            before = await snapshot(self.session)
            result = await self.session.execute(action)
            if result.scope_violation:
                return result
            if result.status == 'success':
                return result
            evidence = await completed(self.session, action, before)
            if evidence:
                return evidence
            if await login_page(self.session, self.login_spec):
                raise FatalRecovery('重新登录后仍失效，已停止后续派发')
        if action.kind == 'download' and result.effect_started:
            return ActionResult(status='failed', message='导出已触发但完成结果不确定，请人工检查；未再次点击导出', effect_started=True)
        if not self.agent or not self.agent.available or action.kind == 'navigate':
            return result
        for attempt in (1, 2):
            await self.boundary()
            if not await browser_alive(self.session):
                raise FatalRecovery('浏览器不可用，已停止后续派发')
            observation = await self.session.observe()
            started = time.monotonic()
            self.calls += 1
            target = None
            try:
                target = await self.agent.propose(action, result.message, observation, attempt)
            finally:
                if self.emit:
                    await self.emit({'type': 'recovery_attempt', 'id': f'{step.id}:recovery:{attempt}', 'name': step.name + ' · 恢复',
                        'index': index, 'total': total, 'model_calls': 1, 'attempt': attempt,
                        'elapsed_s': round(time.monotonic() - started, 2),
                        'result': {'status': 'success' if target else 'failed', 'message': f'第 {attempt}/2 次恢复请求：' + ('获得定位建议' if target else '无可用定位建议')}})
            await self.boundary()
            # A download may have completed while the provider was responding.
            evidence = await completed(self.session, action, before)
            if evidence:
                return evidence
            if target is None:
                continue
            candidate = action.model_copy(update={'target': target}, deep=True)
            before = await snapshot(self.session)
            result = await self.session.execute(candidate)
            if result.scope_violation:
                return result
            await self.boundary()
            evidence = await completed(self.session, candidate, before) if result.status != 'success' else None
            if evidence:
                result = evidence
            if result.status == 'success':
                self.overrides[step.id] = target
                if target != step.action.target:
                    repair = {'step_id': step.id, 'step_name': step.name, 'original': step.action.target.model_dump(),
                        'replacement': target.model_dump(), 'reason': '页面定位变化，受约束动作已执行成功'}
                    self.repairs.append(repair)
                    if self.emit:
                        await self.emit({'type': 'repair_suggested', **repair})
                return result
            if candidate.kind == 'download' and result.effect_started:
                return ActionResult(status='failed', message='恢复导出已触发但结果不确定，请人工检查；未再次点击', effect_started=True)
        return result
