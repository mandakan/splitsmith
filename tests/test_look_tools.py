"""``splitsmith.look_tools``: making, checking and previewing a Look (issue #1262).

The checks run against a fake prober here; ``test_look_tools_chromium.py``
runs the real one.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from splitsmith import look_tools, looks
from splitsmith.overlay_raster import TemplateProbe


@pytest.fixture
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    return tmp_path / "looks"


# --- new -----------------------------------------------------------------------------


def test_new_from_a_shipped_look_follows_its_templates_instead_of_copying_them(user_dir: Path) -> None:
    """A copy of the shipped templates froze them: the brand, the event logo
    and the credit shipped later and never reached a Look duplicated before
    them. The copy takes the manifest and draws the current shipped files."""
    root = look_tools.new_look("club-red", from_look="splitsmith")
    assert root == user_dir / "club-red"
    manifest = json.loads((root / "look.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "club-red" and manifest["label"] == "Club red"
    assert manifest["slots"] == {} and manifest["base"] == "splitsmith"
    assert sorted(p.name for p in root.iterdir()) == ["look.json"]
    look = looks.load_look("club-red")
    assert look.source == "user"
    shipped = looks.shipped_looks_dir() / "splitsmith"
    assert looks.template_for(look, "slate", "rise") == shipped / "card-rise.html"
    assert looks.overlay_template_for(look, "plate") == shipped / "hud-plate.html"


def test_new_from_a_shipped_look_with_templates_copies_them_to_edit(user_dir: Path) -> None:
    root = look_tools.new_look("club-red", from_look="splitsmith", templates=True)
    assert (root / "card.html").is_file() and (root / "sting-wipe.html").is_file()
    look = looks.load_look("club-red")
    assert looks.template_for(look, "slate", "rise") == root / "card-rise.html"


# --- copies of shipped templates -----------------------------------------------------


def test_an_unedited_copy_of_an_older_shipped_template_is_outdated(
    user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = look_tools.new_look("old-copy", from_look="splitsmith", templates=True)
    old = b"<!doctype html><p>card.html as it shipped once</p>"
    (root / "card.html").write_bytes(old)
    import hashlib

    history = {**look_tools.shipped_template_history(), hashlib.sha256(old).hexdigest(): "card.html"}
    monkeypatch.setattr(look_tools, "shipped_template_history", lambda: history)
    look = looks.load_look("old-copy")
    assert look_tools.outdated_copies(look) == ("card.html",)


def test_a_current_copy_and_an_edited_file_are_not_outdated(user_dir: Path) -> None:
    root = look_tools.new_look("mine", from_look="splitsmith", templates=True)
    (root / "card-rise.html").write_text("<!doctype html><p>my own rise</p>", encoding="utf-8")
    assert look_tools.outdated_copies(looks.load_look("mine")) == ()


def test_refresh_drops_every_unedited_copy_and_keeps_edited_files(
    user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = look_tools.new_look("old-copy", from_look="splitsmith", templates=True)
    old = b"<!doctype html><p>card.html as it shipped once</p>"
    (root / "card.html").write_bytes(old)
    (root / "card-rise.html").write_text("<!doctype html><p>my own rise</p>", encoding="utf-8")
    import hashlib

    history = {**look_tools.shipped_template_history(), hashlib.sha256(old).hexdigest(): "card.html"}
    monkeypatch.setattr(look_tools, "shipped_template_history", lambda: history)

    removed = look_tools.refresh_templates("old-copy")

    # The old card.html and every unedited current copy go; the edited rise stays.
    shipped_files = {p.name for p in (looks.shipped_looks_dir() / "splitsmith").glob("*.html")}
    assert set(removed) == shipped_files - {"card-rise.html"}
    assert not (root / "card.html").exists() and (root / "card-rise.html").is_file()
    look = looks.load_look("old-copy")
    shipped = looks.shipped_looks_dir() / "splitsmith"
    assert looks.template_for(look, "title_page") == shipped / "card.html"
    assert looks.template_for(look, "title_page", "rise") == root / "card-rise.html"
    assert looks.sting_template_for(look, "wipe") == shipped / "sting-wipe.html"
    assert "default" not in look.manifest.slots.get("title_page", {})
    assert look_tools.outdated_copies(look) == ()


def test_refresh_keeps_a_file_another_slot_still_names(
    user_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """card.html draws four slots; it goes only when every slot naming it is
    an unedited copy, which it is when the bytes are, so all four go."""
    root = look_tools.new_look("old-copy", from_look="splitsmith", templates=True)
    look_tools.refresh_templates("old-copy")
    look = looks.load_look("old-copy")
    assert not any("card.html" in v.values() for v in look.manifest.slots.values())
    assert not (root / "card.html").exists()


def test_the_history_holds_every_current_shipped_template() -> None:
    """Run scripts/record_template_history.py after changing a shipped
    template: an edit missing here would make every unedited copy of the
    new version look like an own template forever."""
    import hashlib

    history = look_tools.shipped_template_history()
    for path in sorted(looks.shipped_looks_dir().glob("*/*.html")):
        if path.parent.name.startswith("_"):
            continue
        assert history.get(hashlib.sha256(path.read_bytes()).hexdigest()) == path.name, path


@pytest.mark.parametrize(
    ("starter", "slots"),
    [
        ("still", {"title_page", "slate", "closing"}),
        ("animated", {"title_page", "slate", "closing"}),
        ("lower-third", {"lower_third"}),
        ("sting", {"transition"}),
        ("hud", {"overlay"}),
    ],
)
def test_new_from_a_starter_is_a_loadable_look_with_the_starter_in_its_slots(
    user_dir: Path, starter: str, slots: set[str]
) -> None:
    root = look_tools.new_look("mine", starter=starter)
    look = looks.load_look("mine")
    assert set(look.manifest.slots) == slots
    file = look_tools.STARTERS[starter]
    assert (root / file).read_text(encoding="utf-8") == (
        looks.shipped_looks_dir() / "_starters" / file
    ).read_text(encoding="utf-8")


def test_new_refuses_an_existing_look_a_shipped_name_and_a_bad_name(user_dir: Path) -> None:
    look_tools.new_look("club", from_look="clean")
    with pytest.raises(look_tools.LookToolError, match="already exists"):
        look_tools.new_look("club", from_look="clean")
    with pytest.raises(look_tools.LookToolError, match="shipped Look"):
        look_tools.new_look("clean", from_look="splitsmith")
    with pytest.raises(look_tools.LookToolError, match="lower-case"):
        look_tools.new_look("Club Red", from_look="clean")
    with pytest.raises(look_tools.LookToolError, match="one of"):
        look_tools.new_look("x", starter="nope")


# --- check ---------------------------------------------------------------------------


class _Prober:
    """Answers every probe with ``probe``, or per template file name. A HUD
    timeline draws the same frame at every time unless the file is in
    ``moves`` (``"before"`` / ``"after"`` the live span) or ``refuses``
    (the template lacks a hook)."""

    def __init__(
        self,
        probe: TemplateProbe | None = None,
        by_file: dict[str, TemplateProbe] | None = None,
        moves: dict[str, str] | None = None,
        refuses: dict[str, str] | None = None,
    ) -> None:
        self.probe = probe or TemplateProbe()
        self.by_file = by_file or {}
        self.moves = moves or {}
        self.refuses = refuses or {}
        self.calls: list[tuple[str, dict]] = []
        self.probed_at: list[tuple[str, float | None]] = []
        self.timelines: list[tuple[str, list[float]]] = []

    def probe_template(
        self, template: Path, *, context, width: int, height: int, at: float | None = None
    ) -> TemplateProbe:
        self.calls.append((template.name, context.data))
        self.probed_at.append((template.name, at))
        return self.by_file.get(template.name, self.probe)

    def render_template_timeline(
        self, template: Path, *, context, width: int, height: int, plan
    ):  # noqa: ANN001
        from splitsmith.overlay_raster import TemplateFrames, TemplateScriptError

        if template.name in self.refuses:
            raise TemplateScriptError(f"{template.name}: {self.refuses[template.name]}")
        times = list(plan(0.5))
        self.timelines.append((template.name, times))
        frames = [bytes([0]) * 4] * len(times)
        moved = self.moves.get(template.name)
        if moved == "before":
            frames[1] = bytes([9]) * 4
        elif moved == "after":
            frames[-1] = bytes([9]) * 4
        return TemplateFrames(duration=0.5, frame_count=len(times), width=1, height=1, frames=iter(frames))


def _levels(report: look_tools.CheckReport) -> list[tuple[str, str]]:
    return [(item.subject, item.level) for item in report.items]


def test_a_clean_look_checks_ok_and_probes_every_own_template_with_every_sample(user_dir: Path) -> None:
    look_tools.new_look("club", from_look="splitsmith", templates=True)
    prober = _Prober()
    report = look_tools.check_look("club", prober=prober)
    assert report.errors == 0 and report.warnings == 0
    files = {name for name, _ in prober.calls}
    assert files == {
        "card.html",
        "card-rise.html",
        "card-end-screen.html",
        "sting-wipe.html",
        "hud-minimal.html",
        "hud-pips.html",
        "hud-plate.html",
        "hud-ticker.html",
        "hud-timeline.html",
    }
    # Every card slot and variant, with each sample case; the sting with its own.
    samples = {
        json.dumps(data.get("card", data.get("transition")), sort_keys=True) for _, data in prober.calls
    }
    assert len(samples) >= 8
    texts = [data["card"]["text"] for _, data in prober.calls if "card" in data]
    assert any(len(t) > 40 for t in texts), "a long stage name is among the samples"
    shooters = [len(data["shooters"]) for _, data in prober.calls if "shooters" in data]
    assert 2 in shooters and 1 in shooters


def test_an_overlay_style_is_probed_on_three_stages_mid_stage_and_landed(user_dir: Path) -> None:
    """A HUD template gets stage data, not a card: twelve rounds with
    classes, thirty-two rounds, and a stage with no class data, each probed
    mid-stage and after the landing, plus one stillness timeline."""
    look_tools.new_look("club", from_look="splitsmith", templates=True)
    prober = _Prober()
    report = look_tools.check_look("club", prober=prober)
    assert report.errors == 0 and report.warnings == 0
    stages = [data["stage"] for name, data in prober.calls if name == "hud-plate.html"]
    # The twelve-round stage carries regions, so it is also probed mid-reload.
    assert sorted(stage["rounds"] for stage in stages) == [8, 8, 12, 12, 12, 32, 32]
    assert any(all(shot["cls"] is None for shot in stage["shots"]) for stage in stages)
    times = [at for name, at in prober.probed_at if name == "hud-plate.html"]
    assert all(at is not None for at in times) and len(set(times)) > 1
    (timeline,) = [t for name, t in prober.timelines if name == "hud-plate.html"]
    assert len(timeline) == 4, "two frames either side of the live span"
    assert any(item.subject.startswith("overlay plate") and item.level == "ok" for item in report.items)


def test_an_overlay_style_is_checked_on_a_stage_with_regions_and_the_toggles_on(user_dir: Path) -> None:
    """A check with the region toggles off, or on a stage without confirmed
    regions, would never run a template's reload chip or stage bar."""
    samples = look_tools.hud_samples()
    with_regions = [s for s in samples if s.events]
    assert with_regions, "a sample stage carries confirmed regions"
    kinds = {e.kind for s in with_regions for e in s.events}
    assert {"reload", "movement"} <= kinds and all(
        e.source == "manual" for s in with_regions for e in s.events
    )

    # The shipped Look owns its HUD templates; a fresh copy borrows them
    # (#1316) and `check_look` probes only what a Look owns.
    prober = _Prober()
    look_tools.check_look("splitsmith", prober=prober)
    calls = [data for name, data in prober.calls if name == "hud-ticker.html"]
    assert calls, "the ticker template was probed"
    assert all(data["options"]["reload_chip"] and data["options"]["stage_bar"] for data in calls)
    staged = [data["stage"] for data in calls if data["stage"]["reloads"]]
    assert staged and all(data["stage"]["events"] for data in calls if data["stage"]["rounds"] == 12)
    # One probe lands inside the reload, where the chip is up.
    (reload,) = staged[0]["reloads"]
    times = [at for name, at in prober.probed_at if name == "hud-ticker.html"]
    assert any(at is not None and reload["start"] < at < reload["end"] for at in times)


def test_the_sample_reload_is_on_the_move_so_the_split_band_path_runs() -> None:
    """Every shipped style cuts a reload on the move into its half of the
    stage bar; a sample whose reload never overlaps a movement would never
    run that path in a custom template. The mid-reload probe lands inside
    the movement too, and the reload outlasts it (a positive overhang)."""
    (sample,) = [s for s in look_tools.hud_samples() if s.events]
    reload = next(e for e in sample.events if e.kind == "reload")
    mid = (reload.start + reload.end) / 2
    movements = [e for e in sample.events if e.kind == "movement"]
    assert any(m.start < mid < m.end and m.end < reload.end for m in movements)


def test_an_overlay_style_that_moves_outside_its_live_span_is_a_warning(user_dir: Path) -> None:
    look_tools.new_look("club", from_look="splitsmith", templates=True)
    prober = _Prober(moves={"hud-pips.html": "before", "hud-ticker.html": "after"})
    report = look_tools.check_look("club", prober=prober)
    found = {
        (item.subject.split()[1], item.level): item.message
        for item in report.items
        if item.subject.startswith("overlay")
    }
    assert "before the beep" in found[("pips", "warn")]
    assert "settle()" in found[("ticker", "warn")]
    assert ("pips", "ok") not in found and ("plate", "ok") in found


def test_an_overlay_style_without_its_hooks_is_an_error(user_dir: Path) -> None:
    look_tools.new_look("club", from_look="splitsmith", templates=True)
    prober = _Prober(
        refuses={"hud-minimal.html": "an overlay template must define settle() returning seconds >= 0"}
    )
    report = look_tools.check_look("club", prober=prober)
    errors = [item for item in report.items if item.level == "error"]
    assert (
        len(errors) == 1
        and errors[0].subject.startswith("overlay minimal")
        and "settle()" in errors[0].message
    )


def test_a_borrowed_template_is_named_not_probed(user_dir: Path) -> None:
    d = user_dir / "plain"
    d.mkdir(parents=True)
    manifest = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    manifest["name"] = "plain"
    (d / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    prober = _Prober()
    report = look_tools.check_look("plain", prober=prober)
    assert prober.calls == []
    assert any(item.level == "ok" and "borrowed" in item.message for item in report.items)


def test_each_defect_is_reported_with_its_level(user_dir: Path) -> None:
    look_tools.new_look("club", from_look="splitsmith", templates=True)
    prober = _Prober(
        by_file={
            "card.html": TemplateProbe(errors=("forEach of undefined",)),
            "card-rise.html": TemplateProbe(
                duration=0.8,
                poster=1.4,
                has_seek=True,
                families=("Inter", "Splitsmith Display", "sans-serif"),
            ),
            "sting-wipe.html": TemplateProbe(
                duration=1.0,
                poster=0.5,
                has_seek=False,
                overflow=(("Stage 7 - The Very", 38),),
                blocked=("https://fonts.googleapis.com/css2?family=Inter",),
            ),
        }
    )
    report = look_tools.check_look("club", prober=prober)
    found = [(item.level, item.message) for item in report.items]
    assert any(level == "error" and "forEach of undefined" in msg for level, msg in found)
    assert any(level == "warn" and "Inter" in msg and "Splitsmith Display" in msg for level, msg in found)
    assert any(level == "warn" and "poster" in msg for level, msg in found)
    assert any(level == "error" and "seek()" in msg for level, msg in found)
    assert any(level == "warn" and "38 px" in msg for level, msg in found)
    assert any(
        level == "warn" and "fonts.googleapis.com" in msg and "cannot load" in msg for level, msg in found
    )
    assert report.errors >= 2


def test_a_broken_manifest_is_one_error_naming_the_problem(user_dir: Path) -> None:
    d = user_dir / "broken"
    d.mkdir(parents=True)
    (d / "look.json").write_text(
        json.dumps({"name": "broken", "colors": {"ink": [1, 2, 3]}}), encoding="utf-8"
    )
    report = look_tools.check_look("broken", prober=_Prober())
    assert _levels(report) == [("look.json", "error")]
    assert "missing colour tokens" in report.items[0].message


def test_a_broken_user_look_shadowing_a_shipped_one_is_checked_not_skipped(user_dir: Path) -> None:
    d = user_dir / "clean"
    d.mkdir(parents=True)
    (d / "look.json").write_text("{not json", encoding="utf-8")
    report = look_tools.check_look("clean", prober=_Prober())
    assert report.errors == 1 and report.source == "user"


def test_an_unknown_look_is_an_error(user_dir: Path) -> None:
    report = look_tools.check_look("nope", prober=_Prober())
    assert report.errors == 1 and "no Look named" in report.items[0].message


# --- preview -------------------------------------------------------------------------


class _Raster:
    def __init__(self) -> None:
        self.templates: list[str] = []

    def png(self, html: str, *, width: int, height: int) -> bytes:
        return self._blank(width, height)

    def render_template(self, template: Path, *, context, width: int, height: int) -> bytes:
        self.templates.append(template.name)
        return self._blank(width, height)

    def engine_version(self) -> str:
        return "fake"

    def render_template_frames(self, template, *, context, width, height, fps, max_seconds):
        from splitsmith.overlay_raster import TemplateFrames

        self.templates.append(template.name)
        return TemplateFrames(
            duration=0.0, frame_count=1, width=width, height=height, frames=iter([bytes(width * height * 4)])
        )

    @staticmethod
    def _blank(width: int, height: int) -> bytes:
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()


def test_preview_renders_every_card_variant_and_sting_and_a_contact_sheet(
    user_dir: Path, tmp_path: Path
) -> None:
    look_tools.new_look("club", from_look="splitsmith", templates=True)
    out = tmp_path / "out"
    written = look_tools.preview_look("club", rasterizer=_Raster(), out=out).written
    names = sorted(p.name for p in written)
    assert names == sorted(
        [
            "title_page-default.png",
            "title_page-rise.png",
            "slate-default.png",
            "slate-rise.png",
            "lower_third-default.png",
            "lower_third-rise.png",
            "closing-default.png",
            "closing-end-screen.png",
            "closing-rise.png",
            "transition-wipe.png",
            "contact-sheet.png",
        ]
    )
    with Image.open(out / "slate-rise.png") as im:
        assert im.size == (look_tools.PREVIEW_WIDTH, look_tools.PREVIEW_HEIGHT) and im.mode == "RGB"
    with Image.open(out / "contact-sheet.png") as sheet:
        assert sheet.width > look_tools.PREVIEW_WIDTH


def test_preview_refuses_a_broken_user_look_instead_of_drawing_the_shipped_one(user_dir: Path) -> None:
    root = user_dir / "clean"
    root.mkdir(parents=True)
    (root / "look.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(look_tools.LookToolError, match="look.json"):
        look_tools.preview_look("clean", rasterizer=_Raster(), out=user_dir / "out")


class _ThrowingRaster(_Raster):
    def render_template(self, template: Path, *, context, width: int, height: int) -> bytes:
        from splitsmith.overlay_raster import TemplateScriptError

        raise TemplateScriptError(f"{template.name}: boom")

    def render_template_frames(self, template, *, context, width, height, fps, max_seconds):
        from splitsmith.overlay_raster import TemplateScriptError

        raise TemplateScriptError(f"{template.name}: boom")


def test_preview_names_every_card_and_sting_a_throwing_template_left_out(user_dir: Path) -> None:
    look_tools.new_look("club", from_look="splitsmith", templates=True)
    result = look_tools.preview_look("club", rasterizer=_ThrowingRaster(), out=user_dir / "out")
    assert "sting / wipe" in result.skipped
    assert "title_page / default" in result.skipped
    assert not any(p.name == "transition-wipe.png" for p in result.written)


def test_new_from_a_look_copies_its_other_files_but_not_its_previews(user_dir: Path) -> None:
    """A Look whose template loads ``badge.png`` or ``style.css`` beside it
    lost them on a copy: only the manifest's templates came along."""
    source = look_tools.new_look("source", starter="still")
    (source / "img").mkdir()
    (source / "img" / "badge.png").write_bytes(b"png bytes")
    (source / "style.css").write_text("body { }", encoding="utf-8")
    (source / "preview").mkdir()
    (source / "preview" / "look.png").write_bytes(b"old preview")
    copy = look_tools.new_look("copy", from_look="source")
    assert (copy / "img" / "badge.png").read_bytes() == b"png bytes"
    assert (copy / "style.css").is_file() and (copy / "still.html").is_file()
    assert not (copy / "preview").exists()
    assert json.loads((copy / "look.json").read_text(encoding="utf-8"))["name"] == "copy"
