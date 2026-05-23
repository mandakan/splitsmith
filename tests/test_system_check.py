"""Tests for the first-launch ffmpeg presence check (issue #377 -- doc 04)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from splitsmith import runtime as runtime_module
from splitsmith import system_check


def _make_fake_ffmpeg(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)


def _point_runtime_at(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ffmpeg: str,
    ffprobe: str,
    config_dir: Path,
) -> None:
    monkeypatch.setenv("SPLITSMITH_FFMPEG", ffmpeg)
    monkeypatch.setenv("SPLITSMITH_FFPROBE", ffprobe)
    monkeypatch.setenv("SPLITSMITH_CONFIG_DIR", str(config_dir))
    runtime_module._clear_runtime_cache()


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    runtime_module._clear_runtime_cache()
    yield
    runtime_module._clear_runtime_cache()


def test_present_binaries_yield_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ff = tmp_path / "ffmpeg"
    fp = tmp_path / "ffprobe"
    _make_fake_ffmpeg(ff)
    _make_fake_ffmpeg(fp)
    _point_runtime_at(monkeypatch, ffmpeg=str(ff), ffprobe=str(fp), config_dir=tmp_path / "cfg")

    outcome = system_check.check_ffmpeg()
    assert outcome.ok is True
    assert outcome.binary == str(ff)
    assert outcome.hint == ""


def test_missing_binary_returns_install_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bogus_ff = tmp_path / "does-not-exist-ffmpeg"
    bogus_fp = tmp_path / "does-not-exist-ffprobe"
    _point_runtime_at(monkeypatch, ffmpeg=str(bogus_ff), ffprobe=str(bogus_fp), config_dir=tmp_path / "cfg")

    outcome = system_check.check_ffmpeg()
    assert outcome.ok is False
    assert "Splitsmith couldn't find" in outcome.hint
    # State file must not record the negative result.
    state_path = system_check._state_path()
    if state_path.exists():
        assert system_check.CACHE_KEY not in json.loads(state_path.read_text())


def test_positive_result_is_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ff = tmp_path / "ffmpeg"
    fp = tmp_path / "ffprobe"
    _make_fake_ffmpeg(ff)
    _make_fake_ffmpeg(fp)
    _point_runtime_at(monkeypatch, ffmpeg=str(ff), ffprobe=str(fp), config_dir=tmp_path / "cfg")

    first = system_check.check_ffmpeg()
    assert first.ok is True

    state = json.loads(system_check._state_path().read_text())
    assert system_check.CACHE_KEY in state
    entry = state[system_check.CACHE_KEY]
    assert entry["ok"] is True
    assert entry["binary"] == str(ff)
    assert entry["ffprobe"] == str(fp)


def test_cached_positive_skips_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ff = tmp_path / "ffmpeg"
    fp = tmp_path / "ffprobe"
    _make_fake_ffmpeg(ff)
    _make_fake_ffmpeg(fp)
    _point_runtime_at(monkeypatch, ffmpeg=str(ff), ffprobe=str(fp), config_dir=tmp_path / "cfg")
    system_check.check_ffmpeg()

    called = []

    def _explode(*args, **kwargs):
        called.append(args)
        raise AssertionError("subprocess.run should not be called on cache hit")

    monkeypatch.setattr(subprocess, "run", _explode)
    outcome = system_check.check_ffmpeg()
    assert outcome.ok is True
    assert called == []


def test_stale_cache_re_verifies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ff = tmp_path / "ffmpeg"
    fp = tmp_path / "ffprobe"
    _make_fake_ffmpeg(ff)
    _make_fake_ffmpeg(fp)
    _point_runtime_at(monkeypatch, ffmpeg=str(ff), ffprobe=str(fp), config_dir=tmp_path / "cfg")

    # Write a stale positive cache entry from a day + change ago.
    state_path = system_check._state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                system_check.CACHE_KEY: {
                    "ok": True,
                    "binary": str(ff),
                    "ffprobe": str(fp),
                    "ts": time.time() - (system_check.CACHE_TTL_S + 60),
                }
            }
        )
    )

    called: list[list[str]] = []
    real_run = subprocess.run

    def _spy(args, **kwargs):
        called.append(list(args))
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy)
    outcome = system_check.check_ffmpeg()
    assert outcome.ok is True
    # The probe re-ran -- subprocess called at least once.
    assert called, "stale cache must trigger a re-verify"


def test_negative_result_is_not_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bogus = tmp_path / "missing-ffmpeg"
    _point_runtime_at(monkeypatch, ffmpeg=str(bogus), ffprobe=str(bogus), config_dir=tmp_path / "cfg")

    system_check.check_ffmpeg()
    state_path = system_check._state_path()
    if state_path.exists():
        state = json.loads(state_path.read_text())
        assert system_check.CACHE_KEY not in state


def test_install_hint_is_platform_aware(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    hint = system_check._install_hint("ffmpeg")
    assert "brew install ffmpeg" in hint

    monkeypatch.setattr(sys, "platform", "linux")
    hint = system_check._install_hint("ffmpeg")
    assert "apt install ffmpeg" in hint

    monkeypatch.setattr(sys, "platform", "win32")
    hint = system_check._install_hint("ffmpeg")
    assert "winget install" in hint


def test_use_cache_false_forces_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ff = tmp_path / "ffmpeg"
    fp = tmp_path / "ffprobe"
    _make_fake_ffmpeg(ff)
    _make_fake_ffmpeg(fp)
    _point_runtime_at(monkeypatch, ffmpeg=str(ff), ffprobe=str(fp), config_dir=tmp_path / "cfg")
    system_check.check_ffmpeg()  # primes cache

    called: list[list[str]] = []
    real_run = subprocess.run

    def _spy(args, **kwargs):
        called.append(list(args))
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy)
    outcome = system_check.check_ffmpeg(use_cache=False)
    assert outcome.ok is True
    assert called, "use_cache=False must run the probe"
