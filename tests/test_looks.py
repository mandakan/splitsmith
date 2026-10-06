"""The Look directory: loading, listing, precedence, template fallback."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from splitsmith import looks


def _write_look(root: Path, name: str, *, slots: dict[str, str] | None = None, colors=None) -> Path:
    base = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    base["name"] = name
    base["slots"] = slots or {}
    if colors is not None:
        base["colors"] = colors
    d = root / name
    d.mkdir(parents=True)
    (d / "look.json").write_text(json.dumps(base), encoding="utf-8")
    for file in (slots or {}).values():
        (d / file).write_text("<!doctype html>", encoding="utf-8")
    return d


@pytest.fixture
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    return tmp_path / "looks"


def test_the_two_shipped_looks_load_with_the_default_first(user_dir: Path) -> None:
    names = [look.name for look in looks.list_looks()]
    assert names[:2] == ["splitsmith", "clean"]
    assert all(look.source == "shipped" for look in looks.list_looks())


def test_shipped_splitsmith_declares_every_card_slot_and_the_files_exist() -> None:
    look = looks.load_look("splitsmith")
    for slot in ("title_page", "slate", "lower_third", "closing"):
        path = look.own_template(slot)
        assert path is not None and path.is_file(), slot


def test_clean_declares_no_slots_and_falls_back_to_the_default_templates() -> None:
    clean = looks.load_look("clean")
    assert clean.own_template("slate") is None
    assert looks.template_for(clean, "slate") == looks.load_look("splitsmith").own_template("slate")


def test_a_user_look_is_listed_after_the_shipped_ones(user_dir: Path) -> None:
    _write_look(user_dir, "club")
    names = [look.name for look in looks.list_looks()]
    assert names == ["splitsmith", "clean", "club"]
    assert looks.load_look("club").source == "user"


def test_a_user_look_shadows_a_shipped_look_of_the_same_name(user_dir: Path) -> None:
    _write_look(user_dir, "clean", colors=None)
    assert looks.load_look("clean").source == "user"
    assert [look.name for look in looks.list_looks()].count("clean") == 1


def test_unknown_look_raises_not_found(user_dir: Path) -> None:
    with pytest.raises(looks.LookNotFoundError):
        looks.load_look("nope")


def test_list_looks_skips_a_broken_user_look_and_warns(
    user_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    broken = user_dir / "broken"
    broken.mkdir(parents=True)
    (broken / "look.json").write_text("{not json", encoding="utf-8")
    (user_dir / "no-manifest").mkdir()
    with caplog.at_level(logging.WARNING, logger="splitsmith.looks"):
        names = [look.name for look in looks.list_looks()]
    assert names == ["splitsmith", "clean"]
    assert "broken" in caplog.text


def test_a_declared_slot_file_must_exist(user_dir: Path) -> None:
    d = _write_look(user_dir, "club", slots={"slate": "slate.html"})
    (d / "slate.html").unlink()
    with pytest.raises(looks.LookError, match="slate.html"):
        looks.load_look("club")


@pytest.mark.parametrize("bad", ["../x.html", "sub/x.html", "x.htm", "x"])
def test_a_slot_file_is_a_bare_html_file_name(user_dir: Path, bad: str) -> None:
    d = user_dir / "club"
    d.mkdir(parents=True)
    manifest = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    manifest["name"] = "club"
    manifest["slots"] = {"slate": bad}
    (d / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(looks.LookError):
        looks.load_look("club")


def test_a_manifest_must_carry_every_required_colour(user_dir: Path) -> None:
    colors = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))[
        "colors"
    ]
    del colors["ink"]
    _write_look(user_dir, "club", colors=colors)
    with pytest.raises(looks.LookError, match="ink"):
        looks.load_look("club")


def test_the_manifest_name_must_match_its_directory(user_dir: Path) -> None:
    d = _write_look(user_dir, "club")
    manifest = json.loads((d / "look.json").read_text(encoding="utf-8"))
    manifest["name"] = "other"
    (d / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(looks.LookError, match="other"):
        looks.load_look("club")


def test_shared_dir_holds_the_engine_scripts() -> None:
    assert looks.shared_dir().name == "_shared"
    assert looks.shared_dir().parent == looks.shipped_looks_dir()
