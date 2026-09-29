# Desktop command queue: render and upload to YouTube

Date: 2026-09-28
Status: design, awaiting review
Issue: #1100. An addendum to `2026-09-28-desktop-command-queue-design.md`,
which shipped the queue with one kind (`shot_detect`, 0.45.0). This adds a
second kind on the same table, routes and runner. Nothing here changes how
`shot_detect` behaves.

## Goal

From the phone, in a desktop-synced match, ask the desktop to render a
shooter's match video and upload it to YouTube. The desktop renders,
uploads once, and the phone shows the video link.

Success: on the phone, open Export for a mirrored match, press "Render on
desktop"; once the desktop is up and the render finishes, the request's
row shows "Uploaded" with the youtu.be link and the channel it went to,
and hosted rendered nothing.

## What this reverses

The queue spec parks both later kinds behind #752: "`render_export` once
#752 gives a rendered MP4 a path to hosted", and `youtube_upload` "by
export run id". This addendum ships one combined kind now instead:

- **Render alone waits for #752.** Its result is a file, and a file has
  no way to hosted yet: the sync media key guard (`_SYNC_MEDIA_KEY_RE`)
  admits `trimmed/` and `beep_review/` only.
- **Upload alone waits too.** It names an export on the desktop, and the
  phone cannot list those: `export_runs` is not a pullable kind.
- **Render then upload does not need #752.** Its result is a URL, which
  fits in the command's `result`. The phone never needs the bytes.

When #752 lands, `render_export` can still ship as its own kind; nothing
here is in its way.

## Decisions

| Question | Decision |
|---|---|
| Kind | `render_upload`. One command, one local job. |
| Which export | `match_export` (the single-shooter match video). The compare grid has no `-youtube.json` sidecar and is out. |
| Settings | The phone's Export page builds a full `MatchExportRequest`, carried in `args`. Presets do not sync (hosted keeps them in `export_presets`, the desktop in a JSON file), so "render with preset X" cannot work: the desktop would not have X. |
| YouTube account | The desktop's own connection, its local OAuth file. Not the hosted account's. |
| Double upload | The match's `desktop_commands_done.json` ledger records `command_id -> video` the moment YouTube returns the id; the upload record in the sidecar carries `command_id` too. The desktop checks both before rendering. |
| Re-claim after a lapsed lease | Only by the desktop that claimed it (`claimed_by`). A second desktop never re-runs an upload. |
| A render that succeeds and an upload that fails | The command fails, naming both: "Rendered on the desktop, but the upload failed: ...". The upload is the point of the request. |

## Design

### Hosted

**Kind allowlist.** `db/desktop_commands.COMMAND_KINDS` gains
`render_upload`.

**Request.** `POST /api/match/desktop-commands` with:

```
{kind: "render_upload", slug, stage_number: null, args: {request: <MatchExportRequest>}}
```

The route validates `args.request` as a `MatchExportRequest` and requires
`youtube_upload` to be true. The model's own validator then requires
`youtube_sidecar` and `output_format == "mp4"`. Either failure answers
422. `expected_revision` stays null: re-rendering from state that moved is
not destructive the way a reset re-detect is.

**Dedupe** keys on `(match_id, kind, slug, stage_number)` as today.
`stage_number` is null, so it is one render per shooter per match at a
time.

**Claim.** The claim query already takes `pending` rows and `claimed` rows
whose lease expired. For `render_upload` an expired lease is re-claimable
only when `claimed_by` equals the claiming token. A restart of the same
desktop picks its own command back up after the 10 min lease. Another
desktop leaves it alone, so the command waits until the first desktop
returns or the user cancels it. The fingerprint count is per token too:
`pending_commands` leaves out a pinned command another desktop holds, or
that desktop would sync every poll and claim nothing.

### Desktop

**`sync/commands.py`.**

- `RUNNABLE_KINDS` gains `render_upload`.
- `refuse_reason` branches by kind. For `render_upload` it refuses when:
  - `slug` is not in the match on this desktop;
  - `args.request` does not validate;
  - the desktop has no YouTube connection: "YouTube is not connected on
    the desktop".
- A new `prior_result(match_root, command) -> dict | None` reads the
  match's ledger `<match_root>/desktop_commands_done.json` first, then
  scans the shooter's `exports/*-youtube.json` for an `upload.command_id`
  equal to the command's id, and returns `{video_id, url,
  channel_title}`. The ledger is written atomically by `upload_export`'s
  `on_video_id` callback before captions, thumbnail and playlist, so a
  desktop killed in that window, or a desk re-export that rewrote the
  sidecar without its `upload`, still finds the video. It is root-level
  and outside everything `sync.plan` reads, so it is never pushed. The runner calls it before `refuse_reason`. A hit
  completes the command as `succeeded` with that result and starts
  nothing.

  This has to run before rendering: a re-render rewrites the sidecar and
  would drop the record. It covers the one case the lease cannot: the
  upload finished, then the desktop quit before hosted took the
  completion.

An older desktop claims a `render_upload` and fails it at once with "this
desktop cannot run 'render_upload' requests yet; update the desktop app".
That behaviour is already in `refuse_reason`; the phone shows the reason.

**The job.**
- A new local job kind, `render_upload`. It goes in `RENDER_KINDS`, so
  automatic syncs hold while it runs, as for a render started at the
  desk.
- `_start_desktop_command` (`ui/server.py`) submits it when the kind is
  `render_upload`, deduped with `find_active` like `shot_detect`.

The body runs in three steps:

1. **Render.** The `render_upload` body runs `_run_match_export` unchanged
   through a handle wrapper that captures its `set_result` payload
   (`fcpxml_path`) and scales its progress; the request it passes has
   `youtube_upload` off, so no separate upload job is chained.
2. **Check for a cancel.** `handle.check_cancel()`.
3. **Upload.** Call `run_youtube_upload` in-process on the same handle,
   with:
   - the rendered MP4's filename;
   - the request's `youtube_privacy`, `youtube_playlist` and
     `youtube_playlist_id`;
   - `again=True`, as in the existing chain;
   - a new `command_id` argument, written into
     `youtube_sidecar.UploadRecord.command_id` (optional; a sidecar
     without it reads as before).

   The body sets the job result to `{video_id, url, channel_title}`.

Progress runs 0 to 0.8 for the render and 0.8 to 1.0 for the upload.
Messages read "Rendering stage 3 of 8" and "Uploading 42%", and the runner
forwards them as heartbeat progress. Cancel works throughout: the export
checks between stages and the upload between chunks. An aborted resumable
upload publishes nothing.

If the upload raises after a good render, the body re-raises as "Rendered
on the desktop, but the upload failed: <reason>". The MP4 stays in the
shooter's exports, where the desk's Export history can upload it again.

**Runner** (`ui/command_runner.py`). Today a succeeded job waits for a
sync that started after it ended, then completes. That wait exists
because detection's result travels in the audit doc. A render-upload's
result travels in the command itself, so:

- The wait becomes per kind. `render_upload` completes as soon as the job
  succeeds, with `result` taken from `job.result`.
- The failure text for a failed result sync ("detection ran, but its
  result could not be synced") stays `shot_detect`'s.
- Heartbeats, cancel and the retried completion (#1111) are unchanged.

### Phone UI

**Export page on a mirror.** Today a mirror gets the page disabled with
`READ_ONLY_MIRROR_MESSAGE` (no `edit`). On a desktop-origin match it
becomes a "Render on desktop" mode:

- The form works as usual, with the hosted presets.
- Output format is fixed to MP4, and "Upload after render" is on and
  locked. `settingsToBody` is unchanged; the page overrides those fields
  when it posts.
- The single primary button reads "Render on desktop". It posts the
  command through a new `api.requestDesktopRender` wrapper, which spreads
  the payload as `exportMatch` does (CLAUDE.md, export presets: never list
  fields by hand).
- The YouTube row becomes one line: "Uploads to the YouTube account
  connected on your desktop." The connect button and the playlist picker
  are hidden, because both act on the hosted connection, which may be a
  different channel. Playlist by title stays.
- The presence line from the queue spec sits above the button.
- The rail's preview already degrades to one muted line on hosted, so no
  change is needed there.
- The history area lists this shooter's `render_upload` requests, from
  the same `GET /api/match/desktop-commands`, in hairline rows: the
  shipped `DesktopCommandLine` (icon, line, Cancel), with the video link
  on success.

**Match menu.** "Desktop requests" already lists every command. A
`render_upload` row reads "Render and upload, <shooter>", with the same
statuses.

**Wording.** Row derivation (status, line, link) extends the queue spec's
pure module in `lib/`, with vitest cases per state. The page maps it onto
the primitives.

## Error handling

| Situation | Behaviour |
|---|---|
| Desktop has no YouTube connection | `failed`: "YouTube is not connected on the desktop" |
| Source footage or trims missing on the desktop | `failed` with the export's own error text |
| Render fails | `failed` with the job's error |
| Render succeeds, upload fails | `failed`: "Rendered on the desktop, but the upload failed: ..."; the MP4 stays on the desktop |
| Desktop quits mid-render | lease lapses after 10 min; the same desktop re-claims on restart and renders again (the segment cache makes it cheap); another desktop does not |
| Upload finished, completion lost, desktop quits | on restart the re-claim hits `prior_result` and completes with the existing video; no second upload |
| Cancel while waiting | `cancelled` at once (existing) |
| Cancel while rendering or uploading | `cancel_requested` reaches the job at the next heartbeat; the export stops between stages, the upload between chunks |
| Cancel after the holder's lease lapsed | `cancelled` at once; a holder that comes back gets no heartbeat and stops, and its completion leaves the row cancelled |
| Cancel after the last chunk | the job already succeeded; the command completes `succeeded` with the video |
| Older desktop | claims and fails it with "update the desktop app" |

## Testing

Each new test must fail against the pre-change code: delete the fix and
watch it fail.

- **Store:**
  - A `render_upload` whose lease lapsed is re-claimed by its own token
    and not by another.
  - A lapsed `shot_detect` is still re-claimable by any token. This pins
    that the rule is per kind.
  - Dedupe is one per shooter.
- **Route:**
  - `youtube_upload: false` gets 422.
  - A non-MP4 format gets 422.
  - A hosted-native match gets 409 `not_a_mirror`, as today.
- **`prior_result`:**
  - A sidecar whose `upload.command_id` matches returns the record.
  - A sidecar with another id returns nothing, and so does one with none.
- **Runner:**
  - A `prior_result` hit completes `succeeded` and starts no job. This
    is the double-upload guard and gets the delete-the-fix drill.
  - `render_upload` completes on job success without waiting for a sync.
  - `shot_detect` still waits.
- **Job body** (render and upload monkeypatched at their seams):
  - It renders, then uploads with `command_id`, and the result carries
    the record.
  - A failed upload fails the job with both halves named.
  - A cancel between the two steps uploads nothing.
- **The render wrapper:** `_run_match_export` itself is unchanged; the
  `render_upload` body calls it through a handle wrapper that captures its
  result. The existing match-export tests stay green, including the
  chained-upload ones, and one in-process test runs a whole
  `render_upload` job (encoder and YouTube client faked) from the
  desktop's command start to its result.
- **SPA:**
  - The desktop-mode request body carries `youtube_upload: true` and
    `output_format: "mp4"`, typed `Required<...>` as in
    `api.exportBodies.test.ts`.
  - The YouTube connect row and the playlist picker are absent in desktop
    mode.
  - Row derivation covers each state.
- **By hand, recorded in the PR:**
  - Setup: a scratch `SPLITSMITH_HOME` and the seeded `--media` demo
    match synced to staging. Never the real `~/.splitsmith`.
  - Happy path: request through the hosted API and watch the desktop
    claim, render, upload an unlisted test video and complete.
  - Crash after the upload: block the completion call, kill the desktop,
    restart it, and confirm the command completes with the same video id.
  - Read the video back through `videos.list`, then delete it.

## Slices

1. **Hosted:** the kind, request validation, the same-token re-claim
   rule. Inert until a desktop runs it; an older desktop fails it with
   the update message.
2. **Desktop:** the `render_upload` job (the match export run unchanged
   through a wrapper that captures its result),
   `prior_result`, `command_id` on the upload record, the runner's
   per-kind completion.
3. **Phone:** the Export page's desktop mode and the request rows.

## Risks

- **The phone cannot see the desktop's channel before the upload.** The
  result names it afterwards. Reporting the connected channel on claim
  or with presence is a possible follow-up.
- **A desktop that claimed and never returns holds the command.** It
  waits with no expiry (the queue spec's decision) until the user cancels
  it; a cancel on a lapsed lease ends it at once, so the next request for
  the shooter is a new command. That is the price of never uploading twice from two machines. The
  row's heartbeat time shows when it was last worked on.
