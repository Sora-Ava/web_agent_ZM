from ..contracts import ActionResult


async def browser_alive(session):
    return bool(session.page and not session.page.is_closed() and session.browser and session.browser.is_connected())


async def login_page(session, spec=None):
    if spec:
        from ..login import LoginController
        return await LoginController(session).is_login_page(spec)
    try:
        fields = session.page.locator('input[type=password]')
        return any([await fields.nth(i).is_visible() for i in range(await fields.count())])
    except Exception:
        return False


async def snapshot(session):
    query = None
    try:
        query = await session.page.evaluate('window.__queryState?.attempt || null')
    except Exception:
        pass
    return {'files': set(session.downloads.files), 'url': session.page.url, 'query': query}


async def completed(session, action, before):
    # Only this step's newly registered, fully saved files count as evidence.
    if action.kind == 'download':
        for identity in set(session.downloads.files) - before['files']:
            entry = session.downloads.get(identity)
            if entry.path.is_file() and entry.path.stat().st_size > 0:
                return ActionResult(status='success', message='已核实本步骤文件保存完成，未重复导出', file_id=identity)
        return None
    try:
        if action.kind == 'navigate':
            if before['url'] != action.value and session.page.url == action.value:
                return ActionResult(status='success', message='已核实导航完成')
            return None
        locator = session.locator(action.target)
        if await locator.count() != 1:
            return None
        if await locator.evaluate('(el)=>el.matches("input[type=password],input[type=hidden]")'):
            return None
        if action.kind in ('fill', 'select') and await locator.input_value() == action.value:
            return ActionResult(status='success', message='已核实目标字段与本次参数一致')
        if action.kind == 'wait' and await locator.is_visible():
            return ActionResult(status='success', message='已核实目标可见')
        if action.wait_for_query:
            state = await session.page.evaluate('window.__queryState || null')
            if state and state.get('attempt') != before['query'] and all(state.get(k) for k in ('seen', 'finished', 'changed')):
                return ActionResult(status='success', message='已核实本次查询加载及结果更新完成')
    except Exception:
        pass
    return None
