"""The renderer (Playwright's headless Chromium) on a desktop install.

0.83.0's DMG did not have it, the only install button showed when a card
preview failed, and a match export ran anyway and dropped every overlay and
card. Now a local app installs it at startup when it is missing, and an
export that needs it refuses with the way out instead of rendering without.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from splitsmith.overlay import raster
from splitsmith.ui import system_api


def _pinned_revision() -> str:
    import playwright

    pins = json.loads(
        (Path(playwright.__file__).parent / "driver" / "package" / "browsers.json").read_text(
            encoding="utf-8"
        )
    )
    return next(b["revision"] for b in pins["browsers"] if b["name"] == raster.CHROMIUM_CHANNEL)


def test_the_check_reads_the_pinned_revisions_install_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert raster.renderer_installed() is False
    folder = tmp_path / f"chromium_headless_shell-{_pinned_revision()}"
    folder.mkdir()
    assert raster.renderer_installed() is False, "a half-finished install has no marker"
    (folder / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
    assert raster.renderer_installed() is True
    # Another revision's install is not this Playwright's (the DMG pinned a
    # newer one than the folder a user had).
    other = tmp_path / "other"
    (other / "chromium_headless_shell-1").mkdir(parents=True)
    (other / "chromium_headless_shell-1" / "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(other))
    assert raster.renderer_installed() is False


def test_the_install_hint_names_the_interpreter_this_process_runs_on() -> None:
    import sys

    assert raster.INSTALL_HINT.startswith(sys.executable)
    assert "uv run" not in raster.INSTALL_HINT


class _Jobs:
    def __init__(self, active: bool = False) -> None:
        self.active = active
        self.submitted: list[str] = []

    async def find_active(self, *, kind: str):  # type: ignore[no-untyped-def]
        return object() if self.active else None

    async def submit(self, *, kind: str):  # type: ignore[no-untyped-def]
        self.submitted.append(kind)


@pytest.mark.parametrize(
    "installed, active, env, expected",
    [
        (False, False, None, ["chromium_install"]),
        (True, False, None, []),
        (None, False, None, []),
        (False, True, None, []),
        (False, False, "1", []),
    ],
)
def test_a_local_app_installs_the_renderer_at_startup_only_when_it_is_missing(
    monkeypatch: pytest.MonkeyPatch, installed, active, env, expected
) -> None:
    monkeypatch.setattr(system_api, "renderer_installed", lambda: installed)
    monkeypatch.delenv("SPLITSMITH_MODE", raising=False)
    if env is None:
        monkeypatch.delenv(system_api.ENV_NO_RENDERER_PREFETCH, raising=False)
    else:
        monkeypatch.setenv(system_api.ENV_NO_RENDERER_PREFETCH, env)
    jobs = _Jobs(active=active)
    asyncio.run(system_api.maybe_install_renderer(jobs))
    assert jobs.submitted == expected


def test_hosted_never_installs_or_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_api, "renderer_installed", lambda: False)
    monkeypatch.setenv("SPLITSMITH_MODE", "hosted")
    monkeypatch.delenv(system_api.ENV_NO_RENDERER_PREFETCH, raising=False)
    jobs = _Jobs()
    asyncio.run(system_api.maybe_install_renderer(jobs))
    assert jobs.submitted == []
    system_api.require_renderer()  # no raise


def test_a_match_export_that_draws_overlays_refuses_without_the_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .ui._helpers import _seed_match_export_project

    client, _root = _seed_match_export_project(tmp_path, stage_count=1)
    monkeypatch.setattr(system_api, "renderer_installed", lambda: False)
    resp = client.post(
        "/api/shooters/me/export/match",
        json={"stage_numbers": [1], "output_format": "mp4", "include_overlay": True},
    )
    assert resp.status_code == 409, resp.text
    assert "Install renderer" in resp.json()["detail"]


def test_what_needs_the_renderer() -> None:
    from splitsmith.ui.exports_api import MatchExportRequest, match_export_needs_renderer

    plain = MatchExportRequest(stage_numbers=[1], include_overlay=False)
    assert match_export_needs_renderer(plain) is False
    for change in (
        {"include_overlay": True},
        {"title_page": True},
        {"closing_card": True},
        {"title_kind": "slate"},
        {"summary_hold_seconds": 2.0},
        {"match_summary": True},
    ):
        assert match_export_needs_renderer(plain.model_copy(update=change)) is True, change
