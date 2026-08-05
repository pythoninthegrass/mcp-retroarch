import asyncio
import socket
import pytest


@pytest.fixture
async def udp_capture():
    """Bind an ephemeral UDP socket; yield (port, received_datagrams list).

    Each element of received_datagrams is a (data: bytes, addr: tuple) pair.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    port = sock.getsockname()[1]
    received: list[tuple[bytes, tuple[str, int]]] = []

    loop = asyncio.get_event_loop()
    stop = asyncio.Event()

    async def _reader():
        while not stop.is_set():
            try:
                data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 4096), timeout=0.05)
                received.append((data, addr))
            except asyncio.TimeoutError:
                continue

    task = asyncio.create_task(_reader())
    yield port, received
    stop.set()
    await task
    sock.close()
