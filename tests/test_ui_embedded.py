"""Tests for the embedded UI server entrypoint (issue #131)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from splitsmith.ui.embedded import (
    READY_PREFIX,
    run_embedded,
)


def test_context_manager_binds_serves_health_and_shuts_down() -> None:
    with run_embedded(port=0) as handle:
        assert handle.port != 0
        resp = httpx.get(f"{handle.base_url}/api/health", timeout=5.0)
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "ok"
        assert "version" in payload

    # After __exit__, the port must be released. Re-binding it from
    # another socket should succeed (i.e. the server is actually gone).
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"http://127.0.0.1:{handle.port}/api/health", timeout=0.5)


def test_port_zero_yields_nonzero_in_handle() -> None:
    with run_embedded(port=0) as handle:
        assert handle.port > 0
        assert handle.host == "127.0.0.1"
        assert handle.base_url == f"http://127.0.0.1:{handle.port}"


def test_handle_reflects_resolved_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Acceptance: env override visible end-to-end on the handle."""
    from splitsmith import runtime as runtime_module

    fake_ffmpeg = tmp_path / "fake-ffmpeg"
    fake_ffmpeg.write_text("#!/bin/sh\nexit 0\n")
    fake_ffmpeg.chmod(0o755)

    monkeypatch.setenv("SPLITSMITH_FFMPEG", str(fake_ffmpeg))
    runtime_module._clear_runtime_cache()
    try:
        with run_embedded(port=0) as handle:
            assert handle.ffmpeg_binary == str(fake_ffmpeg)
    finally:
        runtime_module._clear_runtime_cache()


def test_banner_is_well_formed_json() -> None:
    with run_embedded(port=0) as handle:
        banner = handle.as_banner()
    prefix, _, payload = banner.partition(" ")
    assert prefix == READY_PREFIX
    parsed = json.loads(payload)
    assert set(parsed) == {
        "host",
        "port",
        "pid",
        "base_url",
        "artifacts_dir",
        "ffmpeg_binary",
        "log_file",
    }
    assert parsed["host"] == handle.host
    assert parsed["port"] == handle.port


def test_ready_fd_receives_banner_before_yield() -> None:
    """The shell-style handshake: a pipe parent reads the banner synchronously."""
    read_fd, write_fd = os.pipe()
    try:
        with run_embedded(port=0, ready_fd=write_fd) as handle:
            # By the time the context yields, the banner has been written.
            # Read non-blockingly with a tight deadline -- if the fd is
            # empty we have a regression.
            os.set_blocking(read_fd, False)
            time.sleep(0.05)
            data = os.read(read_fd, 4096).decode("utf-8")
    finally:
        os.close(read_fd)
        try:
            os.close(write_fd)
        except OSError:
            pass

    assert data.startswith(READY_PREFIX + " ")
    payload = json.loads(data[len(READY_PREFIX) + 1 :].rstrip("\n"))
    assert payload["port"] == handle.port


def test_sigterm_to_main_exits_clean(tmp_path: Path) -> None:
    """The subprocess entrypoint exits 0 on SIGTERM and emits a banner."""
    env = os.environ.copy()
    env["SPLITSMITH_PORT"] = "0"
    env["SPLITSMITH_HOST"] = "127.0.0.1"
    # Keep the test isolated from the developer's real ~/.splitsmith.
    env["SPLITSMITH_HOME"] = str(tmp_path / "home")
    env["SPLITSMITH_CONFIG_DIR"] = str(tmp_path / "config")
    env["SPLITSMITH_LOG_DIR"] = str(tmp_path / "logs")

    proc = subprocess.Popen(
        [sys.executable, "-m", "splitsmith.ui.embedded"],
        env=env,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        banner_line = _read_banner(proc, deadline_s=15.0)
        proc.terminate()
        # Allow the same grace window the runtime does
        # (``DEFAULT_SHUTDOWN_TIMEOUT_S = 30s`` in ``embedded.py``) plus
        # a small buffer for the test runner's process-teardown
        # bookkeeping. The previous 10s was tighter than the runtime's
        # own drain allowance and tripped intermittently on slow CI.
        rc = proc.wait(timeout=35.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert rc == 0, f"non-zero exit {rc}; banner={banner_line!r}"
    assert banner_line.startswith(READY_PREFIX + " ")
    payload = json.loads(banner_line[len(READY_PREFIX) + 1 :])
    assert payload["port"] > 0
    assert payload["pid"] == proc.pid
    # The banner advertises a log file inside the directory we set up.
    assert payload["log_file"] is not None
    log_file = Path(payload["log_file"])
    assert log_file.parent == tmp_path / "logs"
    assert log_file.exists() and log_file.stat().st_size > 0


def _read_banner(proc: subprocess.Popen[str], *, deadline_s: float) -> str:
    """Read stderr line-by-line until ``SPLITSMITH_READY`` shows up."""
    assert proc.stderr is not None
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        line = proc.stderr.readline()
        if not line:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early (rc={proc.returncode}) before banner")
            time.sleep(0.05)
            continue
        if line.startswith(READY_PREFIX + " "):
            return line.rstrip("\n")
    raise TimeoutError("never saw SPLITSMITH_READY banner")
