"""Run the splitsmith MCP server over stdio.

Invoked by MCP-aware clients (Claude Desktop, Claude Code, etc.) via
their server config. The typer wrapper ``splitsmith mcp`` is the
preferred user-facing entry; this module exists so clients that only
know how to launch ``python -m splitsmith.mcp`` work out of the box.
"""

from __future__ import annotations

from . import create_server


def main() -> None:
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
