"""``/api/system/chromium`` routes (desktop packaging spec, "First-run downloads")."""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from splitsmith.overlay_raster import RasterizerUnavailableError
from splitsmith.ui import system_api
from splitsmith.ui.server import create_app

STATUS = "/api/system/chromium"
INSTALL = "/api/system/chromium/install"


@contextmanager
def _browser_present():
    yield object()


@contextmanager
def _browser_missing():
    raise RasterizerUnavailableError("no browser", "install one")
    yield  # pragma: no cover


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)
    return TestClient(create_app())


def _wait_terminal(client: TestClient, job_id: str, timeout_s: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = client.get(f"/api/me/jobs/{job_id}").json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def test_status_reports_installed_when_the_rasterizer_launches(client, monkeypatch) -> None:
    monkeypatch.setattr(system_api, "rasterizer_factory", _browser_present)
    r = client.get(STATUS)
    assert r.status_code == 200
    assert r.json() == {"installed": True, "channel": "chromium-headless-shell"}


def test_status_reports_missing_when_it_cannot(client, monkeypatch) -> None:
    monkeypatch.setattr(system_api, "rasterizer_factory", _browser_missing)
    assert client.get(STATUS).json()["installed"] is False


def test_install_runs_playwright_with_the_running_interpreter(client, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], handle: Any) -> int:
        calls.append(argv)
        handle.update(progress=1.0, message="done")
        return 0

    monkeypatch.setattr(system_api, "_run_install", fake_run)
    r = client.post(INSTALL)
    assert r.status_code == 202, r.text
    job = _wait_terminal(client, r.json()["id"])
    assert job["status"] == "succeeded", job
    assert calls == [[sys.executable, "-m", "playwright", "install", "chromium-headless-shell"]]


def test_install_failure_fails_the_job(client, monkeypatch) -> None:
    monkeypatch.setattr(system_api, "_run_install", lambda argv, handle: 3)
    job = _wait_terminal(client, client.post(INSTALL).json()["id"])
    assert job["status"] == "failed"
    assert "exited 3" in (job.get("error") or "")


def test_install_is_idempotent_while_running(client, monkeypatch) -> None:
    gate = threading.Event()

    def slow_run(argv: list[str], handle: Any) -> int:
        gate.wait(5)
        return 0

    monkeypatch.setattr(system_api, "_run_install", slow_run)
    first = client.post(INSTALL).json()
    second = client.post(INSTALL).json()
    gate.set()
    assert second["id"] == first["id"]
    _wait_terminal(client, first["id"])


def test_routes_are_404_hosted(client, monkeypatch) -> None:
    monkeypatch.setattr(system_api, "_hosted", lambda: True)
    assert client.get(STATUS).status_code == 404
    assert client.post(INSTALL).status_code == 404
