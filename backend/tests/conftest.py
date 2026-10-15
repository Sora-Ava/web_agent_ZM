import socket
import threading
import time

import pytest
import pytest_asyncio
import uvicorn

from app.config import Settings
from app.demo import demo_app
from app.sessions import BrowserSession


@pytest.fixture(scope='session')
def demo_url():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(demo_app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError('demo server did not start')
        time.sleep(.02)
    yield f'http://127.0.0.1:{port}'
    server.should_exit = True
    thread.join(timeout=5)


@pytest_asyncio.fixture
async def session(tmp_path, demo_url):
    settings = Settings(_env_file=None, site_url=demo_url + '/demo/login', download_dir=tmp_path,
                        headless=True, action_timeout_ms=3000, query_timeout_ms=3000, download_timeout_ms=3000)
    s = BrowserSession(settings)
    await s.start()
    errors = []
    s.page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        result = await s.login()
        assert result.status == 'success', (s.page.url, await s.page.locator('body').inner_text(), errors)
        yield s
    finally:
        await s.close()
