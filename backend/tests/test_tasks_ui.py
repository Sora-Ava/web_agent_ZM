import asyncio

from playwright.async_api import async_playwright, expect

from app.config import ROOT
from test_ui import local_app
from workflow_samples import sample_workflow
from app.tasks.repository import TaskRepository
from app.workflows.repository import WorkflowRepository
from app.workflows.models import site_key


async def test_paste_batch_queue_mixed_results_and_history(local_app):
    url, model_requests = local_app
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 1520, 'height': 1120})
        try:
            flow = sample_workflow(url).model_dump()
            saved = await page.request.post(url + '/api/workflows', data={'definition': flow, 'confirmed': True})
            assert saved.status == 201
            # An invalid select option is a single-row browser failure, not a validation failure.
            await page.goto(url)
            await expect(page.locator('.local-status')).to_contain_text('本地服务已连接')
            await page.get_by_role('button', name='启动并登录', exact=True).click()
            await expect(page.get_by_text('已就绪', exact=True)).to_be_visible()
            await page.get_by_role('button', name='流程：' + flow['name'], exact=True).click()
            await page.get_by_role('button', name='批量运行', exact=True).click()
            await page.get_by_label('粘贴参数表格').fill('000P3\t2\nNONE\t1\nBAD\tinvalid\nAFTER\t1\n000P3\t2')
            await page.get_by_role('button', name='应用粘贴内容', exact=True).click()
            await expect(page.get_by_label('第 1 行 生产批次号', exact=True)).to_have_value('000P3')
            await page.get_by_role('button', name='加入任务队列', exact=True).click()
            await expect(page.locator('.task-detail-status')).to_have_text('排队中')
            await page.get_by_role('button', name='结束编排并启动队列', exact=True).click()
            await expect(page.locator('.task-detail-status')).to_have_text('已完成', timeout=30000)
            await expect(page.locator('.task-row[data-status="success"]')).to_have_count(3)
            await expect(page.locator('.task-row[data-status="no_data"]')).to_have_count(1)
            await expect(page.locator('.task-row[data-status="failed"]')).to_have_count(1)
            tasks = await (await page.request.get(url + '/api/tasks')).json()
            identity = tasks[0]['id']
            task = await (await page.request.get(url + '/api/tasks/' + identity)).json()
            assert task['version'] == 1 and task['model_calls'] == 0
            files = [row['result']['file_ids'][0] for row in task['rows'] if row['status'] == 'success']
            assert len(set(files)) == 3 and not model_requests
            content = await page.request.get(url + '/api/files/' + files[0] + '/download')
            assert '000P3,2,120' in (await content.body()).decode('utf-8-sig')
            await page.reload()
            await page.get_by_role('button', name='任务历史', exact=True).click()
            await page.get_by_role('button', name='查看任务 ' + identity, exact=True).click()
            await expect(page.locator('.task-detail-status')).to_have_text('已完成')
            await expect(page.get_by_role('link', name='下载第 1 行文件')).to_be_visible()
            await page.get_by_role('button', name='第 3 行步骤', exact=True).click()
            await expect(page.locator('.task-steps')).to_contain_text('失败')
            await page.screenshot(path=ROOT / 'test-results/tasks-phase3.png', full_page=True)
        finally:
            await browser.close()


async def test_task_controls_visible_and_effective(local_app):
    url, requests = local_app
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            flow = sample_workflow(url).model_dump()
            flow['steps'] = [{'id': 'slow', 'name': '等待测试元素', 'action': {'kind': 'wait', 'target': {'kind': 'text', 'value': '不存在的测试元素'}}}]
            saved = await (await page.request.post(url + '/api/workflows', data={'definition': flow, 'confirmed': True})).json()
            submitted = await (await page.request.post(url + '/api/tasks', data={'flow_id': saved['id'], 'rows': [{'batch': 'P1'}, {'batch': 'P2'}]})).json()
            await page.goto(url)
            await page.get_by_role('button', name='任务历史', exact=True).click()
            await page.get_by_role('button', name='查看任务 ' + submitted['id'], exact=True).click()
            await page.get_by_role('button', name='暂停任务', exact=True).click()
            await expect(page.locator('.task-detail-status')).to_have_text('已暂停', timeout=10000)
            await expect(page.get_by_role('button', name='关闭浏览器', exact=True)).to_be_disabled()
            await page.get_by_role('button', name='继续任务', exact=True).click()
            await expect(page.get_by_role('button', name='停止任务', exact=True)).to_be_enabled()
            await page.get_by_role('button', name='停止任务', exact=True).click()
            await expect(page.locator('.task-detail-status')).to_have_text('已停止', timeout=10000)
            assert not requests
        finally:
            await browser.close()


async def test_interrupted_row_keeps_download_link_without_final_result(local_app, tmp_path):
    url, _ = local_app
    flows = WorkflowRepository(tmp_path / 'agent.sqlite3', site_key(url))
    flow = flows.create(sample_workflow(url), True)
    repo = TaskRepository(flows)
    task = repo.create(flow, [{'batch': 'PARTIAL', 'level': '1'}])
    repo.update_task(task['id'], 'paused')
    repo.update_row(task['id'], 0, status='running')
    path = tmp_path / 'partial.csv'
    path.write_bytes(b'PARTIAL,1,120')
    repo.register_file({'id': 'partial', 'name': path.name, 'path': str(path), 'original_name': 'production.csv',
        'size': path.stat().st_size, 'task_id': task['id'], 'row_index': 0})
    repo.interrupt_unfinished()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(url)
            await page.get_by_role('button', name='任务历史', exact=True).click()
            await page.get_by_role('button', name='查看任务 ' + task['id'], exact=True).click()
            await expect(page.locator('.task-detail-status')).to_have_text('已中断')
            await expect(page.get_by_role('link', name='下载第 1 行文件')).to_have_attribute('href', '/api/files/partial/download')
        finally:
            await browser.close()
