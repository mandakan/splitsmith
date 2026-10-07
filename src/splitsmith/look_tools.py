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

import io
import json
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from PIL import Image, ImageDraw

from . import composition
from .identity import ResolvedIdentity
from .look_sting import sting_context
from .look_template import TemplateContext
from .looks import (
    DEFAULT_LOOK,
    LOOK_NAME_RE,
    MANIFEST_FILE,
    STING_SLOT,
    Look,
    LookError,
    load_look,
    read_look,
    shipped_looks_dir,
    sting_template_for,
    template_for,
    user_looks_dir,
    variants_for,
)
from .overlay_card import build_card_still, build_lower_third, card_context
from .overlay_raster import Rasterizer, TemplateProbe
from .overlay_theme import theme_for

#: The starter templates ``looks new --starter`` copies (``data/looks/_starters/``).
STARTERS: dict[str, str] = {
    "still": "still.html",
    "animated": "animated.html",
    "lower-third": "lower-third.html",
    "sting": "sting.html",
}
_STARTER_SLOTS: dict[str, dict[str, object]] = {
    "still": {"title_page": "still.html", "slate": "still.html", "closing": "still.html"},
    "animated": {"title_page": "animated.html", "slate": "animated.html", "closing": "animated.html"},
    "lower-third": {"lower_third": "lower-third.html"},
    "sting": {STING_SLOT: {"sting": "sting.html"}},
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
        self, template: Path, *, context: TemplateContext, width: int, height: int
    ) -> TemplateProbe: ...


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


def new_look(name: str, *, from_look: str | None = None, starter: str | None = None) -> Path:
    """Make ``~/.splitsmith/looks/<name>``: a copy of ``from_look`` (its
    manifest and every template it owns), or a Look whose slots hold the
    ``starter`` template over the default Look's palette. Neither given
    copies the default Look. Returns the new folder."""
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
        source = load_look(from_look or DEFAULT_LOOK)
        manifest = source.manifest.model_dump(exclude={"source"})
        manifest.update(name=name, label=_label(name))
        files = {
            file: source.root / file
            for variants in source.manifest.slots.values()
            for file in variants.values()
        }
    root.mkdir(parents=True)
    try:
        for file, src in files.items():
            shutil.copyfile(src, root / file)
        (root / MANIFEST_FILE).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        load_look(name)  # the folder must load before it is handed back
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return root


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
    try:
        look = read_look(root, source)  # type: ignore[arg-type]
    except LookError as exc:
        return CheckReport(name, root, source, (CheckItem(MANIFEST_FILE, "error", str(exc)),))

    items = [
        CheckItem(
            MANIFEST_FILE,
            "ok",
            f"{len(look.manifest.colors)} colours, {len(look.accent_series)} in the accent series",
        )
    ]
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


def _demo_backdrop() -> Image.Image:
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


def preview_look(
    name: str,
    *,
    rasterizer: Rasterizer,
    out: Path,
    shooters: Sequence[ResolvedIdentity] = (),
    backdrop: Image.Image | None = None,
) -> list[Path]:
    """Render every card variant and sting the Look ``name`` resolves (its
    own templates, the shipped default's for the rest) to
    ``<slot>-<variant>.png`` under ``out``, plus ``contact-sheet.png``.
    ``backdrop`` is the frame behind the cards (a stage's, from the CLI);
    ``None`` paints the demo scene."""
    look = load_look(name)
    out.mkdir(parents=True, exist_ok=True)
    frame = (backdrop or _demo_backdrop()).convert("RGB").resize((PREVIEW_WIDTH, PREVIEW_HEIGHT))
    written: list[tuple[str, Path]] = []
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
            png = rasterizer.render_template(
                template, context=context, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT
            )
            path = out / f"{STING_SLOT}-{variant}.png"
            _over(frame, png).save(path)
            written.append((f"sting / {variant}", path))
    paths = [path for _, path in written]
    paths.append(_contact_sheet(written, out))
    return paths


__all__ = [
    "BUNDLED_FAMILIES",
    "CheckItem",
    "CheckReport",
    "LookToolError",
    "PREVIEW_HEIGHT",
    "PREVIEW_WIDTH",
    "STARTERS",
    "check_look",
    "new_look",
    "preview_look",
    "template_for",
]
