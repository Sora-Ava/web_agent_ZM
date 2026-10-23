import asyncio
from playwright.async_api import async_playwright, expect

from test_ui import local_app


async def test_staged_marker_confirmation_then_regular_editor(local_app):
    url, _ = local_app
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width':1480,'height':1100})
        try:
            await page.goto(url)
            await expect(page.locator('.local-status')).to_contain_text('本地服务已连接')
            await page.get_by_role('button', name='启动浏览器', exact=True).click()
            await expect(page.get_by_text('已就绪', exact=True)).to_be_visible()
            await page.get_by_label('下一步操作').fill('访问测试站并搜索黄金价格')
            await page.get_by_role('button', name='发送指令', exact=True).click()
            await expect(page.locator('.agent-result.success')).to_have_count(1)
            proposal = await (await page.request.post(url+'/api/recording/proposal',data={'use_model':False})).json()
            pending = {**proposal, 'definition':None, 'login_confirmation':{'message':'凭据已保存到本机；请确认登录成功标识', 'attempts':[{
                'id':'1','status':'awaiting_confirmation','fields':['username','password','submit'],'origin':url,
                'candidates':[{'label':'业务首页','target':{'kind':'role','role':'link','value':'业务首页'}}]}]}}
            await page.route('**/api/recording/proposal', lambda route: route.fulfill(json=pending))
            confirmations = []
            async def confirm(route):
                confirmations.append(route.request.post_data_json)
                await route.fulfill(json=proposal)
            await page.route('**/api/recording/confirm-login', confirm)
            await page.get_by_role('button', name='保存为标准流程').click()
            await expect(page.get_by_role('dialog', name='确认登录成功标识')).to_be_visible()
            await expect(page.get_by_text('凭据已保存到本机；请确认登录成功标识', exact=True)).to_be_visible()
            await expect(page.get_by_role('button', name='确认成功标识')).to_be_disabled()
            await page.get_by_label('登录成功标识', exact=True).select_option('0')
            await page.get_by_label('我确认此标识表示登录后的业务页面').check()
            await page.get_by_role('button', name='确认成功标识').click()
            await expect(page.get_by_role('dialog', name='保存标准流程')).to_be_visible()
            assert confirmations[0]['confirmed'] is True
            assert confirmations[0]['target']['value'] == '业务首页'
        finally:
            await browser.close()


async def test_command_input_clears_before_network_response(local_app):
    url, _ = local_app
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(url)
            await expect(page.locator('.local-status')).to_contain_text('本地服务已连接')
            await page.get_by_role('button', name='启动浏览器', exact=True).click()
            await expect(page.get_by_text('已就绪', exact=True)).to_be_visible()
            release = asyncio.Event()
            async def delayed(route):
                await release.wait()
                await route.fulfill(json={'id':'synthetic'})
            await page.route('**/api/commands', delayed)
            await page.get_by_label('下一步操作').fill('密码 "synthetic secret"')
            await page.get_by_role('button', name='发送指令', exact=True).click()
            try:
                await expect(page.get_by_label('下一步操作')).to_have_value('', timeout=1000)
                assert await page.evaluate('Object.keys(localStorage).length + Object.keys(sessionStorage).length') == 0
            finally:
                release.set()
        finally:
            await browser.close()
