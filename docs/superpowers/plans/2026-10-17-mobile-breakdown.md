# Breakdown on a phone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Review Breakdown's region proposals on a phone (Keep, Delete,
change kind, adjust an edge to the frame with the video following), on top
of the real frame rate of each clip.

**Architecture:** Two PRs. PR 1 reads each trim's frame rate from its MP4
boxes (`media_check`), carries it on the coach payload as `fps`, and uses it
for the desktop nudge. PR 2 adds `MobileBreakdown`, rendered instead of the
desktop gate below 768 px. Its rules are pure modules (`proposalQueue`,
`focusWindow`, `edgeDrag`) over the existing region rules (`lib/events.ts`,
`laneDrag.ts`). Saving reuses `useStageEvents` and the workspace reuses
`useStageWorkspace` unchanged, except that read-only follows the capability
alone.

**Tech Stack:** Python 3.11 / FastAPI / pytest; React 18 / TypeScript /
Tailwind / vitest / Playwright (verification only).

**Spec:** `docs/superpowers/specs/2026-10-17-mobile-breakdown-design.md`

## Global Constraints

- No new dependencies (Python or SPA).
- All times are seconds from the beep, as `StageEvent.start` / `end` and
  `CoachShot.time_from_beep` are. The video is driven only through
  `ws.seekFromBeep`, never `videoRef.current.currentTime`.
- Every region commit goes through `regions.change(next, commit)`. A commit
  whose lanes overlap never goes out (the hook already enforces it). Never
  commit per drag frame: frames are `change(next, false)`, the release is
  one `change(next, true)`.
- Touch targets are at least 44 px.
- The UI budget applies (CLAUDE.md "UI: the visual budget"): primitives
  from `components/ui` only (`Button`, `Chip`, `PageHeader`, `Label`,
  `Sheet`, `Menu`, `numeral`). One `primary` Button per view (Keep). No
  arbitrary `text-[...]` outside `components/ui`. Red only for the brand,
  the primary action, the current position and focus. No issue numbers in
  UI copy.
- User-visible text is ASCII: no em or en dash, no curly quotes, no `...`
  character; `...` is three periods.
- Python: Black at 110, Ruff, type hints. SPA: `pnpm typecheck` (that is
  `tsc -b`), `pnpm lint`, `pnpm test`. Never run prettier in `ui_static`.
- Worktree rules: run git as `/usr/bin/git`; never `git add src` (the
  node_modules symlink); add files by path.
- pytest needs the project ffmpeg on PATH:
  `env PATH=/Users/mathias/work/splitsmith/desktop/build/bin:$PATH uv run pytest ...`.
- The SPA lives in `src/splitsmith/ui_static/`; run pnpm there.

## Review Focus

1. **A region near the clip's ends.** A proposal starting under 2 s from
   the beep, or ending at the stage time, must still get a full-width
   window and reachable handles. Pinned in Task 4 (`focusWindow` clamps
   and keeps 3 s).
2. **The open region disappears** (a 409 reload or another writer drops
   it). The page must move to the next proposal, never render a strip for
   a missing id. Pinned in Task 7 ("a region that vanishes on reload opens
   the next").
3. **An edge pushed into a same-lane neighbour or below 50 ms.** It stops
   there; the saved edge in the pill never crosses. Pinned in Task 5
   (`edgeFrame` clamps).
4. **A gesture interrupted by the OS** (a call, an iOS edge swipe:
   `pointercancel`). It restores the region as it was, like Esc on the
   desktop, and saves nothing. Pinned in Task 6 (EdgePad) and Task 7
   (the page restores).
5. **A desktop mirror on hosted.** Read-only: no Keep, no edge mode, one
   line saying where to edit. No PUT ever leaves. Pinned in Task 7.

---

# PR 1: the frame rate

Branch `feat/clip-frame-rate` from `origin/main`.

### Task 1: `media_check` reads the video track's frame rate

**Files:**
- Modify: `src/splitsmith/media_check.py`
- Modify: `tests/media_bytes.py`
- Test: `tests/test_media_check.py`

**Interfaces:**
- Produces: `MediaCheck.frame_rate: float | None` (frames per second of the
  video track, rounded to 3 decimals; None when the track has no `mdhd`
  or a zero duration). `valid_mp4_cached(path).frame_rate` is what Task 2
  reads.

- [ ] **Step 1: Give the test bytes an `mdhd`.** In `tests/media_bytes.py`,
  replace `_trak` and `tiny_mp4` with:

```python
def _trak(handler: bytes, samples: int, *, media_timescale: int = 1000, media_duration: int | None = None) -> bytes:
    hdlr = _full(b"hdlr", b"\x00\x00\x00\x00" + handler + b"\x00" * 12 + b"\x00")
    mdhd = (
        _full(b"mdhd", struct.pack(">IIII", 0, 0, media_timescale, media_duration) + b"\x00\x00\x00\x00")
        if media_duration is not None
        else b""
    )
    stsz = _full(b"stsz", struct.pack(">II", 1000, samples))
    stbl = _box(b"stbl", stsz)
    minf = _box(b"minf", stbl)
    mdia = _box(b"mdia", mdhd + hdlr + minf)
    return _box(b"trak", mdia)


def tiny_mp4(
    *,
    duration_s: float = 1.0,
    video_samples: int = 25,
    with_video: bool = True,
    with_mdhd: bool = True,
) -> bytes:
    timescale = 1000
    mvhd = _full(b"mvhd", struct.pack(">IIII", 0, 0, timescale, int(duration_s * timescale)) + b"\x00" * 80)
    video = (
        _trak(b"vide", video_samples, media_duration=int(duration_s * timescale) if with_mdhd else None)
        if with_video
        else b""
    )
    traks = video + _trak(b"soun", 40)
    moov = _box(b"moov", mvhd + traks)
    ftyp = _box(b"ftyp", b"isom" + b"\x00\x00\x02\x00" + b"isomiso2avc1mp41")
    mdat = _box(b"mdat", b"\x00" * 64)
    return ftyp + moov + mdat
```

- [ ] **Step 2: Write the failing tests** in `tests/test_media_check.py`:

```python
def test_the_video_tracks_frame_rate_comes_from_its_mdhd(tmp_path: Path) -> None:
    check = media_check.valid_mp4(_write(tmp_path / "fifty.mp4", tiny_mp4(duration_s=2.0, video_samples=100)))
    assert check.ok, check.reason
    assert check.frame_rate == pytest.approx(50.0)


def test_no_mdhd_means_no_frame_rate_and_the_file_still_passes(tmp_path: Path) -> None:
    check = media_check.valid_mp4(_write(tmp_path / "bare.mp4", tiny_mp4(with_mdhd=False)))
    assert check.ok
    assert check.frame_rate is None


def test_a_broken_file_has_no_frame_rate(tmp_path: Path) -> None:
    assert media_check.valid_mp4(_write(tmp_path / "bad.mp4", corrupt_moov_mp4())).frame_rate is None
```

  And extend the integration test
  `test_a_real_ffmpeg_file_and_a_full_range_one_pass` with, right after
  `assert media_check.valid_mp4(src).ok`:

```python
    from tests.synthetic_media import SYNTHETIC_FPS_DEN, SYNTHETIC_FPS_NUM

    assert media_check.valid_mp4(src).frame_rate == pytest.approx(SYNTHETIC_FPS_NUM / SYNTHETIC_FPS_DEN, abs=0.01)
```

- [ ] **Step 3: Run them to see them fail.**
  Run: `uv run pytest -n0 tests/test_media_check.py -q`
  Expected: FAIL. `MediaCheck` has no `frame_rate`.

- [ ] **Step 4: Implement.** In `src/splitsmith/media_check.py`:

```python
class MediaCheck(BaseModel):
    ok: bool
    reason: str | None = None
    duration_s: float | None = None
    video_samples: int = 0
    #: The video track's frames per second (samples over its mdhd seconds), when its boxes say.
    frame_rate: float | None = None
```

  Replace `_track` so it also returns the track's media time:

```python
def _track(
    data: bytes, start: int, end: int, depth: int = 0
) -> tuple[bytes | None, int, tuple[int, int] | None]:
    if depth > _MAX_DEPTH:
        raise _StructureError("boxes nested too deep")
    handler: bytes | None = None
    samples = 0
    media: tuple[int, int] | None = None
    for typ, s, e in _boxes(data, start, end):
        if typ == b"hdlr" and e - s >= 12:
            handler = data[s + 8 : s + 12]
        elif typ == b"stsz" and e - s >= 12:
            samples = struct.unpack(">I", data[s + 8 : s + 12])[0]
        elif typ == b"mdhd" and e - s >= 20:
            if data[s] == 1 and e - s >= 32:
                media = struct.unpack(">IQ", data[s + 20 : s + 32])
            else:
                media = struct.unpack(">II", data[s + 12 : s + 20])
        elif typ in _CONTAINERS:
            h, n, m = _track(data, s, e, depth + 1)
            handler = handler or h
            samples = samples or n
            media = media or m
    return handler, samples, media
```

  In `_check_moov`, track the rate of the video track with the most
  samples:

```python
def _check_moov(data: bytes) -> MediaCheck:
    duration: float | None = None
    video_samples = 0
    frame_rate: float | None = None
    for typ, s, e in _boxes(data, 0, len(data)):
        if typ == b"mvhd" and e - s >= 20:
            version = data[s]
            if version == 1 and e - s >= 32:
                timescale, dur = struct.unpack(">IQ", data[s + 20 : s + 32])
            else:
                timescale, dur = struct.unpack(">II", data[s + 12 : s + 20])
            if timescale:
                duration = dur / timescale
        elif typ == b"trak":
            handler, samples, media = _track(data, s, e)
            if handler == b"vide" and samples > video_samples:
                video_samples = samples
                frame_rate = None
                if media is not None and media[0] and media[1]:
                    frame_rate = round(samples * media[0] / media[1], 3)
    if video_samples <= 0:
        return MediaCheck(ok=False, reason="no video track with samples", duration_s=duration)
    return MediaCheck(ok=True, duration_s=duration, video_samples=video_samples, frame_rate=frame_rate)
```

- [ ] **Step 5: Run the tests to see them pass.**
  Run: `env PATH=/Users/mathias/work/splitsmith/desktop/build/bin:$PATH uv run pytest -n0 tests/test_media_check.py -q`
  Expected: all pass, the integration one included.

- [ ] **Step 6: Check against the real camera.** Run:
  `uv run python -c "from pathlib import Path; from splitsmith.media_check import valid_mp4; print(valid_mp4(Path('/Volumes/X9/matches/little-trouble-in-big-satra-2026/shooters/s_70f3bf39/trimmed/stage2_cam_e194cea17bb9_web.mp4')).frame_rate)"`
  Expected: `50.0` (skip the step if `/Volumes/X9` is not mounted).

- [ ] **Step 7: Commit.**

```bash
/usr/bin/git add src/splitsmith/media_check.py tests/media_bytes.py tests/test_media_check.py
/usr/bin/git commit -m "feat(media): read a clip's frame rate from its moov"
```

### Task 2: `fps` on the coach payload, used by the desktop nudge

**Files:**
- Modify: `src/splitsmith/ui/server.py` (next to `_scrub_version_for`
  around line 8203; the coach video dict around line 14430)
- Modify: `src/splitsmith/ui_static/src/lib/api.ts` (`CoachVideoEntry`,
  around line 1861)
- Modify: `src/splitsmith/ui_static/src/components/coach/StageBand.tsx`
- Create: `src/splitsmith/ui_static/src/lib/clipFps.ts`
- Test: `tests/test_coach_api.py`, `src/splitsmith/ui_static/src/lib/clipFps.test.ts`

**Interfaces:**
- Consumes: `media_check.valid_mp4_cached(path).frame_rate` (Task 1).
- Produces: `CoachVideoEntry.fps?: number | null`.
  `clipFps(videos: CoachVideoEntry[] | undefined): number` gives the
  primary's fps, else `DEFAULT_FPS` (30). Tasks 5 and 7 use both.

- [ ] **Step 1: Write the failing Python tests** in `tests/test_coach_api.py`,
  after `test_get_coach_video_entries_carry_trim_and_scrub_versions`:

```python
def test_get_coach_video_entries_carry_the_trims_frame_rate(tmp_path: Path) -> None:
    from tests.media_bytes import tiny_mp4

    client, base = _bootstrap_legacy_trim(tmp_path, stage_numbers=(1,))
    trims = list(tmp_path.rglob("stage1_cam_*_trimmed.mp4"))
    assert len(trims) == 1
    trims[0].write_bytes(tiny_mp4(duration_s=2.0, video_samples=100))
    primary = client.get(f"{base}/shooters/me/stages/1/coach").json()["videos"][0]
    assert primary["fps"] == pytest.approx(50.0)


def test_get_coach_fps_is_null_when_the_trim_does_not_say(tmp_path: Path) -> None:
    client, base = _bootstrap_legacy_trim(tmp_path, stage_numbers=(1,))  # the fixture's trim is not an MP4
    assert client.get(f"{base}/shooters/me/stages/1/coach").json()["videos"][0]["fps"] is None


def test_get_coach_source_video_has_no_fps(tmp_path: Path) -> None:
    client, _audit, base = _bootstrap(tmp_path)
    assert client.get(f"{base}/shooters/me/stages/1/coach").json()["videos"][0]["fps"] is None
```

- [ ] **Step 2: Run them to see them fail.**
  Run: `uv run pytest -n0 tests/test_coach_api.py -k fps -q`
  Expected: FAIL with `KeyError: 'fps'`.

- [ ] **Step 3: Implement the server side.** In `src/splitsmith/ui/server.py`,
  add after `_scrub_version_for`:

```python
def _frame_rate_for(root: Path, stage_number: int, video: StageVideo, project: MatchProject) -> float | None:
    """The local audit trim's frame rate from its own boxes (``media_check``,
    cached per file version), or None. The rendition is cut from the trim at
    the same rate. Local files only, like :func:`_trim_version_for`: a trim
    that is not on this disk (hosted, a mirror) has none and the SPA uses 30.
    """
    try:
        trim = audio_helpers.resolve_trim_for_read(root, stage_number, video, project=project)
    except OSError:
        return None
    if trim is None:
        return None
    return valid_mp4_cached(trim).frame_rate
```

  Import `valid_mp4_cached` at the top with the other module imports
  (`from ..media_check import valid_mp4_cached`; check whether
  `server.py` already imports from `media_check` and extend that line).
  In the coach video dict (the loop that writes `"trim_version"` and
  `"scrub_version"` around line 14438), add:

```python
                    # Frame steps and the pill's frame number (spec 2026-10-17): null
                    # when the trim is not on this disk; the SPA falls back to 30.
                    "fps": _frame_rate_for(root, stg.stage_number, v, project),
```

- [ ] **Step 4: Run the Python tests.**
  Run: `uv run pytest -n0 tests/test_coach_api.py -q`
  Expected: PASS.

- [ ] **Step 5: Write the failing SPA test.** Create
  `src/splitsmith/ui_static/src/lib/clipFps.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { CoachVideoEntry } from "@/lib/api";
import { DEFAULT_FPS, clipFps } from "@/lib/clipFps";

const video = (role: "primary" | "secondary", fps: number | null | undefined): CoachVideoEntry =>
  ({ path: `${role}.mp4`, role, beep_in_clip: 5, kind: "trim", fps }) as CoachVideoEntry;

describe("clipFps", () => {
  it("is the primary's frame rate", () => {
    expect(clipFps([video("secondary", 25), video("primary", 50)])).toBe(50);
  });
  it("falls back to 30 when the server does not know it or sends nonsense", () => {
    expect(clipFps(undefined)).toBe(DEFAULT_FPS);
    expect(clipFps([video("primary", null)])).toBe(30);
    expect(clipFps([video("primary", undefined)])).toBe(30);
    expect(clipFps([video("primary", 0)])).toBe(30);
    expect(clipFps([video("primary", 1000)])).toBe(30);
  });
});
```

- [ ] **Step 6: Run it to see it fail.**
  Run (in `src/splitsmith/ui_static`): `pnpm vitest run src/lib/clipFps.test.ts`
  Expected: FAIL, module not found.

- [ ] **Step 7: Implement.** In `lib/api.ts`, add to `CoachVideoEntry`
  after `scrub_version`:

```ts
  /** The trim's frames per second from its own MP4 boxes; null when the
   *  trim is not on the server's disk. Read through ``clipFps``. */
  fps?: number | null;
```

  Create `lib/clipFps.ts`:

```ts
/**
 * Frames per second for frame steps and frame numbers: the primary
 * camera's clip as the server read it from the MP4, else 30. The cameras
 * splitsmith sees record at 25 to 60; anything outside 1-240 is a broken
 * file, not a frame rate.
 */
import type { CoachVideoEntry } from "@/lib/api";

export const DEFAULT_FPS = 30;

export function clipFps(videos: CoachVideoEntry[] | undefined): number {
  const fps = videos?.find((v) => v.role === "primary")?.fps;
  return typeof fps === "number" && fps >= 1 && fps <= 240 ? fps : DEFAULT_FPS;
}
```

  In `components/coach/StageBand.tsx`, import `clipFps` and compute
  `const fps = clipFps(coach.videos);` after the `if (!coach) return null;`
  line, then pass `fps={fps}` to `<Timeline>` (replacing `fps={30}`) and
  add `fps={fps}` to `<LaneEditor>`.

- [ ] **Step 8: Run the SPA checks.**
  Run (in `src/splitsmith/ui_static`): `pnpm vitest run src/lib/clipFps.test.ts src/pages/Breakdown && pnpm typecheck && pnpm lint`
  Expected: PASS.

- [ ] **Step 9: Commit, push and open the PR.**

```bash
/usr/bin/git add src/splitsmith/ui/server.py tests/test_coach_api.py \
  src/splitsmith/ui_static/src/lib/api.ts src/splitsmith/ui_static/src/lib/clipFps.ts \
  src/splitsmith/ui_static/src/lib/clipFps.test.ts src/splitsmith/ui_static/src/components/coach/StageBand.tsx
/usr/bin/git commit -m "fix(breakdown): frame steps use the clip's own frame rate"
/usr/bin/git push -u origin feat/clip-frame-rate
gh pr create --title "fix(breakdown): frame steps use the clip's own frame rate" --body "..."
```

  The PR body says: the desktop nudge and the pill's frame number assumed
  30 fps; 50 fps headcam footage moved 1.7 frames per nudge. Add a What's
  new entry (`whats-new` skill) only if the reviewer judges the nudge
  change user-visible; a fix to a feature people use is, so add
  `"breakdown-frame-steps"`, e.g. title "Frame steps match your camera",
  body "Arrow nudges on Breakdown now move one frame of your own footage,
  at 50 or 60 fps as well as 30."

---

# PR 2: the phone page

Branch `feat/mobile-breakdown` from `origin/main` after PR 1 merges.

### Task 3: The seams: read-only by capability, the rendition on phones, `dragFrame` without a pointer

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/useStageWorkspace.ts:477`
- Modify: `src/splitsmith/ui_static/src/lib/scrubSource.ts`, `lib/useScrubSource.ts`
- Modify: `src/splitsmith/ui_static/src/components/coach/laneDrag.ts:74`
- Test: `src/splitsmith/ui_static/src/lib/scrubSource.test.ts`

**Interfaces:**
- Produces: `scrubSource({ ..., phone })`. On a phone the rendition wins
  over `fullRes`. `dragFrame(drag: DragInit, ...)` accepts a gesture
  without pointer fields (Task 5 calls it). `StageView.eventsReadOnly`
  is the edit capability alone.

- [ ] **Step 1: Write the failing test** in `lib/scrubSource.test.ts`:

```ts
it("plays the rendition on a phone even when full resolution is on", () => {
  expect(scrubSource({ trimVersion: "t", scrubVersion: "s", fullRes: true, failed: false, phone: true })).toEqual({
    kind: "scrub",
    version: "s",
  });
  expect(scrubSource({ trimVersion: "t", scrubVersion: "s", fullRes: true, failed: false, phone: false })).toEqual({
    kind: "trim",
    version: "t",
  });
});
```

  Existing calls in that file pass no `phone`; make it optional.

- [ ] **Step 2: Run it to see it fail.**
  Run: `pnpm vitest run src/lib/scrubSource.test.ts`. Expected: FAIL.

- [ ] **Step 3: Implement.** In `lib/scrubSource.ts`:

```ts
export function scrubSource(args: {
  trimVersion: string | null | undefined;
  scrubVersion: string | null | undefined;
  fullRes: boolean;
  failed: boolean;
  /** A phone always plays the rendition: the full trim ran at 9 frames a second on Android (spike 2026-10-17). */
  phone?: boolean;
}): ScrubChoice {
  const { trimVersion, scrubVersion, fullRes, failed, phone = false } = args;
  if (scrubVersion && (!fullRes || phone) && !failed) return { kind: "scrub", version: scrubVersion };
  return { kind: "trim", version: trimVersion ?? null };
}
```

  Add one sentence to the module comment saying so. In `lib/useScrubSource.ts`,
  call `const phone = useIsMobile();` in the hook and pass `phone` into
  the `scrubSource({...})` call inside `choose` (and into its dependency
  list).

  In `lib/useStageWorkspace.ts`, change the read-only rule and its doc
  comment on `StageView.eventsReadOnly`:

```ts
  /** Regions are desktop-owned: a mirror without the edit capability reads them. */
  eventsReadOnly: boolean;
```

```ts
  const eventsReadOnly = capabilityDenied(project.capabilities, "edit");
```

  In `components/coach/laneDrag.ts`, widen `dragFrame`'s first
  parameter from `drag: Drag` to `drag: DragInit`. It reads only the
  gesture fields, and a full `Drag` is still assignable. Update its doc
  comment's first line: "One frame of a gesture (a LaneEditor drag past
  its threshold, or a phone edge drag)."

- [ ] **Step 4: Run every SPA test, since the read-only rule is shared.**
  Run: `pnpm vitest run && pnpm typecheck && pnpm lint`
  Expected: PASS. If a Coach or Breakdown test asserted read-only on a
  mobile viewport, it tested the dead rule: change it to assert read-only
  through `capabilities` instead, and say so in the commit message.

- [ ] **Step 5: Commit.**

```bash
/usr/bin/git add src/splitsmith/ui_static/src/lib/useStageWorkspace.ts src/splitsmith/ui_static/src/lib/scrubSource.ts \
  src/splitsmith/ui_static/src/lib/scrubSource.test.ts src/splitsmith/ui_static/src/lib/useScrubSource.ts \
  src/splitsmith/ui_static/src/components/coach/laneDrag.ts
/usr/bin/git commit -m "refactor(breakdown): read-only by capability, the rendition on phones"
```

### Task 4: `proposalQueue` and `focusWindow`

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/proposalQueue.ts`, `lib/proposalQueue.test.ts`
- Create: `src/splitsmith/ui_static/src/lib/focusWindow.ts`, `lib/focusWindow.test.ts`

**Interfaces:**
- Produces:
  - `proposals(events: StageEvent[]): StageEvent[]`: the `source === "auto"`
    regions by start, ties by `LANES` order.
  - `nextOpenId(events: StageEvent[], after: number): string | null`: the
    first proposal starting after `after`, else the first proposal, else
    null.
  - `prevOpenId(events: StageEvent[], before: number): string | null`.
  - `queuePosition(events: StageEvent[], id: string | null): { index: number; total: number } | null`.
  - `FOCUS_PAD_S = 2`, `FOCUS_MIN_S = 3`.
  - `focusWindow(region: { start: number; end: number }, stageTime: number): { from: number; to: number }`.
  - `fracOf(t: number, w: FocusWindow): number` (0..1, clamped).
  - `playWindow(region, stageTime): { from: number; to: number }`
    (0.5 s either side, clamped to `[0, stageTime]`).

- [ ] **Step 1: Write the failing tests.** `lib/proposalQueue.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { StageEvent } from "@/lib/api";
import { nextOpenId, prevOpenId, proposals, queuePosition } from "@/lib/proposalQueue";

const ev = (id: string, kind: StageEvent["kind"], start: number, source: "auto" | "manual" = "auto"): StageEvent => ({
  id,
  kind,
  start,
  end: start + 1,
  source,
});

const LIST = [ev("r2", "reload", 9), ev("m1", "movement", 3), ev("k", "reload", 5, "manual"), ev("r1", "reload", 3)];

describe("the proposal queue", () => {
  it("is the auto regions by start, a tie in lane order (movement first)", () => {
    expect(proposals(LIST).map((e) => e.id)).toEqual(["m1", "r1", "r2"]);
  });
  it("moves on to the next proposal after a time, wrapping to the first", () => {
    expect(nextOpenId(LIST, 3)).toBe("r2");
    expect(nextOpenId(LIST, 9)).toBe("m1");
    expect(nextOpenId([ev("k", "reload", 5, "manual")], 0)).toBeNull();
  });
  it("steps back to the proposal before, wrapping to the last", () => {
    expect(prevOpenId(LIST, 9)).toBe("r1");
    expect(prevOpenId(LIST, 3)).toBe("r2");
  });
  it("says where an open proposal sits, and nothing for a confirmed region", () => {
    expect(queuePosition(LIST, "r1")).toEqual({ index: 1, total: 3 });
    expect(queuePosition(LIST, "k")).toBeNull();
    expect(queuePosition(LIST, null)).toBeNull();
  });
});
```

  `lib/focusWindow.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { focusWindow, fracOf, playWindow } from "@/lib/focusWindow";

describe("the focus window", () => {
  it("is the region plus 2 s each side", () => {
    expect(focusWindow({ start: 8, end: 9.4 }, 30)).toEqual({ from: 6, to: 11.4 });
  });
  it("keeps 3 s for a short region", () => {
    const w = focusWindow({ start: 10, end: 10.05 }, 30);
    expect(w.to - w.from).toBeCloseTo(4.05);
    const tiny = focusWindow({ start: 10, end: 10.05 }, 10.2);
    expect(tiny.to - tiny.from).toBeGreaterThanOrEqual(3);
  });
  it("slides inside the stage at either end rather than shrinking", () => {
    // -1.7..3 slides right to 0..4.7; 27..32 slides left to 25..30.
    expect(focusWindow({ start: 0.3, end: 1 }, 30)).toEqual({ from: 0, to: 4.7 });
    expect(focusWindow({ start: 29, end: 30 }, 30)).toEqual({ from: 25, to: 30 });
  });
  it("is the whole stage when the stage is shorter than 3 s", () => {
    expect(focusWindow({ start: 0.5, end: 1 }, 2)).toEqual({ from: 0, to: 2 });
  });
  it("maps a time to a fraction of the window, clamped", () => {
    const w = { from: 6, to: 11 };
    expect(fracOf(8.5, w)).toBeCloseTo(0.5);
    expect(fracOf(2, w)).toBe(0);
    expect(fracOf(20, w)).toBe(1);
  });
  it("plays half a second either side, inside the stage", () => {
    expect(playWindow({ start: 8, end: 9 }, 30)).toEqual({ from: 7.5, to: 9.5 });
    expect(playWindow({ start: 0.2, end: 29.8 }, 30)).toEqual({ from: 0, to: 30 });
  });
});
```

- [ ] **Step 2: Run them to see them fail.**
  Run: `pnpm vitest run src/lib/proposalQueue.test.ts src/lib/focusWindow.test.ts`. Expected: FAIL.

- [ ] **Step 3: Implement.** `lib/proposalQueue.ts`:

```ts
/**
 * The phone Breakdown's queue (spec 2026-10-17): the proposals the seeder
 * made, in stage order. It is one way of choosing which region the focus
 * strip opens; a confirmed region can be open too and simply has no place
 * in the queue.
 */
import type { StageEvent } from "@/lib/api";
import { LANES } from "@/components/coach/laneDrag";

export function proposals(events: StageEvent[]): StageEvent[] {
  return events
    .filter((e) => e.source === "auto")
    .sort((a, b) => a.start - b.start || LANES.indexOf(a.kind) - LANES.indexOf(b.kind));
}

export function nextOpenId(events: StageEvent[], after: number): string | null {
  const queue = proposals(events);
  return (queue.find((e) => e.start > after) ?? queue[0])?.id ?? null;
}

export function prevOpenId(events: StageEvent[], before: number): string | null {
  const queue = proposals(events);
  return ([...queue].reverse().find((e) => e.start < before) ?? queue[queue.length - 1])?.id ?? null;
}

export function queuePosition(events: StageEvent[], id: string | null): { index: number; total: number } | null {
  const queue = proposals(events);
  const index = queue.findIndex((e) => e.id === id);
  return index < 0 ? null : { index, total: queue.length };
}
```

  `lib/focusWindow.ts`:

```ts
/**
 * The phone focus strip's window (spec 2026-10-17): the open region plus
 * 2 s each side, at least 3 s wide, slid (never shrunk) to stay inside
 * the stage. Times are seconds from the beep.
 */
export const FOCUS_PAD_S = 2;
export const FOCUS_MIN_S = 3;
const PLAY_PAD_S = 0.5;

export interface FocusWindow {
  from: number;
  to: number;
}

export function focusWindow(region: { start: number; end: number }, stageTime: number): FocusWindow {
  const limit = Math.max(stageTime, region.end);
  let from = region.start - FOCUS_PAD_S;
  let to = region.end + FOCUS_PAD_S;
  if (to - from < FOCUS_MIN_S) {
    const mid = (region.start + region.end) / 2;
    from = mid - FOCUS_MIN_S / 2;
    to = mid + FOCUS_MIN_S / 2;
  }
  if (from < 0) {
    to = Math.min(limit, to - from);
    from = 0;
  }
  if (to > limit) {
    from = Math.max(0, from - (to - limit));
    to = limit;
  }
  return { from, to };
}

export function fracOf(t: number, w: FocusWindow): number {
  const span = w.to - w.from;
  if (span <= 0) return 0;
  return Math.min(Math.max((t - w.from) / span, 0), 1);
}

export function playWindow(region: { start: number; end: number }, stageTime: number): FocusWindow {
  return { from: Math.max(0, region.start - PLAY_PAD_S), to: Math.min(stageTime, region.end + PLAY_PAD_S) };
}
```

- [ ] **Step 4: Run them to see them pass.** Same command. Expected: PASS.
  If the "short region" case fails on rounding, the expectation is
  wrong, not the code: `{10, 10.05}` padded is 4.05 s, already over 3.

- [ ] **Step 5: Commit.**

```bash
/usr/bin/git add src/splitsmith/ui_static/src/lib/proposalQueue.ts src/splitsmith/ui_static/src/lib/proposalQueue.test.ts \
  src/splitsmith/ui_static/src/lib/focusWindow.ts src/splitsmith/ui_static/src/lib/focusWindow.test.ts
/usr/bin/git commit -m "feat(breakdown): the phone's proposal queue and focus window"
```

### Task 5: `edgeDrag`

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/edgeDrag.ts`, `lib/edgeDrag.test.ts`

**Interfaces:**
- Consumes: `dragFrame` (`DragInit`, Task 3), `Frame`, `nudge`, `touched`
  from `components/coach/laneDrag.ts`; `snapTime` from `lib/events.ts`.
- Produces:
  - `SECONDS_PER_WIDTH = 1`.
  - `type EdgeTarget = "start" | "end" | "body"`.
  - `interface EdgeGesture { target: EdgeTarget; id: string; before: StageEvent }`.
  - `beginEdge(events, id, target): EdgeGesture | null`.
  - `edgeFrame(g, current, dxFrac, opts: { snap: boolean; shotTimes: number[]; duration: number; padPx: number }): Frame | null`.
    `dxFrac` is the finger's horizontal travel over the pad's width.
  - `restoreRegion(current, g): StageEvent[]`.
  - `stepEdge(current, id, target: "start" | "end", dir: -1 | 1, fps): StageEvent[] | null`.
  - `snappedTo(edge: number, raw: number, shots: { shot_number: number; time_from_beep: number }[]): string | null`.

- [ ] **Step 1: Write the failing tests.** `lib/edgeDrag.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type { StageEvent } from "@/lib/api";
import { MIN_EVENT_S } from "@/lib/events";
import { beginEdge, edgeFrame, restoreRegion, snappedTo, stepEdge } from "@/lib/edgeDrag";

const reload: StageEvent = { id: "r", kind: "reload", start: 8, end: 9.4, source: "auto" };
const before: StageEvent = { id: "p", kind: "reload", start: 5, end: 7.9, source: "manual" };
const LIST = [before, reload];
const opts = { snap: false, shotTimes: [7.95, 9.5], duration: 20, padPx: 300 };

describe("a relative edge drag", () => {
  it("moves the start by the finger's travel, one pad width a second, and confirms the region", () => {
    const g = beginEdge(LIST, "r", "start")!;
    const f = edgeFrame(g, LIST, 0.25, opts)!;
    const r = f.events.find((e) => e.id === "r")!;
    expect(r.start).toBeCloseTo(8.25);
    expect(r.end).toBe(9.4);
    expect(r.source).toBe("manual");
    expect(f.seek).toBeCloseTo(8.25);
    expect(f.edge).toBeCloseTo(8.25);
  });
  it("seeks to the unsnapped edge while saving the snapped one", () => {
    const g = beginEdge(LIST, "r", "end")!;
    const f = edgeFrame(g, LIST, 0.09, { ...opts, snap: true })!; // 9.49, 10 ms from the shot at 9.5
    expect(f.edge).toBe(9.5);
    expect(f.seek).toBeCloseTo(9.49);
  });
  it("stops at a same-lane neighbour and at the minimum length", () => {
    const g = beginEdge(LIST, "r", "start")!;
    expect(edgeFrame(g, LIST, -1, opts)!.edge).toBe(7.9);
    expect(edgeFrame(g, LIST, 5, opts)!.edge).toBeCloseTo(9.4 - MIN_EVENT_S);
  });
  it("slides the whole region, keeping its length", () => {
    const g = beginEdge(LIST, "r", "body")!;
    const r = edgeFrame(g, LIST, 0.5, opts)!.events.find((e) => e.id === "r")!;
    expect(r.start).toBeCloseTo(8.5);
    expect(r.end - r.start).toBeCloseTo(1.4);
  });
  it("judges every frame from where the gesture began, not from the last frame", () => {
    const g = beginEdge(LIST, "r", "start")!;
    const once = edgeFrame(g, LIST, 0.2, opts)!.events;
    expect(edgeFrame(g, once, 0.2, opts)!.edge).toBeCloseTo(8.2);
  });
  it("restores the region as it was when the gesture began, source included", () => {
    const g = beginEdge(LIST, "r", "start")!;
    const moved = edgeFrame(g, LIST, 0.3, opts)!.events;
    expect(restoreRegion(moved, g)).toEqual(LIST);
  });
  it("knows nothing of a region that is gone", () => {
    expect(beginEdge(LIST, "nope", "start")).toBeNull();
  });
});

describe("frame steps and the snap label", () => {
  it("steps one frame of the clip's own rate, never snapping", () => {
    const at50 = stepEdge(LIST, "r", "start", 1, 50)!.find((e) => e.id === "r")!;
    expect(at50.start).toBeCloseTo(8.02);
    const at30 = stepEdge(LIST, "r", "end", -1, 30)!.find((e) => e.id === "r")!;
    expect(at30.end).toBeCloseTo(9.4 - 1 / 30);
  });
  it("names what pulled the edge off the finger", () => {
    const shots = [{ shot_number: 7, time_from_beep: 9.5 }];
    expect(snappedTo(9.5, 9.49, shots)).toBe("shot 7");
    expect(snappedTo(0, 0.01, shots)).toBe("beep");
    expect(snappedTo(9.3, 9.3, shots)).toBeNull();
  });
});
```

- [ ] **Step 2: Run to see it fail.**
  Run: `pnpm vitest run src/lib/edgeDrag.test.ts`. Expected: FAIL.

- [ ] **Step 3: Implement** `lib/edgeDrag.ts`:

```ts
/**
 * The phone's edge mode (spec 2026-10-17): a sideways drag anywhere on the
 * pad moves one edge (or the whole region) by the finger's travel, one pad
 * width a second, so a pixel is a few milliseconds and no target has to be
 * hit. Every rule is the desktop's: each frame goes through
 * ``laneDrag.dragFrame`` (clamp at a same-lane neighbour, the minimum
 * length, the shot snap, the unsnapped seek), judged from where the
 * gesture began.
 */
import type { StageEvent } from "@/lib/api";
import { SNAP_PX, dragFrame, nudge, type Frame } from "@/components/coach/laneDrag";
import { snapTime } from "@/lib/events";

export const SECONDS_PER_WIDTH = 1;

export type EdgeTarget = "start" | "end" | "body";

export interface EdgeGesture {
  target: EdgeTarget;
  id: string;
  /** The region when the gesture began: the base of every frame and what Cancel restores. */
  before: StageEvent;
}

export function beginEdge(events: StageEvent[], id: string, target: EdgeTarget): EdgeGesture | null {
  const before = events.find((e) => e.id === id);
  return before ? { target, id, before } : null;
}

export function edgeFrame(
  g: EdgeGesture,
  current: StageEvent[],
  dxFrac: number,
  opts: { snap: boolean; shotTimes: number[]; duration: number; padPx: number },
): Frame | null {
  const delta = dxFrac * SECONDS_PER_WIDTH;
  // The snap tolerance is the desktop's 8 px, in the pad's seconds per pixel.
  const tolerance = (SNAP_PX / Math.max(opts.padPx, 1)) * SECONDS_PER_WIDTH;
  const snap = (t: number) => (opts.snap ? snapTime(t, [0, ...opts.shotTimes], tolerance) : t);
  if (g.target === "body") {
    return dragFrame(
      { mode: "body", id: g.id, before: g.before, grabOffsetT: 0 },
      current,
      g.before.start + delta,
      snap,
      opts.duration,
    );
  }
  const from = g.target === "start" ? g.before.start : g.before.end;
  return dragFrame({ mode: "edge", edge: g.target, id: g.id, before: g.before }, current, from + delta, snap, opts.duration);
}

export function restoreRegion(current: StageEvent[], g: EdgeGesture): StageEvent[] {
  return current.map((e) => (e.id === g.id ? g.before : e));
}

export function stepEdge(
  current: StageEvent[],
  id: string,
  target: "start" | "end",
  dir: -1 | 1,
  fps: number,
): StageEvent[] | null {
  return nudge(current, id, dir, { alt: false, shift: target === "end" }, fps);
}

export function snappedTo(
  edge: number,
  raw: number,
  shots: { shot_number: number; time_from_beep: number }[],
): string | null {
  if (Math.abs(edge - raw) < 1e-9) return null;
  if (edge === 0) return "beep";
  const shot = shots.find((s) => s.time_from_beep === edge);
  return shot ? `shot ${shot.shot_number}` : null;
}
```

  If `tsc` rejects the object literals passed to `dragFrame` (a
  `DragInit` member carries no `kind` / `anchorT` for edge and body, so it
  should accept them), the fix is in Task 3's `DragInit`, not a cast here.

- [ ] **Step 4: Run to see it pass**, then `pnpm typecheck`. Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
/usr/bin/git add src/splitsmith/ui_static/src/lib/edgeDrag.ts src/splitsmith/ui_static/src/lib/edgeDrag.test.ts
/usr/bin/git commit -m "feat(breakdown): relative edge drags for the phone"
```

### Task 6: The phone components: `PhoneVideo`, `FocusStrip`, `EdgePad`

**Files:**
- Create: `src/splitsmith/ui_static/src/components/breakdown/mobile/PhoneVideo.tsx`
- Create: `src/splitsmith/ui_static/src/components/breakdown/mobile/FocusStrip.tsx`
- Create: `src/splitsmith/ui_static/src/components/breakdown/mobile/EdgePad.tsx`
- Test: `src/splitsmith/ui_static/src/components/breakdown/mobile/mobile.test.tsx`

**Interfaces:**
- Consumes: `StageWorkspace`, `StageView` (`lib/useStageWorkspace`);
  `FocusWindow`, `fracOf` (Task 4).
- Produces:
  - `<PhoneVideo ws view />`: the primary camera bound to `ws.videoRef`,
    with "Tap to load" until its metadata loads.
  - `<FocusStrip window events region shots tFromBeep readOnly activeEdge onEdge />`:
    `onEdge(target: "start" | "end")`.
  - `<EdgePad onMove={(dxFrac, padPx) => void} onEnd={() => void} onCancel={() => void} label className />`.

- [ ] **Step 1: Write the failing tests** in `mobile.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EdgePad } from "@/components/breakdown/mobile/EdgePad";
import { FocusStrip } from "@/components/breakdown/mobile/FocusStrip";
import type { StageEvent } from "@/lib/api";

beforeEach(() => {
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.releasePointerCapture = vi.fn();
});

describe("EdgePad", () => {
  function pad() {
    const onMove = vi.fn();
    const onEnd = vi.fn();
    const onCancel = vi.fn();
    render(<EdgePad onMove={onMove} onEnd={onEnd} onCancel={onCancel} label="Drag to move the start" />);
    const el = screen.getByTestId("edge-pad");
    Object.defineProperty(el, "clientWidth", { value: 300 });
    return { el, onMove, onEnd, onCancel };
  }
  it("reports the finger's travel as a share of its width", () => {
    const { el, onMove, onEnd } = pad();
    fireEvent.pointerDown(el, { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(el, { pointerId: 1, clientX: 175 });
    expect(onMove).toHaveBeenLastCalledWith(0.25, 300);
    fireEvent.pointerUp(el, { pointerId: 1, clientX: 175 });
    expect(onEnd).toHaveBeenCalledTimes(1);
  });
  it("cancels on pointercancel and never ends", () => {
    const { el, onEnd, onCancel } = pad();
    fireEvent.pointerDown(el, { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(el, { pointerId: 1, clientX: 140 });
    fireEvent.pointerCancel(el, { pointerId: 1 });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onEnd).not.toHaveBeenCalled();
  });
  it("ignores a second finger", () => {
    const { el, onMove } = pad();
    fireEvent.pointerDown(el, { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(el, { pointerId: 2, clientX: 250 });
    expect(onMove).not.toHaveBeenCalled();
  });
});

describe("FocusStrip", () => {
  const region: StageEvent = { id: "r", kind: "reload", start: 8, end: 9.4, source: "auto" };
  it("draws the region with two 44 px handles that open edge mode", () => {
    const onEdge = vi.fn();
    render(
      <FocusStrip
        window={{ from: 6, to: 11.4 }}
        events={[region]}
        region={region}
        shots={[{ shot_number: 7, time_from_beep: 9.5 }]}
        tFromBeep={8.5}
        readOnly={false}
        activeEdge={null}
        onEdge={onEdge}
      />,
    );
    const start = screen.getByRole("button", { name: "Adjust start, 8.00 s" });
    expect(start.className).toContain("w-11");
    fireEvent.click(start);
    expect(onEdge).toHaveBeenCalledWith("start");
  });
  it("has no handles when read-only", () => {
    render(
      <FocusStrip
        window={{ from: 6, to: 11.4 }}
        events={[region]}
        region={region}
        shots={[]}
        tFromBeep={8}
        readOnly
        activeEdge={null}
        onEdge={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /Adjust/ })).toBeNull();
  });
});
```

- [ ] **Step 2: Run to see it fail.**
  Run: `pnpm vitest run src/components/breakdown/mobile`. Expected: FAIL.

- [ ] **Step 3: Implement `EdgePad.tsx`:**

```tsx
/**
 * The phone's edge-mode drag surface (spec 2026-10-17): the finger's
 * sideways travel over the pad's width, relative to where it went down,
 * never where it is. One finger; pointercancel (a call, an OS swipe) is a
 * cancel, never an end.
 */
import { useRef, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export function EdgePad({
  onMove,
  onEnd,
  onCancel,
  label,
  className,
  children,
}: {
  onMove: (dxFrac: number, padPx: number) => void;
  onEnd: () => void;
  onCancel: () => void;
  label: string;
  className?: string;
  children?: ReactNode;
}) {
  const down = useRef<{ id: number; x: number } | null>(null);
  return (
    <div
      data-testid="edge-pad"
      role="application"
      aria-label={label}
      className={cn("touch-none select-none", className)}
      onPointerDown={(e) => {
        if (down.current) return;
        e.currentTarget.setPointerCapture(e.pointerId);
        down.current = { id: e.pointerId, x: e.clientX };
      }}
      onPointerMove={(e) => {
        const d = down.current;
        if (!d || d.id !== e.pointerId) return;
        const width = Math.max(e.currentTarget.clientWidth, 1);
        onMove((e.clientX - d.x) / width, width);
      }}
      onPointerUp={(e) => {
        if (down.current?.id !== e.pointerId) return;
        down.current = null;
        onEnd();
      }}
      onPointerCancel={(e) => {
        if (down.current?.id !== e.pointerId) return;
        down.current = null;
        onCancel();
      }}
    >
      {children}
    </div>
  );
}
```

  **`FocusStrip.tsx`:** a `relative h-24` box (`border border-rule
  rounded-md bg-surface`), positioned by percentage from `fracOf`:
  - A ruler row (`h-4`, `numeral text-sm text-muted`): a label every 0.5 s
    inside the window, formatted `t.toFixed(1)`.
  - A shots row: a 2 px `bg-ink` tick per shot inside the window.
  - Same-lane neighbours of `region` (same `kind`, other id) as muted
    bands.
  - The region as a band: `kind` colour from the existing Breakdown
    palette (reuse the class the desktop `LaneEditor` uses for the
    lane's colour: import its kind-to-class map if exported, else export
    it from `LaneEditor.tsx` as `KIND_BAND_CLASS` and use it in both). An
    `auto` region draws dashed.
  - The playhead: a 2 px `bg-led` line at `fracOf(tFromBeep)`.
  - Unless `readOnly`: two `<button type="button">` handles,
    `absolute top-9 h-12 w-11 -ml-[22px]`, each holding a 10x36 px
    `rounded bg-ink` bar (`bg-led` when `activeEdge` is that edge),
    `aria-label={\`Adjust ${edge}, ${t.toFixed(2)} s\`}`,
    `onClick={() => onEdge(edge)}`.

  The `-ml-[22px]` margin is a spacing utility, not a `text-[...]` or
  `tracking-[...]`, so the lint allows it. If the lint disagrees, use
  `-translate-x-1/2`.

  **`PhoneVideo.tsx`:** the primary only.

```tsx
/**
 * The phone Breakdown's player (spec 2026-10-17): the primary camera,
 * bound to the workspace's videoRef so every seek is the workspace's. iOS
 * loads no media before a tap (the 2026-10-17 spike), so until its
 * metadata loads the player shows one button that loads and primes it.
 */
import { Play } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { StageView, StageWorkspace } from "@/lib/useStageWorkspace";
import { cn } from "@/lib/utils";

export function PhoneVideo({ ws, view, className }: { ws: StageWorkspace; view: StageView; className?: string }) {
  const [ready, setReady] = useState(false);
  if (!view.streamUrl || !view.primary) {
    return (
      <div className={cn("flex aspect-video items-center justify-center bg-surface-2 text-md text-muted", className)}>
        No primary video
      </div>
    );
  }
  const load = () => {
    const v = ws.videoRef.current;
    if (!v) return;
    v.load();
    void v
      .play()
      .then(() => v.pause())
      .catch(() => {});
  };
  return (
    <div data-testid="phone-video" className={cn("relative aspect-video overflow-hidden rounded-md bg-black", className)}>
      <video
        ref={(el) => {
          ws.videoRef.current = el;
        }}
        src={view.streamUrl}
        controls={false}
        preload="metadata"
        playsInline
        onLoadedMetadata={() => {
          setReady(true);
          ws.onVideoReady();
        }}
        onTimeUpdate={(e) => {
          const v = e.target as HTMLVideoElement;
          if (v.readyState >= 1) ws.setCurrentTime(ws.fromVideoTime(v.currentTime));
        }}
        onPlay={() => ws.setIsPlaying(true)}
        onPause={() => ws.setIsPlaying(false)}
        onError={() => {
          if (view.playingScrub && view.primary) ws.scrub.markFailed(view.primary);
        }}
        className="absolute inset-0 h-full w-full object-contain"
      />
      {ready ? null : (
        <div className="absolute inset-0 flex items-center justify-center">
          <Button type="button" onClick={load}>
            <Play className="size-4" aria-hidden /> Tap to load
          </Button>
        </div>
      )}
    </div>
  );
}
```

  Check that `ws.scrub.markFailed` takes a `CoachVideoEntry`
  (`StageViewer.tsx` calls it with `big.video`) and that
  `view.primary` is one. Both are in the code read for this plan.

- [ ] **Step 4: Run to see it pass**, then `pnpm typecheck && pnpm lint`.

- [ ] **Step 5: Commit.**

```bash
/usr/bin/git add src/splitsmith/ui_static/src/components/breakdown/mobile
/usr/bin/git commit -m "feat(breakdown): the phone's video, focus strip and edge pad"
```

  (Add `LaneEditor.tsx` too if you exported `KIND_BAND_CLASS` from it.)

### Task 7: `MobileBreakdown` and its route

**Files:**
- Create: `src/splitsmith/ui_static/src/pages/MobileBreakdown.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/Breakdown.tsx` (export `FirstStageRedirect`)
- Modify: `src/splitsmith/ui_static/src/App.tsx:386-393` (a `BreakdownRoute` like `AuditRoute`)
- Test: `src/splitsmith/ui_static/src/pages/MobileBreakdown.test.tsx`

**Interfaces:**
- Consumes: everything above; `useStageWorkspace(slug, stage, { seed: true, onRegionsSaved, link })`;
  `regions.change / cancel / events / issue / busy`; `keepEvent`,
  `keepAllEvents`, `withKind` (`lib/events`); `sameEvents` (`laneDrag`);
  `useSnapToShots` (`lib/timelinePrefs`); `clipFps` (Task 2);
  `SaveNotice` (`components/coach/SaveNotice`).
- Produces: `export function MobileBreakdown()`; `export function BreakdownRoute()`
  in `App.tsx`.

- [ ] **Step 1: Write the failing page tests** in `pages/MobileBreakdown.test.tsx`.
  Copy the `vi.mock("@/lib/api", ...)`, the `vi.mock("@/lib/features", ...)`,
  `beforeAll` (with `matchMedia` answering `matches: true`, so
  `useIsMobile()` is true), `makeShot`, `stageWith` and `PROJECT`
  helpers from `pages/Breakdown.regions.test.tsx` (copy them, do not
  import a test file). Render at
  `/match/m1/breakdown/anna/1` with a route
  `/match/:matchId/breakdown/:slug/:stage` and element `<MobileBreakdown />`.
  Then:

```tsx
const R1: StageEvent = { id: "r1", kind: "reload", start: 4, end: 5.2, source: "auto" };
const M1: StageEvent = { id: "m1", kind: "movement", start: 2, end: 3, source: "auto" };
const K: StageEvent = { id: "k", kind: "reload", start: 8, end: 9, source: "manual" };

describe("MobileBreakdown", () => {
  it("opens on the first proposal by start", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1, M1, K]));
    renderPage();
    expect(await screen.findByText("Proposal 1 of 2")).toBeTruthy();
    expect(screen.getByTestId("open-region").textContent).toContain("Movement");
  });

  it("Keep confirms the open proposal, saves once and moves on", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1, M1], "v1v1v1v1v1v1v1v1"));
    vi.mocked(api.putStageEvents).mockResolvedValue(
      stageWith([R1, { ...M1, source: "manual" }], "v2v2v2v2v2v2v2v2"),
    );
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep" }));
    await waitFor(() =>
      expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [R1, { ...M1, source: "manual" }], "v1v1v1v1v1v1v1v1"),
    );
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Proposal 1 of 1")).toBeTruthy();
    expect(screen.getByTestId("open-region").textContent).toContain("Reload");
  });

  it("Delete removes it and moves on", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1, M1], "v1v1v1v1v1v1v1v1"));
    vi.mocked(api.putStageEvents).mockResolvedValue(stageWith([R1], "v2v2v2v2v2v2v2v2"));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledWith("anna", 1, [R1], "v1v1v1v1v1v1v1v1"));
  });

  it("disables a kind that would overlap, saying why", async () => {
    const otherReload: StageEvent = { id: "r0", kind: "reload", start: 2.2, end: 2.8, source: "manual" };
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([M1, otherReload]));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Kind/ }));
    const reload = await screen.findByRole("button", { name: /Reload/ });
    expect(reload).toHaveProperty("disabled", true);
    expect(reload.getAttribute("title")).toContain("overlap");
  });

  it("edge mode commits once on Done, and Cancel restores without saving", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1], "v1v1v1v1v1v1v1v1"));
    vi.mocked(api.putStageEvents).mockImplementation(async (_s, _n, events) => stageWith(events, "v2v2v2v2v2v2v2v2"));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Adjust start/ }));
    const padEl = screen.getByTestId("edge-pad");
    Object.defineProperty(padEl, "clientWidth", { value: 300 });
    fireEvent.pointerDown(padEl, { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(padEl, { pointerId: 1, clientX: 130 });
    fireEvent.pointerUp(padEl, { pointerId: 1, clientX: 130 });
    expect(api.putStageEvents).not.toHaveBeenCalled(); // nothing goes out until Done
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(api.putStageEvents).toHaveBeenCalledTimes(1));
    const sent = vi.mocked(api.putStageEvents).mock.calls[0][2][0];
    expect(sent.start).toBeCloseTo(4.1);
    expect(sent.source).toBe("manual");

    fireEvent.click(screen.getByRole("button", { name: /Adjust end/ }));
    const pad2 = screen.getByTestId("edge-pad");
    Object.defineProperty(pad2, "clientWidth", { value: 300 });
    fireEvent.pointerDown(pad2, { pointerId: 2, clientX: 100 });
    fireEvent.pointerMove(pad2, { pointerId: 2, clientX: 160 });
    fireEvent.pointerUp(pad2, { pointerId: 2, clientX: 160 });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await new Promise((r) => setTimeout(r, COMMIT_DEBOUNCE_MS + 50));
    expect(api.putStageEvents).toHaveBeenCalledTimes(1);
  });

  it("a pointercancel in edge mode restores the region and saves nothing", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1]));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Adjust start/ }));
    const padEl = screen.getByTestId("edge-pad");
    Object.defineProperty(padEl, "clientWidth", { value: 300 });
    fireEvent.pointerDown(padEl, { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(padEl, { pointerId: 1, clientX: 200 });
    fireEvent.pointerCancel(padEl, { pointerId: 1 });
    expect(screen.getByRole("button", { name: /Adjust start, 4.00 s/ })).toBeTruthy();
    await new Promise((r) => setTimeout(r, COMMIT_DEBOUNCE_MS + 50));
    expect(api.putStageEvents).not.toHaveBeenCalled();
  });

  it("a region that vanishes on reload opens the next one", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValueOnce(stageWith([M1, R1], "v1v1v1v1v1v1v1v1"));
    vi.mocked(api.putStageEvents).mockRejectedValueOnce(new ApiError(409, "version_conflict"));
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1], "v3v3v3v3v3v3v3v3"));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Keep" }));
    await waitFor(() => expect(screen.getByTestId("open-region").textContent).toContain("Reload"));
  });

  it("says when the stage has no proposals", async () => {
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([K]));
    renderPage();
    expect(await screen.findByText("No proposals on this stage")).toBeTruthy();
  });

  it("a mirror without the edit capability reads only", async () => {
    vi.mocked(api.getProject).mockResolvedValue({
      ...(PROJECT as object),
      origin: "desktop",
      capabilities: ["review"],
    } as never);
    vi.mocked(api.getStageCoach).mockResolvedValue(stageWith([R1]));
    renderPage();
    expect(await screen.findByText(/Edit regions on the desktop/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Keep" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Adjust/ })).toBeNull();
  });
});
```

  Check `ApiError`'s constructor signature in `lib/api.ts` before using
  it, and how `Breakdown.regions.test.tsx` simulates a 409 (copy that
  exactly). If the 409 test's mocks do not match how `useStageEvents`
  reloads, follow the existing 409 test there rather than this sketch.

- [ ] **Step 2: Run to see them fail.**
  Run: `pnpm vitest run src/pages/MobileBreakdown.test.tsx`. Expected: FAIL.

- [ ] **Step 3: Implement.** In `pages/Breakdown.tsx` change
  `function FirstStageRedirect` to `export function FirstStageRedirect`.
  In `App.tsx`, next to `AuditRoute`:

```tsx
/* Breakdown on a phone (spec 2026-10-17): reviewing proposals. The desktop
 * workspace stays behind DesktopGate below 768 px. */
export function BreakdownRoute() {
  const isMobile = useIsMobile();
  return isMobile ? (
    <MobileBreakdown />
  ) : (
    <DesktopGate screen="Breakdown">
      <Breakdown />
    </DesktopGate>
  );
}
```

  Use it for both `breakdown/:slug` and `breakdown/:slug/:stage`:
  `element={<ShooterScopedRoute element={<BreakdownRoute />} />}`. Update
  the comment above them ("desktop only like Coach" -> "a phone reviews
  proposals; Coach stays desktop only").

  `pages/MobileBreakdown.tsx`. The structure:

```tsx
/**
 * Breakdown on a phone (spec 2026-10-17, wireframes lumet.dev/mobile-breakdown):
 * review the regions the seeder proposed, one at a time. The open region
 * sits in a focus strip (its time plus 2 s each side); Keep, Delete and
 * Kind act on it, and a handle opens edge mode, where a sideways drag on
 * the video or the pad moves that edge relative to the finger and the
 * picture follows it. Every rule is shared with the desktop: the queue,
 * the window and the drag are pure (lib/proposalQueue, focusWindow,
 * edgeDrag) over lib/events and laneDrag; saves are useStageEvents';
 * the player is the workspace's.
 */
export function MobileBreakdown() {
  const { slug, stage } = useParams<{ slug?: string; stage?: string }>();
  const href = useMatchHref();
  if (!slug) return <Navigate to={href("breakdown")} replace />;
  if (stage == null) return <FirstStageRedirect slug={slug} />;
  const n = Number(stage);
  if (!Number.isFinite(n)) return <div className="px-4 py-4 text-sm text-muted">Bad stage.</div>;
  return <MobileBreakdownStage key={`${slug}-${n}`} slug={slug} stage={n} />;
}

function MobileBreakdownStage({ slug, stage }: { slug: string; stage: number }) {
  const shell = useOutletContext<MatchShellOutletContext | undefined>();
  const { search } = useLocation();
  const ws = useStageWorkspace(slug, stage, { onRegionsSaved: shell?.refreshProject, link: search, seed: true, peaks: false });
  const { regions, coach } = ws;
  const [openId, setOpenId] = useState<string | null>(null);
  const [edit, setEdit] = useState<{ g: EdgeGesture; pill: { t: number; snapped: string | null } | null } | null>(null);
  const [kindOpen, setKindOpen] = useState(false);
  const [snapOn] = useSnapToShots();
  const stopAt = useRef<number | null>(null);
  const view = deriveStageView(ws, slug, stage);

  // Open the first proposal once the regions are there, and move on
  // whenever the open one is gone (deleted here, or by a reload).
  const events = regions.events;
  const open = events.find((e) => e.id === openId) ?? null;
  useEffect(() => {
    if (open == null && !edit) setOpenId(nextOpenId(events, -Infinity));
  }, [open, edit, events]);

  // Play region: stop half a second after it.
  useEffect(() => {
    if (stopAt.current != null && view && view.tFromBeep >= stopAt.current) {
      stopAt.current = null;
      ws.videoRef.current?.pause();
    }
  }, [view?.tFromBeep, ws.videoRef]);

  if (!coach || !view) return <Loading />;
  // ...render (below)
}
```

  The actions, all through `regions.change`:

```tsx
  const fps = clipFps(coach.videos);
  const shotTimes = coach.shots.map((s) => s.time_from_beep);
  const readOnly = view.eventsReadOnly;
  const pos = queuePosition(events, openId);

  const moveOn = (next: StageEvent[], from: StageEvent) => setOpenId(nextOpenId(next, from.start));
  const keep = () => {
    if (!open) return;
    const next = keepEvent(events, open.id);
    regions.change(next, true);
    moveOn(next, open);
  };
  const remove = () => {
    if (!open) return;
    const next = events.filter((e) => e.id !== open.id);
    regions.change(next, true);
    moveOn(next, open);
  };
  const setKind = (kind: StageEventKind) => {
    if (!open) return;
    const next = withKind(events, open.id, kind);
    if (next) regions.change(next, true);
    setKindOpen(false);
  };
  const playRegion = () => {
    if (!open) return;
    const w = playWindow(open, view.stageTime);
    ws.seekFromBeep(w.from);
    stopAt.current = w.to;
    void ws.videoRef.current?.play().catch(() => {});
  };

  // Edge mode.
  const enterEdge = (target: EdgeTarget) => {
    if (!open || readOnly) return;
    const g = beginEdge(events, open.id, target);
    if (!g) return;
    setEdit({ g, pill: null });
    ws.seekFromBeep(target === "end" ? g.before.end : g.before.start);
  };
  const onPadMove = (dxFrac: number, padPx: number) => {
    if (!edit) return;
    const frame = edgeFrame(edit.g, regions.events, dxFrac, { snap: snapOn, shotTimes, duration: view.stageTime, padPx });
    if (!frame) return;
    regions.change(frame.events, false);
    ws.seekFromBeep(frame.seek);
    setEdit({ ...edit, pill: { t: frame.edge, snapped: edit.g.target === "body" ? null : snappedTo(frame.edge, frame.seek, coach.shots) } });
  };
  // The pad's release keeps the edit local; Done commits it, Cancel and pointercancel restore it.
  const restore = () => {
    if (!edit) return;
    regions.change(restoreRegion(regions.events, edit.g), false);
    regions.cancel();
  };
  const done = () => {
    if (!edit) return;
    const startEvents = events.map((e) => (e.id === edit.g.id ? edit.g.before : e));
    if (!sameEvents(startEvents, regions.events)) regions.change(regions.events, true);
    else regions.cancel();
    setEdit(null);
  };
  const cancel = () => {
    restore();
    setEdit(null);
  };
  const step = (dir: -1 | 1) => {
    if (!edit || edit.g.target === "body" || !open) return;
    const next = stepEdge(regions.events, open.id, edit.g.target, dir, fps);
    if (!next) return;
    regions.change(next, false);
    const me = next.find((e) => e.id === open.id);
    if (me) {
      const t = edit.g.target === "start" ? me.start : me.end;
      ws.seekFromBeep(t);
      setEdit({ ...edit, pill: { t, snapped: null } });
    }
  };
```

  Note on `done`: a frame step or a drag already flipped the region to
  `manual` locally (`touched`), so "nothing changed" compares positions
  with `sameEvents` (positions only, by design), as LaneEditor does.
  `regions.change(regions.events, true)` sends the current local list.

  Render, top to bottom (one `primary` Button: Keep):

  - `PageHeader` with `ordinal={pad2(stage)}`, `title={coach.stage_name || "Stage"}`,
    and `actions` holding a `Menu` with: "Snap to shots on/off"
    (`useSnapToShots`'s setter), "Keep all" (`regions.change(keepAllEvents(events), true)`,
    hidden when read-only or with no proposals), Previous / Next stage
    links (`view.prevStage` / `view.nextStage`).
  - When `pos`: a queue strip (one `h-1.5 flex-1 rounded-full` segment
    per proposal; `bg-led` for the current, `bg-ink` before it, `bg-rule`
    after) and `Proposal {pos.index + 1} of {pos.total}` in `text-sm text-muted`.
    When `open` is manual: `Chip tick="muted"` "Confirmed".
  - When editing: the header row becomes "{Kind} {target}" with Cancel
    (default Button) and Done (primary Button; Keep is not rendered in
    edit mode, so there is still one primary).
  - `<PhoneVideo ws={ws} view={view} />`. In edit mode it is wrapped in an
    `<EdgePad className="relative" ...>` so a drag on the picture moves
    the edge, with the pill (`numeral`, `{pill.t.toFixed(2)} s`, frame
    `Math.round(pill.t * fps)`) and the snap note (`snapped to {pill.snapped}`)
    over its top corners.
  - If `!open`: "No proposals on this stage" (`text-md text-muted`) and a
    "Play stage" Button (`ws.seekFromBeep(0); play`). Nothing else.
  - The open region's line (`data-testid="open-region"`): a `Chip` with
    the kind label (Movement / Reload / Activation) and tick colour, a
    `Chip` "Auto" when `source === "auto"`, the duration
    `numeral` `(open.end - open.start).toFixed(2) s`.
  - `<FocusStrip window={focusWindow(open, view.stageTime)} events={events} region={open} shots={coach.shots} tFromBeep={view.tFromBeep} readOnly={readOnly} activeEdge={edit?.g.target === "body" ? null : (edit?.g.target ?? null)} onEdge={enterEdge} />`.
  - Edit mode only: a `Segmented` (`components/ui/Segmented`) Start /
    End / Move that switches `edit.g` with `beginEdge(regions.events, open.id, t)`;
    an `<EdgePad className="flex h-28 items-center justify-center rounded-md border-2 border-dashed border-rule text-sm text-muted" label="Drag sideways to move the edge">`
    with the text "Drag sideways: one width is one second"; two Buttons
    "-1 frame" / "+1 frame" (`step(-1)` / `step(1)`, disabled for Move).
    Wire every EdgePad's `onMove={onPadMove} onEnd={() => {}} onCancel={cancel}`.
  - Not editing, not read-only: Delete (`variant="destructive"`), Kind
    (default, opens a `Sheet` listing the three kinds; each is a 44 px
    Button, disabled with
    `title="Would overlap another {kind} region"` when `withKind(events, open.id, kind)`
    is null, the current kind marked), Keep (`variant="primary"`).
    Then Prev / Play region / Next as default Buttons
    (`setOpenId(prevOpenId(events, open.start))` and so on).
  - Read-only: Play region, Prev and Next only, plus one line:
    "Edit regions on the desktop: this match is synced from it."
  - `regions.issue` renders `<SaveNotice ... />` exactly as
    `BreakdownInspector` renders it (copy its props from there).

  Sizes: every Button gets `min-h-11` (44 px). The page is a normal
  scrolling column (`flex flex-col gap-3 px-4 py-3`), not the desktop's
  fixed grid.

- [ ] **Step 4: Run the page tests, then everything.**
  Run: `pnpm vitest run src/pages/MobileBreakdown.test.tsx`, then
  `pnpm vitest run && pnpm typecheck && pnpm lint`.
  Expected: PASS. If `Breakdown.navCount.test.tsx` or the App route
  tests break, it is because the route element changed: update them to
  `BreakdownRoute`, keeping what they assert.

- [ ] **Step 5: Prove the edge-mode test can fail.** In `MobileBreakdown.tsx`,
  temporarily make `onPadMove` call `regions.change(frame.events, true)`
  (a commit per frame). Run the edge-mode test. It must fail on
  "nothing goes out until Done". Revert.

- [ ] **Step 6: Commit.**

```bash
/usr/bin/git add src/splitsmith/ui_static/src/pages/MobileBreakdown.tsx src/splitsmith/ui_static/src/pages/MobileBreakdown.test.tsx \
  src/splitsmith/ui_static/src/pages/Breakdown.tsx src/splitsmith/ui_static/src/App.tsx
/usr/bin/git commit -m "feat(breakdown): review proposals on a phone"
```

### Task 8: See it, document it, ship it

**Files:**
- Modify: `src/splitsmith/data/whats_new.json`
- Modify: `CLAUDE.md` (the "Stage events" section)

- [ ] **Step 1: Look at it.** Seed and serve a demo match:
  `uv run python scripts/seed_demo_match.py ~/.claude-tmp/mb-demo --media`, then
  `SPLITSMITH_AUTO_SYNC=0 SPLITSMITH_HOME=~/.claude-tmp/mb-home uv run splitsmith ui --project ~/.claude-tmp/mb-demo --skip-system-check --no-browser --port 5198`.
  Wait for `/api/health`. With Playwright (WebKit and Chromium,
  viewport 390x844, `has_touch=True`), open
  `/match/<match_id>/breakdown/<slug>/1` and screenshot:
  - the loading state, then after "Tap to load";
  - the first proposal;
  - edge mode mid-drag (dispatch pointer events on the pad), with the
    pill showing;
  - the Kind sheet;
  - the "No proposals" state.

  Read each screenshot. Check that nothing overflows 390 px, every
  target is at least 44 px, there is one red action per view, and the
  video frame changes between two drag positions (compare two
  screenshots' video areas). Stop the server.
- [ ] **Step 2: On real phones.** Run the CLI with
  `--host 0.0.0.0 --lab-token <random>` on the real match, then ask the
  user to open `http://<mac>:<port>/?lab_token=<token>` on Android and
  iPhone. They Keep one proposal and adjust one edge. Then confirm the
  audit doc on disk changed as expected. Stop the server afterwards.
- [ ] **Step 3: What's new.** Use the `whats-new` skill. Id
  `breakdown-on-phone`, e.g. title "Review Breakdown proposals on your
  phone", body "Open Breakdown on a phone to step through the proposed
  reloads and movement, keep or delete them, and drag an edge on the
  video until the frame is right." Run `uv run pytest -n0 tests/test_whats_new.py`.
- [ ] **Step 4: CLAUDE.md.** Add one paragraph at the end of "Stage
  events" naming `MobileBreakdown`, the pure modules, `BreakdownRoute`,
  the rendition-on-phones rule in `scrubSource`, `clipFps`, and that
  read-only is the capability alone. Mention that a phone reaches the
  desktop's server only through the CLI's `--host` / `--lab-token` until
  pairing exists.
- [ ] **Step 5: Review, then the PR.** One review pass over the branch
  (CLAUDE.md "Review practice"). Name the claims to verify:
  - `edgeFrame` never commits;
  - Done sends exactly one PUT, Cancel and pointercancel none;
  - read-only by capability did not change any desktop behaviour.
  Then push `feat/mobile-breakdown`, open the PR with the screenshots,
  and watch the checks.

---

## Self-review notes

- Spec coverage:
  - The queue, Keep / Delete / Kind, Play region, edge mode with relative
    drag, frame steps, snap, Cancel, Tap to load, the rendition on
    phones: Tasks 3-7.
  - Read-only by capability: Tasks 3 and 7.
  - The frame rate: Tasks 1-2.
  - Extensibility (the strip takes any region; the queue is only a
    picker): Task 4's `nextOpenId` / `queuePosition` treat a manual open
    region as normal, and Task 6's `FocusStrip` takes any region.
  - Pairing, the whole-stage view and hosted mirror editing are later
    designs, not tasks.
- `edgeFrame`'s body branch passes `g.before.start + delta` with
  `grabOffsetT: 0`, which `dragFrame` turns into a start of
  `before.start + delta`, clamped to the free gap. That matches Task 5's
  test.
