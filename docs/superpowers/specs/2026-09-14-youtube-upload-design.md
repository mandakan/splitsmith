# Direct YouTube upload, phase 1: local CLI and local UI (issue #1000)

Approved in chat 2026-09-14. Phase 1 covers the desktop install only: the
CLI and the local FastAPI server the SPA talks to. Hosted upload (phase 2
in #1000) is out of scope; the engine module is written so phase 2 adds
token storage and a job wrapper, nothing else.

## Two corrections to #1000

Both change the design and are recorded here so nobody re-derives the
old plan from the issue text.

1. **The private lock applies to every unaudited project, personal ones
   included.** `videos.insert` docs: "All videos uploaded via the
   videos.insert endpoint from unverified API projects created after 28
   July 2020 will be restricted to private viewing mode", lifted only by
   the YouTube API Services audit. The issue's "does not apply when a
   local user supplies their own OAuth client" is wrong. There is
   therefore no per-user client and no paste-your-client-JSON path: one
   splitsmith Google Cloud project, its client id built into the app.
2. **Quota is 100 `videos.insert` calls per day per project**, a separate
   bucket from the 10,000-unit pool. The 1,600-units-per-upload figure is
   stale. The quota extension form is not urgent.

What the owner files, independently of the code: the OAuth consent-screen
brand verification (leaves "Testing" status, which caps consent to 100
listed test users and expires refresh tokens after 7 days; sensitive
scopes only, so no security assessment) and the YouTube API Services
Audit and Quota Extension form (lifts the private lock). Until the audit
passes, every upload lands private and stays private; the whole flow can
still be exercised end to end by a listed test user.

*Update 2026-09-17:* both passed. The consent screen goes straight to
consent, and a test upload on 2026-09-18 requested ``unlisted`` came
back ``unlisted`` through ``processed`` (then deleted), so the private
lock is lifted. The 100-uploads-per-day quota still applies.

## Decisions

| Question | Decision |
|---|---|
| OAuth client | Built-in id/secret (installed-app secrets are non-confidential by Google's definition), overridable by `SPLITSMITH_YOUTUBE_CLIENT_ID` / `SPLITSMITH_YOUTUBE_CLIENT_SECRET`. Constants are empty until the shipped id exists; with no client configured, Connect reports that instead of opening a broken consent page. |
| Token storage | `~/.splitsmith/youtube.json` via `user_config` (atomic write, honours `SPLITSMITH_HOME` and `SPLITSMITH_DISABLE_USER_CONFIG`). Refresh token only; access tokens live in memory. Same trust level as `hosted_token` in `config.yaml`. |
| Scope | `https://www.googleapis.com/auth/youtube.force-ssl`, alone. It covers `videos.insert`, `captions.insert`, `thumbnails.set` and `channels.list`; adding `youtube.upload` adds a consent line and no capability. |
| Where "Connect" lives in the UI | Inline on the Export page's YouTube row. Local mode has no settings page (`Account` redirects to `/pick` there) and the feature means nothing anywhere else. |
| How an upload is triggered in the UI | Both entry points onto one background job: an "Upload to YouTube" action on an export-history row, and an "Upload after render" control on the export form that chains the job onto the export job. |
| Options in phase 1 | Privacy (`unlisted` default, `private`, `public`). Captions and thumbnail are sent whenever the files exist beside the MP4. Playlists and `publishAt` deferred. |
| Where the video id is recorded | The `-youtube.json` sidecar beside the MP4, only. Both the CLI and the UI paths have it, and a re-render overwrites it, which is exactly when a new upload must be allowed again. The UI history reads it per request; no second copy on the export-run record. |

## Engine: `src/splitsmith/youtube/`

A package, `httpx` only, no Google client library, nothing under it
imports FastAPI or typer.

### `oauth.py`

- `OAuthClient(client_id, client_secret)` with `OAuthClient.configured()`
  reading the env overrides then the constants; `is_configured` is false
  when either is empty.
- `YouTubeConnection` (pydantic): `schema_version`, `refresh_token`,
  `channel_id`, `channel_title`, `connected_at`, `scopes`. Stored as
  `youtube.json` in `user_config.user_config_dir()`; `load_connection()`,
  `save_connection()`, `clear_connection()` follow the `scoreboard.json`
  accessors (reads never create files, disabled mode returns `None` and
  writes are no-ops).
- `LoopbackListener`: a one-shot HTTP server bound to `127.0.0.1` on an
  OS-assigned port. `start()` returns the redirect URI; `wait(timeout)`
  returns the captured `code` or raises. It accepts exactly one request,
  checks the `state` it was given, answers a small "you can close this
  tab" page, and shuts down. Bound to loopback only; anything else is a
  bug.
- `authorize_url(client, redirect_uri, state, code_challenge)` builds the
  Google consent URL (`access_type=offline`, `prompt=consent` so a
  refresh token is issued every time, PKCE S256).
- `exchange_code(client, http, code, redirect_uri, code_verifier) ->
  TokenResponse` and `refresh_access_token(client, http, refresh_token)
  -> str`. A refresh answered with `invalid_grant` raises
  `ReauthorizeError` and the caller clears the stored connection.
- `connect(client, http, *, open_browser, timeout) -> YouTubeConnection`:
  the whole flow, ending with `channels.list?mine=true` for the channel
  title, then `save_connection`. `open_browser` is a callable so the CLI
  passes `webbrowser.open` and the server passes a no-op (the SPA opens
  the URL itself).

### `client.py`

`YouTubeClient(http: httpx.Client, token_provider)` where the provider
returns a bearer token and can be told to refresh. Every request retries
once on 401 after a refresh.

- `my_channel() -> Channel(id, title)`.
- `start_resumable_upload(metadata: VideoMetadata, size: int,
  content_type="video/mp4") -> str`: `POST
  /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status`
  with `X-Upload-Content-Length` / `-Type`; returns the session URL from
  the `Location` header.
- `upload_bytes(session_url, path, *, chunk_size=8 MiB, progress,
  check_cancel) -> str`: PUTs the file in chunks with `Content-Range`.
  On a transport error or a 5xx it asks the session where it is (`PUT`
  with `Content-Range: bytes */<size>`, expecting 308 and a `Range`
  header) and continues from the byte after the acknowledged range;
  exponential backoff, at most `max_attempts` (default 8) consecutive
  failures. `check_cancel` is called between chunks so a job cancel
  lands at a chunk boundary. Returns the video id from the final 200/201
  body. `progress(sent, total)` is called after each acknowledged chunk.
- `insert_caption(video_id, srt_path, *, language="en", name="Shots")`:
  multipart `captions.insert` with `part=snippet`.
- `set_thumbnail(video_id, jpg_path)`: `thumbnails.set`.
- Errors: `YouTubeError` base; `NotConnectedError`, `ReauthorizeError`,
  `QuotaExceededError` (403 with reason `quotaExceeded` or
  `uploadLimitExceeded`), `UploadFailedError` (everything else, with the
  API's `error.message` when present).

### `upload.py`

- `UploadRecord` (pydantic, the sidecar's new optional `upload` field):
  `video_id`, `url` (`https://youtu.be/<id>`), `privacy`, `uploaded_at`,
  `channel_title`, `captions_uploaded: bool`, `thumbnail_set: bool`,
  `notes: list[str]`.
- `YouTubeSidecar.upload: UploadRecord | None = None`. The model is
  `extra="forbid"`, so this is the only way the field can exist.
- `upload_export(mp4: Path, *, client: YouTubeClient, privacy, again=False,
  progress, check_cancel) -> UploadRecord`:
  1. Reads `<stem>-youtube.json` beside the MP4. Missing: `UploadFailedError`
     naming the file; the sidecar is the metadata, there is no fallback.
  2. Refuses with `AlreadyUploadedError(record)` when `upload` is set and
     `again` is false.
  3. Builds `VideoMetadata` from the sidecar: title, description (chapter
     lines already embedded), tags, `categoryId` from a name-to-id table
     (`Sports` = 17; unknown names fall back to 17 with a note),
     `privacyStatus`, `selfDeclaredMadeForKids=false`.
  4. Resumable insert, then `captions.insert` if the `.srt` beside the MP4
     exists, then `thumbnails.set` if `-thumbnail.jpg` exists. A caption
     or thumbnail failure becomes a note on the record, not a failure:
     the video is up, and a channel without phone verification cannot set
     a thumbnail at all.
  5. Writes the record into the sidecar (`write_sidecar`, atomic) and
     returns it.

Cancellation between the insert and the sidecar write leaves a video on
the channel with no local record. The job reports the video id in its
failure message so the user can find it; a retry with `again` is the
recovery. Not worth a two-phase write.

## CLI

New typer group `splitsmith youtube` in `src/splitsmith/youtube/cli.py`,
registered from `cli.py` beside `match`.

- `youtube login`: opens the consent page (prints the URL as well, for a
  headless shell), waits for the callback (5 minute timeout), prints the
  channel title. Exit 2 when no client is configured, with the env var
  names.
- `youtube status`: channel title and `connected_at`, or "not connected".
- `youtube logout`: deletes the stored connection; a Google-side revoke
  (`oauth2.googleapis.com/revoke`) is attempted and its failure ignored.
- `youtube upload <mp4> [--privacy unlisted|private|public] [--again]`:
  `upload_export` with a rich progress bar; prints the URL and any notes
  (`soft_wrap=True`, they are one-line facts the user copies). Exit codes:
  2 not connected or no sidecar, 1 upload failed, 3 already uploaded
  (prints the existing URL and how to re-upload).
- `match export --youtube-upload [--youtube-privacy ...]`: implies
  `--youtube-sidecar`; exit 2 with `--format` other than `mp4`. Runs after
  the render and the `--output` rename, so the sidecar it reads is the one
  beside the final file.

`docs/COMMANDS.md` gets the four verbs and the flag.

## Local server

### `ui/youtube_api.py`

Mounted like `device_auth_api`, gated the other way: `_local_gate()`
raises 404 in hosted mode. Phase 2 removes the gate.

- `GET /api/settings/youtube -> {configured, connected, channel_title,
  connected_at}`.
- `POST /api/settings/youtube/connect/start -> {auth_url, expires_at}`:
  starts a `LoopbackListener` and a background thread running `connect`
  with `open_browser` a no-op. One pending attempt at a time; a second
  start cancels the first. 409 when not configured.
- `GET /api/settings/youtube/connect/status -> {state: "idle" | "pending"
  | "connected" | "failed", channel_title, error}`. The SPA polls this
  every 2 s while pending.
- `DELETE /api/settings/youtube/session`: `clear_connection` plus the
  best-effort revoke.
- `POST /api/shooters/{slug}/exports/youtube-upload {filename, privacy,
  again}` -> Job snapshot. `filename` is confined to the shooter's
  `exports/` dir exactly as `download_export_file` confines it; it must
  end in `.mp4` and its sidecar must exist (400 otherwise). 409 when not
  connected.

### Job kind `youtube_upload`

Body `_run_youtube_upload(handle, *, slug, filename, privacy, again)`:
builds the client from the stored connection, calls `upload_export` with
`progress` mapped onto `handle.update(progress=sent/total,
message="Uploading 412 MB of 1.9 GB")` and `check_cancel=handle.check_cancel`,
and sets `result={video_id, url, notes}`. `AlreadyUploadedError` fails the
job with the existing URL in the message. Registered next to
`match_export` in `_register_job_bodies`.

`MatchExportRequest` gains `youtube_upload: bool = False` and
`youtube_privacy: Literal["unlisted", "private", "public"] = "unlisted"`.
`youtube_upload` requires `youtube_sidecar` and `output_format == "mp4"`
(422 otherwise, validated on the model). `_run_match_export` chains the
upload job at its end when set, with the same
`asyncio.run(state.jobs.submit(...))` idiom `_run_trim` uses to chain
`shot_detect`. The chain is the last statement after the run record is
written, so an export whose upload fails still has its history row.

### History enrichment

`list_export_runs` adds `youtube: {video_id, url, privacy, uploaded_at}
| null` to each run that lists a `-youtube.json` artifact, read from that
sidecar per request, the way `available` is derived per request. A
sidecar that fails to parse reads as `null`; the history never 500s over
bookkeeping. In hosted mode the sidecar may not be on local disk; the
read goes through `export_storage.pull_export_file` (a no-op locally) so
phase 2 inherits it.

## SPA

- `lib/api.ts`: `YouTubeSettings`, `YouTubeConnectStatus`, `ExportRun.
  youtube`, the five calls, and the two new `MatchExportRequest` fields
  in `submitExport`.
- `lib/youtubeRows.ts` (pure, tested): `uploadableArtifact(run)` (the MP4
  whose `-youtube.json` sibling is in the run's artifacts and available),
  `youtubeLink(run)`, `uploadLabel(run)` ("Upload to YouTube" / "Upload
  again"). The history component maps these to primitives.
- `components/export/YouTubeConnect.tsx`: the Export page's YouTube row
  states. Not configured: one muted line. Not connected: "Connect
  YouTube" (`default` variant; the page's one primary stays on Export),
  which calls `connect/start`, opens `auth_url` in a new tab, polls
  `connect/status`, and settles on "Connected as <channel>" with
  Disconnect in a `Menu`. Connected and `renderedMp4 && youtube`: an
  "Upload after render" `Segmented` (Off / Unlisted / Private / Public)
  that sets `youtube_upload` and `youtube_privacy`. A failed attempt
  shows the reason in `--color-destructive` text and the Connect button
  again.
- `ExportHistory`: for a run with an uploadable artifact and no
  `youtube`, an "Upload to YouTube" action that submits the job with
  the privacy the form's "Upload after render" control shows when it is
  not Off, else `unlisted`; the progress strip
  shows it like any other job. With `youtube` set: the `youtu.be/<id>`
  link and "Upload again" in a `Menu`. The list refetches when a
  `youtube_upload` job finishes, the way it already refetches after
  `match_export`.
- Every count and time through `numeral`; no new colours; no issue
  numbers in copy.

## Tests

- `tests/test_youtube_client.py` (`respx`): chunk boundaries at exactly
  `chunk_size` and one byte over; a transport error mid-chunk followed by
  a 308 with `Range` and a resumed PUT from the right offset; a 5xx
  followed by resume; `max_attempts` exhausted; 401 then one refresh then
  success; `invalid_grant` on refresh raises `ReauthorizeError`; a 403
  `quotaExceeded`; `check_cancel` raising between chunks stops the
  upload with no further request.
- `tests/test_youtube_oauth.py`: `LoopbackListener` driven by a real
  `httpx` GET to its port with the right and the wrong `state`; the
  connection accessors under `SPLITSMITH_HOME=tmp_path` and under
  `SPLITSMITH_DISABLE_USER_CONFIG`; `connect` with token exchange mocked.
- `tests/test_youtube_upload.py`: fake client; sidecar missing, already
  uploaded, `again`, captions and thumbnail present / absent / failing
  (notes), category mapping, the sidecar written back and re-loadable.
- `tests/test_youtube_cli.py`: the four verbs and `match export
  --youtube-upload` with the client monkeypatched; every exit code above.
- `tests/test_youtube_api.py`: the settings routes, hosted-mode 404,
  the upload route's path confinement and 400/409 cases, the job body's
  progress and result, chaining from `match_export` (assert a
  `youtube_upload` job was submitted with the request's privacy), and
  history enrichment including an unparseable sidecar.
- SPA: vitest for `youtubeRows.ts`, `YouTubeConnect` (all four states,
  the poll settling), and the history row actions; the existing
  `submitExport` payload test extended with the two fields.
- Visual: seed the demo match, connect against a monkeypatched
  `connect`, screenshot the row in each state with Playwright, and look
  at the frames before calling it done.

Each new test is checked against the pre-change code where a fix is
claimed (delete the behaviour, watch it fail).

## Packaging

| PR | Contents |
|---|---|
| A | `youtube/oauth.py`, `youtube/client.py`, `youtube/upload.py`, the sidecar field, `splitsmith youtube` verbs, `match export --youtube-upload`, `COMMANDS.md` |
| B | `ui/youtube_api.py`, the job kind, request fields and chaining, history enrichment, the SPA |

A ships alone and is usable from the shell; B depends on A.

## Not in this spec

Playlists, `publishAt`, hosted mode (phase 2 of #1000), Brand Account /
multi-channel switching, YouTube Analytics.
