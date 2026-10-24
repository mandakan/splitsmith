"""No request handler derives media (spec 2026-10-14).

A producer (``audio_helpers.ensure_*``) runs ffmpeg, decodes a source or
mirrors a large file; it belongs in a job body (``_run_*``). A route reads
the published file and queues the job on a miss. This walks ``ui/server.py``
and fails on any producer call outside a job body, so a new route cannot
quietly cut audio in a request again (the 2026-10-13 short-audio incident).
"""

from __future__ import annotations

import ast
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "src" / "splitsmith" / "ui" / "server.py"

#: Request paths allowed to produce, each with its reason. Keep this short.
ALLOWED = {
    # Lab actions on the desktop that write a fixture from a secondary angle:
    # a deliberate, local, one-off write the operator waits on, not a page load.
    "promote_secondary": "local lab action, writes a fixture",
    "lab_promote_against_fixture": "local lab action, writes a fixture",
    # One preview clip per scrubbed time in the beep picker; it publishes
    # whole (thumbnail._publish) and a job per scrub would be worse.
    "_serve_beep_preview": "interactive per-t clip, published whole",
}


def _producer_calls() -> list[tuple[str, str, int]]:
    """Each producer call as (function, producer, line). The function is the
    outermost job body (``_run_*``) around the call when there is one, since
    a helper nested in a job body runs in the job, else the innermost."""
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    found: list[tuple[str, str, int]] = []

    def visit(node: ast.AST, enclosing: list[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            enclosing = [*enclosing, node.name]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            base = node.func.value
            if (
                isinstance(base, ast.Name)
                and base.id in ("audio_helpers", "thumbnail_helpers")
                and node.func.attr.startswith("ensure_")
            ):
                body = next((name for name in enclosing if name.startswith("_run_")), None)
                owner = body or (enclosing[-1] if enclosing else "<module>")
                found.append((owner, node.func.attr, node.lineno))
                return
        for child in ast.iter_child_nodes(node):
            visit(child, enclosing)

    visit(tree, [])
    return found


def test_only_job_bodies_produce_derived_media() -> None:
    offenders = [
        (func, call, line)
        for func, call, line in _producer_calls()
        if not func.startswith("_run_") and func not in ALLOWED
    ]
    assert offenders == [], (
        "a request handler calls a producer; read the published file and queue "
        f"its job instead (spec 2026-10-14): {offenders}"
    )


def test_the_guard_sees_the_producers() -> None:
    """The walk finds the job bodies' producer calls, so a green run means
    something: an empty walk would pass vacuously."""
    funcs = {func for func, _call, _line in _producer_calls()}
    assert {"_run_trim", "_run_derive_audio", "_run_shot_detect"} <= funcs
