# Tests for the RetroArch NCI client.
#
# A mock UDP echo server binds on a loopback port and replies to specific
# commands, letting us verify command serialization, response parsing, and
# error handling without a running RetroArch instance.

import asyncio
import socket
import struct
import pytest
from mcp_retroarch.retroarch import RetroArchClient, _parse_memory_reply


# ── _parse_memory_reply unit tests ────────────────────────────────────────

def test_parse_memory_reply_returns_bytes():
    result = _parse_memory_reply("READ_CORE_MEMORY 0x1000 ab cd ef 01", "READ_CORE_MEMORY", 4)
    assert result == bytes([0xAB, 0xCD, 0xEF, 0x01])


def test_parse_memory_reply_failure_shape_raises():
    with pytest.raises(RuntimeError, match="no memory map defined"):
        _parse_memory_reply("READ_CORE_MEMORY 0x1000 -1 no memory map defined", "READ_CORE_MEMORY", 4)


def test_parse_memory_reply_wrong_prefix_raises():
    with pytest.raises(ValueError, match="unexpected reply prefix"):
        _parse_memory_reply("READ_CORE_RAM 0x1000 ab cd", "READ_CORE_MEMORY", 2)


def test_parse_memory_reply_malformed_byte_raises():
    with pytest.raises(ValueError, match="malformed byte"):
        _parse_memory_reply("READ_CORE_MEMORY 0x1000 ab zz", "READ_CORE_MEMORY", 2)


def test_parse_memory_reply_no_bytes_raises():
    with pytest.raises(ValueError, match="returned no bytes"):
        _parse_memory_reply("READ_CORE_MEMORY 0x1000", "READ_CORE_MEMORY", 0)


# ── mock UDP server fixture ───────────────────────────────────────────────

class _MockServer:
    """Simple UDP server that replies to commands based on a dispatch table."""

    def __init__(self, responses: dict[str, bytes | None]) -> None:
        # responses: command prefix → reply bytes (None = no reply / fire-and-forget)
        self._responses = responses
        self._sock: socket.socket | None = None
        self._task: asyncio.Task | None = None
        self.received: list[str] = []
        self.port: int = 0

    async def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.setblocking(False)
        self.port = self._sock.getsockname()[1]
        self._task = asyncio.create_task(self._serve())

    async def _serve(self) -> None:
        loop = asyncio.get_running_loop()
        assert self._sock is not None
        while True:
            try:
                data, addr = await asyncio.wait_for(loop.sock_recvfrom(self._sock, 4096), timeout=0.05)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break
            cmd_str = data.decode().strip()
            self.received.append(cmd_str)
            cmd_name = cmd_str.split()[0]
            reply = self._responses.get(cmd_name)
            if reply is not None:
                await loop.sock_sendto(self._sock, reply, addr)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self._sock:
            self._sock.close()


@pytest.fixture
async def mock_server():
    """Yield a factory for _MockServer; tear down all instances after the test."""
    instances: list[_MockServer] = []

    async def factory(responses: dict[str, bytes | None]) -> _MockServer:
        srv = _MockServer(responses)
        await srv.start()
        instances.append(srv)
        return srv

    yield factory

    for srv in instances:
        await srv.stop()


# ── RetroArchClient integration tests ─────────────────────────────────────

async def test_get_version(mock_server):
    srv = await mock_server({"VERSION": b"1.17.0\n"})
    client = RetroArchClient(host="127.0.0.1", port=srv.port, timeout=2.0)
    try:
        result = await client.get_version()
        assert result == "1.17.0"
    finally:
        client.disconnect()


async def test_get_status_playing(mock_server):
    reply = b"GET_STATUS PLAYING snes,Super Mario World (USA),crc32=A31BEAD4\n"
    srv = await mock_server({"GET_STATUS": reply})
    client = RetroArchClient(host="127.0.0.1", port=srv.port, timeout=2.0)
    try:
        status = await client.get_status()
        assert status.state == "playing"
        assert status.system == "snes"
        assert status.game == "Super Mario World (USA)"
        assert status.crc32 == "A31BEAD4"
    finally:
        client.disconnect()


async def test_get_status_paused(mock_server):
    reply = b"GET_STATUS PAUSED snes,Super Mario World (USA),crc32=A31BEAD4\n"
    srv = await mock_server({"GET_STATUS": reply})
    client = RetroArchClient(host="127.0.0.1", port=srv.port, timeout=2.0)
    try:
        status = await client.get_status()
        assert status.state == "paused"
    finally:
        client.disconnect()


async def test_get_status_contentless(mock_server):
    srv = await mock_server({"GET_STATUS": b"GET_STATUS CONTENTLESS\n"})
    client = RetroArchClient(host="127.0.0.1", port=srv.port, timeout=2.0)
    try:
        status = await client.get_status()
        assert status.state == "contentless"
        assert status.system is None
    finally:
        client.disconnect()


async def test_read_memory_parses_hex_bytes(mock_server):
    reply = b"READ_CORE_MEMORY 0x1000 ab cd ef 01\n"
    srv = await mock_server({"READ_CORE_MEMORY": reply})
    client = RetroArchClient(host="127.0.0.1", port=srv.port, timeout=2.0)
    try:
        result = await client.read_memory(0x1000, 4)
        assert result == bytes([0xAB, 0xCD, 0xEF, 0x01])
    finally:
        client.disconnect()


async def test_read_memory_failure_raises(mock_server):
    reply = b"READ_CORE_MEMORY 0x1000 -1 no memory map defined\n"
    srv = await mock_server({"READ_CORE_MEMORY": reply})
    client = RetroArchClient(host="127.0.0.1", port=srv.port, timeout=2.0)
    try:
        with pytest.raises(RuntimeError, match="no memory map defined"):
            await client.read_memory(0x1000, 4)
    finally:
        client.disconnect()


async def test_write_memory_returns_byte_count(mock_server):
    reply = b"WRITE_CORE_MEMORY 0x1000 3\n"
    srv = await mock_server({"WRITE_CORE_MEMORY": reply})
    client = RetroArchClient(host="127.0.0.1", port=srv.port, timeout=2.0)
    try:
        n = await client.write_memory(0x1000, [0xAA, 0xBB, 0xCC])
        assert n == 3
        assert any("WRITE_CORE_MEMORY" in cmd for cmd in srv.received)
    finally:
        client.disconnect()


async def test_write_memory_failure_raises(mock_server):
    reply = b"WRITE_CORE_MEMORY 0x1000 -1 address out of range\n"
    srv = await mock_server({"WRITE_CORE_MEMORY": reply})
    client = RetroArchClient(host="127.0.0.1", port=srv.port, timeout=2.0)
    try:
        with pytest.raises(RuntimeError, match="address out of range"):
            await client.write_memory(0x1000, [0xAA])
    finally:
        client.disconnect()


async def test_query_timeout_raises(mock_server):
    # No reply for VERSION → should time out
    srv = await mock_server({"VERSION": None})
    client = RetroArchClient(host="127.0.0.1", port=srv.port, timeout=0.1)
    try:
        with pytest.raises(TimeoutError, match="timed out"):
            await client.get_version()
    finally:
        client.disconnect()


async def test_fire_and_forget_send_does_not_block(mock_server):
    # PAUSE_TOGGLE has no reply; send() must return without waiting
    srv = await mock_server({"PAUSE_TOGGLE": None})
    client = RetroArchClient(host="127.0.0.1", port=srv.port, timeout=0.5)
    try:
        await client.pause_toggle()
        # Give the mock a moment to receive, then confirm it arrived
        await asyncio.sleep(0.05)
        assert any("PAUSE_TOGGLE" in cmd for cmd in srv.received)
    finally:
        client.disconnect()


async def test_get_config_param(mock_server):
    reply = b"GET_CONFIG_PARAM savefile_directory /home/user/.config/retroarch/saves\n"
    srv = await mock_server({"GET_CONFIG_PARAM": reply})
    client = RetroArchClient(host="127.0.0.1", port=srv.port, timeout=2.0)
    try:
        value = await client.get_config_param("savefile_directory")
        assert value == "/home/user/.config/retroarch/saves"
    finally:
        client.disconnect()


async def test_read_memory_validates_length():
    client = RetroArchClient()
    with pytest.raises(ValueError, match="positive"):
        await client.read_memory(0x1000, 0)
    with pytest.raises(ValueError, match="4096"):
        await client.read_memory(0x1000, 4097)
