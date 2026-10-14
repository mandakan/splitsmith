# Review fixtures in the Audit page

Date: 2026-10-14. Status: draft for review.

## Why

Reviewing a corpus fixture today means the lab's walk (`pages/Review.tsx`,
`components/review/Walk*`): its own UI, example waveforms per stop, and on a
snapped fixture a stop at every detector candidate and every unmarked sound.
HFO stage 10's Go 3S snap took 212 decisions for 32 shots. Worse, the walk
works from the detector's candidates, not the snapped shots: its decisions
replaced 30 of the 32 snapped times with detected candidates (not rise-foot
times) and saved after each one. Its sign-off counts against the stage's
scorecard rounds (28), where a snap's reference is the primary it was
snapped from (32).

The person reviewing audits every day in the Audit page: the timeline band,
the shot list, Seat, Earlier rise, flags and F to jump between them, the
keyboard. Reviewing a fixture is an audit pass over a shot list and its
audio; it should happen there.

## What

**A fixture opens in the Audit page's own workspace.** Its step 2 (video,
shot list, timeline band, footer) becomes a component fed by a small data
source. The match's Audit page and a new fixture page both host it:

| | Match stage (today) | Fixture (new) |
| --- | --- | --- |
| Audit doc | `getStageAudit` / `saveStageAudit` | `getFixtureAudit` / `saveFixtureAudit` |
| Waveform and Seat | stage peaks from the server | the fixture WAV (`fixtureAudioUrl`) |
| Video | the stage's cameras, beep-anchored | the fixture's `source_video`, offset by `fixture_window_in_source[0]` (as the editor does today) |
| Flags | anomalies | anomalies plus the review signals below |
| Primary action | Save & next stage | Mark reviewed & next fixture |

Route: `/dev/review/:stem` (lab). The review queue's Open goes there. The
match page's beep step, stage list, trim and detection controls are not part
of the workspace and do not appear on a fixture.

**The walk's useful part becomes flags** in the shot list and on the band,
read from `build/fixture_review_inventory.json` and the snap's promotion
report:

- a shot whose leading edge disagrees with the stricter onset by more than
  5 ms (the inventory's suggested moves), with the suggested time;
- a shot at the edge of its snap window;
- an unmarked loud sound between shots that may be a missed shot.

F and Shift+F jump between them, so a clean fixture is a quick pass and a
messy one shows where to look.

**Sign-off counts against the right reference.** For a snap, "32 shots, as
on the primary"; a warning only when shots were added or removed relative to
the primary's list, naming which. For a hand-audited fixture, the shots it
was promoted with. The scorecard's rounds are shown beside, never the gate.
"Mark reviewed" sets `review.status` through `confirmReviewFixture` and opens
the next fixture in the queue's order.

**The walk is retired from snaps now** (its candidate-based stops corrupt
them), and from everything once the new page has been used for a match. The
old fixture editor stays reachable until then.

## Data integrity

- The workspace edits the fixture's own shot list. Candidates are never
  promoted to shots by viewing them; a shot exists because it was snapped or
  placed.
- A save writes the fixture JSON whole (`saveFixtureAudit`, an atomic write
  server side) and records an audit event per change, as today.
- HFO stage 10's snap is restored from its commit before review starts.

## Out of scope

- The match Audit page's behaviour: it changes only by the extraction, with
  its tests passing unchanged.
- Re-snap and promotion scripts (done on `corpus/hfo-repromote`).
- The phone: fixture review is a desktop task.

## Testing

- The workspace extraction: Audit's existing page tests pass unchanged.
- Fixture source: load, save, Mark reviewed and next, against the existing
  fixture API (server tests for the routes it uses already exist; add the
  ones missing).
- A snapped fixture opened, flags shown for suggested moves and window
  edges, Seat moves a shot onto its rise foot, the saved file keeps every
  snapped shot's `source` and changes only the moved times.
- Sign-off line: against the primary's list for a snap, added and removed
  shots named.
- The walk refuses a snapped fixture (until it is retired).
- Look at it: a snapped HFO fixture reviewed end to end in the browser.
