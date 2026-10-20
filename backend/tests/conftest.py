import json
from pydantic import SecretStr
from app.login import LoginController
from app.workflows.models import LoginSpec
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


class FixtureSettings(Settings):
    site_url: str


def set_secret(settings, secret):
    settings.credentials_json = SecretStr(json.dumps({'test': {'origin': settings.site_url, 'username':'operator', 'password':secret}}))
    settings.__dict__.pop('credentials',None)


@pytest_asyncio.fixture
async def session(tmp_path, demo_url):
    settings = FixtureSettings(_env_file=None, site_url=demo_url + '/demo/login', download_dir=tmp_path, database_path=tmp_path / 'agent.sqlite3',
                        headless=True, action_timeout_ms=3000, query_timeout_ms=3000, download_timeout_ms=3000)
    s = BrowserSession(settings)
    await s.start()
    errors = []
    s.page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        settings.credentials_json = SecretStr(json.dumps({'test': {'origin': demo_url, 'username':'operator', 'password':'demo-password'}}))
        spec = LoginSpec(url=settings.site_url, credential_ref='test', username={'kind':'css','value':'#username'},password={'kind':'css','value':'#password'},submit={'kind':'css','value':'#login'},success={'kind':'css','value':'#logged-in'})
        result = await LoginController(s).execute(spec, {}, demo_url)
        s.flow_scope = demo_url
        assert result.status == 'success', (s.page.url, await s.page.locator('body').inner_text(), errors)
        yield s
    finally:
        await s.close()
