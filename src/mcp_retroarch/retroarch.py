# RetroArch Network Control Interface (NCI) client
# ─────────────────────────────────────────────────
# Wire format (text-based UDP):
#   Send:    "COMMAND arg1 arg2 ...\n" as a single UDP datagram
#   Receive: One UDP datagram per response, plain text. Most responses echo
#            the command name as a prefix (e.g. "READ_CORE_MEMORY 0x1000 ab cd ef").
#            VERSION is the only one with no echo — the response is just the
#            version string itself.
#
# Default port: 55355 (UDP). Enable in retroarch.cfg with:
#   network_cmd_enable = "true"
#   network_cmd_port = "55355"
# (Or via Settings > Network > Network Commands.)
#
# Concurrency model: this client serializes — exactly one query in flight at
# a time. UDP responses don't carry request IDs, and matching by command-name
# echo is fragile (VERSION doesn't echo at all, two reads to different
# addresses look indistinguishable to a naive matcher). Serial keeps the
# implementation honest and small. Loopback latency makes this fine.

import asyncio
import re
from dataclasses import dataclass
from typing import Literal


@dataclass
class EmuStatus:
    state: Literal["playing", "paused", "contentless"]
    system: str | None = None
    game: str | None = None
    crc32: str | None = None


class _RetroArchProtocol(asyncio.DatagramProtocol):
    """asyncio DatagramProtocol that forwards each received datagram to a Future."""

    def __init__(self) -> None:
        self._transport: asyncio.DatagramTransport | None = None
        self._response_future: asyncio.Future[bytes] | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        fut = self._response_future
        if fut is None or fut.done():
            return  # unsolicited or late reply — drop
        self._response_future = None
        fut.set_result(data)

    def error_received(self, exc: Exception) -> None:
        fut = self._response_future
        if fut is not None and not fut.done():
            self._response_future = None
            fut.set_exception(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        fut = self._response_future
        if fut is not None and not fut.done():
            self._response_future = None
            fut.set_exception(exc or ConnectionResetError("UDP transport closed"))


class RetroArchClient:
    """Async UDP client for RetroArch's Network Control Interface (NCI)."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 55355,
        timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _RetroArchProtocol | None = None
        self._lock = asyncio.Lock()

    def describe_target(self) -> str:
        return f"udp {self.host}:{self.port}"

    async def connect(self) -> None:
        """Open a UDP endpoint. Idempotent."""
        if self._transport is not None:
            return
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            _RetroArchProtocol,
            remote_addr=(self.host, self.port),
        )
        self._transport = transport  # type: ignore[assignment]
        self._protocol = protocol  # type: ignore[assignment]

    def disconnect(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            self._protocol = None

    async def send(self, command: str) -> None:
        """Fire-and-forget send. Use for hotkey-style commands (PAUSE_TOGGLE, etc.)."""
        if self._transport is None:
            await self.connect()
        self._transport.sendto(command.encode())  # type: ignore[union-attr]

    async def query(self, command: str) -> bytes:
        """Send and await one UDP response.

        Serial: acquires a lock so only one query is in flight at a time.
        Times out per self.timeout.
        """
        if self._transport is None:
            await self.connect()
        async with self._lock:
            loop = asyncio.get_running_loop()
            fut: asyncio.Future[bytes] = loop.create_future()
            self._protocol._response_future = fut  # type: ignore[union-attr]
            self._transport.sendto(command.encode())  # type: ignore[union-attr]
            try:
                return await asyncio.wait_for(fut, timeout=self.timeout)
            except asyncio.TimeoutError:
                self._protocol._response_future = None  # type: ignore[union-attr]
                cmd_name = command.split()[0]
                raise TimeoutError(
                    f'RetroArch query "{cmd_name}" timed out after {self.timeout}s '
                    f"— is RetroArch running with Network Commands enabled?"
                ) from None

    # ── High-level commands ────────────────────────────────────────────────

    async def get_version(self) -> str:
        r = await self.query("VERSION")
        return r.decode().strip()

    async def get_status(self) -> EmuStatus:
        r = (await self.query("GET_STATUS")).decode().strip()
        # "GET_STATUS PAUSED system_id,game_basename,crc32=XXXXXXXX"
        # "GET_STATUS PLAYING ..."
        # "GET_STATUS CONTENTLESS"
        m = re.match(r"^GET_STATUS\s+(\w+)(?:\s+([^,]+),(.+?)(?:,crc32=([0-9a-fA-F]+))?)?$", r)
        if not m:
            raise ValueError(f"unexpected GET_STATUS reply: {r}")
        state = m.group(1).lower()
        if state == "contentless":
            return EmuStatus(state="contentless")
        if state not in ("playing", "paused"):
            raise ValueError(f"unexpected emulator state: {state}")
        return EmuStatus(
            state=state,  # type: ignore[arg-type]
            system=m.group(2) or "(unknown)",
            game=m.group(3) or "(unknown)",
            crc32=m.group(4),
        )

    async def read_memory(self, addr: int, length: int) -> bytes:
        """Memory read via libretro's system memory map (preferred)."""
        if length <= 0:
            raise ValueError("length must be positive")
        if length > 4096:
            raise ValueError("length exceeds 4096 byte limit")
        cmd = f"READ_CORE_MEMORY 0x{addr:x} {length}"
        r = (await self.query(cmd)).decode().strip()
        return _parse_memory_reply(r, "READ_CORE_MEMORY", length)

    async def read_ram(self, addr: int, length: int) -> bytes:
        """Memory read via the achievements (CHEEVOS) address space."""
        if length <= 0:
            raise ValueError("length must be positive")
        if length > 4096:
            raise ValueError("length exceeds 4096 byte limit")
        cmd = f"READ_CORE_RAM 0x{addr:x} {length}"
        r = (await self.query(cmd)).decode().strip()
        return _parse_memory_reply(r, "READ_CORE_RAM", length)

    async def write_memory(self, addr: int, data: bytes | list[int]) -> int:
        """Memory write via libretro's system memory map (preferred)."""
        if len(data) == 0:
            raise ValueError("at least one byte required")
        if len(data) > 4096:
            raise ValueError("byte count exceeds 4096 limit")
        hex_bytes = " ".join(f"{b:02x}" for b in data)
        cmd = f"WRITE_CORE_MEMORY 0x{addr:x} {hex_bytes}"
        r = (await self.query(cmd)).decode().strip()
        # "WRITE_CORE_MEMORY <addr> <bytes_written>" or "<addr> -1 <error>"
        m = re.match(r"^WRITE_CORE_MEMORY\s+\S+\s+(-?\d+)(?:\s+(.+))?$", r)
        if not m:
            raise ValueError(f"unexpected WRITE_CORE_MEMORY reply: {r}")
        n = int(m.group(1))
        if n < 0:
            raise RuntimeError(f"WRITE_CORE_MEMORY failed: {m.group(2) or '(no error message)'}")
        return n

    async def write_ram(self, addr: int, data: bytes | list[int]) -> None:
        """Memory write via CHEEVOS address space. No reply — fire-and-forget."""
        if len(data) == 0:
            raise ValueError("at least one byte required")
        if len(data) > 4096:
            raise ValueError("byte count exceeds 4096 limit")
        hex_bytes = " ".join(f"{b:02x}" for b in data)
        await self.send(f"WRITE_CORE_RAM 0x{addr:x} {hex_bytes}")

    # ── Emulator control (all fire-and-forget) ─────────────────────────────

    async def pause_toggle(self) -> None:
        await self.send("PAUSE_TOGGLE")

    async def frame_advance(self) -> None:
        await self.send("FRAMEADVANCE")

    async def reset(self) -> None:
        await self.send("RESET")

    async def screenshot(self) -> None:
        await self.send("SCREENSHOT")

    async def show_message(self, msg: str) -> None:
        await self.send(f"SHOW_MSG {msg}")

    # ── Save state ─────────────────────────────────────────────────────────
    # RetroArch's NCI exposes:
    #   * SAVE_STATE         — save to currently-selected slot (no reply)
    #   * LOAD_STATE         — load from currently-selected slot (no reply)
    #   * LOAD_STATE_SLOT N  — load from explicit slot (echoes command)
    #   * STATE_SLOT_PLUS / STATE_SLOT_MINUS — change current slot
    # There's NO "save_state_slot N" or "get_current_slot" — saving to a
    # specific slot requires walking the slot pointer to it first.

    async def save_state_current(self) -> None:
        await self.send("SAVE_STATE")

    async def load_state_current(self) -> None:
        await self.send("LOAD_STATE")

    async def load_state_slot(self, slot: int) -> None:
        await self.query(f"LOAD_STATE_SLOT {slot}")

    async def state_slot_plus(self) -> None:
        await self.send("STATE_SLOT_PLUS")

    async def state_slot_minus(self) -> None:
        await self.send("STATE_SLOT_MINUS")

    # ── Config / paths ──────────────────────────────────────────────────────

    async def get_config_param(self, name: str) -> str:
        """GET_CONFIG_PARAM — query select RetroArch config values by name."""
        r = (await self.query(f"GET_CONFIG_PARAM {name}")).decode().strip()
        # "GET_CONFIG_PARAM <name> <value>"
        prefix = f"GET_CONFIG_PARAM {name} "
        if not r.startswith(prefix):
            raise ValueError(f"unexpected GET_CONFIG_PARAM reply: {r}")
        return r[len(prefix):]


def _parse_memory_reply(reply: str, expected_cmd: str, expected_len: int) -> bytes:
    """Parse a "READ_CORE_MEMORY <addr> b1 b2 b3 ..." (or READ_CORE_RAM) reply.

    Returns a bytes object. Raises on the documented "-1 <error>" failure shape.
    """
    tokens = reply.split()
    if not tokens or tokens[0] != expected_cmd:
        raise ValueError(f'unexpected reply prefix (got "{tokens[0] if tokens else ""}", expected "{expected_cmd}")')
    tail = tokens[2:]  # skip cmd and addr
    if not tail:
        raise ValueError(f"{expected_cmd} returned no bytes")
    # Failure shape: "<cmd> <addr> -1 <error_msg>"
    if tail[0] == "-1":
        err = " ".join(tail[1:]) or "(no error message)"
        raise RuntimeError(f"{expected_cmd} failed: {err}")
    out = bytearray()
    for i, token in enumerate(tail):
        try:
            v = int(token, 16)
        except ValueError:
            raise ValueError(f"{expected_cmd}: malformed byte at index {i}: {token!r}") from None
        if v < 0 or v > 255:
            raise ValueError(f"{expected_cmd}: malformed byte at index {i}: {token!r}")
        out.append(v)
    # Not strictly an error if short — RetroArch may clamp at memory-region boundaries.
    # Caller can decide what to do with a short read.
    return bytes(out)
