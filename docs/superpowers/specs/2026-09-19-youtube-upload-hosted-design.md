# Direct YouTube upload, phase 2: hosted mode (issue #1000)

Written 2026-09-19, two days after Google's OAuth verification and the
YouTube API audit passed (see the 2026-09-17 update in the phase 1 spec,
`2026-09-14-youtube-upload-design.md`). Phase 1 covered the desktop
install: the CLI and the local FastAPI server. This phase makes the same
surface work when `SPLITSMITH_MODE=hosted`: one YouTube connection per
account, stored in Postgres, used by the upload job wherever it runs.

## What changes and what does not

The engine package `splitsmith.youtube` is untouched except for two
seams it needed anyway: `oauth.build_connection` (the tail of `connect`,
so a redirect flow can share it) and an `on_reauthorize` hook on
`AccessTokenProvider` (so an `invalid_grant` clears the row instead of
the file). `upload_export`, the resumable client, the sidecar record and
the CLI are as in phase 1. The SPA's `YouTubeConnect` component, its
polling and the `connect/start` -> `connect/status` shape are kept: the
hosted server answers the same three routes, only what happens behind
them differs.

## Decisions

| Question | Decision |
|---|---|
| Token storage | `users.youtube_connection`, one nullable JSON column, one connection per account (the `scoreboard_identity` precedent). The refresh token is sealed with Fernet (`cryptography`, new runtime dependency, decided 2026-09-19) under `SPLITSMITH_YOUTUBE_TOKEN_KEY`. The channel id, title, `connected_at` and scopes are plain. |
| Who holds the key | The API process and every worker: the Railway worker through its own env, a home agent through the registration bundle (`credentials.youtube`), which also carries the OAuth client id and secret. A worker without the key fails the upload job with a message naming the variable. |
| Consent flow | Google's redirect flow, not the loopback listener. `connect/start` writes `{state, code_verifier, started_at}` under `pending` in the same JSON column (it must survive a second API replica) and returns the consent URL whose `redirect_uri` is `<SPLITSMITH_PUBLIC_URL>/api/settings/youtube/callback`. The callback is a top-level GET carrying the session cookie (SameSite=Lax allows it), so the auth gate pins the tenant and the route knows whose `pending` to check. It exchanges the code, looks up the channel, seals the token, writes the connection, clears `pending`, and answers a small HTML page. A failure is written as `pending.error` and shown on that page; `connect/status` reports it as `failed` so the SPA's poll settles exactly as it does locally. |
| Redirect URI registration | The owner adds `https://my.splitsmith.app/api/settings/youtube/callback` and the staging equivalent to the OAuth client in the Google Cloud console. Not a code change; recorded in `docs/saas-readiness/11-environment-strategy.md`. |
| `configured` hosted | True only when the OAuth client **and** the token key are present. With either missing, Connect is a muted line, never a consent page that would end in a 500. |
| Where Connect lives | Two mounts of one component. The Export page's YouTube row (phase 1) now renders in both modes. The Account page gets a YouTube section (`components/account/YouTubeSection.tsx`) with the same connect / connected / disconnect states and no upload options. The login hook (`start`, open the URL, poll) moves out of `YouTubeConnect` into `components/export/useYouTubeLogin.ts` so both mounts share it. |
| Upload job hosted | `run_youtube_upload` pulls the MP4, sidecar, `.srt` and thumbnail through `export_storage.pull_export_file` before uploading (a no-op locally, and for files already on disk), and pushes the rewritten sidecar back afterwards so the history route on the API sees the record. The route's pre-flight checks use `storage.exists` for the MP4 hosted instead of the local `exists()`. |
| Local mode | Unchanged: the JSON file under the user config dir, the loopback listener, the thread. `callback` answers 404 there. |

## Store: `db/youtube_connections.py`

`PostgresYouTubeConnectionStore(session_factory, user_id=)`, the
`PostgresProfileStore` pattern (fail loud on an empty user id, every
statement filters on `User.id == user_id`, one isolation test per
method).

- `get() -> StoredYouTubeConnection | None`: the connection block, or
  `None` when the column is null, has no `connection` key, or fails to
  validate (logged, read as not connected).
- `set(conn: StoredYouTubeConnection)`: writes `connection`, keeps
  `pending`.
- `clear()`: drops `connection`, keeps `pending`.
- `set_pending(p: PendingLogin)`, `get_pending() -> PendingLogin | None`,
  `clear_pending()`.

`StoredYouTubeConnection` is `YouTubeConnection` with
`refresh_token_sealed` in place of `refresh_token`; `sealed.open` turns it
back into a `YouTubeConnection` for `build_client`. `PendingLogin` is
`{state, code_verifier, started_at, error: str | None}`.

## Routes (`ui/youtube_api.py`)

`_local_gate` goes. A helper `_hosted_store(request)` returns the
tenant's store or `None`; each route branches on it.

- `GET /api/settings/youtube`: hosted reads the store; `configured` as
  above.
- `POST connect/start`: hosted writes `pending`, no thread, and returns
  `auth_url` + `expires_at` (`started_at` + 10 minutes). 409 when not
  configured.
- `GET connect/status`: hosted derives it: `pending.error` -> `failed`
  (with the reason); `pending` younger than the timeout -> `pending`;
  a connection -> `connected`; else `idle`.
- `GET /api/settings/youtube/callback?code=&state=`: hosted only.
  Wrong or missing `state`, or no `pending`: `pending.error` is set and
  the page says so. Otherwise exchange, channel, seal, store. Runs the
  blocking httpx calls in a threadpool. Always answers HTML 200 so the
  tab the SPA opened reads as a page, not a JSON blob.
- `DELETE session`: hosted revokes (best effort) and clears the row.
- `GET playlists` and `POST .../youtube-upload`: build the client from
  the store hosted; 409 when not connected, unchanged shape.

## Worker plumbing

- `state.worker_credentials["youtube"]` is `{client_id, client_secret,
  token_key}` when all three are set on the API, else `None`.
- `agent.apply_credentials` puts them in the env with `setdefault`, so a
  box-local override wins. An agent registered before this ships has no
  `youtube` key and keeps working for every other kind; the upload job on
  it fails with the variable's name, and re-registering fixes it.
- `docs/self-hosted-workers.md` gets a paragraph.

## SPA

- `components/export/useYouTubeLogin.ts`: `{pending, error, connect,
  cancel}` over `api.startYouTubeConnect` and `api.youtubeConnectStatus`
  with the same 2 s poll and 10 minute limit.
- `YouTubeConnect.tsx` uses the hook; `DetailsGroup` and `Export.tsx`
  drop their `!hosted` guards on the row and on the history actions.
- `components/account/YouTubeSection.tsx`: a section like
  `DesktopTokensSection` with one `Field` ("Channel"). Mounted on
  `Account.tsx` under Profile.
- `api.ts` doc comments: the settings routes are no longer local-only.

## Tests

- `tests/test_youtube_sealed.py`: round trip; a different key fails;
  a missing env var is `NotConfiguredError`.
- `tests/test_youtube_connection_store.py`: each method, and two users
  through the same engine never see each other's connection or pending.
- `tests/test_youtube_api_hosted.py` over `hosted_app` + `login`:
  settings unconfigured without the key; start writes `pending` and the
  URL carries the public callback; status is `pending`; a callback with
  the wrong state settles `failed` with a reason; the right state (token
  exchange and channel lookup monkeypatched) settles `connected`, the
  settings show the channel, and the row holds no plaintext refresh
  token; delete clears; the callback 404s locally; the routes now answer
  hosted (the phase 1 404 test is rewritten to that); the upload route
  409s hosted when not connected and queues when connected; the job body
  pulls the files and pushes the sidecar (pull/push monkeypatched at the
  module); two users are isolated end to end.
- `tests/test_agent.py`: the `youtube` bundle lands in the env and does
  not overwrite a set variable.
- The migration is checked by the existing "migrations apply + match the
  models" job through `schema_diff`.
- SPA: `useYouTubeLogin` (start, settle connected, settle failed, time
  out), `YouTubeSection` (each state), `DetailsGroup` renders the row
  hosted.
- Visual: the Account section and the Export row screenshotted hosted.

## Not in this spec

Brand Account / channel switching, a hosted upload quota meter (the
100-per-day project cap is shared across every hosted user; a 429 from
the playlists or upload route is the only signal today), YouTube
Analytics.
