# Fixture review on the phone (step A of C)

Date: 2026-10-11. Status: approved in conversation, awaiting spec review.

## Goal

Review the fixture corpus in 5 to 10 minute sessions on a phone, with the
same walk (`lib/walk.ts`) the desktop uses, and find out whether placing
onsets on a phone is good enough before anything moves to production.

The plan is C: **A** (this spec) serves the lab from the Mac to the phone;
**B** (a later spec) puts an admin-only corpus review on splitsmith.app with
a hosted decision store and a pull script, reusing the phone screen as is.
B is out of scope here, but nothing in A may assume the Mac's filesystem in
the phone screen itself: the screen talks to the same API calls the desktop
walk uses.

## What the user said

- Sessions of 5 to 10 minutes on the phone, away from the computer.
- Reached over the home Wi-Fi, or the user's VPN home when out.
- Approved: one walk with two layouts, the bottom-bar controls, the whole
  stage folded away, loop on by default, a Continue entry, the token guard.
- No moving playhead (ruled for the desktop walk; holds here too).

## 1. Serving the lab to the phone

The lab server reads any file a path names (`/api/fixture/audio?path=`,
`/api/fixture/video?path=`) and writes fixture files. Bound to a LAN
address it must not answer other devices on that network.

- `splitsmith ui` gains `--lab-token <secret>` (env `SPLITSMITH_LAB_TOKEN`).
  When the server binds to anything other than a loopback address, a token
  is required: the command refuses to start without one.
- With a token set, a middleware admits a request only when it carries the
  token: the `lab_token` query parameter on the first request (the response
  sets an `HttpOnly`, `SameSite=Strict` cookie holding it and redirects to
  the same URL without the parameter) or that cookie afterwards. Anything
  else answers the same opaque 404 as an unknown route. Compared in
  constant time.
- Loopback binds without a token behave exactly as today.
- `scripts/review_session.py --phone`: finds the Mac's LAN address (the
  interface the default route uses; `--host` overrides it), generates a
  token (`secrets.token_urlsafe(24)`), starts the server bound to that
  address with the token and prints
  `http://<lan-ip>:<port>/dev/review?lab_token=<token>` to open on the
  phone. No QR code (no new dependency). The token lives only for that
  server run.
- Auto-sync stays off (`SPLITSMITH_AUTO_SYNC=0`), as the script already sets.

## 2. One walk, two layouts

- `useWalk` (new, `components/review/useWalk.ts`): everything `Walk.tsx`
  holds today except rendering: stops, index, decisions, the anchor, and one
  function per action (`confirm`, `shot`, `notShot`, `riseFoot`,
  `nudge(ms)`, `place(t)`, `back`, `listen`, `toggleLoop`, `jump(t)`), plus
  the derived prompt, flags, placement and count check. The desktop
  keyboard handler maps keys to these functions; the phone's buttons call
  the same ones. `lib/walk.ts` is unchanged.
- `Walk.tsx` keeps its layout and keys on top of the hook; its existing
  tests must pass unchanged (they are the proof the extraction moved
  nothing).
- `MobileWalk.tsx` (new) is the phone layout over the same hook and the
  same `Strip`. `Review.tsx` picks it when the viewport is under 768 px
  wide or the URL has `?phone=1`, and passes it the same props.

### The phone layout

Top to bottom, one column, no horizontal scroll:

1. Header: stop counter, fixture's short name, kept / rounds.
2. The question in words (`stopPrompt`), and the flags.
3. The 80 ms strip, tall (`clamp(160px, 28vh, 260px)`), tap places.
4. The 300 ms strip, tap places.
5. The 3 s strip.
6. "Whole stage" folded; opened, tap jumps to the nearest stop.
7. "Video" folded; opened, the small video.
8. "Guide" opens `WalkGuide` as a sheet.

Fixed at the bottom, within thumb reach, respecting the safe-area inset:

- Row 1: `-5` `-1` `+1` `+5` (ms) and `Back`.
- Row 2: `Not a shot`, `Shot`, `Rise foot`, and `Confirm`, the widest.
  Buttons not applicable to the stop's state are disabled, the same rule
  the keys follow.
- Minimum touch target 44 px.

### Sound

- The phone walk opens on a "Start" screen (one tap), since a phone plays
  no sound before a gesture; that tap starts the loop on the first stop.
- The loop defaults on for the phone layout (the desktop default stays off
  and remembered); a "Loop" toggle sits in the header.
- Tapping the 3 s strip or a play button in the header toggles playback.

## 3. Short sessions

- `/dev/review` on a phone leads with one primary button, **Continue**: the
  first `needs_review` fixture in the queue's order that has undecided
  stops, opened at its first undecided stop (the walk's resume rule).
  Fixtures with every stop decided but not signed off come first, so a
  session can finish one.
- Decisions autosave per stop (as today); leaving mid-fixture loses nothing.
- Sign-off stays at the end of a fixture, with the count check, as a
  full-width button.
- Committing the changed fixtures stays on the Mac.

## Testing

- Server: the token middleware (no token on a LAN bind refuses to start; a
  request without token or cookie is 404; the query parameter sets the
  cookie and redirects; the cookie admits; loopback without a token is
  unchanged).
- `useWalk`: the existing `Walk.test.tsx` suite passes unchanged after the
  extraction.
- `MobileWalk`: each button calls its action and is disabled where its key
  is a no-op; tap on a strip places; Confirm moves and the next stop loops;
  the Start screen gates sound.
- Continue: the fixture choice as a pure function in `lib/walk.ts`.
- Browser: the phone layout at 390 x 844 (Playwright) on a hand-labelled and
  a snapped fixture, then on the user's phone over the Wi-Fi with the token.

## Out of scope

- B: the hosted store, the pull script, the admin gate, signed video.
- Any change to the walk's rules, scope or the rise foot.
- A What's new entry (lab only).
