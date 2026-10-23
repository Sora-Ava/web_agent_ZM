"""Synthetic natural-language recording through the real UI, then offline restart."""
import asyncio
import json
import threading
import time

import pytest
import uvicorn
from playwright.async_api import async_playwright, expect

from app.config import ROOT, Settings
from app.main import create_app
from test_ui import local_app


@pytest.mark.parametrize('local_app', ['natural'], indirect=True)
async def test_natural_login_confirm_save_restart_and_offline_batch(local_app, tmp_path):
    url, requests = local_app
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width':1520,'height':1120})
        restarted = None
        try:
            await page.goto(url)
            await expect(page.locator('.local-status')).to_contain_text('本地服务已连接')
            await page.get_by_role('button',name='启动浏览器',exact=True).click()
            await expect(page.get_by_text('已就绪',exact=True)).to_be_visible()
            async def command(text, count):
                await page.get_by_label('下一步操作').fill(text)
                await page.get_by_role('button',name='发送指令',exact=True).click()
                await expect(page.get_by_label('下一步操作')).to_have_value('')
                try:
                    await expect(page.locator('.agent-result.success')).to_have_count(count,timeout=15000)
                except AssertionError:
                    raise AssertionError(await page.locator('.agent-result').all_text_contents()) from None
            for count, text in enumerate(['打开合成登录页','账号 "a"','密码 「synthetic"p\\ss<雪>」','点击登录'],1):
                await command(text,count)
            await expect(page.locator('.agent-result.success').last).to_contain_text('结果待确认')
            await page.get_by_role('button',name='保存为标准流程').click()
            await expect(page.get_by_role('dialog',name='确认登录成功标识')).to_be_visible()
            await page.get_by_label('登录成功标识',exact=True).select_option('0')
            await page.get_by_label('我确认此标识表示登录后的业务页面').check()
            await page.get_by_role('button',name='确认成功标识',exact=True).click()
            await expect(page.get_by_role('dialog',name='保存标准流程')).to_be_visible()
            await page.get_by_role('button',name='取消',exact=True).click()
            for count,text in enumerate(['点击生产查询','输入批次号 PC001 和物料层级 2','点击查询','导出文件'],5):
                await command(text,count)
            await page.get_by_role('button',name='保存为标准流程').click()
            await page.get_by_label('流程名称',exact=True).fill('自然语言登录验收')
            await page.get_by_label('我已检查步骤、参数和条件规则，确认保存').check()
            await page.get_by_role('button',name='确认保存流程',exact=True).click()
            await expect(page.get_by_role('heading',name='自然语言登录验收',exact=True)).to_be_visible()
            payloads = json.dumps(requests,ensure_ascii=False)
            assert 'synthetic' not in payloads
            assert 'synthetic"p\\ss<雪>'.encode() not in requests.settings.database_path.read_bytes()
            def leaves(value):
                if isinstance(value,str):
                    yield value
                elif isinstance(value,dict):
                    for child in value.values():
                        yield from leaves(child)
                elif isinstance(value,list):
                    for child in value:
                        yield from leaves(child)
            with requests.app.state.repository.connection() as db:
                persisted = [json.loads(row[0]) for row in db.execute('SELECT action FROM draft_steps')]
                persisted += [json.loads(row[0]) for row in db.execute('SELECT definition FROM workflow_versions')]
            assert 'a' not in list(leaves(persisted))
            before = len(requests)
            # Stop the original uvicorn lifespan, including browser, and create a fresh app.
            requests.server.should_exit = True
            await asyncio.to_thread(requests.thread.join,10)
            assert not requests.thread.is_alive()
            settings = Settings(_env_file=None,database_path=requests.settings.database_path,
                download_dir=tmp_path,headless=True,frontend_origin=url,action_timeout_ms=3000,
                query_timeout_ms=5000,download_timeout_ms=5000)
            app = create_app(settings)  # No model URL/name/client is configured.
            # Reuse the stateless synthetic site's auth contract, not application state.
            app.router.routes.insert(0,next(route for route in requests.app.router.routes
                if getattr(route,'path',None)=='/demo/auth' and route.endpoint.__name__=='natural_auth'))
            server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=int(url.rsplit(':',1)[1]),log_level='error'))
            def serve():
                with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
                    runner.run(server.serve())
            thread = threading.Thread(target=serve,daemon=True)
            thread.start()
            restarted = server,thread
            deadline = time.monotonic()+10
            while not server.started:
                assert time.monotonic()<deadline
                await asyncio.sleep(.02)
            await page.reload()
            await expect(page.locator('.local-status')).to_contain_text('本地服务已连接')
            await page.get_by_role('button',name='启动浏览器',exact=True).click()
            await expect(page.get_by_text('已就绪',exact=True)).to_be_visible()
            await page.get_by_role('button',name='流程：自然语言登录验收',exact=True).click()
            await page.get_by_role('button',name='批量运行',exact=True).click()
            flows = await (await page.request.get(url+'/api/workflows')).json()
            flow = await (await page.request.get(url+'/api/workflows/'+flows[0]['id'])).json()
            parameters = flow['definition']['parameters']
            rows = []
            for batch,level in [('PC002','1'),('NONE','2'),('PC003','3')]:
                rows.append('\t'.join(batch if parameter['label']=='生产批次号' else
                    level if parameter['label']=='物料层级' else parameter['default'] for parameter in parameters))
            await page.get_by_label('粘贴参数表格').fill('\n'.join(rows))
            await page.get_by_role('button',name='应用粘贴内容',exact=True).click()
            await expect(page.get_by_label('第 3 行 生产批次号',exact=True)).to_have_value('PC003')
            await page.get_by_role('button',name='加入任务队列',exact=True).click()
            await page.get_by_role('button',name='结束编排并启动队列',exact=True).click()
            await expect(page.locator('.task-detail-status')).to_have_text('已完成',timeout=30000)
            tasks = await (await page.request.get(url+'/api/tasks')).json()
            task = await (await page.request.get(url+'/api/tasks/'+tasks[0]['id'])).json()
            assert [row['status'] for row in task['rows']] == ['success','no_data','success']
            assert task['model_calls'] == 0 and len(requests)==before
            files = [row['result']['file_ids'][0] for row in task['rows'] if row['status']=='success']
            assert len(set(files)) == 2
            for identity,batch in zip(files,['PC002','PC003']):
                result = await page.request.get(url+'/api/files/'+identity+'/download')
                assert batch in (await result.body()).decode('utf-8-sig')
            (ROOT/'test-results').mkdir(exist_ok=True)
            await page.screenshot(path=ROOT/'test-results/natural-login-offline-batch.png',full_page=True)
        finally:
            await browser.close()
            if restarted:
                restarted[0].should_exit=True
                await asyncio.to_thread(restarted[1].join,10)
