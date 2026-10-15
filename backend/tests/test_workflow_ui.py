from playwright.async_api import async_playwright, expect

from app.config import ROOT
from test_ui import local_app


async def test_record_confirm_save_replay_no_data_and_immutable_versions(local_app):
    url, model_requests = local_app
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 1480, 'height': 1100})
        try:
            await page.goto(url)
            await expect(page.locator('.local-status')).to_contain_text('本地服务已连接')
            await page.get_by_role('button', name='启动并登录').click()
            await expect(page.get_by_text('已就绪', exact=True)).to_be_visible(timeout=10000)
            for index, instruction in enumerate(['点击生产查询', '输入批次号 PC001 和物料层级 2', '点击查询', '导出文件']):
                await page.get_by_label('下一步操作').fill(instruction)
                await page.get_by_role('button', name='发送指令', exact=True).click()
                await expect(page.locator('.agent-result.success')).to_have_count(index + 1, timeout=15000)
            await page.get_by_role('button', name='保存为标准流程').click()
            await expect(page.get_by_role('dialog', name='保存标准流程')).to_be_visible()
            await page.get_by_label('流程名称', exact=True).fill('生产导出')
            await expect(page.get_by_role('button', name='确认保存流程', exact=True)).to_be_disabled()
            await page.get_by_label('我已检查步骤、参数和条件规则，确认保存').check()
            await page.get_by_role('button', name='确认保存流程', exact=True).click()
            await expect(page.get_by_role('heading', name='生产导出', exact=True)).to_be_visible()
            await page.get_by_label('生产批次号', exact=True).fill('PC002')
            await page.get_by_label('物料层级', exact=True).fill('1')
            before_model_calls = len(model_requests)
            await page.get_by_role('button', name='运行标准流程', exact=True).click()
            await expect(page.locator('.run-result')).to_have_attribute('data-status', 'success', timeout=15000)
            await expect(page.get_by_text('LLM 调用：0 次', exact=True)).to_be_visible()
            state = await (await page.request.get(url + '/api/session')).json()
            identity = state['run']['flow_id']
            file_id = state['run']['result']['file_ids'][0]
            downloaded = await page.request.get(url + '/api/files/' + file_id + '/download')
            assert 'PC002,1,120' in (await downloaded.body()).decode('utf-8-sig')
            assert len(model_requests) == before_model_calls
            (ROOT / 'test-results').mkdir(exist_ok=True)
            await page.screenshot(path=ROOT / 'test-results/workflows-phase2.png', full_page=True)
            files_before = len(state['files'])
            await page.get_by_label('生产批次号', exact=True).fill('NONE')
            await page.get_by_role('button', name='运行标准流程', exact=True).click()
            await expect(page.locator('.run-result')).to_have_attribute('data-status', 'no_data', timeout=15000)
            state = await (await page.request.get(url + '/api/session')).json()
            assert len(state['files']) == files_before
            assert len(model_requests) == before_model_calls
            await page.get_by_role('button', name='编辑并另存新版本').click()
            await page.get_by_label('流程名称', exact=True).fill('生产导出 v2')
            await page.get_by_label('输入来源 3', exact=True).select_option('fixed')
            await page.get_by_role('button', name='删除参数 2', exact=True).click()
            await page.get_by_label('我已检查步骤、参数和条件规则，确认保存').check()
            await page.get_by_role('button', name='确认保存新版本', exact=True).click()
            await expect(page.get_by_label('流程版本')).to_have_value('2')
            await expect(page.locator('.run-step.success')).to_have_count(0)
            await expect(page.locator('.run-result')).to_have_count(0)
            old = await (await page.request.get(url + f'/api/workflows/{identity}?version=1')).json()
            latest = await (await page.request.get(url + f'/api/workflows/{identity}')).json()
            assert old['definition']['name'] == '生产导出'
            assert len(old['definition']['parameters']) == 2
            assert latest['version'] == 2 and len(latest['definition']['parameters']) == 1
            await page.get_by_label('流程版本').select_option('1')
            await expect(page.get_by_role('heading', name='生产导出', exact=True)).to_be_visible()
            await page.reload()
            await page.get_by_role('button', name='流程：生产导出 v2', exact=True).click()
            await expect(page.get_by_label('流程版本')).to_have_value('2')
            await expect(page.get_by_label('生产批次号', exact=True)).to_be_visible()
            assert await page.get_by_label('物料层级', exact=True).count() == 0
        finally:
            await browser.close()
