# Looks slice 1: the Look directory, two migrated Looks, one theme source

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce `splitsmith.looks` and the Look directory, migrate the two overlay themes into it, make every generated card render through a Look template, with the default Look's output pixel-identical to today's.

**Architecture:** A Look is a directory with `look.json` (colour tokens) and HTML templates per card slot. The existing Python card declaration (`overlay_card.card_groups`) and the existing stylesheet (`overlay_html`) stay the single source of markup rules; the shipped `card.html` template renders them through two shared JavaScript files (`fit.js`, `cell.js`) that mirror the Python builders. `overlay_raster` grows `render_template`, which injects `window.splitsmith` into a fresh page before the template's scripts run. Nothing animates yet: `seek(0)` is the whole contract this slice uses.

**Tech Stack:** Python 3.11+, Pydantic, Playwright (Chromium headless shell, already shipped), PIL, pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-rendered-video-first-design.md`, section 1. Issue #1241, epic #1240.

## Global Constraints

- `uv` for everything, never `pip`. Black at line length 110, Ruff clean.
- No new dependency. The vendored `lottie-web` of the spec is slice 2's (#1242), not this one.
- `splitsmith.looks` and `splitsmith.look_template` are pure: no browser, no ffmpeg, no file writes. They read JSON and resolve paths.
- Nothing under `src/` imports `splitsmith.db` on the `create_app` path; the new modules import `user_config` and `overlay_*` only.
- For the `splitsmith` and `clean` Looks the rendered title page, slate, lower third and closing card are pixel-identical to main's. Verified by frames, not by argv (Task 6).
- Looks are named `^[a-z][a-z0-9_-]{0,31}$`; a slot's template is a file name (no path separator) inside the Look directory.
- The API request field `overlay_theme: ThemeName` keeps its `Literal["splitsmith", "clean"]` type in this slice. The CLI accepts any installed Look. Widening the API is #1246.
- A render never fails because of a card. A template that cannot be rasterized skips the card and logs, as today.

## Review Focus

1. A user Look directory with a malformed or missing `look.json` must not stop the shipped Looks from listing or rendering. Test in Task 1 (`test_list_looks_skips_a_broken_user_look_and_warns`).
2. A `look.json` naming a slot file that does not exist must fail at load, with the file named, never in the middle of a render. Test in Task 1 (`test_a_declared_slot_file_must_exist`).
3. Card text containing `<`, `&` and quotes must render escaped in the JavaScript port exactly as Python escapes it. The parity test in Task 4 uses such text.
4. A template whose script throws (a user's broken `card.html`) must skip that card and let the render continue. Test in Task 5 (`test_a_template_that_raises_skips_the_card`).
5. `--theme <user-look>` on the CLI is accepted and `--theme nope` is refused before any ffmpeg runs. Test in Task 2 (`test_cli_theme_accepts_an_installed_user_look`).

---

### Task 1: `splitsmith.looks` and the two shipped Looks

**Files:**
- Create: `src/splitsmith/looks.py`
- Create: `src/splitsmith/data/looks/splitsmith/look.json`
- Create: `src/splitsmith/data/looks/clean/look.json`
- Test: `tests/test_looks.py`

**Interfaces:**
- Consumes: `splitsmith.user_config.user_config_dir() -> Path`.
- Produces:
  - `SLOT_NAMES: tuple[str, ...] = ("title_page", "slate", "lower_third", "summary", "closing")`
  - `CardSlot = Literal["title_page", "slate", "lower_third", "closing"]`
  - `DEFAULT_LOOK = "splitsmith"`
  - `class LookError(RuntimeError)`, `class LookNotFoundError(LookError)`
  - `class LookManifest(BaseModel)`: `schema_version: int`, `name: str`, `label: str`, `colors: dict[str, tuple[int, int, int]]`, `fonts: dict[str, str]`, `slots: dict[str, str]`, `source: str | None`
  - `class Look(BaseModel)`: `manifest: LookManifest`, `root: Path`, `source: Literal["shipped", "user"]`; properties `name`, `label`; `own_template(slot: str) -> Path | None`
  - `shipped_looks_dir() -> Path`, `user_looks_dir() -> Path`, `shared_dir() -> Path`
  - `list_looks() -> list[Look]`, `look_names() -> tuple[str, ...]`, `load_look(name: str) -> Look`
  - `template_for(look: Look, slot: CardSlot) -> Path`

- [ ] **Step 1: Write the two manifests by hand**

`src/splitsmith/data/looks/splitsmith/look.json` carries the thirteen colours of today's `src/splitsmith/data/overlay_theme.json` verbatim (including `split_slow`), its `fonts` and `source`, plus `slots`. The build script in Task 2 rewrites it with sorted keys, so the exact formatting here does not matter yet:

```json
{
  "schema_version": 1,
  "name": "splitsmith",
  "label": "Splitsmith",
  "source": "src/splitsmith/ui_static/src/styles/index.css",
  "colors": {
    "accent": [255, 45, 45],
    "accent_fill": [220, 38, 38],
    "accent_text": [255, 180, 180],
    "ink": [244, 244, 245],
    "ink_2": [201, 204, 210],
    "muted": [142, 147, 155],
    "rule": [38, 43, 51],
    "split": [251, 191, 36],
    "split_good": [74, 222, 128],
    "split_slow": [255, 45, 45],
    "stroke": [10, 11, 13],
    "subtle": [107, 112, 121],
    "surface": [20, 23, 28]
  },
  "fonts": {"display": "Antonio", "mono": "JetBrains Mono", "sans": "Geist"},
  "slots": {
    "title_page": "card.html",
    "slate": "card.html",
    "lower_third": "card.html",
    "closing": "card.html"
  }
}
```

`src/splitsmith/data/looks/clean/look.json` carries `overlay_theme._CLEAN`'s values and declares no slots, so it exercises the fallback to the default Look's templates:

```json
{
  "schema_version": 1,
  "name": "clean",
  "label": "Clean",
  "colors": {
    "accent": [255, 45, 45],
    "accent_fill": [220, 38, 38],
    "accent_text": [255, 180, 180],
    "ink": [255, 255, 255],
    "ink_2": [205, 205, 205],
    "muted": [150, 150, 150],
    "rule": [60, 60, 60],
    "split": [255, 220, 80],
    "split_good": [46, 204, 113],
    "stroke": [0, 0, 0],
    "subtle": [128, 128, 128],
    "surface": [0, 0, 0]
  },
  "fonts": {},
  "slots": {}
}
```

The `card.html` the splitsmith manifest names does not exist until Task 4. Task 1's loader checks that declared slot files exist, so until Task 4 lands, create an empty placeholder `src/splitsmith/data/looks/splitsmith/card.html` containing only `<!doctype html>` and the line `<!-- replaced in Task 4 -->`. Task 4 overwrites it.

- [ ] **Step 2: Write the failing tests**

`tests/test_looks.py`:

```python
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


def test_list_looks_skips_a_broken_user_look_and_warns(user_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
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
    colors = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))["colors"]
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_looks.py -n0 -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.looks'`.

- [ ] **Step 4: Write `src/splitsmith/looks.py`**

```python
"""Looks: where a rendered video's colours and card templates come from.

Spec ``docs/superpowers/specs/2026-10-06-rendered-video-first-design.md``,
section 1. A Look is a directory: ``look.json`` (colour tokens, font
names, which template draws which slot) and one HTML template per card
slot. Shipped Looks live under ``splitsmith/data/looks/``; user Looks
under ``<user_config_dir>/looks/`` and shadow a shipped Look of the same
name. ``_shared/`` under the shipped directory is not a Look: it holds
the engine scripts (``fit.js``, ``cell.js``) every shipped template
loads.

This module is pure. It reads JSON and resolves paths; it never opens a
browser, runs ffmpeg or writes a file. A template file that a manifest
names must exist at load time, so a missing one fails here, with the
file named, rather than in the middle of a render.
"""

from __future__ import annotations

import json
import logging
import re
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .user_config import user_config_dir

logger = logging.getLogger(__name__)

SLOT_NAMES: tuple[str, ...] = ("title_page", "slate", "lower_third", "summary", "closing")
"""Every slot a manifest may name. ``summary`` is reserved: no renderer
reads it in this slice, the stage summary still composes through
``overlay_summary_cell``."""

CardSlot = Literal["title_page", "slate", "lower_third", "closing"]
"""The slots ``overlay_card`` renders through a template."""

DEFAULT_LOOK = "splitsmith"

REQUIRED_COLORS: tuple[str, ...] = (
    "ink",
    "split",
    "split_good",
    "stroke",
    "accent",
    "accent_fill",
    "accent_text",
    "rule",
    "muted",
    "ink_2",
    "surface",
    "subtle",
)
"""The tokens ``overlay_theme.OverlayTheme`` is built from. A manifest may
carry more (``split_slow`` is written by the build script and read by
nothing yet)."""

MANIFEST_FILE = "look.json"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_TEMPLATE_FILE_RE = re.compile(r"^[A-Za-z0-9_.-]+\.html$")

RGB = tuple[int, int, int]


class LookError(RuntimeError):
    """A Look directory that cannot be used, with the reason."""


class LookNotFoundError(LookError):
    """No shipped or user Look has this name."""


class LookManifest(BaseModel):
    """``look.json``. ``extra="ignore"`` so a manifest written by a newer
    splitsmith (with fields this version does not know) still loads."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: int = 1
    name: str
    label: str = ""
    colors: dict[str, RGB]
    fonts: dict[str, str] = {}
    slots: dict[str, str] = {}
    source: str | None = None

    @field_validator("name")
    @classmethod
    def _name_shape(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError(f"Look name {value!r} must match {_NAME_RE.pattern}")
        return value

    @field_validator("colors")
    @classmethod
    def _required_colors(cls, value: dict[str, RGB]) -> dict[str, RGB]:
        missing = [token for token in REQUIRED_COLORS if token not in value]
        if missing:
            raise ValueError(f"missing colour tokens: {', '.join(missing)}")
        for token, rgb in value.items():
            if any(not 0 <= channel <= 255 for channel in rgb):
                raise ValueError(f"colour {token!r} has a channel outside 0..255: {rgb!r}")
        return value

    @field_validator("slots")
    @classmethod
    def _slot_shape(cls, value: dict[str, str]) -> dict[str, str]:
        for slot, file in value.items():
            if slot not in SLOT_NAMES:
                raise ValueError(f"unknown slot {slot!r}; expected one of {SLOT_NAMES}")
            if not _TEMPLATE_FILE_RE.match(file):
                raise ValueError(f"slot {slot!r} must name a bare .html file inside the Look, got {file!r}")
        return value


class Look(BaseModel):
    """A loaded Look: its manifest, where it lives, and whether it is ours
    or the user's."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    manifest: LookManifest
    root: Path
    source: Literal["shipped", "user"]

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def label(self) -> str:
        return self.manifest.label or self.manifest.name

    def own_template(self, slot: str) -> Path | None:
        """This Look's template for ``slot``, or ``None`` when it declares
        none (see :func:`template_for` for the fallback)."""
        file = self.manifest.slots.get(slot)
        return None if file is None else self.root / file


def shipped_looks_dir() -> Path:
    """``splitsmith/data/looks`` inside the installed package."""
    return Path(str(resources.files("splitsmith.data").joinpath("looks")))


def user_looks_dir() -> Path:
    """``<user_config_dir>/looks``; honours ``SPLITSMITH_HOME``. Not created here."""
    return user_config_dir() / "looks"


def shared_dir() -> Path:
    """The engine scripts shipped templates load (``fit.js``, ``cell.js``)."""
    return shipped_looks_dir() / "_shared"


def _read_look(root: Path, source: Literal["shipped", "user"]) -> Look:
    manifest_path = root / MANIFEST_FILE
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LookError(f"{root}: no {MANIFEST_FILE}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise LookError(f"{manifest_path}: cannot read: {exc}") from exc
    try:
        manifest = LookManifest.model_validate(raw)
    except ValidationError as exc:
        raise LookError(f"{manifest_path}: {exc.errors()[0]['msg']}") from exc
    if manifest.name != root.name:
        raise LookError(f"{manifest_path}: name {manifest.name!r} does not match its directory {root.name!r}")
    for slot, file in manifest.slots.items():
        if not (root / file).is_file():
            raise LookError(f"{manifest_path}: slot {slot!r} names {file!r}, which is not in {root}")
    return Look(manifest=manifest, root=root, source=source)


def _candidate_dirs(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_"))


def list_looks() -> list[Look]:
    """Every usable Look: the shipped ones (the default first, then by
    name), then the user's by name. A user Look with a shipped Look's
    name replaces it in place. A user directory that is not a valid Look
    is skipped with a warning; a broken shipped Look raises, because that
    is a packaging defect."""
    shipped: dict[str, Look] = {}
    for root in _candidate_dirs(shipped_looks_dir()):
        look = _read_look(root, "shipped")
        shipped[look.name] = look
    ordered = [name for name in (DEFAULT_LOOK, *sorted(shipped)) if name in shipped]
    result: dict[str, Look] = {}
    for name in ordered:
        if name not in result:
            result[name] = shipped[name]
    for root in _candidate_dirs(user_looks_dir()):
        try:
            look = _read_look(root, "user")
        except LookError as exc:
            logger.warning("ignoring user Look %s: %s", root.name, exc)
            continue
        result[look.name] = look
    return list(result.values())


def look_names() -> tuple[str, ...]:
    return tuple(look.name for look in list_looks())


def load_look(name: str) -> Look:
    """The user's Look of this name, else the shipped one, else
    :class:`LookNotFoundError`. A user Look that exists but is broken
    raises :class:`LookError` rather than silently falling through to
    the shipped one: the user asked for theirs."""
    user_root = user_looks_dir() / name
    if (user_root / MANIFEST_FILE).exists():
        return _read_look(user_root, "user")
    shipped_root = shipped_looks_dir() / name
    if (shipped_root / MANIFEST_FILE).exists():
        return _read_look(shipped_root, "shipped")
    raise LookNotFoundError(f"no Look named {name!r}; installed: {', '.join(look_names()) or 'none'}")


def template_for(look: Look, slot: CardSlot) -> Path:
    """The template that draws ``slot`` for ``look``: its own, else the
    shipped default Look's. The shipped default declares every card
    slot (pinned by ``tests/test_looks.py``), so this always resolves."""
    own = look.own_template(slot)
    if own is not None:
        return own
    fallback = _read_look(shipped_looks_dir() / DEFAULT_LOOK, "shipped").own_template(slot)
    if fallback is None:
        raise LookError(f"the shipped {DEFAULT_LOOK!r} Look has no template for slot {slot!r}")
    return fallback


__all__ = [
    "DEFAULT_LOOK",
    "REQUIRED_COLORS",
    "SLOT_NAMES",
    "CardSlot",
    "Look",
    "LookError",
    "LookManifest",
    "LookNotFoundError",
    "list_looks",
    "load_look",
    "look_names",
    "shared_dir",
    "shipped_looks_dir",
    "template_for",
    "user_looks_dir",
]
```

Create `src/splitsmith/data/looks/_shared/.gitkeep` is not needed: Task 3 puts `fit.js` there. For Task 1's last test to pass, create the directory with `fit.js` as an empty file now; Task 3 fills it.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_looks.py -n0 -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/splitsmith/looks.py tests/test_looks.py && uv run black --check src/splitsmith/looks.py tests/test_looks.py
git add src/splitsmith/looks.py src/splitsmith/data/looks tests/test_looks.py
git commit -m "feat(looks): the Look directory, loader and the two shipped manifests (#1241)"
```

---

### Task 2: `overlay_theme` reads from Looks; the build script writes `look.json`

**Files:**
- Modify: `src/splitsmith/overlay_theme.py`
- Delete: `src/splitsmith/data/overlay_theme.json`
- Modify: `scripts/build_overlay_theme.py`
- Modify: `src/splitsmith/cli.py:1204-1240`, `src/splitsmith/match_cli.py:511-545`, `scripts/render_match_frames.py:161`, `scripts/render_look_thumbnails.py:195`
- Test: `tests/test_overlay_theme.py`, `tests/test_looks.py`

**Interfaces:**
- Consumes: `looks.load_look`, `looks.look_names`, `looks.Look`, `looks.LookNotFoundError`.
- Produces: `overlay_theme.theme_for(look: Look) -> OverlayTheme`; `overlay_theme.load_theme(name: str) -> OverlayTheme` (any installed Look; `OverlayThemeError` for an unknown one); `OverlayTheme.name: str`. `ThemeName` and `THEME_NAMES` stay, documented as the API's typed pair.

- [ ] **Step 1: Update the theme tests**

In `tests/test_overlay_theme.py` replace `test_splitsmith_preset_loads_from_packaged_json` with:

```python
def test_splitsmith_preset_loads_from_its_look_manifest() -> None:
    """The ``splitsmith`` preset round-trips through its Look's manifest so
    a regenerate step actually flows into runtime."""
    from splitsmith import looks

    data = json.loads((looks.shipped_looks_dir() / "splitsmith" / "look.json").read_text(encoding="utf-8"))
    t = overlay_theme.load_theme("splitsmith")
    assert t.name == "splitsmith"
    assert list(t.ink) == data["colors"]["ink"]
    assert list(t.split) == data["colors"]["split"]
```

(keep the rest of that test's assertions, swapping `data` reads the same way), and add:

```python
def test_theme_for_a_user_look_reads_its_own_colours(tmp_path, monkeypatch) -> None:
    from splitsmith import looks

    monkeypatch.setenv("SPLITSMITH_HOME", str(tmp_path))
    manifest = json.loads((looks.shipped_looks_dir() / "clean" / "look.json").read_text(encoding="utf-8"))
    manifest["name"] = "club"
    manifest["colors"]["ink"] = [1, 2, 3]
    (tmp_path / "looks" / "club").mkdir(parents=True)
    (tmp_path / "looks" / "club" / "look.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert load_theme("club").ink == (1, 2, 3)
    assert load_theme("club").name == "club"


def test_the_old_theme_json_is_gone() -> None:
    """One theme source: the Look manifest. A stray copy of the old file
    would be read by nothing and drift silently."""
    assert not resources.files("splitsmith.data").joinpath("overlay_theme.json").is_file()
```

`test_unknown_theme_raises` stays (it expects `OverlayThemeError`). `test_overlay_theme_json_is_in_sync_with_css` stays as written; the script's default output moves.

Add to `tests/test_looks.py`:

```python
def test_cli_theme_accepts_an_installed_user_look(user_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--theme`` validates against the installed Looks, not a literal pair."""
    from splitsmith.cli import _validate_theme

    _write_look(user_dir, "club")
    assert _validate_theme("club") == "club"
    with pytest.raises(Exception, match="nope"):
        _validate_theme("nope")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_overlay_theme.py tests/test_looks.py -n0 -q`
Expected: the new tests FAIL (`theme_for` and the user Look path do not exist; the JSON still exists; `_validate_theme` is not defined).

- [ ] **Step 3: Rewrite the loading half of `overlay_theme.py`**

Keep the module docstring's first paragraphs but replace the "Two presets ship today" and JSON-mirror paragraphs with:

```
Palettes come from Looks (``splitsmith.looks``): ``load_theme(name)``
reads ``<look>/look.json``'s ``colors``. The two shipped Looks are
``splitsmith`` (tokens lifted from the web UI's ``index.css`` by
``scripts/build_overlay_theme.py``, so the overlay cannot drift from the
design system) and ``clean`` (neutral white on black). A user Look under
``~/.splitsmith/looks/`` is a theme too.
```

Delete `_CLEAN`, `_load_splitsmith`, `_rgb`, `_SPLITSMITH` and the `json` / `resources` imports. Change `name: ThemeName` on `OverlayTheme` to `name: str`. Replace `load_theme` with:

```python
def theme_for(look: Look) -> OverlayTheme:
    """The palette a Look declares (``look.json``'s ``colors``)."""
    c = look.manifest.colors
    return OverlayTheme(
        name=look.name,
        ink=c["ink"],
        split=c["split"],
        split_good=c["split_good"],
        stroke=c["stroke"],
        accent=c["accent"],
        accent_fill=c["accent_fill"],
        accent_text=c["accent_text"],
        rule=c["rule"],
        muted=c["muted"],
        ink_2=c["ink_2"],
        surface=c["surface"],
        subtle=c["subtle"],
    )


def load_theme(name: str) -> OverlayTheme:
    """Resolve a Look name to its palette. Any installed Look, shipped or
    the user's. ``OverlayThemeError`` for an unknown name, so the callers
    that predate Looks keep the exception they handle."""
    try:
        return theme_for(load_look(name))
    except LookNotFoundError as exc:
        raise OverlayThemeError(str(exc)) from exc
    except LookError as exc:
        raise OverlayThemeError(f"Look {name!r} cannot be used: {exc}") from exc
```

with `from .looks import Look, LookError, LookNotFoundError, load_look` at the top. Keep `ThemeName`, `THEME_NAMES`, `OverlayThemeError`, `RGB` and the dataclass. Add to `ThemeName`'s docstring: "The typed pair the export API accepts today. The CLI and the renderers take any installed Look; widening the API is #1246."

- [ ] **Step 4: Delete the JSON and point the build script at the manifest**

```bash
git rm src/splitsmith/data/overlay_theme.json
```

In `scripts/build_overlay_theme.py`: module docstring says it now writes `colors`, `fonts` and `source` into `src/splitsmith/data/looks/splitsmith/look.json`, keeping every other key (`slots`, `label`, `schema_version`) as found. Change `DEFAULT_OUTPUT_PATH` to `REPO_ROOT / "src/splitsmith/data/looks/splitsmith/look.json"`. Replace the body of `main()` after `theme = build_theme(args.css)` with:

```python
    existing: dict[str, object] = {}
    if args.output.exists():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
    merged = {**existing, **theme}
    merged.setdefault("schema_version", 1)
    merged.setdefault("name", "splitsmith")
    merged.setdefault("label", "Splitsmith")
    merged.setdefault("slots", {})
    rendered = json.dumps(merged, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not args.output.exists():
            print(f"missing {args.output}", file=sys.stderr)
            return 1
        if args.output.read_text(encoding="utf-8") != rendered:
            print(f"{args.output} is out of sync; re-run without --check", file=sys.stderr)
            return 1
        print(f"{args.output} up to date")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0
```

Then normalise the hand-written manifest once:

```bash
uv run python scripts/build_overlay_theme.py
git diff --stat src/splitsmith/data/looks/splitsmith/look.json
```

Expected: the file is rewritten with sorted keys and the same values.

- [ ] **Step 5: CLI validation against installed Looks**

In `src/splitsmith/cli.py`, next to the `--theme` option (line 1204), add a helper and use it in place of the `THEME_NAMES` check at line 1238:

```python
def _validate_theme(name: str) -> str:
    from .looks import look_names

    names = look_names()
    if name not in names:
        raise typer.BadParameter(f"--theme must be one of {', '.join(names)}, got {name!r}")
    return name
```

and the option help becomes `"Look (colour palette and card templates): one of the installed Looks, `splitsmith` by default."`. In `src/splitsmith/match_cli.py:544` replace the hardcoded tuple check with `from .looks import look_names` and the same message shape (keep `console.print` and `raise typer.Exit(1)` as the file does today). In `scripts/render_match_frames.py:161` and `scripts/render_look_thumbnails.py:195` drop `choices=("splitsmith", "clean")` and keep the default.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_overlay_theme.py tests/test_looks.py tests/test_overlay_html.py tests/test_overlay_card.py -n0 -q`
Expected: all PASS.

Run: `rg -n "overlay_theme.json" src scripts tests docs CLAUDE.md SPEC.md`
Expected: no hits outside the deleted-file test and historical notes in `docs/`. Fix any live reference.

- [ ] **Step 7: Commit**

```bash
git add -A src/splitsmith/overlay_theme.py src/splitsmith/data scripts/build_overlay_theme.py src/splitsmith/cli.py src/splitsmith/match_cli.py scripts/render_match_frames.py scripts/render_look_thumbnails.py tests/test_overlay_theme.py tests/test_looks.py
git commit -m "feat(looks): overlay_theme reads the Look manifest; the build script writes it (#1241)"
```

---

### Task 3: `fit.js` as a shipped file and `single_css` in `overlay_html`

Pixel-neutral refactor so the shipped template can load the same fit policy and stylesheet the Python path emits.

**Files:**
- Create: `src/splitsmith/data/looks/_shared/fit.js`
- Modify: `src/splitsmith/overlay_html.py:741-895` (`_fit_script`), `:921-1000` (`single_html`)
- Test: `tests/test_overlay_html.py`

**Interfaces:**
- Produces: `overlay_html.fit_js() -> str` (the file's text), `overlay_html.single_css(*, width: int, height: int, scale: CellScale, theme: OverlayTheme) -> str`. `single_html` returns the same document it does today, byte for byte.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_overlay_html.py`:

```python
def test_single_html_is_single_css_plus_the_fit_script_plus_the_cell() -> None:
    from splitsmith.overlay_html import _cell_div, _fit_script, single_css, single_html
    from splitsmith.overlay_theme import load_theme

    theme = load_theme("splitsmith")
    scale = CellScale.for_cell(360)
    groups = (Group(anchor=Anchor.MIDDLE_CENTER, flow=Flow.ROW, elements=(Element(role=Role.DETAIL, text="x"),)),)
    doc = single_html(groups, width=640, height=360, scale=scale, theme=theme)
    assert f"<style>{single_css(width=640, height=360, scale=scale, theme=theme)}</style>" in doc
    assert _fit_script() in doc
    assert _cell_div(groups) in doc


def test_the_fit_script_is_the_shipped_file_with_the_floor_set_beside_it() -> None:
    from splitsmith.overlay_html import _fit_script, fit_js
    from splitsmith.overlay_layout import MIN_FONT_SIZE

    script = _fit_script()
    assert fit_js() in script
    assert f"window.__splitsmithMinFont = {MIN_FONT_SIZE};" in script
    assert "window.__splitsmithMinFont" in fit_js()
    assert str(MIN_FONT_SIZE) not in fit_js(), "the floor is set by the caller, never baked into the file"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_overlay_html.py -n0 -q -k "single_css or fit_script"`
Expected: FAIL, `ImportError` for `single_css` / `fit_js`.

- [ ] **Step 3: Move the script body into `fit.js`**

Create `src/splitsmith/data/looks/_shared/fit.js` with the exact JavaScript between `<script>` and `</script>` in today's `_fit_script()`, with two edits: every `{{` becomes `{` and `}}` becomes `}` (they were f-string escapes), and `{MIN_FONT_SIZE} / min` becomes `window.__splitsmithMinFont / min`. Add this header comment at the top of the file:

```js
// The overlay's fit policy (issue #683 F1): shrink a cell's middle band
// to fit its track, then drop elements by data-drop-priority. Loaded
// inline by overlay_html._fit_script() and by file URL from a Look's
// card template. The legibility floor comes from
// window.__splitsmithMinFont, which the caller sets before calling
// window.__splitsmithFit(); the file bakes in no number.
```

Then in `overlay_html.py`:

```python
def fit_js() -> str:
    """The shipped fit-policy script, ``data/looks/_shared/fit.js``. One
    file for the Python path (inlined here) and the Look templates
    (loaded by URL), so the two cannot drift."""
    return resources.files("splitsmith.data").joinpath("looks/_shared/fit.js").read_text(encoding="utf-8")


def _fit_script() -> str:
    return (
        f"<script>window.__splitsmithMinFont = {MIN_FONT_SIZE};</script>\n"
        f"<script>\n{fit_js()}</script>"
    )
```

Keep `_fit_script`'s docstring (move it onto `fit_js`). `from importlib import resources` at the top. The old `_fit_script` body's trailing `.strip()` is gone; nothing depends on it.

- [ ] **Step 4: Factor `single_css` out of `single_html`**

```python
def single_css(*, width: int, height: int, scale: CellScale, theme: OverlayTheme) -> str:
    """The whole stylesheet ``single_html`` puts in its ``<style>``: the
    shared rules, the page sizing, and the one single-shooter override.
    Public so a Look template can ask for exactly this text through
    ``window.splitsmith.engine.css`` and render what Python renders."""
    style = _style_rules(scale=scale, theme=theme)
    page_style = (
        "html, body {\n"
        "margin: 0; padding: 0;\n"
        f"width: {width}px; height: {height}px;\n"
        "background: transparent; overflow: hidden;\n"
        "}"
    )
    single_style = (
        ".role-live-primary {\n"
        f"-webkit-text-stroke: {2 * border_width(scale.live_primary)}px rgb({_rgb(theme.stroke)});\n"
        "}"
    )
    return f"{style}\n{page_style}\n{single_style}"
```

and `single_html`'s return becomes:

```python
    return (
        "<!doctype html>\n"
        '<html><head><meta charset="utf-8"><title>overlay</title>'
        f"<style>{single_css(width=width, height=height, scale=scale, theme=theme)}</style>"
        f"{_fit_script()}"
        "</head>"
        f"<body>{_cell_div(groups)}</body></html>"
    )
```

Keep `single_html`'s docstring; the comment about source order moves into `single_css`.

- [ ] **Step 5: Run the overlay suites**

Run: `uv run pytest tests/test_overlay_html.py tests/test_overlay_card.py tests/test_overlay_summary_cell.py tests/test_compare_overlay_summary.py tests/test_compare_overlay_live.py tests/test_overlay_raster.py -n0 -q`
Expected: all PASS (the raster tests that need Chromium skip when it is absent).

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/data/looks/_shared/fit.js src/splitsmith/overlay_html.py tests/test_overlay_html.py
git commit -m "refactor(overlay): fit.js is a shipped file; single_css is public (#1241)"
```

---

### Task 4: The template contract: `look_template`, `cell.js`, `card.html`, `Rasterizer.render_template`

**Files:**
- Create: `src/splitsmith/look_template.py`
- Create: `src/splitsmith/data/looks/_shared/cell.js`
- Overwrite: `src/splitsmith/data/looks/splitsmith/card.html`
- Modify: `src/splitsmith/overlay_raster.py:60-67` (Protocol), `:209-272` (add `render_template`)
- Test: `tests/test_look_template.py`, `tests/test_overlay_raster.py`

**Interfaces:**
- Consumes: `overlay_layout.Group`, `Element`, `MIN_FONT_SIZE`; `overlay_theme.OverlayTheme`; `looks.shared_dir`.
- Produces:
  - `look_template.TemplateContext(BaseModel)`: `theme: dict[str, str]`, `data: dict[str, Any]`, `size: dict[str, int]`, `fps: float`, `engine: dict[str, Any]`, `assets: dict[str, str]`; method `init_script() -> str`.
  - `look_template.theme_tokens(theme: OverlayTheme) -> dict[str, str]` (token name to `#rrggbb`, `shadow` included).
  - `look_template.group_json(group: Group) -> dict[str, Any]`.
  - `look_template.engine_block(*, css: str) -> dict[str, Any]` returning `{"css": css, "min_font_size": MIN_FONT_SIZE}`.
  - `look_template.shared_url() -> str` (a `file://` URL to `looks.shared_dir()`).
  - `Rasterizer.render_template(template: Path, *, context: TemplateContext, width: int, height: int) -> bytes`.
  - In the page: `window.splitsmith.engine.renderGroups(groups) -> string`, `window.splitsmith.engine.mount(groups)`, `window.duration()`, `window.seek(seconds)`.

- [ ] **Step 1: Write the failing unit tests**

`tests/test_look_template.py`:

```python
"""The template contract: what a Look template receives, and the shipped
card template's parity with the Python markup."""

from __future__ import annotations

import json

import pytest

from splitsmith import look_template, looks
from splitsmith.overlay_layout import MIN_FONT_SIZE, Anchor, ColorToken, Element, Emphasis, Flow, Group, Role
from splitsmith.overlay_theme import load_theme


def test_theme_tokens_are_hex_strings_for_every_palette_field() -> None:
    tokens = look_template.theme_tokens(load_theme("splitsmith"))
    assert tokens["ink"] == "#f4f4f5"
    assert tokens["shadow"] == tokens["stroke"]
    assert set(looks.REQUIRED_COLORS) <= set(tokens)


def test_group_json_carries_every_declaration_field_as_plain_values() -> None:
    group = Group(
        anchor=Anchor.BOTTOM_LEFT,
        flow=Flow.ROW,
        elements=(
            Element(role=Role.HEADLINE, text="Stage 3", emphasis=Emphasis.PLATE, color=ColorToken.SPLIT_GOOD),
            Element(role=Role.DETAIL, text="24", caption="rounds", unit="r", drop_priority=2),
        ),
        align="left",
        gap=4,
        margin_top=8,
    )
    assert look_template.group_json(group) == {
        "anchor": "bottom-left",
        "flow": "row",
        "divider": False,
        "align": "left",
        "gap": 4,
        "margin_top": 8,
        "elements": [
            {
                "role": "headline",
                "text": "Stage 3",
                "emphasis": "plate",
                "caption": None,
                "color": "split_good",
                "unit": None,
                "drop_priority": None,
            },
            {
                "role": "detail",
                "text": "24",
                "emphasis": "plain",
                "caption": "rounds",
                "color": None,
                "unit": "r",
                "drop_priority": 2,
            },
        ],
    }


def test_init_script_assigns_the_whole_context_to_window_splitsmith() -> None:
    ctx = look_template.TemplateContext(
        theme={"ink": "#ffffff"},
        data={"card": {"text": "O'Neil </script><b>"}},
        size={"width": 64, "height": 32},
        fps=30,
        engine=look_template.engine_block(css="body{}"),
        assets={"shared": look_template.shared_url()},
    )
    script = ctx.init_script()
    assert script.startswith("window.splitsmith = ")
    payload = json.loads(script[len("window.splitsmith = ") : -1])
    assert payload["data"]["card"]["text"] == "O'Neil </script><b>"
    assert payload["engine"]["min_font_size"] == MIN_FONT_SIZE
    assert payload["assets"]["shared"].startswith("file://")
    assert "</script>" not in script, "a value must never be able to close the init script element"


def test_shared_url_points_at_the_shipped_engine_scripts() -> None:
    url = look_template.shared_url()
    assert url.endswith("/_shared")
    assert (looks.shared_dir() / "cell.js").is_file()
    assert (looks.shared_dir() / "fit.js").is_file()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_look_template.py -n0 -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'splitsmith.look_template'`.

- [ ] **Step 3: Write `src/splitsmith/look_template.py`**

```python
"""The template contract (spec 2026-10-06, section 1).

A Look template is one HTML document. Before any of its scripts run the
renderer assigns ``window.splitsmith`` (see :class:`TemplateContext`);
the document may define ``window.duration()`` (seconds; 0 for a still)
and ``window.seek(seconds)``. This slice renders every template at
``seek(0)``.

``engine`` carries what the shipped templates need to draw exactly what
``overlay_html`` draws: the stylesheet text (``css``) and the fit
policy's legibility floor (``min_font_size``). ``assets.shared`` is a
``file://`` URL to ``data/looks/_shared``, where ``fit.js`` and
``cell.js`` live. A custom Look may ignore all of it and draw
``data.card`` its own way.

Pure: builds JSON-able values and a path; nothing here opens a browser.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from .looks import shared_dir
from .overlay_layout import MIN_FONT_SIZE, Element, Group
from .overlay_theme import OverlayTheme


class TemplateContext(BaseModel):
    """``window.splitsmith`` as the template sees it."""

    model_config = ConfigDict(frozen=True)

    theme: dict[str, str]
    data: dict[str, Any]
    size: dict[str, int]
    fps: float
    engine: dict[str, Any]
    assets: dict[str, str]

    def init_script(self) -> str:
        """The statement the renderer installs as an init script. ``</``
        is split so a value containing ``</script>`` cannot end the
        script element the browser parses this into."""
        payload = json.dumps(self.model_dump(), ensure_ascii=False).replace("</", "<\\/")
        return f"window.splitsmith = {payload};"


def _hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def theme_tokens(theme: OverlayTheme) -> dict[str, str]:
    """Every palette field as ``#rrggbb``, plus the derived ``shadow``."""
    tokens = {f.name: _hex(getattr(theme, f.name)) for f in dataclasses.fields(theme) if f.name != "name"}
    tokens["shadow"] = _hex(theme.shadow)
    return tokens


def _element_json(element: Element) -> dict[str, Any]:
    return {
        "role": element.role.value,
        "text": element.text,
        "emphasis": element.emphasis.value,
        "caption": element.caption,
        "color": None if element.color is None else element.color.value,
        "unit": element.unit,
        "drop_priority": element.drop_priority,
    }


def group_json(group: Group) -> dict[str, Any]:
    """A :class:`Group` as the plain values ``cell.js`` reads. Field for
    field what ``overlay_html._group_div`` reads."""
    return {
        "anchor": group.anchor.value,
        "flow": group.flow.value,
        "divider": group.divider,
        "align": group.align,
        "gap": group.gap,
        "margin_top": group.margin_top,
        "elements": [_element_json(e) for e in group.elements],
    }


def engine_block(*, css: str) -> dict[str, Any]:
    return {"css": css, "min_font_size": MIN_FONT_SIZE}


def shared_url() -> str:
    return shared_dir().resolve().as_uri()


__all__ = ["TemplateContext", "engine_block", "group_json", "shared_url", "theme_tokens"]
```

- [ ] **Step 4: Write `cell.js`**

`src/splitsmith/data/looks/_shared/cell.js`. It is a line-for-line port of `overlay_html._cell_div` and the helpers it calls (`_anchor_classes`, `_group_classes`, `_color_class`, `_element_div`, `_group_style`, `_group_div`, `_anchor_div`, `_fit`); the parity test in Step 7 holds it to the same markup.

```js
// The cell markup builder: a port of overlay_html._cell_div and the
// helpers it calls, so a Look template can render a declared cell with
// the engine's own stylesheet (window.splitsmith.engine.css). Keep the
// two in step: tests/test_look_template.py compares the DOM this builds
// with the DOM Python builds for the same groups.
(function () {
  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#x27;');
  }
  function isBottom(anchor) { return anchor === 'bottom-left' || anchor === 'bottom-center' || anchor === 'bottom-right'; }
  function isRight(anchor) { return anchor === 'top-right' || anchor === 'bottom-right'; }
  function isCenter(anchor) { return anchor === 'top-center' || anchor === 'middle-center' || anchor === 'bottom-center'; }
  function fit(px) { return 'calc(var(--fit-scale, 1) * ' + px + 'px)'; }

  function anchorClasses(anchor, align) {
    var classes = ['anchor', 'anchor-' + anchor, isBottom(anchor) ? 'stack-reverse' : 'stack-normal'];
    var resolved = align;
    if (resolved == null) {
      resolved = isRight(anchor) ? 'right' : isCenter(anchor) ? 'center' : 'left';
    }
    classes.push('align-' + resolved);
    return classes.join(' ');
  }
  function groupClasses(g) {
    var classes = ['group', 'flow-' + g.flow];
    if (g.flow === 'column') {
      classes.push(isRight(g.anchor) ? 'align-right' : isCenter(g.anchor) ? 'align-center' : 'align-left');
    }
    return classes.join(' ');
  }
  function colorClass(color) { return color == null ? '' : 'tok-' + color.replace(/_/g, '-'); }
  function elementDiv(e) {
    var elClasses = e.role === 'identity' ? 'el el-identity' : 'el';
    var caption = e.caption != null ? '<span class="caption">' + esc(e.caption) + '</span>' : '';
    var unit = e.unit != null ? '<span class="unit">' + esc(e.unit) + '</span>' : '';
    var valueClasses = ['value', 'role-' + e.role, 'emphasis-' + e.emphasis, colorClass(e.color)]
      .filter(function (c) { return c; }).join(' ');
    var value = '<span class="' + valueClasses + '">' + esc(e.text) + unit + '</span>';
    var priority = e.drop_priority != null ? ' data-drop-priority="' + e.drop_priority + '"' : '';
    return '<div class="' + elClasses + '"' + priority + '>' + caption + value + '</div>';
  }
  function groupStyle(g) {
    var parts = [];
    if (g.flow === 'grid') { parts.push('grid-template-columns: repeat(' + Math.max(1, g.elements.length) + ', 1fr)'); }
    if (g.gap != null) { parts.push('gap: ' + fit(g.gap)); }
    if (g.margin_top != null) { parts.push('margin-top: ' + fit(g.margin_top)); }
    return parts.length ? ' style="' + parts.join('; ') + '"' : '';
  }
  function groupDiv(g) {
    if (g.divider) { return '<div class="group divider"></div>'; }
    return '<div class="' + groupClasses(g) + '"' + groupStyle(g) + '>' + g.elements.map(elementDiv).join('') + '</div>';
  }
  function anchorDiv(anchor, members) {
    var align = null;
    for (var i = 0; i < members.length; i++) {
      if (members[i].align != null) { align = members[i].align; break; }
    }
    return '<div class="' + anchorClasses(anchor, align) + '">' + members.map(groupDiv).join('') + '</div>';
  }
  function cellHtml(groups) {
    var order = [];
    var buckets = {};
    groups.forEach(function (g) {
      if (!buckets[g.anchor]) { buckets[g.anchor] = []; order.push(g.anchor); }
      buckets[g.anchor].push(g);
    });
    return '<div class="cell">' + order.map(function (a) { return anchorDiv(a, buckets[a]); }).join('') + '</div>';
  }

  var engine = window.splitsmith.engine;
  engine.renderGroups = cellHtml;
  engine.mount = function (groups) { document.body.innerHTML = cellHtml(groups); };
  if (typeof window.duration !== 'function') { window.duration = function () { return 0; }; }
  if (typeof window.seek !== 'function') { window.seek = function () {}; }
})();
```

- [ ] **Step 5: Write the shipped `card.html`**

Overwrite `src/splitsmith/data/looks/splitsmith/card.html`:

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>card</title>
<script>
  // The shipped card: the engine's own stylesheet and cell markup, so
  // this template renders what overlay_html renders for the same
  // declaration. A custom Look may ignore both and draw
  // window.splitsmith.data.card its own way.
  document.write('<style>' + window.splitsmith.engine.css + '</style>');
  document.write('<script src="' + window.splitsmith.assets.shared + '/fit.js"><\/script>');
  document.write('<script src="' + window.splitsmith.assets.shared + '/cell.js"><\/script>');
</script>
</head>
<body>
<script>
  window.__splitsmithMinFont = window.splitsmith.engine.min_font_size;
  window.splitsmith.engine.mount(window.splitsmith.data.groups);
</script>
</body>
</html>
```

- [ ] **Step 6: Add `render_template` to the rasterizer**

In `src/splitsmith/overlay_raster.py`, the Protocol gains a second method (import `TemplateContext` under `TYPE_CHECKING` and `Path` from `pathlib`):

```python
    def render_template(self, template: Path, *, context: TemplateContext, width: int, height: int) -> bytes: ...
```

and `ChromiumRasterizer` gains:

```python
    def render_template(self, template: Path, *, context: TemplateContext, width: int, height: int) -> bytes:
        """Render a Look template to a ``width`` x ``height`` alpha PNG.

        ``context.init_script()`` is installed on a fresh browser context
        so ``window.splitsmith`` exists before the document's first
        script runs; the template is navigated to by ``file://`` URL so
        its own relative references and the ``file://`` script URLs in
        ``assets.shared`` resolve. After load: fonts, ``seek(0)``, fonts
        again (a template may add a face while mounting), the fit policy
        when the template defines it, then the same transparent
        screenshot ``png`` takes.
        """
        if self._browser is None:
            raise RuntimeError(
                "ChromiumRasterizer.render_template() called outside its own 'with' block -- the browser is "
                "only live between __enter__ and __exit__"
            )
        browser_context = self._browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=DEVICE_SCALE_FACTOR,
        )
        try:
            browser_context.add_init_script(context.init_script())
            page = browser_context.new_page()
            page.goto(template.resolve().as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
            page.evaluate("typeof window.seek === 'function' ? window.seek(0) : undefined")
            page.evaluate("document.fonts.ready")
            page.evaluate("window.__splitsmithFit && window.__splitsmithFit()")
            return page.screenshot(type="png", omit_background=True)
        finally:
            browser_context.close()
```

In `tests/test_overlay_raster.py` give `_RecordingContext` an `add_init_script` double and record it:

```python
    def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)
```

with `self.init_scripts: list[str] = []` in its `__init__`. Then add:

```python
def _context_fixture() -> "TemplateContext":
    from splitsmith.look_template import TemplateContext, engine_block, shared_url

    return TemplateContext(
        theme={"ink": "#ffffff"},
        data={"groups": []},
        size={"width": 64, "height": 32},
        fps=30,
        engine=engine_block(css="body{}"),
        assets={"shared": shared_url()},
    )


def test_render_template_installs_the_context_before_navigating(tmp_path: Path) -> None:
    template = tmp_path / "card.html"
    template.write_text("<!doctype html><body></body>", encoding="utf-8")
    browser = _RecordingBrowser()
    r = ChromiumRasterizer()
    r._browser = browser  # type: ignore[assignment]
    out = r.render_template(template, context=_context_fixture(), width=64, height=32)
    assert out == b"FAKE-PNG-BYTES"
    ctx = browser.contexts[0]
    assert ctx.viewport == {"width": 64, "height": 32}
    assert ctx.init_scripts == [_context_fixture().init_script()]
    page = ctx.pages[0]
    kinds = [c[0] for c in page.calls]
    assert kinds == ["goto", "evaluate", "evaluate", "evaluate", "evaluate", "screenshot"]
    assert page.goto_url == template.resolve().as_uri()
    assert page.calls[1] == ("evaluate", "document.fonts.ready")
    assert "window.seek" in page.calls[2][1]
    assert page.calls[-1] == ("screenshot", "png", True)
    assert ctx.closed


def test_render_template_closes_its_context_when_screenshot_raises(tmp_path: Path) -> None:
    template = tmp_path / "card.html"
    template.write_text("<!doctype html><body></body>", encoding="utf-8")
    browser = _RecordingBrowser(page_factory=_BoomOnScreenshotPage)
    r = ChromiumRasterizer()
    r._browser = browser  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="screenshot boom"):
        r.render_template(template, context=_context_fixture(), width=64, height=32)
    assert browser.contexts[0].closed


def test_render_template_outside_context_manager_raises_runtime_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="render_template"):
        ChromiumRasterizer().render_template(tmp_path / "x.html", context=_context_fixture(), width=1, height=1)
```

Check how the existing `png` tests install `_RecordingBrowser` (they may use a helper rather than assigning `_browser`); follow that file's own idiom.

- [ ] **Step 7: The parity test (integration, needs Chromium)**

Append to `tests/test_look_template.py`:

```python
@pytest.mark.integration
def test_the_shipped_card_template_builds_the_markup_python_builds() -> None:
    """``cell.js`` is a port of ``overlay_html._cell_div``. Same groups in,
    same DOM out, through the browser's own serializer on both sides so
    escaping differences cannot hide. Text carries every character Python
    escapes."""
    from playwright.sync_api import sync_playwright

    from splitsmith.overlay_card import card_groups
    from splitsmith.composition import TitleCard
    from splitsmith.overlay_html import _cell_div, single_css
    from splitsmith.overlay_layout import CellScale
    from splitsmith.overlay_raster import CHROMIUM_CHANNEL, RasterizerUnavailableError, _unavailable

    card = TitleCard(
        text="O'Neil & <Sons> \"Classic\"",
        duration_seconds=1.5,
        style="lower-third",
        info=("24 rounds", "Comstock & co"),
    )
    groups = card_groups(card)
    theme = load_theme("splitsmith")
    ctx = look_template.TemplateContext(
        theme=look_template.theme_tokens(theme),
        data={"card": {"text": card.text}, "groups": [look_template.group_json(g) for g in groups]},
        size={"width": 640, "height": 360},
        fps=30,
        engine=look_template.engine_block(
            css=single_css(width=640, height=360, scale=CellScale.for_cell(360), theme=theme)
        ),
        assets={"shared": look_template.shared_url()},
    )
    template = looks.load_look("splitsmith").own_template("lower_third")
    assert template is not None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel=CHROMIUM_CHANNEL, headless=True)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(str(_unavailable(exc)))
        context = browser.new_context(viewport={"width": 640, "height": 360})
        context.add_init_script(ctx.init_script())
        page = context.new_page()
        page.goto(template.resolve().as_uri(), wait_until="load")
        from_js = page.evaluate("document.body.innerHTML")
        from_python = page.evaluate("html => { document.body.innerHTML = html; return document.body.innerHTML; }", _cell_div(groups))
        assert page.evaluate("typeof window.duration") == "function"
        assert page.evaluate("window.duration()") == 0
        browser.close()
    assert from_js == from_python
```

If `_unavailable` is not importable, catch `RasterizerUnavailableError` the way `test_overlay_raster.py`'s integration tests do and copy that idiom.

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/test_look_template.py tests/test_overlay_raster.py -n0 -q`
Expected: all PASS locally (Chromium installed via `uv run playwright install chromium --only-shell`). The integration test skips only when the browser is missing; CI runs it with `SPLITSMITH_REQUIRE_INTEGRATION=1`.

- [ ] **Step 9: Commit**

```bash
git add src/splitsmith/look_template.py src/splitsmith/data/looks/_shared/cell.js src/splitsmith/data/looks/splitsmith/card.html src/splitsmith/overlay_raster.py tests/test_look_template.py tests/test_overlay_raster.py
git commit -m "feat(looks): the template contract, cell.js and the shipped card template (#1241)"
```

---

### Task 5: Cards render through the Look's template

**Files:**
- Modify: `src/splitsmith/overlay_card.py`
- Modify: `src/splitsmith/mp4_render.py:270-420`, `src/splitsmith/compare/mp4_grid.py:2236-2270` and its lower-third site, `src/splitsmith/export_preview.py:235-300`, `src/splitsmith/ui/export_preview_api.py:115-120`, `scripts/render_look_thumbnails.py:138-186`
- Test: `tests/test_overlay_card.py`, every test file with a fake rasterizer (`tests/test_mp4_render.py`, `tests/test_compare_mp4_grid_cards.py`, `tests/test_export_preview.py`, `tests/test_export_preview_api.py`, `tests/test_render_look_thumbnails.py`; the rest only implement `png` and need no change unless they build cards)

**Interfaces:**
- Consumes: `looks.Look`, `looks.template_for`, `looks.CardSlot`, `overlay_theme.theme_for`, `look_template.*`, `overlay_html.single_css`.
- Produces:
  - `overlay_card.card_context(card: Card, *, slot: CardSlot, width: int, height: int, fps: float, theme: OverlayTheme) -> TemplateContext`
  - `overlay_card.build_card_still(card, *, slot: CardSlot, width, height, fps: float, look: Look, rasterizer, backdrop, blur_radius=None, dim=DEFAULT_DIM) -> Image | None`
  - `overlay_card.build_lower_third(card: TitleCard, *, width, height, fps: float, look: Look, rasterizer) -> Image | None`
  - `card_groups`, `card_scale`, `lower_third_filters`, `LOWER_THIRD_FADE_SECONDS` unchanged.

- [ ] **Step 1: Update the card tests**

In `tests/test_overlay_card.py` the fake rasterizers gain `render_template` and the module loads a Look:

```python
from splitsmith.looks import load_look

LOOK = load_look("splitsmith")


class _FakeRasterizer:
    def __init__(self, *, fill: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.template_calls: list[tuple[Path, dict, int, int]] = []
        self._fill = fill

    def png(self, html: str, *, width: int, height: int) -> bytes:
        self.calls.append((html, width, height))
        return self._blank(width, height)

    def render_template(self, template: Path, *, context, width: int, height: int) -> bytes:
        self.template_calls.append((template, context.model_dump(), width, height))
        return self._blank(width, height)

    def _blank(self, width: int, height: int) -> bytes:
        buf = io.BytesIO()
        Image.new("RGBA", (width, height), self._fill).save(buf, format="PNG")
        return buf.getvalue()


class _BoomRasterizer:
    def png(self, html: str, *, width: int, height: int) -> bytes:
        raise RuntimeError("rasterize boom")

    def render_template(self, template: Path, *, context, width: int, height: int) -> bytes:
        raise RuntimeError("template boom")
```

Every existing `build_card_still(... theme=THEME ...)` call in that file becomes `build_card_still(..., slot="slate", fps=30, look=LOOK, ...)` (`slot="title_page"` for a `MatchTitle`), and `build_lower_third(..., theme=THEME, ...)` becomes `build_lower_third(..., fps=30, look=LOOK, ...)`. Add:

```python
def test_a_card_renders_through_the_looks_template_for_its_slot(tmp_path: Path) -> None:
    r = _FakeRasterizer()
    image = overlay_card.build_card_still(
        TitleCard(text="Stage 3", duration_seconds=1.5, info=("24 rounds",)),
        slot="slate",
        width=640,
        height=360,
        fps=30,
        look=LOOK,
        rasterizer=r,
        backdrop=_frame(tmp_path),
    )
    assert image is not None and image.size == (640, 360)
    assert r.calls == [], "no card goes through the raw png path any more"
    (template, context, w, h), = r.template_calls
    assert template == LOOK.own_template("slate")
    assert (w, h) == (640, 360)
    assert context["data"]["card"] == {"slot": "slate", "text": "Stage 3", "info": ["24 rounds"], "duration_seconds": 1.5}
    assert context["data"]["groups"][0]["elements"][0]["text"] == "Stage 3"
    assert context["size"] == {"width": 640, "height": 360}
    assert context["fps"] == 30
    assert context["theme"]["ink"] == "#f4f4f5"
    assert "html, body" in context["engine"]["css"]
    assert context["assets"]["shared"].endswith("/_shared")


def test_the_clean_look_falls_back_to_the_default_template_with_its_own_colours(tmp_path: Path) -> None:
    r = _FakeRasterizer()
    overlay_card.build_card_still(
        MatchTitle(text="x"), slot="closing", width=64, height=32, fps=30, look=load_look("clean"),
        rasterizer=r, backdrop=None,
    )
    (template, context, _, _), = r.template_calls
    assert template == LOOK.own_template("closing")
    assert context["theme"]["ink"] == "#ffffff"


def test_a_lower_third_uses_the_lower_third_slot_and_stays_transparent() -> None:
    r = _FakeRasterizer()
    image = overlay_card.build_lower_third(
        TitleCard(text="Stage 3", duration_seconds=2.0, style="lower-third"),
        width=64, height=32, fps=30, look=LOOK, rasterizer=r,
    )
    assert image is not None and image.mode == "RGBA"
    assert r.template_calls[0][1]["data"]["card"]["slot"] == "lower_third"


def test_a_template_that_raises_skips_the_card(tmp_path: Path, caplog) -> None:
    """A user's broken card.html costs the card, never the render."""
    image = overlay_card.build_card_still(
        MatchTitle(text="x"), slot="title_page", width=64, height=32, fps=30, look=LOOK,
        rasterizer=_BoomRasterizer(), backdrop=_frame(tmp_path),
    )
    assert image is None
    assert "template boom" in caplog.text
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_overlay_card.py -n0 -q`
Expected: FAIL with `TypeError` on the new keyword arguments.

- [ ] **Step 3: Rewrite the rasterizing half of `overlay_card.py`**

Replace the imports and `_rasterize`, `build_card_still`, `build_lower_third`:

```python
from .look_template import TemplateContext, engine_block, group_json, shared_url, theme_tokens
from .looks import CardSlot, Look, template_for
from .overlay_html import single_css
from .overlay_theme import OverlayTheme, theme_for


def card_context(
    card: Card, *, slot: CardSlot, width: int, height: int, fps: float, theme: OverlayTheme
) -> TemplateContext:
    """What the template for ``slot`` receives: the card as data
    (``data.card``), the engine's default declaration of it
    (``data.groups``, from :func:`card_groups`), the palette, the canvas,
    and the engine block a shipped template draws with."""
    scale = card_scale(height)
    return TemplateContext(
        theme=theme_tokens(theme),
        data={
            "card": {
                "slot": slot,
                "text": card.text,
                "info": list(card.info),
                "duration_seconds": card.duration_seconds,
            },
            "groups": [group_json(g) for g in card_groups(card)],
        },
        size={"width": width, "height": height},
        fps=fps,
        engine=engine_block(css=single_css(width=width, height=height, scale=scale, theme=theme)),
        assets={"shared": shared_url()},
    )


def _rasterize(
    card: Card, *, slot: CardSlot, width: int, height: int, fps: float, look: Look, rasterizer: Rasterizer
) -> Image.Image | None:
    theme = theme_for(look)
    template = template_for(look, slot)
    context = card_context(card, slot=slot, width=width, height=height, fps=fps, theme=theme)
    try:
        png_bytes = rasterizer.render_template(template, context=context, width=width, height=height)
        with Image.open(io.BytesIO(png_bytes)) as rendered:
            return rendered.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 -- one bad rasterization must not lose the render
        logger.warning("could not rasterize the card %r through %s (%s); it is skipped", card.text, template, exc)
        return None


def build_card_still(
    card: Card,
    *,
    slot: CardSlot,
    width: int,
    height: int,
    fps: float,
    look: Look,
    rasterizer: Rasterizer,
    backdrop: Path | None,
    blur_radius: int | None = None,
    dim: float = DEFAULT_DIM,
) -> Image.Image | None:
    text = _rasterize(card, slot=slot, width=width, height=height, fps=fps, look=look, rasterizer=rasterizer)
    if text is None:
        return None
    canvas: Image.Image | None = None
    if backdrop is not None:
        canvas = backdrop_from_frame(backdrop, width=width, height=height, radius=blur_radius, dim_amount=dim)
    if canvas is None:
        canvas = Image.new("RGB", (width, height), theme_for(look).surface)
    composed = canvas.convert("RGBA")
    composed.alpha_composite(text)
    return composed.convert("RGB")


def build_lower_third(
    card: TitleCard, *, width: int, height: int, fps: float, look: Look, rasterizer: Rasterizer
) -> Image.Image | None:
    return _rasterize(
        card, slot="lower_third", width=width, height=height, fps=fps, look=look, rasterizer=rasterizer
    )
```

Keep both functions' docstrings, adding one sentence to `build_card_still`: "``slot`` names the Look template that draws it (a ``MatchTitle`` is ``title_page`` or ``closing``; a slate ``TitleCard`` is ``slate``)." Drop the now-unused `single_html` import. Update the module docstring's first paragraph: the card is declared as groups, handed to the Look's template for its slot through :mod:`look_template`, rasterized by ``Rasterizer.render_template``.

- [ ] **Step 4: Wire the callers**

`src/splitsmith/mp4_render.py` in `_render_with_work_dir`: after `sequence = composition.sequence` add

```python
    look = load_look(overlay_theme) if timeline.needs_rasterizer else None
    theme = theme_for(look) if look is not None else None
    fps = sequence.frame_rate_num / sequence.frame_rate_den
```

(`from .looks import load_look`, `from .overlay_theme import ThemeName, theme_for`; drop `load_theme` if nothing else in the module uses it). The lower-third call becomes `build_lower_third(item.lower_third, width=..., height=..., fps=fps, look=look, rasterizer=rasterizer)` guarded on `look is not None`; the still call becomes `build_card_still(item.card, slot=item.kind, width=..., height=..., fps=fps, look=look, rasterizer=rasterizer, backdrop=backdrop)`. `item.kind` is already `"title_page" | "slate" | "closing"` for a `_StillItem`; if mypy complains about `ItemKind` versus `CardSlot`, add `slot: CardSlot` to `_StillItem` set where the items are planned. The summary call keeps `theme=theme if theme is not None else load_theme(overlay_theme)` (summary is not a template in this slice).

`src/splitsmith/compare/mp4_grid.py`: the function at line 2236 takes `look: Look | None` instead of `theme: OverlayTheme | None` and a `slot: CardSlot`; its callers (grep `_card_segment(` or whatever the function is named at 2236) pass the slot by the card they build (`"title_page"`, `"slate"`, `"closing"`). Its `build_card_still` call gains `slot=slot, fps=<the grid's sequence fps, already in scope as the canvas rate>, look=look`. The lower-third site (grep `build_lower_third(` in the file) gains `fps=` and `look=`. Where the module does `card_theme = load_theme(overlay_theme) if cards_requested else None` (line 2611), load the Look instead and derive the theme for the free cell and the clock: `card_look = load_look(overlay_theme) if cards_requested else None`, `card_theme = theme_for(card_look) if card_look else None`.

`src/splitsmith/export_preview.py`: `render_preview` takes `look: Look` in place of `theme: OverlayTheme`; derive `theme = theme_for(look)` at the top for `_compose_over` and the summary; `size = {"width": spec.width, "height": spec.height, "fps": 30.0, "look": look}`; the three card branches pass `slot="title_page"` / `"closing"` (by `spec.card`), `slot="slate"`, and the lower third as is. `src/splitsmith/ui/export_preview_api.py:120` passes `look=load_look("splitsmith")`. Check `tests/test_export_preview.py` and `tests/test_export_preview_api.py` for the keyword and the fakes.

`scripts/render_look_thumbnails.py`: `build_thumbnails(out, *, rasterizer, look: Look)`; `card = {..., "fps": 30.0, "look": look}` with `slot=` per call; `_overlay(..., theme=theme_for(look))`; `main` loads `load_look(args.theme)`. `tests/test_render_look_thumbnails.py` follows.

- [ ] **Step 5: Give every fake rasterizer that builds cards a `render_template`**

```bash
rg -n "def png\(self" tests scripts
```

For each fake in a file whose tests reach `build_card_still` or `build_lower_third` (`tests/test_mp4_render.py`, `tests/test_compare_mp4_grid_cards.py`, `tests/test_export_preview.py`, `tests/test_export_preview_api.py`, `tests/test_render_look_thumbnails.py`), add the same method the card test's fake has, returning a blank RGBA PNG of the requested size. A fake that only serves the summary or the live overlay keeps `png` alone.

- [ ] **Step 6: Run the suites that touch cards**

Run: `uv run pytest tests/test_overlay_card.py tests/test_mp4_render.py tests/test_compare_mp4_grid_cards.py tests/test_compare_mp4_grid_render.py tests/test_export_preview.py tests/test_export_preview_api.py tests/test_render_look_thumbnails.py tests/test_ui_match_exports.py tests/test_compare_cli_mp4.py -n0 -q`
Expected: all PASS.

Run: `uv run mypy src/splitsmith/overlay_card.py src/splitsmith/mp4_render.py src/splitsmith/compare/mp4_grid.py src/splitsmith/export_preview.py src/splitsmith/looks.py src/splitsmith/look_template.py` (or the project's configured type check command from `pyproject.toml` / CI).
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/overlay_card.py src/splitsmith/mp4_render.py src/splitsmith/compare/mp4_grid.py src/splitsmith/export_preview.py src/splitsmith/ui/export_preview_api.py scripts/render_look_thumbnails.py tests
git commit -m "feat(looks): every generated card renders through its Look's template (#1241)"
```

---

### Task 6: Pixel identity against main, and the docs

**Files:**
- Modify: `CLAUDE.md` (a short "Looks" paragraph under "Rendered cards and stage summaries")
- Modify: `SPEC.md` ("Module responsibilities": `looks.py`, `look_template.py`)
- No new committed script; the comparison runs from the scratch directory.

- [ ] **Step 1: Render the reference frames from main**

```bash
git stash -u 2>/dev/null; git worktree add ~/.claude-tmp/looks-main main
cd ~/.claude-tmp/looks-main && uv sync -q && \
  uv run python scripts/render_match_frames.py --out ~/.claude-tmp/looks-frames/main-slate && \
  uv run python scripts/render_match_frames.py --titles lower-third --out ~/.claude-tmp/looks-frames/main-lt && \
  uv run python scripts/render_match_frames.py --theme clean --out ~/.claude-tmp/looks-frames/main-clean && \
  uv run python scripts/render_grid_frames.py --title-page --closing-card --out ~/.claude-tmp/looks-frames/main-grid
cd - && git worktree remove ~/.claude-tmp/looks-main
```

(Check `render_grid_frames.py --help` for its `--out` flag name before running.)

- [ ] **Step 2: Render the same frames from the branch and diff**

```bash
uv run python scripts/render_match_frames.py --out ~/.claude-tmp/looks-frames/branch-slate && \
uv run python scripts/render_match_frames.py --titles lower-third --out ~/.claude-tmp/looks-frames/branch-lt && \
uv run python scripts/render_match_frames.py --theme clean --out ~/.claude-tmp/looks-frames/branch-clean && \
uv run python scripts/render_grid_frames.py --title-page --closing-card --out ~/.claude-tmp/looks-frames/branch-grid
cat > ~/.claude-tmp/looks-frames/diff.py <<'EOF'
import sys
from pathlib import Path
from PIL import Image, ImageChops
a, b = Path(sys.argv[1]), Path(sys.argv[2])
bad = 0
for pa in sorted(a.glob("*.png")):
    pb = b / pa.name
    if not pb.exists():
        print("missing", pb); bad += 1; continue
    diff = ImageChops.difference(Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB"))
    box = diff.getbbox()
    print(pa.name, "identical" if box is None else f"DIFFERS {box}")
    bad += box is not None
sys.exit(1 if bad else 0)
EOF
for pair in slate lt clean grid; do uv run python ~/.claude-tmp/looks-frames/diff.py ~/.claude-tmp/looks-frames/main-$pair ~/.claude-tmp/looks-frames/branch-$pair || exit 1; done
```

Expected: every card frame (`title-page`, `card-N`, `closing`, the lower-third head frames) prints `identical`. Stage frames are untouched by this slice and must also be identical. A difference is a defect in `cell.js` or `single_css`: open both PNGs, find the element, fix the port, rerun. Do not relax to a tolerance.

- [ ] **Step 3: Docs**

In `CLAUDE.md`, under "Rendered cards and stage summaries", add:

```
Cards draw through a **Look** (``splitsmith.looks``, spec 2026-10-06):
``data/looks/<name>/look.json`` holds the palette and names one HTML
template per card slot; ``~/.splitsmith/looks/<name>/`` shadows a shipped
one. ``overlay_theme.load_theme`` reads the manifest, so a palette change
is a manifest change. The shipped ``card.html`` draws the engine's own
markup through ``_shared/cell.js`` (a port of ``overlay_html._cell_div``,
held to it by ``tests/test_look_template.py``) and ``_shared/fit.js``
(the one fit policy, also inlined by ``overlay_html``); a template gets
``window.splitsmith`` from ``look_template.TemplateContext`` before its
scripts run. The summary and the live overlay are not templates yet.
Check pixels with the frame scripts, not argv.
```

In `SPEC.md`'s module list add `looks.py` (Look directory loader, pure) and `look_template.py` (the template contract) in one line each beside `overlay_card.py`.

- [ ] **Step 4: Full suite, lint, commit**

Run: `uv run pytest -q` (if the host Python crash noted in memory recurs, run the overlay, mp4, compare, export and ui test files by name instead and say so in the PR).
Run: `uv run ruff check . && uv run black --check src tests scripts`.

```bash
git add CLAUDE.md SPEC.md
git commit -m "docs(looks): where cards draw from now (#1241)"
```

Open the PR against main with the frame-diff output pasted in the description, and the four `main-*` / `branch-*` directories' `title-page.png` and `card-1.png` attached side by side. Closes #1241.
