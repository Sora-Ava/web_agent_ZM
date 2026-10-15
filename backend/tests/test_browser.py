import json
import time

from app.contracts import BrowserAction, LocatorSpec


def action(kind, name, **kwargs):
    return BrowserAction(kind=kind, target=LocatorSpec(kind='role', role='button', value=name), **kwargs)


async def enter_business(session):
    result = await session.execute(action('click', '生产查询'))
    assert result.status == 'success'


async def test_login_observation_excludes_secrets_and_hidden_content(session):
    await session.page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div hidden>HIDDEN_TOKEN</div><script>SECRET_SCRIPT</script>')")
    observation = (await session.observe()).model_dump_json()
    assert '生产查询' in observation
    assert 'demo-password' not in observation
    assert 'HIDDEN_TOKEN' not in observation
    assert 'SECRET_SCRIPT' not in observation


async def test_ambiguous_button_does_not_click(session):
    await enter_business(session)
    result = await session.execute(action('click', '重复按钮'))
    assert result.status == 'ambiguous'
    assert await session.page.locator('#click-count').inner_text() == '0'


async def test_query_waits_for_delayed_loading_and_handles_no_data(session):
    await enter_business(session)
    await session.execute(BrowserAction(kind='fill', target=LocatorSpec(kind='label', value='生产批次号'), value='PC001'))
    start = time.monotonic()
    result = await session.execute(action('click', '查询', wait_for_query=True))
    assert result.status == 'success'
    assert time.monotonic() - start >= .5
    assert 'PC001' in (await session.observe()).text
    await session.execute(BrowserAction(kind='fill', target=LocatorSpec(kind='label', value='生产批次号'), value='NONE'))
    assert (await session.execute(action('click', '查询', wait_for_query=True))).status == 'success'
    assert '暂无数据' in (await session.observe()).text


async def test_observation_does_not_include_field_values(session):
    await enter_business(session)
    await session.page.locator('#batch').fill('PRIVATE_FIELD_VALUE')
    assert 'PRIVATE_FIELD_VALUE' not in (await session.observe()).model_dump_json()


async def test_navigation_rejects_external_origin(session):
    result = await session.execute(BrowserAction(kind='navigate', value='https://example.com'))
    assert result.status == 'failed'
    assert session.page.url.startswith(session.settings.site_url.split('/demo')[0])


async def test_element_names_exclude_hidden_and_editable_descendants(session):
    await session.page.evaluate('''() => document.body.insertAdjacentHTML('beforeend', `
      <button>可见按钮<span hidden>HIDDEN_TOKEN</span><span style="display:none">CSS_HIDDEN_TOKEN</span></button>
      <div role="group">分组<textarea>TEXTAREA_SECRET</textarea><div contenteditable="true">EDITABLE_SECRET</div></div>
      <label>普通字段<span hidden>LABEL_SECRET</span><input></label>`)''')
    observation = (await session.observe()).model_dump_json()
    assert '可见按钮' in observation
    for secret in ['HIDDEN_TOKEN', 'CSS_HIDDEN_TOKEN', 'TEXTAREA_SECRET', 'EDITABLE_SECRET', 'LABEL_SECRET']:
        assert secret not in observation


async def test_query_immediate_clear_cannot_finish_before_loading_cycle(session):
    await session.page.set_content('''<button onclick="runQuery()">查询</button><div id="loading" hidden>加载</div><div id="results">旧结果</div>
      <script>function runQuery(){document.querySelector('#results').textContent='';
      setTimeout(()=>document.querySelector('#loading').hidden=false,300);
      setTimeout(()=>{document.querySelector('#results').textContent='最终结果';document.querySelector('#loading').hidden=true},900)}
      </script>''')
    started = time.monotonic()
    result = await session.execute(action('click', '查询', wait_for_query=True))
    assert result.status == 'success'
    assert time.monotonic() - started >= .8
    assert '最终结果' in (await session.observe()).text
