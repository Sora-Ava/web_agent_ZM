from playwright.async_api import async_playwright, expect

from test_ui import local_app
from workflow_samples import sample_with_login as sample_workflow
from app.config import ROOT


async def test_delete_flow_history_and_recycle_export_with_confirmation(local_app):
    url, _ = local_app
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width':1480,'height':1100})
        try:
            flow = await (await page.request.post(url+'/api/workflows', data={'definition':sample_workflow(url).model_dump(),'confirmed':True})).json()
            submitted = await (await page.request.post(url+'/api/workflows/'+flow['id']+'/runs', data={'parameters':{'batch':'DELETE_UI'}})).json()
            await page.goto(url)
            await page.get_by_role('button',name='环境检查',exact=True).click()
            await expect(page.get_by_role('dialog',name='环境检查')).to_contain_text('Chromium 已安装')
            await page.get_by_role('button',name='关闭检查',exact=True).click()
            await page.get_by_role('button',name='任务历史',exact=True).click()
            await page.get_by_role('button',name='查看任务 '+submitted['id'],exact=True).click()
            await expect(page.locator('.task-detail-status')).to_have_text('已完成',timeout=15000)
            await page.screenshot(path=ROOT/'test-results/management-phase5.png',full_page=True)
            await page.get_by_role('button',name='删除任务记录',exact=True).click()
            await expect(page.get_by_role('dialog',name='确认删除')).to_be_visible()
            await page.get_by_role('button',name='取消删除',exact=True).click()
            assert len(await (await page.request.get(url+'/api/tasks')).json()) == 1
            await page.get_by_role('button',name='删除任务记录',exact=True).click()
            await page.get_by_role('button',name='确认删除',exact=True).click()
            await expect(page.get_by_text('暂无任务，选择标准流程后单次或批量运行。',exact=True)).to_be_visible()
            files = await (await page.request.get(url+'/api/files')).json()
            assert len(files) == 1
            await page.get_by_role('button',name='删除文件 '+files[0]['name'],exact=True).click()
            await expect(page.get_by_role('dialog')).to_contain_text('回收目录')
            await page.get_by_role('button',name='确认删除',exact=True).click()
            await expect(page.locator('.file-row')).to_have_count(0)
            assert (await page.request.get(url+'/api/files/'+files[0]['id']+'/download')).status == 404
            await page.get_by_role('button',name='删除流程 '+flow['definition']['name'],exact=True).click()
            await page.get_by_role('button',name='确认删除',exact=True).click()
            await expect(page.get_by_role('button',name='流程：'+flow['definition']['name'],exact=True)).to_have_count(0)
            await page.reload()
            assert (await (await page.request.get(url+'/api/workflows')).json()) == []
        finally:
            await browser.close()
