"""``scripts/bake_youtube_client.py``: the publish-time rewrite of the
YouTube OAuth client constants (issue #1000)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "bake_youtube_client.py"
OAUTH = Path(__file__).resolve().parent.parent / "src" / "splitsmith" / "youtube" / "oauth.py"


def _load():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("bake_youtube_client", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bake_rewrites_both_constants_in_the_real_module_text() -> None:
    mod = _load()
    baked = mod.bake(
        OAUTH.read_text(encoding="utf-8"),
        client_id="123.apps.googleusercontent.com",
        client_secret="GOCSPX-x",
    )
    assert 'BUILTIN_CLIENT_ID = "123.apps.googleusercontent.com"' in baked
    assert 'BUILTIN_CLIENT_SECRET = "GOCSPX-x"' in baked
    assert baked.count("BUILTIN_CLIENT_ID = ") == 1


def test_bake_refuses_empty_or_quoted_values_and_a_moved_constant() -> None:
    mod = _load()
    src = OAUTH.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="client secret"):
        mod.bake(src, client_id="id", client_secret="")
    with pytest.raises(ValueError, match="client id"):
        mod.bake(src, client_id='a"b', client_secret="s")
    with pytest.raises(ValueError, match="not found"):
        mod.bake("x = 1\n", client_id="id", client_secret="s")


def test_cli_writes_the_file_and_exits_2_without_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "oauth.py"
    target.write_text(OAUTH.read_text(encoding="utf-8"), encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, str(SCRIPT), str(target)], env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "must both be set" in r.stderr
    env.update(
        {
            "SPLITSMITH_YOUTUBE_CLIENT_ID": "id.apps.googleusercontent.com",
            "SPLITSMITH_YOUTUBE_CLIENT_SECRET": "sec",
        }
    )
    r = subprocess.run([sys.executable, str(SCRIPT), str(target)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert 'BUILTIN_CLIENT_SECRET = "sec"' in target.read_text(encoding="utf-8")
    assert "sec" not in r.stdout  # the secret never reaches the CI log


def test_the_committed_module_ships_no_client() -> None:
    """The constants stay empty in git; the wheel gets them at publish."""
    src = OAUTH.read_text(encoding="utf-8")
    assert 'BUILTIN_CLIENT_ID = ""' in src
    assert 'BUILTIN_CLIENT_SECRET = ""' in src
