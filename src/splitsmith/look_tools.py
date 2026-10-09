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
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal, Protocol

from PIL import Image, ImageDraw

from . import composition
from .config import StageEvent
from .identity import ResolvedIdentity
from .look_brand import brand_json
from .look_sting import sting_context
from .look_template import TemplateContext
from .looks import (
    DEFAULT_LOOK,
    LOOK_NAME_RE,
    MANIFEST_FILE,
    OVERLAY_SLOT,
    STING_SLOT,
    Look,
    LookError,
    load_look,
    look_files,
    read_look,
    shipped_looks_dir,
    sting_template_for,
    template_for,
    user_looks_dir,
    variants_for,
)
from .overlay_card import build_card_still, build_lower_third, card_context
from .overlay_hud import HudOptions, declared_positions, hud_options_data, hud_stage_data, resolve_position
from .overlay_raster import Rasterizer, TemplateFrames, TemplateProbe, TemplateScriptError
from .overlay_theme import theme_for
from .stage_summary_data import TileShot

#: The starter templates ``looks new --starter`` copies (``data/looks/_starters/``).
STARTERS: dict[str, str] = {
    "still": "still.html",
    "animated": "animated.html",
    "lower-third": "lower-third.html",
    "sting": "sting.html",
    "hud": "hud.html",
}
#: The starters the template editor offers as replacement text: the card and
#: sting templates it edits. An overlay style is not edited there.
EDITOR_STARTERS: tuple[str, ...] = ("animated", "lower-third", "still", "sting")
_STARTER_SLOTS: dict[str, dict[str, object]] = {
    "still": {"title_page": "still.html", "slate": "still.html", "closing": "still.html"},
    "animated": {"title_page": "animated.html", "slate": "animated.html", "closing": "animated.html"},
    "lower-third": {"lower_third": "lower-third.html"},
    "sting": {STING_SLOT: {"sting": "sting.html"}},
    "hud": {OVERLAY_SLOT: {"hud": "hud.html"}},
}
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
        files = {STARTERS[starter]: shipped_looks_dir() / "_starters" / STARTERS[starter]}
    else:
        source = strict_look(from_look or DEFAULT_LOOK)
        manifest = source.manifest.model_dump(exclude={"source"})
        manifest.update(name=name, label=_label(name))
        if source.source == "shipped" and not templates:
            manifest.update(slots={}, base=source.name)
            files = {}
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
    version of the shipped file of the same name, each with whether that
    version is still the current one. Anything else is the Look's own."""
    history = shipped_template_history()
    current_dir = shipped_looks_dir() / DEFAULT_LOOK
    out: dict[str, bool] = {}
    for file in sorted(_named_files(look)):
        path = look.root / file
        if path.is_symlink() or not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if history.get(digest) != Path(file).name:
            continue
        current = current_dir / Path(file).name
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


def _judge(subject: str, results: list[tuple[str, TemplateProbe]]) -> list[CheckItem]:
    """The findings for one template over its sample cases."""
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
        items.append(CheckItem(subject, "ok", message))
    return items


#: Where the beep sits in a HUD sample's clip time.
HUD_SAMPLE_BEEP = 1.0
#: How long after the last shot a HUD is probed landed: past any landing.
HUD_LANDED_AFTER = 2.0
#: How far past ``settle()`` the stillness check looks for motion.
HUD_STILL_AFTER = 0.7


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
    # The reload is on the move and ends after the movement (overhang
    # +0.15), so the check runs a template's split-band path: the stage bar
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


def _hud_context(look: Look, template: Path, sample: HudSample) -> TemplateContext:
    # Deferred: overlay_hud_render builds on the renderer's encoder module.
    from .overlay_hud_render import hud_context

    stage = hud_stage_data(sample.shots, beep_in_clip=HUD_SAMPLE_BEEP, events=sample.events)
    position = resolve_position(None, declared_positions(template))
    # Both region toggles on: a check with them off would never draw the
    # chip or the stage bar, and a stage without regions draws neither.
    return hud_context(
        stage=stage,
        options=hud_options_data(HudOptions(reload_chip=True, stage_bar=True), position),
        theme=theme_for(look),
        width=CHECK_WIDTH,
        height=CHECK_HEIGHT,
        fps=CHECK_FPS,
    )


def _check_hud(subject: str, look: Look, template: Path, prober: Prober) -> list[CheckItem]:
    """A HUD template's findings: what the card checks look for, probed
    mid-stage and landed on every sample stage, then whether it is still
    where the renderer holds one frame (before the beep, after settle())."""
    results: list[tuple[str, TemplateProbe]] = []
    contexts: list[tuple[str, TemplateContext, list[TileShot]]] = []
    for sample in hud_samples():
        case, shots = sample.name, sample.shots
        context = _hud_context(look, template, sample)
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
    items = _judge(subject, results)
    if any(item.level == "error" for item in items):
        return items

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
            items.extend(_judge(subject, results))
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
            try:
                png = rasterizer.render_template(
                    template, context=context, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT
                )
            except TemplateScriptError:
                skipped.append(f"sting / {variant}")
                continue
            path = out / f"{STING_SLOT}-{variant}.png"
            _over(frame, png).save(path)
            written.append((f"sting / {variant}", path))
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
