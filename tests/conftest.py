import asyncio
from contextlib import asynccontextmanager

from starlette.types import ASGIApp


@asynccontextmanager
async def lifespan_running(app: ASGIApp):
    """Drive an ASGI app's lifespan protocol manually.

    httpx.ASGITransport does not send lifespan events on its own, but the
    streamable-HTTP session manager's task group is only started inside the
    app's lifespan context (see huggingface_mcp.__main__.build_app). Tests
    that exercise /mcp over an ASGI transport need this running for the
    duration of the request(s).
    """
    started = asyncio.Event()
    stopped = asyncio.Event()
    recv_queue: asyncio.Queue = asyncio.Queue()
    await recv_queue.put({"type": "lifespan.startup"})

    async def receive():
        return await recv_queue.get()

    async def send(message):
        if message["type"] == "lifespan.startup.complete":
            started.set()
        elif message["type"] == "lifespan.shutdown.complete":
            stopped.set()

    task = asyncio.create_task(app({"type": "lifespan"}, receive, send))
    await started.wait()
    try:
        yield
    finally:
        await recv_queue.put({"type": "lifespan.shutdown"})
        await stopped.wait()
        await task
