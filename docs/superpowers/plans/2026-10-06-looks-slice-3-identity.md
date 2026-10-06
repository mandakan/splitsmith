# Looks slice 3: per-shooter identity

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A shooter carries an identity (accent colour, logo, club line) on their project; it syncs like the rest of the project; every card template and the stage summary read it; the Footage page edits it. A shooter without one renders exactly as today.

**Architecture:** `splitsmith.identity` is the pure model and resolver: `ShooterIdentity` lives on `MatchProject` (already a synced `project` doc), the logo file under `<shooter>/identity/` travels over the sync media channel like a trim, and `resolve_identity` turns a project plus a Look into what a template draws (accent from the shooter, else the Look's accent series by slot index; logo path, else the match logo). The IR gains `Composition.shooters`; both renderers hand `data.shooters` to templates through `card_context`, and the grid passes each cell's accent to `grid_html` as a CSS custom property that falls back to the theme when unset, so the no-identity output is byte-identical. The shipped templates draw the logo through one shared `_shared/identity.js`. A small `IdentitySheet` on the Footage page edits it over two routes.

**Tech Stack:** Python 3.11+, Pydantic, FastAPI (multipart upload), Playwright, PIL; React + vitest for the sheet.

**Spec:** `docs/superpowers/specs/2026-10-06-rendered-video-first-design.md`, section 2. Issue #1243, epic #1240. Builds on slices 1 (#1250) and 2 (#1253).

## Global Constraints

- `uv` for everything, never `pip`; Black 110, Ruff clean; pnpm for the SPA (`pnpm --dir src/splitsmith/ui_static test`, `typecheck`, `lint`). No new dependency.
- A shooter without an identity, and a match without a logo, render pixel-identical to main (the frame scripts, with the project's static ffmpeg first on PATH: `desktop/build/bin`). The identity only changes output when set.
- `identity` is a field on `MatchProject` (the `project` doc kind), never a new `doc_kind`: the sync allowlists do not move.
- The logo is a raster file (`png`, `jpg`, `jpeg`, `webp`), at most 2 MB, named by its content hash (`logo-<sha256[:12]>.<ext>`) under `<shooter>/identity/`, so a replaced logo is a new key and the old one is gc'd by the push like a stale rendition. SVG is refused: a template loads it in Chromium by `file://` and SVG can script.
- Hosted mode: the upload goes to storage under `matches/<match_id>/shooters/<slug>/identity/<name>`; a hosted render mirrors it to local disk the way `_try_pull_trim_from_storage` mirrors a trim; the desktop push sends it; the pull side carries docs only (unchanged), so a mirror that never uploaded the logo renders without it.
- A render never fails because of an identity: an unreadable or missing logo file logs and draws without it.
- The accent bar and the accent on the identity row appear only on the stage summary (grid cells and the single-shooter hold); the live sprites keep their look (ledger it; the sprites are #1249's cosmetic follow-up).

## Review Focus

1. A logo file that the project names but that is missing on disk (or unreadable) must not fail the render or the preview: the card draws without it. Test in Task 3 (`test_a_missing_logo_file_draws_the_card_without_it`).
2. A grid with two shooters where only one has an accent must tint only that cell; the other cell's markup is byte-identical to today's. Test in Task 4 (`test_only_the_cell_with_an_accent_carries_the_variable`).
3. The identity PATCH must reject an accent that is not `#rrggbb` and a club line over 60 characters with a 422, never write a half-valid identity. Test in Task 5.
4. A logo upload must refuse SVG and anything over 2 MB with a 422 and leave no file behind. Test in Task 5.
5. The push plan must send `identity/` files and the delete route must accept their keys, so a replaced logo is gc'd like a stale rendition. Tests in Task 2.

---

### Task 1: The identity model and resolver, the Look's accent series, the IR

**Files:**
- Create: `src/splitsmith/identity.py`
- Modify: `src/splitsmith/match_project.py` (`MatchProject.identity`), `src/splitsmith/looks.py` (`LookManifest.accent_series`), `src/splitsmith/data/looks/splitsmith/look.json`, `src/splitsmith/data/looks/clean/look.json`, `src/splitsmith/composition.py` (`Composition.shooters`)
- Test: `tests/test_identity.py`, `tests/test_looks.py`

**Interfaces:**
- Produces:
  - `identity.ShooterIdentity(BaseModel)`: `accent: str | None`, `logo: str | None`, `club: str | None` with validators (`^#[0-9a-fA-F]{6}$`; `^logo-[0-9a-f]{12}\.(png|jpg|jpeg|webp)$`; club stripped, 1..60 chars, blank -> None).
  - `identity.ResolvedIdentity` (frozen dataclass): `label: str`, `accent: str`, `logo_path: Path | None`, `club: str | None`.
  - `identity.LOGO_DIR = "identity"`, `identity.LOGO_EXTENSIONS = ("png", "jpg", "jpeg", "webp")`, `identity.LOGO_MAX_BYTES = 2 * 1024 * 1024`.
  - `identity.logo_name(data: bytes, ext: str) -> str`.
  - `identity.resolve_identity(*, label, identity: ShooterIdentity | None, index: int, look: Look, shooter_root: Path | None, match_logo: Path | None) -> ResolvedIdentity`: accent = `identity.accent` else `look.accent_series[index % len]` else the Look's `accent` token as hex; `logo_path = shooter_root / LOGO_DIR / identity.logo` when set (existence is the renderer's problem), else `match_logo`.
  - `LookManifest.accent_series: list[str] = []` (each `#rrggbb`); `Look.accent_series` property; shipped `splitsmith`: `["#ff2d2d", "#fbbf24", "#4ade80", "#60a5fa", "#c084fc", "#f472b6"]`; `clean`: `["#ff2d2d", "#ffdc50", "#2ecc71", "#4da3ff", "#b17aff", "#ff79c6"]`.
  - `composition.CompositionShooter` (frozen dataclass): `label`, `accent`, `logo_path: Path | None`, `club: str | None`; `Composition.shooters: tuple[CompositionShooter, ...] = ()`.
  - `MatchProject.identity: ShooterIdentity = Field(default_factory=ShooterIdentity)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_identity.py`:

```python
"""Per-shooter identity: the model, its shape rules, and how it resolves against a Look."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from splitsmith import identity, looks


def test_identity_defaults_to_nothing() -> None:
    assert identity.ShooterIdentity() == identity.ShooterIdentity(accent=None, logo=None, club=None)


@pytest.mark.parametrize("bad", ["red", "#fff", "#GGGGGG", "ff2d2d", "#ff2d2d00"])
def test_accent_must_be_six_hex_digits(bad: str) -> None:
    with pytest.raises(ValidationError):
        identity.ShooterIdentity(accent=bad)


def test_accent_is_normalised_to_lower_case() -> None:
    assert identity.ShooterIdentity(accent="#FF2D2D").accent == "#ff2d2d"


@pytest.mark.parametrize("bad", ["logo.png", "logo-abc.png", "logo-0123456789ab.svg", "../logo-0123456789ab.png"])
def test_logo_is_a_hash_named_raster_file(bad: str) -> None:
    with pytest.raises(ValidationError):
        identity.ShooterIdentity(logo=bad)


def test_club_is_stripped_bounded_and_blank_is_none() -> None:
    assert identity.ShooterIdentity(club="  Bromma PK  ").club == "Bromma PK"
    assert identity.ShooterIdentity(club="   ").club is None
    with pytest.raises(ValidationError):
        identity.ShooterIdentity(club="x" * 61)


def test_logo_name_is_content_addressed() -> None:
    assert identity.logo_name(b"abc", "png") == identity.logo_name(b"abc", "png")
    assert identity.logo_name(b"abc", "png") != identity.logo_name(b"abd", "png")
    assert identity.logo_name(b"abc", "jpeg").endswith(".jpeg")
    assert identity.ShooterIdentity(logo=identity.logo_name(b"abc", "webp"))


def test_resolve_takes_the_shooters_accent_else_the_looks_series_by_slot(tmp_path: Path) -> None:
    look = looks.load_look("splitsmith")
    own = identity.resolve_identity(
        label="A", identity=identity.ShooterIdentity(accent="#123456"), index=0, look=look,
        shooter_root=tmp_path, match_logo=None,
    )
    assert own.accent == "#123456"
    series = look.accent_series
    for index in (0, 1, len(series)):
        resolved = identity.resolve_identity(
            label="B", identity=None, index=index, look=look, shooter_root=tmp_path, match_logo=None
        )
        assert resolved.accent == series[index % len(series)]


def test_resolve_uses_the_shooters_logo_else_the_match_logo(tmp_path: Path) -> None:
    look = looks.load_look("splitsmith")
    name = identity.logo_name(b"x", "png")
    mine = identity.resolve_identity(
        label="A", identity=identity.ShooterIdentity(logo=name, club="Bromma PK"), index=0, look=look,
        shooter_root=tmp_path, match_logo=tmp_path / "match.png",
    )
    assert mine.logo_path == tmp_path / identity.LOGO_DIR / name and mine.club == "Bromma PK"
    theirs = identity.resolve_identity(
        label="A", identity=None, index=0, look=look, shooter_root=tmp_path, match_logo=tmp_path / "match.png"
    )
    assert theirs.logo_path == tmp_path / "match.png" and theirs.club is None


def test_a_look_without_a_series_falls_back_to_its_accent_token(user_dir_fixture=None) -> None:
    from splitsmith.look_template import theme_tokens
    from splitsmith.overlay_theme import load_theme

    look = looks.load_look("clean")
    stripped = look.model_copy(update={"manifest": look.manifest.model_copy(update={"accent_series": []})})
    resolved = identity.resolve_identity(label="A", identity=None, index=3, look=stripped, shooter_root=None, match_logo=None)
    assert resolved.accent == theme_tokens(load_theme("clean"))["accent"]
```

In `tests/test_looks.py`:

```python
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_identity.py tests/test_looks.py -n0 -q -p no:cacheprovider`
Expected: FAIL (`splitsmith.identity` missing; `accent_series` unknown).

- [ ] **Step 3: Write `src/splitsmith/identity.py`**

```python
"""Per-shooter identity (spec 2026-10-06, section 2): an accent colour,
a logo and a club line, on the shooter's project. A Look is match-level;
identity is what tells one shooter's tile, lower third and roster row
from another's. ``resolve_identity`` is where the defaults live, so a
shooter who never set one renders with the Look's own accent series and
the match's logo and nothing is ever required of them.

Pure: no file is opened here. The logo is a path the renderer reads.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from .looks import Look
from .overlay_theme import theme_for

LOGO_DIR = "identity"
LOGO_EXTENSIONS: tuple[str, ...] = ("png", "jpg", "jpeg", "webp")
LOGO_MAX_BYTES = 2 * 1024 * 1024
CLUB_MAX_CHARS = 60

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOGO_RE = re.compile(r"^logo-[0-9a-f]{12}\.(?:png|jpg|jpeg|webp)$")


class ShooterIdentity(BaseModel):
    """What a shooter chose. Every field optional; ``None`` means "the
    Look's default", never "blank"."""

    model_config = ConfigDict(extra="ignore")

    accent: str | None = None
    logo: str | None = None
    club: str | None = None

    @field_validator("accent")
    @classmethod
    def _accent_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _HEX_RE.match(value):
            raise ValueError("accent must be a #rrggbb colour")
        return value.lower()

    @field_validator("logo")
    @classmethod
    def _logo_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _LOGO_RE.match(value):
            raise ValueError("logo must be a content-named raster file (logo-<hash>.png|jpg|jpeg|webp)")
        return value

    @field_validator("club")
    @classmethod
    def _club_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) > CLUB_MAX_CHARS:
            raise ValueError(f"club line is at most {CLUB_MAX_CHARS} characters")
        return stripped


def logo_name(data: bytes, ext: str) -> str:
    """The content-addressed file name a logo is stored under, so a
    replaced logo is a new key and the old one can be swept."""
    return f"logo-{hashlib.sha256(data).hexdigest()[:12]}.{ext.lower()}"


@dataclass(frozen=True)
class ResolvedIdentity:
    """What a template draws for one shooter, defaults applied."""

    label: str
    accent: str
    logo_path: Path | None
    club: str | None


def _hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def resolve_identity(
    *,
    label: str,
    identity: ShooterIdentity | None,
    index: int,
    look: Look,
    shooter_root: Path | None,
    match_logo: Path | None,
) -> ResolvedIdentity:
    """The shooter's choices over the Look's defaults: their accent, else
    the Look's accent series by slot ``index`` (wrapping), else the Look's
    ``accent`` token; their logo under ``shooter_root/identity/``, else
    ``match_logo``; their club line or nothing."""
    chosen = identity or ShooterIdentity()
    if chosen.accent is not None:
        accent = chosen.accent
    elif look.accent_series:
        accent = look.accent_series[index % len(look.accent_series)]
    else:
        accent = _hex(theme_for(look).accent)
    logo_path: Path | None = match_logo
    if chosen.logo is not None and shooter_root is not None:
        logo_path = shooter_root / LOGO_DIR / chosen.logo
    return ResolvedIdentity(label=label, accent=accent, logo_path=logo_path, club=chosen.club)


__all__ = [
    "CLUB_MAX_CHARS",
    "LOGO_DIR",
    "LOGO_EXTENSIONS",
    "LOGO_MAX_BYTES",
    "ResolvedIdentity",
    "ShooterIdentity",
    "logo_name",
    "resolve_identity",
]
```

`looks.py`: `LookManifest.accent_series: list[str] = []` with a validator that each entry matches `^#[0-9a-fA-F]{6}$` (lower-cased); `Look.accent_series` property returning `tuple(self.manifest.accent_series)`. The two manifests gain the series above; run `scripts/build_overlay_theme.py` so the file stays canonical (the script keeps unknown keys).

`match_project.py`: `identity: ShooterIdentity = Field(default_factory=ShooterIdentity)` after `competitor_division`, importing `ShooterIdentity` from `.identity` (check for an import cycle: `identity` imports `looks` and `overlay_theme`, neither imports `match_project`).

`composition.py`: `CompositionShooter` and `Composition.shooters`; `from_stage_compositions(..., shooters: Sequence[CompositionShooter] = ())`.

- [ ] **Step 4: Run, lint, commit**

Run: `uv run pytest tests/test_identity.py tests/test_looks.py tests/test_match_project.py -n0 -q -p no:cacheprovider` (pick the match project test file by name; if several exist, run `tests/ -k "match_project" -n0 -q`).
Expected: PASS.

```bash
git add src/splitsmith/identity.py src/splitsmith/match_project.py src/splitsmith/looks.py src/splitsmith/composition.py src/splitsmith/data/looks tests/test_identity.py tests/test_looks.py
git commit -m "feat(identity): the per-shooter identity model, the Look accent series, Composition.shooters (#1243)"
```

---

### Task 2: The logo travels: push plan, hosted key rule, delete route, hosted mirror-down

**Files:**
- Modify: `src/splitsmith/sync/plan.py` (enumerate `identity/`), `src/splitsmith/sync/push.py` (`_MEDIA_KEY_LOCAL_RE` subdir), `src/splitsmith/ui/sync_api.py` (`_SYNC_MEDIA_KEY_RE`, `delete_media`)
- Create: `src/splitsmith/ui/identity_media.py` (`ensure_local_logo`)
- Test: `tests/test_sync_plan.py` (or where `build_push_plan` is tested; find with `rg "build_push_plan" tests`), `tests/test_sync_api.py`, `tests/test_identity_media.py`

**Interfaces:**
- Produces: `identity_media.ensure_local_logo(project: MatchProject, shooter_root: Path) -> Path | None`: the local file when present, else mirrored down from `project._storage` under the storage key `matches/<match_id>/shooters/<slug>/identity/<name>` (the same shape the push writes), else `None`; never raises for a storage hiccup (logs). `identity_media.logo_storage_key(match_id, slug, name) -> str`.

- [ ] **Step 1: Write the failing tests**

Push plan: a shooter root with `identity/logo-<hash>.png` is planned as media with `remote_key = matches/<id>/shooters/<slug>/identity/<name>`; a stray `identity/notes.txt` is not. Push gc: `_local_media_path` maps an identity key back to `<shooter>/identity/<name>` (so a removed logo's remote object is a gc candidate, mirroring the rendition rule: find the gc test for `beep_review` in `tests/test_sync_push.py` and add the identity case beside it). Hosted key gate (`tests/test_sync_api.py`): `matches/m/shooters/alice/identity/logo-0123456789ab.png` is admitted, `.../identity/logo.svg` and `.../identity/x.txt` are refused; `delete_media` accepts an identity key. `ensure_local_logo`: returns the local file when it exists; with a fake storage (`exists`/`download` doubles, the way `tests/test_ui_audio*.py` fake `_storage`) mirrors it down and returns the path; returns `None` and logs when storage raises.

- [ ] **Step 2: Run to verify they fail; Step 3: implement**

`plan.py` after the `beep_review` block:

```python
        identity_dir = shooter_root / LOGO_DIR
        if identity_dir.is_dir():
            for artifact in sorted(identity_dir.iterdir()):
                if not _LOGO_FILE_RE.match(artifact.name):
                    continue
                remote_key = _remote_key(match.match_id, slug, artifact.name, subdir=LOGO_DIR)
                item = _plan_media_item(artifact, remote_key, sync_state)
                if item is None:
                    media_skipped += 1
                else:
                    media.append(item)
```

with `_LOGO_FILE_RE = re.compile(r"^logo-[0-9a-f]{12}\.(?:png|jpe?g|webp)$")` and `from ..identity import LOGO_DIR`. `push.py`: `(?P<subdir>trimmed|beep_review|identity)`. `sync_api.py`: the regex gains `|identity/logo-[0-9a-f]{12}\.(?:png|jpe?g|webp)`; `delete_media` admits a key with `/identity/` (a replaced logo). `identity_media.ensure_local_logo`: local path `shooter_root / LOGO_DIR / project.identity.logo`; if missing and `getattr(project, "_storage", None)` is set, `storage.exists(key)` then `storage.download(key, path)` (match the method names `_try_pull_trim_from_storage` uses) inside try/except logging a warning.

- [ ] **Step 4: Run, lint, commit**

```bash
git commit -m "feat(identity): the logo syncs like a trim: push plan, hosted key rule, delete, mirror-down (#1243)"
```

---

### Task 3: Templates read `data.shooters`; the shipped cards draw the logo

**Files:**
- Modify: `src/splitsmith/look_template.py` (`shooter_json`), `src/splitsmith/overlay_card.py` (`card_context(..., shooters=())`, `card_motion(..., shooters)`, `build_card_still(..., shooters)`, `build_lower_third(..., shooters)`)
- Create: `src/splitsmith/data/looks/_shared/identity.js`
- Modify: `src/splitsmith/data/looks/splitsmith/card.html`, `card-rise.html`
- Modify: `src/splitsmith/mp4_render.py`, `src/splitsmith/compare/mp4_grid.py` (pass the composition's shooters / the grid's resolved identities), `src/splitsmith/ui/match_exports.py` (`MatchExportRequestData.shooter_identity: ResolvedIdentity | None`; `Composition.shooters` from it), `src/splitsmith/compare/project_loader.py` or `mp4_grid.render_grid_mp4(identities=...)`
- Test: `tests/test_look_template.py`, `tests/test_overlay_card.py`, `tests/test_mp4_render.py`, `tests/test_compare_mp4_grid_cards.py`, `tests/test_ui_match_exports.py`

**Interfaces:**
- `look_template.shooter_json(shooter: ResolvedIdentity) -> dict`: `{"label", "accent", "club", "logo": file URL or None}`; the URL is `path.resolve().as_uri()` only when the file exists and is under 2 MB, else `None` with a warning (Review Focus 1).
- `card_context(card, *, slot, width, height, fps, theme, shooters: Sequence[ResolvedIdentity] = ())` puts `"shooters": [shooter_json(s) for s in shooters]` in `data`.
- `card_motion` / `build_card_still` / `build_lower_third` gain `shooters: Sequence[ResolvedIdentity] = ()`.
- `Composition.shooters` -> `mp4_render` resolves nothing (the caller resolved); it passes `composition.shooters` mapped to `ResolvedIdentity` (same fields) to every card. `render_grid_mp4(..., identities: Mapping[str, ResolvedIdentity] = {})` keyed by tile label; a card gets the tiles' identities in slot order; a lower third gets them too (the grid's lower third is the stage's, not one shooter's: templates draw no logo for a multi-shooter lower third unless exactly one shooter).
- In the page: `engine.mountIdentity(shooters, slot)` (from `identity.js`) appends, outside `.cell`, a `<div class="identity-logos">` with one `<img>` per shooter that has a `logo`, sized to 12% of the canvas height, top-right for full-frame cards, left of the plate for a lower third; when no shooter has a logo it appends nothing (the DOM parity test stays exact).

- [ ] **Step 1: Write the failing tests** (unit): `shooter_json` returns a file URL for an existing small PNG and `None` plus a log line for a missing one; `card_context` carries `data.shooters`; `test_a_missing_logo_file_draws_the_card_without_it` through `build_card_still` with the fake rasterizer (context has `logo: None`, the call succeeds). Integration (`tests/test_look_template.py`): render the shipped `card.html` with one shooter whose logo is a 64x64 red PNG in `tmp_path`: the alpha bbox of the top-right region is non-empty and the frame differs from the no-shooter render; with no logo the two renders are byte-identical (the parity guard).

- [ ] **Step 2: RED; Step 3: implement; Step 4: GREEN, lint, commit**

```bash
git commit -m "feat(identity): templates read data.shooters; the shipped cards draw the logo (#1243)"
```

---

### Task 4: The summary's accent: grid cells and the single-shooter hold

**Files:**
- Modify: `src/splitsmith/compare/overlay_sprites.py` (`TilePlacement.accent: str | None = None`), `src/splitsmith/overlay_html.py` (`grid_html` writes `--accent` on a cell wrapper when set; `_style_rules` reads `var(--accent, <theme>)` for the identity row colour and a top bar; `single_html(..., accent=None)`), `src/splitsmith/overlay_summary_cell.py` (`build_summary_still(..., accent=None)`), `src/splitsmith/compare/overlay_summary.py` (placements carry the accent), `src/splitsmith/mp4_render.py` (hold passes the shooter's accent)
- Test: `tests/test_overlay_html.py`, `tests/test_compare_mp4_grid_hold.py`, `tests/test_overlay_summary_cell.py`

**Rules:** `.cell { box-shadow: inset 0 <bar>px 0 0 var(--accent, transparent); }` with `bar = max(3, scale.pad // 4)` and `.role-identity { color: var(--accent, rgb(<ink>)); }`. With the variable unset both resolve to today's values, so every existing markup and pixel test holds. The grid test (Review Focus 2): two placements, one with an accent: only that wrapper's `style` carries `--accent:#...`; the other wrapper's markup equals the pre-change string. Frames: the default renders stay 33 of 33 identical; a run of `scripts/render_grid_frames.py` with `--identity-demo` (a new flag that assigns the shipped series to the tiles) shows the tinted cells.

```bash
git commit -m "feat(identity): the stage summary carries the shooter's accent (#1243)"
```

---

### Task 5: The routes and the Footage page's identity sheet

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`PATCH /api/shooters/{slug}/identity`, `POST /api/shooters/{slug}/identity/logo` multipart, `DELETE /api/shooters/{slug}/identity/logo`; the shooter list entry gains `identity`), `src/splitsmith/ui_static/src/lib/api.ts` (`ShooterListEntry.identity`, `updateShooterIdentity`, `uploadShooterLogo`, `removeShooterLogo`)
- Create: `src/splitsmith/ui_static/src/components/footage/IdentitySheet.tsx`, `IdentitySheet.test.tsx`
- Modify: `src/splitsmith/ui_static/src/components/footage/ShootersPanel.tsx` (an "Identity…" row-menu item and a 10 px accent dot before the initials when set), `src/splitsmith/ui_static/src/pages/Ingest.tsx` (mount the sheet)
- Test: `tests/test_ui_server.py` (or a new `tests/test_identity_api.py`), vitest

**Rules:** PATCH body `{accent?: string|null, club?: string|null}` validated by `ShooterIdentity`'s own validators (a 422 names the field); it never touches `logo`. The upload reads the whole body (bounded by `LOGO_MAX_BYTES`, 413/422 above), sniffs the type with PIL (`Image.open` + `format in {"PNG", "JPEG", "WEBP"}`; SVG and anything else 422), writes `<shooter>/identity/logo-<hash>.<ext>` locally or to storage hosted, sets `project.identity.logo`, removes the previous file locally (hosted: the push's gc handles the remote), and saves the project under the audit lock discipline the other project writers use (`_audit_rmw()` is for audit docs; project saves go through `state.save_shooter_project` or whatever the compare-camera PATCH uses: copy that). The sheet: eight accent swatches from the shipped series plus a hex input, a logo drop zone with preview and Remove, a club field; `Field` rows inside `Sheet`; one primary button. Phone: nothing.

```bash
git commit -m "feat(identity): identity routes and the Footage page's identity sheet (#1243)"
```

---

### Task 6: Wiring the resolved identity into every render, frames, docs

**Files:**
- Modify: `src/splitsmith/ui/server.py` (match export job and compare grid job resolve identities: `resolve_identity` per shooter with `ensure_local_logo`; the preview API passes the shooter's identity), `src/splitsmith/match_cli.py`, `src/splitsmith/compare/cli.py` (resolve from the loaded projects), `scripts/render_match_frames.py` (`--identity-demo`), `scripts/render_grid_frames.py` (`--identity-demo`), `CLAUDE.md`, `SPEC.md`
- Test: `tests/test_ui_match_exports.py` (the composition carries `shooters`), `tests/test_compare_cli_mp4.py`

**Gate:** default frames 33 of 33 identical to main with the project's ffmpeg; `--identity-demo` frames show the logo on the title page and lower third and the accent bar on the summary cells; publish side by side on Urdr. Full suite green except the known timezone test.

```bash
git commit -m "feat(identity): every render resolves identities; demo frames; docs (#1243)"
```
