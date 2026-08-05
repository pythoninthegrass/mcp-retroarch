# RetroArch Network RetroPad sender
# ──────────────────────────────────
# Talks to RetroArch's Network Gamepad receiver (Settings > Input > Network
# Gamepad, `network_remote_enable = true`), which binds one UDP socket per
# enabled player at `network_remote_base_port + player` (default 55400+).
#
# Wire format (from RetroArch input/input_driver.h, struct remote_message):
#
#   struct remote_message {
#     int      port;    // offset  0 — player index; receiver derives the user
#                       //             from the socket, but we mirror it anyway
#     int      device;  // offset  4 — RETRO_DEVICE_JOYPAD (1) or _ANALOG (2)
#     int      index;   // offset  8 — analog stick: 0 = left, 1 = right
#     int      id;      // offset 12 — button id (0-15) or axis (0 = X, 1 = Y)
#     uint16_t state;   // offset 16 — 1/0 for buttons, int16 for analog axes
#   };                  // sizeof == 20 (2 trailing pad bytes)
#
# The receiver (input/input_driver.c) only parses datagrams whose size is
# exactly sizeof(struct remote_message) and reads the struct with the host's
# native layout — little-endian on every platform RetroArch ships on. Two
# receiver behaviors matter to callers:
#
#   1. LATCHING — a button stays pressed until an explicit state=0 message
#      arrives. There is no per-frame keepalive.
#   2. ONE DATAGRAM PER FRAME — the receiver consumes at most one queued
#      datagram per emulated frame per player. Two changes sent back-to-back
#      apply on two consecutive frames, not the same one. For frame-accurate
#      scripting, interleave sends with FRAMEADVANCE while paused.
#
# A datagram of any OTHER size makes the receiver zero all buttons and axes
# for that player — release_all() uses that deliberately as a single-packet
# panic reset.
#
# There is no handshake and no acknowledgement; this is fire-and-forget UDP,
# same as the NCI command channel.

import asyncio
import socket
import struct

RETRO_DEVICE_JOYPAD = 1
RETRO_DEVICE_ANALOG = 2

# libretro RetroPad button ids (RETRO_DEVICE_ID_JOYPAD_*)
BUTTON_IDS: dict[str, int] = {
    "b": 0, "y": 1, "select": 2, "start": 3,
    "up": 4, "down": 5, "left": 6, "right": 7,
    "a": 8, "x": 9, "l": 10, "r": 11,
    "l2": 12, "r2": 13, "l3": 14, "r3": 15,
}

ANALOG_INDEX: dict[str, int] = {"left": 0, "right": 1}


def encode_remote_message(port: int, device: int, index: int, id: int, state: int) -> bytes:
    """Encode one remote_message as the exact 20-byte struct RetroArch expects.

    Fields are packed little-endian: four signed int32s followed by one signed
    int16 (state), then two zero pad bytes to reach sizeof == 20.

    Using a signed int16 (h) for state handles negative analog axis values
    natively, eliminating any manual two's-complement adjustment.
    """
    return struct.pack("<iiiih", port, device, index, id, state) + b"\x00\x00"


class RetropadClient:
    """Fire-and-forget UDP sender for RetroArch's Network Gamepad receiver."""

    def __init__(self, host: str = "127.0.0.1", base_port: int = 55400) -> None:
        self.host = host
        self.base_port = base_port
        self._socket: socket.socket | None = None

    def describe_target(self) -> str:
        return f"udp {self.host}:{self.base_port}+player"

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def _ensure_socket(self) -> socket.socket:
        if self._socket is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            self._socket = sock
        return self._socket

    async def _send_raw(self, payload: bytes, player: int) -> None:
        if not isinstance(player, int) or player < 0 or player > 20:
            raise ValueError(f"player must be an integer 0-20, got {player!r}")
        sock = self._ensure_socket()
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(sock, payload, (self.host, self.base_port + player))

    async def _send_message(self, player: int, device: int, index: int, id: int, state: int) -> None:
        await self._send_raw(encode_remote_message(player, device, index, id, state), player)

    @staticmethod
    def _button_id(name: str) -> int:
        id = BUTTON_IDS.get(name.lower())
        if id is None:
            valid = ", ".join(BUTTON_IDS)
            raise ValueError(f'unknown button "{name}" — valid RetroPad names: {valid}')
        return id

    async def press_buttons(self, buttons: list[str], player: int = 0) -> None:
        """Press (latch down) each named button. State persists until released."""
        ids = [self._button_id(b) for b in buttons]  # validate all before sending any
        for bid in ids:
            await self._send_message(player, RETRO_DEVICE_JOYPAD, 0, bid, 1)

    async def release_buttons(self, buttons: list[str], player: int = 0) -> None:
        """Release each named button."""
        ids = [self._button_id(b) for b in buttons]
        for bid in ids:
            await self._send_message(player, RETRO_DEVICE_JOYPAD, 0, bid, 0)

    async def set_analog(self, stick: str, x: int, y: int, player: int = 0) -> None:
        """Set one analog stick's X and Y axes (each -32768..32767; 0,0 = centered)."""
        index = ANALOG_INDEX.get(stick)
        if index is None:
            raise ValueError(f'unknown stick "{stick}" — expected "left" or "right"')
        for v in (x, y):
            if not isinstance(v, int) or v < -32768 or v > 32767:
                raise ValueError(f"analog value {v} outside int16 range -32768..32767")
        await self._send_message(player, RETRO_DEVICE_ANALOG, index, 0, x)
        await self._send_message(player, RETRO_DEVICE_ANALOG, index, 1, y)

    async def release_all(self, player: int = 0) -> None:
        """Zero every button and both analog sticks for the player in one packet.

        Sends a deliberately undersized datagram: RetroArch's receiver resets all
        input state for a player whenever it reads a datagram whose size is not
        exactly sizeof(struct remote_message).
        """
        await self._send_raw(b"\x00", player)
