# mcp-retroarch

MCP server for RetroArch — memory r/w, save states, screenshots, pause/frame-advance/reset via the
[Network Control Interface](https://docs.libretro.com/development/retroarch/network-control-interface/),
plus gamepad input via the Network RetroPad protocol (UDP).

> Inspired by [dmang-dev/mcp-retroarch](https://github.com/dmang-dev/mcp-retroarch).
> Rewritten in Python to eliminate the npm git-install reliability issues that
> plagued the original TypeScript distribution (`npm install -g github:...` produces
> a broken symlink on most systems due to a known npm bug). `uv tool install git+...`
> handles this correctly.

## Requirements

- Python 3.13
- [RetroArch](https://www.retroarch.com/) with:
  - **Network Commands** enabled for NCI tools (memory, save states, screenshots, emulator control)
  - **Network Gamepad** enabled for input tools

## Install

```bash
# Install as a persistent uv tool (recommended)
uv tool install git+https://github.com/pythoninthegrass/mcp-retroarch

# Or run ephemerally without installing
uvx --from git+https://github.com/pythoninthegrass/mcp-retroarch mcp-retroarch
```

## RetroArch configuration

### Network Commands (NCI — required for most tools)

In `retroarch.cfg` or via **Settings → Network → Network Commands**:

```cfg
network_cmd_enable = "true"
network_cmd_port = "55355"
```

### Network Gamepad (required for input tools)

In `retroarch.cfg` or via **Settings → Input → Network Gamepad**:

```cfg
network_remote_enable = "true"
network_remote_base_port = "55400"

# Enable per player (p1 through p16)
network_remote_enable_user_p1 = "true"
```

## Client configuration

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "retroarch": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/pythoninthegrass/mcp-retroarch",
        "mcp-retroarch"
      ]
    }
  }
}
```

If installed via `uv tool install`:

```json
{
  "mcpServers": {
    "retroarch": {
      "command": "mcp-retroarch"
    }
  }
}
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `RETROARCH_HOST` | `127.0.0.1` | RetroArch host for both NCI and RetroPad |
| `RETROARCH_PORT` | `55355` | NCI UDP port (`network_cmd_port`) |
| `RETROARCH_RETROPAD_HOST` | `$RETROARCH_HOST` | Override host for Network Gamepad only |
| `RETROARCH_RETROPAD_BASE_PORT` | `55400` | Base port for Network Gamepad (`network_remote_base_port`); player N listens at base + N |

## Tools

### Connectivity & introspection

| Tool | Description |
|---|---|
| `retroarch_ping` | Verify NCI connectivity; returns RetroArch version |
| `retroarch_get_status` | Report playing/paused state, loaded system, game, and CRC32 |
| `retroarch_get_config` | Read a single RetroArch config parameter by name |

### Memory

| Tool | Description |
|---|---|
| `retroarch_read_memory` | Read bytes via the libretro system memory map (preferred) |
| `retroarch_read_ram` | Read bytes via the CHEEVOS address space (fallback) |
| `retroarch_write_memory` | Write bytes via the libretro system memory map |
| `retroarch_write_ram` | Write bytes via the CHEEVOS address space (no acknowledgement) |

### Emulator control

| Tool | Description |
|---|---|
| `retroarch_pause_toggle` | Toggle pause/unpause |
| `retroarch_frame_advance` | Step one frame (paused only) |
| `retroarch_reset` | Soft-reset the loaded game |
| `retroarch_screenshot` | Save a screenshot to RetroArch's screenshot directory |
| `retroarch_show_message` | Display an OSD notification on the RetroArch window |

### Save states

| Tool | Description |
|---|---|
| `retroarch_save_state_current` | Save to currently-selected slot |
| `retroarch_load_state_current` | Load from currently-selected slot |
| `retroarch_load_state_slot` | Load from an explicit slot number (does not change the current-slot pointer) |
| `retroarch_state_slot_plus` | Increment current slot pointer |
| `retroarch_state_slot_minus` | Decrement current slot pointer |

### Gamepad input (Network RetroPad)

| Tool | Description |
|---|---|
| `retroarch_input_press` | Latch one or more buttons down |
| `retroarch_input_release` | Release one or more buttons |
| `retroarch_input_release_all` | Zero all buttons and analog sticks in one packet |
| `retroarch_input_set_analog` | Set an analog stick's X/Y position |
| `retroarch_input_tap` | Press, hold for a duration, then release (everyday input) |

Valid RetroPad button names: `b`, `y`, `select`, `start`, `up`, `down`, `left`, `right`, `a`, `x`, `l`, `r`, `l2`, `r2`, `l3`, `r3`.

On PlayStation cores: `b`=Cross, `a`=Circle, `y`=Square, `x`=Triangle.

See [`docs/RECIPES.md`](docs/RECIPES.md) for usage patterns.

## Development

```bash
# Install with dev dependencies
uv sync

# Run tests
uv run pytest

# Format and lint
uv run ruff format .
uv run ruff check .
```

## Docker

```bash
docker build -t mcp-retroarch .
docker run --rm -i \
  -e RETROARCH_HOST=host.docker.internal \
  mcp-retroarch
```

## License

MIT — see [LICENSE](LICENSE).
