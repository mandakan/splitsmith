"""Model Context Protocol server for splitsmith (issue #211).

Exposes splitsmith's existing pipeline as agent-callable tools so any
MCP-aware client (Claude Desktop, Claude Code, IDE plugins) can drive a
match end-to-end. Layered roll-out -- this layer (#211 layer 1) ships
the package scaffolding and the read-only tools (probe video, discover
videos, get project state, get HITL queue). Subsequent layers add
write tools, detection orchestration, export pipeline, and the
``/splitsmith-match`` skill.

Run via::

    python -m splitsmith.mcp                # stdio transport (default)
    splitsmith mcp                          # same, via the typer CLI

Tools take ``project_root`` as a path string -- stateless, so multiple
agents can collaborate on the same project. ``SPLITSMITH_MCP_ALLOWED_ROOT``
optionally sandboxes every path argument under that directory.
"""

from .server import create_server

__all__ = ["create_server"]
