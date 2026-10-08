# You, your brand and the shooter book: implementation plan

> **For agentic workers:** execute task by task (superpowers:executing-plans);
> steps use checkbox syntax. Each task is test first: write the named tests, watch
> them fail, implement, watch them pass, commit.

**Goal:** a shooter's identity follows their SSI shooter id from match to match,
and the video maker's brand (set once on the account) marks every video.

**Architecture:** two per-account stores (`AccountProfile` for the brand, the
`ShooterBook` keyed by SSI shooter id), local JSON plus files and hosted Postgres
plus R2, behind one async store protocol each, like export presets. Renderers never
read them: the request layer loads a sync snapshot once per export and hands it to
`identity_media.resolved_identity_for` / `grid_identities` (identities) and to the
title and closing cards (`MatchTitle.brand`, like the event logo). "You" is the
existing `ScoreboardIdentity.shooter_id`.

**Tech stack:** Python 3.12, Pydantic, FastAPI, SQLAlchemy + Alembic (hosted),
React + vitest (SPA).

**Spec:** `docs/superpowers/specs/2026-10-08-account-identity-and-shooter-book-design.md`

**Shipping:** two PRs. PR 1 = tasks 1-7 (everything, local mode; hosted stores
answer "empty" so hosted renders exactly as today). PR 2 = task 8 (the hosted
stores, files and backfill).

## Global constraints

- An empty book, no account brand, or the switch off renders every frame, digest
  and argv as on main (both frame scripts' default path, pixel-identical).
- Matching is by SSI shooter id only; never by name.
- The match's own identity, when any field is set, wins as a whole record.
- Never a `state_docs` kind; nothing new enters the sync manifest.
- Uploads: `sniff_logo` / the existing PIL checks, 2 MB, `LOGO_MAX_SIDE`, content
  names, the client's filename never read, symlinks refused.
- The share surface never reads the book or the profile.
- UI copy follows the visual budget (primitives, `Label` 1-3 words, no issue numbers).
- ASCII, no dash punctuation in user copy; a What's new entry in PR 1.

## Review focus

1. A shooter whose project has an SSI id but whose match identity was cleared on
   purpose ("Use shooter book" vs "set nothing"): the book must apply, and a
   match identity of all-`None` fields counts as unset.
2. Two matches open for the same shooter id, edited one after the other: the book
   holds the latest edit, and neither match is written by the other's edit.
3. A book entry whose logo file is missing on disk: a tile without a logo, never a
   failed render or a dangling path in a template.
4. A Look with a brand line but no logo, and an account brand with a logo: the Look
   wins as a whole (no mixing of the Look's line with the account's logo).
5. The CLI on a machine with no `~/.splitsmith/account/`: renders as today, and
   creates nothing.

---

### Task 1: the stores (core models, local JSON)

**Files:**
- Create: `src/splitsmith/account_profile.py`, `src/splitsmith/shooter_book.py`
- Modify: `src/splitsmith/identity.py` (hoist the upload checks)
- Modify: `src/splitsmith/ui/server.py` (the shooter logo route calls the hoisted check)
- Test: `tests/test_shooter_book.py`, `tests/test_account_profile.py`

**Produces:**
- `identity.check_logo_bytes(data: bytes) -> str` returning the extension (`png`,
  `jpeg`, `webp`), raising `identity.LogoError(status, message)`; the body of
  today's `upload_shooter_logo` checks, moved, so the 413/422 texts are unchanged.
- `shooter_book.ShooterBookEntry(BaseModel)`: `shooter_id: int`, `identity:
  ShooterIdentity`, `label: str | None`, `updated_at: datetime`.
- `shooter_book.ShooterBookStore(Protocol)`: `async list() -> list[ShooterBookEntry]`,
  `async get(shooter_id) -> ShooterBookEntry | None`, `async put(entry)`, `async
  delete(shooter_id)`, `async put_logo(shooter_id, data: bytes) -> str` (content
  name), `logo_path(name: str) -> Path | None` (local file, mirrored on hosted).
- `shooter_book.JsonShooterBookStore(root: Path | None = None)` over
  `account_dir()/shooter_book.json` and `account_dir()/files/`.
- `shooter_book.BookSnapshot`: frozen, `entries: Mapping[int, ShooterIdentity]`,
  `logo_path(identity) -> Path | None`; `BookSnapshot.EMPTY`.
- `shooter_book.load_snapshot(store) -> BookSnapshot` (sync, through `run_sync`).
- `account_profile.AccountProfile(BaseModel)`: `brand: LookBrand | None`.
- `account_profile.AccountProfileStore(Protocol)` / `JsonAccountProfileStore` over
  `account_dir()/profile.json`, `put_brand_logo(data) -> str` (via
  `look_brand.save_brand_logo` rules), `brand_path() -> Path | None`.
- `account_profile.account_dir() -> Path`: `user_config` home + `/account`;
  `None`-safe when `user_config.is_disabled()` (stores then read empty, write nothing).

- [ ] **Tests first** (`tests/test_shooter_book.py`):
  `test_put_then_get_round_trips_an_entry`, `test_a_logo_is_content_named_and_sniffed`
  (a PNG renamed `.jpeg` keeps `.png`; an SVG is refused with the shooter route's
  422 text), `test_a_symlinked_book_file_is_never_served`,
  `test_missing_account_dir_reads_empty_and_creates_nothing`,
  `test_snapshot_logo_path_is_none_when_the_file_is_gone`,
  `test_the_shooter_logo_route_still_answers_its_old_errors` (413, 422 texts pinned).
- [ ] Same shape for `tests/test_account_profile.py` (round trip, brand logo checks,
  empty when disabled).
- [ ] Implement; run `uv run pytest -n0 tests/test_shooter_book.py tests/test_account_profile.py tests/test_identity*.py`.
- [ ] Commit `feat(identity): the shooter book and account profile stores`.

### Task 2: resolution reads the book

**Files:**
- Modify: `src/splitsmith/ui/identity_media.py`
- Test: `tests/test_shooter_book_resolution.py`

**Consumes:** `BookSnapshot` (task 1).
**Produces:** `resolved_identity_for(..., book: BookSnapshot = BookSnapshot.EMPTY)`
and `grid_identities(..., book=...)`; `identity_media.identity_source(project, book)
-> Literal["match", "book", "none"]` (for the API and the sheet).

Rule, in one function `_effective_identity(project, book) -> tuple[ShooterIdentity |
None, Path | None]`:

```python
own = project.identity
if own is not None and (own.accent or own.club or own.logo):
    return own, None  # logo resolved as today (ensure_local_logo)
sid = project.selected_shooter_id
if sid is None:
    return own, None
entry = book.entries.get(sid)
if entry is None:
    return own, None
return entry, book.logo_path(entry)
```

- [ ] **Tests first:** `test_the_match_identity_wins_as_a_whole_record` (match sets
  accent only, book has a logo: no logo), `test_an_unset_match_identity_reads_the_book`,
  `test_no_ssi_id_never_reads_the_book` (same label as a book entry's `label`: still
  nothing), `test_an_empty_book_resolves_exactly_as_today` (compare the
  `ResolvedIdentity` against main's for the seeded demo match),
  `test_a_book_logo_missing_on_disk_is_no_logo`, `test_grid_identities_reads_the_book_per_tile`.
- [ ] Implement; run the new file plus `tests/test_identity*.py tests/test_compare_mp4_grid_cards.py`.
- [ ] Commit `feat(identity): a shooter's identity comes from the book when the match sets none`.

### Task 3: every export and preview hands the book in

**Files:**
- Modify: `src/splitsmith/ui/server.py` (the match export job ~4706, the grid job
  ~3069), `src/splitsmith/ui/export_preview_api.py` (~236), `src/splitsmith/ui/palette_api.py`,
  `src/splitsmith/match_cli.py` (~716), `src/splitsmith/compare/cli.py` (~648),
  `src/splitsmith/ui/server.py` `AppState` (`shooter_book`, `account_profile`
  properties: JSON stores locally, task 8's hosted stores; until then an
  `EmptyShooterBookStore` / `EmptyAccountProfileStore` hosted).
- Test: `tests/test_shooter_book_wiring.py`

**Consumes:** task 2's `book=`. Load once per job with `load_snapshot(state.shooter_book)`;
the CLIs build `JsonShooterBookStore()`.

- [ ] **Tests first:** one test per caller that seeds a book entry for the demo
  shooter's SSI id with an accent and asserts it reaches the renderer's
  `shooter_identity` / `identities` kwarg (match job, grid job, preview, both CLIs);
  `test_hosted_reads_an_empty_book_until_its_store_exists`.
- [ ] Preview cache key: the resolved identity already joins it? Read
  `export_preview` key building; if the identity is not in the key, add the
  resolved accent, club and logo content name, and test
  `test_a_book_edit_moves_the_preview_key`.
- [ ] Commit `feat(identity): exports and the preview read the shooter book`.

### Task 4: edits write the book

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`set_shooter_identity`, `upload_shooter_logo`,
  the logo delete route), request model `ShooterIdentityRequest` (`scope:
  Literal["book", "match"] = "book"`), a new `POST /api/shooters/{slug}/identity/use-book`
  (clears the match record: `project.identity = ShooterIdentity()`).
- Test: `tests/test_shooter_book_writes.py`

Rule: after the project is saved, when `scope == "book"` and the project has a
`selected_shooter_id`, `put` the book entry with the match's full identity (the
logo bytes copied into the book's files with the same content name) and the
shooter's display name as `label`. `scope == "match"` writes the project only. The
upload route takes `scope` as a form field.

- [ ] **Tests first:** `test_an_edit_writes_the_book_entry`,
  `test_only_this_match_leaves_the_book_alone`, `test_a_logo_upload_copies_into_the_book`,
  `test_no_ssi_id_writes_no_entry`, `test_use_book_clears_the_match_record_only`,
  `test_two_matches_edited_in_turn_leave_the_latest_in_the_book` (Review focus 2).
- [ ] Commit `feat(identity): setting a shooter's look saves it to the shooter book`.

### Task 5: the account brand on the cards

**Files:**
- Modify: `src/splitsmith/composition.py` (`MatchTitle.brand: BrandMark | None`,
  `BrandMark(logo_path: Path | None, line: str | None)`), `src/splitsmith/look_brand.py`
  (`brand_json(look, slot, fallback: BrandMark | None = None)`: the Look's brand when
  it has a logo or a line, else the fallback's, as a whole), `src/splitsmith/overlay_card.py`
  (pass `card.brand`), `look_sandbox` (mount the fallback logo exactly as the Look's
  brand logo is mounted: a real PNG/JPEG/WebP, not a symlink),
  `src/splitsmith/ui/match_exports.py`, `src/splitsmith/compare/cards.py`
  (`CardOptions.account_brand: bool = True`, `title_cards(..., brand=)`),
  `src/splitsmith/export_preview.py` + `ui/export_preview_api.py`,
  `src/splitsmith/ui/exports_api.py` (`account_brand: bool = True` on
  `MatchExportRequest`, `CompareGridRequest`), `src/splitsmith/export_presets.py`,
  `src/splitsmith/match_cli.py` + `compare/cli.py` (`--account-brand/--no-account-brand`),
  `src/splitsmith/ui/server.py` (resolve `state.account_profile` once per job).
- Test: `tests/test_account_brand.py`

- [ ] **Tests first:** `test_the_look_brand_wins_as_a_whole` (Review focus 4),
  `test_the_account_brand_marks_a_look_without_one`, `test_switch_off_is_the_card_it_always_was`
  (context and `template_digest` equal to main's), `test_no_account_brand_changes_nothing`,
  `test_only_the_title_page_and_closing_card_carry_it`, `test_the_fallback_logo_is_mounted_in_the_sandbox_and_a_symlink_is_not`,
  request/preset/CLI wiring, `test_the_preview_key_moves_with_the_brand_only_when_drawn`.
- [ ] Pixel gate: `scripts/render_match_frames.py` and `render_grid_frames.py` default
  path against main (no account brand), and once with a demo account brand: look at
  the title page and closing card.
- [ ] Commit `feat(looks): your account brand on every video`.

### Task 6: the "You" API

**Files:**
- Create: `src/splitsmith/ui/me_identity_api.py` (router; reaches state through
  `request.app.state`, never imports `server`), registered in `server.py`.
- Test: `tests/test_me_identity_api.py`

Routes (both modes; the hosted store is task 8's): `GET/PUT /api/me/profile` (brand
line), `POST/DELETE /api/me/profile/brand-logo`, `GET /api/me/profile/brand-logo`
(serves the file, `nosniff`, `private, no-cache`), `GET /api/me/shooter-book` (entries
with a `logo_url`), `PUT /api/me/shooter-book/{shooter_id}` (accent, club; validated by
`ShooterIdentity`), `POST/DELETE /api/me/shooter-book/{shooter_id}/logo`, `GET
/api/me/shooter-book/{shooter_id}/logo`, `DELETE /api/me/shooter-book/{shooter_id}`,
`GET /api/me/shooter-search?q=` (the live index through `SsiHttpClient`, no match,
no cache dir). Not on any share allowlist.

- [ ] **Tests first:** round trips, the upload checks' status codes, a share token
  request to every route is the share surface's opaque 404, `test_shooter_search_needs_no_match`.
- [ ] Commit `feat(identity): the You and shooter book routes`.

### Task 7: the SPA

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/You.tsx` (route `/you`, both modes),
  `components/you/YouSection.tsx` (shooter pin through the existing
  `get/putScoreboardIdentity` plus `shooter-search`; your look = your book entry;
  your brand), `components/you/ShooterBookList.tsx` (entries, remove), `lib/you.ts`
  (pure: source wording, "is this me").
- Modify: `pages/Account.tsx` (hosted: a link to "You"), `pages/Pick.tsx` (a "You"
  link in the header, local), the roster (`components/footage/ShootersPanel.tsx`:
  the "You" mark when `selected_shooter_id === me.shooter_id`; "This is me" in the
  row menu, which PUTs the scoreboard identity), the identity sheet (source line,
  "Only this match" checkbox sending `scope`, "Use shooter book"),
  `lib/renderOptions.ts` (`accountBrand`, default true; `matchExportFields` and
  `gridExportFields` send `account_brand` only when false, so an untouched form sends
  the body it always sent), `lib/exportPresets.ts`, the Details group ("Your brand"
  checkbox beside "Made with splitsmith"), `lib/api.ts` (+ `api.exportBodies.test.ts`
  `Required<...>` payloads), `data/whats_new.json` (entry `your-brand-and-shooter-book`,
  chip on the "You" link).
- Test: `lib/you.test.ts`, `pages/You.test.tsx`, the touched components' tests,
  renderOptions/exportPresets/api.exportBodies updates.

- [ ] **Tests first** for `lib/you.ts`; then the page and roster tests (mock the API).
- [ ] Gate: `npx tsc -b --noEmit`, `npx eslint <files>`, `npx vitest run` (never prettier).
- [ ] Look at it: seed the demo match (`scripts/seed_demo_match.py`), scratch
  `SPLITSMITH_HOME`, `SPLITSMITH_AUTO_SYNC=0`, screenshot `/you`, the roster and the
  identity sheet.
- [ ] Commit `feat(ui): You, your brand and the shooter book`.

**PR 1 ends here**: full suite, one review pass over tasks 1-7, What's new checked,
merge on green by hand.

### Task 8: hosted (PR 2)

**Files:**
- Create: `src/splitsmith/db/account_identity.py` (`PostgresShooterBookStore`,
  `PostgresAccountProfileStore`), an Alembic migration (`shooter_book`: `user_id`,
  `shooter_id`, `identity` JSON, `label`, `updated_at`, PK `(user_id, shooter_id)`;
  `account_profiles`: `user_id` PK, `brand` JSON), models in `db/models.py`.
- Modify: `server.py` `_build_tenant` (wire the stores like `PostgresExportPresetStore`),
  `identity_media` (`ensure_local_account_file(storage, user_id, name)`: R2 key
  `users/<user_id>/account/<name>`, mirrored to the cache like a shooter logo, `None`
  when missing), the hosted delete-key rule (only content names under the caller's
  own prefix, only through these routes).
- Backfill: `shooter_book.backfill(store, matches)`: from every match the account
  owns (`ProjectStateStore.load_docs_for_matches`, one query), shooters with an SSI
  id and a set identity, the most recently updated winning; runs once per account
  (a `backfilled_at` on `account_profiles`); locally the same function over
  `match_registry` on the first `JsonShooterBookStore` load with no file.
- Test: `tests/test_account_identity_db.py` (sqlite like the other db tests),
  `tests/test_account_identity_docker.py` (`-m docker`): rows per user, another
  user's shooter id answers 404, files under the caller's prefix only, the share
  surface reaches neither, backfill once and most recent wins, a job reads the
  snapshot through `run_sync`.
- [ ] Commit `feat(hosted): the shooter book and account brand on splitsmith.app`.

**PR 2 ends here**: migrations job green, one review pass, merge on green, staging
check before release.
