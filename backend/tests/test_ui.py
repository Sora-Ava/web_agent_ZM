"""End-to-end UI smoke with a deterministic model substitute, not real LLM validation."""
import asyncio
import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from playwright.async_api import async_playwright, expect

from app.agent import StepAgent
from pydantic import SecretStr
from app.login import LoginController
from app.workflows.models import LoginSpec
from app.config import ROOT, Settings
from app.main import create_app
from app.recovery.agent import RecoveryAgent


@pytest.fixture
def local_app(tmp_path, request):
    if not (ROOT / 'frontend/dist/index.html').is_file():
        pytest.skip('Build frontend first: npm.cmd --prefix frontend run build')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    settings = Settings(_env_file=None, site_url=url + '/demo/login', download_dir=tmp_path, database_path=tmp_path / 'agent.sqlite3',
                        frontend_origin=url, action_timeout_ms=3000, query_timeout_ms=5000, download_timeout_ms=5000, llm_base_url='http://fixture-model/v1', llm_model='fixture')
    settings.credentials_json=SecretStr(json.dumps({'test':{'origin':url,'username':'operator','password':'demo-password'}}))
    app = create_app(settings)
    requests = []
    @app.get('/fixture-search')
    async def fixture_search(q: str = ''):
        from fastapi.responses import HTMLResponse
        from html import escape
        return HTMLResponse('<h1>'+escape(q or '测试搜索')+'</h1><form><label>搜索内容<input type="search" name="q"></label><button>搜索</button></form>')
    @app.post("/fixture-login")
    async def fixture_login():
        return (await LoginController(app.state.session).execute(LoginSpec(url=url+'/demo/login',credential_ref='test',username={'kind':'css','value':'#username'},password={'kind':'css','value':'#password'},submit={'kind':'css','value':'#login'},success={'kind':'css','value':'#logged-in'}),{},url)).model_dump()
    fixtures = app.router.routes[-2:]
    del app.router.routes[-2:]
    app.router.routes[0:0] = fixtures

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload['tools'][0]['function']['name'] == 'repair_locator':
            return httpx.Response(200, json={'choices': [{'message': {'tool_calls': [{'function': {'name': 'repair_locator',
                'arguments': json.dumps({'decision': 'retry', 'target': {'kind': 'role', 'role': 'button', 'value': '导出'}, 'reason': '按钮名变化'}, ensure_ascii=False)}}]}}]})
        users = [m['content'] for m in payload['messages'] if m['role'] == 'user' and '页面观察（数据）' not in m['content']]
        instruction = users[-1]
        completed = sum(m['role'] == 'tool' for m in payload['messages'])
        actions = []
        if instruction == '访问测试站并搜索黄金价格':
            actions = [{'kind':'navigate','value':url+'/fixture-search'},
                       {'kind':'fill','target':{'kind':'role','role':'searchbox','value':'搜索内容'},'value':'黄金价格'},
                       {'kind':'press','target':{'kind':'role','role':'searchbox','value':'搜索内容'},'value':'Enter'}]
        elif instruction == '点击生产查询':
            actions = [{'kind': 'click', 'target': {'kind': 'role', 'role': 'button', 'value': '生产查询'}}]
        elif instruction == '输入批次号 PC001 和物料层级 2':
            actions = [{'kind': 'fill', 'target': {'kind': 'label', 'value': '生产批次号'}, 'value': 'PC001'},
                       {'kind': 'select', 'target': {'kind': 'label', 'value': '物料层级'}, 'value': '2'}]
        elif instruction == '点击查询':
            actions = [{'kind': 'click', 'target': {'kind': 'role', 'role': 'button', 'value': '查询'}, 'wait_for_query': True}]
        elif instruction == '导出文件':
            actions = [{'kind': 'download', 'target': {'kind': 'role', 'role': 'button', 'value': '导出'}, 'name_parts': ['PC001', '层级2']}]
        if completed < len(actions):
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{'type': 'function', 'id': f'call_{completed}',
                       'function': {'name': 'browser_action', 'arguments': json.dumps(actions[completed], ensure_ascii=False)}}]}
        else:
            message = {'role': 'assistant', 'content': '本步操作已完成'}
        return httpx.Response(200, json={'choices': [{'message': message}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.state.runner.agent = StepAgent(settings, client=client)
    # Existing UI cases isolate the deterministic executor. Recovery cases opt in
    # explicitly and use the same in-process model transport, never the network.
    app.state.tasks.recovery = RecoveryAgent(settings if getattr(request, 'param', False) else Settings(_env_file=None), client=client)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))

    def run():
        factory = asyncio.ProactorEventLoop if hasattr(asyncio, 'ProactorEventLoop') else asyncio.new_event_loop
        with asyncio.Runner(loop_factory=factory) as runner:
            runner.run(server.serve(sockets=[sock]))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError('local app did not start')
        time.sleep(.02)
    yield url, requests
    server.should_exit = True
    thread.join(timeout=10)


async def test_ui_login_step_commands_live_view_download_and_refresh(local_app):
    url, requests = local_app
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        try:
            await page.goto(url)
            await expect(page.locator('.local-status')).to_contain_text('本地服务已连接')
            await page.get_by_role('button', name='启动浏览器').click()
            await expect(page.get_by_text('已就绪', exact=True)).to_be_visible(timeout=10000)
            assert (await page.request.post(url+'/fixture-login')).ok
            await expect(page.get_by_alt_text('自动化浏览器实时画面')).to_be_visible(timeout=10000)
            for index, instruction in enumerate(['点击生产查询', '输入批次号 PC001 和物料层级 2', '点击查询', '导出文件']):
                await page.get_by_label('下一步操作').fill(instruction)
                await page.get_by_role('button', name='发送指令', exact=True).click()
                await expect(page.locator('.agent-result.success')).to_have_count(index + 1, timeout=15000)
            file_link = page.get_by_role('link', name='下载 PC001', exact=False)
            await expect(file_link).to_be_visible()
            async with page.expect_download() as event:
                await file_link.click()
            download = await event.value
            assert 'PC001' in download.suggested_filename
            assert 'PC001,2,120' in Path(await download.path()).read_text(encoding='utf-8-sig')
            await page.reload()
            await expect(page.locator('.agent-result.success')).to_have_count(4)
            await expect(page.get_by_role('link', name='下载 PC001', exact=False)).to_be_visible()
            (ROOT / 'test-results').mkdir(exist_ok=True)
            await page.screenshot(path=ROOT / 'test-results/workbench.png', full_page=True)
            payload = json.dumps(requests)
            assert 'demo-password' not in payload and 'image_url' not in payload
            await page.get_by_role('button', name='关闭浏览器').click()
            await expect(page.get_by_text('已关闭', exact=True)).to_be_visible()
        finally:
            await browser.close()

async def test_generic_browser_starts_blank_and_executes_search(local_app):
    url, requests=local_app
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={'width':1480,'height':1100})
        try:
            await page.goto(url)
            await expect(page.locator('.local-status')).to_contain_text('本地服务已连接')
            await page.get_by_role('button',name='启动浏览器',exact=True).click()
            await expect(page.locator('.address')).to_contain_text('about:blank')
            await page.get_by_label('下一步操作').fill('访问测试站并搜索黄金价格')
            await page.get_by_role('button',name='发送指令',exact=True).click()
            await expect(page.locator('.agent-result.success')).to_have_count(1,timeout=15000)
            await expect(page.locator('.address')).to_contain_text('/fixture-search?q=')
            await expect(page.get_by_alt_text('自动化浏览器实时画面')).to_be_visible()
            await page.screenshot(path=ROOT/'test-results/generic-browser-step1.png',full_page=True)
            await page.reload()
            await expect(page.locator('.agent-result.success')).to_have_count(1)
        finally:
            await browser.close()
