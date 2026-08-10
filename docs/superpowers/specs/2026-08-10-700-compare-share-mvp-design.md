# Design: Compare-behind-a-token share MVP (#700)

Date: 2026-08-10
Issue: #700 (MVP path in the 2026-08-09 comments; owner decisions in the
2026-08-10 comment). Approach approved by owner 2026-08-10.

## Goal

An anonymous holder of a share link watches the beep-aligned compare grid
in the browser: clean trims, DOM overlay-lite (timeline + ranking), the
existing Compare.tsx sync engine. Desktop-only, with a mobile CTA to the
share results view. Tight privacy surface per the decisions on #700.

## The finding that shapes the design

Hosted Compare is an empty state today. `get_stage_compare`
(server.py:12586-12648) resolves trims with raw `Path.exists()` on the
app container's disk, and `stream_shooter_video`'s non-registered
fallback (server.py:12863-12893) is local-disk-only by docstring. The
storage-aware machinery exists (`audio_helpers._storage_trim_key`,
`storage.exists`, `serve_media` presign, `export_storage`) but compare
never consumes it. Share links live on hosted, so the MVP wires compare
into that machinery - which also fixes owner-side hosted Compare.

## Backend

### 1. Storage-aware trim resolution; `video_path` becomes `video_ref`

`get_stage_compare` returns, per shooter, a **logical relative ref** -
`exports/<name>` or `trimmed/<name>` - instead of an absolute path.
Resolution order stays lossless-export-first, audit-cache second:

- Local: existence checks against the actual `exports_dir`/`trimmed_dir`
  (which may be absolute overrides outside the shooter root - the ref is
  logical, mapped to real dirs server-side, exactly as the stream
  fallback's containment check does today).
- Hosted: `storage.exists` on `{scope}/exports/<name>` (export_storage
  key convention) then `{scope}/trimmed/<name>` (`_storage_trim_key`
  convention, same shape `_SYNC_MEDIA_KEY_RE` pins). One HEAD per
  candidate per shooter is accepted for the MVP.

The field is renamed (`video_ref: str | None`) so every consumer is
updated consciously; `beep_offset_in_clip` semantics unchanged.

Rejected alternative: fully opaque id + server-side mapping table - more
moving parts, no added privacy (the name reveals only stage number and
the blake2s video id). Precedent for relative refs on the share surface:
registered video paths are already exposed there as load-bearing for
streaming (server.py:6727-6729).

### 2. Stream fallback: relative-only, hosted-aware

`stream_shooter_video`'s non-registered fallback:

- accepts only `^(exports|trimmed)/[^/]+\.mp4$` - absolute paths and
  traversal shapes are rejected outright (Compare was the only consumer
  of the absolute form; verified during implementation),
- local: resolves against the actual dirs, containment check kept,
  Range-capable file response as today,
- hosted: builds the storage key for the ref's dir kind, `storage.exists`
  -> `serve_media` presigned 307, else 404.

The registered-video branch is untouched. No legacy absolute-path
acceptance remains (clean-no-fallbacks).

### 3. Allowlist + share-context stripping

- `_SHARE_PATH_RE` (server.py:936-946) gains exactly two shapes:
  `match/stage/\d+/compare` and `match/shooters/[^/]+/videos/stream`.
- `_build_coach_response` (server.py:10382-10383) nulls `coaching_note`
  and forces `improvement_flag` False when `current_share_request.get()`
  - the pre-existing leak named in the #700 decision comment.
- The compare payload needs no share-conditional stripping: after the
  ref change it carries name, slug, beep offset, duration, stage time,
  shots (time + interval_class) - the decided minimal surface.

## Frontend

### 4. Share route + desktop gate + discoverability

- `share/:token/compare/:stage` inside `ShareShell`, wrapped in
  `<DesktopGate screen="Compare">`. `DesktopOnlyNotice` links via
  `useMatchHref`, which is already share-aware (matchHref.ts:26-27), so
  the mobile notice's results link resolves to `/share/{token}/results`
  - the decided CTA.
- The share results stage view gains a "Compare" affordance linking to
  the compare route (hidden on mobile).
- API plumbing is free: `scopeRequestPath` (api.ts:1827-1842) rewrites
  every `/api/match/...` and `/api/shooters/...` call to the share
  prefix automatically inside the `/share/{token}` tree.

### 5. Compare.tsx share mode

Share mode is detected the way `useMatchHref` does it (path-based
token). In share mode:

- hidden: Audit and Coach tab-strip buttons (Compare.tsx:318-344),
  "Open in audit" (:540-547), "Audit {name}" buttons in the empty state
  (:585-596), "Build trim cache" (:526-538; its POST is unreachable
  under the GET-only share middleware regardless),
- empty-state copy becomes viewer-neutral ("The owner hasn't prepared
  video for this stage") instead of audit instructions,
- everything else (sync engine, timeline, RankingTable, shot views) is
  unchanged.

### 6. Drift instrumentation

The resync loop (Compare.tsx:149-171) tracks the max observed
`|el.currentTime - target|` across slaves; on pause/unmount it logs one
`console.info` line with stage number and max drift, then resets. This
answers the 0.15s-tolerance question from a real staging session; no
telemetry backend (none exists in the SPA - console is the pattern).

## Error handling

- Shooter with no resolvable trim in either mode: `video_ref` null, SPA
  renders the existing unfinished/empty tiles (share copy per above).
- Stream ref that stops existing between payload and fetch: 404; the
  share middleware normalizes it to the opaque share 404.
- Malformed refs (absolute, traversal, wrong extension): 404 before any
  filesystem or storage touch.

## Testing

- Backend: hosted-fixture tests (tests/hosted_helpers.py pattern) for
  ref resolution (storage.exists hit/miss for both key kinds) and the
  stream fallback's hosted presign + rejection matrix (absolute path,
  `..` traversal, non-mp4, unknown name). Local-mode tests updated for
  the ref shape. `test_compare_stage_endpoint.py` extended, not
  replaced.
- Share surface: regex-lock tests extended for the two new shapes;
  boundary probes in the #786 style (compare happy path via
  `_share_url`, revoked/unknown stay opaque 404, non-GET rejected,
  coach response carries no note/flag for share reads while the owner
  read still does).
- SPA: route test for the share compare mount + DesktopGate, share-mode
  affordance gating, `video_ref` rename fallout (RankingTable fixtures),
  drift-summary unit if cheaply testable.
- Gates: ruff/black/pytest, `pytest -m docker` (share + storage paths),
  pnpm typecheck/test, scoped eslint, dash sweep.
- After merge: staging end-to-end (sync real match, mint link, watch
  grid anonymously, confirm opaque-404 surface, read the drift line).

## Out of scope

- Passphrase (#787), revoked info page (#788), OG card for the compare
  route, burned-in-parity overlays (post-#684/#699 work), iOS
  multi-video measurement, tuning `SYNC_DRIFT_THRESHOLD_S`.
