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


def test_new_from_a_shipped_look_copies_its_manifest_and_templates(user_dir: Path) -> None:
    root = look_tools.new_look("club-red", from_look="splitsmith")
    assert root == user_dir / "club-red"
    manifest = json.loads((root / "look.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "club-red" and manifest["label"] == "Club red"
    assert (
        (root / "card.html").is_file()
        and (root / "card-rise.html").is_file()
        and (root / "sting-wipe.html").is_file()
    )
    look = looks.load_look("club-red")
    assert look.source == "user"
    assert looks.template_for(look, "slate", "rise") == root / "card-rise.html"


@pytest.mark.parametrize(
    ("starter", "slots"),
    [
        ("still", {"title_page", "slate", "closing"}),
        ("animated", {"title_page", "slate", "closing"}),
        ("lower-third", {"lower_third"}),
        ("sting", {"transition"}),
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
    """Answers every probe with ``probe``, or per template file name."""

    def __init__(
        self, probe: TemplateProbe | None = None, by_file: dict[str, TemplateProbe] | None = None
    ) -> None:
        self.probe = probe or TemplateProbe()
        self.by_file = by_file or {}
        self.calls: list[tuple[str, dict]] = []

    def probe_template(self, template: Path, *, context, width: int, height: int) -> TemplateProbe:
        self.calls.append((template.name, context.data))
        return self.by_file.get(template.name, self.probe)


def _levels(report: look_tools.CheckReport) -> list[tuple[str, str]]:
    return [(item.subject, item.level) for item in report.items]


def test_a_clean_look_checks_ok_and_probes_every_own_template_with_every_sample(user_dir: Path) -> None:
    look_tools.new_look("club", from_look="splitsmith")
    prober = _Prober()
    report = look_tools.check_look("club", prober=prober)
    assert report.errors == 0 and report.warnings == 0
    files = {name for name, _ in prober.calls}
    assert files == {"card.html", "card-rise.html", "sting-wipe.html"}
    # Every card slot and variant, with each sample case; the sting with its own.
    samples = {
        json.dumps(data.get("card", data.get("transition")), sort_keys=True) for _, data in prober.calls
    }
    assert len(samples) >= 8
    texts = [data["card"]["text"] for _, data in prober.calls if "card" in data]
    assert any(len(t) > 40 for t in texts), "a long stage name is among the samples"
    shooters = [len(data["shooters"]) for _, data in prober.calls]
    assert 2 in shooters and 1 in shooters


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
    look_tools.new_look("club", from_look="splitsmith")
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
                duration=1.0, poster=0.5, has_seek=False, overflow=(("Stage 7 - The Very", 38),)
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
    look_tools.new_look("club", from_look="splitsmith")
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
    look_tools.new_look("club", from_look="splitsmith")
    result = look_tools.preview_look("club", rasterizer=_ThrowingRaster(), out=user_dir / "out")
    assert "sting / wipe" in result.skipped
    assert "title_page / default" in result.skipped
    assert not any(p.name == "transition-wipe.png" for p in result.written)
