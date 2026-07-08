# Hide rejected shots by default + hold-to-peek

Date: 2026-07-08
Status: approved, in implementation

## Problem

During audit, every rejected shot candidate is drawn on the waveform (outline
triangle + strikethrough, gray). Rejected candidates are numerous and mostly
low-confidence noise, so they litter the waveform and make it harder to audit
the accepted shots. But they still matter: a rejected candidate can be a false
negative (a real shot the ensemble dropped), and the user recovers those during
audit.

The user finds false negatives two ways: spotting an amplitude **peak with no
marker**, and **scrubbing/listening end-to-end**. So rejected markers need to be
out of the way by default but instantly recoverable.

## Approach (chosen: A)

Hide rejected markers by default; add a spring-loaded "peek" that reveals them
while held, via both a hold button and a hold key. Keep the existing sticky
`rejected` filter chip for pinning them on during a long inspection.

Deferred (not in this change): near-miss "ghost" tier (approach B), proximity
reveal (approach C).

## Scope

Both the Audit page (`/audit/:stage`) and the Review page (`/review`), since they
share `AuditControls` (`DEFAULT_FILTERS`, `visibleKindsFromFilters`, `FilterBar`)
and `MarkerLayer`.

Files in play:
- `src/components/AuditControls.tsx` -- `DEFAULT_FILTERS`, `FilterBar` (add peek button)
- `src/pages/Audit.tsx` -- peek state + key handler + visibleKinds wiring + help overlay
- `src/pages/Review.tsx` -- same wiring
- (help/shortcuts overlay content, wherever it lives per page)

## Behavior

1. **Default off.** `DEFAULT_FILTERS.rejected` changes `true -> false`. Rejected
   markers do not render on load. The sticky `rejected` chip stays (now shown as
   off) and its count stays visible.

2. **Hold-to-peek.** A momentary reveal driven by page state `peeking: boolean`.
   Visible-rejected is computed as `showRejected = filters.rejected || peeking`.
   When the chip is already on, peek is a no-op.
   - **Button:** a press-and-hold control in the `FilterBar`, visually distinct
     from the toggle chips (reads as hold, not toggle). Pointer: `pointerdown`
     starts peek, `pointerup` / `pointerleave` / `lostpointercapture` end it.
     Keyboard: focusable; `Space`/`Enter` `keydown` starts and `keyup` ends
     (with `preventDefault` so it does not fire a click). `aria-label="Peek
     rejected shots (hold)"`, `aria-pressed={peeking}`.
   - **Key:** hold `p` (`P`). `keydown` starts peek, `keyup` ends it. Ignored
     while typing in an input/textarea/contenteditable and when a modifier
     (meta/ctrl/alt) is held. Auto-repeat keydown is idempotent.

3. **Stuck-reveal guards.** `peeking` resets to `false` on: pointer up, pointer
   leave, lost pointer capture, key up, and window `blur` (covers alt-tab/focus
   loss mid-hold).

4. **Preserve `n`-steps-to-rejected.** `n` steps through every marker including
   rejected so the user can `k` a false negative back to kept. To avoid focusing
   an invisible marker, a rejected marker that is currently focused
   (`focusedMarkerId`) renders even when rejected is otherwise hidden. Simplest
   rule: when the focused marker is rejected, include `"rejected"` in the
   computed `visibleKinds`. (This reveals rejected siblings too while parked on
   one; acceptable -- the user is in reject-hunting mode at that point.)

5. **Help overlay.** Add `p` (hold -- peek rejected) to the `?` shortcuts list on
   both pages.

## Accessibility

- Press-and-hold is not a native pattern; the `p` key hold is the primary
  keyboard path, and the button additionally supports `Space`/`Enter` hold.
- `aria-pressed` reflects peek state; button has an explicit `aria-label`.
- Marker shape distinction (outline + strikethrough) already carries state
  without relying on color; unchanged.
- No animation, so nothing for `prefers-reduced-motion`.

## Testing / verification

SPA has no test runner; verify via `pnpm typecheck` + `pnpm build` + scoped
`eslint` on changed files (per project norms). Manual checks:
- Rejected hidden on load (Audit + Review).
- Hold button reveals; release hides. Pointer-leave while held hides.
- Hold `p` reveals; release hides. Alt-tab while held (window blur) hides.
- Sticky `rejected` chip still pins rejected on/off.
- `n` still steps onto rejected markers and they are visible/focusable while
  parked; `k` toggles them back to kept.
- Peek key does nothing while typing in the notes field.
```
