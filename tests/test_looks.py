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


def test_cli_theme_accepts_an_installed_user_look(user_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--theme`` validates against the installed Looks, not a literal pair."""
    from splitsmith.cli import _validate_theme

    _write_look(user_dir, "club")
    assert _validate_theme("club") == "club"
    with pytest.raises(Exception, match="nope"):
        _validate_theme("nope")


def test_a_broken_user_override_of_a_shipped_look_falls_back_to_the_shipped_one(
    user_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``splitsmith`` is the default every renderer asks for. One bad file
    in the user directory must not fail every default export; it is
    skipped with a warning, exactly as ``list_looks`` skips it."""
    d = user_dir / "splitsmith"
    d.mkdir(parents=True)
    (d / "look.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="splitsmith.looks"):
        look = looks.load_look("splitsmith")
    assert look.source == "shipped"
    assert "splitsmith" in caplog.text


def test_a_broken_user_only_look_still_raises(user_dir: Path) -> None:
    d = user_dir / "club"
    d.mkdir(parents=True)
    (d / "look.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(looks.LookError):
        looks.load_look("club")


def test_cli_theme_validator_is_defined_before_the_main_guard() -> None:
    """``python -m splitsmith.cli`` runs the app at the ``__main__`` guard;
    a helper defined below it would be a NameError on that path."""
    import inspect

    from splitsmith import cli

    source = inspect.getsource(cli)
    assert source.index("def _validate_theme") < source.index('if __name__ == "__main__"')


# --- variants (slice 2, #1242) ---------------------------------------------------


def test_a_bare_slot_string_is_the_default_variant() -> None:
    clean = looks.load_look("clean")
    assert clean.variants("slate") == ()
    assert looks.variants_for(clean, "slate")[0] == "default"


def test_the_shipped_splitsmith_names_a_rise_variant_for_every_card_slot() -> None:
    look = looks.load_look("splitsmith")
    for slot in ("title_page", "slate", "lower_third", "closing"):
        assert look.variants(slot) == ("default", "rise"), slot
        rise = look.own_template(slot, "rise")
        assert rise is not None and rise.is_file()


def test_a_user_look_may_declare_variants_as_a_map(user_dir: Path) -> None:
    d = _write_look(user_dir, "club", slots={"slate": "slate.html"})
    manifest = json.loads((d / "look.json").read_text(encoding="utf-8"))
    manifest["slots"] = {"slate": {"default": "slate.html", "wipe": "wipe.html"}}
    (d / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "wipe.html").write_text("<!doctype html>", encoding="utf-8")
    club = looks.load_look("club")
    assert club.variants("slate") == ("default", "wipe")
    assert looks.template_for(club, "slate", "wipe") == d / "wipe.html"


def test_a_variant_file_must_exist_and_a_variant_name_has_a_shape(user_dir: Path) -> None:
    d = _write_look(user_dir, "club", slots={"slate": "slate.html"})
    manifest = json.loads((d / "look.json").read_text(encoding="utf-8"))
    manifest["slots"] = {"slate": {"default": "slate.html", "wipe": "wipe.html"}}
    (d / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(looks.LookError, match="wipe.html"):
        looks.load_look("club")
    manifest["slots"] = {"slate": {"Bad Name": "slate.html"}}
    (d / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(looks.LookError):
        looks.load_look("club")


def test_a_variant_the_look_lacks_comes_from_the_shipped_default_look(user_dir: Path) -> None:
    club = looks.load_look(_write_look(user_dir, "club").name)
    expected = looks.load_look("splitsmith").own_template("slate", "rise")
    assert looks.template_for(club, "slate", "rise") == expected


def test_an_unknown_variant_falls_back_to_default_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    look = looks.load_look("splitsmith")
    with caplog.at_level(logging.WARNING, logger="splitsmith.looks"):
        path = looks.template_for(look, "slate", "nope")
    assert path == look.own_template("slate")
    assert "nope" in caplog.text


# --- accent series (slice 3, #1243) ----------------------------------------------


def test_the_shipped_looks_carry_an_accent_series_of_hex_colours() -> None:
    for name in ("splitsmith", "clean"):
        series = looks.load_look(name).accent_series
        assert len(series) >= 6 and all(len(c) == 7 and c.startswith("#") for c in series), name


def test_an_accent_series_entry_must_be_a_hex_colour(user_dir: Path) -> None:
    d = _write_look(user_dir, "club")
    manifest = json.loads((d / "look.json").read_text(encoding="utf-8"))
    manifest["accent_series"] = ["red"]
    (d / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(looks.LookError):
        looks.load_look("club")


# --- stings (slice 5, #1245) --------------------------------------------------------


def test_the_shipped_look_declares_the_wipe_sting() -> None:
    look = looks.load_look("splitsmith")
    path = looks.sting_template_for(look, "wipe")
    assert path is not None and path.name == "sting-wipe.html" and path.is_file()
    assert look.variants("transition") == ("wipe",)


def test_a_missing_sting_is_none_not_a_fallback(user_dir: Path) -> None:
    """A sting the Look lacks falls back to the shipped Look's sting of
    that name and to nothing else: the renderer decides on a fade."""
    club = looks.load_look(_write_look(user_dir, "club").name)
    assert looks.sting_template_for(club, "nope") is None
    shipped = looks.load_look("splitsmith").own_template("transition", "wipe")
    assert looks.sting_template_for(club, "wipe") == shipped


def test_a_user_look_may_declare_its_own_sting(user_dir: Path) -> None:
    d = _write_look(user_dir, "club")
    manifest = json.loads((d / "look.json").read_text(encoding="utf-8"))
    manifest["slots"] = {"transition": {"wipe": "my-wipe.html"}}
    (d / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    (d / "my-wipe.html").write_text("<!doctype html>", encoding="utf-8")
    club = looks.load_look("club")
    assert looks.sting_template_for(club, "wipe") == d / "my-wipe.html"


# --- the catalog (slice 6, #1246) ----------------------------------------------------


def test_the_catalog_lists_every_slot_with_default_first_and_previews(user_dir: Path) -> None:
    catalog = looks.look_catalog()
    assert [c.name for c in catalog][:2] == ["splitsmith", "clean"]
    splitsmith = catalog[0]
    assert splitsmith.label == "Splitsmith" and splitsmith.source == "shipped"
    assert set(splitsmith.slots) == set(looks.SLOT_NAMES)
    assert [v.name for v in splitsmith.slots["slate"]] == ["default", "rise"]
    assert [v.name for v in splitsmith.slots["transition"]] == ["wipe"]
    assert splitsmith.slots["summary"] == []
    assert splitsmith.preview == "/api/looks/splitsmith/preview/look.png"
    assert splitsmith.slots["slate"][1].preview == "/api/looks/splitsmith/preview/slate-rise.png"
    assert splitsmith.slots["transition"][0].preview == "/api/looks/splitsmith/preview/transition-wipe.webp"
    assert splitsmith.accent_series[0] == "#ff2d2d"


def test_every_shipped_look_resolves_a_preview_for_every_variant() -> None:
    for look in looks.list_looks():
        if look.source != "shipped":
            continue
        assert looks.preview_file(look, "look", "default") is not None, look.name
        for slot in ("title_page", "slate", "lower_third", "closing", "transition"):
            for variant in looks.variants_for(look, slot):
                assert looks.preview_file(look, slot, variant) is not None, (look.name, slot, variant)


def test_a_user_look_without_previews_borrows_the_shipped_defaults(user_dir: Path) -> None:
    _write_look(user_dir, "club")
    club = next(c for c in looks.look_catalog() if c.name == "club")
    assert club.source == "user"
    assert club.slots["slate"][1].preview == "/api/looks/_shipped/preview/slate-rise.png"
    assert club.preview == "/api/looks/_shipped/preview/look.png"
    assert looks.preview_file(looks.load_look("club"), "slate", "rise") == (
        looks.shipped_looks_dir() / "splitsmith" / "preview" / "slate-rise.png"
    )


def test_a_user_look_with_its_own_preview_serves_it_from_its_own_name(user_dir: Path) -> None:
    d = _write_look(user_dir, "club")
    (d / "preview").mkdir()
    (d / "preview" / "look.webp").write_bytes(b"RIFF")
    club = next(c for c in looks.look_catalog() if c.name == "club")
    assert club.preview == "/api/looks/club/preview/look.webp"


def test_a_user_look_shadowing_the_shipped_name_still_borrows_the_shipped_previews(user_dir: Path) -> None:
    """Review of #1246: a user ``splitsmith`` without previews, and another
    user Look beside it, both show the shipped default's pictures, served
    from the ``_shipped`` owner (the name ``splitsmith`` now resolves to
    the user's directory)."""
    _write_look(user_dir, "splitsmith")
    _write_look(user_dir, "foo")
    catalog = {c.name: c for c in looks.look_catalog()}
    assert catalog["splitsmith"].source == "user"
    assert catalog["splitsmith"].preview == "/api/looks/_shipped/preview/look.png"
    assert catalog["splitsmith"].slots["slate"][1].preview == "/api/looks/_shipped/preview/slate-rise.png"
    assert catalog["foo"].preview == "/api/looks/_shipped/preview/look.png"
    assert looks.preview_owner_root("_shipped") == looks.shipped_looks_dir() / "splitsmith"
    assert looks.preview_owner_root("foo") == user_dir / "foo"
    assert looks.preview_owner_root("nope") is None


# --- styles, base and the user-Looks provider (#1263) ------------------------------


def test_styles_pick_the_variant_a_slots_default_draws(user_dir: Path) -> None:
    d = _write_look(user_dir, "club")
    manifest = json.loads((d / "look.json").read_text(encoding="utf-8"))
    manifest.update(base="clean", styles={"slate": "rise"})
    (d / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    club = looks.load_look("club")
    shipped = looks.load_look("splitsmith")
    assert club.manifest.base == "clean"
    assert looks.template_for(club, "slate") == shipped.own_template("slate", "rise")
    assert looks.template_for(club, "slate", "default") == shipped.own_template("slate", "rise")
    assert looks.template_for(club, "title_page") == shipped.own_template("title_page")


@pytest.mark.parametrize(
    "styles, message",
    [
        ({"summary": "rise"}, "card slot"),
        ({"slate": "Rise!"}, "variant name"),
        ({"transition": "x"}, "card slot"),
    ],
)
def test_styles_name_a_card_slot_and_a_variant_shape(user_dir: Path, styles: dict, message: str) -> None:
    raw = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    raw.update(name="club", styles=styles)
    with pytest.raises(ValueError, match=message):
        looks.LookManifest.model_validate(raw)


def test_a_provider_replaces_the_user_looks_folder_and_none_means_no_user_looks(
    user_dir: Path, tmp_path: Path
) -> None:
    _write_look(user_dir, "home-look")
    elsewhere = tmp_path / "tenant"
    _write_look(elsewhere, "tenant-look")
    token = looks.set_user_looks_provider(lambda: elsewhere)
    try:
        assert looks.user_looks_dir() == elsewhere
        assert "tenant-look" in looks.look_names() and "home-look" not in looks.look_names()
    finally:
        looks.reset_user_looks_provider(token)
    token = looks.set_user_looks_provider(lambda: None)
    try:
        assert looks.look_names() == ("splitsmith", "clean")
        with pytest.raises(looks.LookNotFoundError):
            looks.load_look("home-look")
    finally:
        looks.reset_user_looks_provider(token)
    assert "home-look" in looks.look_names()


def test_the_catalog_marks_user_looks_editable(user_dir: Path) -> None:
    _write_look(user_dir, "club")
    editable = {info.name: info.editable for info in looks.look_catalog()}
    assert editable == {"splitsmith": False, "clean": False, "club": True}


# --- the overlay slot (template HUD, spec 2026-10-08) ------------------------


def _manifest(name: str, slots: dict) -> dict:
    shipped = looks.load_look("splitsmith").manifest
    return {"name": name, "colors": {k: list(v) for k, v in shipped.colors.items()}, "slots": slots}


def test_overlay_is_a_slot_and_default_means_classic(user_dir: Path) -> None:
    assert "overlay" in looks.SLOT_NAMES and looks.OVERLAY_SLOT == "overlay"
    splitsmith = looks.load_look("splitsmith")
    assert looks.overlay_template_for(splitsmith, "default") is None


def test_a_manifest_cannot_name_a_template_for_the_classic_overlay(user_dir: Path) -> None:
    with pytest.raises(ValueError, match="Classic"):
        looks.LookManifest.model_validate(_manifest("mine", {"overlay": {"default": "hud.html"}}))
    with pytest.raises(ValueError, match="Classic"):
        looks.LookManifest.model_validate(_manifest("mine", {"overlay": "hud.html"}))


def test_a_look_without_the_slot_borrows_the_shipped_overlay_template(user_dir: Path) -> None:
    clean = looks.load_look("clean")
    template = looks.overlay_template_for(clean, "plate")
    assert template is not None and template.name == "hud-plate.html"
    assert template.parent == looks.shipped_looks_dir() / "splitsmith"


def test_an_overlay_variant_no_look_has_is_none(user_dir: Path) -> None:
    assert looks.overlay_template_for(looks.load_look("splitsmith"), "nope") is None


def test_the_catalog_lists_the_overlay_variants(user_dir: Path) -> None:
    splitsmith = next(c for c in looks.look_catalog() if c.name == "splitsmith")
    assert [v.name for v in splitsmith.slots["overlay"]] == ["minimal", "pips", "plate", "ticker", "timeline"]


def test_the_catalog_lists_the_positions_of_each_overlay_style(user_dir: Path) -> None:
    """The gallery offers a position only for a style that declares some,
    in the template's own order (its default first)."""
    splitsmith = next(c for c in looks.look_catalog() if c.name == "splitsmith")
    positions = {v.name: v.positions for v in splitsmith.slots["overlay"]}
    assert positions["plate"] == ["bottom-left", "top-left", "top-right", "bottom-right"]
    assert positions["ticker"] == ["top-right", "top-left"]
    assert positions["timeline"] == [] and positions["minimal"] == []
    assert all(v.positions == [] for v in splitsmith.slots["slate"])
