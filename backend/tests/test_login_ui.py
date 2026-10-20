from playwright.async_api import async_playwright,expect
from test_ui import local_app
from app.config import ROOT

async def test_configure_record_save_and_run_explicit_login(local_app):
    url,requests=local_app
    async with async_playwright() as p:
        browser=await p.chromium.launch()
        page=await browser.new_page(viewport={'width':1480,'height':1100})
        try:
            await page.goto(url)
            await expect(page.locator('.local-status')).to_contain_text('本地服务已连接')
            assert await page.get_by_role('button',name='配置并执行登录',exact=True).count()==1
            await page.get_by_role('button',name='启动浏览器',exact=True).click()
            await page.get_by_role('button',name='配置并执行登录',exact=True).click()
            dialog=page.get_by_role('dialog',name='配置并执行登录')
            await dialog.get_by_label('登录网址',exact=True).fill(url+'/demo/login')
            await dialog.get_by_label('凭据引用',exact=True).select_option('test')
            for label,value in [('账号框定位','#username'),('密码框定位','#password'),('提交按钮定位','#login'),('成功标识定位','#logged-in')]:
                await dialog.get_by_label(label,exact=True).fill(value)
            await dialog.get_by_role('button',name='执行登录',exact=True).click()
            await expect(dialog).not_to_be_visible(timeout=15000)
            await expect(page.get_by_text('已记录 1 个成功操作',exact=True)).to_be_visible()
            await page.get_by_role('button',name='保存为标准流程',exact=True).click()
            editor=page.get_by_role('dialog',name='保存标准流程')
            await editor.get_by_label('流程名称',exact=True).fill('显式登录测试')
            await editor.get_by_label('我已检查步骤、参数和条件规则，确认保存').check()
            await editor.get_by_role('button',name='确认保存流程',exact=True).click()
            await page.get_by_role('button',name='运行标准流程',exact=True).click()
            await expect(page.locator('.run-result')).to_have_attribute('data-status','success',timeout=15000)
            await expect(page.locator('.run-result')).to_contain_text('LLM 调用：0 次')
            await page.screenshot(path=ROOT/'test-results/explicit-login.png',full_page=True)
            await page.get_by_role('button',name='批量运行',exact=True).click()
            await page.get_by_role('button',name='加入任务队列',exact=True).click()
            await expect(page.locator('.task-detail-status')).to_have_text('已完成',timeout=15000)
            flows=await (await page.request.get(url+'/api/workflows')).json();identity=flows[0]['id']
            await page.get_by_role('button',name='流程：显式登录测试',exact=True).click()
            await page.get_by_role('button',name='编辑并另存新版本',exact=True).click()
            editor=page.get_by_role('dialog',name='保存标准流程')
            await editor.get_by_label('复制登录的来源流程',exact=True).select_option(identity)
            await editor.get_by_role('button',name='复制登录步骤',exact=True).click()
            await expect(editor.locator('.login-step-editor')).to_have_count(2)
            await editor.get_by_label('我已检查步骤、参数和条件规则，确认保存').check()
            await editor.get_by_role('button',name='确认保存新版本',exact=True).click()
            await expect(page.get_by_label('流程版本',exact=True)).to_have_value('2')
            original=await (await page.request.get(url+'/api/workflows/'+identity+'?version=1')).json()
            latest=await (await page.request.get(url+'/api/workflows/'+identity)).json()
            assert len(original['definition']['steps'])==1
            assert len(latest['definition']['steps'])==2
            assert len({p['key'] for p in latest['definition']['parameters']})==4
            assert requests==[]
        finally:await browser.close()
