import pytest
from playwright.async_api import async_playwright, expect

from test_ui import local_app
from workflow_samples import sample_workflow
from app.config import ROOT


@pytest.mark.parametrize('local_app', [True], indirect=True)
async def test_recovery_review_creates_version_after_confirmation(local_app):
    url, requests = local_app
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 1500, 'height': 1120})
        try:
            definition = sample_workflow(url).model_dump()
            definition['steps'][-1]['action']['target']['value'] = '旧导出'
            saved = await (await page.request.post(url + '/api/workflows', data={'definition': definition, 'confirmed': True})).json()
            submitted = await (await page.request.post(url + '/api/tasks', data={'flow_id': saved['id'], 'rows': [{'batch': 'REPAIR_UI'}]})).json()
            await page.goto(url)
            await page.get_by_role('button', name='任务历史', exact=True).click()
            await page.get_by_role('button', name='查看任务 ' + submitted['id'], exact=True).click()
            await expect(page.locator('.task-detail-status')).to_have_text('已完成', timeout=15000)
            await expect(page.get_by_text('LLM 调用：1 次', exact=True)).to_be_visible()
            await expect(page.get_by_role('heading', name='待确认定位修复', exact=True)).to_be_visible()
            await expect(page.locator('.repair-locators')).to_contain_text('旧导出')
            before = await (await page.request.get(url + '/api/workflows/' + saved['id'])).json()
            assert before['version'] == 1
            await expect(page.get_by_role('button', name='确认并保存为新版本', exact=True)).to_be_disabled()
            await page.get_by_label('我已检查定位修复，确认另存流程新版本').check()
            await page.get_by_role('button', name='确认并保存为新版本', exact=True).click()
            await expect(page.get_by_text('已保存为 v2，原版本保持不变', exact=True)).to_be_visible()
            latest = await (await page.request.get(url + '/api/workflows/' + saved['id'])).json()
            old = await (await page.request.get(url + '/api/workflows/' + saved['id'] + '?version=1')).json()
            assert old['definition'] == definition and latest['version'] == 2
            again = await (await page.request.post(url + '/api/tasks', data={'flow_id': saved['id'], 'version': 2, 'rows': [{'batch': 'NORMAL_UI'}]})).json()
            await page.get_by_role('button', name='任务历史', exact=True).click()
            await page.get_by_role('button', name='查看任务 ' + again['id'], exact=True).click()
            await expect(page.locator('.task-detail-status')).to_have_text('已完成')
            await expect(page.get_by_text('LLM 调用：0 次', exact=True)).to_be_visible()
            assert len(requests) == 1
            await page.get_by_role('button', name='任务历史', exact=True).click()
            await page.get_by_role('button', name='查看任务 ' + submitted['id'], exact=True).click()
            await page.screenshot(path=ROOT / 'test-results/recovery-phase4.png', full_page=True)
        finally:
            await browser.close()
