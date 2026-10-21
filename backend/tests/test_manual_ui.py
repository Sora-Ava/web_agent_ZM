from playwright.async_api import async_playwright, expect

from app.config import ROOT
from test_ui import local_app


async def test_native_window_control_buttons_and_command_lock(local_app):
    url, model_requests = local_app
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={'width':1520,'height':1100})
        try:
            await page.goto(url)
            await expect(page.locator('.local-status')).to_contain_text('本地服务已连接')
            await page.get_by_role('button', name='启动浏览器', exact=True).click()
            await expect(page.get_by_text('Agent 控制中', exact=True)).to_be_visible()
            await page.get_by_role('button', name='人工接管', exact=True).click()
            await expect(page.get_by_text('人工接管中', exact=True)).to_be_visible()
            blocked = await page.request.post(url+'/api/commands',data={'instruction':'不能执行'})
            assert blocked.status == 409
            await expect(page.locator('.browser-canvas img')).to_be_visible()
            await page.screenshot(path=ROOT/'test-results/manual-control.png', full_page=True)
            await page.get_by_role('button', name='交还 Agent', exact=True).click()
            await expect(page.get_by_text('Agent 控制中', exact=True)).to_be_visible()
            assert not model_requests
            await page.get_by_role('button', name='关闭浏览器', exact=True).click()
            await expect(page.get_by_role('button', name='启动浏览器', exact=True)).to_be_visible()
        finally:
            await browser.close()
