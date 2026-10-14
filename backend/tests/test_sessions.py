import asyncio

import pytest

from app.config import Settings
from app.sessions import BrowserSession, BusyError


@pytest.mark.asyncio
async def test_start_close_idempotent(tmp_path):
    session = BrowserSession(Settings(headless=True, download_dir=tmp_path))
    try:
        await session.start()
        page = session.page
        await session.start()
        assert session.page is page
        assert session.state().status == 'ready'
    finally:
        await session.close()
        await session.close()
    assert session.state().status == 'closed'


@pytest.mark.asyncio
async def test_exclusive_reservation_rejects_second_operation(tmp_path):
    session = BrowserSession(Settings(download_dir=tmp_path))
    async with session.reserve():
        with pytest.raises(BusyError):
            async with session.reserve():
                pass
    async with session.reserve():
        pass
