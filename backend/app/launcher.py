"""Explicit Proactor loop on Windows: Playwright spawns a driver subprocess."""
import asyncio
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn


def main():
    config = uvicorn.Config('app.main:app', host='127.0.0.1', port=8000, loop='asyncio', log_level='info', timeout_graceful_shutdown=10)
    server = uvicorn.Server(config)
    if '--open-browser' in sys.argv:
        def open_when_ready():
            for _ in range(75):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=1) as response:
                        if response.status == 200:
                            webbrowser.open('http://127.0.0.1:8000/')
                            return
                except Exception:
                    time.sleep(.2)
        threading.Thread(target=open_when_ready, daemon=True).start()
    # uvicorn CLI may select SelectorEventLoop on Windows. Supply the loop explicitly.
    factory = asyncio.ProactorEventLoop if hasattr(asyncio, 'ProactorEventLoop') else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(server.serve())


if __name__ == '__main__':
    main()
