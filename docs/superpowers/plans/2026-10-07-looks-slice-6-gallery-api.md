# The Look Gallery Fed from the Looks API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Export page's Look group lets the user pick an installed Look and, per card slot, one of that Look's template variants (and its stings), from `GET /api/looks`; the choice reaches every export request and the rail preview, and round-trips through presets.

**Architecture:** `splitsmith.looks` grows a pure catalog (`look_catalog()`: every installed Look with its slots, variants and the preview file each resolves to, the shipped default's preview standing in for one a Look lacks). `ui/looks_api.py` serves the catalog and the preview files. The request layer stops hard-coding the two palette names: `overlay_theme` is any installed Look name, and three per-slot variant fields (`title_page_variant`, `stage_card_variant`, `closing_card_variant`, each `None` meaning "the `card_variant` knob") reach the IR's `variant` fields through `match_exports` and `compare/cards`. The preview endpoint takes `look` and `variant` and keys its cache on them; an animated template already renders at its poster through `render_template`. In the SPA, `lib/looks.ts` is the pure reader of the catalog (`useLooks()` fetches it once), `ExportSettings` gains `look` and the three variant fields, the gallery registry stays the static table of slots and gains one function that folds a Look's variants in (`slotsForLook`: the Look picker tile row at the head, a Style control under a card slot with more than one variant, the Look's stings as transition tiles with API previews), and every mapper sends only what the selected Look offers. The thumbnail script renders each shipped Look's `preview/` set (and, for the transition tiles, short looping WebP clips of the real xfade through the project ffmpeg, and of each sting through Chromium) at authoring time.

**Tech Stack:** FastAPI, Pydantic, Python 3.12; React 18 + TypeScript, vitest; PIL animated WebP; the project's static ffmpeg and Chromium at authoring time only.

**Spec:** `docs/superpowers/specs/2026-10-06-rendered-video-first-design.md` section 4 (bullets 1 to 4). Issue #1246 (this PR closes it), epic #1240 item 6.

## Global Constraints

- Python 3.11+, type hints, Black 110, Ruff; no new dependencies (PIL writes animated WebP already).
- `ExportPresetBody`: every new field defaults, `extra="ignore"` stays; a body saved before this slice loads with `look="splitsmith"` and every variant `"default"`.
- The export wrappers in `lib/api.ts` keep spreading their payload; `api.exportBodies.test.ts` types each payload `Required<...>` so the new fields fail the typecheck until they reach the wire.
- `lookGallery.test.ts` keeps pinning the per-format visibility table and that every bundled thumbnail is referenced; `renderOptions.test.ts` keeps pinning that the mappers never send a field the registry hides. Both extend to the Look fields.
- Hosted mode serves `GET /api/looks` and the preview files (shipped Looks only, there is no user Looks dir); the preview route's `{file}` parameter is a bare file name inside the Look's `preview/` directory and is listed in `route_scope.HOSTED_CONFINED_ROUTES` with that reason (the scope test fails otherwise).
- A render with every new field at its default is the render of today: same request to the engine, same argv, the frame scripts' default sets stay identical.
- Run pytest and the frame or thumbnail scripts with the project's static ffmpeg first on PATH.

## Review Focus

1. A preset saved with a Look that is no longer installed (a user Look deleted, or a hosted preset from a desktop with a user Look): the page shows the default Look selected, the request carries `splitsmith`, and nothing 422s. Pinned in Task 6 (`visibleLook`) and Task 3 (the validator message names the installed Looks).
2. A stored variant the selected Look lacks (switching Looks, or a preset from another machine): the mapper sends `default`, not the stored name, so the server never warns about a fallback the user did not pick. Pinned in Task 6 (`visibleVariant`).
3. The preview cache: a rise title page and the default title page on the same stage must be two cache entries. Pinned in Task 4 (`preview_key` differs by look and variant).
4. The preview file route: `..`, a nested path, an unknown Look and a file outside `preview/` are all 404, locally and hosted. Pinned in Task 1.
5. The CLI's `--card-variant` still drives every slot (the per-slot request fields are `None` there), and a request with `card_variant="rise"` and `stage_card_variant="default"` renders a default slate and rise match cards. Pinned in Task 3.

---

### Task 1: The Look catalog and its API

**Files:**
- Modify: `src/splitsmith/looks.py` (`PREVIEW_DIR = "preview"`, `preview_file`, `LookVariantInfo`, `LookInfo`, `look_catalog`)
- Create: `src/splitsmith/ui/looks_api.py` (`GET /api/looks`, `GET /api/looks/{name}/preview/{file}`), mount it in `ui/server.py` beside `export_preview_api`
- Modify: `src/splitsmith/ui/route_scope.py` (`HOSTED_CONFINED_ROUTES` entry for the preview route)
- Test: `tests/test_looks.py`, `tests/test_looks_api.py` (new; the TestClient fixture pattern of `tests/test_export_preview_api.py`)

**Interfaces:**
- Produces: `looks.preview_file(look: Look, slot: str, variant: str) -> Path | None` (`<look.root>/preview/<slot>-<variant>.webp` then `.png`, else the shipped default Look's, else `None`; `slot` `"look"` with variant `"default"` is the Look's own sample tile `preview/look.png`). `LookVariantInfo(name: str, preview: str | None)` where `preview` is the URL path `/api/looks/<owner>/preview/<file>` (`owner` is the Look whose file it is). `LookInfo(name, label, source: Literal["shipped","user"], accent_series: list[str], preview: str | None, slots: dict[str, list[LookVariantInfo]])` with every slot in `SLOT_NAMES` present, `default` first for card slots, the union with the shipped default's variants (what `variants_for` gives), the `transition` slot listing the Look's own stings plus the shipped default's. `look_catalog() -> list[LookInfo]` in `list_looks()` order.
- The route `GET /api/looks` returns `{"looks": [LookInfo...]}`; `GET /api/looks/{name}/preview/{file}` returns the file (`image/png` / `image/webp`, `Cache-Control: public, max-age=3600`) or 404 when `name` is not installed, `file` is not `^[a-z0-9_-]+\.(png|webp)$`, or the file does not exist in that Look's `preview/`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_looks.py
def test_the_catalog_lists_every_slot_with_default_first_and_previews(user_dir: Path) -> None:
    catalog = looks.look_catalog()
    assert [c.name for c in catalog][:2] == ["splitsmith", "clean"]
    splitsmith = catalog[0]
    assert set(splitsmith.slots) == set(looks.SLOT_NAMES)
    assert [v.name for v in splitsmith.slots["slate"]] == ["default", "rise"]
    assert [v.name for v in splitsmith.slots["transition"]] == ["wipe"]
    assert splitsmith.preview == "/api/looks/splitsmith/preview/look.png"
    assert splitsmith.slots["slate"][1].preview == "/api/looks/splitsmith/preview/slate-rise.png"


def test_a_user_look_without_previews_borrows_the_shipped_defaults(user_dir: Path) -> None:
    _write_look(user_dir, "club")
    club = next(c for c in looks.look_catalog() if c.name == "club")
    assert club.slots["slate"][1].preview == "/api/looks/splitsmith/preview/slate-rise.png"
    assert club.preview == "/api/looks/splitsmith/preview/look.png"
    assert looks.preview_file(looks.load_look("club"), "slate", "rise") == (
        looks.shipped_looks_dir() / "splitsmith" / "preview" / "slate-rise.png"
    )


# tests/test_looks_api.py
def test_get_looks_lists_the_shipped_looks(client) -> None:
    body = client.get("/api/looks").json()
    assert [l["name"] for l in body["looks"]][:2] == ["splitsmith", "clean"]
    assert body["looks"][0]["slots"]["title_page"][1] == {
        "name": "rise", "preview": "/api/looks/splitsmith/preview/title_page-rise.png"}


def test_preview_files_are_served_and_everything_else_is_404(client) -> None:
    ok = client.get("/api/looks/splitsmith/preview/slate-rise.png")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png"
    for path in (
        "/api/looks/nope/preview/slate-rise.png",
        "/api/looks/splitsmith/preview/look.json",
        "/api/looks/splitsmith/preview/..%2Flook.json",
        "/api/looks/splitsmith/preview/missing.png",
    ):
        assert client.get(path).status_code == 404, path
```

(The previews referenced here are written by Task 2; until then the catalog's `preview` is `None` and the tests of this task that name a file go RED for that reason too. Order: write Task 2's files before expecting this task green, or run these two tasks as one commit pair.)

- [ ] **Step 2: Run, expect failures.**
- [ ] **Step 3: Implement** `preview_file`, the two Pydantic models (`BaseModel`, frozen), `look_catalog`, the router (`APIRouter`; `FileResponse` with the media type by suffix; the name must be in `look_names()` and the file must match the regex, then `(look.root / "preview" / file).is_file()`), the `HOSTED_CONFINED_ROUTES` entry, the mount in `create_app`.
- [ ] **Step 4: Run `tests/test_looks.py tests/test_looks_api.py tests/test_hosted_route_scope.py`; green after Task 2's files exist.**
- [ ] **Step 5: Commit** `feat(gallery): the Look catalog and GET /api/looks (#1246)`.

---

### Task 2: The shipped previews

**Files:**
- Modify: `scripts/render_look_thumbnails.py` (`--look-previews`: for every shipped Look, `preview/look.png` plus `preview/<slot>-<variant>.png` for each card slot variant the Look resolves, each the slot's sample card through that Look's own template at the gallery size; `preview/transition-<name>.webp` for each sting, Task 7 renders the loop, this task writes a poster PNG `transition-<name>.png`)
- Create: `src/splitsmith/data/looks/splitsmith/preview/*.png`, `src/splitsmith/data/looks/clean/preview/*.png` (committed)
- Delete: `src/splitsmith/ui_static/src/assets/look/transition-sting-wipe.png` (the sting tile comes from the Look now; Task 6 reads it from the API)
- Test: `tests/test_render_look_thumbnails.py` (the stub rasterizer; `build_look_previews(out, look, rasterizer)` writes the expected file set for `splitsmith` and for `clean`), `tests/test_looks.py` (every variant of every shipped Look resolves a preview file: `preview_file` is never `None` for a shipped Look)

**Interfaces:**
- Produces: `build_look_previews(out_root: Path, *, look: Look, rasterizer: Rasterizer) -> list[Path]` writing under `out_root / look.name / "preview"`; the sample cards are the ones `build_thumbnails` draws (title page, slate, lower third as a composed frame, closing) through `build_card_still` / `build_lower_third` with `variant=` on the card; `look.png` is the title page in the Look's default variant.

- [ ] Steps: failing tests (file set per Look; `preview_file` never `None` for shipped Looks and the transition slot); implement; render with Chromium; look at `slate-rise.png` and `clean/preview/look.png`; commit `feat(gallery): shipped Look previews (#1246)`.

---

### Task 3: The request layer: any installed Look, per-slot variants, the preset fields

**Files:**
- Modify: `src/splitsmith/ui/exports_api.py` (`overlay_theme: str` on `ExportStageRequest`, `MatchExportRequest`, `CompareGridRequest` with a `field_validator` against `looks.look_names()`; `title_page_variant`, `stage_card_variant`, `closing_card_variant: str | None = None` on the match and grid requests)
- Modify: `src/splitsmith/ui/match_exports.py` (`MatchExportRequestData` gains the three fields, `overlay_theme: str`; the IR cards take `variant=request.title_page_variant or request.card_variant` etc.), `src/splitsmith/compare/cards.py` (`CardOptions` per-slot variants), `src/splitsmith/ui/server.py` (`_run_compare_grid` passes them; slates get `card_variant=req.stage_card_variant or req.card_variant`)
- Modify: `src/splitsmith/export_presets.py` (`look: str = "splitsmith"`, `title_page_variant`, `stage_card_variant`, `closing_card_variant: str = "default"`; `look` validated by shape only, `_NAME_RE`, never against the installed set: a preset must load on a machine without that Look)
- Modify: `src/splitsmith/mcp/export_tools.py`, `src/splitsmith/mcp/server.py` (`overlay_theme: str`), `src/splitsmith/overlay_theme.py` (`ThemeName = str` with the docstring saying it is a Look name)
- Test: `tests/test_ui_match_exports.py`, `tests/test_compare_grid_endpoint.py`, `tests/test_export_presets.py`, `tests/test_ui_exports.py` (the 422 for an unknown Look names the installed ones)

**Interfaces:**
- Produces: request fields as above; `MatchExportRequestData.title_page_variant / stage_card_variant / closing_card_variant: str | None = None`; `compare.cards.CardOptions(..., title_page_variant: str | None = None, closing_card_variant: str | None = None)`; `render_grid_mp4(card_variant=)` unchanged (it draws slates; the caller passes the stage card variant).

- [ ] **Step 1: Failing tests**

```python
def test_per_slot_variants_reach_the_cards_and_fall_back_to_the_knob(tmp_path, monkeypatch) -> None:
    # capture the composition handed to mp4_render.render_mp4; request card_variant="rise",
    # stage_card_variant="default", title_page=True, closing_card=True, title_kind="slate":
    # title_page.variant == "rise", closing.variant == "rise", every stage.title.variant == "default"


def test_an_unknown_look_is_a_422_naming_the_installed_ones(client) -> None:
    r = client.post("/api/match/export", json={"stage_numbers": [1], "overlay_theme": "nope"})
    assert r.status_code == 422 and "splitsmith" in r.text


def test_a_user_look_is_accepted_by_the_request(client, user_look) -> None: ...


def test_the_preset_body_carries_the_look_and_the_variants_with_defaults() -> None:
    body = ExportPresetBody()
    assert (body.look, body.title_page_variant, body.stage_card_variant, body.closing_card_variant) == (
        "splitsmith", "default", "default", "default")
    assert ExportPresetBody.model_validate({"look": "club", "title_page_variant": "rise"}).look == "club"
    with pytest.raises(ValidationError):
        ExportPresetBody(look="Not a name")
```

- [ ] Steps 2-5 as usual; commit `feat(gallery): any installed Look and per-slot variants on the export requests and presets (#1246)`.

---

### Task 4: The preview endpoint takes the Look and the variant

**Files:**
- Modify: `src/splitsmith/export_preview.py` (`PreviewSpec.look: str = "splitsmith"`, `PreviewSpec.variant: str = "default"`; `render_preview` builds the cards with `variant=spec.variant`; the key includes both through the spec's JSON), `src/splitsmith/ui/export_preview_api.py` (`ExportPreviewRequest.look` validated like the export requests, `.variant: str = "default"`; `load_look(req.look)` for the Look and the identity)
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`ExportPreviewBody.look`, `.variant`), `src/splitsmith/ui_static/src/lib/exportPreview.ts` (`previewBody` sends the settings' look and the focused slot's variant; `previewCaption` names the variant when it is not `default`)
- Test: `tests/test_export_preview.py` (`preview_key` differs by look and by variant; `render_preview` hands the rise template to the rasterizer: the fake records `template.name`), `tests/test_export_preview_api.py` (an unknown look 422; `look=clean` renders through the clean palette: the surface colour of the `frame` card with no trim), `exportPreview.test.ts`

- [ ] Steps: failing tests; implement; commit `feat(gallery): the rail preview draws the chosen Look and variant at its poster (#1246)`.

---

### Task 5: SPA state: the catalog reader, the settings fields, the mappers

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`LookInfo`, `LookVariantInfo`, `api.listLooks()`, `overlay_theme` on the payload types is `string`, the three variant fields on `MatchExportRequestPayload` and `CompareGridRequestPayload`, `ExportPresetBody.look` and the three variants)
- Create: `src/splitsmith/ui_static/src/lib/looks.ts` (pure: `DEFAULT_LOOK`, `BUILTIN_LOOKS` (the splitsmith entry the page uses before the fetch answers or when it fails: slots with `default` only, no previews), `visibleLook(looks, name) -> string` (the name when installed, else `DEFAULT_LOOK`), `variantsFor(looks, look, slot) -> LookVariantInfo[]`, `visibleVariant(looks, look, slot, stored) -> string` (stored when the Look offers it, else `"default"`), `stingsFor(looks, look) -> LookVariantInfo[]`, `previewSrc(info) -> string | null` (the API path through `scopeRequestPath`)), `src/splitsmith/ui_static/src/lib/useLooks.ts` (fetch once per page load, `{ looks, loaded }`)
- Modify: `src/splitsmith/ui_static/src/lib/exportPresets.ts` (`ExportSettings.look`, `.titlePageVariant`, `.stageCardVariant`, `.closingCardVariant`; `DEFAULT_EXPORT_SETTINGS`; `settingsToBody` / `applyBody` columns; `describeSettings` names a non-default Look and variant), `src/splitsmith/ui_static/src/lib/renderOptions.ts` (`matchExportFields(options, format, look)` and `gridExportFields(options, look)` return `overlay_theme` and the three variant fields, variants already passed through `visibleVariant`, only when `cardsSupported` / stage cards are supported for the format), `src/splitsmith/ui_static/src/pages/matchExportModel.ts` (threads `look` and the variants)
- Test: `lib/looks.test.ts` (new), `exportPresets.test.ts`, `renderOptions.test.ts`, `api.exportBodies.test.ts`, `pages/matchExportModel.test.ts`

**Interfaces:**
- Produces: `ExportSettings.look: string`, `titlePageVariant: string`, `stageCardVariant: string`, `closingCardVariant: string`; `matchExportFields(options: RenderOptions, outputFormat, look: LookChoice)` where `LookChoice = { look: string; titlePageVariant: string; stageCardVariant: string; closingCardVariant: string }` already resolved through `visibleLook` / `visibleVariant` by the caller (`matchExportModel`), so the mappers stay pure of the catalog.

- [ ] Steps: failing tests (the preset round trip of the four fields; a body without them applies the defaults; the mappers send `overlay_theme` always and the variants only where cards are drawn; `Required<>` payloads name the new fields; `visibleLook` / `visibleVariant` cases of Review Focus 1 and 2); implement; `pnpm typecheck && pnpm lint && pnpm vitest run`; commit `feat(gallery): the SPA carries the Look and per-slot variants through settings, presets and requests (#1246)`.

---

### Task 6: The gallery: Look tiles, the Style control, API stings

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/lookGallery.ts` (`LookVariant.previewUrl?: string`; `slotsForLook(looks, settings) -> LookSlot[]`: a `look` slot first (one variant per installed Look: `id` the name, `name` the label, `previewUrl` its sample tile; `read` the visible look, `write` `{ look }`), the static slots, the transition slot with the Look's stings appended as `sting:<name>` variants with `previewUrl` (MP4, both modes); `visibleTransitionKind(kind, format, mode, stings: string[])`; `variantChoice(slotId) -> keyof LookChoice | null` naming which settings field a card slot's Style writes)
- Modify: `src/splitsmith/ui_static/src/components/export/LookGallery.tsx` (takes `looks`; a tile shows `previewUrl ?? thumbnailUrl(thumbnail)`; under a card slot whose selected variant is on and whose Look offers more than one template variant, a `Segmented` labelled "Style" with the variant names, writing the slot's `LookChoice` field), `LookGroup.tsx` (passes `useLooks()`), `PreviewPane.tsx` (`genericFor` prefers `previewUrl`; the preview request carries the slot's variant)
- Modify: `src/splitsmith/ui_static/src/lib/lookGallery.test.ts` (the visibility table over `slotsForLook(BUILTIN_LOOKS, ...)`; with a catalog carrying `rise` and a sting: the Style field appears, `sting:wipe` is offered on MP4 with its API preview, hidden on FCPXML; a stored `sting:other` is `none`), `pages/Export.test.tsx` (the Look group shows the Look tiles and the Style control through `openGroups`), `lookGallery.test.ts` thumbnail coverage counts only bundled `thumbnail`s
- Test: the visibility table stays as pinned today for the built-in catalog.

- [ ] Steps: failing tests; implement; `pnpm typecheck && pnpm lint && pnpm vitest run`; screenshot the Export page on the seeded demo match (CLAUDE.md's recipe, `--media`), look at it; commit `feat(gallery): Look tiles, a Style per card slot and the Look's stings from the API (#1246)`.

---

### Task 7: Looping transition previews at authoring time

**Files:**
- Modify: `scripts/render_look_thumbnails.py` (`xfade_loop(kind, left, right, *, ffmpeg, frames=12, seconds=1.0) -> list[Image]` runs the project ffmpeg's `xfade` on the two stills, injectable `runner` so the unit test never shells out; `sting_loop(name, look, rasterizer, base_frames)` samples the sting template at `seek(i / fps)` over the fade frames; `save_loop(path, frames)` writes an animated WebP (12 fps, the first and last frame held 0.5 s, `loop=0`); the transition tiles become `transition-<kind>.webp` in the SPA assets and `preview/transition-<name>.webp` in the Look)
- Modify: `src/splitsmith/ui_static/src/lib/lookGallery.ts` (the xfade variants' `thumbnail` is `.webp`), `lookGallery.test.ts` (the glob covers `*.png` and `*.webp`), `tests/test_render_look_thumbnails.py` (the stub runner returns PIL blends; the file set; a loop has more than one frame)
- Delete: the eleven `transition-*.png` tiles the WebP replaces (cut / static / zoom stay PNG)

- [ ] Steps: failing tests; implement; render with the project ffmpeg and Chromium; open two WebPs in the browser screenshot of the gallery (they loop); commit `feat(gallery): looping transition previews (#1246)`.

---

### Task 8: Docs, frames parity, the page

- Modify: `CLAUDE.md` (the Looks paragraph: `card_variant` is the CLI knob and the fallback, the per-slot request fields, the catalog and its routes, the preview fallback rule, the `look` preset field; the gallery paragraph: `slotsForLook`, `useLooks`, the Style control, API previews), `SPEC.md` (module lines for `ui/looks_api.py` and `lib/looks.ts`), the spec's section 4 (an amendment naming the request fields and the preview fallback).
- Run the frame scripts' default sets against main (identical), the Export page screenshots (Look tiles, Style, a looping tile), the Urdr page `looks-slice-6`.
- Commit `docs(gallery): CLAUDE.md, SPEC.md and the spec amendment (#1246)`.
