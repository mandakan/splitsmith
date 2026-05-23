"""Tests for ``splitsmith.runtime`` (issue #130)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from splitsmith import runtime as runtime_module
from splitsmith.runtime import (
    ENV_ARTIFACTS_DIR,
    ENV_CACHE_DIR,
    ENV_CONFIG_DIR,
    ENV_FFMPEG,
    ENV_FFPROBE,
    _clear_runtime_cache,
    resolve_runtime,
    runtime,
)


@pytest.fixture(autouse=True)
def _reset_runtime_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the resolver cache + scrub runtime env vars per test.

    Without this, tests leak Runtime objects across each other and the
    autouse ``_isolate_user_config`` fixture's ``SPLITSMITH_HOME`` --
    plus any developer-shell exports -- bleed into the priority test.
    """
    for key in (
        ENV_ARTIFACTS_DIR,
        ENV_FFMPEG,
        ENV_FFPROBE,
        ENV_CACHE_DIR,
        ENV_CONFIG_DIR,
        "HF_HOME",
        "TORCH_HOME",
    ):
        monkeypatch.delenv(key, raising=False)
    _clear_runtime_cache()
    yield
    _clear_runtime_cache()


def test_default_artifacts_dir_points_at_package_data() -> None:
    rt = resolve_runtime()
    assert rt.artifacts_dir.name == "data"
    # The shipped calibration should resolve via ``artifact()``.
    assert rt.artifact("ensemble_calibration.json").is_file()


def test_artifact_missing_raises_actionable_error(tmp_path: Path) -> None:
    rt = resolve_runtime(artifacts_dir=tmp_path)
    with pytest.raises(FileNotFoundError) as exc:
        rt.artifact("ensemble_calibration.json")
    msg = str(exc.value)
    assert "ensemble_calibration.json" in msg
    assert str(tmp_path) in msg
    assert ENV_ARTIFACTS_DIR in msg


def test_explicit_kwarg_beats_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_dir = tmp_path / "env-dir"
    kwarg_dir = tmp_path / "kwarg-dir"
    env_dir.mkdir()
    kwarg_dir.mkdir()
    monkeypatch.setenv(ENV_ARTIFACTS_DIR, str(env_dir))
    monkeypatch.setenv(ENV_FFMPEG, "/env/bin/ffmpeg")

    rt = resolve_runtime(artifacts_dir=kwarg_dir, ffmpeg_binary="/kwarg/bin/ffmpeg")
    assert rt.artifacts_dir == kwarg_dir
    assert rt.ffmpeg_binary == "/kwarg/bin/ffmpeg"


def test_env_beats_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_ARTIFACTS_DIR, str(tmp_path))
    monkeypatch.setenv(ENV_FFMPEG, "/env/ffmpeg")
    monkeypatch.setenv(ENV_FFPROBE, "/env/ffprobe")
    monkeypatch.setenv(ENV_CACHE_DIR, str(tmp_path / "cache"))
    monkeypatch.setenv(ENV_CONFIG_DIR, str(tmp_path / "cfg"))

    rt = resolve_runtime()
    assert rt.artifacts_dir == tmp_path
    assert rt.ffmpeg_binary == "/env/ffmpeg"
    assert rt.ffprobe_binary == "/env/ffprobe"
    assert rt.cache_dir == tmp_path / "cache"
    assert rt.user_config_dir == tmp_path / "cfg"


def test_default_binary_is_ffmpeg() -> None:
    rt = resolve_runtime()
    assert rt.ffmpeg_binary == "ffmpeg"
    assert rt.ffprobe_binary == "ffprobe"


def test_default_cache_dir_is_platform_specific() -> None:
    rt = resolve_runtime()
    if sys.platform == "darwin":
        assert rt.cache_dir == Path.home() / "Library" / "Caches" / "splitsmith"
    elif sys.platform.startswith("win"):
        # The cache dir lands under ``%LOCALAPPDATA%\splitsmith`` or
        # ``~/AppData/Local/splitsmith``. Either is acceptable.
        assert rt.cache_dir.name == "splitsmith"
    else:
        # Linux: XDG_CACHE_HOME-aware -- with the env var scrubbed by
        # the fixture, falls back to ``~/.cache/splitsmith``.
        assert rt.cache_dir == Path.home() / ".cache" / "splitsmith"


def test_hf_and_torch_home_setdefault(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    monkeypatch.setenv(ENV_CACHE_DIR, str(cache))
    resolve_runtime()
    assert os.environ["HF_HOME"] == str(cache / "hf")
    assert os.environ["TORCH_HOME"] == str(cache / "torch")


def test_hf_and_torch_home_respect_existing_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HF_HOME", "/already/set/hf")
    monkeypatch.setenv("TORCH_HOME", "/already/set/torch")
    monkeypatch.setenv(ENV_CACHE_DIR, str(tmp_path))
    resolve_runtime()
    assert os.environ["HF_HOME"] == "/already/set/hf"
    assert os.environ["TORCH_HOME"] == "/already/set/torch"


def test_resolve_runtime_is_cached() -> None:
    first = resolve_runtime()
    second = resolve_runtime()
    assert first is second
    assert runtime() is first


def test_clear_runtime_cache_resets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first = resolve_runtime()
    monkeypatch.setenv(ENV_ARTIFACTS_DIR, str(tmp_path))
    # Without clearing, the cached value wins.
    assert resolve_runtime() is first
    _clear_runtime_cache()
    second = resolve_runtime()
    assert second is not first
    assert second.artifacts_dir == tmp_path


def test_missing_binary_logs_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(ENV_FFMPEG, "definitely-not-a-real-binary-xyz")
    with caplog.at_level("WARNING", logger=runtime_module.__name__):
        rt = resolve_runtime()
    assert rt.ffmpeg_binary == "definitely-not-a-real-binary-xyz"
    assert any("ffmpeg" in r.message for r in caplog.records)


def test_calibration_loader_uses_runtime_artifacts_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The actionable error from ``runtime().artifact()`` reaches callers."""
    from splitsmith.ensemble.calibration import load_calibration

    monkeypatch.setenv(ENV_ARTIFACTS_DIR, str(tmp_path))
    with pytest.raises(FileNotFoundError) as exc:
        load_calibration()
    assert "ensemble_calibration.json" in str(exc.value)


# ----------------------------------------------------------------------
# Bundled-binary discovery (issue #370)
# ----------------------------------------------------------------------


def _make_executable(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)


def test_meipass_dir_wins_over_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A bundled binary under ``sys._MEIPASS`` beats whatever's on PATH."""
    bundle = tmp_path / "meipass"
    bundle.mkdir()
    _make_executable(bundle / "ffmpeg")
    _make_executable(bundle / "ffprobe")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    rt = resolve_runtime()
    assert rt.ffmpeg_binary == str(bundle / "ffmpeg")
    assert rt.ffprobe_binary == str(bundle / "ffprobe")


def test_executable_dir_used_when_no_meipass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without _MEIPASS, ffmpeg next to ``sys.executable`` is picked up."""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    bin_dir = tmp_path / "venv-bin"
    bin_dir.mkdir()
    fake_exe = bin_dir / "python-fake"
    fake_exe.write_text("")
    fake_exe.chmod(0o755)
    _make_executable(bin_dir / "ffmpeg")
    _make_executable(bin_dir / "ffprobe")
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    rt = resolve_runtime()
    assert rt.ffmpeg_binary == str(bin_dir / "ffmpeg")
    assert rt.ffprobe_binary == str(bin_dir / "ffprobe")


def test_env_var_beats_bundled_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``SPLITSMITH_FFMPEG`` overrides the bundled lookup."""
    bundle = tmp_path / "meipass"
    bundle.mkdir()
    _make_executable(bundle / "ffmpeg")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    override = tmp_path / "override-ffmpeg"
    _make_executable(override)
    monkeypatch.setenv(ENV_FFMPEG, str(override))

    rt = resolve_runtime()
    assert rt.ffmpeg_binary == str(override)


def test_explicit_kwarg_beats_bundled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = tmp_path / "meipass"
    bundle.mkdir()
    _make_executable(bundle / "ffmpeg")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    rt = resolve_runtime(ffmpeg_binary="/explicit/ffmpeg")
    assert rt.ffmpeg_binary == "/explicit/ffmpeg"


def test_falls_back_to_bare_name_when_no_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No env / no _MEIPASS / nothing next to executable -> bare name for PATH lookup."""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    empty_dir = tmp_path / "no-binaries"
    empty_dir.mkdir()
    fake_exe = empty_dir / "python-fake"
    fake_exe.write_text("")
    fake_exe.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    rt = resolve_runtime()
    assert rt.ffmpeg_binary == "ffmpeg"
    assert rt.ffprobe_binary == "ffprobe"


def test_non_executable_candidate_is_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A file named 'ffmpeg' without +x doesn't get picked up."""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    bin_dir = tmp_path / "venv-bin"
    bin_dir.mkdir()
    fake_exe = bin_dir / "python-fake"
    fake_exe.write_text("")
    fake_exe.chmod(0o755)
    not_executable = bin_dir / "ffmpeg"
    not_executable.write_text("")
    not_executable.chmod(0o644)
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    rt = resolve_runtime()
    assert rt.ffmpeg_binary == "ffmpeg"  # fell through to PATH lookup
