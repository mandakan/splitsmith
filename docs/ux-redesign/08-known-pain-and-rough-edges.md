# UX redesign -- known pain and rough edges

A working list of UX gaps, awkward flows, and "feels wrong but I can't
quite say why" moments the user has hit personally. Use this as the
entry point for a design review -- these are the *real* grievances,
not inferred ones.

Format: each item is a short title + the situation that triggers it +
what was tried + current state (`open` / `mitigated` / `shipped`).
"Mitigated" means a fix shipped but the underlying tension may not be
fully resolved.

Last touched: 2026-05-16.

## 1. Workflows that recover from upstream errors

### 1.1 "The beep is on the wrong sound" -- recovery during audit

**Triggered when:** Auditing a stage, the user notices shots are
clustered well after t=0 or the last shot overshoots the official
stage time -- both signs the auto-detected beep landed on a non-buzzer
transient (steel hit, RO chatter, ambient bang).

**Was:** No affordance to fix the beep from Audit. The user had to
leave Audit -> open Ingest -> find the stage -> re-pick beep -> wait
for trim + shot-detect to re-run -> navigate back to Audit. Three
context switches.

**Now (mitigated, commits `788668a` + `10bccf5`):** Always-visible
"Re-pick beep" button next to the beep readout in the Audit toolbar,
plus a proactive anomaly banner when heuristics fire (draw > 2.5s,
last-shot overshoots stage time > 1s). Both paths open an inline
`BeepWaveformPicker`; Apply queues trim + shot-detect via the existing
override endpoint.

**Still rough:**
- Fire-and-forget: the user has to watch JobsPanel for chain completion
  and manually reload Audit. Inline progress under the picker was
  considered + rejected as too heavy for a per-stage tweak.
- The two heuristic thresholds (2.5s / 1s) are guesses. Need real
  match data to validate they fire only when they should.

### 1.2 "My source videos moved" -- relink

**Triggered when:** Source footage was on a USB drive / NAS and the
project's symlinks broke because the source was unplugged / moved /
renamed.

**Was:** `RelinkDialog` + `relinkScan` / `relinkApply` endpoints
existed but were never mounted in the redesigned shell. The JobsPanel
even carried a TODO comment "Inline relink lands with the match-
settings redesign".

**Now (mitigated, commit `6c520b2`):** "Find moved videos" button on
the Videos page opens the existing dialog. Scans a folder recursively,
matches by basename, lets the user confirm per-video target paths
before rewriting symlinks.

**Still rough:**
- The dialog uses a stock folder picker; it could pre-suggest sensible
  scan roots (recent dirs, drives, the original scan dir).
- Bulk-confirm with high-confidence auto-matches would help long lists
  -- today every video needs a click.

### 1.3 "Auto-queue never fired" -- manual beep retry

**Triggered when:** A primary lacks a beep (`processed.beep: false`,
`beep_time: null`) because the scan-time auto-queue was never invoked
or its job failed silently. Real example: re-ingest path on the
blacksmith project where the Pixel headcam was promoted to primary
without auto-queueing `detect_beep`.

**Was:** No UI affordance to retry. The HITL queue doesn't help -- it
only emits `beep_missing` when `beep_auto_detect_failed` is true (a
job ran and produced nothing). Pristine never-attempted primaries
were invisible to the queue.

**Now (mitigated, commit `ed4d926`):** Per-video "Detect beep" button
on the Videos page, visible whenever a non-ignored video lacks a beep.
Plus a `beep 12.34s` status pill on rows that have one so the user
can scan the list and see status at a glance.

**Still rough:**
- N primaries without a beep = N clicks. Should there be a "Detect
  all missing beeps" action on the page header?
- The button label is "Detect beep" -- maybe "Retry detection" when
  `beep_auto_detect_failed` is true and "Detect beep" otherwise so the
  user knows the prior history.

## 2. Discoverability + nav

### 2.1 "Where do I manage videos after ingestion?"

**Triggered when:** After initial ingest, the user wants to add more
cameras / remove a wrong assignment / find moved videos / re-detect a
beep. No persistent path to Ingest from anywhere in the shell.

**Was:** Only post-confirm hint text mentioned the audit page.
Sidebar had Overview / Audit / Coach / Shooters / Export but no
Ingest. Implicit assumption: Ingest is a one-time setup wizard.

**Now (mitigated, commits `6c520b2` + `8c9c0e7`):** Persistent
**Videos** sidebar row in MatchShell, slug-aware so it lands on the
current shooter's `/ingest/<slug>` or `default_shooter_slug` fallback.

**Still rough:**
- "Videos" as the label is fine but loses the "add footage" framing
  the empty state still uses. Could the page header switch between
  "Add footage" (empty) and "Manage videos" (review state) more
  clearly?

### 2.2 "How do I switch to another shooter?" before #354

**Triggered when:** Multi-shooter match, user is auditing shooter A
and wants to flip to shooter B.

**Was:** Audit had a chip strip from #354. Ingest / Coach / Export
did not -- the user had to navigate to `/shooters`, click another
shooter, then re-enter the surface.

**Now (mitigated, commit `8c9c0e7`):** Shared `ShooterChipStrip`
mounted on every shooter-scoped page. Same chip behaviour, same URL
shape, hidden for single-shooter matches.

**Still rough:**
- Per-chip "stages audited / total" works on Audit / Coach / Export
  but the Videos page uses video-count instead; the parameterization
  works but inconsistency may confuse first-time users.

### 2.3 "Why did the JobsPanel disappear?" -- server-state drift

**Triggered when:** Dev server restarts (uvicorn `--reload` after a
code change) and wipes the in-memory bind state. Every API call
returns 409 `no_project`. The user sees a quietly-broken page with no
explanation.

**Was:** Pages caught individual 409s and either ignored or rendered
errors; no global handler refreshed `getHealth` or redirected.
JobsPanel emptied silently.

**Now (mitigated, commit `90c2c8f`):** Shared `request()` in api.ts
dispatches a `splitsmith:no-project` window event on 409 `no_project`;
MatchShell listens, refreshes health, and the existing bound-check
redirects to `/pick`.

**Still rough:**
- The backend is *not* stateless (AppState holds the bound match-root
  in memory). The auto-redirect is the safety net. True statelessness
  (match-root in URL or header) is a noted SaaS-readiness follow-up.

## 3. Visual + a11y

### 3.1 Dark text on saturated red

**Triggered when:** Reading any filled red CTA or badge -- saturated
`#FF2D2D` background with near-black text. Subjectively shimmery,
fails for protanopia / deuteranopia, borderline AA.

**Was:** Pattern persisted in 10+ files even after commit `888933c`
introduced the `.btn-led-fill` recipe (`#DC2626` darker red + cream
text). The original commit only touched five surfaces.

**Now (mitigated, commit `6ed90bc`):** Every `bg-led + text-bg`
pairing swept to `bg-led-fill + text-ink`. Hover states updated to
match `.btn-led-fill:hover` (brighter red, cream text stays). Other
status colors (`bg-done`, `bg-beep`, `bg-live`) left alone -- they
have enough luminance to pass AA with dark text.

**Still rough:**
- ESLint rule against `bg-led text-bg` would catch regressions; not
  written yet.
- Future similar mistakes are easy: any tailwind class pairing two
  tokens that fail AA together. A more general approach (token
  contrast registry?) would scale.

### 3.2 "1 beep" pill always said 1

**Triggered when:** Auditing a stage whose primary had no beep yet.
The filter chip on the audit waveform legend showed "1 beep" anyway,
suggesting there was a beep marker rendered when there wasn't.

**Was:** Hardcoded `count={1}` in `FilterBar`. Symptom of UI built
against the happy path without considering "this stage has no primary
beep yet" edge case.

**Now (mitigated, commit `ed4d926`):** Count derived from
`peaks.beep_time != null ? 1 : 0`. Reflects whether the primary
actually has a beep on the audit timeline.

**Still rough:**
- The same "happy-path-only" pattern probably hides in other counts /
  badges / pills across the SPA. Worth a targeted audit.

## 4. Multi-shooter UX

### 4.1 Compare grid drops unfinished shooters

**Triggered when:** A stage where only some shooters have cached
trim. Compare's video grid only renders playable shooters; the others
either disappear or appear in an empty-state fallback only when
*everyone* is unfinished.

**Was:** Other shooters fell through to chip-strip-only visibility
when at least one was playable. Easy to miss that they exist.

**Now (mitigated, commit `e577a9a`):** Inline "Unfinished shooters"
banner above the grid surfaces shooters without a cached trim. Per-
shooter affordance: "Build trim cache" (if they have audit data) or
"Open in audit" (if they don't). Calls existing
`buildShooterTrimCaches` endpoint; fire-and-forget into JobsPanel.

**Still rough:**
- After "Build trim cache" succeeds, the grid doesn't auto-refresh.
  User has to reload the page.
- The banner shows the shooters but doesn't visually preserve the grid
  slot (i.e. no placeholder tile in the grid itself). A placeholder
  tile would make the multi-shooter set's *shape* visible even when
  half the data is missing.

### 4.2 Coach intentionally has no cross-shooter view

**Documented (not pain) but worth flagging to a reviewer:** Per
`07-redesign-progress.md` open items: "Coach cross-shooter comparison
-- intentionally absent; shooters take stages in different orders so
shot N is not the same physical shot for everyone. Self-referential
baselines only."

A reviewer may want to challenge this. Possible counter-design:
align by shot-from-beep time, not by shot index. Open question.

## 5. Onboarding / first-run

### 5.1 JTBD #1 has not been walked end-to-end on a fresh project

**Triggered when:** the user wants to validate the redesigned shell on
a never-before-ingested match. Today every test has been on
migrated / hand-tweaked projects that carry accumulated state.

**Status: open.** Planned but deferred while the path-scoped
refactor stabilised.

**Why this matters for a design review:** the redesigned surfaces
work on data that *was* in a good state; we don't know how they look
when:
- A match has 0 shooters
- A match has 1 shooter and 0 videos
- A shooter has 0 audited stages
- A shooter has 8 stages all in different processing states
  simultaneously
- The first ingest queues 7+ beep-detect jobs and the JobsPanel
  becomes the user's mental dashboard

The empty / partial states *exist* in code but have not been
exercised in a focused session.

### 5.2 Scoreboard search latency

**Triggered when:** User searches scoreboard.urdr.dev for the match
they just shot. The fetch can take 1-5 seconds depending on cache
state.

**From original JTBD doc (#4, "dropdown latency needs progress
feedback").** Status: not addressed in the redesign. The user has
been working with cached matches throughout development; the latency
gap surfaces only on fresh searches.

## 6. Progress + feedback

### 6.1 Confirm & Start Processing button is misleading

**Triggered when:** User assembles videos on Ingest, clicks "Confirm
& start processing", expects something to start. The button only
navigates to the Match Overview. Processing is auto-queued at scan /
move-assignment time -- it already started; the user just doesn't know.

**Status: open.** Mentioned in this session but deferred behind the
JTBD-1 end-to-end work. The right fix is either:
- Rename to "Done -- back to overview" so it reflects what it does
- Make it actually trigger detect-beep for primaries that lack one
  (mirrors the new per-video button, but applied at the page level)

### 6.2 JobsPanel as the universal progress dashboard

**Triggered when:** Many jobs queue at once (initial ingest, "detect
all missing beeps", batch trim rebuild). The JobsPanel surfaces them
but the user has to *open* the panel to see anything is happening.

**Status: open.** The FAB does glow when work is running, but the
glow is the only ambient signal. A reviewer might propose a slimmer
always-visible progress strip (e.g. under the breadcrumb).

## 7. Things a reviewer should NOT flag

These look like rough edges but are intentional:

- **Saturated `--color-led` (#FF2D2D) on dots / hairlines / focus
  rings / brand mark.** That saturation is what gives the instrument-
  panel feel at small sizes. The contrast rule applies to filled
  surfaces with text, not decorative pixels.
- **No cross-shooter Coach view.** See section 4.2.
- **`/dev/retrain` promote/rollback buttons disabled.** Documented in
  the surface; depends on multi-version artifact storage.
- **`/dev/legacy/lab` still reachable.** Parity-verification window;
  removed after a few weeks of confirming the new dev surfaces cover
  the corpus + review flows.
- **Slugs are opaque (`s_<8 hex>`).** Deliberately not derived from
  names so URLs / disk paths / logs don't leak competitor PII. The
  display name is in `shooter.json`.
