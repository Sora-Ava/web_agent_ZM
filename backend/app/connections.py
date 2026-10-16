import asyncio
from contextlib import suppress


async def until_disconnected(websocket, produce):
    async def receive():
        while True:
            if (await websocket.receive())['type'] == 'websocket.disconnect':
                return
    reader = asyncio.create_task(receive())
    writer = asyncio.create_task(produce())
    try:
        done, _ = await asyncio.wait((reader, writer), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        reader.cancel()
        writer.cancel()
        await asyncio.gather(reader, writer, return_exceptions=True)
