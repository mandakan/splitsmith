# 07 -- Sync and migration

This doc defines **how a project moves between local mode and hosted
mode** -- the user's first onboarding path, the "I started locally but
want to share with my squad" flow, and the v2 ramp into bidirectional
sync.

The v1 deliverable is **one-way push from desktop to cloud, with
tarball import as the no-desktop fallback.** v2 is bidirectional.
v3 is the public match repository.

## Personas and flows

Three users; each takes a different path into hosted mode.

### Persona A -- desktop-first existing user

Uses `splitsmith ui` daily. Wants to back up matches to cloud + share
one match with a squadmate.

Flow:
1. Updates the desktop app. Sees a "Sign in to cloud" affordance in
   settings (introduced in this v1 release).
2. Clicks it; magic-link flow opens in browser; clicks the link;
   browser deep-links back to the desktop with a token.
3. Desktop now shows "Sync to cloud" buttons next to each project in
   the picker.
4. Clicks the button on one project; sees a progress indicator;
   project lands in their cloud account; the cloud URL is offered
   for sharing (v1: read-only public link; v2: per-user invite).

### Persona B -- new hosted-first user

Has never installed the desktop. Visits `splitsmith.app`, signs up,
wants to detect shots in a head-cam video they have on their phone.

Flow:
1. Lands on marketing page; clicks "Try it".
2. Magic-link signup.
3. First-run flow: "Upload audio" or "Upload full video?" choice.
4. Audio path: browser extracts in WebAudio, uploads via tus, Tier 2
   detection runs, results displayed.
5. Free tier preview: first match free, second match prompts for
   premium upgrade.

### Persona C -- existing local user without desktop access

Has a tarball of an old project from a previous machine. Wants to
view it in the cloud without reinstalling the desktop.

Flow:
1. Signs up at `splitsmith.app` (magic link).
2. Picker shows "Import a project" affordance.
3. Drags the tarball into the browser; tus uploads it; an importer
   job processes it; the project appears in their picker.

## Direct sync (Persona A)

The preferred flow. Implemented as **discrete API calls**, not as a
sync engine. Each call is idempotent; the desktop drives.

### Sign-in

The desktop opens the user's browser at:

```
https://splitsmith.app/link/desktop?challenge=<random_id>&
  callback=splitsmith://link
```

The hosted page handles magic-link auth as usual. On success, it
redirects to:

```
splitsmith://link?challenge=<random_id>&token=<short_lived_token>
```

The desktop's URL handler captures this. It exchanges `token` for a
refresh token via:

```
POST /api/v1/auth/desktop/exchange
  { challenge: "<random_id>", token: "<short_lived_token>",
    device_name: "Mathias's MacBook Pro" }
->
  { refresh_token: "...", access_token: "...", access_expires_in: 3600 }
```

The refresh token is persisted at `~/.splitsmith/auth.json` (0600).
The desktop now signs API requests with `Authorization: Bearer
<access_token>`.

### Pushing a project

The desktop walks the project layout and pushes each file. The flow
is conceptually:

```
POST /api/v1/projects
  { display_name, match_date }
->
  { project_id }

# For each file in the project:
POST /api/v1/uploads
  { kind: 'project_file',
    project_id, relative_path: 'shooters/foo/project.json',
    size_bytes, sha256 }
->
  { upload_id, tus_endpoint }

[tus PATCH chunks to tus_endpoint, completing the upload]

# After all files uploaded:
POST /api/v1/projects/<project_id>/sync/finalize
->
  204
```

The project bytes land at `projects/<project_id>/...` in R2 with the
exact same layout as on the desktop's disk. The `projects` row in
Postgres gets created on the first POST.

The desktop maintains a local `sync_state.json` per project recording
which files were last pushed with which sha256. Subsequent pushes
diff against this; only changed files re-upload.

Raw video is **excluded by default**. The desktop shows a per-file
toggle "include raw video?" before sync; defaults off. Audio,
match.json, project.json, shots.csv, audits, FCPXML exports all
push.

### Idempotency

Every sync POST carries an `Idempotency-Key` derived from
`<project_id>:<relative_path>:<sha256>`. Re-running sync after a
network drop is safe -- already-uploaded files no-op.

### Conflict handling (v1)

v1 doesn't have proper bidirectional sync, so conflicts are simple:

- The desktop never overwrites cloud data unless the user explicitly
  initiates a push.
- The cloud never modifies pushed projects on its own (no sneaky
  re-detection or schema migration that mutates the JSON).
- If the cloud version was modified out-of-band (theoretically only
  possible if the user used the web UI to edit the synced project),
  the next desktop push detects the divergence (cloud `updated_at`
  > local `last_pushed_at`) and prompts: "Cloud version is newer.
  Overwrite cloud, abort, or re-pull?"

In v1, the practical recommendation in the UI is "edit a project
either locally OR in the cloud, not both". v2's bidirectional sync
removes this restriction.

### Pulling a project (v1: rare path)

The user wants to pull a cloud project to the desktop -- typically
because they switched machines.

```
GET /api/v1/projects/<project_id>/manifest
->
  { project_id, files: [{path, sha256, size_bytes}, ...] }

# For each file:
GET /api/v1/files/signed-url?path=projects/<id>/<relative_path>&method=GET
[fetch from R2]
```

The manifest includes a flag per file for "available?" -- raw video
that wasn't uploaded shows up as `available: false` so the desktop
knows it's there in metadata but not in storage.

After pull, the desktop's `sync_state.json` records the pulled
sha256s so future pushes diff correctly.

## Tarball import (Persona C)

For users who don't have the desktop running.

Tarball format (already used by `splitsmith export tarball` -- this
predates the SaaS work):

```
project-<slug>.tar.gz
  match.json
  shooters/
    <slug>/
      project.json
      audio.wav
      shots.csv
      shots.fcpxml
      audits/...
  raw/                     # excluded by default in export
    headcam.mp4            # only present if user opted in
```

Browser flow:
1. User clicks "Import project" in the picker.
2. Drags the tarball into the dropzone.
3. tus upload starts (kind=`project_tarball`), lands at
   `imports/<upload_id>.tar.gz`.
4. After upload completes, the browser POSTs to `POST /api/v1/
   imports/<upload_id>/process`.
5. A worker job processes:
   - Streams the tarball.
   - Validates structure (must have `match.json` at top level,
     schema-conformant).
   - Mints a new `project_id`.
   - Rewrites the `local` user sentinel (see 02) to the importing
     user's ID anywhere it appears.
   - Uploads each file to `projects/<new_id>/<original_relative_path>`.
   - Inserts `projects` + `project_members` rows.
   - Deletes the source tarball from `imports/`.
6. The job completes; the picker refreshes; the new project appears.

The whole import takes 1-5 minutes for a typical match (audio only).
The user sees progress in the jobs drawer.

### Validation

The importer rejects:

- **Missing `match.json`.** Required; no point in importing a non-
  Splitsmith tarball.
- **Schema mismatch.** If `match.json` has a major version we don't
  understand, we reject with a "this project was created with a
  newer Splitsmith; upgrade your hosted account [link]" error.
- **Path traversal in tarball.** `../etc/passwd`-style entries
  rejected; only paths relative to the project root accepted.
- **Tarball >50 GB.** Same upload limit as everything else.

### What about the inverse: cloud -> tarball?

Available as `GET /api/v1/projects/<id>/export?format=tarball`. The
server streams a tarball of the project's R2 contents. Idempotent;
the user can re-export anytime.

This is critical for **data portability**: the user can leave hosted
mode and take their data with them. We mention this on the marketing
page; it's a trust signal.

## v2 -- bidirectional sync

Out of scope for v1 implementation. Architectural notes for forward
compatibility:

- The current sync model is "the desktop pushes; cloud is a passive
  store." v2 changes this to "either side can edit; reconcile."
- Reconciliation happens at the per-file level using sha256 + a
  per-file `last_modified` timestamp from R2 / disk.
- Conflict resolution: per-file three-way merge isn't feasible
  (binary files, complex JSON). We do **per-file last-writer-wins
  with conflict copies**: the loser's version is preserved as
  `<original_path>.conflict-<timestamp>` in both locations.
- A real-time sync engine isn't on the table -- we operate in pull
  intervals (desktop polls every 60s when running; web app polls
  on tab focus).
- Squad-share invites (also v2) reuse this same engine: a squadmate
  sees the project in their picker via an ACL row, and their local
  desktop pulls a copy to disk for offline editing if they want.

The v1 abstractions don't preclude this:
- Each file's sync state is an opaque blob in `sync_state.json` --
  v2 can extend the format without breaking v1.
- The API surface is additive (new endpoints for v2 reconcile
  flows; v1 endpoints remain).
- The data model has nothing v1-specific that would prevent v2
  sharing.

## Relinking and revoking

The desktop's link can be revoked from the web UI ("Devices" page,
shipped in v1):

- User sees a list of `desktop_links` rows.
- Click "Revoke" sets `revoked_at`; the desktop's next API call
  fails with 401; desktop shows "Sign in again" prompt.
- The local-mode functionality keeps working; only the cloud bridge
  breaks.

Re-linking the same desktop creates a new row. We don't try to
"reuse" old links -- it's simpler to mint new tokens.

## Local-mode-only users

A user who never signs in stays entirely local. Nothing in v1
changes for them. The "Sign in to cloud" affordance lives in
settings; if they never click it, they never see hosted-mode UI.

## Concurrency between sync and detection

The desktop should not push a project while a local detection job is
running on it (the JSON files would be in flux). The sync flow
takes a local advisory lock per project before walking files; if a
job is active, it waits or aborts with a UX message.

The cloud doesn't run detection on synced projects automatically.
Detection only runs when the user explicitly requests it via the
web UI -- which means the user is online, looking at the project,
and not simultaneously syncing the same project from the desktop.

## What this implies for the desktop release

A v1 desktop release adds:

- Settings page entry: "Cloud account" -> sign in / out, current
  email, current device name.
- Project picker: per-project "Sync to cloud" button + status
  (synced / not synced / syncing / out of date).
- Background sync state in `sync_state.json` per project.
- URL handler for `splitsmith://` (macOS / Windows registration).
- HTTP client that signs requests with Bearer tokens + handles
  refresh.

These are additive to the existing local-mode UI. If the user never
signs in, the cloud-related UI shows "Sign in to enable" stub
content; nothing else changes.

## Open questions

- **Refresh-token rotation.** Should refresh tokens rotate on every
  refresh? Best practice says yes; complicates the desktop's auth
  storage. Probably worth doing in v1; track during impl.
- **Sync over flaky networks.** Tus handles per-file resume but
  not "I lost my connection mid-walk and don't know which files I
  pushed." `sync_state.json` should be append-only with fsync
  per write so a crash mid-sync doesn't lose progress.
- **Multi-device same-account.** Two desktops syncing the same
  account is fine in v1 (each pushes independently; the latest
  push wins per file). v2's bidirectional model handles it
  cleanly.
- **Granularity of "out of date".** Per-file or per-project?
  Per-project is enough for v1's UX (the badge just says "out of
  sync"); per-file matters for v2's conflict UI.
- **What happens to entitlements when a user signs out of the
  desktop?** They keep their local-mode functionality; only the
  cloud bridge is severed. Clear in the UI copy.
