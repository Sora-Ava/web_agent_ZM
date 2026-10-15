import asyncio
from contextlib import asynccontextmanager

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
        self._lifecycle = asyncio.Lock()

    def state(self) -> SessionState:
        url = self.page.url if self.page and not self.page.is_closed() else None
        return SessionState(status='running' if self.busy else self.status, current_url=url)

    @asynccontextmanager
    async def reserve(self):
        if self.busy:
            raise BusyError('浏览器正在执行操作')
        self.busy = True
        try:
            yield
        finally:
            self.busy = False

    async def start(self) -> SessionState:
        async with self._lifecycle:
            if self.page and not self.page.is_closed():
                return self.state()
            self.status = 'starting'
            try:
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(headless=self.settings.headless)
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
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.page = self.context = self.browser = self.playwright = None

    async def close(self):
        async with self._lifecycle:
            await self._dispose()
            self.status = 'closed'
