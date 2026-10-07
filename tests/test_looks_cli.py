"""``splitsmith looks``: list, new, check, preview (issue #1262). Chromium is a fake here."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from splitsmith import looks_cli
from splitsmith.cli import app
from splitsmith.overlay_raster import TemplateProbe
from tests.test_look_tools import _Prober, _Raster

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    return tmp_path


def _use(monkeypatch: pytest.MonkeyPatch, fake) -> None:  # type: ignore[no-untyped-def]
    @contextmanager
    def chromium():  # type: ignore[no-untyped-def]
        yield fake

    monkeypatch.setattr(looks_cli, "open_chromium", chromium)


def test_list_names_the_shipped_looks_and_a_new_one(home: Path) -> None:
    runner.invoke(app, ["looks", "new", "club-red", "--from", "splitsmith"])
    result = runner.invoke(app, ["looks", "list"])
    assert result.exit_code == 0, result.output
    for name in ("splitsmith", "clean", "club-red"):
        assert name in result.output
    assert "user" in result.output and "shipped" in result.output


def test_new_prints_where_and_what_next_and_refuses_a_duplicate(home: Path) -> None:
    result = runner.invoke(app, ["looks", "new", "mine", "--starter", "animated"])
    assert result.exit_code == 0, result.output
    assert str(home / "looks" / "mine") in result.output.replace("\n", "")
    assert "looks check mine" in result.output and "authoring.md" in result.output
    again = runner.invoke(app, ["looks", "new", "mine", "--starter", "still"])
    assert again.exit_code == 2 and "already exists" in again.output


def test_check_exits_0_when_clean_and_1_on_an_error(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner.invoke(app, ["looks", "new", "mine", "--starter", "still"])
    _use(monkeypatch, _Prober())
    ok = runner.invoke(app, ["looks", "check", "mine"])
    assert ok.exit_code == 0, ok.output
    assert "0 errors" in ok.output
    _use(monkeypatch, _Prober(TemplateProbe(errors=("boom",))))
    bad = runner.invoke(app, ["looks", "check", "mine"])
    assert bad.exit_code == 1
    assert "script error: boom" in bad.output


def test_check_without_chromium_says_how_to_install_it(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith.overlay_raster import RasterizerUnavailableError

    @contextmanager
    def missing():  # type: ignore[no-untyped-def]
        raise RasterizerUnavailableError("no browser", "Chromium is not installed")
        yield  # pragma: no cover

    monkeypatch.setattr(looks_cli, "open_chromium", missing)
    result = runner.invoke(app, ["looks", "check", "splitsmith"])
    assert result.exit_code == 2 and "Chromium is not installed" in result.output


def test_preview_writes_the_cards_and_names_the_contact_sheet(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, _Raster())
    out = home / "out"
    result = runner.invoke(app, ["looks", "preview", "clean", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "contact-sheet.png").is_file() and (out / "slate-default.png").is_file()
    assert "contact-sheet.png" in result.output
