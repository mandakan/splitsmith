# Fixture Review on the Phone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Review the fixture corpus from a phone in short sessions, served by the Mac's lab server over the home network behind a token.

**Architecture:** A token middleware lets the lab listen on the LAN safely; `review_session.py --phone` starts it and prints the phone's URL. The walk's state and actions move into a `useWalk` hook so the desktop `Walk` (keys) and a new `MobileWalk` (thumb buttons) share one implementation of the rules. The queue gains a Continue entry that resumes the fixture already started.

**Tech Stack:** FastAPI middleware (Python 3.11), React 18 + TypeScript, vitest + Testing Library, pytest, Playwright for the visual check.

**Spec:** `docs/superpowers/specs/2026-10-11-mobile-fixture-review-design.md`

## Global Constraints

- No new dependencies (Python or npm).
- `lib/walk.ts` rules are unchanged: stops, scope, prompt, flags, placement, rise foot.
- The desktop walk behaves exactly as before; `components/review/Walk.test.tsx` passes without edits after the extraction.
- Loopback binds without a token behave exactly as today.
- UI copy: ASCII only, no dash punctuation, no issue numbers.
- Touch targets at least 44 px; the phone layout never scrolls horizontally at 390 px.
- No What's new entry (lab only).
- Worktree git goes through `/usr/bin/git`; never `git add src` (node_modules symlink).

## Rulings against the spec (made while planning)

- **Cookie `SameSite=Lax`, not `Strict`.** With Strict, opening a bookmark or a link from another app is a cross-site navigation that drops the cookie, so the phone would get a 404 every time it is opened from outside the browser tab. Lax still withholds it from cross-site subrequests. Cost if wrong: a cross-site top-level GET carries the cookie; the lab has no state-changing GET.
- **The token persists across runs** in `build/review_phone_token` (gitignored, mode 600), so the phone stays signed in between sessions. The spec said one per run; that means sending a new URL to the phone every time the server starts, which defeats 5-minute sessions. `--new-token` rotates it. Cost if wrong: a leaked token stays valid until rotated.
- **Continue picks "started" fixtures first, by `walk_decided` events**, not "every stop decided". The stops are computed in the browser from the audio; the queue route cannot know them without decoding every WAV. A fixture with walk decisions and no sign-off is the one a session should finish. Cost if wrong: Continue may resume a fixture with many stops left rather than one nearly done.

## Review Focus

1. A request on a LAN bind with a wrong or missing token, to any path including static files and `/api/fixture/audio?path=`: an opaque 404, never content. (Task 1 tests a fixture-audio path.)
2. Phone opened from a bookmark after the server restarted: still signed in (persisted token). (Task 2 tests persistence.)
3. Desktop walk after the extraction: every key does what it did. (Task 4 runs the untouched suite.)
4. Phone stop where a button's action is a no-op (Rise foot on a "not a shot" stop, nudges on a non-shot): the button is disabled, not silently inert. (Task 5 tests it.)
5. First sound on the phone: playback starts from the Start tap, not before. (Task 5 tests that the loop is not asked to play until Start.)

---

### Task 1: The lab token gate

**Files:**
- Create: `src/splitsmith/ui/lab_token.py`
- Modify: `src/splitsmith/ui/server.py` (`create_app` and `serve` signatures; register the middleware last in `create_app`, so it is the outermost)
- Modify: `src/splitsmith/cli.py` (`ui` command: `--lab-token`, the refusal)
- Test: `tests/test_lab_token.py`

**Interfaces:**
- Produces: `create_app(..., lab_token: str | None = None)`, `serve(..., lab_token: str | None = None)`, `splitsmith ui --lab-token TEXT` (env `SPLITSMITH_LAB_TOKEN`), `lab_token.COOKIE = "splitsmith_lab_token"`, `lab_token.PARAM = "lab_token"`, `lab_token.is_loopback(host: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
"""The lab token gate: a LAN-bound lab answers only the holder of its token."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from splitsmith.ui import lab_token
from splitsmith.ui.server import create_app

TOKEN = "s3cret-token"


def _client(tmp_path: Path, token: str | None) -> TestClient:
    app = create_app(lab_enabled=True, lab_token=token)
    return TestClient(app, follow_redirects=False)


def test_without_token_every_path_is_an_opaque_404(tmp_path: Path) -> None:
    c = _client(tmp_path, TOKEN)
    for path in ("/api/health", "/dev/review", "/api/fixture/audio?path=/etc/hosts", "/"):
        r = c.get(path)
        assert r.status_code == 404, path
        assert "hosts" not in r.text


def test_wrong_token_is_404(tmp_path: Path) -> None:
    c = _client(tmp_path, TOKEN)
    assert c.get("/api/health", params={"lab_token": "nope"}).status_code == 404
    c.cookies.set(lab_token.COOKIE, "nope")
    assert c.get("/api/health").status_code == 404


def test_token_in_the_url_sets_the_cookie_and_redirects_without_it(tmp_path: Path) -> None:
    c = _client(tmp_path, TOKEN)
    r = c.get("/dev/review", params={"lab_token": TOKEN, "phone": "1"})
    assert r.status_code == 303
    assert r.headers["location"] == "/dev/review?phone=1"
    cookie = r.headers["set-cookie"]
    assert f"{lab_token.COOKIE}={TOKEN}" in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie.replace("Lax", "lax")
    assert c.get("/api/health").status_code == 200


def test_no_token_configured_changes_nothing(tmp_path: Path) -> None:
    c = _client(tmp_path, None)
    assert c.get("/api/health").status_code == 200


@pytest.mark.parametrize(
    ("host", "loop"),
    [("127.0.0.1", True), ("localhost", True), ("::1", True), ("192.168.1.20", False), ("0.0.0.0", False)],
)
def test_is_loopback(host: str, loop: bool) -> None:
    assert lab_token.is_loopback(host) is loop


def test_ui_refuses_a_lan_bind_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from splitsmith import cli

    called: list[dict] = []
    monkeypatch.setattr("splitsmith.ui.server.serve", lambda **kw: called.append(kw))
    monkeypatch.delenv("SPLITSMITH_LAB_TOKEN", raising=False)
    r = CliRunner().invoke(cli.app, ["ui", "--lab", "--no-browser", "--host", "192.168.1.20"])
    assert r.exit_code != 0
    assert "--lab-token" in r.output
    assert called == []
    r = CliRunner().invoke(
        cli.app, ["ui", "--lab", "--no-browser", "--host", "192.168.1.20", "--lab-token", TOKEN]
    )
    assert r.exit_code == 0, r.output
    assert called[0]["lab_token"] == TOKEN
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -n0 tests/test_lab_token.py -q`
Expected: FAIL at import (`cannot import name 'lab_token'`).

- [ ] **Step 3: Implement the gate**

`src/splitsmith/ui/lab_token.py`:

```python
"""The lab's token gate for a server bound beyond loopback.

The lab serves any file a path names and writes fixture files, so a lab
listening on the home network must answer only the phone holding its token:
``?lab_token=`` once (answered with a cookie and a redirect to the same URL
without it), the cookie after that. Everything else is the same 404 as an
unknown route, so the server does not even admit to being there.
"""

from __future__ import annotations

import hmac
import ipaddress
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

COOKIE = "splitsmith_lab_token"
PARAM = "lab_token"
# A year: the token itself is what is rotated (review_session.py --new-token).
_MAX_AGE = 365 * 24 * 3600


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def make_middleware(token: str):
    expected = token.encode()

    def _ok(value: str | None) -> bool:
        return value is not None and hmac.compare_digest(value.encode(), expected)

    async def lab_token_gate(request: Request, call_next) -> Response:
        offered = request.query_params.get(PARAM)
        if offered is not None:
            if not _ok(offered):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            rest = [(k, v) for k, v in request.query_params.multi_items() if k != PARAM]
            target = request.url.path + (f"?{urlencode(rest)}" if rest else "")
            resp = RedirectResponse(target, status_code=303)
            resp.set_cookie(COOKIE, token, max_age=_MAX_AGE, httponly=True, samesite="lax")
            return resp
        if not _ok(request.cookies.get(COOKIE)):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return await call_next(request)

    return lab_token_gate
```

In `server.py`, add `lab_token: str | None = None` to `create_app`'s keyword arguments and document it in the docstring (`"Required by the CLI when the server binds beyond loopback; see ui/lab_token."`). Immediately before `create_app`'s final `return app`, register it so it wraps every other middleware:

```python
    if lab_token:
        from . import lab_token as _lab_token

        app.middleware("http")(_lab_token.make_middleware(lab_token))
```

Add `lab_token: str | None = None` to `serve(...)` and pass it through to `create_app(..., lab_token=lab_token)`.

In `cli.py`'s `ui` command add the option after `lab`:

```python
    lab_token: str | None = typer.Option(
        None,
        "--lab-token",
        envvar="SPLITSMITH_LAB_TOKEN",
        help=(
            "Answer only requests carrying this token (a ?lab_token= once, "
            "then a cookie). Required when --host is not a loopback address."
        ),
    ),
```

and, first thing in the body:

```python
    from .ui.lab_token import is_loopback

    if not is_loopback(host) and not lab_token:
        raise typer.BadParameter(
            f"--host {host} is reachable from other devices; pass --lab-token "
            "(scripts/review_session.py --phone does this for you)"
        )
```

Pass `lab_token=lab_token` to the `serve(...)` call.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -n0 tests/test_lab_token.py -q`
Expected: all pass. If `test_ui_refuses_a_lan_bind_without_a_token` fails because `serve` is imported inside the command before the patch takes effect, patch `splitsmith.ui.server.serve` (the command imports it at call time, so the patch above applies).

- [ ] **Step 5: Run the server suite for regressions**

Run: `uv run pytest tests/test_ui_server.py -q -x`
Expected: pass.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add src/splitsmith/ui/lab_token.py src/splitsmith/ui/server.py src/splitsmith/cli.py tests/test_lab_token.py
/usr/bin/git commit -m "feat(lab): a token gate for a lab served beyond loopback"
```

---

### Task 2: `review_session.py --phone`

**Files:**
- Modify: `scripts/review_session.py`
- Test: `tests/test_review_session.py`

**Interfaces:**
- Consumes: `splitsmith ui --host H --lab-token T` (Task 1).
- Produces: `lan_address() -> str`, `phone_token(path: Path, rotate: bool) -> str`, `phone_url(host: str, port: int, token: str) -> str`; flags `--phone`, `--host`, `--new-token`.

- [ ] **Step 1: Write the failing tests**

```python
"""review_session.py's phone helpers."""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("review_session", ROOT / "scripts" / "review_session.py")
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)


def test_phone_token_persists_private_and_rotates(tmp_path: Path) -> None:
    p = tmp_path / "build" / "review_phone_token"
    first = rs.phone_token(p, rotate=False)
    assert len(first) >= 32
    assert rs.phone_token(p, rotate=False) == first
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert rs.phone_token(p, rotate=True) != first


def test_phone_url_opens_the_queue_with_the_token() -> None:
    assert rs.phone_url("192.168.1.20", 5180, "abc") == "http://192.168.1.20:5180/dev/review?lab_token=abc"


def test_lan_address_is_not_loopback() -> None:
    addr = rs.lan_address()
    assert not addr.startswith("127.")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -n0 tests/test_review_session.py -q`
Expected: FAIL with `AttributeError: module 'review_session' has no attribute 'phone_token'`.

- [ ] **Step 3: Implement**

Add to `scripts/review_session.py` (imports `secrets` alongside the others):

```python
TOKEN_FILE = ROOT / "build" / "review_phone_token"


def lan_address() -> str:
    """The address the default route leaves from (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed, nothing sent
        return s.getsockname()[0]


def phone_token(path: Path, rotate: bool) -> str:
    """The phone's token, kept between runs so the phone stays signed in."""
    if path.exists() and not rotate:
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    path.write_text(token)
    path.chmod(0o600)
    return token


def phone_url(host: str, port: int, token: str) -> str:
    return f"http://{host}:{port}/dev/review?lab_token={token}"
```

Change `_serve(port)` to `_serve(port: int, host: str = "127.0.0.1", token: str | None = None)`: append `["--host", host]` to `cmd`, and when `token` is set pass it through the environment (`env["SPLITSMITH_LAB_TOKEN"] = token`, never on the command line where `ps` shows it); poll health at `http://{host}:{port}/api/health?lab_token={token}` when a token is set (the 303 counts as up: catch `urllib.error.HTTPError` and treat any HTTP answer as up); when a token is set print

```python
        print(f"open on the phone: {phone_url(host, port, token)}", flush=True)
        print("  (same Wi-Fi or your VPN home; the token stays valid until --new-token)", flush=True)
```

and do not open the desktop browser. In `main()` add:

```python
    ap.add_argument("--phone", action="store_true", help="serve on the LAN for a phone, behind a token")
    ap.add_argument("--host", help="LAN address to bind with --phone (default: the default route's)")
    ap.add_argument("--new-token", action="store_true", help="rotate the phone's token")
```

and serve with `host = args.host or lan_address()` and `token = phone_token(TOKEN_FILE, args.new_token)` when `--phone`, else as before. Update the module docstring's Usage with `--phone`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -n0 tests/test_review_session.py -q`
Expected: pass. Check `build/` is gitignored: `/usr/bin/git check-ignore build/review_phone_token` prints the path.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add scripts/review_session.py tests/test_review_session.py
/usr/bin/git commit -m "feat(lab): review_session --phone serves the queue to a phone"
```

---

### Task 3: The queue knows a started walk; Continue

**Files:**
- Modify: `src/splitsmith/lab/core.py` (`FixtureRecord.walk_decisions`, filled in `list_fixtures`)
- Modify: `src/splitsmith/ui/server.py` (`DevReviewQueueItem.walk_decisions`, set in `dev_review_queue`)
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`DevReviewQueueItem.walk_decisions?: number`)
- Modify: `src/splitsmith/ui_static/src/lib/walk.ts` (`continueTarget`)
- Modify: `src/splitsmith/ui_static/src/pages/dev/DevReviewQueue.tsx` (Continue button)
- Test: `tests/test_ui_server.py`, `src/splitsmith/ui_static/src/lib/walk.test.ts`, `src/splitsmith/ui_static/src/pages/dev/DevReviewQueue.test.tsx`

**Interfaces:**
- Produces: `continueTarget(pending: ReadonlyArray<DevReviewQueueItem>): DevReviewQueueItem | null`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_ui_server.py`, after `test_dev_review_queue_includes_batch_promoted_unlabeled`:

```python
def test_dev_review_queue_counts_walk_decisions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Continue resumes a walk already started: the queue says how far each got."""
    import splitsmith.lab.core as lab_core

    _seed_review_state_fixtures(tmp_path / "fixtures")
    path = tmp_path / "fixtures" / "stage-shots-hfo-masters-2026-stage1-s0fe3d797.json"
    doc = json.loads(path.read_text())
    doc["audit_events"] = [
        {"kind": "walk_decided", "payload": {"stop": "a"}},
        {"kind": "walk_decided", "payload": {"stop": "b"}},
        {"kind": "marker_moved", "payload": {}},
    ]
    path.write_text(json.dumps(doc))
    monkeypatch.setattr(lab_core, "DEFAULT_FIXTURES_ROOT", tmp_path / "fixtures")
    client = _MatchClient(_match_create_app(project_root=tmp_path / "match", project_name="x"))
    pending = {i["slug"]: i for i in client.get("/api/dev/review-queue").json()["pending"]}
    assert pending["stage-shots-hfo-masters-2026-stage1-s0fe3d797"]["walk_decisions"] == 2
```

In `lib/walk.test.ts`:

```ts
describe("continueTarget", () => {
  const item = (slug: string, extra: Partial<DevReviewQueueItem> = {}) =>
    ({ slug, audit_path: `/f/${slug}.json`, review_status: "needs_review", walk_decisions: 0, ...extra }) as DevReviewQueueItem;

  it("resumes a started walk before the queue's order", () => {
    expect(continueTarget([item("a"), item("b", { walk_decisions: 3 })])?.slug).toBe("b");
  });
  it("falls back to the first fixture needing review", () => {
    expect(continueTarget([item("a", { review_status: "reviewed" }), item("b")])?.slug).toBe("b");
  });
  it("has nothing to continue on an empty queue", () => {
    expect(continueTarget([])).toBeNull();
  });
});
```

(import `continueTarget` from `./walk` and `type DevReviewQueueItem` from `./api`.)

In `DevReviewQueue.test.tsx`, a test that with the queue mocked to two pending items (the second with `walk_decisions: 2`) and `window.matchMedia` reporting a phone (`(max-width: 767px)` matches), a link named "Continue" points at `walkHref` of the second item. Follow the file's existing mocking of `api.devReviewQueue`; mock matchMedia with `vi.stubGlobal("matchMedia", (q: string) => ({ matches: true, media: q, addEventListener() {}, removeEventListener() {} }))`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -n0 tests/test_ui_server.py -k walk_decisions -q` and, in `src/splitsmith/ui_static`, `pnpm exec vitest run src/lib/walk.test.ts src/pages/dev/DevReviewQueue.test.tsx`
Expected: KeyError `walk_decisions`; `continueTarget is not a function`; no "Continue" link.

- [ ] **Step 3: Implement**

`FixtureRecord`: `walk_decisions: int = 0` with the comment `# walk_decided audit events: how far the review walk has got (Continue).` In `list_fixtures`:

```python
        events = payload.get("audit_events")
        walk_decisions = (
            sum(1 for e in events if isinstance(e, dict) and e.get("kind") == "walk_decided")
            if isinstance(events, list)
            else 0
        )
```

and `walk_decisions=walk_decisions` in the constructor. `DevReviewQueueItem`: `walk_decisions: int = 0`; set `walk_decisions=fx.walk_decisions`. `api.ts`: `walk_decisions?: number;` with a one-line comment.

`lib/walk.ts`, beside `nextFixtureToReview`:

```ts
/** Where a short session picks up: a walk already started (and not signed
 *  off, so still pending), else the queue's first fixture needing review. */
export function continueTarget(pending: ReadonlyArray<DevReviewQueueItem>): DevReviewQueueItem | null {
  const open = pending.filter((i) => i.review_status === "needs_review");
  return open.find((i) => (i.walk_decisions ?? 0) > 0) ?? open[0] ?? null;
}
```

`DevReviewQueue.tsx`: `const phone = useIsMobile();` and, when `phone` and `continueTarget(pending)` is not null, render above the queue a full-width primary `Link` button "Continue" with the fixture's short name (`venue` and `stage_number`) under it, `to={walkHref(t.audit_path, t.video_path ?? null)}`, height at least 44 px.

- [ ] **Step 4: Run the tests**

Same commands as Step 2. Expected: pass.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/lab/core.py src/splitsmith/ui/server.py tests/test_ui_server.py src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/lib/walk.ts src/splitsmith/ui_static/src/lib/walk.test.ts src/splitsmith/ui_static/src/pages/dev/DevReviewQueue.tsx src/splitsmith/ui_static/src/pages/dev/DevReviewQueue.test.tsx
/usr/bin/git commit -m "feat(lab): the queue's Continue resumes a started walk"
```

---

### Task 4: `useWalk` and `Strip` out of `Walk.tsx`

A pure refactor. The proof is the untouched `Walk.test.tsx`.

**Files:**
- Create: `src/splitsmith/ui_static/src/components/review/useWalk.ts`
- Create: `src/splitsmith/ui_static/src/components/review/Strip.tsx`
- Modify: `src/splitsmith/ui_static/src/components/review/Walk.tsx`

**Interfaces:**
- Produces (used by Task 5):

```ts
export interface WalkActions {
  confirm: () => void;      // a stop: record and move on; past the end: sign off (two presses on a count mismatch)
  shot: () => void;
  notShot: () => void;
  riseFoot: () => void;
  nudge: (ms: number) => void;
  place: (t: number) => void;
  back: () => void;
  listen: () => void;       // props.onListen(anchor)
  jump: (t: number) => void; // nearest stop
  firstUndecided: () => void;
}
export interface WalkModel {
  stops: WalkStop[];
  index: number;
  stop: WalkStop | null;
  marker: AuditMarker | null;
  state: "shot" | "not_shot";
  time: number;
  anchor: number;           // where this stop was found
  level: number;
  decidedCount: number;
  keptCount: number;
  check: ReturnType<typeof countCheck>;
  armed: boolean;
  isDecided: (stop: WalkStop) => boolean;
  can: { riseFoot: boolean; nudge: boolean; notShot: boolean; shot: boolean };
  actions: WalkActions;
}
/** ``holdLoop``: do not ask the page to loop yet (the phone's Start screen). */
export function useWalk(props: WalkProps, opts?: { holdLoop?: boolean }): WalkModel;
```

and `Strip` with its current props, plus the `CLOSE_HEIGHT` / `CONTEXT_HEIGHT` constants, exported from `Strip.tsx`.

- [ ] **Step 1: Record the baseline**

Run (in `src/splitsmith/ui_static`): `pnpm exec vitest run src/components/review src/pages/Review.test.tsx`
Expected: all pass. Note the count.

- [ ] **Step 2: Move `Strip`**

Cut `Strip`, `CLOSE_HEIGHT`, `CONTEXT_HEIGHT`, `H` and the doc comments above them from `Walk.tsx` into `Strip.tsx` unchanged, add `export` to `Strip`, `CLOSE_HEIGHT` and `CONTEXT_HEIGHT`, carry over the imports `Strip` uses (`useMemo`, `AuditMarker`, `SnapPeaks`, `DecodedAudio`, `windowBins`), and import them in `Walk.tsx`.

- [ ] **Step 3: Move the state and actions into `useWalk`**

`useWalk(props)` takes over, verbatim, everything in `Walk` from `const { markers, peaks, ... } = props;` down to and including `place`, plus the two effects (the anchor/loop effect and the focus effect). The loop effect calls `onLoopStop` only when `opts?.holdLoop` is not true, and depends on `opts?.holdLoop` too, so lifting the hold starts the current stop. The keyboard effect's `switch` bodies become the `actions` functions, each body moved as is:

```ts
  const confirm = () => {
    if (!stop) {
      if (!busy && decidedCount === stops.length) {
        if (check.ok || armed) props.onDone();
        else setArmed(true);
      }
      return;
    }
    // body of `case "confirm"` unchanged
  };
  const back = () => {
    if (!stop) {
      if (stops.length > 0) setIndex(stops.length - 1);
      return;
    }
    setIndex((i) => Math.max(0, i - 1));
  };
  const shot = () => { if (stop) makeShot(); };
  const notShot = () => { if (stop) { /* body of `case "not_shot"` */ } };
  const riseFoot = () => { if (!stop || state !== "shot" || !marker) return; const foot = snapToLeadingEdge(time, peaks); if (foot != null) place(foot); };
  const nudge = (ms: number) => { if (stop && state === "shot" && marker) place(time + ms / 1000); };
  const listen = () => { if (stop) props.onListen(anchorRef.current); };
  const jump = (t: number) => { const i = nearestStop(stops, t); if (i >= 0) setIndex(i); };
  const firstUndecided = () => setIndex(firstUndecidedFrom(0));
```

`can` is `{ riseFoot: state === "shot" && !!marker, nudge: state === "shot" && !!marker, notShot: state === "shot", shot: state !== "shot" }` (each false when `stop` is null).

`Walk` becomes `const w = useWalk(props);` plus its render, reading `w.*`; its keyboard effect maps `walkActionForKey` to `w.actions` (`confirm`, `shot`, `not_shot` -> `notShot`, `rise_foot` -> `riseFoot`, `nudge` -> `nudge(action.ms)`, `listen`, `back`; `guide` and `loop` stay on `props`), with the same `isTypingTextTarget`, `preventDefault`, `stopPropagation` and capture phase. The end screen's "Go to the first undecided stop" calls `w.actions.firstUndecided`, the whole-stage strip's `onPick` calls `w.actions.jump`.

The keyboard effect closes over `w`, so depend on `[w, props, toggleGuide]` (the object is rebuilt every render, which re-binds the listener each render exactly as the current dependency list does).

- [ ] **Step 4: Run the untouched suite**

Run: `pnpm exec vitest run src/components/review src/pages/Review.test.tsx`, then `pnpm exec tsc -b --noEmit` and `pnpm exec eslint src/components/review src/pages/Review.tsx`
Expected: the same count as Step 1, all pass, no type or lint errors. `/usr/bin/git diff --stat -- src/splitsmith/ui_static/src/components/review/Walk.test.tsx` is empty.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/ui_static/src/components/review/useWalk.ts src/splitsmith/ui_static/src/components/review/Strip.tsx src/splitsmith/ui_static/src/components/review/Walk.tsx
/usr/bin/git commit -m "refactor(lab): the walk's rules in useWalk, its strip on its own"
```

---

### Task 5: `MobileWalk` and the page's choice

**Files:**
- Create: `src/splitsmith/ui_static/src/components/review/MobileWalk.tsx`
- Create: `src/splitsmith/ui_static/src/components/review/MobileWalk.test.tsx`
- Modify: `src/splitsmith/ui_static/src/lib/walk.ts` (`readLoopOn` / `writeLoopOn` take a device)
- Modify: `src/splitsmith/ui_static/src/lib/walk.test.ts`
- Modify: `src/splitsmith/ui_static/src/pages/Review.tsx`

**Interfaces:**
- Consumes: `useWalk`, `WalkProps`, `Strip`, `CLOSE_HEIGHT`, `CONTEXT_HEIGHT` (Task 4).
- Produces: `MobileWalk(props: WalkProps)`; `readLoopOn(device: "desktop" | "phone"): boolean` (phone defaults on, its own key `splitsmith.reviewWalk.loop.phone`), `writeLoopOn(on: boolean, device: "desktop" | "phone")`.

- [ ] **Step 1: Write the failing tests**

`lib/walk.test.ts`, extend "loop preference":

```ts
  it("starts on for the phone, kept apart from the desktop's", () => {
    window.localStorage.removeItem("splitsmith.reviewWalk.loop.phone");
    expect(readLoopOn("phone")).toBe(true);
    writeLoopOn(false, "phone");
    expect(readLoopOn("phone")).toBe(false);
    expect(readLoopOn("desktop")).toBe(false);
  });
```

(Update the existing desktop case to pass `"desktop"`.)

`MobileWalk.test.tsx`, reusing `Walk.test.tsx`'s `envelope` and `m` helpers (copy them; they are 20 lines) and a harness rendering `MobileWalk` with the same props:

```tsx
describe("MobileWalk", () => {
  it("asks for nothing to play until Start, then loops the first stop", () => {
    const onLoopStop = vi.fn();
    render(<Harness initial={markersAll} loop onLoopStop={onLoopStop} record={vi.fn()} />);
    const start = screen.getByRole("button", { name: "Start" });
    expect(onLoopStop).not.toHaveBeenCalled();
    fireEvent.click(start);
    expect(onLoopStop).toHaveBeenLastCalledWith(0.5);
    expect(screen.getByText("Stop 1 of 3")).toBeTruthy();
  });

  it("Confirm records the stop and moves on, and the next stop loops", () => {
    const record = vi.fn();
    const onLoopStop = vi.fn();
    render(<Harness initial={markersAll} loop onLoopStop={onLoopStop} record={record} />);
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(record).toHaveBeenCalledWith(WALK_DECIDED_EVENT, expect.objectContaining({ stop: "cand-1" }));
    expect(screen.getByText("Stop 2 of 3")).toBeTruthy();
    expect(onLoopStop).toHaveBeenLastCalledWith(1.0);
  });

  it("disables what does nothing at this stop", () => {
    render(<Harness initial={markersAll} record={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" })); // stop 2: a rejected candidate
    for (const name of ["Rise foot", "-5 ms", "-1 ms", "+1 ms", "+5 ms", "Not a shot"]) {
      expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
    }
    expect((screen.getByRole("button", { name: "Shot" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("nudges and puts the shot back on the rule with buttons", () => {
    const record = vi.fn();
    render(<Harness initial={markersAll} record={record} />);
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    fireEvent.click(screen.getByRole("button", { name: "+5 ms" }));
    expect(screen.getByText(/5 ms after the rise foot/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Rise foot" }));
    expect(screen.getByText("On the rise foot (the rule).")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `pnpm exec vitest run src/lib/walk.test.ts src/components/review/MobileWalk.test.tsx`
Expected: FAIL (`readLoopOn("phone")` returns false; `MobileWalk` not found).

- [ ] **Step 3: Implement**

`lib/walk.ts`:

```ts
const LOOP_KEYS = { desktop: "splitsmith.reviewWalk.loop", phone: "splitsmith.reviewWalk.loop.phone" } as const;

/** Whether each stop's sound repeats as the walk reaches it: off on the
 *  desktop until turned on, on for the phone (no keyboard to ask for it),
 *  each remembered on its own. */
export function readLoopOn(device: "desktop" | "phone"): boolean {
  try {
    const v = window.localStorage.getItem(LOOP_KEYS[device]);
    return v == null ? device === "phone" : v === "1";
  } catch {
    return device === "phone";
  }
}

export function writeLoopOn(on: boolean, device: "desktop" | "phone"): void {
  try {
    window.localStorage.setItem(LOOP_KEYS[device], on ? "1" : "0");
  } catch {
    // Without storage the loop starts at the device's default.
  }
}
```

`MobileWalk.tsx`: `const w = useWalk(props)`; a `started` state (false). Until started, render the header and one full-width `Button` "Start" (`min-h-11`) with the line "Plays each stop on repeat. Confirm moves on." and on click: `setStarted(true); props.onLoopStop(w.anchor)` when `props.loop`, else `props.onListen(w.anchor)`. Call `useWalk(props, { holdLoop: !started })` (Task 4's option), so nothing asks to play before the Start tap; the desktop `Walk` passes nothing. Start calls `onLoopStop` itself inside the tap, because a phone only lets a gesture start sound; the effect re-firing when the hold lifts repeats the same window, which is harmless. After Start, one column (`flex flex-col gap-2 pb-40`, so the fixed bar never covers content):

1. Header row: `Stop {index+1} of {stops.length}`, `{keptCount} / {expectedRounds}`, a `Loop` toggle (`aria-pressed`, `props.onToggleLoop`), a `Play` toggle (`w.actions.listen`), a `Guide` button opening `WalkGuide` in the `components/ui/Sheet` primitive.
2. The prompt box (question, `now: ...`, placement line, flags), the same markup as `Walk`'s.
3. `Strip` 80 ms (`cssHeight="clamp(160px, 28vh, 260px)"`, `onPlace={w.actions.place}`), `Strip` 300 ms (`CLOSE_HEIGHT`, `onPlace`), `Strip` 3 s (`CONTEXT_HEIGHT`).
4. `<details>` "Whole stage": the stage `Strip` with `onPick={w.actions.jump}`.
5. `<details>` "Video": `props.aside`.
6. Fixed bottom bar: `fixed inset-x-0 bottom-0 border-t border-border bg-surface p-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))]`, two rows of `Button`s, each `min-h-11`:
   - `-5 ms`, `-1 ms`, `+1 ms`, `+5 ms` (`w.actions.nudge(...)`, `disabled={!w.can.nudge}`), `Back`.
   - `Not a shot` (`disabled={!w.can.notShot}`), `Shot` (`disabled={!w.can.shot}`), `Rise foot` (`disabled={!w.can.riseFoot}`), `Confirm` (`variant="primary"`, `flex-[2]`).
   Past the last stop the bar is replaced by the end screen: the count check and one full-width `Confirm` ("Sign off with N shots", a second press when `armed` on a mismatch, `w.actions.confirm`), or "Go to the first undecided stop".

`Review.tsx`: `const phone = useIsMobile() || params.get("phone") === "1";`, the loop state reads and writes with `phone ? "phone" : "desktop"` (`useState(() => readLoopOn(phone ? "phone" : "desktop"))`), and in walk mode render `<MobileWalk {...walkProps} />` when `phone`, else `<Walk {...walkProps} />`, with `walkProps` the object of props the page passes today (extract it once so both get the same). On the phone the page title and editor chrome stay hidden exactly as in walk mode now.

- [ ] **Step 4: Run the tests**

Run: `pnpm exec vitest run src/lib/walk.test.ts src/components/review src/pages/Review.test.tsx`, `pnpm exec tsc -b --noEmit`, `pnpm exec eslint src/components/review src/pages/Review.tsx src/lib/walk.ts`
Expected: pass, clean.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add src/splitsmith/ui_static/src/components/review/MobileWalk.tsx src/splitsmith/ui_static/src/components/review/MobileWalk.test.tsx src/splitsmith/ui_static/src/lib/walk.ts src/splitsmith/ui_static/src/lib/walk.test.ts src/splitsmith/ui_static/src/pages/Review.tsx
/usr/bin/git commit -m "feat(lab): the walk on a phone, thumb buttons over the same rules"
```

---

### Task 6: Check it on a phone-sized screen, then on the phone

**Files:**
- Modify: `docs/superpowers/specs/2026-10-11-mobile-fixture-review-design.md` (the three rulings above, under a "Changed while planning" heading)

- [ ] **Step 1: Full frontend and Python suites**

Run: `pnpm exec vitest run` (in `ui_static`) and `uv run pytest -q tests/test_lab_token.py tests/test_review_session.py tests/test_ui_server.py`
Expected: pass, apart from `src/lib/lazyPage.test.tsx`'s known local failure (it fails on main too); report it as such.

- [ ] **Step 2: Build and serve with a token on loopback**

Run: `pnpm build`, then `SPLITSMITH_AUTO_SYNC=0 SPLITSMITH_HOME=~/.claude-tmp/walk-home uv run splitsmith ui --lab --skip-system-check --no-browser --port 5183 --lab-token test-token` in the background (loopback with a token exercises the gate without exposing anything).

- [ ] **Step 3: Playwright at 390 x 844**

Open `http://127.0.0.1:5183/dev/review?lab_token=test-token`, check the redirect drops the token and the queue shows Continue; follow it; check Start, then Confirm moves on and the next stop's window loops (sample `currentTime` as in the loop PR), the disabled buttons on a rejected candidate, a tap on the 80 ms strip places the shot, no horizontal scroll (`document.documentElement.scrollWidth <= 390`), the bottom bar does not cover the last strip. Screenshot the walk and the end screen and look at them. A request without the cookie (`fetch` in a fresh context) answers 404.

- [ ] **Step 4: Restore the fixture the check wrote**

Run: `/usr/bin/git checkout -- tests/fixtures/` and confirm `/usr/bin/git status --short tests/fixtures` is empty.

- [ ] **Step 5: Commit the spec update; hand the phone URL to the user**

```bash
/usr/bin/git add docs/superpowers/specs/2026-10-11-mobile-fixture-review-design.md
/usr/bin/git commit -m "docs: rulings made while planning the phone review"
```

Then ask the user to run `uv run python scripts/review_session.py --phone` on their review branch and open the printed URL on the phone over the Wi-Fi.
