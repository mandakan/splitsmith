"""Shared Look builders for tests that need a user Look on disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splitsmith import looks
from splitsmith.looks import Look, load_look

FRAMES_STING_HTML = """<!doctype html>
<html>
<head>
<meta name="splitsmith-frames" content="edges">
</head>
<body>
<script>
window.duration = () => splitsmith.data.transition.duration_seconds;
window.seek = (t) => Promise.resolve();
</script>
</body>
</html>
"""


def install_frames_look(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Look:
    """A user Look named ``framesy`` whose ``push`` sting declares
    ``splitsmith-frames``, installed under ``SPLITSMITH_HOME=tmp_path``."""
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    root = tmp_path / "looks" / "framesy"
    root.mkdir(parents=True)
    manifest = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    manifest["name"] = "framesy"
    manifest["slots"] = {"transition": {"push": "push.html"}}
    (root / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "push.html").write_text(FRAMES_STING_HTML, encoding="utf-8")
    return load_look("framesy")
