"""Text I/O names its encoding, so Windows does not decode as cp1252.

Without ``encoding=``, Python decodes with the locale's encoding: UTF-8 on
macOS and Linux, cp1252 on most Windows installs, where an ffprobe line with
a non-ASCII file name or a JSON doc with a shooter's name fails or comes
back garbled. Ruff's PLW1514 (``pyproject.toml``) only fires where it can
tell the receiver is a ``Path``, and never on subprocess, so this reads the
source for both.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TREES = ("src", "tests", "scripts")
# ``subprocess.run``, ``Popen`` and friends, and the injected ``runner`` the
# media modules take. ``text=`` on any other call (an XML element's text) is
# not a decode.
_SUBPROCESS_CALLEES = {"run", "Popen", "check_output", "check_call", "call", "runner"}


def _is_text_mode(mode: ast.expr | None) -> bool:
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" not in mode.value


def _unencoded(node: ast.Call) -> str | None:
    """What kind of text I/O ``node`` is when it names no encoding, else None."""
    if "encoding" in {kw.arg for kw in node.keywords}:
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
    mode_kw = next((kw.value for kw in node.keywords if kw.arg == "mode"), None)
    if name in _SUBPROCESS_CALLEES and any(
        kw.arg in ("text", "universal_newlines")
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is True
        for kw in node.keywords
    ):
        return "subprocess"
    if isinstance(func, ast.Attribute) and name == "read_text" and not node.args:
        return "read_text"
    if isinstance(func, ast.Attribute) and name == "write_text" and len(node.args) == 1:
        return "write_text"
    if isinstance(func, ast.Name) and name == "open":
        mode = node.args[1] if len(node.args) >= 2 else mode_kw
        return "open" if mode is None or _is_text_mode(mode) else None
    # ``x.open("w")``: only with the mode written out, since a bare
    # ``.open()`` is also a sealed token's and a policy's constructor.
    if isinstance(func, ast.Attribute) and name == "open":
        return "open" if _is_text_mode(node.args[0] if node.args else mode_kw) else None
    return None


def _offenders(source: str) -> list[tuple[int, str]]:
    return [
        (node.lineno, kind)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and (kind := _unencoded(node))
    ]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("subprocess.run(['x'], text=True)", [(1, "subprocess")]),
        ("runner(['x'], capture_output=True, text=True)", [(1, "subprocess")]),
        ("subprocess.run(['x'], text=True, encoding='utf-8')", []),
        ("subprocess.run(['x'], text=False)", []),
        ("ET.SubElement(parent, 'a', text=label)", []),
        ("FIXTURE.read_text()", [(1, "read_text")]),
        ("FIXTURE.read_text(encoding='utf-8')", []),
        ("out.write_text(body)", [(1, "write_text")]),
        ("open(path)", [(1, "open")]),
        ("open(path, 'rb')", []),
        ("lock.open('a')", [(1, "open")]),
        ("stored.open()", []),
        ("wave.open(path, 'rb')", []),
    ],
)
def test_the_check_tells_text_io_from_everything_else(source: str, expected: list[tuple[int, str]]) -> None:
    assert _offenders(source) == expected


def test_every_text_read_write_and_decode_names_its_encoding() -> None:
    offenders = [
        f"{path.relative_to(ROOT).as_posix()}:{line} {kind}"
        for tree in TREES
        for path in sorted((ROOT / tree).rglob("*.py"))
        for line, kind in _offenders(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
