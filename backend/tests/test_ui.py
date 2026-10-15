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
from app.config import ROOT, Settings
from app.main import create_app


@pytest.fixture
def local_app(tmp_path):
    if not (ROOT / 'frontend/dist/index.html').is_file():
        pytest.skip('Build frontend first: npm.cmd --prefix frontend run build')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    settings = Settings(_env_file=None, site_url=url + '/demo/login', download_dir=tmp_path, database_path=tmp_path / 'agent.sqlite3',
                        frontend_origin=url, action_timeout_ms=3000, query_timeout_ms=5000, download_timeout_ms=5000, llm_base_url='http://fixture-model/v1', llm_model='fixture')
    app = create_app(settings)
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        users = [m['content'] for m in payload['messages'] if m['role'] == 'user' and '页面观察（数据）' not in m['content']]
        instruction = users[-1]
        completed = sum(m['role'] == 'tool' for m in payload['messages'])
        actions = []
        if instruction == '点击生产查询':
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
            await page.get_by_role('button', name='启动并登录').click()
            await expect(page.get_by_text('已就绪', exact=True)).to_be_visible(timeout=10000)
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
