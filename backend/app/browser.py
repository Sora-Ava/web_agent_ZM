"""Validated browser operations; no model-supplied JavaScript is evaluated."""
import asyncio
import logging
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

from .contracts import ActionResult, BrowserAction, PageObservation

logger = logging.getLogger(__name__)


OBSERVE = r'''() => {
 const excluded = 'script,style,noscript,input,textarea,[contenteditable]:not([contenteditable="false"]),[hidden],[aria-hidden="true"]';
 function visible(el){return el && !el.closest(excluded) && el.getClientRects().length>0 && getComputedStyle(el).visibility!=='hidden'}
 function safeText(root){
   if(!root)return '';
   const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node, texts=[];
   while(node=walker.nextNode()){if(visible(node.parentElement)){const t=node.textContent.trim();if(t)texts.push(t)}}
   return texts.join(' ');
 }
 const text=safeText(document.body);
 const elements=[];
 for(const el of document.querySelectorAll('button,a,input,textarea,select,[role],[contenteditable="true"]')){
   if(el.matches('[type=password],[type=hidden]') || el.closest('[hidden],[aria-hidden="true"]') || !el.getClientRects().length || getComputedStyle(el).visibility==='hidden')continue;
   const tag=el.tagName.toLowerCase();
   const label=safeText(el.labels?.[0]);
   const name=el.getAttribute('aria-label') || label || (['input','textarea'].includes(tag)||el.isContentEditable ? el.getAttribute('placeholder')||'' : safeText(el));
   const role=el.getAttribute('role') || ({button:'button',a:'link',input:'textbox',textarea:'textbox',select:'combobox'}[tag]);
   elements.push({role,name:name.slice(0,200),label:label?.slice(0,200),tag,disabled:!!el.disabled});
 }
 return {text:text.slice(0,16000),elements:elements.slice(0,150)};
}'''


def origin(url: str):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


class BrowserOperations:
    def locator(self, spec):
        if spec.kind == 'role':
            return self.page.get_by_role(spec.role, name=spec.value, exact=True)
        if spec.kind == 'label':
            return self.page.get_by_label(spec.value, exact=True)
        if spec.kind == 'text':
            return self.page.get_by_text(spec.value, exact=True)
        return self.page.locator(spec.value)

    def require_page(self):
        if not self.page or self.page.is_closed():
            raise ValueError('请先启动浏览器并登录')

    async def login(self) -> ActionResult:
        try:
            self.require_page()
            c = self.settings
            await self.page.goto(c.site_url, wait_until='domcontentloaded')
            if await self.page.locator(c.logged_in_selector).is_visible():
                return ActionResult(status='success', message='当前会话已登录')
            await self.page.locator(c.login_username_selector).fill(c.site_username.get_secret_value())
            await self.page.locator(c.login_password_selector).fill(c.site_password.get_secret_value())
            await self.page.locator(c.login_submit_selector).click()
            await self.page.locator(c.logged_in_selector).wait_for(state='visible')
            return ActionResult(status='success', message='登录成功')
        except Exception:
            return ActionResult(status='failed', message='登录失败：请检查网站、凭据和登录定位配置')

    async def observe(self) -> PageObservation:
        self.require_page()
        parsed = urlsplit(self.page.url)
        if origin(self.page.url) != origin(self.settings.site_url):
            raise ValueError('当前页面不属于已配置系统')
        data = await self.page.evaluate(OBSERVE)
        safe = self.settings.scrub(data)
        return PageObservation(url=urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', '')), **safe)

    async def execute(self, action: BrowserAction) -> ActionResult:
        effect_started = False
        try:
            self.require_page()
            if action.kind == 'navigate':
                if origin(action.value) != origin(self.settings.site_url):
                    raise ValueError('只能导航至已配置系统')
                await self.page.goto(action.value, wait_until='domcontentloaded')
                return ActionResult(status='success', message='页面已打开')
            if origin(self.page.url) != origin(self.settings.site_url):
                raise ValueError('当前页面不属于已配置系统')
            loc = self.locator(action.target)
            count = await loc.count()
            if count > 1:
                return ActionResult(status='ambiguous', message='匹配多个元素，请明确目标')
            await loc.wait_for(state='visible')
            if await loc.count() != 1:
                return ActionResult(status='ambiguous', message='目标不唯一，请明确目标')
            # Credential fields are exclusively owned by the direct login routine.
            if await loc.evaluate('(el)=>el.matches("input[type=password],input[type=hidden]")'):
                raise ValueError('敏感字段只能通过登录程序操作')
            if action.kind == 'download':
                async with self.page.expect_download(timeout=self.settings.download_timeout_ms) as pending:
                    effect_started = True
                    await loc.click()
                download = await pending.value
                identity = await asyncio.wait_for(self.downloads.save(download, action.name_parts), self.settings.download_timeout_ms / 1000)
                return ActionResult(status='success', message='文件已导出并验证非空', file_id=identity)
            if action.wait_for_query:
                if action.kind != 'click':
                    raise ValueError('查询等待只适用于点击')
                has_signals = (await self.page.locator(self.settings.result_selector).count() == 1
                               and await self.page.locator(self.settings.loading_selector).count() == 1)
                if not has_signals:
                    # Models may label "enter query page" as a query. Accept only
                    # a new document, not pushState/hash changes or a click alone.
                    previous_url = self.page.url
                    document_loaded = asyncio.Event()
                    def on_document_loaded():
                        document_loaded.set()
                    self.page.on('domcontentloaded', on_document_loaded)
                    try:
                        await loc.click()
                        await asyncio.wait_for(document_loaded.wait(), self.settings.action_timeout_ms / 1000)
                        await self.page.wait_for_url(lambda url: str(url) != previous_url,
                                                     wait_until='domcontentloaded', timeout=self.settings.action_timeout_ms)
                    finally:
                        self.page.remove_listener('domcontentloaded', on_document_loaded)
                    return ActionResult(status='success', message='页面跳转已完成')
                await self.page.evaluate('''selectors => {
                    const result=document.querySelector(selectors.result), loading=document.querySelector(selectors.loading);
                    if(!result || !loading)throw Error('missing query selector');
                    window.__queryObserver?.disconnect();
                    const state=window.__queryState={seen:false,finished:false,changed:false,attempt:selectors.attempt};
                    function check(records){
                        if(records.some(r=>r.type!=='attributes'&&(r.target===result||result.contains(r.target))))state.changed=true;
                        const active=loading.getClientRects().length>0&&getComputedStyle(loading).visibility!=='hidden';
                        if(active){state.seen=true;state.finished=false;}
                        else if(state.seen)state.finished=true;
                    }
                    window.__queryObserver=new MutationObserver(check);
                    window.__queryObserver.observe(document.body,{childList:true,subtree:true,characterData:true,attributes:true});
                }''', {'result': self.settings.result_selector, 'loading': self.settings.loading_selector, 'attempt': uuid4().hex})
            if action.kind == 'click':
                await loc.click()
            elif action.kind == 'fill':
                await loc.fill(action.value)
            elif action.kind == 'select':
                await loc.select_option(action.value)
            elif action.kind == 'wait':
                await loc.wait_for(state='visible')
            if action.wait_for_query:
                try:
                    await self.page.wait_for_function('window.__queryState?.seen && window.__queryState?.finished && window.__queryState?.changed', timeout=self.settings.query_timeout_ms)
                finally:
                    await self.page.evaluate('window.__queryObserver?.disconnect()')
            return ActionResult(status='success', message='操作完成')
        except Exception as exc:
            # Do not return Playwright call logs: they may include filled values.
            reason = self.settings.redact(str(exc).split('Call log:')[0])[:300]
            logger.warning('Browser action failed (%s): %s', type(exc).__name__, reason)
            return ActionResult(status='failed', message=f'操作失败（{type(exc).__name__}），请检查当前页面或定位/等待配置', effect_started=effect_started)
