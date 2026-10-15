from app.contracts import BrowserAction, LocatorSpec
from test_browser import action, enter_business


async def test_download_saved_nonempty_with_unique_safe_parameter_name(session):
    await enter_business(session)
    await session.execute(BrowserAction(kind='fill', target=LocatorSpec(kind='label', value='生产批次号'), value='PC001'))
    await session.execute(action('click', '查询', wait_for_query=True))
    first = await session.execute(action('download', '导出', name_parts=['PC001', '层级:1']))
    second = await session.execute(action('download', '导出', name_parts=['PC001', '层级:1']))
    assert first.status == second.status == 'success'
    a, b = session.downloads.get(first.file_id), session.downloads.get(second.file_id)
    assert a.path != b.path
    assert 'PC001' in a.name and ':' not in a.name
    assert a.path.stat().st_size > 0
    assert 'PC001' in a.path.read_text(encoding='utf-8-sig')


async def test_empty_download_is_failure_and_not_listed(session):
    await enter_business(session)
    result = await session.execute(action('download', '导出空文件'))
    assert result.status == 'failed'
    assert not session.downloads.list()
