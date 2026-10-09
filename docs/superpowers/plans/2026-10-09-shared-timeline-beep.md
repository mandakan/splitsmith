# Shared Timeline, PR 3 (beep step) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The desktop Audit beep step (`components/audit/BeepStep.tsx`) picks the beep on the shared full-width `Timeline` band, with the same layout as Coach and Audit: preview video and candidate list on top, the band full width below.

**Architecture:** A new `components/audit/BeepTimeline.tsx` replaces `BeepWaveformPicker` inside `BeepStep` only. It fetches the video's peaks (`api.getVideoPeaks`), owns the band's zoom, and renders a `Timeline` with a Candidates row (one pin per candidate, click picks it) and a seekable Audio row (`WaveformTrack` over the whole source, the chosen beep as its `beepTime` overlay). A press-and-drag on the Audio row seeks the preview video live; the release picks that time (`onScrubEnd` -> `onPick`), which is exactly what the old picker's click-or-drag did. `BeepWaveformPicker` stays for the phone review (`MobileBeepReview`) and `StageTimeSection`, untouched.

**Tech Stack:** React 19 + TypeScript, Tailwind, vitest + Testing Library, pnpm in `src/splitsmith/ui_static`.

**Spec:** `docs/superpowers/specs/2026-10-09-shared-timeline-design.md` (issue #1352). PR 1 #1356, PR 2 #1358.

## Global Constraints

- Desktop beep step only. `components/BeepSection.tsx` (`BeepWaveformPicker`), `pages/MobileBeepReview.tsx`, `components/StageTimeSection.tsx` and the phone surfaces are untouched.
- Time: the band's domain is the video's own peaks duration (the full source, or the cached trim when that is what the peaks route serves), in that clip's seconds. `origin` is the detector's beep in that clip (`peaks.beep_time`, else 0), so the ruler reads seconds from the detected beep and the operator sees how far a candidate is from it; it does not move while picking.
- Clip to source: picks are source seconds. `offset = videoBeepTime - peaks.beep_time` (the old picker's rule, `BeepSection.tsx` ~821-824) converts local clip time to source time (`source = local + offset`) and back; when either is null the offset is 0.
- Picking: a release after a press or drag on the Audio row picks the release time (`onPick(local + offset)`); clicking a candidate pin picks that candidate (the same `setDraft` path the list uses); the list stays the primary control. Confirm stays BeepStep's (button and Cmd/Ctrl+Enter).
- The chosen time shows as the band's `beepTime` overlay at `draft ?? item.beep_time` (local); candidates show as pins, the selected one marked.
- Zoom: the band's (Fit to 16x, `+ - 0` on window). The old picker's own zoom listener is not mounted on this step any more (it lives inside `BeepWaveformPicker`, which BeepStep no longer renders).
- Layout: top row `lg:grid-cols-[minmax(0,1fr)_340px]` with `BeepPreview` left and the candidate radiogroup (and its hint) right, then the band full width. Verify in pixels at 1440x900 that the preview and the band share the screen; bound the top row's height as Audit does if they do not.
- UI primitives only; no arbitrary `text-[...]` / `tracking-[...]`; red only for the playhead and focus. No new dependencies. Clean prose: ASCII punctuation.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01QpwUtKUhYZRaEbZthbv5HM`. `/usr/bin/git` only, one command at a time; never `git add -A`.

## Review Focus

1. **Offset.** When the peaks are from a cached trim (peaks.beep_time differs from the video's confirmed beep), a pick lands at the right source time and the overlay shows at the right place. Test in Task 1.
2. **Release picks, press alone picks.** A press without movement picks the press time (the old picker did); a drag picks only on release, not per frame. Test in Task 1.
3. **Camera switch.** Switching the active camera (BeepStep's tabs) loads that video's peaks and resets the band to Fit; a stale peaks response for the previous camera never lands. Test in Task 1.
4. **Typing.** `+ - 0` in a text field never zooms (zoomActionForKey guards it); Cmd/Ctrl+Enter still confirms. Test in Task 2.
5. **Long source.** A source of several minutes at Fit and at 16x draws (viewport-sized canvas) and the ruler stays legible. Covered by the rendered check in Task 3.

---

### Task 1: `components/audit/BeepTimeline.tsx`

**Files:**
- Create: `src/splitsmith/ui_static/src/components/audit/BeepTimeline.tsx`
- Test: `src/splitsmith/ui_static/src/components/audit/BeepTimeline.test.tsx`

**Interfaces:**
- Consumes: `Timeline`, `TimelineTrack` (`components/timeline/Timeline.tsx`), `WaveformTrack`, `Zoom` (`lib/timelineView.ts`), `api.getVideoPeaks(slug, stageNumber, videoId)` -> `PeaksResult { duration, peaks, beep_time, ... }`.
- Produces:
  ```ts
  export interface BeepCandidate { time: number; detected: boolean; label?: string }
  export interface BeepTimelineProps {
    slug: string;
    stageNumber: number;
    videoId: string;
    /** The video's confirmed / current beep in source seconds (for the offset); null when none. */
    videoBeepTime: number | null;
    /** The operator's pick in source seconds; null = the detector's time. */
    draftSourceTime: number | null;
    /** Candidates in source seconds. */
    candidates: BeepCandidate[];
    /** The preview <video>; the band seeks it and reads its time and play state. */
    mediaRef: React.RefObject<HTMLVideoElement | null>;
    onPick: (sourceTime: number) => void;
    onError?: (message: string) => void;
  }
  export function BeepTimeline(props: BeepTimelineProps): JSX.Element
  ```

Behaviour:
- Fetch peaks on `[slug, stageNumber, videoId]` with an alive flag (a stale response never lands); while loading render a muted "Loading audio" line in place of the band; on error call `onError` and render "No audio".
- Track the media element's `currentTime` (timeupdate plus requestAnimationFrame while playing) and `playing` (play / pause events) and pass them to the band in local seconds.
- Band: `duration = peaks.duration`, `origin = peaks.beep_time ?? 0`, `zoom` state reset to Fit when `videoId` changes, `title="Source audio"`.
- Tracks: `candidates` row (height 22): one button per candidate at its local time (`(source - offset)`), `aria-label` "Candidate <time from origin, 2 decimals> s", the selected one (equal to the chosen time within 5 ms) styled as selected, click -> `onPick(candidate.time)`; `audio` row (height 120, `seekable`): `WaveformTrack` from 0 to `peaks.duration` with `beepTime` at the chosen local time.
- `onSeek(t)`: set the media element's `currentTime = t` and remember `t` as the last scrub time; `onScrubEnd()`: `onPick(lastScrubTime + offset)`.

- [ ] **Step 1: Failing tests** (mock `api.getVideoPeaks`; a real `<video>` element in the test with `currentTime` writable; stub rAF):
  - renders "Loading audio" then the band with Candidates and Audio rows;
  - a press and release on the Audio row at clientX 250 (1000 px viewport, 10 s clip, offset 0) calls `onPick(2.5)` once and sets the video's currentTime to 2.5; a drag 250 -> 600 calls `onPick` once with 6.0, not per move;
  - with `videoBeepTime 12.0` and `peaks.beep_time 5.0` (offset 7): a release at local 2.5 picks 9.5; the overlay for `draftSourceTime 9.5` sits at local 2.5 (left 25 %);
  - clicking a candidate pin calls `onPick` with its source time; the selected pin is marked;
  - switching `videoId` refetches, a slow first response resolving after the switch is ignored, and the band is back at Fit.
  Show each failing first.
- [ ] **Step 2: Implement.**
- [ ] **Step 3:** `pnpm exec vitest run src/components/audit/BeepTimeline.test.tsx src/components/timeline`, typecheck, lint.
- [ ] **Step 4: Commit** `feat(audit): pick the beep on the shared timeline band (#1352)`.

---

### Task 2: BeepStep on the band

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/audit/BeepStep.tsx` (grid ~326, picker ~333-348, candidate list ~362-418)
- Modify: `src/splitsmith/ui_static/src/components/audit/BeepStep.test.tsx` (it mocks `BeepWaveformPicker`; mock `BeepTimeline` the same way)

Wiring:
1. Top row per the Global Constraints: `BeepPreview` left, the candidate radiogroup and hint right. The camera tabs and the "full source" header stay above the row. Below, full width: `BeepTimeline` with `slug`, `stageNumber`, `videoId={item.video_id}` (use the real field name), `videoBeepTime={item.beep_time}`, `draftSourceTime={draft}`, `candidates` from the existing de-duped list (detected flag kept), `mediaRef={videoRef}`, `onPick={setDraft}` (a pick equal to the detected time within 5 ms sets null, as a click on the detected row does), `onError` to the existing error path.
2. Remove the `BeepWaveformPicker` import and usage from BeepStep only.
3. Keyboard: Cmd/Ctrl+Enter confirm unchanged.

- [ ] **Step 1: Failing tests** in `BeepStep.test.tsx` (mock `@/components/audit/BeepTimeline` with a button calling `onPick(9.87)` and a span echoing the props):
  - the timeline renders outside the two-column grid, after it; the preview is in the left column and the candidates in the right;
  - a pick from the timeline shows as the operator's row and Confirm sends it (port the existing waveform-pick test to the new mock);
  - picking the detected time from the timeline clears the draft (no override sent);
  - `+` typed in a focused text field does not reach the band (render the real `Timeline` in one test, or assert via `zoomActionForKey` on an input target), and Cmd/Ctrl+Enter still confirms.
  Show each failing first.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Gate:** `pnpm typecheck && pnpm lint && pnpm test`.
- [ ] **Step 4: Commit** `feat(audit): the beep step on the full-width timeline (#1352)`.

---

### Task 3: Docs and the rendered check

**Files:**
- Modify: `CLAUDE.md` (the timeline paragraph and the Audit entry in "UI: the visual budget": beep confirmation's waveform is the band via `BeepTimeline`; `BeepWaveformPicker` remains for the phone and the stage-time picker)
- Modify: `docs/superpowers/specs/2026-10-09-shared-timeline-design.md` ("As built (PR 3)": origin at the detected beep, release-picks, candidate pins, the offset rule)
- Modify: `src/splitsmith/data/whats_new.json` (extend the existing timeline entry's body to mention the beep step; do not rename its id)

- [ ] **Step 1:** Edits; `uv run pytest tests/test_whats_new.py -q -n0`.
- [ ] **Step 2: Rendered check:** the demo match (`~/.claude-tmp/demo-audit` exists; reseed if needed with `scripts/seed_demo_match.py --media`), stage 10 has an unconfirmed beep (the seeder's; otherwise use "Re-pick beep" on stage 3). Build, run the server with `SPLITSMITH_HOME=/home/mathias/.claude-tmp/timeline-home SPLITSMITH_AUTO_SYNC=0`, screenshot the beep step at 1440x900 and 1440x1080: Fit, zoomed around the beep, after a pick. Measure with Playwright bounding boxes that the preview video and the band's Audio row are both within the viewport at 900; if not, bound the top row's height like Audit (`lg:h-[max(300px,calc(100dvh-560px))]`, preview filling, list scrolling) and re-measure. Save to `~/.claude-tmp/timeline-pr3/`. Stop the server by PID.
- [ ] **Step 3: Commit** `docs: the beep step on the shared timeline band (#1352)`.
