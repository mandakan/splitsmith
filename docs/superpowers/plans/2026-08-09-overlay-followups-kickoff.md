# Kickoff -- the four #684 follow-ups (#759, #760, #761, #762)

Entry point for a session starting fresh on the debris #684 left behind.
Assumes no memory of the session that built the single-shooter overlay
port.

**Read this before the issues.** All four issue bodies were corrected on
2026-08-09 with a "Verified against the code" section after three of my
own claims turned out to be wrong on inspection. The corrections are in
the issues themselves, but the sequencing and the traps below are not.

## Check this first, because everything depends on it

**Is PR #758 merged?** All four issues describe code that branch
introduced or exposed. At the time of writing it is `OPEN`, `MERGEABLE`,
`CLEAN`, targeting `main`.

- **Merged:** work from `main` as normal.
- **Still open:** stop and ask. Branching off an unmerged PR stacks PRs,
  which this project has explicitly decided against, and rebasing four
  follow-ups when #758 changes is worse than waiting.

## What these four are

| # | Kind | Risk | Touches |
|---|---|---|---|
| [#762](https://github.com/mandakan/splitsmith/issues/762) | perf / tidy | low | `runtime.py`, two call sites |
| [#761](https://github.com/mandakan/splitsmith/issues/761) | bug (user-facing) | low | SPA only |
| [#759](https://github.com/mandakan/splitsmith/issues/759) | dead-code removal | medium | `overlay_text.py` + 2 docstrings elsewhere |
| [#760](https://github.com/mandakan/splitsmith/issues/760) | layering refactor | **high** | 10 files across `ui/`, `compare/`, `mcp/` |

## Recommended order, and why

**#762, then #761, then #759, then #760.** Four separate branches and
four separate PRs -- do not stack them, and do not bundle them into one
"cleanup" PR. They fail differently and want reviewing differently.

The order is cheapest-and-most-isolated first, riskiest last, so that if
attention runs out the expensive one is the one left undone rather than
the one left half-done. #760 is genuinely last: it is the only one that
can break the whole package, and #759 shrinks the surface it has to move
through.

## Per-issue traps

### #762 -- the capability cache that can never hit

Smallest of the four. The issue offers three options; **pick one and say
why in the PR**, do not implement all three. Keying the probe on the
font's bundled resource name rather than its temp path is the least
invasive.

Trap: `compare/mp4_grid.py` has the identical pattern. Fixing only
`overlay_render` leaves the grid still thrashing the shared 8-slot cache,
which is where the cost actually shows (12 stages per render). Fix both
or say plainly why not.

### #761 -- the SPA codec select

Do not just swap the option strings. The point of the issue is that
`api.ts` already declares the correct type and `Export.tsx:800` defeats
it with `v as OverlayCodec`. Type the options array so the compiler
rejects the bad values, then fix them; otherwise the same drift returns
the next time someone edits that select.

Trap: the MCP `export_stage` / `export_match` tools take `overlay_codec`
as a free `str` with no validation. Same class of bug, different surface.
Worth folding in.

Check whether the SPA has vitest coverage for this control before
assuming a test is easy.

### #759 -- removing the dead PIL text machinery

Read the issue's verified section carefully. In short:

- `_draw_text_with_shadow` has two **docstring** references in modules
  this issue does not otherwise touch (`overlay_html.py:62`,
  `compare/overlay_summary.py:282`). Deleting the function without
  updating them leaves dangling references.
- `resolve_overlay_face` **stays** -- two live callers. Only its
  preset/discovery branch is unreachable.
- `overlay_font_file` **stays** -- three live callers.
- Genuinely dead: `_load_font`, `_fit_text`, `available_font_names`,
  `reset_font_log_cache`, `_FONT_PRESETS`, `_FONT_FALLBACKS`.

Trap: five tests moved into `tests/test_overlay_text.py` during #684
because they were real coverage. They now cover only dead code and go
with it -- but confirm that before deleting, rather than inheriting this
sentence.

Trap: check the slim-wheel packaging job (`run-slim-smoke` label) before
assuming nothing outside `src/` reaches these names.

### #760 -- the layering fix. The dangerous one.

This is the only one that can stop the package importing, and it has a
coupling nobody would guess from the issue title:

**Completion means `overlay_html` imports `SpriteGeometry`/`TilePlacement`
at runtime again.** That means
`tests/test_overlay_html.py::test_overlay_html_stays_a_leaf_and_pulls_in_no_compare_module`
must be **deliberately removed or rewritten as part of this work**. It
was added by #684 to pin the `TYPE_CHECKING` stopgap; once the root edge
is gone, the constraint it names no longer holds.

Removing the guard without fixing the root edge, or removing that test
without fixing the root edge, re-breaks `import splitsmith.cli` for the
whole package. The failure is loud (`ImportError: cannot import name
'OverlayCodec' from partially initialized module`) so it cannot ship
silently, but it will waste a session if you meet it without expecting
it.

The move itself spans ten files -- see the issue's table. Most are
one-line import changes; `tests/test_ui_exports.py` has eight hits and
needs its import updated without any assertion changing.

## How to verify, for all four

The suite is 3053 tests, ~105 s at `-n auto`, and this machine has 12
cores. The project's decided inner loop is **touched files while
iterating, full suite before commit** -- do not run the full suite in a
loop, and never let two agents run it at once.

The integration gate must show **0 skips**:

```bash
SPLITSMITH_REQUIRE_INTEGRATION=1 uv run pytest -q -m integration
```

`uv run` drifts `uv.lock` with unrelated upstream index metadata on
nearly every invocation. Check `git status` before every commit and
`git checkout -- uv.lock` if it moved. This happened on every single
task of #684.

None of these four should change a rendered pixel. If any of them does,
that is a finding, not a side effect -- `scripts/render_overlay_frames.py`
and `scripts/render_grid_frames.py` are how you check.

## What #684 learned that applies here

- Every overlay defect that mattered on that branch was found by
  rendering or measuring. None was found by the test suite, which was
  green throughout. A green suite is evidence nothing known broke.
- Plan-authored and issue-authored code is unverified draft. Three bugs
  in #684's own prescribed code were found by executing it, and two
  instructions given to implementers were simply wrong and were corrected
  by the people receiving them. Measure before implementing a stated
  diagnosis.
