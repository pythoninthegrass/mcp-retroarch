# Packet-level tests for the Network RetroPad sender.
#
# RetroArch's receiver (input/input_driver.c) does:
#   recvfrom(fd, &msg, sizeof(msg)) where msg is
#   struct remote_message { int port; int device; int index; int id; uint16_t state; }
# and only parses when ret == sizeof(msg) — i.e. exactly 20 bytes with
# standard x86/ARM alignment (4×int32 LE + uint16 LE + 2 pad bytes).
# Any other datagram size zeroes ALL input state for that user.
#
# These tests pin the exact wire bytes against a loopback UDP listener; no
# RetroArch instance is required.

import struct
import pytest
from mcp_retroarch.retropad import (
    BUTTON_IDS,
    RETRO_DEVICE_ANALOG,
    RETRO_DEVICE_JOYPAD,
    RetropadClient,
    encode_remote_message,
)


# ── encoding ──────────────────────────────────────────────────────────────

def test_encode_remote_message_20_byte_little_endian_struct():
    buf = encode_remote_message(port=0, device=1, index=0, id=8, state=1)
    assert len(buf) == 20
    assert struct.unpack_from("<i", buf, 0)[0] == 0    # port
    assert struct.unpack_from("<i", buf, 4)[0] == 1    # device
    assert struct.unpack_from("<i", buf, 8)[0] == 0    # index
    assert struct.unpack_from("<i", buf, 12)[0] == 8   # id
    assert struct.unpack_from("<H", buf, 16)[0] == 1   # state (unsigned read)
    assert struct.unpack_from("<H", buf, 18)[0] == 0   # struct padding — must be zero


def test_encode_remote_message_negative_analog_twos_complement():
    # -32768 must appear as 0x8000 when read back as unsigned uint16
    buf_min = encode_remote_message(port=0, device=RETRO_DEVICE_ANALOG, index=0, id=0, state=-32768)
    assert struct.unpack_from("<H", buf_min, 16)[0] == 0x8000

    # -1 must appear as 0xffff
    buf_neg1 = encode_remote_message(port=0, device=RETRO_DEVICE_ANALOG, index=1, id=1, state=-1)
    assert struct.unpack_from("<H", buf_neg1, 16)[0] == 0xFFFF


def test_button_ids_match_libretro_retropad_layout():
    expected = {
        "b": 0, "y": 1, "select": 2, "start": 3,
        "up": 4, "down": 5, "left": 6, "right": 7,
        "a": 8, "x": 9, "l": 10, "r": 11,
        "l2": 12, "r2": 13, "l3": 14, "r3": 15,
    }
    assert BUTTON_IDS == expected


# ── UDP sending against a loopback listener ───────────────────────────────

async def test_press_buttons_sends_one_20_byte_joypad_message_per_button(udp_capture):
    port, received = udp_capture
    pad = RetropadClient(host="127.0.0.1", base_port=port)
    try:
        await pad.press_buttons(["a", "up"])
        # wait for both packets
        import asyncio
        for _ in range(20):
            if len(received) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(received) >= 2
        for data, _ in received[:2]:
            assert len(data) == 20
        pkt0, _ = received[0]
        pkt1, _ = received[1]
        assert struct.unpack_from("<i", pkt0, 4)[0] == RETRO_DEVICE_JOYPAD
        assert struct.unpack_from("<i", pkt0, 12)[0] == BUTTON_IDS["a"]
        assert struct.unpack_from("<H", pkt0, 16)[0] == 1
        assert struct.unpack_from("<i", pkt1, 12)[0] == BUTTON_IDS["up"]
        assert struct.unpack_from("<H", pkt1, 16)[0] == 1
    finally:
        pad.close()


async def test_release_buttons_sends_state_0(udp_capture):
    port, received = udp_capture
    pad = RetropadClient(host="127.0.0.1", base_port=port)
    try:
        await pad.release_buttons(["start"])
        import asyncio
        for _ in range(20):
            if len(received) >= 1:
                break
            await asyncio.sleep(0.01)
        assert len(received) >= 1
        pkt, _ = received[0]
        assert len(pkt) == 20
        assert struct.unpack_from("<i", pkt, 12)[0] == BUTTON_IDS["start"]
        assert struct.unpack_from("<H", pkt, 16)[0] == 0
    finally:
        pad.close()


async def test_player_index_selects_base_port_plus_player(udp_capture):
    port, received = udp_capture
    # listener bound at `port`; set base_port = port - 1, send player=1 → hits port
    pad = RetropadClient(host="127.0.0.1", base_port=port - 1)
    try:
        await pad.press_buttons(["b"], player=1)
        import asyncio
        for _ in range(20):
            if len(received) >= 1:
                break
            await asyncio.sleep(0.01)
        assert len(received) >= 1
        pkt, _ = received[0]
        assert struct.unpack_from("<i", pkt, 0)[0] == 1           # port field mirrors player index
        assert struct.unpack_from("<i", pkt, 12)[0] == BUTTON_IDS["b"]
    finally:
        pad.close()


async def test_set_analog_sends_x_and_y_messages(udp_capture):
    port, received = udp_capture
    pad = RetropadClient(host="127.0.0.1", base_port=port)
    try:
        await pad.set_analog("right", 12345, -20000)
        import asyncio
        for _ in range(20):
            if len(received) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(received) >= 2
        pkt0, _ = received[0]
        pkt1, _ = received[1]
        assert struct.unpack_from("<i", pkt0, 4)[0] == RETRO_DEVICE_ANALOG
        assert struct.unpack_from("<i", pkt0, 8)[0] == 1       # index: right stick
        assert struct.unpack_from("<i", pkt0, 12)[0] == 0      # id: X
        assert struct.unpack_from("<H", pkt0, 16)[0] == 12345
        assert struct.unpack_from("<i", pkt1, 12)[0] == 1      # id: Y
        assert struct.unpack_from("<h", pkt1, 16)[0] == -20000  # signed read
    finally:
        pad.close()


async def test_set_analog_rejects_out_of_range():
    pad = RetropadClient(host="127.0.0.1", base_port=55400)
    try:
        with pytest.raises(ValueError, match="range"):
            await pad.set_analog("left", 40000, 0)
        with pytest.raises(ValueError, match="range"):
            await pad.set_analog("left", 0, -40000)
    finally:
        pad.close()


async def test_press_buttons_rejects_unknown_button_names():
    pad = RetropadClient(host="127.0.0.1", base_port=55400)
    try:
        with pytest.raises(ValueError, match="(?i)unknown button"):
            await pad.press_buttons(["triangle"])
    finally:
        pad.close()


async def test_release_all_sends_short_datagram(udp_capture):
    """release_all must NOT send 20 bytes — the short size triggers RetroArch's input reset."""
    port, received = udp_capture
    pad = RetropadClient(host="127.0.0.1", base_port=port)
    try:
        await pad.release_all()
        import asyncio
        for _ in range(20):
            if len(received) >= 1:
                break
            await asyncio.sleep(0.01)
        assert len(received) >= 1
        pkt, _ = received[0]
        assert len(pkt) != 20
    finally:
        pad.close()
