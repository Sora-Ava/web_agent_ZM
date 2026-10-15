"""Explicit Proactor loop on Windows: Playwright spawns a driver subprocess."""
import asyncio

import uvicorn


def main():
    config = uvicorn.Config('app.main:app', host='127.0.0.1', port=8000, loop='asyncio', log_level='info')
    server = uvicorn.Server(config)
    # uvicorn CLI may select SelectorEventLoop on Windows. Supply the loop explicitly.
    factory = asyncio.ProactorEventLoop if hasattr(asyncio, 'ProactorEventLoop') else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(server.serve())


if __name__ == '__main__':
    main()
