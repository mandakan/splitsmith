"""Text I/O names its encoding, so Windows does not decode as cp1252.

Without ``encoding=``, Python decodes with the locale's encoding: UTF-8 on
macOS and Linux, cp1252 on most Windows installs, where an ffprobe line with
a non-ASCII file name or a JSON doc with a shooter's name fails or comes
back garbled. Ruff's PLW1514 (``pyproject.toml``) covers ``open`` and
``Path.read_text``/``write_text``; it does not see subprocess, so this does.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "splitsmith"
# ``subprocess.run``, ``Popen`` and friends, and the injected ``runner`` the
# media modules take. ``text=`` on any other call (an XML element's text) is
# not a decode.
_SUBPROCESS_CALLEES = {"run", "Popen", "check_output", "check_call", "call", "runner"}


def _text_subprocess_calls_without_encoding(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if ast.unparse(node.func).rsplit(".", 1)[-1] not in _SUBPROCESS_CALLEES:
            continue
        kwargs = {kw.arg for kw in node.keywords if kw.arg}
        text_mode = any(
            kw.arg in ("text", "universal_newlines")
            and isinstance(kw.value, ast.Constant)
            and kw.value.value is True
            for kw in node.keywords
        )
        if text_mode and "encoding" not in kwargs:
            lines.append(node.lineno)
    return lines


def test_the_check_finds_a_text_call_without_encoding() -> None:
    assert _text_subprocess_calls_without_encoding("subprocess.run(['x'], text=True)\n") == [1]
    assert _text_subprocess_calls_without_encoding("runner(['x'], capture_output=True, text=True)\n") == [1]
    assert (
        _text_subprocess_calls_without_encoding("subprocess.run(['x'], text=True, encoding='utf-8')\n") == []
    )
    assert _text_subprocess_calls_without_encoding("subprocess.run(['x'], text=False)\n") == []
    assert _text_subprocess_calls_without_encoding("ET.SubElement(parent, 'a', text=label)\n") == []


def test_every_text_mode_subprocess_call_names_its_encoding() -> None:
    offenders = [
        f"{path.relative_to(SRC.parent).as_posix()}:{line}"
        for path in sorted(SRC.rglob("*.py"))
        for line in _text_subprocess_calls_without_encoding(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
