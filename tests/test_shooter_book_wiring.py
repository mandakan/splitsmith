"""Every export and the preview read the shooter book (spec 2026-10-08).

The conftest points ``SPLITSMITH_HOME`` at a fresh directory per test, so
``JsonShooterBookStore()`` here is that test's own book.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from splitsmith.identity import ShooterIdentity
from splitsmith.match_project import MatchProject
from splitsmith.shooter_book import JsonShooterBookStore, ShooterBookEntry

SID = 40821
BOOK = ShooterIdentity(accent="#aa0000", club="Bromma PK")


def _book_entry(identity: ShooterIdentity = BOOK) -> None:
    asyncio.run(JsonShooterBookStore().put(ShooterBookEntry(shooter_id=SID, identity=identity, label="Me")))


def _pin(shooter_root: Path) -> None:
    project = MatchProject.load(shooter_root)
    project.selected_shooter_id = SID
    project.save(shooter_root)


def test_the_match_export_job_reads_the_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.ui import match_exports as match_exports_mod
    from tests.test_ui_server import _seed_match_export_project, _stub_match_export_probe, _wait_for_job

    client, root = _seed_match_export_project(tmp_path)
    _stub_match_export_probe(monkeypatch)
    _pin(next((root / "shooters").iterdir()))
    _book_entry()
    seen: list[Any] = []
    real = match_exports_mod.export_match

    def capture(*args: object, **kwargs: object) -> object:
        seen.append(kwargs["request"])
        return real(*args, **kwargs)

    monkeypatch.setattr(match_exports_mod, "export_match", capture)
    resp = client.post("/api/shooters/me/export/match", json={"stage_numbers": [1], "include_overlay": False})
    assert resp.status_code == 200, resp.text
    assert _wait_for_job(client, resp.json()["id"])["status"] == "succeeded"
    assert (seen[0].shooter_identity.accent, seen[0].shooter_identity.club) == ("#aa0000", "Bromma PK")


def test_the_grid_job_reads_the_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.match_model import Match
    from tests.test_compare_grid_endpoint import (
        _fake_probe,
        _fake_render_grid_mp4,
        _match_create_app,
        _MatchClient,
        _seed_match,
        _wait_for_job,
        _write_trims,
        mp4_grid_mod,
        pl_mod,
    )

    captured: list[dict[str, Any]] = []

    def fake_render(shooters: Any, *, audio_label: str, output_path: Path, **kwargs: Any) -> Any:
        captured.append(kwargs)
        return _fake_render_grid_mp4(shooters, audio_label=audio_label, output_path=output_path)

    monkeypatch.setattr(pl_mod.fcpxml_gen, "probe_video", _fake_probe)
    monkeypatch.setattr(mp4_grid_mod, "render_grid_mp4", fake_render)
    match_root = _seed_match(tmp_path, shooters=["mathias"], stage_numbers=[1])
    _write_trims(match_root, slug="mathias", stage_numbers=[1])
    _pin(Match.shooter_root(match_root, "mathias"))
    _book_entry()
    client = _MatchClient(_match_create_app(project_root=match_root, project_name="Compare Match"))
    response = client.post("/api/match/compare-export", json={"stage_numbers": [1], "audio_from": "mathias"})
    assert response.status_code == 200
    assert _wait_for_job(client, response.json()["id"])["status"] == "succeeded"
    (identity,) = captured[-1]["identities"].values()
    assert identity.accent == "#aa0000"


def test_the_match_cli_reads_the_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.cli import app
    from tests.test_match_cli_export import _capture_mp4, _seed, runner

    root = _seed(tmp_path)
    _pin(root / "shooters" / "me")
    _book_entry()
    captured = _capture_mp4(monkeypatch)
    result = runner.invoke(
        app,
        ["match", "export", str(root), "--shooter", "me", "--format", "mp4", "-o", str(tmp_path / "o.mp4")],
    )
    assert result.exit_code == 0, result.output
    assert [s.accent for s in captured["comp"].shooters] == ["#aa0000"]


def test_the_compare_cli_reads_the_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.match_model import Match
    from tests.test_compare_cli_mp4 import _capture_render, _invoke_mp4, _patch_probe, _seed_match_with_stages

    match_root = _seed_match_with_stages(tmp_path / "match", stage_count=1)
    _pin(Match.shooter_root(match_root, "mathias"))
    _book_entry()
    _patch_probe(monkeypatch)
    captured = _capture_render(monkeypatch)
    result = _invoke_mp4(match_root, tmp_path / "out.mp4")
    assert result.exit_code == 0, result.output
    assert captured["identities"]["Mathias"].accent == "#aa0000"


def test_a_hosted_request_without_a_tenant_never_reads_the_local_book(tmp_path: Path) -> None:
    from splitsmith.account_profile import EmptyAccountProfileStore
    from splitsmith.shooter_book import EmptyShooterBookStore
    from splitsmith.ui.server import AppState

    _book_entry()
    state = AppState()
    assert isinstance(state.shooter_book, JsonShooterBookStore)
    state._build_tenant = lambda user_id: None  # type: ignore[assignment,return-value]
    assert isinstance(state.shooter_book, EmptyShooterBookStore)
    assert isinstance(state.account_profile, EmptyAccountProfileStore)


def test_the_preview_draws_the_book_and_a_book_edit_moves_its_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from splitsmith import export_preview
    from splitsmith.ui import export_preview_api
    from tests.test_ui_server import _seed_match_export_project

    client, root = _seed_match_export_project(tmp_path)
    _pin(next((root / "shooters").iterdir()))
    keys: list[str] = []
    drawn: list[Any] = []
    real_key = export_preview_api.preview_key

    def spy_key(*args: Any, **kwargs: Any) -> str:
        keys.append(real_key(*args, **kwargs))
        return keys[-1]

    def fake_render(spec: Any, **kwargs: Any) -> bytes:
        drawn.append(kwargs["shooter"])
        from tests.test_shooter_book import _png

        return _png()

    monkeypatch.setattr(export_preview_api, "preview_key", spy_key)
    monkeypatch.setattr(export_preview_api, "render_preview", fake_render)
    monkeypatch.setattr(export_preview_api, "ChromiumRasterizer", _NoopRasterizer)
    body = {"card": "title", "stage_number": 1, "width": 480}
    assert client.post("/api/shooters/me/export-preview", json=body).status_code == 200
    _book_entry()
    assert client.post("/api/shooters/me/export-preview", json=body).status_code == 200
    _book_entry(ShooterIdentity(accent="#00aa00"))
    assert client.post("/api/shooters/me/export-preview", json=body).status_code == 200
    assert len(set(keys)) == 3, "no book, a book entry and an edited entry are three pictures"
    assert [d.accent for d in drawn] == [None, "#aa0000", "#00aa00"]
    assert export_preview.PreviewSpec.__dataclass_fields__["book_identity"].default is None


class _NoopRasterizer:
    def __enter__(self) -> _NoopRasterizer:
        return self

    def __exit__(self, *exc: object) -> None:
        pass
