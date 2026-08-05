import asyncio
import os
import sys

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from mcp_retroarch import __version__
from mcp_retroarch.retroarch import RetroArchClient
from mcp_retroarch.retropad import RetropadClient
from mcp_retroarch.tools import register_tools


async def _run() -> None:
    host = os.environ.get("RETROARCH_HOST", "127.0.0.1")
    port = int(os.environ.get("RETROARCH_PORT", "55355"))
    retropad_host = os.environ.get("RETROARCH_RETROPAD_HOST", host)
    retropad_base_port = int(os.environ.get("RETROARCH_RETROPAD_BASE_PORT", "55400"))

    ra = RetroArchClient(host=host, port=port)
    pad = RetropadClient(host=retropad_host, base_port=retropad_base_port)

    server = Server("mcp-retroarch")
    register_tools(server, ra, pad)

    async def _probe() -> None:
        try:
            await ra.connect()
            v = await ra.get_version()
            sys.stderr.write(f"[mcp-retroarch] connected to {ra.describe_target()} — RetroArch {v}\n")
        except Exception as err:
            sys.stderr.write(
                f"[mcp-retroarch] note: RetroArch not reachable yet ({ra.describe_target()}): {err}\n"
                f"             Enable Network Commands in retroarch.cfg (network_cmd_enable / network_cmd_port)\n"
                f"             or Settings > Network > Network Commands. Tool calls will connect on demand.\n"
            )

    # Bring up the MCP transport immediately — don't block startup on reaching
    # RetroArch. Introspection (tools/list) must respond fast even with no
    # emulator running. The connectivity probe runs in the background and just
    # logs its result; individual tool calls each (re)connect on demand.
    async with stdio_server() as (read_stream, write_stream):
        sys.stderr.write(f"[mcp-retroarch] MCP server ready (stdio) v{__version__}\n")
        asyncio.create_task(_probe())  # fire-and-forget, mirrors TS behavior
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    try:
        asyncio.run(_run())
    except Exception as err:
        sys.stderr.write(f"[mcp-retroarch] fatal: {err}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
