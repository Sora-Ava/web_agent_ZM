import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress

from playwright.async_api import async_playwright

from .config import Settings
from .contracts import SessionState
from .browser import BrowserOperations
from .downloads import DownloadStore


class BusyError(Exception):
    pass


class BrowserSession(BrowserOperations):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.downloads = DownloadStore(settings.download_dir)
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None
        self.status = 'idle'
        self.busy = False
        self.authenticated = {}
        self.credential_binding = None
        self.credential_authorization = None
        self.credential_draft_id = None
        self.flow_scope = None
        self._lifecycle = asyncio.Lock()
        self.control_mode = 'agent'
        self.control_revision = 0
        self.control_listener = None
        self._agent_turn = asyncio.Event()
        self._agent_turn.set()

    def state(self) -> SessionState:
        url = self.page.url if self.page and not self.page.is_closed() else None
        return SessionState(status='running' if self.busy else self.status, current_url=url, control_mode=self.control_mode)

    @asynccontextmanager
    async def reserve(self, *, allow_manual=False):
        if self.busy or (not allow_manual and self.control_mode != 'agent'):
            raise BusyError('浏览器正在执行操作')
        self.busy = True
        try:
            yield
        finally:
            self.busy = False
            await self.manual_checkpoint(wait=False)

    async def notify_control(self):
        if self.control_listener:
            await self.control_listener({'type': 'browser_control', 'mode': self.control_mode})

    async def request_takeover(self):
        self.require_page()
        if self.control_mode == 'agent':
            self._agent_turn.clear()
            self.control_mode = 'requesting' if self.busy else 'manual'
            await self.notify_control()

    async def manual_checkpoint(self, *, wait=True):
        if self.control_mode == 'requesting':
            self.control_mode = 'manual'
            await self.notify_control()
        if wait:
            await self._agent_turn.wait()

    async def return_control(self):
        if self.control_mode == 'requesting':
            raise BusyError('请等待当前操作结束，人工接管生效后再交还')
        if self.control_mode == 'manual':
            self.control_revision += 1
            self.authenticated.clear()  # Manual browsing may change the signed-in account.
            self.credential_authorization = None
            self.credential_binding = None
            self.control_mode = 'agent'
            self._agent_turn.set()
            await self.notify_control()

    async def start(self, *, visible=False) -> SessionState:
        async with self._lifecycle:
            if self.page and not self.page.is_closed():
                return self.state()
            self.status = 'starting'
            try:
                await self._dispose()
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(headless=False if visible else self.settings.headless)
                self.context = await self.browser.new_context(accept_downloads=True, viewport={'width': 1280, 'height': 800})
                self.page = await self.context.new_page()
                self.page.set_default_timeout(self.settings.action_timeout_ms)
                self.status = 'ready'
            except Exception:
                await self._dispose()
                self.status = 'failed'
                raise
        return self.state()

    async def _dispose(self):
        self.control_mode = 'agent'
        self._agent_turn.set()
        self.authenticated.clear()
        self.credential_binding = None
        self.credential_authorization = None
        self.credential_draft_id = None
        self.flow_scope = None
        browser, playwright = self.browser, self.playwright
        self.page = self.context = self.browser = self.playwright = None
        if browser:
            with suppress(Exception):
                await asyncio.wait_for(browser.close(), 5)
        if playwright:
            with suppress(Exception):
                await asyncio.wait_for(playwright.stop(), 5)

    async def close(self):
        async with self._lifecycle:
            await self._dispose()
            self.status = 'closed'
