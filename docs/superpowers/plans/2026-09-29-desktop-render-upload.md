# Desktop render-and-upload command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the phone ask the desktop to render a shooter's match video and upload it to YouTube, through the shipped desktop command queue, with the video link shown back on the phone.

**Architecture:** A second command kind, `render_upload`, on the shipped `desktop_commands` table, routes and runner. Hosted validates and stores the request; the desktop claims it, checks it has not already uploaded it (sidecar `command_id`), runs one local `render_upload` job (the match export with its chained upload off, then the upload in-process), and completes with `{video_id, url, channel_title}`. The phone's Export page on a mirror becomes a "Render on desktop" form.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, Pydantic, pytest (xdist; `-n0` for focused runs); React + TypeScript, vitest, corepack pnpm.

**Spec:** `docs/superpowers/specs/2026-09-28-desktop-command-render-upload-design.md`, an addendum to `docs/superpowers/specs/2026-09-28-desktop-command-queue-design.md`. Read both.

## Global Constraints

- Three PRs, in order: PR 1 = Tasks 1-2 (hosted), PR 2 = Tasks 3-6 (desktop), PR 3 = Tasks 7-10 (phone). Do not stack PRs: open the next branch from `main` after the previous PR merges.
- Kind name: `render_upload`. Wire result: `{video_id, url, channel_title}`.
- Lease: unchanged, 10 min (`db/desktop_commands.LEASE`). No expiry: commands wait until cancelled.
- A lapsed `render_upload` lease is re-claimable only by the same desktop token (`claimed_by`).
- A failed upload after a good render fails the command with text starting `Rendered on the desktop, but the upload failed: `.
- Refusal when the desktop has no YouTube connection: `YouTube is not connected on the desktop`.
- Phone copy for the YouTube row in desktop mode: `Uploads to the YouTube account connected on your desktop.`
- The phone's primary button label in desktop mode: `Render on desktop`.
- `uv` only, never `pip`. Black line length 110, ruff clean. Type hints everywhere.
- `uv run` can rewrite `uv.lock`: never `git add -A`; add the files you changed by name.
- SPA: build only with `components/ui` primitives; one primary per view; no issue numbers in UI copy; no `text-[...]` outside `components/ui`.
- Each new test must fail against the pre-change code. For the tests marked **drill**, delete or revert the fix, run the test, see it fail, restore the fix.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. PR bodies end with the Claude Code line from the session's attribution rule.

## Review Focus

1. **A second desktop and a lapsed `render_upload` it may not claim.** If `pending_commands` counted it, that desktop would see "commands due" every poll and sync every minute forever. Expect: the count is per token and excludes it. Pinned in Task 1.
2. **The upload finished, the completion call was lost, the desktop restarted.** Expect: the re-claim completes with the existing video and nothing renders or uploads. Pinned in Task 5 (drill).
3. **Cancel between the render and the upload.** Expect: no upload starts, the command reads cancelled. Pinned in Task 6.
4. **The upload fails after a good render.** Expect: failed, with both halves named, and the MP4 left in exports. Pinned in Task 6.
5. **A request whose pads exceed the desktop project's trim buffers.** The phone cannot check this (a mirror has no sources, and hosted must not run the source check). Expect: the desktop fails the command with the same message the local route gives. Pinned in Task 6.

Accepted and not tested: a sidecar written with `command_id` is unreadable to an older app (`UploadRecord` has `extra="forbid"`) after a downgrade. Only that export's history row is affected.

---

## PR 1: hosted

Branch: `feat/1100-render-upload-hosted` from `main`.

### Task 1: Store: the kind, the same-token re-claim rule, per-token counts

**Files:**
- Modify: `src/splitsmith/db/desktop_commands.py` (`COMMAND_KINDS`, `_claimable`, `pending_counts`, `claim`)
- Modify: `src/splitsmith/ui/sync_api.py:470-500` (`get_fingerprints` passes the token)
- Modify: `docs/superpowers/specs/2026-09-28-desktop-command-render-upload-design.md` (corrections)
- Test: `tests/test_desktop_commands.py`

**Interfaces:**
- Produces: `COMMAND_KINDS = frozenset({"shot_detect", "render_upload"})`; `PINNED_KINDS = frozenset({"render_upload"})`; `DesktopCommandStore.pending_counts(*, token_id: str | None = None, now: datetime | None = None) -> dict[str, int]`; `claim(...)` signature unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_desktop_commands.py`:

```python
def _render_upload(client: TestClient):
    return client.post(
        PHONE,
        json={
            "kind": "render_upload",
            "slug": SLUG,
            "args": {
                "request": {
                    "stage_numbers": [1],
                    "output_format": "mp4",
                    "youtube_sidecar": True,
                    "youtube_upload": True,
                }
            },
        },
    )


def test_a_lapsed_render_upload_goes_back_only_to_the_desktop_that_held_it(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """Re-running an upload on a second machine would publish it twice."""
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _render_upload(client).json()["id"]
    store = _store(client)
    now = datetime.now(UTC)
    later = now + timedelta(minutes=11)
    assert [c.id for c in asyncio.run(store.claim([MATCH], token_id="tok-a", now=now))] == [command_id]

    assert asyncio.run(store.claim([MATCH], token_id="tok-b", now=later)) == []
    # Counted only for the desktop that may take it: another desktop that
    # saw it would sync every poll and claim nothing, forever.
    assert asyncio.run(store.pending_counts(token_id="tok-b", now=later)) == {}
    assert asyncio.run(store.pending_counts(token_id="tok-a", now=later)) == {MATCH: 1}
    again = asyncio.run(store.claim([MATCH], token_id="tok-a", now=later))
    assert [c.id for c in again] == [command_id]


def test_a_lapsed_shot_detect_is_still_claimable_by_any_desktop(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    """The pin is per kind: a re-detect is safe anywhere (its revision
    guard refuses a doubled run)."""
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _request(client).json()["id"]
    store = _store(client)
    now = datetime.now(UTC)
    later = now + timedelta(minutes=11)
    asyncio.run(store.claim([MATCH], token_id="tok-a", now=now))
    assert asyncio.run(store.pending_counts(token_id="tok-b", now=later)) == {MATCH: 1}
    assert [c.id for c in asyncio.run(store.claim([MATCH], token_id="tok-b", now=later))] == [command_id]


def test_a_waiting_render_upload_is_claimable_by_any_desktop(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    command_id = _render_upload(client).json()["id"]
    store = _store(client)
    assert asyncio.run(store.pending_counts(token_id="tok-b")) == {MATCH: 1}
    assert [c.id for c in asyncio.run(store.claim([MATCH], token_id="tok-b"))] == [command_id]
```

The first test will fail at `_render_upload` (the route refuses the kind) until Task 2 lands. Make it fail on the store instead by adding the kind first (Step 3), then these tests fail on the pin.

- [ ] **Step 2: Add the kind only, run, see the pin tests fail**

In `src/splitsmith/db/desktop_commands.py` change `COMMAND_KINDS` to `frozenset({"shot_detect", "render_upload"})`. The request route (server.py `request_desktop_command`) checks `req.kind not in COMMAND_KINDS`, then only special-cases `shot_detect`, so a `render_upload` is now stored with its raw args. That is enough for these store tests; Task 2 adds its validation.

Run: `uv run pytest -n0 tests/test_desktop_commands.py -k "render_upload or any_desktop" -v`
Expected: `test_a_lapsed_render_upload_goes_back_only_to_the_desktop_that_held_it` FAILS (`tok-b` claims it). The other two pass.

- [ ] **Step 3: Implement the pin and the per-token count**

In `src/splitsmith/db/desktop_commands.py`, after `LEASE`:

```python
#: Kinds whose lapsed lease goes back only to the desktop that held it.
#: Re-running one elsewhere would repeat a side effect hosted cannot see
#: (a YouTube upload); the holder checks its own record before re-running.
PINNED_KINDS = frozenset({"render_upload"})
```

Replace `_claimable`:

```python
    def _claimable(self, now: datetime, token_id: str | None) -> Any:
        lapsed = and_(DesktopCommandRow.status == "claimed", DesktopCommandRow.lease_expires_at < now)
        # ``claimed_by == NULL`` is never true in SQL, so a caller without a
        # token id never re-claims a pinned kind.
        holder = or_(
            DesktopCommandRow.kind.not_in(PINNED_KINDS),
            DesktopCommandRow.claimed_by == token_id,
        )
        return or_(DesktopCommandRow.status == "pending", and_(lapsed, holder))
```

Change `pending_counts`:

```python
    async def pending_counts(
        self, *, token_id: str | None = None, now: datetime | None = None
    ) -> dict[str, int]:
        """``match_id -> commands this desktop could claim now``, for the
        fingerprint poll. Per token: a pinned command another desktop holds
        must not wake this one (it would sync every poll and claim
        nothing). Matches with none are absent."""
        now = now or datetime.now(UTC)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(DesktopCommandRow.match_id, func.count(DesktopCommandRow.id))
                    .where(self._mine(), self._claimable(now, token_id))
                    .group_by(DesktopCommandRow.match_id)
                )
            ).all()
        return {str(m): int(c) for m, c in rows}
```

In `claim`, change both `self._claimable(now)` calls to `self._claimable(now, token_id)`.

In `src/splitsmith/ui/sync_api.py` `get_fingerprints`, change the pending line to:

```python
    pending = await _command_store(request).pending_counts(token_id=getattr(user, "token_id", None))
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -n0 tests/test_desktop_commands.py tests/test_sync_api.py -v`
Expected: all PASS, including the shipped `test_an_expired_lease_is_claimable_again` (a `shot_detect`, token `None`).

- [ ] **Step 5: Drill**

Revert `holder` to `DesktopCommandRow.kind.not_in(PINNED_KINDS) | True` (any), run the first new test, see it fail, restore.

- [ ] **Step 6: Spec corrections**

In the addendum spec, make these edits so it matches the code this plan builds:
- "Claim" paragraph: add "The fingerprint count is per token too: `pending_commands` leaves out a pinned command another desktop holds, or that desktop would sync every poll and claim nothing."
- "The job" step 1: replace the `_run_match_export` split with "The `render_upload` body runs `_run_match_export` unchanged through a handle wrapper that captures its `set_result` payload (`fcpxml_path`) and scales its progress; the request it passes has `youtube_upload` off, so no separate upload job is chained."
- Phone UI history rows: replace "status as a neutral `Chip`" with "the shipped `DesktopCommandLine` (icon, line, Cancel), with the video link on success".

- [ ] **Step 7: Commit**

```bash
git add src/splitsmith/db/desktop_commands.py src/splitsmith/ui/sync_api.py tests/test_desktop_commands.py docs/superpowers/specs/2026-09-28-desktop-command-render-upload-design.md
git commit -m "feat(sync): render_upload command kind, pinned to its desktop on re-claim (#1100)"
```

### Task 2: Request route validates a render-upload

**Files:**
- Modify: `src/splitsmith/ui/server.py` (`request_desktop_command`, near line 15860)
- Test: `tests/test_desktop_commands.py`

**Interfaces:**
- Consumes: `MatchExportRequest` (`ui/exports_api.py:91`, already imported in server.py at line 261).
- Produces: stored `args == {"request": <MatchExportRequest.model_dump(mode="json")>}`, `slug` set, `stage_number` null, `expected_revision` null.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_render_upload_is_queued_with_its_validated_request(
    hosted_app: tuple[TestClient, _CapturingSender],
) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    resp = _render_upload(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["kind"], body["slug"], body["stage_number"]) == ("render_upload", SLUG, None)
    assert body["expected_revision"] is None
    # Stored as the full, defaulted request, so the desktop renders what the
    # phone saw rather than its own defaults.
    assert body["args"]["request"]["youtube_upload"] is True
    assert body["args"]["request"]["head_pad_seconds"] is not None
    # One per shooter at a time.
    again = _render_upload(client)
    assert again.status_code == 200 and again.json()["id"] == body["id"]


def test_a_render_upload_must_upload_an_mp4(hosted_app: tuple[TestClient, _CapturingSender]) -> None:
    client, sender = hosted_app
    login(client, sender, EMAIL)
    _mirror(client)
    base = {"stage_numbers": [1], "output_format": "mp4", "youtube_sidecar": True}
    for request in (
        {**base, "youtube_upload": False},
        {**base, "output_format": "fcpxml", "youtube_upload": True},
        {"youtube_upload": True},
    ):
        resp = client.post(PHONE, json={"kind": "render_upload", "slug": SLUG, "args": {"request": request}})
        assert resp.status_code == 422, (request, resp.text)
    no_slug = client.post(PHONE, json={"kind": "render_upload", "args": {"request": {**base, "youtube_upload": True}}})
    assert no_slug.status_code == 422
```

- [ ] **Step 2: Run, see them fail**

Run: `uv run pytest -n0 tests/test_desktop_commands.py -k "render_upload_is_queued or must_upload" -v`
Expected: FAIL (args stored raw; bad requests answer 201).

- [ ] **Step 3: Implement**

In `request_desktop_command`, after the `if req.kind == "shot_detect":` block, add:

```python
        elif req.kind == "render_upload":
            if req.slug is None or req.stage_number is not None:
                raise HTTPException(status_code=422, detail="render_upload needs slug and no stage_number")
            state.shooter_project(req.slug)  # an unknown shooter 404s here, as for shot_detect
            try:
                export_req = MatchExportRequest.model_validate(args.get("request"))
            except ValidationError as exc:
                first = exc.errors()[0]
                raise HTTPException(
                    status_code=422, detail=f"render settings are not valid: {first.get('msg', 'invalid')}"
                ) from exc
            if not export_req.youtube_upload:
                raise HTTPException(status_code=422, detail="render_upload needs youtube_upload")
            # The pads and sources are checked on the desktop: a mirror has
            # no sources here, and the desktop's project owns the buffers.
            args = {"request": export_req.model_dump(mode="json")}
```

Import `ValidationError` from `pydantic` at the top of `server.py` if it is not already imported (`grep -n "ValidationError" src/splitsmith/ui/server.py`). Update the route's docstring: add "For `render_upload` the render settings are validated as a `MatchExportRequest` that uploads an MP4; the pads and sources are the desktop's to check."

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -n0 tests/test_desktop_commands.py -v`
Expected: PASS.

- [ ] **Step 5: Commit, then open PR 1**

```bash
git add src/splitsmith/ui/server.py tests/test_desktop_commands.py
git commit -m "feat(sync): validate a phone's render-and-upload request (#1100)"
uv run pytest
```

Full suite green, then push and open PR 1: title `feat(sync): render_upload command kind on hosted (#1100)`. Body: what is inert until PR 2 (an older desktop claims it and fails it with "update the desktop app"), the pin and why the count is per token. Watch `gh pr checks <n> --watch`, then squash-merge.

---

## PR 2: desktop

Branch: `feat/1100-render-upload-desktop` from `main` after PR 1 merged.

### Task 3: `command_id` on the upload record

**Files:**
- Modify: `src/splitsmith/youtube_sidecar.py:44-66` (`UploadRecord`)
- Modify: `src/splitsmith/youtube/upload.py:141-236` (`upload_export`)
- Modify: `src/splitsmith/ui/youtube_api.py:496-560` (`run_youtube_upload`)
- Test: `tests/test_youtube_upload.py`, `tests/test_youtube_sidecar.py`

**Interfaces:**
- Produces: `UploadRecord.command_id: str | None = None`; `upload_export(..., command_id: str | None = None)`; `run_youtube_upload(..., command_id: str | None = None)` whose `set_result` payload is `{"video_id", "url", "channel_title", "notes"}`.

- [ ] **Step 1: Write the failing tests**

Read `tests/test_youtube_upload.py` for its fake `Uploader` and sidecar fixture helper, and add alongside the existing happy-path test (reuse its helper names exactly):

```python
def test_the_record_carries_the_command_that_asked_for_it(tmp_path: Path) -> None:
    mp4 = _rendered(tmp_path)  # the file's existing helper that writes an MP4 and its sidecar
    record = upload_export(mp4, client=_FakeUploader(), command_id="cmd-1")
    assert record.command_id == "cmd-1"
    assert youtube_sidecar.load_sidecar(youtube_sidecar.sidecar_path_for(mp4)).upload.command_id == "cmd-1"


def test_a_record_without_a_command_reads_as_before(tmp_path: Path) -> None:
    mp4 = _rendered(tmp_path)
    assert upload_export(mp4, client=_FakeUploader()).command_id is None
```

If the helpers are named differently, use the file's names; do not add new fakes.

- [ ] **Step 2: Run, see them fail**

Run: `uv run pytest -n0 tests/test_youtube_upload.py -k command -v`
Expected: FAIL (`unexpected keyword argument 'command_id'`).

- [ ] **Step 3: Implement**

`UploadRecord`, after `notify_subscribers`:

```python
    #: The desktop command that asked for this upload, when one did. The
    #: desktop checks it before re-running a command, so a request whose
    #: completion was lost never uploads twice.
    command_id: str | None = None
```

`upload_export`: add `command_id: str | None = None` after `check_cancel` in the signature and `command_id=command_id,` in the `UploadRecord(...)` call.

`run_youtube_upload`: add `command_id: str | None = None` as the last keyword parameter, pass `command_id=command_id` to `upload_export`, and change its `set_result` to:

```python
    handle.set_result(
        {
            "video_id": record.video_id,
            "url": record.url,
            "channel_title": record.channel_title,
            "notes": record.notes,
        }
    )
```

- [ ] **Step 4: Run the YouTube tests**

Run: `uv run pytest -n0 tests/test_youtube_upload.py tests/test_youtube_sidecar.py tests/test_youtube_api.py tests/test_youtube_api_hosted.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/youtube_sidecar.py src/splitsmith/youtube/upload.py src/splitsmith/ui/youtube_api.py tests/test_youtube_upload.py
git commit -m "feat(youtube): record which desktop command asked for an upload (#1100)"
```

### Task 4: The desktop's checks: refusal and prior result

**Files:**
- Modify: `src/splitsmith/sync/commands.py`
- Test: `tests/test_desktop_command_runner.py`

**Interfaces:**
- Consumes: `UploadRecord.command_id` (Task 3); `youtube.oauth.load_connection() -> YouTubeConnection | None`.
- Produces: `RUNNABLE_KINDS = frozenset({"shot_detect", "render_upload"})`; `YOUTUBE_NOT_CONNECTED = "YouTube is not connected on the desktop"`; `prior_result(match_root: Path, command: dict) -> dict | None` returning `{"video_id", "url", "channel_title"}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_desktop_command_runner.py`, add imports `from datetime import UTC, datetime` (already there), `from splitsmith import youtube_sidecar`, `from splitsmith.match_project import MatchProject`, and extend the `splitsmith.sync.commands` import with `YOUTUBE_NOT_CONNECTED, prior_result`. Then:

```python
def _upload_command(**over) -> dict:
    base = {
        "id": "c9",
        "kind": "render_upload",
        "slug": SLUG,
        "stage_number": None,
        "args": {"request": {"stage_numbers": [1], "output_format": "mp4", "youtube_sidecar": True, "youtube_upload": True}},
        "expected_revision": None,
    }
    base.update(over)
    return base


def _sidecar(root: Path, name: str, *, command_id: str | None) -> None:
    shooter_root = match_model.Match.shooter_root(root, SLUG)
    exports = MatchProject.load(shooter_root).exports_path(shooter_root)
    exports.mkdir(parents=True, exist_ok=True)
    record = youtube_sidecar.UploadRecord(
        video_id="vid1",
        url="https://youtu.be/vid1",
        privacy="unlisted",
        uploaded_at=datetime(2026, 9, 29, tzinfo=UTC),
        channel_title="My channel",
        command_id=command_id,
    )
    sidecar = youtube_sidecar.YouTubeSidecar(title="T", description="D", upload=record)
    youtube_sidecar.write_sidecar(sidecar, exports / f"{name}-youtube.json")


def test_a_render_upload_is_refused_without_a_youtube_connection(tmp_path: Path, monkeypatch) -> None:
    root = _match(tmp_path, None)
    monkeypatch.setattr("splitsmith.sync.commands.oauth.load_connection", lambda: None)
    assert refuse_reason(root, _upload_command()) == YOUTUBE_NOT_CONNECTED
    monkeypatch.setattr("splitsmith.sync.commands.oauth.load_connection", lambda: object())
    assert refuse_reason(root, _upload_command()) is None
    assert "not in this match" in refuse_reason(root, _upload_command(slug="nobody"))


def test_prior_result_finds_this_commands_upload_only(tmp_path: Path) -> None:
    root = _match(tmp_path, None)
    assert prior_result(root, _upload_command()) is None
    _sidecar(root, "other", command_id="someone-else")
    _sidecar(root, "plain", command_id=None)
    assert prior_result(root, _upload_command()) is None
    _sidecar(root, "mine", command_id="c9")
    assert prior_result(root, _upload_command()) == {
        "video_id": "vid1",
        "url": "https://youtu.be/vid1",
        "channel_title": "My channel",
    }
    assert prior_result(root, _command()) is None  # a re-detect never has one
```

Check the `YouTubeSidecar` required fields in `youtube_sidecar.py` (`title`, `description` are required; the rest default) and `write_sidecar`'s signature before running.

- [ ] **Step 2: Run, see them fail**

Run: `uv run pytest -n0 tests/test_desktop_command_runner.py -k "render_upload or prior_result" -v`
Expected: FAIL (ImportError on `YOUTUBE_NOT_CONNECTED`).

- [ ] **Step 3: Implement**

In `src/splitsmith/sync/commands.py`:

```python
from .. import youtube_sidecar
from ..match_model import load_match_or_legacy
from ..match_project import MatchProject
from ..youtube import oauth

#: Kinds this desktop can run. Hosted may queue a kind a newer desktop
#: knows; an older one refuses it with a reason instead of guessing.
RUNNABLE_KINDS = frozenset({"shot_detect", "render_upload"})

STAGE_CHANGED = "the stage changed after this was requested; ask again"
YOUTUBE_NOT_CONNECTED = "YouTube is not connected on the desktop"
```

Split `refuse_reason` so the kind check stays first and each kind has its own branch:

```python
def refuse_reason(match_root: Path, command: dict) -> str | None:
    """Why this desktop will not run ``command``, or ``None`` to run it."""
    kind = command.get("kind")
    if kind not in RUNNABLE_KINDS:
        return f"this desktop cannot run {kind!r} requests yet; update the desktop app"
    if kind == "render_upload":
        return _render_upload_refusal(match_root, command)
    ...  # the existing shot_detect body, unchanged


def _shooter_root(match_root: Path, slug: str) -> Path | None:
    _, shooter_roots = load_match_or_legacy(match_root)
    return shooter_roots.get(slug)


def _render_upload_refusal(match_root: Path, command: dict) -> str | None:
    slug = command.get("slug")
    if not isinstance(slug, str):
        return "the request names no shooter"
    try:
        if _shooter_root(match_root, slug) is None:
            return f"shooter {slug!r} is not in this match on the desktop"
    except (OSError, ValueError):
        return "the match could not be read on the desktop"
    if oauth.load_connection() is None:
        return YOUTUBE_NOT_CONNECTED
    return None


def prior_result(match_root: Path, command: dict) -> dict | None:
    """The upload this very command already made, from its sidecar record.

    A render-upload whose completion never reached hosted is re-claimed
    after its lease lapses; this is what stops the re-run from uploading
    twice. It has to run before the render, which rewrites the sidecar
    and would drop the record."""
    if command.get("kind") != "render_upload":
        return None
    slug, command_id = command.get("slug"), command.get("id")
    if not isinstance(slug, str) or not command_id:
        return None
    try:
        root = _shooter_root(match_root, slug)
        if root is None:
            return None
        exports = MatchProject.load(root).exports_path(root)
    except (OSError, ValueError):
        return None
    for path in sorted(exports.glob("*-youtube.json")):
        try:
            record = youtube_sidecar.load_sidecar(path).upload
        except (OSError, ValueError):
            continue
        if record is not None and record.command_id == command_id:
            return {"video_id": record.video_id, "url": record.url, "channel_title": record.channel_title}
    return None
```

`pydantic.ValidationError` subclasses `ValueError`, so `except (OSError, ValueError)` covers a malformed sidecar. Refactor the existing `_audit_path` to use `_shooter_root`.

- [ ] **Step 4: Run the runner tests**

Run: `uv run pytest -n0 tests/test_desktop_command_runner.py -v`
Expected: PASS, including the shipped `"cannot run" in refuse_reason(root, _command(kind="render_export"))`.

- [ ] **Step 5: Slim import surface**

`sync/commands.py` now imports `splitsmith.youtube.oauth`. Run `uv run python scripts/ci/assert_slim_import_surface.py` (read its header for the exact invocation) and confirm it still passes.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/sync/commands.py tests/test_desktop_command_runner.py
git commit -m "feat(sync): the desktop refuses or recognises a render-upload it already ran (#1100)"
```

### Task 5: Runner: prior result first, and completion per kind

**Files:**
- Modify: `src/splitsmith/ui/command_runner.py`
- Test: `tests/test_desktop_command_runner.py`

**Interfaces:**
- Consumes: `prior_result`, `refuse_reason` (Task 4).
- Produces: `SYNCED_RESULT_KINDS = frozenset({"shot_detect"})` in `command_runner.py`. A `render_upload` completes `succeeded` with `result=dict(job.result or {})` on the tick after its job succeeds.

- [ ] **Step 1: Write the failing tests**

Extend the fake `_Api` so tests can read results, without changing the shape of `completed`:

```python
class _Api:
    def __init__(self, commands: list[dict]) -> None:
        ...
        self.results: dict[str, dict | None] = {}

    def complete(self, command_id, *, status, error=None, result=None) -> None:
        self.completed.append((command_id, status, error))
        self.results[command_id] = result
```

Then:

```python
def test_a_render_upload_already_made_completes_without_running(tmp_path: Path, monkeypatch) -> None:
    """The upload happened, its completion was lost, the desktop restarted
    and re-claimed it: report the existing video, render nothing."""
    root = _match(tmp_path, None)
    _sidecar(root, "mine", command_id="c9")
    jobs, started, synced_now = _Jobs(), [], []
    runner = _runner(jobs, started, synced_now)
    api = _Api([_upload_command()])
    runner.claim_after_sync("m1")
    asyncio.run(runner.tick(api, {"m1": root}))
    assert started == []
    assert api.completed == [("c9", "succeeded", None)]
    assert api.results["c9"]["url"] == "https://youtu.be/vid1"


def test_a_render_upload_completes_when_its_job_succeeds(tmp_path: Path, monkeypatch) -> None:
    """Its result travels in the command, so no sync is awaited."""
    root = _match(tmp_path, None)
    monkeypatch.setattr("splitsmith.sync.commands.oauth.load_connection", lambda: object())
    jobs, started, synced_now = _Jobs(), [], []
    runner = _runner(jobs, started, synced_now)
    api = _Api([_upload_command()])
    runner.claim_after_sync("m1")
    asyncio.run(runner.tick(api, {"m1": root}))
    assert started == ["c9"]
    done = _job("j1", JobStatus.SUCCEEDED, message="Uploaded")
    done.result = {"video_id": "v2", "url": "https://youtu.be/v2", "channel_title": "C"}
    jobs.jobs["j1"] = done
    asyncio.run(runner.tick(api, {"m1": root}))
    assert synced_now == []
    assert api.completed == [("c9", "succeeded", None)]
    assert api.results["c9"] == {"video_id": "v2", "url": "https://youtu.be/v2", "channel_title": "C"}
```

If `Job` is frozen or has no `result` field, pass `result=` through `_job` instead (add an optional `result` parameter to the helper).

- [ ] **Step 2: Run, see them fail**

Run: `uv run pytest -n0 tests/test_desktop_command_runner.py -k render_upload -v`
Expected: the first FAILS (`started == ["c9"]`); the second FAILS (`synced_now == ["m1"]`, nothing completed).

- [ ] **Step 3: Implement**

In `command_runner.py`, import `prior_result` next to `refuse_reason`, and add below `StartCommand`:

```python
#: Kinds whose result travels in a synced doc (the stage audit), so the
#: command completes only after a sync that carried it. Any other kind's
#: result travels in the completion itself.
SYNCED_RESULT_KINDS = frozenset({"shot_detect"})
```

In `_claim`, first thing inside `for command in commands:`:

```python
            done = await asyncio.to_thread(prior_result, root, command)
            if done is not None:
                await self._complete(api, command["id"], "succeeded", result=done)
                continue
```

In `_heartbeat_and_finish`, replace the `SUCCEEDED` branch with:

```python
            if t.finished_at is None and job.status == JobStatus.SUCCEEDED:
                if t.command.get("kind") not in SYNCED_RESULT_KINDS:
                    # A failed call leaves it tracked; the next tick sees the
                    # same succeeded job and tries again.
                    await self._complete(api, t.command["id"], "succeeded", result=dict(job.result or {}))
                    continue
                t.finished_at = job.finished_at.timestamp() if job.finished_at else self._clock()
                t.result = {"job_id": job.id, "message": job.message}
                self._request_sync_now(t.match_id)
```

and change the failed branch's fallback to name the kind:

```python
            elif job.status == JobStatus.FAILED:
                fallback = "detection failed" if t.command.get("kind") == "shot_detect" else "the desktop job failed"
                await self._complete(api, t.command["id"], "failed", error=job.error or fallback)
                continue
```

Update the module docstring's step 3 to say a kind outside `SYNCED_RESULT_KINDS` completes as soon as its job succeeds.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -n0 tests/test_desktop_command_runner.py tests/test_auto_sync_service.py -v`
Expected: PASS.

- [ ] **Step 5: Drill**

Delete the `prior_result` block from `_claim`, run `test_a_render_upload_already_made_completes_without_running`, see it fail, restore.

- [ ] **Step 6: Commit**

```bash
git add src/splitsmith/ui/command_runner.py tests/test_desktop_command_runner.py
git commit -m "feat(sync): a render-upload completes with its video, and never runs twice (#1100)"
```

### Task 6: The `render_upload` job and its start

**Files:**
- Create: `src/splitsmith/ui/render_upload.py`
- Modify: `src/splitsmith/ui/exports_api.py:457-536` (extract `check_match_export`, add `http_detail_text`)
- Modify: `src/splitsmith/ui/server.py` (`_start_desktop_command` ~line 7294; body registration ~line 4620)
- Modify: `src/splitsmith/ui/auto_sync.py:41-46` (`RENDER_KINDS`, `_UNSYNCED_JOB_KINDS`)
- Test: `tests/test_render_upload.py` (create)

**Interfaces:**
- Consumes: `_run_match_export(handle, slug, req)` (server.py:4139, sets `{"fcpxml_path", ...}` via `handle.set_result`); `run_youtube_upload(..., command_id=)` (Task 3).
- Produces: `render_upload.run_render_upload(handle, *, render: Callable[[Any], None], upload: Callable[[Any, str], None]) -> None`; `render_upload.RENDER_SHARE = 0.8`; `render_upload.UPLOAD_FAILED_PREFIX = "Rendered on the desktop, but the upload failed: "`; `exports_api.check_match_export(state, slug: str, req: MatchExportRequest) -> None` (raises `HTTPException`); `exports_api.http_detail_text(exc: HTTPException) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_render_upload.py`:

```python
"""The render_upload job (#1100, spec 2026-09-28 addendum): render, then
upload in the same job, with the upload's result as the job's."""

from __future__ import annotations

from typing import Any

import pytest

from splitsmith.ui.jobs import JobCancelled
from splitsmith.ui.render_upload import RENDER_SHARE, UPLOAD_FAILED_PREFIX, run_render_upload


class _Handle:
    def __init__(self, *, cancel_after_render: bool = False) -> None:
        self.updates: list[tuple[float | None, str | None]] = []
        self.result: dict | None = None
        self.cancel = False
        self._cancel_after_render = cancel_after_render

    def update(self, *, progress: float | None = None, message: str | None = None) -> None:
        self.updates.append((progress, message))

    def set_result(self, payload: dict[str, Any]) -> None:
        self.result = payload

    def check_cancel(self) -> None:
        if self.cancel:
            raise JobCancelled()


def _render(handle: Any) -> None:
    handle.update(progress=0.5, message="Rendering")
    handle.set_result({"fcpxml_path": "/x/exports/match.mp4", "stage_count": 2})


def test_it_renders_then_uploads_and_reports_the_video() -> None:
    handle, uploaded = _Handle(), []

    def upload(h: Any, filename: str) -> None:
        uploaded.append(filename)
        h.update(progress=0.5, message="Uploading")
        h.set_result({"video_id": "v", "url": "https://youtu.be/v", "channel_title": "C", "notes": []})

    run_render_upload(handle, render=_render, upload=upload)
    assert uploaded == ["match.mp4"]
    assert handle.result == {"video_id": "v", "url": "https://youtu.be/v", "channel_title": "C"}
    progresses = [p for p, _ in handle.updates if p is not None]
    assert (0.5 * RENDER_SHARE) in progresses  # the render's half-way, scaled
    assert (RENDER_SHARE + 0.5 * (1 - RENDER_SHARE)) in progresses
    assert progresses == sorted(progresses)


def test_a_failed_upload_names_both_halves() -> None:
    def upload(h: Any, filename: str) -> None:
        raise RuntimeError("quota exceeded")

    with pytest.raises(RuntimeError) as exc:
        run_render_upload(_Handle(), render=_render, upload=upload)
    assert str(exc.value) == UPLOAD_FAILED_PREFIX + "quota exceeded"


def test_a_cancel_after_the_render_uploads_nothing() -> None:
    handle, uploaded = _Handle(), []

    def render(h: Any) -> None:
        _render(h)
        handle.cancel = True

    with pytest.raises(JobCancelled):
        run_render_upload(handle, render=render, upload=lambda h, f: uploaded.append(f))
    assert uploaded == []


def test_a_cancel_during_the_upload_is_a_cancel_not_an_upload_failure() -> None:
    def upload(h: Any, filename: str) -> None:
        raise JobCancelled()

    with pytest.raises(JobCancelled):
        run_render_upload(_Handle(), render=_render, upload=upload)


def test_a_render_without_an_mp4_does_not_upload() -> None:
    uploaded: list[str] = []

    def render(h: Any) -> None:
        h.set_result({"fcpxml_path": "/x/exports/match.fcpxml"})

    with pytest.raises(RuntimeError, match="no MP4"):
        run_render_upload(_Handle(), render=render, upload=lambda h, f: uploaded.append(f))
    assert uploaded == []
```

And the preflight, reusing `tests/test_ui_server.py`'s seeding helper:

```python
from fastapi import HTTPException

from splitsmith.ui.exports_api import MatchExportRequest, check_match_export, http_detail_text
from tests.test_ui_server import _seed_match_export_project


def test_the_desktop_checks_a_request_the_way_the_local_route_does(tmp_path) -> None:
    client, _ = _seed_match_export_project(tmp_path, stage_count=1)
    state = client.app.state.splitsmith_state
    check_match_export(state, "me", MatchExportRequest(stage_numbers=[1]))  # fine
    with pytest.raises(HTTPException) as exc:
        check_match_export(state, "me", MatchExportRequest(stage_numbers=[1], head_pad_seconds=99.0))
    assert "head_pad_seconds" in http_detail_text(exc.value)
```

If `_seed_match_export_project`'s app resolves the shooter only inside a request (it binds a single project, so it should not), set `current_match_id` / `current_match_root` from `splitsmith.ui.server` around the calls, as `_start_desktop_command` does.

- [ ] **Step 2: Run, see them fail**

Run: `uv run pytest -n0 tests/test_render_upload.py -v`
Expected: FAIL (ImportError on `splitsmith.ui.render_upload`).

- [ ] **Step 3: Implement `render_upload.py`**

```python
"""The ``render_upload`` job (#1100, spec 2026-09-28 render-upload
addendum): the phone asked the desktop to render a match video and upload
it, as one command whose result is the video.

One job, not the match export's chained upload job: the command completes
from one job's outcome, and here a failed upload fails the request (the
upload is what was asked for), while a desk export keeps its render
"succeeded" when its chained upload fails.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .jobs import JobCancelled

#: The render's share of the bar; the upload gets the rest.
RENDER_SHARE = 0.8
UPLOAD_FAILED_PREFIX = "Rendered on the desktop, but the upload failed: "


class _Stage:
    """A job handle for one half of the job: progress maps onto
    ``[lo, hi]`` of the real bar, and ``set_result`` is kept here rather
    than becoming the job's (the render's payload is not the command's
    result)."""

    def __init__(self, handle: Any, lo: float, hi: float) -> None:
        self._handle, self._lo, self._hi = handle, lo, hi
        self.result: dict[str, Any] | None = None

    def update(self, *, progress: float | None = None, message: str | None = None) -> None:
        if progress is not None:
            progress = self._lo + (self._hi - self._lo) * progress
        self._handle.update(progress=progress, message=message)

    def set_result(self, payload: dict[str, Any]) -> None:
        self.result = payload

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


def run_render_upload(
    handle: Any,
    *,
    render: Callable[[Any], None],
    upload: Callable[[Any, str], None],
) -> None:
    rendering = _Stage(handle, 0.0, RENDER_SHARE)
    render(rendering)
    path = str((rendering.result or {}).get("fcpxml_path") or "")
    if not path.lower().endswith(".mp4"):
        raise RuntimeError("the render produced no MP4 to upload")
    handle.check_cancel()
    uploading = _Stage(handle, RENDER_SHARE, 1.0)
    try:
        upload(uploading, Path(path).name)
    except JobCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - reported to the phone as the command's reason
        raise RuntimeError(f"{UPLOAD_FAILED_PREFIX}{exc}") from exc
    record = uploading.result or {}
    handle.set_result({k: record.get(k) for k in ("video_id", "url", "channel_title")})
    handle.update(progress=1.0, message=f"Uploaded {record.get('url', '')}".strip())
```

- [ ] **Step 4: Extract the route's preflight**

In `ui/exports_api.py`, move everything in the match-export route from `project = state.shooter_project(slug)` through the per-stage loop into:

```python
def check_match_export(state: Any, slug: str, req: MatchExportRequest) -> None:
    """The match export's pre-flight: stages chosen, pads within the
    project's trim buffers, each stage with a primary, a beep and a
    reachable source. Raises ``HTTPException`` with the route's status and
    message. Shared with the desktop's ``render_upload`` start, which
    reports the message to the phone instead of raising it."""
    project = state.shooter_project(slug)
    ...  # the moved code, unchanged


def http_detail_text(exc: HTTPException) -> str:
    """An ``HTTPException``'s detail as one line: the string itself, or a
    structured detail's ``message``."""
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or detail)
    return str(detail)
```

The route body becomes `state = ...; check_match_export(state, slug, req)` followed by its unchanged `find_active` / `submit`. Check `ui/http_errors.py`'s `ensure_source_reachable` detail keys and adjust `http_detail_text` to pick its human-readable key.

- [ ] **Step 5: Wire the job and the start in `server.py`**

Next to `_run_match_export`'s registration (~line 4622):

```python
    def _run_render_upload(handle: JobHandle, slug: str, req: MatchExportRequest, command_id: str) -> None:
        """A phone's render-and-upload request (#1100): the match export
        without its chained upload job, then the upload on this job."""
        from .render_upload import run_render_upload
        from .youtube_api import run_youtube_upload

        run_render_upload(
            handle,
            render=lambda h: _run_match_export(h, slug, req.model_copy(update={"youtube_upload": False})),
            upload=lambda h, filename: run_youtube_upload(
                h,
                state=state,
                slug=slug,
                filename=filename,
                privacy=req.youtube_privacy,
                again=True,
                playlist=req.youtube_playlist,
                playlist_id=req.youtube_playlist_id,
                publish_at=req.youtube_publish_at.isoformat() if req.youtube_publish_at else None,
                notify_subscribers=req.youtube_notify_subscribers,
                command_id=command_id,
            ),
        )

    state.jobs.bodies.register("render_upload", _run_render_upload)
```

In `_start_desktop_command`, replace the first two lines (the kind check) with a dispatch; keep the `shot_detect` code as it is:

```python
            kind = command.get("kind")
            if kind == "render_upload":
                return await _start_render_upload(match_id, match_root, command)
            if kind != "shot_detect":
                return None, f"this desktop cannot run {kind!r} requests yet"
```

and add beside it:

```python
        async def _start_render_upload(
            match_id: str, match_root: Path, command: dict
        ) -> tuple[str | None, str | None]:
            from .exports_api import check_match_export, http_detail_text

            slug = command["slug"]
            try:
                req = MatchExportRequest.model_validate((command.get("args") or {}).get("request"))
            except ValidationError:
                return None, "the render settings are not valid on this desktop; update the desktop app"
            id_token = current_match_id.set(match_id)
            root_token = current_match_root.set(match_root)
            try:
                try:
                    check_match_export(state, slug, req)
                except HTTPException as exc:
                    return None, http_detail_text(exc)
                for busy in ("match_export", "render_upload"):
                    if await state.jobs.find_active(kind=busy, shooter_slug=slug) is not None:
                        return None, "a match export is already running for this shooter on the desktop"
                job = await state.jobs.submit(
                    kind="render_upload",
                    shooter_slug=slug,
                    args={"slug": slug, "req": req, "command_id": command["id"]},
                )
                return job.id, None
            except Exception as exc:  # noqa: BLE001 - reported to the phone, not raised
                return None, f"could not start on the desktop: {exc}"
            finally:
                current_match_root.reset(root_token)
                current_match_id.reset(id_token)
```

In `ui/auto_sync.py` add `"render_upload"` to `RENDER_KINDS` (holds automatic syncs) and to `_UNSYNCED_JOB_KINDS` (writes nothing that syncs).

- [ ] **Step 6: Run the tests**

Run: `uv run pytest -n0 tests/test_render_upload.py tests/test_desktop_command_runner.py tests/test_auto_sync_service.py tests/test_ui_server.py -k "render_upload or match_export or command or auto" -v`
Expected: PASS, including every shipped `test_match_export_endpoint_*`.

- [ ] **Step 7: Drill**

Remove the `except JobCancelled: raise` clause in `run_render_upload`, run `test_a_cancel_during_the_upload_is_a_cancel_not_an_upload_failure`, see it fail, restore.

- [ ] **Step 8: Commit**

```bash
git add src/splitsmith/ui/render_upload.py src/splitsmith/ui/exports_api.py src/splitsmith/ui/server.py src/splitsmith/ui/auto_sync.py tests/test_render_upload.py
git commit -m "feat(sync): the desktop renders and uploads a phone's request in one job (#1100)"
```

- [ ] **Step 9: Verify by hand, then open PR 2**

Follow CLAUDE.md "Verifying a screen locally": seed `~/.claude-tmp/demo-render` with `--media`, run the desktop against a scratch `SPLITSMITH_HOME` linked to staging with PR 1 deployed there (or the two-server harness from the `verify-unreleased-hosted-features-locally` memory if staging does not have PR 1). Never the real `~/.splitsmith`.

1. `POST /api/match/desktop-commands` with a `render_upload` (privacy `unlisted`) through the hosted API; watch the desktop claim, render, upload and complete; read the command back and see `result.url`.
2. Crash case: block the completion (point the desktop at a dead host after the upload finishes, or kill it between the job's end and the next tick), restart after the 10 min lease, and see the command complete with the same `video_id` and no second upload in YouTube Studio.
3. Read the video back through `videos.list`, then delete it.

Record the commands and outputs in the PR body. Full suite (`uv run pytest`) green, push, open PR 2, `gh pr checks <n> --watch`, squash-merge.

---

## PR 3: phone

Branch: `feat/1100-render-upload-phone` from `main` after PR 2 merged. All paths below are under `src/splitsmith/ui_static/src/`. Run SPA tests with `corepack pnpm --dir src/splitsmith/ui_static exec vitest run <path>` from the repo root, and the typecheck with `corepack pnpm --dir src/splitsmith/ui_static exec tsc --noEmit`.

### Task 7: Wording for a render-upload request

**Files:**
- Modify: `lib/desktopCommands.ts`
- Modify: `components/desktop/DesktopCommandLine.tsx`
- Test: `lib/desktopCommands.test.ts`

**Interfaces:**
- Produces: `CommandLine.link?: { href: string; label: string }`; `commandTitle` for `render_upload` returns `Render and upload (<slug>)`; `latestForShooter(commands, kind, slug): DesktopCommand | null`.

- [ ] **Step 1: Write the failing tests**

```ts
describe("render_upload", () => {
  const ru = (over: Partial<DesktopCommand> = {}) =>
    cmd({ kind: "render_upload", stage_number: null, args: { request: {} }, ...over });

  it("is titled by shooter", () => {
    expect(commandTitle(ru())).toBe("Render and upload (anna)");
  });

  it("links the video when it succeeded, naming the channel", () => {
    const line = commandLine(
      ru({
        status: "succeeded",
        finished_at: "2026-09-28T11:58:00Z",
        result: { video_id: "v", url: "https://youtu.be/v", channel_title: "Anna Shoots" },
      }),
      around,
      NOW,
    );
    expect(line.tone).toBe("ok");
    expect(line.text).toBe("Uploaded to Anna Shoots 2 min ago.");
    expect(line.link).toEqual({ href: "https://youtu.be/v", label: "youtu.be/v" });
  });

  it("finds the newest request for one shooter and kind", () => {
    const list = [ru({ id: "new" }), cmd({ id: "detect" }), ru({ id: "old" })];
    expect(latestForShooter(list, "render_upload", "anna")?.id).toBe("new");
    expect(latestForShooter(list, "render_upload", "bob")).toBeNull();
  });
});

it("still says re-detected for a re-detect", () => {
  expect(commandLine(cmd({ status: "succeeded" }), around, NOW).text).toMatch(/^Re-detected on your desktop/);
});
```

Check the `formatRelative` output for 2 minutes against the shipped tests in this file and match it exactly.

- [ ] **Step 2: Run, see them fail**

Run: `corepack pnpm --dir src/splitsmith/ui_static exec vitest run src/lib/desktopCommands.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `lib/desktopCommands.ts`:

```ts
export interface CommandLine {
  text: string;
  tone: CommandTone;
  cancellable: boolean;
  /** The result to open, when the request produced one (a video). */
  link?: { href: string; label: string };
}

export function latestForShooter(
  commands: readonly DesktopCommand[],
  kind: DesktopCommand["kind"],
  slug: string,
): DesktopCommand | null {
  return commands.find((c) => c.kind === kind && c.slug === slug) ?? null;
}
```

In `commandLine`'s `succeeded` case:

```ts
    case "succeeded": {
      const when = c.finished_at ? ` ${formatRelative(new Date(c.finished_at), now)}` : "";
      if (c.kind === "render_upload") {
        const url = typeof c.result?.url === "string" ? c.result.url : null;
        const channel = typeof c.result?.channel_title === "string" && c.result.channel_title ? c.result.channel_title : "YouTube";
        return {
          text: `Uploaded to ${channel}${when}.`,
          tone: "ok",
          cancellable: false,
          link: url ? { href: url, label: url.replace(/^https?:\/\//, "") } : undefined,
        };
      }
      return { text: `Re-detected on your desktop${when}.`, tone: "ok", cancellable: false };
    }
```

In `commandTitle`:

```ts
export function commandTitle(c: DesktopCommand): string {
  if (c.kind === "render_upload") return c.slug ? `Render and upload (${c.slug})` : "Render and upload";
  ...  // unchanged
}
```

In `components/desktop/DesktopCommandLine.tsx`, after the text span:

```tsx
      {line.link ? (
        <a className="text-sm text-ink underline" href={line.link.href} target="_blank" rel="noreferrer">
          {line.link.label}
        </a>
      ) : null}
```

- [ ] **Step 4: Run the tests**

Run: `corepack pnpm --dir src/splitsmith/ui_static exec vitest run src/lib/desktopCommands.test.ts src/components/desktop`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/lib/desktopCommands.ts src/splitsmith/ui_static/src/lib/desktopCommands.test.ts src/splitsmith/ui_static/src/components/desktop/DesktopCommandLine.tsx
git commit -m "feat(ui): word a render-and-upload request and link its video (#1100)"
```

### Task 8: One match-export payload builder, and the request wrapper

**Files:**
- Modify: `pages/matchExportModel.ts` (add `buildMatchExportPayload`)
- Modify: `pages/Export.tsx:558-596` (`submitBundle` uses it)
- Modify: `lib/api.ts` (`requestDesktopRender`; widen `requestDesktopCommand`'s kind)
- Modify: `lib/useDesktopCommands.ts` (`requestRender`)
- Test: `pages/matchExportModel.test.ts` (create or extend), `lib/api.exportBodies.test.ts`

**Interfaces:**
- Produces: `buildMatchExportPayload(input: MatchExportPayloadInput): MatchExportRequestPayload`; `api.requestDesktopRender(slug: string, request: MatchExportRequestPayload): Promise<DesktopCommand>`; `DesktopCommands.requestRender(slug: string, request: MatchExportRequestPayload): Promise<void>`.

- [ ] **Step 1: Write the failing tests**

In `lib/api.exportBodies.test.ts`, add inside the describe block (reuse the file's `payload` object for `exportMatch` by lifting it to a module-level `const matchPayload: Required<MatchExportRequestPayload>` if it is inline):

```ts
  it("requestDesktopRender", async () => {
    const fetchMock = mockFetch();
    await api.requestDesktopRender("anna", matchPayload);
    const body = sentBody(fetchMock);
    expect(body.kind).toBe("render_upload");
    expect(body.slug).toBe("anna");
    // Every declared field reaches the desktop, as for exportMatch.
    for (const key of Object.keys(matchPayload)) {
      expect((body.args as { request: Record<string, unknown> }).request).toHaveProperty(key);
    }
  });
```

In `pages/matchExportModel.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { DEFAULT_EXPORT_SETTINGS as S } from "@/lib/exportPresets";

import { buildMatchExportPayload, type MatchExportPayloadInput } from "./matchExportModel";

describe("buildMatchExportPayload", () => {
  const base: MatchExportPayloadInput = {
    stageNumbers: [1, 2],
    headPad: S.headPad,
    tailPad: S.tailPad,
    camOptions: S.camOptions,
    outputFormat: "mp4",
    transitionKind: S.transitionKind,
    transitionSeconds: S.transitionSeconds,
    renderOptions: S.renderOptions,
    youtube: true,
    descriptionLead: "",
    uploadOptions: { ...S.uploadOptions, enabled: true, playlistId: "PL1", playlist: "Match" },
    includeOverlay: S.includeOverlay,
    overlayCodec: S.overlayCodec,
    projectName: "Match",
    uploadTarget: "desk",
    youtubeConnected: true,
  };

  it("uploads only with a connected channel on the desk", () => {
    expect(buildMatchExportPayload({ ...base, uploadTarget: "desk", youtubeConnected: false }).youtube_upload).toBe(false);
    expect(buildMatchExportPayload({ ...base, uploadTarget: "desk", youtubeConnected: true }).youtube_upload).toBe(true);
  });

  it("always renders an MP4 that uploads, on the desktop", () => {
    const p = buildMatchExportPayload({ ...base, outputFormat: "fcpxml", uploadTarget: "desktop", youtubeConnected: false });
    expect(p.output_format).toBe("mp4");
    expect(p.youtube_sidecar).toBe(true);
    expect(p.youtube_upload).toBe(true);
    expect(p.youtube_playlist_id).toBeNull();
  });
});
```

If `UploadFormOptions` names its fields differently from `enabled` / `playlist` / `playlistId` (check `lib/exportPresets.ts` and `YouTubeConnect.tsx`), use its names. Add one more case: for `uploadTarget: "desk"`, the builder's output equals, field by field, the literal `submitBundle` sends today for the same inputs (copy that literal into the test as the expected object before deleting it from `Export.tsx`), so the refactor changes nothing for the desk.

- [ ] **Step 2: Run, see them fail**

Run: `corepack pnpm --dir src/splitsmith/ui_static exec vitest run src/lib/api.exportBodies.test.ts src/pages/matchExportModel.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `pages/matchExportModel.ts`, move the object literal from `submitBundle` into a builder. Its input is exactly what `submitBundle` reads today, plus two new fields:

```ts
export interface MatchExportPayloadInput {
  stageNumbers: number[];
  headPad: number;
  tailPad: number;
  camOptions: CamOptions;
  outputFormat: OutputFormat;
  transitionKind: TransitionKind;
  transitionSeconds: number;
  renderOptions: RenderOptions;
  youtube: boolean;
  descriptionLead: string;
  uploadOptions: UploadFormOptions;
  includeOverlay: boolean;
  overlayCodec: OverlayCodec;
  projectName: string;
  /** "desk" posts an export here; "desktop" asks the linked desktop to
   *  render and upload, which only makes sense as an MP4 that uploads. */
  uploadTarget: "desk" | "desktop";
  youtubeConnected: boolean;
}

export function buildMatchExportPayload(input: MatchExportPayloadInput): MatchExportRequestPayload {
  const desktop = input.uploadTarget === "desktop";
  const outputFormat = desktop ? "mp4" : input.outputFormat;
  const renderedMp4 = outputFormat === "mp4";
  const youtube = desktop || (renderedMp4 && input.youtube);
  const upload = rowUploadOptions(input.uploadOptions);
  return {
    stage_numbers: input.stageNumbers,
    head_pad_seconds: input.headPad,
    tail_pad_seconds: input.tailPad,
    ...camExportFields(input.camOptions),
    output_format: outputFormat,
    transition_kind: transitionsSupported(outputFormat) ? input.transitionKind : "none",
    transition_duration_seconds: clampSeconds(input.transitionSeconds, 0.1),
    ...matchExportFields(input.renderOptions, outputFormat),
    intro_path: undefined,
    outro_path: undefined,
    youtube_sidecar: youtube,
    description_lead: youtube ? input.descriptionLead.trim() || null : undefined,
    youtube_preset: youtube,
    youtube_upload: desktop || (youtube && input.youtubeConnected && input.uploadOptions.enabled),
    youtube_privacy: upload.privacy,
    youtube_playlist: upload.playlist,
    // The hosted picker lists the hosted connection's playlists, which may
    // be another channel than the desktop's; by title only there.
    youtube_playlist_id: desktop ? null : upload.playlist_id,
    youtube_publish_at: upload.publish_at,
    youtube_notify_subscribers: upload.notify_subscribers,
    include_overlay: input.includeOverlay,
    overlay_codec: input.overlayCodec,
    overlay_max_height: null,
    overlay_max_fps: null,
    project_name: input.projectName,
  };
}
```

Move the imports these helpers need (`camExportFields`, `matchExportFields`, `transitionsSupported`, `clampSeconds`, `rowUploadOptions`, the types) from `Export.tsx` into this module; import them from where `Export.tsx` imports them today. `submitBundle` becomes `api.exportMatch(slug, buildMatchExportPayload({ ..., uploadTarget: "desk", youtubeConnected: !!youtubeSettings?.connected }))`. Diff the old literal against the builder field by field: nothing may change for `uploadTarget: "desk"`.

In `lib/api.ts`:

```ts
  requestDesktopCommand: (body: { kind: "shot_detect"; slug: string; stage_number: number }) =>
    request<DesktopCommand>("/api/match/desktop-commands", { method: "POST", json: body }),
  /** Ask the linked desktop to render this shooter's match video and
   *  upload it. Spreads the payload, as exportMatch does. */
  requestDesktopRender: (slug: string, payload: MatchExportRequestPayload) =>
    request<DesktopCommand>("/api/match/desktop-commands", {
      method: "POST",
      json: { kind: "render_upload", slug, args: { request: { ...payload } } },
    }),
```

In `lib/useDesktopCommands.ts`, add `requestRender` beside `requestRedetect` with the same error handling and `refresh()`, calling `api.requestDesktopRender`, and add it to the `DesktopCommands` interface.

- [ ] **Step 4: Run the SPA tests and typecheck**

Run: `corepack pnpm --dir src/splitsmith/ui_static exec vitest run src/lib src/pages/Export src/pages/matchExportModel.test.ts && corepack pnpm --dir src/splitsmith/ui_static exec tsc --noEmit`
Expected: PASS, including the shipped `Export.renderOptions.test.tsx` and `Export.presets.test.tsx`.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/pages/matchExportModel.ts src/splitsmith/ui_static/src/pages/matchExportModel.test.ts src/splitsmith/ui_static/src/pages/Export.tsx src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/lib/api.exportBodies.test.ts src/splitsmith/ui_static/src/lib/useDesktopCommands.ts
git commit -m "refactor(ui): one match-export payload builder; request a desktop render (#1100)"
```

### Task 9: The YouTube row on the desktop's account

**Files:**
- Modify: `components/export/YouTubeConnect.tsx`
- Modify: `components/export/DetailsGroup.tsx:93-104`
- Test: `components/export/YouTubeConnect.test.tsx`

**Interfaces:**
- Produces: `YouTubeConnectProps.onDesktop?: boolean`; `DetailsGroup` prop `onDesktop?: boolean` passed through.

- [ ] **Step 1: Write the failing test**

In `YouTubeConnect.test.tsx`, reusing the file's render helper and default props:

```tsx
it("on the desktop's account: no connect, no picker, no Off", async () => {
  const onOptionsChange = vi.fn();
  render(
    <YouTubeConnect
      {...defaultProps}
      settings={null}
      onDesktop
      options={{ ...defaultProps.options, enabled: true }}
      onOptionsChange={onOptionsChange}
      showUploadControl
    />,
  );
  expect(screen.getByText("Uploads to the YouTube account connected on your desktop.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /connect youtube/i })).toBeNull();
  expect(screen.queryByRole("radio", { name: "Off" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: /playlist/i })).toBeNull();
  expect(screen.getByRole("textbox", { name: /playlist/i })).toBeInTheDocument();
  expect(api.getYouTubePlaylists).not.toHaveBeenCalled();
});
```

Match `Segmented`'s real role and the playlist input's real label in this file's existing tests.

- [ ] **Step 2: Run, see it fail**

Run: `corepack pnpm --dir src/splitsmith/ui_static exec vitest run src/components/export/YouTubeConnect.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add the prop:

```ts
  /** Uploads run on the linked desktop with its own connection: no
   *  connect flow, no picker over this account's playlists, and no Off
   *  (uploading is the request). */
  onDesktop?: boolean;
```

At the top of the component body, before `if (settings === null) return null;`, branch:

```tsx
  if (onDesktop) {
    return (
      <div className="flex flex-col gap-2">
        <span className="text-md text-ink-2">Uploads to the YouTube account connected on your desktop.</span>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-md text-muted">Privacy</span>
          <Segmented<UploadAfterRender>
            label="Privacy"
            value={uploadAfterRender === "off" ? "unlisted" : uploadAfterRender}
            onChange={setUploadAfterRender}
            options={UPLOAD_OPTIONS.filter((o) => o.value !== "off")}
            disabled={busy}
          />
        </div>
        <label className="flex flex-wrap items-center gap-2">
          <span className="text-md text-muted">Playlist</span>
          <input
            type="text"
            aria-label="Playlist"
            className={inputClass}
            placeholder="None"
            value={options.playlist ?? ""}
            onChange={(e) => onOptionsChange({ ...options, playlist: e.target.value || null, playlistId: null })}
            disabled={busy}
          />
        </label>
      </div>
    );
  }
```

Hooks must run before this branch: place it after every `useState` / `useEffect` in the component, and make the playlist fetch effect's condition include `!onDesktop`. Reuse the component's existing `uploadAfterRender` / `setUploadAfterRender` and input class names; read the component first.

In `DetailsGroup.tsx`, accept `onDesktop?: boolean`, pass it to `YouTubeConnect`, and render the Upload field when `renderedMp4 || onDesktop`.

- [ ] **Step 4: Run the tests**

Run: `corepack pnpm --dir src/splitsmith/ui_static exec vitest run src/components/export`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/export/YouTubeConnect.tsx src/splitsmith/ui_static/src/components/export/YouTubeConnect.test.tsx src/splitsmith/ui_static/src/components/export/DetailsGroup.tsx
git commit -m "feat(ui): the YouTube row names the desktop's account in desktop mode (#1100)"
```

### Task 10: Export page "Render on desktop" mode

**Files:**
- Modify: `pages/Export.tsx`
- Test: `pages/Export.desktopRender.test.tsx` (create)

**Interfaces:**
- Consumes: `buildMatchExportPayload` (Task 8), `useDesktopCommands(enabled).requestRender / commands / presence / cancel` (Task 8), `latestForShooter`, `commandTitle`, `presenceText` (Task 7), `DesktopCommandLine`, `YouTubeConnect onDesktop` (Task 9).

- [ ] **Step 1: Write the failing test**

Model the file on `pages/Export.presets.test.tsx`: same mocks for `api` and the outlet context, same `openGroups` helper. Set the context's `capabilities` to the mirror set (no `edit`), the deployment mode to hosted and the match origin to `desktop`.

```tsx
it("a mirror renders on the desktop instead of here", async () => {
  api.requestDesktopRender.mockResolvedValue(renderCommand({ status: "pending" }));
  api.listDesktopCommands.mockResolvedValue({ commands: [], presence: away });
  renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "hosted" });

  const button = await screen.findByRole("button", { name: "Render on desktop" });
  expect(button).toBeEnabled();
  expect(screen.getByText(/Waiting for your desktop \(last seen/)).toBeInTheDocument();
  await openGroups(["details"]);
  expect(screen.getByText("Uploads to the YouTube account connected on your desktop.")).toBeInTheDocument();

  await userEvent.click(button);
  expect(api.exportMatch).not.toHaveBeenCalled();
  const [slug, payload] = api.requestDesktopRender.mock.calls[0];
  expect(slug).toBe("anna");
  expect(payload.output_format).toBe("mp4");
  expect(payload.youtube_upload).toBe(true);
});

it("shows this shooter's render requests with their state", async () => {
  api.listDesktopCommands.mockResolvedValue({
    commands: [renderCommand({ status: "succeeded", result: { url: "https://youtu.be/v", channel_title: "C", video_id: "v" } })],
    presence: around,
  });
  renderExport({ capabilities: MIRROR_CAPABILITIES, origin: "desktop", mode: "hosted" });
  expect(await screen.findByRole("link", { name: "youtu.be/v" })).toHaveAttribute("href", "https://youtu.be/v");
});

it("a hosted-native match keeps its own export", async () => {
  renderExport({ capabilities: FULL_CAPABILITIES, origin: "hosted", mode: "hosted" });
  expect(await screen.findByRole("button", { name: /^Export/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Render on desktop" })).toBeNull();
});
```

Define `renderCommand`, `away`, `around`, `MIRROR_CAPABILITIES`, `FULL_CAPABILITIES` and `renderExport` in the test file from the shapes in `lib/desktopCommands.test.ts` and `Export.presets.test.tsx`. Use the page's real primary label for the hosted-native case.

- [ ] **Step 2: Run, see it fail**

Run: `corepack pnpm --dir src/splitsmith/ui_static exec vitest run src/pages/Export.desktopRender.test.tsx`
Expected: FAIL (no "Render on desktop").

- [ ] **Step 3: Implement**

In `ExportInner`:

```tsx
  // A desktop-synced match cannot export here (no sources, no edit), but
  // its desktop can: the form becomes a request the desktop renders and
  // uploads. Match mode only; the compare grid and trims have no upload.
  const onDesktop = hosted && ctx?.origin === "desktop" && editDenied;
  const desktop = useDesktopCommands(onDesktop);
  const renderRequests = useMemo(
    () => desktop.commands.filter((c) => c.kind === "render_upload" && c.slug === slug),
    [desktop.commands, slug],
  );
```

Use the outlet context's real origin field (read `MatchShellOutletContext`; the capability helpers already know the origin). Then:

- `canExport`: `onDesktop ? mode === "single" && orderedSelection.length > 0 && !!project && !desktop.busy : <today's expression>` (add a `busy` flag to the hook's return if it has none, set while a request is in flight).
- `primaryLabel`: `"Render on desktop"` when `onDesktop`.
- `submitExport`: when `onDesktop`, call `desktop.requestRender(slug, buildMatchExportPayload({ ...sameInputsAsSubmitBundle, uploadTarget: "desktop", youtubeConnected: false }))` and return.
- The primary's `title`: `undefined` when `onDesktop`.
- Hide the compare and trims mode choices when `onDesktop` (the mode control reads its options from the page; filter to `single`).
- Pass `onDesktop` to `DetailsGroup`.
- Under the primary, when `onDesktop` and `desktop.presence`, one muted line: `presenceText(desktop.presence)`; then `desktop.error` as the page's error line when set.
- In the history area, when `onDesktop`, render instead of `ExportHistory`:

```tsx
            <div className="divide-y divide-rule">
              {renderRequests.length === 0 ? (
                <p className="px-3.5 py-3 text-sm text-muted">No render requests yet.</p>
              ) : (
                renderRequests.map((c) => (
                  <div key={c.id} className="px-3.5 py-2.5">
                    <p className="text-md text-ink">{commandTitle(c)}</p>
                    <DesktopCommandLine command={c} presence={desktop.presence} onCancel={(id) => void desktop.cancel(id)} />
                  </div>
                ))
              )}
            </div>
```

Keep one primary on the page: the rows use `DesktopCommandLine`'s ghost Cancel only.

- [ ] **Step 4: Run the SPA suite, typecheck and lint**

Run: `corepack pnpm --dir src/splitsmith/ui_static exec vitest run && corepack pnpm --dir src/splitsmith/ui_static exec tsc --noEmit && corepack pnpm --dir src/splitsmith/ui_static exec eslint src`
Expected: PASS, no lint errors.

- [ ] **Step 5: Look at it**

Run the seeded demo match per CLAUDE.md ("Verifying a screen locally"), as a mirror: the hosted app locally with a desktop-origin match (the two-server harness from the `verify-unreleased-hosted-features-locally` memory). Screenshot the Export page at phone width (390 px) with Playwright: the waiting state, a running row, and a succeeded row with its link. The host is headless: publish the screenshots as an Artifact for review, per the `gaspode-headless-use-artifacts` memory.

- [ ] **Step 6: Commit and open PR 3**

```bash
git add src/splitsmith/ui_static/src/pages/Export.tsx src/splitsmith/ui_static/src/pages/Export.desktopRender.test.tsx src/splitsmith/ui_static/src/lib/useDesktopCommands.ts
git commit -m "feat(ui): render and upload on the desktop from a mirror's Export page (#1100)"
```

Full `uv run pytest` and the SPA suite green; push; open PR 3 with the screenshots' Artifact link; `gh pr checks <n> --watch`; squash-merge. Then comment on #1100 that render-and-upload shipped, linking the three PRs and the addendum; leave the issue open for `render_export` (#752).
