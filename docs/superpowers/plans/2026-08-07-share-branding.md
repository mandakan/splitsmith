# Share Surface Branding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Brand the public share pages (thin header + one-line footer linking to splitsmith.app) and document the share feature on the marketing site and in the README.

**Architecture:** All share-page chrome lives in `ShareShell.tsx` (the only component exclusive to `/share/:token`), so owner surfaces cannot regress. The marketing site and README are static content edits. Spec: `docs/superpowers/specs/2026-08-07-share-branding-design.md`.

**Tech Stack:** React + Tailwind (SPA), plain HTML/CSS (site), Markdown (README), vitest + Testing Library.

## Global Constraints

- Share viewer stays zero-clutter: exactly one thin non-sticky header and one footer line; no other chrome.
- The share surface must keep NOT setting `--shell-header-h` (sticky-player contract in ResultsStage).
- New copy and comments use a single ASCII dash "-", never "--" or an em dash, even where surrounding site copy uses "·"/em dashes.
- Outbound links: `https://splitsmith.app` only, always `target="_blank" rel="noopener"`.
- No new dependencies. Waitlist/hosted copy on the site untouched.
- SPA commands run from `src/splitsmith/ui_static/`. Commit trailer on every commit:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_013p2JUqQX6BRGjUfqFoPVYi`

---

### Task 1: ShareShell header + footer

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/share/ShareShell.tsx`
- Test: `src/splitsmith/ui_static/src/components/share/ShareShell.test.tsx` (new)

**Interfaces:**
- Consumes: `BrandMark` from `@/components/ui/Brand` (`{ className?: string }`), existing `ShareShell` states (`dead`, `loadFailed`).
- Produces: local `ShareFrame({ children })` component wrapping all three render paths. No new exports.

- [ ] **Step 1: Write the failing test**

Create `src/splitsmith/ui_static/src/components/share/ShareShell.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ShareShell } from "@/components/share/ShareShell";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMatchShooters: vi.fn(),
      getProject: vi.fn(),
    },
  };
});

import { api } from "@/lib/api";

function renderShare() {
  return render(
    <MemoryRouter initialEntries={["/share/tok123/results"]}>
      <Routes>
        <Route path="share/:token" element={<ShareShell />}>
          <Route path="results" element={<div>SHARE CONTENT</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("ShareShell branding chrome", () => {
  it("renders branded header + footer around live share content", async () => {
    // Empty roster: pickDefaultShooterSlug finds no slug, so no project
    // fetch fires and the outlet renders directly.
    vi.mocked(api.listMatchShooters).mockResolvedValue({
      match_root: "/x",
      match_name: "m",
      shooters: [],
      origin: null,
    } as never);
    renderShare();
    expect(await screen.findByText("SHARE CONTENT")).toBeInTheDocument();
    const brand = screen.getByRole("link", { name: /splitsmith$/i });
    expect(brand).toHaveAttribute("href", "https://splitsmith.app");
    expect(brand).toHaveAttribute("target", "_blank");
    const footer = screen.getByRole("link", {
      name: /made with splitsmith - analyze your own matches/i,
    });
    expect(footer).toHaveAttribute("href", "https://splitsmith.app");
  });

  it("keeps the header on the dead-link page", async () => {
    vi.mocked(api.listMatchShooters).mockRejectedValue(new Error("404"));
    renderShare();
    expect(
      await screen.findByText("This link is no longer available"),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /splitsmith$/i })).toHaveAttribute(
      "href",
      "https://splitsmith.app",
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run src/components/share/ShareShell.test.tsx`
Expected: FAIL - no link matching /splitsmith$/i.

- [ ] **Step 3: Implement ShareFrame in ShareShell.tsx**

Add imports:

```tsx
import { BrandMark } from "@/components/ui/Brand";
import type { ReactNode } from "react";
```

Add above `ShareShell`:

```tsx
const MARKETING_URL = "https://splitsmith.app";

/** Branded page frame for every share render path (results, dead link,
 *  load error): one thin non-sticky header + one footer line, both
 *  linking to the marketing site. Non-sticky by design - it scrolls
 *  away during playback and stays out of the --shell-header-h
 *  sticky-player contract (the share surface never sets that var). */
function ShareFrame({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col bg-bg">
      <header className="border-b border-rule bg-surface">
        <div className="mx-auto flex w-full max-w-[1100px] items-center justify-between gap-3 px-4 py-2.5 md:px-7">
          <a
            href={MARKETING_URL}
            target="_blank"
            rel="noopener"
            className="inline-flex items-center gap-2 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
          >
            <BrandMark className="size-5" />
            <span className="font-display text-sm font-bold uppercase tracking-tight text-ink">
              Splitsmith
            </span>
          </a>
          <a
            href={MARKETING_URL}
            target="_blank"
            rel="noopener"
            className="rounded font-mono text-[0.625rem] uppercase tracking-[0.14em] text-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
          >
            splitsmith.app
          </a>
        </div>
      </header>
      <div className="flex-1">{children}</div>
      <footer className="border-t border-rule">
        <div className="mx-auto w-full max-w-[1100px] px-4 py-4 md:px-7">
          <a
            href={MARKETING_URL}
            target="_blank"
            rel="noopener"
            className="rounded font-mono text-[0.625rem] uppercase tracking-[0.14em] text-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
          >
            Made with Splitsmith - analyze your own matches
          </a>
        </div>
      </footer>
    </div>
  );
}
```

Change the three render paths:

```tsx
  if (dead)
    return (
      <ShareFrame>
        <ShareUnavailable />
      </ShareFrame>
    );
  if (loadFailed)
    return (
      <ShareFrame>
        <ShareLoadError onRetry={refresh} />
      </ShareFrame>
    );
  ...
  return (
    <ShareFrame>
      <Outlet context={context} />
    </ShareFrame>
  );
```

(The old `<div className="min-h-dvh bg-bg">` wrapper around the Outlet is
replaced by ShareFrame.)

In `ShareLoadError` and `ShareUnavailable`, change the outer div class
from `grid min-h-dvh place-items-center bg-bg px-6 py-10` to
`grid min-h-full place-items-center px-6 py-10` - the frame now owns the
viewport height and background; `min-h-full` fills the frame's flex-1
slot so the card stays centered.

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm vitest run src/components/share/ShareShell.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/splitsmith/ui_static/src/components/share/ShareShell.tsx \
  src/splitsmith/ui_static/src/components/share/ShareShell.test.tsx
git commit -m "feat(ui): branded header + footer on the public share surface"
```

---

### Task 2: Marketing site - third way-card

**Files:**
- Modify: `site/index.html` (`.ways-grid` CSS ~line 649; `#ways` section ~line 1097)

**Interfaces:** none (static HTML/CSS).

- [ ] **Step 1: Update the grid CSS**

Replace:

```css
.ways-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}
```

with:

```css
.ways-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 24px;
}
```

(The 720px single-column breakpoint stays.)

- [ ] **Step 2: Update the section title and append the card**

Change `<h2>Two ways to use Splitsmith</h2>` to
`<h2>Three ways to use Splitsmith</h2>`.

After the closing `</article>` of the "Export to Final Cut Pro" card
(before `</div>` closing `.ways-grid`), insert:

```html
      <article class="way-card">
        <p class="way-label">Path C · Send it out</p>
        <h3>Share a link</h3>
        <p class="way-body">
          Every match gets a shareable results page. Send one link and your
          squad watches each run with beep-aligned playback and a live
          splits ticker. Phone-friendly, no account, no install.
        </p>
        <ul class="way-points">
          <li>Read-only - viewers watch results, not your workflow</li>
          <li>Beep-aligned playback with per-shot splits</li>
          <li>Works in any mobile browser</li>
        </ul>
        <p class="way-foot">Links are revocable any time</p>
      </article>
```

(The `·` in the label matches the existing Path A/B labels - it is
pre-existing site convention, not new dash copy.)

- [ ] **Step 3: Verify rendering**

Open the file locally at desktop and 390px widths (headless screenshot
of `file://.../site/index.html` is fine) and check: three cards across
on desktop, single column on phone, no overflow.

- [ ] **Step 4: Commit**

```bash
git add site/index.html
git commit -m "feat(site): share-a-link card in the ways section"
```

---

### Task 3: README - share section + marketing link

**Files:**
- Modify: `README.md` (intro paragraph ~line 9; after the screenshots blockquote ~line 30)

**Interfaces:** none.

- [ ] **Step 1: Add the marketing-site link to the intro**

The intro line:

```markdown
Extract per-shot split times from head-mounted camera footage of IPSC matches and generate Final Cut Pro timelines with per-shot markers.
```

becomes:

```markdown
Extract per-shot split times from head-mounted camera footage of IPSC matches and generate Final Cut Pro timelines with per-shot markers. Project site: [splitsmith.app](https://splitsmith.app).
```

- [ ] **Step 2: Add the Share your results section**

After the blockquote ending `See [Regenerating screenshots](#regenerating-screenshots) below.` and before `## Quickstart`, insert:

```markdown
## Share your results

Hosted matches get shareable results pages. Send one link and the
recipient watches each run in a mobile browser - beep-aligned playback,
a live splits ticker, and per-stage scorecards. Links are read-only and
token-authorized: viewers see finished results, never your workflow, and
you can revoke a link at any time from the Share dialog on the results
page.

<!-- Screenshot pending: capture_screenshots.py cannot mint a share
token against a local project yet; add docs/screenshots/share.png once
it can. -->
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document share links and link the project site"
```

---

### Task 4: Gates + visual pass + finish

**Files:** none expected; fixes only if gates fail.

- [ ] **Step 1: SPA gates**

From `src/splitsmith/ui_static/`:
Run: `pnpm typecheck && pnpm test && pnpm exec eslint src/components/share/ShareShell.tsx src/components/share/ShareShell.test.tsx`
Expected: clean.

- [ ] **Step 2: Dash check on added lines**

From repo root:
Run: `git diff main -- src README.md site | grep '^+' | grep -v '^+++' | grep -nE 'PLACEHOLDER_EMDASH|[^-]--[^-]' || echo clean`
(where PLACEHOLDER_EMDASH is the literal em dash character)
Expected: `clean`, except the pre-existing-convention `·` label is fine.

- [ ] **Step 3: Visual pass**

Serve the SPA locally (`uv run splitsmith ui --project <match>`), build
the bundle if the server's auto-rebuild does not fire (`pnpm build`),
and screenshot `/share/anytoken/results` at 390x844 - the dead-link
page must show the branded header + footer. Screenshot the site file
for Task 2 if not already done.

- [ ] **Step 4: Push, PR, merge when green**

```bash
git push -u origin feat/share-branding
gh pr create --title "feat(ui): brand the public share surface + document share links" \
  --body "<summary per repo convention>"
gh pr checks <n>   # poll until green (main has no required checks; do not --auto)
gh pr merge <n> --squash --delete-branch
```

---

## Self-review notes

- Spec section 1 -> Task 1 (header, footer, dead/error states, tests);
  section 2 -> Task 2; section 3 -> Task 3; spec testing section ->
  Task 1 tests + Task 4 gates/visual.
- Dead/error card centering: `min-h-full` inside the frame's `flex-1`
  requires the flex child to have a definite height chain - `flex-1`
  on a `min-h-dvh` column provides it; verified pattern used by the
  DesktopGate full-page states.
- No new exports anywhere; `ShareFrame` stays module-local.
