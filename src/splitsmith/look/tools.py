"""Making, checking and previewing a Look (issue #1262, spec 2026-10-07).

``splitsmith looks new | check | preview`` are thin wrappers over these.
They exist so an author never has to run an export to see a template, and
never finds out from a log line that a Look was skipped:

- :func:`new_look` copies a Look, or makes one from a starter template, in
  the user Looks folder.
- :func:`check_look` loads every template the Look owns in Chromium
  against sample cases (one shooter with a logo, two without, a long stage
  name; for a sting, the transition) and words what the prober saw: script
  errors, animation hooks that disagree, fonts that will not load, text
  that runs off the card. A broken ``look.json`` is a finding naming the
  field, not a silently skipped Look.
- :func:`preview_look` renders every card variant and sting the Look
  resolves to PNGs and a contact sheet, on a demo backdrop or a real stage.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal, Protocol

from PIL import Image, ImageDraw

from .. import composition
from ..config import StageEvent, StageRounds
from ..events import reload_figures
from ..hud_extras import HudExtras, declared_data, hud_extras_data
from ..identity import ResolvedIdentity
from ..looks import (
    DEFAULT_LOOK,
    LOOK_NAME_RE,
    MANIFEST_FILE,
    OVERLAY_SLOT,
    STING_SLOT,
    SUMMARY_SLOTS,
    Look,
    LookError,
    SummarySlot,
    load_look,
    look_files,
    read_look,
    shipped_looks_dir,
    sting_template_for,
    template_for,
    user_looks_dir,
    variants_for,
)
from ..match_project import StageScorecard
from ..match_summary import MatchSummary, MatchSummaryRow
from ..overlay.card import build_card_still, build_lower_third, card_context
from ..overlay.hud import HudOptions, declared_positions, hud_options_data, hud_stage_data, resolve_position
from ..overlay.raster import Rasterizer, TemplateFrames, TemplateProbe, TemplateScriptError
from ..overlay.theme import theme_for
from ..stage_summary_data import TileShot, TileStageData
from ..summary_template import match_summary_payload, stage_summary_payload, summary_context
from .brand import brand_json
from .frames import misplaced_frames_tag, wants_frames, with_frames
from .sandbox import ORIGIN
from .sting import sting_context
from .template import TemplateContext

#: The starter templates ``looks new --starter`` copies (``data/looks/_starters/``).
STARTERS: dict[str, str] = {
    "still": "still.html",
    "animated": "animated.html",
    "lower-third": "lower-third.html",
    "sting": "sting.html",
    "sting-frames": "sting-frames.html",
    "hud": "hud.html",
    "summary": "summary.html",
    "broadcast": "broadcast-hud.html",
}
#: The starters the template editor offers as replacement text: the card and
#: sting templates it edits. An overlay style or a summary is not edited there.
EDITOR_STARTERS: tuple[str, ...] = ("animated", "lower-third", "still", "sting", "sting-frames")
_STARTER_SLOTS: dict[str, dict[str, object]] = {
    "still": {"title_page": "still.html", "slate": "still.html", "closing": "still.html"},
    "animated": {"title_page": "animated.html", "slate": "animated.html", "closing": "animated.html"},
    "lower-third": {"lower_third": "lower-third.html"},
    "sting": {STING_SLOT: {"sting": "sting.html"}},
    "sting-frames": {STING_SLOT: {"sting": "sting-frames.html"}},
    "hud": {OVERLAY_SLOT: {"hud": "hud.html"}},
    "summary": {"summary": {"summary": "summary.html"}},
    # The reference Look: every slot a template can draw, each using all of
    # the data its slot gets (docs/looks/authoring.md).
    "broadcast": {
        "title_page": "broadcast-card.html",
        "slate": "broadcast-card.html",
        "lower_third": "broadcast-card.html",
        "closing": {"default": "broadcast-card.html", "end-screen": "broadcast-card.html"},
        "thumbnail": "broadcast-thumbnail.html",
        OVERLAY_SLOT: {"broadcast": "broadcast-hud.html"},
        "summary": {"results": "broadcast-summary.html"},
        "match_summary": {"table": "broadcast-match.html"},
        STING_SLOT: {"push": "broadcast-sting.html"},
    },
}


#: The shipped Look whose sounds and grade a starter carries: its
#: templates' ``window.sounds()`` name those files.
_STARTER_ASSETS: dict[str, str] = {"broadcast": "broadcast"}


def _raw_manifest(root: Path) -> dict[str, object]:
    try:
        raw = json.loads((root / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _asset_files(root: Path) -> dict[str, Path]:
    """The Look folder ``root``'s sound files and the LUT its grade names, by
    path relative to ``root``: what a copy carries even when it follows the
    shipped templates, since these are files the manifest names, not code
    that goes stale. Links are never followed out of the folder."""
    files: dict[str, Path] = {}
    folder = root / "sounds"
    if folder.is_dir() and not folder.is_symlink():
        for path in sorted(folder.rglob("*")):
            if path.is_file() and not path.is_symlink():
                files[str(path.relative_to(root))] = path
    grade = _raw_manifest(root).get("grade")
    lut = grade.get("lut") if isinstance(grade, dict) else None
    if isinstance(lut, str):
        path = root / lut
        if path.is_file() and not path.is_symlink() and root.resolve() in path.resolve().parents:
            files[str(path.resolve().relative_to(root.resolve()))] = path
    return files


def _starter_files(starter: str) -> list[str]:
    """Every template file a starter's slots name, its own first."""
    files = [STARTERS[starter]]
    for spec in _STARTER_SLOTS[starter].values():
        for file in spec.values() if isinstance(spec, dict) else [spec]:
            if file not in files:
                files.append(file)
    return files


CARD_SLOTS: tuple[Literal["title_page", "slate", "lower_third", "closing"], ...] = (
    "title_page",
    "slate",
    "lower_third",
    "closing",
)
#: The faces the engine stylesheet declares; any other named family falls back.
BUNDLED_FAMILIES: frozenset[str] = frozenset({"Splitsmith Display", "Splitsmith Mono"})
_GENERIC_FAMILIES = frozenset(
    {
        "serif",
        "sans-serif",
        "monospace",
        "cursive",
        "fantasy",
        "system-ui",
        "ui-sans-serif",
        "ui-serif",
        "ui-monospace",
    }
)
CHECK_WIDTH, CHECK_HEIGHT, CHECK_FPS = 1280, 720, 30.0
#: The upright page a HUD style is also probed on (issue #1394): what a
#: 1080x1920 or 720x1280 video renders its HUD at (``hud_page_size``), so
#: its context carries the platform safe area.
CHECK_UPRIGHT_WIDTH, CHECK_UPRIGHT_HEIGHT = 608, 1080
PREVIEW_WIDTH, PREVIEW_HEIGHT = 960, 540
LONG_STAGE_NAME = "Stage 7 - The Very Long Corridor Of Doom And Despair"


class LookToolError(ValueError):
    """A ``looks new`` request that cannot be carried out, worded for the user."""


class Prober(Protocol):
    def probe_template(
        self, template: Path, *, context: TemplateContext, width: int, height: int, at: float | None = None
    ) -> TemplateProbe: ...

    def render_template_timeline(
        self,
        template: Path,
        *,
        context: TemplateContext,
        width: int,
        height: int,
        plan: Callable[[float], Sequence[float]],
    ) -> TemplateFrames: ...

    def render_template_frames(
        self,
        template: Path,
        *,
        context: TemplateContext,
        width: int,
        height: int,
        fps: float,
        max_seconds: float,
        frames_dir: Path | None = None,
    ) -> TemplateFrames: ...


@dataclass(frozen=True)
class CheckItem:
    subject: str
    level: Literal["ok", "warn", "error"]
    message: str


@dataclass(frozen=True)
class CheckReport:
    look: str
    root: Path | None
    source: str
    items: tuple[CheckItem, ...]

    @property
    def errors(self) -> int:
        return sum(item.level == "error" for item in self.items)

    @property
    def warnings(self) -> int:
        return sum(item.level == "warn" for item in self.items)


# --- new -----------------------------------------------------------------------------


def _label(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").capitalize()


def strict_look(name: str) -> Look:
    """The Look ``name`` as the authoring commands see it: the user's folder
    when there is one, read strictly (a broken one is a
    :class:`LookToolError` naming its manifest, never the shipped Look of
    the same name drawn in its place), else the shipped one."""
    root = user_looks_dir() / name
    try:
        if root.is_dir():
            return read_look(root, "user")
        return load_look(name)
    except LookError as exc:
        raise LookToolError(f"{root / MANIFEST_FILE}: {exc}" if root.is_dir() else str(exc)) from None


def new_look(
    name: str, *, from_look: str | None = None, starter: str | None = None, templates: bool = False
) -> Path:
    """Make ``~/.splitsmith/looks/<name>``: a copy of ``from_look``, or a
    Look whose slots hold the ``starter`` template over the default Look's
    palette. Neither given copies the default Look. Returns the new folder.

    A copy of a shipped Look takes its manifest and no template: its slots
    are empty, so every card draws the current shipped template and a
    later release reaches it (a copied ``card.html`` froze the cards before
    the brand, the event logo and the credit). ``templates`` copies them
    anyway, to edit by hand. A copy of your own Look carries every file:
    its templates may be yours."""
    if not LOOK_NAME_RE.match(name):
        raise LookToolError(
            f"{name!r} is not a Look name: lower-case letters, digits, '-' and '_', starting with a letter"
        )
    if (shipped_looks_dir() / name / MANIFEST_FILE).exists():
        raise LookToolError(f"{name!r} is a shipped Look; pick another name and copy it with --from {name}")
    root = user_looks_dir() / name
    if root.exists():
        raise LookToolError(f"a Look named {name!r} already exists at {root}")
    if starter is not None and starter not in STARTERS:
        raise LookToolError(f"starter {starter!r} is not one of {', '.join(sorted(STARTERS))}")
    if starter is not None and from_look is not None:
        raise LookToolError("give --from or --starter, not both")

    if starter is not None:
        base = load_look(DEFAULT_LOOK)
        manifest = base.manifest.model_dump(exclude={"source"})
        manifest.update(name=name, label=_label(name), slots=_STARTER_SLOTS[starter])
        files = {file: shipped_looks_dir() / "_starters" / file for file in _starter_files(starter)}
        if starter in _STARTER_ASSETS:
            # The shipped folder itself: a user Look of the same name shadows
            # it for load_look, and the starter's templates name these files.
            assets = shipped_looks_dir() / _STARTER_ASSETS[starter]
            files.update(_asset_files(assets))
            for key in ("sounds", "grade"):
                if (block := _raw_manifest(assets).get(key)) is not None:
                    manifest[key] = block
    else:
        source = strict_look(from_look or DEFAULT_LOOK)
        manifest = source.manifest.model_dump(exclude={"source"})
        manifest.update(name=name, label=_label(name))
        # ``LookManifest`` does not carry ``sounds`` (``audio_dress`` reads it
        # on its own), so model_dump above dropped it.
        if (sound_map := _raw_manifest(source.root).get("sounds")) is not None:
            manifest["sounds"] = sound_map
        if source.source == "shipped" and not templates:
            manifest.update(slots={}, base=source.name)
            files = _asset_files(source.root)
        else:
            # The whole folder, not only the templates the manifest names: a
            # template may load an image or a stylesheet beside it. Its
            # previews are pictures of the source, and the manifest is
            # written fresh below.
            files = look_files(source.root)
    root.mkdir(parents=True)
    try:
        for file, src in files.items():
            (root / file).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, root / file)
        (root / MANIFEST_FILE).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        load_look(name)  # the folder must load before it is handed back
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return root


# --- copies of shipped templates ---------------------------------------------------


@cache
def shipped_template_history() -> dict[str, str]:
    """Every version a shipped template has had, by the sha256 of its bytes,
    to its file name (``data/looks/_history.json``, written from git by
    ``scripts/record_template_history.py``)."""
    raw = (shipped_looks_dir() / "_history.json").read_text(encoding="utf-8")
    return dict(json.loads(raw))


def _named_files(look: Look) -> set[str]:
    return {file for variants in look.manifest.slots.values() for file in variants.values()}


def _shipped_copies(look: Look) -> dict[str, bool]:
    """The template files ``look`` names that are byte-identical to some
    version of the shipped default Look's file of the same name, each with
    whether that version is still the current one. Anything else is the
    Look's own.

    A file another shipped Look draws (the ``broadcast`` starter's, which
    ships as the Broadcast Look) is the Look's own too: dropping it would
    fall back to the default Look's card, a different template, not to the
    same one."""
    history = shipped_template_history()
    current_dir = shipped_looks_dir() / DEFAULT_LOOK
    others = {
        p.name
        for p in shipped_looks_dir().glob("*/*.html")
        if p.parent.name != DEFAULT_LOOK and not p.parent.name.startswith("_")
    }
    out: dict[str, bool] = {}
    for file in sorted(_named_files(look)):
        path = look.root / file
        if path.is_symlink() or not path.is_file():
            continue
        name = Path(file).name
        current = current_dir / name
        if not current.is_file() and name in others:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if history.get(digest) != name:
            continue
        out[file] = current.is_file() and hashlib.sha256(current.read_bytes()).hexdigest() == digest
    return out


def outdated_copies(look: Look) -> tuple[str, ...]:
    """The template files ``look`` holds that are unedited copies of an
    older shipped version: the cards they draw miss what shipped since.
    Only a user Look has any."""
    if look.source != "user":
        return ()
    return tuple(file for file, current in _shipped_copies(look).items() if not current)


def refresh_templates(name: str) -> tuple[str, ...]:
    """Stop ``name`` from holding unedited copies of shipped templates, old
    or current: drop every slot entry naming one and delete the file, so
    those cards draw the shipped template from now on. A file the Look
    edited is never touched. Returns the files removed."""
    look = strict_look(name)
    if look.source != "user":
        raise LookToolError(f"{name!r} is a shipped Look; there is nothing to refresh")
    copies = set(_shipped_copies(look))
    if not copies:
        return ()
    manifest_path = look.root / MANIFEST_FILE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    slots = {
        slot: {variant: file for variant, file in variants.items() if file not in copies}
        for slot, variants in manifest.get("slots", {}).items()
    }
    manifest["slots"] = {slot: variants for slot, variants in slots.items() if variants}
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for file in sorted(copies):
        (look.root / file).unlink(missing_ok=True)
    return tuple(sorted(copies))


# --- check ---------------------------------------------------------------------------


def _sample_logo(work: Path) -> Path:
    path = work / "logo.png"
    Image.new("RGB", (256, 128), (230, 230, 230)).save(path)
    return path


def _card(slot: str, variant: str, text: str) -> composition.TitleCard | composition.MatchTitle:
    if slot in ("title_page", "closing"):
        return composition.MatchTitle(text=text, info=("2026-06-27", "Production Optics"), variant=variant)
    style: Literal["slate", "lower-third"] = "lower-third" if slot == "lower_third" else "slate"
    return composition.TitleCard(
        text=text, duration_seconds=1.5, style=style, info=("24 rounds",), variant=variant
    )


def _cases(slot: str, logo: Path) -> list[tuple[str, str, tuple[ResolvedIdentity, ...]]]:
    """``(case, text, shooters)`` for a card slot or the sting."""
    normal = "Bromma Classifier" if slot in ("title_page", "closing") else "Stage 3 . Standards"
    if slot == STING_SLOT:
        normal = "Stage 4"
    one = ResolvedIdentity(label="Mathias Axell", accent="#ff2d2d", logo_path=logo, club="Bromma PK")
    two = (
        ResolvedIdentity(label="Anders", accent="#fbbf24", logo_path=None, club=None),
        ResolvedIdentity(label="Bea", accent=None, logo_path=None, club=None),
    )
    plain = ResolvedIdentity(label="Mathias Axell", accent=None, logo_path=None, club=None)
    return [
        ("one shooter with a logo", normal, (one,)),
        ("two shooters, no logo", normal, two),
        ("a long stage name", LONG_STAGE_NAME, (plain,)),
    ]


def _context(
    look: Look, slot: str, variant: str, text: str, shooters: Sequence[ResolvedIdentity]
) -> TemplateContext:
    theme = theme_for(look)
    if slot == STING_SLOT:
        return sting_context(
            kind=f"sting:{variant}",
            seconds=1.0,
            from_label="Stage 3",
            to_label=text,
            width=CHECK_WIDTH,
            height=CHECK_HEIGHT,
            fps=CHECK_FPS,
            theme=theme,
            shooters=shooters,
        )
    return card_context(
        _card(slot, variant, text),
        slot=slot,  # type: ignore[arg-type]
        width=CHECK_WIDTH,
        height=CHECK_HEIGHT,
        fps=CHECK_FPS,
        theme=theme,
        shooters=shooters,
        brand=brand_json(look, slot),
    )


#: A frames sting draws the whole picture; one that leaves more than this
#: share of a frame's pixels below full alpha shows black there in the
#: export. A little allows antialiased edges against the frame's border.
TRANSPARENT_SHARE = 0.005


def sample_edges(root: Path, *, width: int, height: int, count: int) -> tuple[list[Path], list[Path]]:
    """Two sample edges for a frames sting: ``from`` a light bar moving
    across mid grey (a moving clip), ``to`` a still diagonal gradient."""
    froms, tos = root / "k" / "from", root / "k" / "to"
    froms.mkdir(parents=True, exist_ok=True)
    tos.mkdir(parents=True, exist_ok=True)
    gradient = Image.linear_gradient("L").resize((width, height)).convert("RGB")
    out_from: list[Path] = []
    out_to: list[Path] = []
    for n in range(count):
        frame = Image.new("RGB", (width, height), (110, 110, 110))
        x = int((width - width // 8) * n / max(1, count - 1))
        frame.paste((235, 235, 235), (x, 0, x + width // 8, height))
        out_from.append(froms / f"{n:04d}.jpg")
        frame.save(out_from[-1], quality=90)
        out_to.append(tos / f"{n:04d}.jpg")
        gradient.save(out_to[-1], quality=90)
    return out_from, out_to


def _check_frames_sting(
    subject: str, template: Path, context: TemplateContext, prober: Prober, work: Path
) -> list[CheckItem]:
    """Draw a frames sting over :func:`sample_edges` and warn when it leaves
    the picture transparent."""
    seconds = context.data["transition"]["duration_seconds"]
    count = max(1, math.ceil(seconds * CHECK_FPS - 1e-9))
    try:
        froms, tos = sample_edges(work / "frames", width=CHECK_WIDTH, height=CHECK_HEIGHT, count=count)
        ctx = with_frames(
            context,
            fps=CHECK_FPS,
            count=count,
            from_urls=[f"{ORIGIN}/frames/k/from/{p.name}" for p in froms],
            to_urls=[f"{ORIGIN}/frames/k/to/{p.name}" for p in tos],
        )
        frames = prober.render_template_frames(
            template,
            context=ctx,
            width=CHECK_WIDTH,
            height=CHECK_HEIGHT,
            fps=CHECK_FPS,
            max_seconds=seconds,
            frames_dir=work / "frames",
        )
        try:
            worst = 0.0
            for raw in frames.frames:
                opaque = bytes(raw[3::4]).count(255)
                worst = max(worst, 1 - opaque / (len(raw) // 4))
        finally:
            frames.close()
    except Exception as exc:  # noqa: BLE001 -- a finding, not a crash
        return [CheckItem(subject, "error", f"failed drawing with sample edges: {exc}")]
    if worst > TRANSPARENT_SHARE:
        return [
            CheckItem(
                subject,
                "warn",
                f"leaves {worst * 100:.0f} % of a frame transparent; a frames sting draws the whole "
                "picture, and what it leaves shows black in the export",
            )
        ]
    return []


def _judge(
    subject: str, results: list[tuple[str, TemplateProbe]], sounds_dir: Path | None = None
) -> list[CheckItem]:
    """The findings for one template over its sample cases; ``sounds_dir``
    is the Look's ``sounds/`` folder its ``window.sounds()`` cues name."""
    items: list[CheckItem] = []
    for case, probe in results:
        if probe.errors:
            items.append(CheckItem(subject, "error", f"script error: {probe.errors[0]} (sample: {case})"))
            break
    first = results[0][1]
    if first.duration > 0 and not first.has_seek:
        items.append(
            CheckItem(
                subject,
                "error",
                f"animates for {first.duration:g} s but has no seek(): "
                "every frame of the video would be the same",
            )
        )
    if first.duration > 0 and not 0 <= first.poster <= first.duration + 1e-6:
        items.append(
            CheckItem(
                subject,
                "warn",
                f"poster() at {first.poster:g} s is outside duration() {first.duration:g} s: "
                "previews show a frame the video never reaches",
            )
        )
    named = {f for _, probe in results for f in probe.families}
    foreign = sorted(f for f in named if f not in BUNDLED_FAMILIES and f.lower() not in _GENERIC_FAMILIES)
    if foreign:
        items.append(
            CheckItem(
                subject,
                "warn",
                f"names {', '.join(foreign)}: only Splitsmith Display and Splitsmith Mono load, so that text "
                "falls back to a system font",
            )
        )
    refused = sorted({url for _, probe in results for url in probe.blocked})
    if refused:
        shown = ", ".join(refused[:3]) + (f" and {len(refused) - 3} more" if len(refused) > 3 else "")
        items.append(
            CheckItem(
                subject,
                "warn",
                f"asks for {shown}, which a Look cannot load: a template reaches only the files in its "
                "own folder, the shared engine scripts and the bundled fonts, never the network",
            )
        )
    named_sounds = {
        str(cue.get("play") or cue.get("bed") or "") for _, probe in results for cue in probe.sounds
    }
    missing = sorted(name for name in named_sounds if sounds_dir is None or not (sounds_dir / name).is_file())
    if missing:
        items.append(
            CheckItem(
                subject,
                "warn",
                f"sounds() names {', '.join(repr(m) for m in missing)}, not in the Look's sounds folder: "
                "the dress pass leaves those cues out",
            )
        )
    for case, probe in results:
        if probe.overflow:
            text, by = probe.overflow[0]
            items.append(
                CheckItem(subject, "warn", f'"{text}" runs past the card by {by} px (sample: {case})')
            )
            break
    if not items:
        cases = f"{len(results)} sample cases"
        message = (
            f"{first.duration:g} s, poster at {first.poster:g} s, {cases}" if first.duration > 0 else cases
        )
        if first.sounds:
            message += f", {len(first.sounds)} sound{'' if len(first.sounds) == 1 else 's'}"
        items.append(CheckItem(subject, "ok", message))
    return items


#: Where the beep sits in a HUD sample's clip time.
HUD_SAMPLE_BEEP = 1.0
#: How long after the last shot a HUD is probed landed: past any landing.
HUD_LANDED_AFTER = 2.0
#: How far past ``settle()`` the stillness check looks for motion.
HUD_STILL_AFTER = 0.7
#: How long after a shot the upright probe looks: a split's flash is up.
HUD_AFTER_SHOT = 0.1
#: What a template that declares ``splitsmith-data`` keys is checked with:
#: a scored Production Optics stage, so the ammo counter and every spread
#: have something to draw. The same for every sample stage; its counts need
#: not match a sample's shots (real scorecards rarely do either).
HUD_SAMPLE_EXTRAS = HudExtras(
    stage_name="Sample stage with a long name",
    stage_number=3,
    scorecard=StageScorecard(
        hit_factor=5.1234,
        stage_points=98,
        stage_pct=87.5,
        alphas=18,
        charlies=4,
        deltas=1,
        misses=1,
        no_shoots=1,
        procedurals=0,
    ),
    division="Production Optics",
    min_rounds=24,
    capacity=15,
)


def _hud_stage(gaps: Sequence[tuple[str | None, float]]) -> list[TileShot]:
    shots: list[TileShot] = []
    elapsed = 0.0
    for cls, split in gaps:
        elapsed = round(elapsed + split, 6)
        shots.append(TileShot(time_from_beep=elapsed, split=split, interval_class=cls))  # type: ignore[arg-type]
    return shots


@dataclass(frozen=True)
class HudSample:
    """One stage ``looks check`` runs a HUD template on: its shots and its
    confirmed regions (seconds from the beep, like the shots)."""

    name: str
    shots: list[TileShot]
    events: tuple[StageEvent, ...] = ()


def _region(n: int, kind: str, start: float, end: float) -> StageEvent:
    return StageEvent.model_validate(
        {"id": f"evt-{n}", "kind": kind, "start": start, "end": end, "source": "manual"}
    )


def hud_samples() -> list[HudSample]:
    """The stages ``looks check`` runs a HUD template on: twelve rounds with
    every class a stage has and a confirmed reload on the move, a second
    movement and an activation in the gaps those classes name, thirty-two (a long row of anything per
    round), and eight with no class data (an audit from before classes)."""
    twelve = [("first_shot", 1.12), ("split", 0.24), ("split", 0.26), ("transition", 0.71), ("split", 0.22)]
    twelve += [("split", 0.25), ("reload", 1.64), ("split", 0.27), ("movement", 1.9), ("split", 0.23)]
    twelve += [("transition", 0.66), ("split", 0.25)]
    long = [("first_shot", 1.0)] + [("split", 0.26)] * 31
    bare: list[tuple[str | None, float]] = [(None, 1.05)]
    bare += [(None, 0.3)] * 7
    # Shot 6 lands at 2.80 s, 7 at 4.44, 8 at 4.71, 9 at 6.61, 10 at 6.84, 11 at 7.50.
    # The reload is on the move and ends after the movement (0.15 of it
    # exposed), so the check runs a template's split-band path: the stage bar
    # cuts a reload on the move into its half of the bar.
    regions = (
        _region(1, "reload", 2.95, 4.25),
        _region(2, "movement", 3.0, 4.1),
        _region(3, "movement", 4.85, 6.45),
        _region(4, "activation", 7.0, 7.3),
    )
    return [
        HudSample("12 rounds", _hud_stage(twelve), regions),
        HudSample("32 rounds", _hud_stage(long)),
        HudSample("no class data", _hud_stage(bare)),
    ]


#: The name the summary samples print: long, as a real one can be.
SUMMARY_SAMPLE_LABEL = "Sample shooter with a long name"


def summary_samples(
    look: Look,
    slot: SummarySlot,
    variant: str,
    template: Path,
    *,
    sizes: Sequence[tuple[str, int, int]] | None = None,
) -> list[tuple[str, TemplateContext, int, int]]:
    """The cases ``looks check`` and ``looks preview`` draw a summary
    template on (spec 2026-10-16-summary-templates-design): every HUD sample
    stage as a stage summary, scored like :data:`HUD_SAMPLE_EXTRAS`, and the
    twelve-round one again upright; or a six-stage match summary with a
    stage name past the card's width and a manual time, landscape and
    upright. ``sizes`` is ``(case suffix, width, height)`` per canvas, the
    check's landscape and upright pages by default. ``(case, context,
    width, height)``."""
    theme = theme_for(look)
    if sizes is None:
        sizes = [("", CHECK_WIDTH, CHECK_HEIGHT), (", upright", CHECK_UPRIGHT_WIDTH, CHECK_UPRIGHT_HEIGHT)]
    out: list[tuple[str, TemplateContext, int, int]] = []
    if slot == "summary":
        for index, sample in enumerate(hud_samples()):
            tile = TileStageData(
                label=SUMMARY_SAMPLE_LABEL,
                stage_number=3,
                shots=tuple(sample.shots),
                stage_time_seconds=sample.shots[-1].time_from_beep if sample.shots else None,
                scorecard=HUD_SAMPLE_EXTRAS.scorecard,
                stage_rounds=StageRounds(expected=HUD_SAMPLE_EXTRAS.min_rounds),
                reloads=tuple(reload_figures(sample.events)),
                events=sample.events,
            )
            payload = stage_summary_payload(tile, extras=HUD_SAMPLE_EXTRAS, keys=declared_data(template))
            for suffix, width, height in sizes if index == 0 else sizes[:1]:
                context = summary_context(
                    slot=slot,
                    variant=variant,
                    text=SUMMARY_SAMPLE_LABEL,
                    payload=payload,
                    duration_seconds=3.0,
                    width=width,
                    height=height,
                    fps=CHECK_FPS,
                    theme=theme,
                )
                out.append((f"{sample.name}{suffix}", context, width, height))
        return out
    rows = tuple(
        MatchSummaryRow(
            number=n,
            name="Stage with a name long enough to run past the card" if n == 2 else f"Stage {n}",
            time_seconds=10.0 + n * 3.1,
            time_is_manual=n == 4,
            hit_factor=4.0 + n * 0.3,
            stage_pct=50.0 + n * 6.0,
            draw=1.1 + n * 0.05,
            avg_split=0.3 + n * 0.01,
        )
        for n in range(1, 7)
    )
    summary = MatchSummary(
        title="Sample match with a long name",
        label=SUMMARY_SAMPLE_LABEL,
        rows=rows,
        avg_split=0.33,
        best_draw=1.15,
        rounds=140,
        hits={"A": 110, "C": 22, "D": 5, "M": 3, "NS": 1, "P": 0},
        split_stages=6,
        scored_stages=6,
    )
    payload = match_summary_payload(summary)
    for suffix, width, height in sizes:
        context = summary_context(
            slot=slot,
            variant=variant,
            text=summary.title,
            payload=payload,
            duration_seconds=summary.duration_seconds,
            width=width,
            height=height,
            fps=CHECK_FPS,
            theme=theme,
        )
        out.append((f"six stages{suffix}", context, width, height))
    return out


def _hud_context(
    look: Look, template: Path, sample: HudSample, *, width: int = CHECK_WIDTH, height: int = CHECK_HEIGHT
) -> TemplateContext:
    # Deferred: overlay_hud_render builds on the renderer's encoder module.
    from ..overlay.hud_render import hud_context

    stage = hud_stage_data(sample.shots, beep_in_clip=HUD_SAMPLE_BEEP, events=sample.events)
    extras = hud_extras_data(
        declared_data(template),
        sample.shots,
        beep_in_clip=HUD_SAMPLE_BEEP,
        events=sample.events,
        extras=HUD_SAMPLE_EXTRAS,
    )
    position = resolve_position(None, declared_positions(template))
    # Both region toggles on: a check with them off would never draw the
    # chip or the stage bar, and a stage without regions draws neither.
    return hud_context(
        stage=stage,
        options=hud_options_data(HudOptions(reload_chip=True, stage_bar=True), position),
        theme=theme_for(look),
        width=width,
        height=height,
        fps=CHECK_FPS,
        extras=extras,
    )


def _check_hud(subject: str, look: Look, template: Path, prober: Prober) -> list[CheckItem]:
    """A HUD template's findings: what the card checks look for, probed
    mid-stage and landed on every sample stage, then whether it is still
    where the renderer holds one frame (before the beep, after settle()).

    Every sample is probed again on an upright page (issue #1394), whose
    context carries the platform safe area, at the same moments and just
    after a shot (where a split flashes up): anything drawn into the area is
    a warning naming it."""
    results: list[tuple[str, TemplateProbe]] = []
    upright: list[tuple[str, TemplateProbe]] = []
    contexts: list[tuple[str, TemplateContext, list[TileShot]]] = []
    for sample in hud_samples():
        case, shots = sample.name, sample.shots
        context = _hud_context(look, template, sample)
        upright_context = _hud_context(
            look, template, sample, width=CHECK_UPRIGHT_WIDTH, height=CHECK_UPRIGHT_HEIGHT
        )
        contexts.append((case, context, shots))
        # Mid-stage at rest: just before the shot that ends the longest gap,
        # so a shot effect caught mid-way is never mistaken for a layout fault.
        _gap, index = max(
            (shots[i + 1].time_from_beep - shots[i].time_from_beep, i) for i in range(len(shots) - 1)
        )
        mid = HUD_SAMPLE_BEEP + shots[index + 1].time_from_beep - 0.02
        landed = HUD_SAMPLE_BEEP + shots[-1].time_from_beep + HUD_LANDED_AFTER
        moments = [("mid-stage", mid), ("landed", landed)]
        # Half-way through the first reload: the reload chip is up.
        reloads = [e for e in sample.events if e.kind == "reload"]
        if reloads:
            moments.append(("mid-reload", HUD_SAMPLE_BEEP + (reloads[0].start + reloads[0].end) / 2))
        for moment, at in moments:
            probe = prober.probe_template(
                template, context=context, width=CHECK_WIDTH, height=CHECK_HEIGHT, at=at
            )
            results.append((f"{case}, {moment}", probe))
        # Just after a shot a split is flashing up or a row is sliding in:
        # read for the safe area only, never as a layout fault (a row half
        # way through its slide is clipped on purpose).
        shot = HUD_SAMPLE_BEEP + shots[index + 1].time_from_beep + HUD_AFTER_SHOT
        for moment, at in [*moments, ("just after a shot", shot)]:
            probe = prober.probe_template(
                template,
                context=upright_context,
                width=CHECK_UPRIGHT_WIDTH,
                height=CHECK_UPRIGHT_HEIGHT,
                at=at,
            )
            upright.append((f"{case}, {moment}, upright", probe))
            if moment != "just after a shot":
                results.append((f"{case}, {moment}, upright", probe))
    items = _judge(subject, results, look.root / "sounds")
    if any(item.level == "error" for item in items):
        return items
    for case, probe in upright:
        if probe.unsafe:
            what, by = probe.unsafe[0]
            items = [item for item in items if item.level != "ok"]
            items.append(
                CheckItem(
                    subject,
                    "warn",
                    f"{what} reaches {by} px into an upright video's safe area, where Shorts, Reels and "
                    "TikTok draw their captions and buttons; keep clear of data.safe_area "
                    f"(sample: {case})",
                )
            )
            break

    _, context, shots = contexts[0]
    last = HUD_SAMPLE_BEEP + shots[-1].time_from_beep
    settled: list[float] = []

    def plan(settle: float) -> list[float]:
        settled.append(settle)
        end = last + settle
        return [0.0, HUD_SAMPLE_BEEP - 0.02, end, end + HUD_STILL_AFTER]

    try:
        rendered = prober.render_template_timeline(
            template, context=context, width=CHECK_WIDTH, height=CHECK_HEIGHT, plan=plan
        )
        try:
            before, at_beep, at_settle, later = list(rendered.frames)
        finally:
            rendered.close()
    except TemplateScriptError as exc:
        message = str(exc).removeprefix(f"{template.name}: ")
        return [item for item in items if item.level != "ok"] + [CheckItem(subject, "error", message)]

    still: list[CheckItem] = []
    if before != at_beep:
        still.append(
            CheckItem(
                subject,
                "warn",
                "moves before the beep: the video holds one frame there, so that motion never plays",
            )
        )
    if at_settle != later:
        still.append(
            CheckItem(
                subject,
                "warn",
                f"still moves after settle() ({settled[0]:g} s past the last shot): the video holds "
                "that frame, so the motion freezes part-way; return a longer settle()",
            )
        )
    if still:
        return [item for item in items if item.level != "ok"] + still
    return items


def check_look(name: str, *, prober: Prober) -> CheckReport:
    """Validate the Look ``name`` (the user's folder when there is one, even
    when it shadows a shipped Look, else the shipped one) and probe every
    template it owns against the sample cases."""
    user_root = user_looks_dir() / name
    shipped_root = shipped_looks_dir() / name
    if (user_root / MANIFEST_FILE).exists() or user_root.is_dir():
        root, source = user_root, "user"
    elif (shipped_root / MANIFEST_FILE).exists():
        root, source = shipped_root, "shipped"
    else:
        try:
            load_look(name)
        except LookError as exc:
            return CheckReport(name, None, "missing", (CheckItem("look", "error", str(exc)),))
        raise AssertionError("unreachable: a Look that loads has a folder")  # pragma: no cover
    return check_folder(name, root, source, prober=prober)  # type: ignore[arg-type]


def sample_contexts(look: Look, slot: str, variant: str, work: Path) -> list[tuple[str, TemplateContext]]:
    """What a ``slot`` / ``variant`` template of ``look`` receives in each
    sample case ``looks check`` runs (the template editor's data panel,
    #1265). The sample logo is written under ``work``."""
    logo = _sample_logo(work)
    return [
        (case, _context(look, slot, variant, text, shooters)) for case, text, shooters in _cases(slot, logo)
    ]


def check_folder(name: str, root: Path, source: Literal["shipped", "user"], *, prober: Prober) -> CheckReport:
    """:func:`check_look` on the Look folder ``root`` itself: the template
    editor checks an unsaved draft written to a temporary folder (#1265)."""
    try:
        look = read_look(root, source)
    except LookError as exc:
        return CheckReport(name, root, source, (CheckItem(MANIFEST_FILE, "error", str(exc)),))

    items = [
        CheckItem(
            MANIFEST_FILE,
            "ok",
            f"{len(look.manifest.colors)} colours, {len(look.accent_series)} in the accent series",
        )
    ]
    items.extend(
        CheckItem(
            file,
            "warn",
            "an unedited copy of an older shipped template: its cards miss what shipped since. "
            f"splitsmith looks refresh {name} draws the current one",
        )
        for file in outdated_copies(look)
    )
    borrowed = 0
    with tempfile.TemporaryDirectory(prefix="looks-check-") as tmp:
        logo = _sample_logo(Path(tmp))
        targets: list[tuple[str, str, Path]] = []
        for slot in CARD_SLOTS:
            for variant in variants_for(look, slot):
                own = look.own_template(slot, variant)
                if own is None:
                    borrowed += 1
                else:
                    targets.append((slot, variant, own))
        for variant in look.variants(STING_SLOT):
            own = look.own_template(STING_SLOT, variant)
            if own is not None:
                targets.append((STING_SLOT, variant, own))
        for variant in look.variants(OVERLAY_SLOT):
            own = look.own_template(OVERLAY_SLOT, variant)
            if own is not None:
                items.extend(_check_hud(f"{OVERLAY_SLOT} {variant} {own.name}", look, own, prober))
        for summary_slot in SUMMARY_SLOTS:
            for variant in look.variants(summary_slot):
                own = look.own_template(summary_slot, variant)
                if own is None:
                    continue
                cases = summary_samples(look, summary_slot, variant, own)
                results = [
                    (case, prober.probe_template(own, context=context, width=width, height=height))
                    for case, context, width, height in cases
                ]
                subject = f"{summary_slot} {variant} {own.name}"
                found = _judge(subject, results, look.root / "sounds")
                # An upright summary keeps its text out of what Shorts, Reels
                # and TikTok draw over (the probe measures it on every page
                # that carries ``data.safe_area``). Text only: a summary may
                # dim the whole frame behind it, which an overlay never does.
                texts = [
                    (case, hit) for case, probe in results for hit in probe.unsafe if hit[0].startswith('"')
                ]
                if texts and not any(item.level == "error" for item in found):
                    case, (what, by) = texts[0]
                    found = [item for item in found if item.level != "ok"]
                    found.append(
                        CheckItem(
                            subject,
                            "warn",
                            f"{what} reaches {by} px into an upright video's safe area, where Shorts, "
                            "Reels and TikTok draw their captions and buttons; keep clear of "
                            f"data.safe_area (sample: {case})",
                        )
                    )
                items.extend(found)
        for target_slot, variant, template in targets:
            subject = f"{target_slot} {variant} {template.name}"
            results = [
                (
                    case,
                    prober.probe_template(
                        template,
                        context=_context(look, target_slot, variant, text, shooters),
                        width=CHECK_WIDTH,
                        height=CHECK_HEIGHT,
                    ),
                )
                for case, text, shooters in _cases(target_slot, logo)
            ]
            items.extend(_judge(subject, results, look.root / "sounds"))
            if target_slot == STING_SLOT and wants_frames(template):
                items.extend(
                    _check_frames_sting(
                        subject,
                        template,
                        _context(look, STING_SLOT, variant, "Stage 4", ()),
                        prober,
                        Path(tmp) / f"frames-{variant}",
                    )
                )
        for slot_name in look.manifest.slots:
            for variant in look.variants(slot_name):
                own = look.own_template(slot_name, variant)
                if own is None or not misplaced_frames_tag(own):
                    continue
                if slot_name == STING_SLOT and wants_frames(own):
                    continue
                items.append(
                    CheckItem(
                        f"{slot_name} {variant} {own.name}",
                        "warn",
                        "splitsmith-frames does nothing outside the transition slot "
                        '(or with a content other than "edges")',
                    )
                )
    if borrowed:
        items.append(
            CheckItem(
                "templates",
                "ok",
                f"{borrowed} borrowed from the shipped {DEFAULT_LOOK} Look and checked there",
            )
        )
    return CheckReport(look.name, root, source, tuple(items))


# --- preview -------------------------------------------------------------------------


def demo_backdrop() -> Image.Image:
    """A range-like scene: a sky gradient over a ground band and three
    target silhouettes; enough for the card's blur to have something to
    blur, nothing anyone mistakes for footage."""
    w, h = PREVIEW_WIDTH, PREVIEW_HEIGHT
    im = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        draw.line([(0, y), (w, y)], fill=(int(38 + 30 * t), int(46 + 34 * t), int(58 + 30 * t)))
    horizon = int(h * 0.62)
    draw.rectangle([0, horizon, w, h], fill=(96, 84, 66))
    for x, tall in ((160, 140), (380, 190), (800, 110)):
        draw.rectangle([x - 44, horizon - tall, x + 44, horizon], fill=(180, 140, 92))
        draw.rectangle([x - 24, horizon - tall - 60, x + 24, horizon - tall], fill=(180, 140, 92))
    return im


def _over(base: Image.Image, overlay_png: bytes | Image.Image) -> Image.Image:
    out = base.convert("RGBA")
    layer = overlay_png if isinstance(overlay_png, Image.Image) else Image.open(io.BytesIO(overlay_png))
    out.alpha_composite(layer.convert("RGBA").resize(out.size))
    return out.convert("RGB")


def _contact_sheet(images: list[tuple[str, Path]], out: Path) -> Path:
    thumb_w, thumb_h, label_h, gap, cols = 320, 180, 22, 12, 3
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new(
        "RGB",
        (cols * thumb_w + (cols + 1) * gap, rows * (thumb_h + label_h) + (rows + 1) * gap),
        (14, 15, 18),
    )
    draw = ImageDraw.Draw(sheet)
    for i, (label, path) in enumerate(images):
        x = gap + (i % cols) * (thumb_w + gap)
        y = gap + (i // cols) * (thumb_h + label_h + gap)
        with Image.open(path) as im:
            sheet.paste(im.convert("RGB").resize((thumb_w, thumb_h)), (x, y))
        draw.text((x, y + thumb_h + 4), label, fill=(201, 204, 210))
    path = out / "contact-sheet.png"
    sheet.save(path)
    return path


@dataclass(frozen=True)
class PreviewResult:
    """What ``preview_look`` wrote (the contact sheet last) and the cards a
    template's own error left out, as ``"<slot> / <variant>"``."""

    written: tuple[Path, ...]
    skipped: tuple[str, ...] = ()


def preview_look(
    name: str,
    *,
    rasterizer: Rasterizer,
    out: Path,
    shooters: Sequence[ResolvedIdentity] = (),
    backdrop: Image.Image | None = None,
) -> PreviewResult:
    """Render every card variant and sting the Look ``name`` resolves (its
    own templates, the shipped default's for the rest) to
    ``<slot>-<variant>.png`` under ``out``, plus ``contact-sheet.png``.
    ``backdrop`` is the frame behind the cards (a stage's, from the CLI);
    ``None`` paints the demo scene. A broken user Look raises
    :class:`LookToolError` before anything is written."""
    look = strict_look(name)
    out.mkdir(parents=True, exist_ok=True)
    frame = (backdrop or demo_backdrop()).convert("RGB").resize((PREVIEW_WIDTH, PREVIEW_HEIGHT))
    written: list[tuple[str, Path]] = []
    skipped: list[str] = []
    with tempfile.TemporaryDirectory(prefix="looks-preview-") as tmp:
        frame_png = Path(tmp) / "frame.png"
        frame.save(frame_png)
        for slot in CARD_SLOTS:
            for variant in variants_for(look, slot):
                text = "Bromma Classifier" if slot in ("title_page", "closing") else "Stage 3 . Standards"
                card = _card(slot, variant, text)
                if slot == "lower_third":
                    layer = build_lower_third(
                        card,  # type: ignore[arg-type]
                        width=PREVIEW_WIDTH,
                        height=PREVIEW_HEIGHT,
                        fps=CHECK_FPS,
                        look=look,
                        rasterizer=rasterizer,
                        shooters=shooters,
                    )
                    image = None if layer is None else _over(frame, layer)
                else:
                    image = build_card_still(
                        card,
                        slot=slot,
                        width=PREVIEW_WIDTH,
                        height=PREVIEW_HEIGHT,
                        fps=CHECK_FPS,
                        look=look,
                        rasterizer=rasterizer,
                        backdrop=frame_png,
                        shooters=shooters,
                    )
                if image is None:
                    skipped.append(f"{slot} / {variant}")
                    continue
                path = out / f"{slot}-{variant}.png"
                image.convert("RGB").save(path)
                written.append((f"{slot} / {variant}", path))
        for variant in variants_for(look, STING_SLOT):
            template = sting_template_for(look, variant)
            if template is None:
                continue
            context = sting_context(
                kind=f"sting:{variant}",
                seconds=1.0,
                from_label="Stage 3",
                to_label="Stage 4",
                width=PREVIEW_WIDTH,
                height=PREVIEW_HEIGHT,
                fps=CHECK_FPS,
                theme=theme_for(look),
                shooters=shooters,
            )
            frames_dir: Path | None = None
            if wants_frames(template):
                # A frames sting draws the whole boundary: hand it the sample
                # edges ``looks check`` uses, or the push shows black.
                frames_dir = Path(tmp) / f"sting-frames-{variant}"
                count = max(1, math.ceil(1.0 * CHECK_FPS - 1e-9))
                froms, tos = sample_edges(frames_dir, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT, count=count)
                context = with_frames(
                    context,
                    fps=CHECK_FPS,
                    count=count,
                    from_urls=[f"{ORIGIN}/frames/k/from/{p.name}" for p in froms],
                    to_urls=[f"{ORIGIN}/frames/k/to/{p.name}" for p in tos],
                )
            try:
                if frames_dir is None:
                    png = rasterizer.render_template(
                        template, context=context, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT
                    )
                else:
                    png = rasterizer.render_template(
                        template,
                        context=context,
                        width=PREVIEW_WIDTH,
                        height=PREVIEW_HEIGHT,
                        frames_dir=frames_dir,
                    )
            except TemplateScriptError:
                skipped.append(f"sting / {variant}")
                continue
            path = out / f"{STING_SLOT}-{variant}.png"
            _over(frame, png).save(path)
            written.append((f"sting / {variant}", path))
        for summary_slot in SUMMARY_SLOTS:
            for variant in look.variants(summary_slot):
                template = look.own_template(summary_slot, variant)
                if template is None:
                    continue
                (_case, context, width, height), *_rest = summary_samples(
                    look, summary_slot, variant, template, sizes=[("", PREVIEW_WIDTH, PREVIEW_HEIGHT)]
                )
                try:
                    png = rasterizer.render_template(template, context=context, width=width, height=height)
                except TemplateScriptError:
                    skipped.append(f"{summary_slot} / {variant}")
                    continue
                path = out / f"{summary_slot}-{variant}.png"
                _over(frame, png).save(path)
                written.append((f"{summary_slot} / {variant}", path))
    paths = [path for _, path in written]
    paths.append(_contact_sheet(written, out))
    return PreviewResult(tuple(paths), tuple(skipped))


__all__ = [
    "BUNDLED_FAMILIES",
    "CheckItem",
    "CheckReport",
    "LookToolError",
    "PREVIEW_HEIGHT",
    "PREVIEW_WIDTH",
    "PreviewResult",
    "EDITOR_STARTERS",
    "STARTERS",
    "check_folder",
    "check_look",
    "new_look",
    "preview_look",
    "sample_contexts",
    "strict_look",
    "template_for",
]
