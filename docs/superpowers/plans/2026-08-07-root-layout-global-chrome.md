# RootLayout Global Chrome Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract a `RootLayout` that owns a global header bar (brand, mode
switch, account menu) so global chrome is defined once, and slim each shell's
header to a context row beneath it.

**Architecture:** `RootLayout` renders one sticky `<header>` containing the
global bar, a portal slot, and the accent hairline, then `<Outlet/>`. Each
inner shell portals its own context row (breadcrumbs, shooter chips, dev
steps) into that slot and declares the hairline accent through context. The
header stack is measured in one place and published as `--shell-header-h`,
which every shell already consumes.

**Tech Stack:** React 19, react-router-dom (nested layout routes), Tailwind
with the project's CSS custom-property tokens, Vitest + Testing Library,
jsdom.

## Global Constraints

- Package manager is `pnpm` via corepack. Never `npm`.
- Working directory for every command in this plan:
  `src/splitsmith/ui_static`.
- Verification trio, all three must pass before any task is considered done:
  `pnpm exec tsc -b --noEmit`, `pnpm exec eslint <changed files>`,
  `pnpm exec vitest run <changed test files>`.
- Prose in comments and copy: ASCII punctuation only. `--` not an em dash,
  `...` not an ellipsis character, straight quotes.
- Imports use the `@/` alias, never relative paths beyond one level.
- Existing token classes only (`bg-surface`, `border-rule`, `text-ink-2`,
  `z-chrome`, ...). Do not introduce raw hex values.
- The app is a committed dark UI. There is no light theme to support.
- `useIsMobile()` is the single breakpoint source (`max-width: 767px`).
- Decision from the design session, binding on every task: **on mobile the
  global bar does not render.** `MatchShell`'s mobile header and nav drawer
  keep their own account menu. Two stacked rows on a phone costs too much
  vertical space.

---

### Task 1: ShellChrome context

The seam every later task depends on. A shell needs two things from
`RootLayout`: somewhere to put its context row, and a way to say which accent
colour the hairline should be.

**Files:**
- Create: `src/components/layout/shellChromeContext.tsx`
- Test: `src/components/layout/shellChromeContext.test.tsx`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `type ShellAccent = "led" | "beep"`
  - `interface ShellChromeValue { contextSlot: HTMLElement | null; setAccent: (a: ShellAccent) => void }`
  - `ShellChromeProvider({ value, children }: { value: ShellChromeValue; children: ReactNode })`
  - `useShellContextSlot(): HTMLElement | null`
  - `useShellAccent(accent: ShellAccent): void` -- registers on mount,
    resets to `"led"` on unmount.

- [ ] **Step 1: Write the failing test**

Create `src/components/layout/shellChromeContext.test.tsx`:

```tsx
/**
 * ShellChrome context (#550).
 *
 * The contract inner shells rely on: they can find the slot RootLayout
 * published, and declaring an accent resets when the shell unmounts so a
 * dev-mode cyan hairline never leaks onto a match surface.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ShellChromeProvider,
  useShellAccent,
  useShellContextSlot,
  type ShellChromeValue,
} from "@/components/layout/shellChromeContext";

function SlotReader() {
  const slot = useShellContextSlot();
  return <div data-testid="slot">{slot ? slot.id : "none"}</div>;
}

function AccentDeclarer({ accent }: { accent: "led" | "beep" }) {
  useShellAccent(accent);
  return null;
}

function makeValue(over: Partial<ShellChromeValue> = {}): ShellChromeValue {
  return { contextSlot: null, setAccent: vi.fn(), ...over };
}

describe("ShellChrome context", () => {
  it("hands the published slot to a consumer", () => {
    const el = document.createElement("div");
    el.id = "ctx-slot";
    render(
      <ShellChromeProvider value={makeValue({ contextSlot: el })}>
        <SlotReader />
      </ShellChromeProvider>,
    );
    expect(screen.getByTestId("slot")).toHaveTextContent("ctx-slot");
  });

  it("returns null outside a provider rather than throwing", () => {
    render(<SlotReader />);
    expect(screen.getByTestId("slot")).toHaveTextContent("none");
  });

  it("declares the accent on mount", () => {
    const setAccent = vi.fn();
    render(
      <ShellChromeProvider value={makeValue({ setAccent })}>
        <AccentDeclarer accent="beep" />
      </ShellChromeProvider>,
    );
    expect(setAccent).toHaveBeenCalledWith("beep");
  });

  it("resets the accent to led when the declaring shell unmounts", () => {
    const setAccent = vi.fn();
    const { unmount } = render(
      <ShellChromeProvider value={makeValue({ setAccent })}>
        <AccentDeclarer accent="beep" />
      </ShellChromeProvider>,
    );
    setAccent.mockClear();
    unmount();
    expect(setAccent).toHaveBeenCalledWith("led");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm exec vitest run src/components/layout/shellChromeContext.test.tsx`
Expected: FAIL -- cannot resolve `@/components/layout/shellChromeContext`.

Note the basename: `lib/shellChrome.ts` already exists and holds
`useShellHeaderHeight`. This new file is `shellChromeContext.tsx` so the two
never get confused at an import site.

- [ ] **Step 3: Write the implementation**

Create `src/components/layout/shellChromeContext.tsx`:

```tsx
/**
 * ShellChrome context (#550).
 *
 * RootLayout owns one sticky header: the global bar, a slot for whichever
 * shell is mounted, and the accent hairline. This context is how an inner
 * shell reaches both halves without RootLayout knowing anything about
 * breadcrumbs, shooter chips or dev steps.
 *
 * The slot is passed as a real DOM node rather than a render prop so the
 * shell keeps its own hooks and state local and portals markup upward. A
 * render prop would force every shell's header state up into RootLayout,
 * which is the coupling this refactor exists to remove.
 *
 * Outside a provider both hooks are inert (null slot, no-op accent). That
 * keeps a shell renderable in isolation -- MatchShell.test.tsx mounts the
 * shell directly, with no router layout above it.
 */

import {
  createContext,
  useContext,
  useEffect,
  type ReactNode,
} from "react";

/** Hairline accent. ``led`` is the match/default red, ``beep`` the
 *  developer-mode cyan. */
export type ShellAccent = "led" | "beep";

export interface ShellChromeValue {
  /** Node the mounted shell portals its context row into. Null on the
   *  first paint, before RootLayout's ref callback has run. */
  contextSlot: HTMLElement | null;
  setAccent: (accent: ShellAccent) => void;
}

const ShellChromeContext = createContext<ShellChromeValue | null>(null);

export function ShellChromeProvider({
  value,
  children,
}: {
  value: ShellChromeValue;
  children: ReactNode;
}) {
  return (
    <ShellChromeContext.Provider value={value}>
      {children}
    </ShellChromeContext.Provider>
  );
}

export function useShellContextSlot(): HTMLElement | null {
  return useContext(ShellChromeContext)?.contextSlot ?? null;
}

/** Declare this shell's hairline accent for as long as it is mounted.
 *  Resets to ``led`` on unmount so leaving /dev/* cannot strand the cyan
 *  hairline on a match surface. */
export function useShellAccent(accent: ShellAccent): void {
  const setAccent = useContext(ShellChromeContext)?.setAccent;
  useEffect(() => {
    if (!setAccent) return;
    setAccent(accent);
    return () => setAccent("led");
  }, [accent, setAccent]);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm exec vitest run src/components/layout/shellChromeContext.test.tsx`
Expected: PASS, 4 tests.

- [ ] **Step 5: Typecheck and lint**

Run:
```bash
pnpm exec tsc -b --noEmit
pnpm exec eslint src/components/layout/shellChromeContext.tsx src/components/layout/shellChromeContext.test.tsx
```
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add src/components/layout/shellChromeContext.tsx src/components/layout/shellChromeContext.test.tsx
git commit -m "feat(ui): shell chrome context for the RootLayout slot and accent (#550)"
```

---

### Task 2: GlobalBar

Row one. Brand, mode switch, account menu -- and nothing that belongs to a
single shell.

**Files:**
- Create: `src/components/layout/GlobalBar.tsx`
- Test: `src/components/layout/GlobalBar.test.tsx`

**Interfaces:**
- Consumes: `Brand` and `ModeSwitch` from `@/components/ui`, `AccountChip`
  from `@/components/AccountChip`.
- Produces: `GlobalBar()` -- no props. Self-contained; every piece inside
  self-gates on deployment mode already.

- [ ] **Step 1: Write the failing test**

Create `src/components/layout/GlobalBar.test.tsx`:

```tsx
/**
 * GlobalBar (#550).
 *
 * Row one of the single header. These tests pin what it owns -- brand,
 * mode switch, account menu -- and, just as importantly, that it does
 * not reach for anything shell-specific.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import { ModeProvider } from "@/lib/mode";
import { GlobalBar } from "@/components/layout/GlobalBar";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn().mockResolvedValue({
        id: "local",
        email: "local@splitsmith",
        display_name: null,
        is_admin: false,
      }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
    },
  };
});

function renderBar() {
  return render(
    <MemoryRouter>
      <ModeProvider>
        <AuthProvider>
          <GlobalBar />
        </AuthProvider>
      </ModeProvider>
    </MemoryRouter>,
  );
}

describe("GlobalBar", () => {
  it("renders the brand wordmark", () => {
    renderBar();
    expect(screen.getByText("Splitsmith")).toBeInTheDocument();
  });

  it("renders the mode switch", () => {
    renderBar();
    expect(
      screen.getByRole("group", { name: /workspace mode/i }),
    ).toBeInTheDocument();
  });

  it("is labelled as global chrome for assistive tech", () => {
    renderBar();
    expect(
      screen.getByRole("navigation", { name: /global/i }),
    ).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm exec vitest run src/components/layout/GlobalBar.test.tsx`
Expected: FAIL -- cannot resolve `@/components/layout/GlobalBar`.

- [ ] **Step 3: Check the ModeSwitch accessible name**

Open `src/components/ui/ModeSwitch.tsx`. If it does not already expose
`role="group"` with an accessible name matching `/workspace mode/i`, add
`role="group" aria-label="Workspace mode"` to its outer element. Do not
change its visuals. If it exposes a different role or name, update the
assertion in Step 1 to match what is actually there rather than changing
shipped markup to suit the test.

- [ ] **Step 4: Write the implementation**

Create `src/components/layout/GlobalBar.tsx`:

```tsx
/**
 * GlobalBar - row one of the app's single header (#550).
 *
 * Owns only what is true on every surface: the brand, the workspace mode
 * switch, and the account menu. Anything that depends on which shell is
 * mounted (breadcrumbs, shooter chips, dev steps, switch project) belongs
 * in that shell's context row instead -- see ``useShellContextSlot``.
 *
 * Not rendered on mobile. RootLayout gates it on ``useIsMobile`` because
 * MatchShell's mobile header and nav drawer already carry the account
 * menu, and a second stacked row costs too much vertical space on a
 * phone.
 */

import { AccountChip } from "@/components/AccountChip";
import { Brand, ModeSwitch } from "@/components/ui";

export function GlobalBar() {
  return (
    <nav
      aria-label="Global"
      className="flex items-center gap-4 px-7 py-3"
    >
      <Brand variant="compact" />
      <span className="font-display text-base font-bold uppercase tracking-tight text-ink">
        Splitsmith
      </span>
      <div className="flex-1" />
      <ModeSwitch size="sm" />
      <AccountChip />
    </nav>
  );
}
```

Note: `Brand variant="compact"` renders the mark only, so the wordmark is
spelled out beside it. Confirm against `src/components/ui/Brand.tsx` -- if
the compact variant already renders a wordmark, drop the extra `<span>`
rather than shipping it twice.

- [ ] **Step 5: Run the test to verify it passes**

Run: `pnpm exec vitest run src/components/layout/GlobalBar.test.tsx`
Expected: PASS, 3 tests.

- [ ] **Step 6: Typecheck, lint, commit**

```bash
pnpm exec tsc -b --noEmit
pnpm exec eslint src/components/layout/GlobalBar.tsx src/components/layout/GlobalBar.test.tsx
git add src/components/layout/GlobalBar.tsx src/components/layout/GlobalBar.test.tsx
git commit -m "feat(ui): GlobalBar owns brand, mode switch and account menu (#550)"
```

---

### Task 3: RootLayout

The header stack itself: global bar, context slot, hairline, and the one
place `--shell-header-h` is measured.

**Files:**
- Create: `src/components/layout/RootLayout.tsx`
- Test: `src/components/layout/RootLayout.test.tsx`
- Modify: `src/lib/shellChrome.ts` (docstring only)

**Interfaces:**
- Consumes: `ShellChromeProvider`, `ShellAccent` (Task 1); `GlobalBar`
  (Task 2); `useShellHeaderHeight` from `@/lib/shellChrome`;
  `useIsMobile` from `@/lib/useIsMobile`.
- Produces: `RootLayout()` -- a react-router layout route element, no props.

- [ ] **Step 1: Write the failing test**

Create `src/components/layout/RootLayout.test.tsx`:

```tsx
/**
 * RootLayout (#550).
 *
 * One sticky header for the whole app. These tests pin the three things
 * inner shells depend on: the slot exists and receives portalled markup,
 * the hairline follows the declared accent, and the global bar is absent
 * on mobile (where the nav drawer carries the account menu instead).
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { createPortal } from "react-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  useShellAccent,
  useShellContextSlot,
} from "@/components/layout/shellChromeContext";
import { RootLayout } from "@/components/layout/RootLayout";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn().mockResolvedValue({
        id: "local",
        email: "local@splitsmith",
        display_name: null,
        is_admin: false,
      }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
    },
  };
});

const mobile = vi.hoisted(() => ({ value: false }));
vi.mock("@/lib/useIsMobile", () => ({
  useIsMobile: () => mobile.value,
}));

/** Stand-in for a real shell: declares an accent and portals a context row. */
function FakeShell({ accent }: { accent: "led" | "beep" }) {
  useShellAccent(accent);
  const slot = useShellContextSlot();
  return slot
    ? createPortal(<div data-testid="ctx-row">breadcrumbs</div>, slot)
    : null;
}

function renderAt(accent: "led" | "beep" = "led") {
  return render(
    <MemoryRouter initialEntries={["/x"]}>
      <Routes>
        <Route element={<RootLayout />}>
          <Route path="x" element={<FakeShell accent={accent} />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("RootLayout", () => {
  beforeEach(() => {
    mobile.value = false;
  });

  it("renders a shell's portalled context row inside the header", async () => {
    renderAt();
    const row = await screen.findByTestId("ctx-row");
    expect(row).toBeInTheDocument();
    expect(row.closest("header")).not.toBeNull();
  });

  it("uses the led hairline by default", async () => {
    renderAt("led");
    await screen.findByTestId("ctx-row");
    expect(screen.getByTestId("shell-hairline")).toHaveAttribute(
      "data-accent",
      "led",
    );
  });

  it("follows a shell that declares the beep accent", async () => {
    renderAt("beep");
    await screen.findByTestId("ctx-row");
    expect(screen.getByTestId("shell-hairline")).toHaveAttribute(
      "data-accent",
      "beep",
    );
  });

  it("renders the global bar on desktop", async () => {
    renderAt();
    await screen.findByTestId("ctx-row");
    expect(
      screen.getByRole("navigation", { name: /global/i }),
    ).toBeInTheDocument();
  });

  it("omits the global bar on mobile", async () => {
    mobile.value = true;
    renderAt();
    await screen.findByTestId("ctx-row");
    expect(
      screen.queryByRole("navigation", { name: /global/i }),
    ).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm exec vitest run src/components/layout/RootLayout.test.tsx`
Expected: FAIL -- cannot resolve `@/components/layout/RootLayout`.

- [ ] **Step 3: Write the implementation**

Create `src/components/layout/RootLayout.tsx`:

```tsx
/**
 * RootLayout - the app's one always-mounted layout (#550).
 *
 * Owns a single sticky header made of three parts:
 *   1. GlobalBar        - brand, mode switch, account menu (desktop only)
 *   2. the context slot - whichever shell is mounted portals its own row here
 *   3. the hairline     - accent colour declared by that shell
 *
 * Why one header rather than a bar stacked above each shell's own: both
 * MatchShell and DeveloperShell already rendered two rows, so the global
 * bar takes over row one instead of adding a third. That also means
 * ``--shell-header-h`` is measured once, here, over the whole stack --
 * the shells stop measuring and just consume the variable, which they
 * already did via ``var(--shell-header-h, 86px)``.
 *
 * The slot is a state-held DOM node rather than a ref so that publishing
 * it re-renders consumers; a plain ref would leave the first shell render
 * with nothing to portal into and never wake it up.
 */

import { useMemo, useState } from "react";
import { Outlet } from "react-router-dom";

import { GlobalBar } from "@/components/layout/GlobalBar";
import {
  ShellChromeProvider,
  type ShellAccent,
  type ShellChromeValue,
} from "@/components/layout/shellChromeContext";
import { useShellHeaderHeight } from "@/lib/shellChrome";
import { useIsMobile } from "@/lib/useIsMobile";
import { cn } from "@/lib/utils";

const HAIRLINE: Record<ShellAccent, string> = {
  led: "linear-gradient(to right, transparent, var(--color-led) 18%, var(--color-led) 22%, var(--color-rule-strong) 30%, var(--color-rule-strong) 70%, var(--color-led) 78%, var(--color-led) 82%, transparent)",
  beep: "linear-gradient(to right, transparent, var(--color-beep) 18%, var(--color-beep) 22%, var(--color-rule-strong) 30%, var(--color-rule-strong) 70%, var(--color-beep) 78%, var(--color-beep) 82%, transparent)",
};

export function RootLayout() {
  const isMobile = useIsMobile();
  const [contextSlot, setContextSlot] = useState<HTMLElement | null>(null);
  const [accent, setAccent] = useState<ShellAccent>("led");
  const { headerRef, headerStyle } = useShellHeaderHeight();

  const value = useMemo<ShellChromeValue>(
    () => ({ contextSlot, setAccent }),
    [contextSlot],
  );

  return (
    <ShellChromeProvider value={value}>
      <div style={headerStyle}>
        <header
          ref={headerRef}
          className={cn(
            "sticky top-0 z-chrome border-b border-rule",
            "bg-gradient-to-b from-surface to-bg",
          )}
        >
          {isMobile ? null : <GlobalBar />}
          <div ref={setContextSlot} />
          <div
            data-testid="shell-hairline"
            data-accent={accent}
            aria-hidden
            className="pointer-events-none absolute inset-x-0 -bottom-px h-px opacity-55"
            style={{ background: HAIRLINE[accent] }}
          />
        </header>
        <Outlet />
      </div>
    </ShellChromeProvider>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm exec vitest run src/components/layout/RootLayout.test.tsx`
Expected: PASS, 5 tests.

- [ ] **Step 5: Update the shellChrome.ts docstring**

`src/lib/shellChrome.ts` describes itself as used by "the shells". Replace
that sentence with: `Called once, by RootLayout, over the whole header
stack; shells consume the published --shell-header-h variable.` Do not
change the hook's behaviour.

- [ ] **Step 6: Typecheck, lint, commit**

```bash
pnpm exec tsc -b --noEmit
pnpm exec eslint src/components/layout/RootLayout.tsx src/components/layout/RootLayout.test.tsx src/lib/shellChrome.ts
git add src/components/layout/RootLayout.tsx src/components/layout/RootLayout.test.tsx src/lib/shellChrome.ts
git commit -m "feat(ui): RootLayout owns the single header stack (#550)"
```

---

### Task 4: Nest the route tree under RootLayout

Structural only -- shells still render their own headers after this task, so
the app will briefly show two headers. That is expected and is resolved by
Tasks 5 to 7. Do not try to fix it here.

**Files:**
- Modify: `src/App.tsx:130-257`
- Test: `src/App.routes.test.tsx` (create)

**Interfaces:**
- Consumes: `RootLayout` (Task 3).
- Produces: a route tree where `pick`, `pick/new`, `pick/merge`,
  `match/:matchId`, `admin/workers`, the `DeveloperShell` group and the
  `AppShell` group all nest under one `<Route element={<RootLayout />}>`.
  `login`, `share/:token` and the `*` catch-all stay outside it.

- [ ] **Step 1: Write the failing test**

Create `src/App.routes.test.tsx`:

```tsx
/**
 * Route-tree shape after the RootLayout extraction (#550).
 *
 * Cheap structural assertions that would have caught the two things most
 * likely to go wrong in the restructure: a surface accidentally left
 * outside RootLayout (so it loses global chrome), and the login / share
 * surfaces accidentally pulled inside it (so an anonymous visitor sees an
 * account menu).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn().mockResolvedValue({
        id: "local",
        email: "local@splitsmith",
        display_name: null,
        is_admin: false,
      }),
      getServerFeatures: vi.fn().mockResolvedValue({ lab: false, mode: "local" }),
      getHealth: vi.fn().mockResolvedValue({ bound: false, match_id: null }),
      listProjects: vi.fn().mockResolvedValue({ projects: [] }),
      getScoreboardIdentity: vi.fn().mockResolvedValue(null),
    },
  };
});

async function renderAt(path: string) {
  window.history.pushState({}, "", path);
  const { App } = await import("@/App");
  return render(<App />);
}

describe("route tree", () => {
  it("renders global chrome on the picker", async () => {
    await renderAt("/pick");
    await waitFor(() =>
      expect(
        screen.getByRole("navigation", { name: /global/i }),
      ).toBeInTheDocument(),
    );
  });

  it("renders global chrome on admin/workers", async () => {
    await renderAt("/admin/workers");
    await waitFor(() =>
      expect(
        screen.getByRole("navigation", { name: /global/i }),
      ).toBeInTheDocument(),
    );
  });

  it("does not render global chrome on the login surface", async () => {
    await renderAt("/login");
    await waitFor(() => expect(screen.getByRole("main")).toBeInTheDocument());
    expect(
      screen.queryByRole("navigation", { name: /global/i }),
    ).not.toBeInTheDocument();
  });
});
```

If `/login` has no `role="main"` landmark, replace that `waitFor` with an
assertion on text the login page actually renders. Read `src/pages/Login.tsx`
first; do not add markup to the page to satisfy the test.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm exec vitest run src/App.routes.test.tsx`
Expected: FAIL -- no element with an accessible name matching `/global/i`,
because nothing nests under `RootLayout` yet.

- [ ] **Step 3: Restructure the route tree**

In `src/App.tsx`, import `RootLayout`:

```tsx
import { RootLayout } from "@/components/layout/RootLayout";
```

Then wrap the route groups. The `login`, `share/:token` and `*` routes stay
siblings of the new layout route; everything else moves inside it:

```tsx
<Routes>
  <Route path="login" element={<Login />} />
  {/* Public share surface (#349): token-authorized, read-only, and
      deliberately outside RootLayout -- an anonymous visitor must not
      see an account menu or a mode switch. */}
  <Route path="share/:token" element={<ShareShell />}>
    <Route index element={<Navigate to="results" replace />} />
    <Route path="results" element={<Results />} />
    <Route path="results/:slug/:stage" element={<ResultsStage />} />
  </Route>

  <Route element={<RootLayout />}>
    {/* Picker: no context sidebar, inherits the global bar. MatchShell
        redirects here when it sees /api/health.bound === false. */}
    <Route path="pick" element={<Pick />} />
    <Route path="pick/new" element={<DesktopGate screen="Match creation" links={false}><CreateMatch /></DesktopGate>} />
    <Route path="pick/merge" element={<DesktopGate screen="Match merge" links={false}><MergeMatches /></DesktopGate>} />

    {/* Admin surfaces are server-wide, not project-scoped. They used to
        route through AppShell purely because it was the only shell left
        that would take them, which meant no account menu and an empty
        sidebar. They nest directly under RootLayout now. */}
    <Route path="admin/workers" element={<AdminWorkers />} />

    <Route path="match/:matchId">
      {/* ...unchanged: the ingest routes and the MatchShell group... */}
    </Route>

    <Route element={<DeveloperShell />}>
      {/* ...unchanged dev routes... */}
    </Route>

    <Route element={<AppShell />}>
      <Route path="review" element={<DesktopGate screen="Fixture editor" links={false}><Review /></DesktopGate>} />
      <Route path="promote-review" element={<DesktopGate screen="Promote review" links={false}><PromoteReview /></DesktopGate>} />
      <Route path="_design" element={<DesktopGate screen="Design system" links={false}><Design /></DesktopGate>} />
      <Route path="lab" element={<Navigate to="/dev/legacy/lab" replace />} />
      <Route path="lab/:slug" element={<RedirectLabSlug />} />
    </Route>
  </Route>

  <Route path="*" element={<LegacyMatchRedirect />} />
</Routes>
```

Copy the `match/:matchId` and `DeveloperShell` children across verbatim from
the current file. Two substantive edits only: `admin/workers` moves out of
the `AppShell` group, and `share/:token` moves above the layout route.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm exec vitest run src/App.routes.test.tsx`
Expected: PASS, 3 tests.

- [ ] **Step 5: Run the whole SPA suite**

Run: `pnpm exec vitest run`
Expected: PASS. If `MatchShell.test.tsx` fails here, stop -- it mounts
`MatchShell` directly with no layout above it, which Task 1's inert-outside-a-
provider behaviour is designed to allow. A failure means that behaviour
regressed.

- [ ] **Step 6: Typecheck, lint, commit**

```bash
pnpm exec tsc -b --noEmit
pnpm exec eslint src/App.tsx src/App.routes.test.tsx
git add src/App.tsx src/App.routes.test.tsx
git commit -m "refactor(ui): nest the route tree under RootLayout (#550)"
```

---

### Task 5: MatchShell adopts the context row

The busiest header, and the only one where the account chip sits inline among
breadcrumbs.

**Files:**
- Modify: `src/components/match/MatchShell.tsx` (header block ~434-534,
  mobile drawer extras ~561-584, `useShellHeaderHeight` call at 157)
- Test: `src/components/match/MatchShell.test.tsx` (add a describe block)

**Interfaces:**
- Consumes: `useShellAccent`, `useShellContextSlot` (Task 1).
- Produces: nothing new. `MatchShell` stops rendering `<header>`, `Brand`
  and the desktop `AccountChip`; it portals a context row instead.

- [ ] **Step 1: Write the failing test**

Append to `src/components/match/MatchShell.test.tsx`:

```tsx
describe("MatchShell chrome ownership (#550)", () => {
  beforeEach(() => {
    setupHappyPath();
  });

  it("does not render its own header element", async () => {
    renderShell();
    await screen.findByRole("navigation", { name: /breadcrumb/i });
    expect(document.querySelector("header")).toBeNull();
  });

  it("keeps the breadcrumb, shooter chips and switch project", async () => {
    renderShell();
    expect(
      await screen.findByRole("navigation", { name: /breadcrumb/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /switch project/i }),
    ).toBeInTheDocument();
  });

  it("still mounts the account menu inside the mobile drawer", async () => {
    mobile.value = true;
    renderShell();
    await userEvent.click(
      await screen.findByRole("button", { name: /open navigation/i }),
    );
    expect(await screen.findByTestId("account-chip")).toBeInTheDocument();
  });
});
```

Reuse the existing file's mock setup. Name the shared arrange helper
`setupHappyPath` and the render helper `renderShell`; if the file's current
tests inline that arrangement, extract it into those two helpers first, in
its own commit, so this task's diff stays about chrome.

Add `data-testid="account-chip"` to `AccountChip`'s outer element in
`src/components/AccountChip.tsx`. Add a `mobile` hoisted mock for
`@/lib/useIsMobile` matching Task 3's pattern.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm exec vitest run src/components/match/MatchShell.test.tsx`
Expected: FAIL on "does not render its own header element" -- `MatchShell`
still renders `<header>`.

- [ ] **Step 3: Rewrite the header block**

Replace the `<header>` block with a portal into the shell slot. The desktop
branch drops `Brand` and `AccountChip`; the mobile branch is unchanged and
keeps rendering inside the portal too, since `RootLayout` renders no global
bar on mobile:

```tsx
const slot = useShellContextSlot();
useShellAccent("led");

const contextRow = isMobile ? (
  <div className="flex items-center gap-3 px-4 py-3">
    {/* ...existing mobile header markup, unchanged... */}
  </div>
) : (
  <div className="flex flex-wrap items-center gap-4 px-7 py-2.5">
    <nav aria-label="Breadcrumb" className="...">
      {/* ...existing breadcrumb, unchanged... */}
    </nav>
    {shooters.length > 1 ? <ShooterChipStrip ... /> : null}
    <div className="flex-1" />
    <button type="button" onClick={switchProject} title="Switch project" className="...">
      {/* ...existing switch-project button, unchanged... */}
    </button>
  </div>
);
```

Then render `{slot ? createPortal(contextRow, slot) : null}` where the
`<header>` used to be.

`className="..."` above means "the existing class string, carried over
character for character". Do not author new classes for the breadcrumb or
the switch-project button -- only the row wrapper's padding changes.

Four deletions in this task, and nothing else:
1. the `<header>` element, its sticky/gradient classes, and its hairline div
   (all three now live in `RootLayout`),
2. `<Brand variant="compact" />` from the desktop branch (not the mobile
   branch -- the mobile header keeps its own),
3. `<AccountChip />` at the old line 508 (desktop only; the drawer's copy at
   the old line 564 stays),
4. the `useShellHeaderHeight()` call and `headerRef`. Keep `shellStyle` and
   `--shell-sidebar-w`; only the header-height half moves out.

Everything else in the file stays: the unbound-project redirect to `/pick`,
the `#631` read-only mirror banner below the header, `MobileNav`,
`MatchSidebar`, `JobsSurface`, and the job-settlement refetch from `#663`.
The existing tests for the last two must stay green without being edited --
if you find yourself changing a `#663` or `#631` assertion, you have changed
behaviour this task was not meant to touch.

Note the desktop row's vertical padding drops from `py-3` to `py-2.5`. The
row is no longer carrying the brand mark, so it can sit tighter under the
global bar.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm exec vitest run src/components/match/MatchShell.test.tsx`
Expected: PASS, including the pre-existing #663 and #631 describe blocks.

- [ ] **Step 5: Look at it**

Run `pnpm dev`, open a match, and confirm against the mockup: two rows, the
sidebar starting below both, no doubled brand, the LED hairline under the
whole stack. Then narrow the window below 768px and confirm the global bar
disappears and the drawer still offers sign-out. A green suite is not
evidence the chrome looks right -- this step is the evidence.

- [ ] **Step 6: Typecheck, lint, commit**

```bash
pnpm exec tsc -b --noEmit
pnpm exec eslint src/components/match/MatchShell.tsx src/components/match/MatchShell.test.tsx src/components/AccountChip.tsx
git add src/components/match/MatchShell.tsx src/components/match/MatchShell.test.tsx src/components/AccountChip.tsx
git commit -m "refactor(ui): MatchShell portals a context row instead of owning a header (#550)"
```

---

### Task 6: DeveloperShell adopts the context row

Already two rows, and today it has no account menu at all -- so in hosted mode
there is no way to sign out from `/dev/*`. This task fixes that as a side
effect of the move.

**Files:**
- Modify: `src/components/developer/DeveloperShell.tsx:99-172`
- Test: `src/components/developer/DeveloperShell.test.tsx` (create)

**Interfaces:**
- Consumes: `useShellAccent`, `useShellContextSlot` (Task 1).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Create `src/components/developer/DeveloperShell.test.tsx`. Mount the shell
inside a real `RootLayout` route so the portal has somewhere to land -- the
whole point of these assertions is the composition, so a bare shell render
would not exercise it:

```tsx
/**
 * DeveloperShell chrome after the RootLayout extraction (#550).
 *
 * Mounted inside a real RootLayout: the assertions are about the seam
 * between the two, so a bare DeveloperShell render would prove nothing.
 * Hosted mode, because AccountChip self-gates and renders nothing local.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import { ModeProvider } from "@/lib/mode";
import { RootLayout } from "@/components/layout/RootLayout";
import { DeveloperShell } from "@/components/developer/DeveloperShell";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn().mockResolvedValue({
        id: "u1",
        email: "m@thias.se",
        display_name: null,
        is_admin: true,
      }),
      getServerFeatures: vi
        .fn()
        .mockResolvedValue({ lab: false, mode: "hosted" }),
    },
  };
});

vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => false }));

function renderDev() {
  return render(
    <MemoryRouter initialEntries={["/dev/corpus"]}>
      <ModeProvider>
        <AuthProvider>
          <Routes>
            <Route element={<RootLayout />}>
              <Route element={<DeveloperShell />}>
                <Route path="dev/corpus" element={<div>corpus page</div>} />
              </Route>
            </Route>
          </Routes>
        </AuthProvider>
      </ModeProvider>
    </MemoryRouter>,
  );
}

describe("DeveloperShell chrome (#550)", () => {
  it("declares the beep accent", async () => {
    renderDev();
    await screen.findByText(/developer/i);
    expect(screen.getByTestId("shell-hairline")).toHaveAttribute(
      "data-accent",
      "beep",
    );
  });

  it("gains the account menu it never had", async () => {
    renderDev();
    expect(await screen.findByTestId("account-chip")).toBeInTheDocument();
  });

  it("keeps the dev breadcrumb and model chip", async () => {
    renderDev();
    expect(await screen.findByText(/corpus/i)).toBeInTheDocument();
  });

  it("does not render its own mode switch", async () => {
    renderDev();
    await screen.findByText(/corpus/i);
    expect(
      screen.getAllByRole("group", { name: /workspace mode/i }),
    ).toHaveLength(1);
  });
});
```

The account-chip assertion only passes in hosted mode, because `AccountChip`
self-gates. Mock `getServerFeatures` to return `{ lab: false, mode: "hosted" }`
and `getMe` to return an authenticated user for that test.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm exec vitest run src/components/developer/DeveloperShell.test.tsx`
Expected: FAIL -- two mode switches (the shell's own plus the global bar's),
and no account chip.

- [ ] **Step 3: Rewrite the header block**

Collapse the shell's two header rows into one context row and portal it:

- Delete the `<header>`, its cyan hairline div, and the
  `useShellHeaderHeight()` call. `RootLayout` owns all three; the accent
  comes from `useShellAccent("beep")`.
- Delete `<Brand variant="compact" />` and `<ModeSwitch size="sm" />`. Both
  now live in the global bar.
- Keep, in one row: the cyan heartbeat dot, the `Splitsmith / Developer /
  <step>` breadcrumb, `<ModelChip model={model} />`, and the Help /
  Notifications / Settings icon buttons. These are dev-specific and stay
  with the shell.
- **Do not touch the first-paint mode sync.** `DeveloperShell` forces the
  workspace mode to `developer` on mount, and the breadcrumb's home button
  navigates with `replace` for the reason its comment gives. Both are
  behaviour #550 lists as must-preserve. The mode switch moving to the
  global bar changes where the control lives, not this effect.
- The 4-step sidebar stepper is untouched.

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm exec vitest run src/components/developer/DeveloperShell.test.tsx`
Expected: PASS, 4 tests.

- [ ] **Step 5: Look at it**

Run `pnpm dev`, open `/dev/corpus`, confirm the cyan hairline still sits
under the stack and that flipping to Match mode from the global bar still
navigates and resets the accent to red.

- [ ] **Step 6: Typecheck, lint, commit**

```bash
pnpm exec tsc -b --noEmit
pnpm exec eslint src/components/developer/DeveloperShell.tsx src/components/developer/DeveloperShell.test.tsx
git add src/components/developer/DeveloperShell.tsx src/components/developer/DeveloperShell.test.tsx
git commit -m "refactor(ui): DeveloperShell drops its header and gains sign-out (#550)"
```

---

### Task 7: Pick and AppShell

The last two duplicate mounts, and the shell that has been accreting
unrelated routes.

**Files:**
- Modify: `src/pages/Pick.tsx` (header block ~320-360)
- Modify: `src/components/AppShell.tsx`
- Test: `src/pages/Pick.test.tsx` (create)

**Interfaces:**
- Consumes: `useShellContextSlot` (Task 1).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

Create `src/pages/Pick.test.tsx`, mounted inside a real `RootLayout` for the
same reason as Task 6:

```tsx
/**
 * Picker chrome after the RootLayout extraction (#550).
 *
 * Pick used to route outside every shell and hand-roll its own header,
 * including its own AccountChip mount. It nests under RootLayout now, so
 * exactly one account menu must be on the page.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "@/lib/auth";
import { ModeProvider } from "@/lib/mode";
import { RootLayout } from "@/components/layout/RootLayout";
import { Pick } from "@/pages/Pick";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getMe: vi.fn().mockResolvedValue({
        id: "u1",
        email: "m@thias.se",
        display_name: null,
        is_admin: false,
      }),
      getServerFeatures: vi
        .fn()
        .mockResolvedValue({ lab: false, mode: "hosted" }),
      getHealth: vi.fn().mockResolvedValue({ bound: false, match_id: null }),
      listProjects: vi.fn().mockResolvedValue({ projects: [] }),
      getScoreboardIdentity: vi.fn().mockResolvedValue(null),
    },
  };
});

vi.mock("@/lib/useIsMobile", () => ({ useIsMobile: () => false }));

function renderPick() {
  return render(
    <MemoryRouter initialEntries={["/pick"]}>
      <ModeProvider>
        <AuthProvider>
          <Routes>
            <Route element={<RootLayout />}>
              <Route path="pick" element={<Pick />} />
            </Route>
          </Routes>
        </AuthProvider>
      </ModeProvider>
    </MemoryRouter>,
  );
}

describe("Pick chrome (#550)", () => {
  it("mounts no account chip of its own", async () => {
    renderPick();
    await screen.findByText(/standby/i);
    expect(screen.getAllByTestId("account-chip")).toHaveLength(1);
  });

  it("keeps the standby strip", async () => {
    renderPick();
    expect(await screen.findByText(/standby/i)).toBeInTheDocument();
  });
});
```

The exact `listProjects` / `getScoreboardIdentity` shapes are guesses from
the call sites. Read `src/lib/api.ts` and match the real return types rather
than reshaping the code to fit these mocks.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm exec vitest run src/pages/Pick.test.tsx`
Expected: FAIL -- two account chips, `Pick`'s own plus the global bar's.

- [ ] **Step 3: Edit Pick**

- Delete `<AccountChip />` at line 355 and its import at line 37.
- Delete the brand block from Pick's own top row.
- Portal the standby strip into the shell slot so it reads as row two,
  and move the shooter identity pill into it.
- Leave every other part of the page alone. `Pick.tsx` is 1079 lines and
  most of it is the match register, which this task does not touch.

- [ ] **Step 4: Edit AppShell**

- Delete the `/admin` handling: the `bindExempt` term
  `|| pathname.startsWith("/admin")` and the comment above it. `admin/workers`
  no longer routes through this shell (Task 4), so the exemption is dead code.
  `fixtureMode` becomes the whole of `bindExempt`; collapse the two variables
  into one named `bindExempt` and keep the fixture-mode comment.
- Delete the `<header>` and its `ModeSwitch`. The global bar owns both.
- Keep the sidebar, the design-system link, `JobsSurface`, the collapse
  persistence, `ProjectHeader`, and the unbound redirect.
- Update the stale comment at lines 138-143: it claims "the global admin link
  lives in AccountChip so it shows on the shell-less picker too." There is no
  shell-less picker any more. Replace with a sentence saying AppShell now
  holds only the fixture editor and the design system.

- [ ] **Step 5: Run the tests**

Run: `pnpm exec vitest run`
Expected: PASS, whole SPA suite.

- [ ] **Step 6: Look at it**

Run `pnpm dev` and walk all four surfaces against the mockup: `/pick`,
a match page, `/dev/corpus`, `/admin/workers`. Confirm exactly one account
menu on each, and that `/admin/workers` -- which had none at all -- can now
sign out.

- [ ] **Step 7: Typecheck, lint, commit**

```bash
pnpm exec tsc -b --noEmit
pnpm exec eslint src/pages/Pick.tsx src/pages/Pick.test.tsx src/components/AppShell.tsx
git add src/pages/Pick.tsx src/pages/Pick.test.tsx src/components/AppShell.tsx
git commit -m "refactor(ui): Pick and AppShell stop duplicating global chrome (#550)"
```

---

### Task 8: Prove the duplication is gone, and correct the issue

A refactor whose whole point is "defined in one place" needs an assertion
that fails if a fifth mount appears later.

**Files:**
- Test: `src/components/layout/globalChrome.test.tsx` (create)
- Modify: `docs/superpowers/specs/2026-08-07-desktop-device-auth-design.md`

- [ ] **Step 1: Write the guard test**

Create `src/components/layout/globalChrome.test.tsx`:

```tsx
/**
 * Single-mount guard (#550).
 *
 * The refactor's whole claim is that global chrome is defined and mounted
 * once. This is the assertion that fails if a fifth mount is added later,
 * which is exactly how the duplication accrued the first time.
 */
import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("global chrome mounts", () => {
  it("renders AccountChip from exactly two call sites", () => {
    const sites = readdirSync("src", { recursive: true, encoding: "utf8" })
      .filter((f) => f.endsWith(".tsx") && !f.endsWith(".test.tsx"))
      .map((f) => `src/${f}`)
      .filter((f) => /<AccountChip\b/.test(readFileSync(f, "utf8")));
    // GlobalBar (desktop, via RootLayout) and MatchShell's mobile nav
    // drawer. The drawer is deliberate: RootLayout renders no global bar
    // on mobile, so the drawer is the only sign-out on a phone.
    expect(sites.sort()).toEqual([
      "src/components/layout/GlobalBar.tsx",
      "src/components/match/MatchShell.tsx",
    ]);
  });
});
```

`readdirSync` with `{ recursive: true }` needs Node 20+, which the repo
already requires. Do not add a glob dependency for this -- the dep list is
small on purpose.

The test runs from the vitest root, `src/splitsmith/ui_static`, so the
relative `"src"` path resolves. If it does not, use
`new URL("../..", import.meta.url)` rather than hardcoding an absolute path.

- [ ] **Step 2: Run it**

Run: `pnpm exec vitest run src/components/layout/globalChrome.test.tsx`
Expected: PASS. If it fails, the listed paths are wrong -- fix the
expectation to the real mount sites, not the other way around.

- [ ] **Step 3: Prove the guard can fail**

Add `<AccountChip />` temporarily to `src/pages/Pick.tsx`, re-run the test,
and confirm it goes red. Revert. A guard test that cannot fail is worth
nothing -- this is the mutation drill from CLAUDE.md's review practice.

- [ ] **Step 4: Full verification**

Run, in order:
```bash
pnpm exec tsc -b --noEmit
pnpm exec eslint .
pnpm exec vitest run
pnpm build
```
Expected: all four clean. Record the actual vitest pass/fail counts in the
commit body -- not "tests pass".

- [ ] **Step 5: Correct #550's issue body**

The issue advertises "a pure refactor" with the acceptance criterion "No
visual or behavioural regression on /pick, match pages, or /dev/*". That is
no longer accurate: the owner chose the global bar, so match pages gain a
row and `--shell-header-h` grows by roughly 34px. Post a comment on #550
recording the change of shape, linking the mockup, and noting the two
behaviour changes that are improvements rather than regressions:
`/dev/*` and `/admin/workers` gain a sign-out they never had.

- [ ] **Step 6: Note the precondition is met**

In `docs/superpowers/specs/2026-08-07-desktop-device-auth-design.md`, the
"Sequencing" section names #550 as step 1. Mark it done and record that
`HostedAccountChip` mounts in `GlobalBar` alongside `AccountChip`, both
self-gating on deployment mode.

- [ ] **Step 7: Commit and open the PR**

```bash
git add -A
git commit -m "test(ui): guard against global chrome re-duplicating (#550)"
```

Open the PR with a body covering, in this order: what moved and why (one
global bar, shells slimmed to a context row); the link to the mockup artifact
recorded in the #550 comment from Step 5; the two behaviour changes that are
fixes, not regressions (`/dev/*` and `/admin/workers` gain a sign-out); the
one measurable layout shift (`--shell-header-h` grows ~34px on match pages,
absorbed by the existing `ResizeObserver`); the mobile decision (no global bar
below 768px, the drawer keeps its own account menu); and the verification
counts from Step 4. Close with `Closes #550` and note that #719's device-auth
work depends on this landing.

Squash-merge, per the repo convention. Do not stack the #719 device-auth
branch on top of this one -- wait for it to land on main first.

---

## Notes for the implementer

**Why the shells portal upward instead of RootLayout rendering their rows.**
Each context row depends on state the shell owns: `MatchShell`'s breadcrumb
needs the resolved project and shooter list, `DeveloperShell`'s needs the
active step. Lifting that into `RootLayout` would recreate the coupling this
refactor exists to remove. The portal keeps the data where it already is.

**The one-frame gap.** `contextSlot` is null on the first paint, so a shell's
context row appears on the second. `useShellHeaderHeight`'s `ResizeObserver`
catches the resulting height change, so nothing needs a hardcoded number.
If you see a flash of a one-row header on load, that is this, and it is
expected -- do not "fix" it by hardcoding a height.

**What is deliberately not in scope.** The owner noted that many pages and
sheets do not yet look integrated with the app and will need their own pass.
That work is real but separate. This plan touches shells and routing only;
resist pulling page-level surfaces in. The `ShellChrome` context is the seam
that later work builds on.
