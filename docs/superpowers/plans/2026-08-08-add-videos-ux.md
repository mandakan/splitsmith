# Add-videos UX Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mode-gate every add-footage affordance (no fake drop targets in local mode, a real full-page drop target in hosted mode) and rewrite FolderPicker as a single-scroll, sidebar-first picker dialog that commits immediately - deleting the multi-source queue, the inline picker shells, and the DirectoryPickerModal facade.

**Architecture:** Three layers, built bottom-up: (1) `useDeploymentMode` gains a `resolved` flag so surfaces can hold a neutral skeleton instead of flashing the wrong mode; (2) an app-root drop guard plus a shared depth-counter drag util make drops safe everywhere and real on the hosted Ingest page; (3) `FolderPicker` becomes the one modal picker (fixed-height, exactly one scroll container, permanent Places sidebar with a Computer entry, commit-with-inline-progress footer) consumed directly by Ingest, CreateMatch, and RelinkDialog. All backend endpoints are unchanged.

**Tech Stack:** React + TypeScript, Tailwind (project tokens), vitest + @testing-library/react, pnpm (never npm)

## Global Constraints

- All frontend work lives in `/Users/mathias/work/splitsmith/src/splitsmith/ui_static`; use pnpm only there - never npm, never a package-lock.json.
- No new dependencies (no new entries in package.json).
- New copy/comments use a single ASCII dash "-", never "--" and never an em dash; grep your added lines before committing.
- Overlays follow the existing convention: z tokens (`z-modal`, `z-toast`, `z-takeover`, `z-drawer` - defined in `src/styles/index.css` lines 271-275), body `<Portal>` from `@/components/ui/Portal`, and `useDialogFocus` from `@/lib/dialogFocus`. Never inline a fixed overlay without them.
- Custom classes that override Tailwind utilities go in `@layer utilities` in `src/styles/index.css`, not `@layer components`. (This plan should not need any; prefer plain utilities.)
- Before referencing any `var(--token)` or `z-*` token, verify the name exists in `src/splitsmith/ui_static/src/styles/index.css` - bare `var(--foo)` refs silently fall back.
- Delete obsolete tests for removed behavior; never add production shims to keep an old test green. (Pre-branch grep found zero vitest files referencing QueueView / autoCommitFiles / mutex flags / DirectoryPickerModal - Task 8 re-verifies.)
- WCAG 2.2 AA: color is never the sole state carrier; keep focus traps, visible focus rings, and announce dynamic overlay state via aria-live.
- Before the PR: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm typecheck && pnpm test && pnpm exec eslint src` - this is a frontend-only change so the frontend gates always run; repo-root `ruff` + `black` + `pytest` only if any Python file was touched (this plan touches none).
- Conventional-commit messages; the bare `ui:` type is forbidden (release-please drops it) - use `feat(ui)` / `refactor(ui)` / `fix(ui)` / `test(ui)`.
- `git add` with enumerated paths only - never globs, never `git add .`.
- Work on branch `feat/add-videos-ux` (already checked out). Line numbers cited for CURRENT files are valid at branch start; for files edited by an earlier task, locate edits by the quoted anchor text, not line numbers.
- Run a single test file with `pnpm test src/path/to/file.test.tsx` (the `test` script is `vitest run`; extra args are filters).

---

### Task 1: Deployment-mode resolution state

**Files:**
- Modify: `src/splitsmith/ui_static/src/lib/features.ts` (whole file, 76 lines)
- Modify (one line each): `src/splitsmith/ui_static/src/App.tsx` (line 104), `src/components/AccountChip.tsx` (line 25), `src/components/match/SyncCard.tsx` (line 94), `src/pages/Results.tsx` (line 146), `src/pages/Ingest.tsx` (line 73), `src/pages/Home.tsx` (line 84), `src/pages/Pick.tsx` (line 65), `src/pages/Export.tsx` (line 135), `src/pages/CreateMatch.tsx` (lines 245 and 1024), `src/components/AddFootageModal.tsx` (line 107) - all paths under `src/splitsmith/ui_static/`
- Modify: `src/splitsmith/ui_static/src/components/match/SyncCard.test.tsx` (lines 35-41, 57, 65), `src/splitsmith/ui_static/src/pages/Results.test.tsx` (line 13)
- Test (create): `src/splitsmith/ui_static/src/lib/features.test.ts`

**Interfaces:**
- Consumes: `api.getServerFeatures(): Promise<{ lab: boolean; mode: "local" | "hosted" }>` (exists in `@/lib/api`).
- Produces: `useDeploymentMode(): DeploymentModeState` where `export interface DeploymentModeState { mode: "local" | "hosted"; resolved: boolean }` - exported from `@/lib/features`. Later tasks (3, 4) rely on exactly this shape. `export type DeploymentMode = "local" | "hosted"` stays exported.

- [ ] **Step 1: Write the failing test**

Create `src/splitsmith/ui_static/src/lib/features.test.ts`:

```ts
/**
 * useDeploymentMode resolution state (add-videos UX rework).
 *
 * The hook used to return a bare string that read "local" while the
 * features fetch was in flight - hosted users briefly saw local-only
 * chrome. It now returns { mode, resolved } so gated surfaces can hold
 * a neutral skeleton until the answer is real.
 *
 * The module keeps a module-level promise cache, so each test resets
 * the module registry and re-imports a fresh copy.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
});

async function setup(mode: "local" | "hosted") {
  let resolveFeatures: (v: { lab: boolean; mode: "local" | "hosted" }) => void = () => {};
  vi.doMock("@/lib/api", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/lib/api")>();
    return {
      ...actual,
      api: {
        ...actual.api,
        getServerFeatures: vi.fn(
          () =>
            new Promise<{ lab: boolean; mode: "local" | "hosted" }>((res) => {
              resolveFeatures = res;
            }),
        ),
      },
    };
  });
  const { useDeploymentMode } = await import("@/lib/features");
  return {
    useDeploymentMode,
    settle: async () => {
      await act(async () => {
        resolveFeatures({ lab: false, mode });
      });
    },
  };
}

describe("useDeploymentMode", () => {
  it("reports local + unresolved while the features fetch is in flight", async () => {
    const { useDeploymentMode } = await setup("hosted");
    const { result } = renderHook(() => useDeploymentMode());
    expect(result.current).toEqual({ mode: "local", resolved: false });
  });

  it("settles to hosted + resolved once the fetch lands", async () => {
    const { useDeploymentMode, settle } = await setup("hosted");
    const { result } = renderHook(() => useDeploymentMode());
    await settle();
    await waitFor(() =>
      expect(result.current).toEqual({ mode: "hosted", resolved: true }),
    );
  });

  it("resolves immediately for mounts after the cache has settled", async () => {
    const { useDeploymentMode, settle } = await setup("hosted");
    const first = renderHook(() => useDeploymentMode());
    await settle();
    await waitFor(() => expect(first.result.current.resolved).toBe(true));
    const second = renderHook(() => useDeploymentMode());
    expect(second.result.current).toEqual({ mode: "hosted", resolved: true });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm test src/lib/features.test.ts`
Expected: FAIL - `result.current` is the string `"local"`, not `{ mode: "local", resolved: false }` (`expect(result.current).toEqual(...)` mismatch in all three tests).

- [ ] **Step 3: Write minimal implementation**

Replace the bottom half of `src/splitsmith/ui_static/src/lib/features.ts` (keep the file header comment and the `useLabEnabled` doc comment; the whole file becomes):

```ts
/**
 * Server feature flags accessor (issue #149 follow-up).
 *
 * The Lab nav entry + every fixture-related action in the production
 * UI is gated on whether ``splitsmith ui --lab`` was passed. The flag
 * comes from the same ``/api/server/features`` endpoint AppShell
 * already polls; this hook lets non-Lab pages reuse the same answer
 * without re-fetching.
 *
 * Implementation: a tiny module-level promise cache. The first hook
 * call kicks off the fetch; subsequent calls share the same promise
 * and resolve once. Cheap and safe for the small set of consumers we
 * have. No invalidation because the flag is a server-launch decision
 * and can only change across a server restart.
 */

import { useEffect, useState } from "react";

import { api } from "./api";

export type DeploymentMode = "local" | "hosted";

export interface DeploymentModeState {
  /** The server's deployment mode. "local" until resolved. */
  mode: DeploymentMode;
  /** False while /api/server/features is still in flight. Surfaces
   *  that differ per mode render a neutral skeleton until this is
   *  true, instead of flashing the local variant at hosted users.
   *  A failed fetch resolves with the local fallback (resolved: true)
   *  so a desktop install with a flaky first request stays usable. */
  resolved: boolean;
}

type Features = { lab: boolean; mode: DeploymentMode };

let cached: Promise<Features> | null = null;
/** Synchronously readable copy of the settled answer so components
 *  mounting after the first resolve start resolved (no skeleton
 *  flash on every later mount). */
let settled: Features | null = null;

function fetchFeatures(): Promise<Features> {
  if (cached === null) {
    cached = api
      .getServerFeatures()
      .catch(() => ({ lab: false, mode: "local" }) as Features)
      .then((f) => {
        settled = f;
        return f;
      });
  }
  return cached;
}

/** Returns ``true`` when the server was launched with ``--lab``.
 *  ``false`` while loading or on fetch failure - the safe default
 *  for hiding fixture-related affordances on end-user installs. */
export function useLabEnabled(): boolean {
  const [enabled, setEnabled] = useState(settled ? Boolean(settled.lab) : false);
  useEffect(() => {
    let alive = true;
    void fetchFeatures().then((f) => {
      if (alive) setEnabled(Boolean(f.lab));
    });
    return () => {
      alive = false;
    };
  }, []);
  return enabled;
}

/** Deployment mode + whether it has actually been fetched yet.
 *
 * - ``"local"`` - ``splitsmith ui`` against the host filesystem.
 *   Folder pickers + project-folder inputs are meaningful.
 * - ``"hosted"`` - ``splitsmith serve`` against object storage;
 *   raw uploads go through the upload endpoint.
 */
export function useDeploymentMode(): DeploymentModeState {
  const [state, setState] = useState<DeploymentModeState>(() =>
    settled
      ? { mode: settled.mode === "hosted" ? "hosted" : "local", resolved: true }
      : { mode: "local", resolved: false },
  );
  useEffect(() => {
    let alive = true;
    void fetchFeatures().then((f) => {
      if (alive) {
        setState({ mode: f.mode === "hosted" ? "hosted" : "local", resolved: true });
      }
    });
    return () => {
      alive = false;
    };
  }, []);
  return state;
}
```

Then update every consumer (mechanical destructures - the surrounding logic is unchanged):

| File (under `src/splitsmith/ui_static/src/`) | Old line | New line |
| --- | --- | --- |
| `App.tsx:104` | `const mode = useDeploymentMode();` | `const { mode } = useDeploymentMode();` |
| `components/AccountChip.tsx:25` | `const mode = useDeploymentMode();` | `const { mode } = useDeploymentMode();` |
| `components/match/SyncCard.tsx:94` | `const mode = useDeploymentMode();` | `const { mode } = useDeploymentMode();` |
| `pages/Results.tsx:146` | `const deploymentMode = useDeploymentMode();` | `const { mode: deploymentMode } = useDeploymentMode();` |
| `pages/Ingest.tsx:73` | `const mode = useDeploymentMode();` | `const { mode } = useDeploymentMode();` |
| `pages/Home.tsx:84` | `const deploymentMode = useDeploymentMode();` | `const { mode: deploymentMode } = useDeploymentMode();` |
| `pages/Pick.tsx:65` | `const mode = useDeploymentMode();` | `const { mode } = useDeploymentMode();` |
| `pages/Export.tsx:135` | `const deploymentMode = useDeploymentMode();` | `const { mode: deploymentMode } = useDeploymentMode();` |
| `pages/CreateMatch.tsx:245` | `const deploymentMode = useDeploymentMode();` | `const { mode: deploymentMode } = useDeploymentMode();` |
| `pages/CreateMatch.tsx:1024` | `const deploymentMode = useDeploymentMode();` | `const { mode: deploymentMode } = useDeploymentMode();` |
| `components/AddFootageModal.tsx:107` | `const deploymentMode = useDeploymentMode();` | `const { mode: deploymentMode } = useDeploymentMode();` |

Also stale-comment fix in `pages/Results.tsx` lines 143-145: replace the sentence `useDeploymentMode() returns "local" while the features fetch is in flight (conservative default), so the button pops in after the first fetch settles - the same behavior as other hosted-only chrome.` with `useDeploymentMode() reports mode "local" until the features fetch resolves, so the button pops in after the first fetch settles - the same behavior as other hosted-only chrome.`

Test-mock updates:

`src/components/match/SyncCard.test.tsx` line 35-41 becomes:

```ts
vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return {
    ...actual,
    useDeploymentMode: vi.fn(() => ({ mode: "local" as const, resolved: true })),
  };
});
```

line 57: `vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: true });`
line 65: `vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });`

`src/pages/Results.test.tsx` line 13 becomes:

```ts
  return { ...actual, useDeploymentMode: () => ({ mode: "local" as const, resolved: true }) };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm test src/lib/features.test.ts && pnpm typecheck && pnpm test`
Expected: features test PASSES; typecheck clean (it is the enforcement that every consumer was updated); full suite green.

- [ ] **Step 5: Commit**

```bash
cd /Users/mathias/work/splitsmith
git add src/splitsmith/ui_static/src/lib/features.ts \
  src/splitsmith/ui_static/src/lib/features.test.ts \
  src/splitsmith/ui_static/src/App.tsx \
  src/splitsmith/ui_static/src/components/AccountChip.tsx \
  src/splitsmith/ui_static/src/components/match/SyncCard.tsx \
  src/splitsmith/ui_static/src/components/match/SyncCard.test.tsx \
  src/splitsmith/ui_static/src/pages/Results.tsx \
  src/splitsmith/ui_static/src/pages/Results.test.tsx \
  src/splitsmith/ui_static/src/pages/Ingest.tsx \
  src/splitsmith/ui_static/src/pages/Home.tsx \
  src/splitsmith/ui_static/src/pages/Pick.tsx \
  src/splitsmith/ui_static/src/pages/Export.tsx \
  src/splitsmith/ui_static/src/pages/CreateMatch.tsx \
  src/splitsmith/ui_static/src/components/AddFootageModal.tsx
git commit -m "feat(ui): expose deployment-mode resolution state from useDeploymentMode"
```

---

### Task 2: Depth-counted drag tracking util

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/dragDepth.ts`
- Test (create): `src/splitsmith/ui_static/src/lib/dragDepth.test.tsx`

**Interfaces:**
- Consumes: nothing project-specific (React only).
- Produces (from `@/lib/dragDepth`), relied on by Tasks 3-5:
  - `dragHasFiles(e: { dataTransfer: DataTransfer | null }): boolean`
  - `useWindowFileDrag(enabled: boolean): boolean` - true while a file drag is anywhere over the window.
  - `useElementFileDrag(): { dragging: boolean; reset: () => void; handlers: { onDragEnter: (e: React.DragEvent) => void; onDragOver: (e: React.DragEvent) => void; onDragLeave: (e: React.DragEvent) => void } }`

- [ ] **Step 1: Write the failing test**

Create `src/splitsmith/ui_static/src/lib/dragDepth.test.tsx`:

```tsx
/**
 * Depth-counted drag tracking (add-videos UX rework).
 *
 * dragenter/dragleave fire per child crossed, so a naive boolean
 * flickers. These tests pin the counter behavior: nested enter/leave
 * pairs keep the state active, drop resets it, non-file drags are
 * ignored, and the disabled window hook attaches nothing.
 */
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useElementFileDrag, useWindowFileDrag } from "@/lib/dragDepth";

const fileDrag = { dataTransfer: { types: ["Files"] } };

describe("useWindowFileDrag", () => {
  it("stays active across nested dragenter/dragleave pairs", () => {
    const { result } = renderHook(() => useWindowFileDrag(true));
    act(() => {
      fireEvent.dragEnter(window, fileDrag);
    });
    act(() => {
      fireEvent.dragEnter(window, fileDrag);
    });
    act(() => {
      fireEvent.dragLeave(window, fileDrag);
    });
    expect(result.current).toBe(true);
    act(() => {
      fireEvent.dragLeave(window, fileDrag);
    });
    expect(result.current).toBe(false);
  });

  it("resets on drop", () => {
    const { result } = renderHook(() => useWindowFileDrag(true));
    act(() => {
      fireEvent.dragEnter(window, fileDrag);
    });
    expect(result.current).toBe(true);
    act(() => {
      fireEvent.drop(window, fileDrag);
    });
    expect(result.current).toBe(false);
  });

  it("ignores non-file drags and does nothing when disabled", () => {
    const { result } = renderHook(() => useWindowFileDrag(true));
    act(() => {
      fireEvent.dragEnter(window, { dataTransfer: { types: ["text/plain"] } });
    });
    expect(result.current).toBe(false);

    const off = renderHook(() => useWindowFileDrag(false));
    act(() => {
      fireEvent.dragEnter(window, fileDrag);
    });
    expect(off.result.current).toBe(false);
  });
});

function Zone() {
  const { dragging, reset, handlers } = useElementFileDrag();
  return (
    <div
      data-testid="zone"
      data-dragging={dragging ? "1" : "0"}
      {...handlers}
      onDrop={(e) => {
        e.preventDefault();
        reset();
      }}
    >
      <span data-testid="child">child</span>
    </div>
  );
}

describe("useElementFileDrag", () => {
  it("does not flicker when the cursor crosses a child element", () => {
    render(<Zone />);
    const zone = screen.getByTestId("zone");
    const child = screen.getByTestId("child");
    fireEvent.dragEnter(zone, fileDrag);
    fireEvent.dragEnter(child, fileDrag); // bubbles to zone -> depth 2
    fireEvent.dragLeave(child, fileDrag); // depth 1 -> still dragging
    expect(zone).toHaveAttribute("data-dragging", "1");
    fireEvent.dragLeave(zone, fileDrag);
    expect(zone).toHaveAttribute("data-dragging", "0");
  });

  it("reset() clears the state on drop", () => {
    render(<Zone />);
    const zone = screen.getByTestId("zone");
    fireEvent.dragEnter(zone, fileDrag);
    expect(zone).toHaveAttribute("data-dragging", "1");
    fireEvent.drop(zone, fileDrag);
    expect(zone).toHaveAttribute("data-dragging", "0");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm test src/lib/dragDepth.test.tsx`
Expected: FAIL - module `@/lib/dragDepth` does not exist (import error).

- [ ] **Step 3: Write minimal implementation**

Create `src/splitsmith/ui_static/src/lib/dragDepth.ts`:

```ts
/**
 * Depth-counted drag tracking (add-videos UX rework).
 *
 * dragenter/dragleave fire for every child element the cursor crosses,
 * so a naive isDragging boolean flickers off between children. The fix
 * is the standard enter/leave depth counter: increment on enter,
 * decrement on leave, active while depth > 0, hard-reset on drop or
 * dragend. Two flavors:
 *
 *   - useWindowFileDrag: window-level listeners for full-page drop
 *     targets (hosted Ingest).
 *   - useElementFileDrag: React handlers to spread on a bounded
 *     dropzone (hosted upload modal).
 */

import { useCallback, useEffect, useRef, useState } from "react";

/** True when a drag carries files (vs text selections or in-app drags). */
export function dragHasFiles(e: { dataTransfer: DataTransfer | null }): boolean {
  const types = e.dataTransfer?.types;
  return Boolean(types && Array.from(types).includes("Files"));
}

/** Window-level file-drag tracking. Returns true while a file drag is
 *  anywhere over the window. Pass ``enabled: false`` to keep the
 *  listeners detached (e.g. local mode, or before the mode resolves). */
export function useWindowFileDrag(enabled: boolean): boolean {
  const depth = useRef(0);
  const [active, setActive] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    const onEnter = (e: DragEvent) => {
      if (!dragHasFiles(e)) return;
      depth.current += 1;
      setActive(true);
    };
    const onLeave = (e: DragEvent) => {
      if (!dragHasFiles(e)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setActive(false);
    };
    const onEnd = () => {
      depth.current = 0;
      setActive(false);
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onEnd);
    window.addEventListener("dragend", onEnd);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onEnd);
      window.removeEventListener("dragend", onEnd);
      depth.current = 0;
      setActive(false);
    };
  }, [enabled]);
  return active;
}

/** Element-level file-drag tracking. Spread ``handlers`` onto the
 *  dropzone element and call ``reset()`` inside your own onDrop.
 *  onDragOver preventDefaults unconditionally - without it the browser
 *  refuses the drop. */
export function useElementFileDrag(): {
  dragging: boolean;
  reset: () => void;
  handlers: {
    onDragEnter: (e: React.DragEvent) => void;
    onDragOver: (e: React.DragEvent) => void;
    onDragLeave: (e: React.DragEvent) => void;
  };
} {
  const depth = useRef(0);
  const [dragging, setDragging] = useState(false);
  const reset = useCallback(() => {
    depth.current = 0;
    setDragging(false);
  }, []);
  const onDragEnter = useCallback((e: React.DragEvent) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    depth.current += 1;
    setDragging(true);
  }, []);
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
  }, []);
  const onDragLeave = useCallback((e: React.DragEvent) => {
    if (!dragHasFiles(e)) return;
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setDragging(false);
  }, []);
  return { dragging, reset, handlers: { onDragEnter, onDragOver, onDragLeave } };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm test src/lib/dragDepth.test.tsx`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/mathias/work/splitsmith
git add src/splitsmith/ui_static/src/lib/dragDepth.ts \
  src/splitsmith/ui_static/src/lib/dragDepth.test.tsx
git commit -m "feat(ui): add depth-counted file-drag tracking util"
```

---
### Task 3: App-wide drop guard with local-mode toast

**Files:**
- Create: `src/splitsmith/ui_static/src/components/DropGuard.tsx`
- Modify: `src/splitsmith/ui_static/src/App.tsx` (mount next to `<UploadDock />`, currently line 137; add import)
- Test (create): `src/splitsmith/ui_static/src/components/DropGuard.test.tsx`

**Interfaces:**
- Consumes: `useDeploymentMode(): { mode: "local" | "hosted"; resolved: boolean }` from `@/lib/features` (Task 1); `dragHasFiles` from `@/lib/dragDepth` (Task 2); `Portal` from `@/components/ui/Portal`.
- Produces: `export function DropGuard(): JSX.Element` from `@/components/DropGuard`. Contract relied on by Task 4: the guard's window `drop` listener checks `e.defaultPrevented` BEFORE calling `e.preventDefault()`, and it never stops propagation - so other window-level drop listeners (hosted Ingest) still fire, and element-level handlers that must suppress the guard call `stopPropagation()`.

- [ ] **Step 1: Write the failing test**

Create `src/splitsmith/ui_static/src/components/DropGuard.test.tsx`:

```tsx
/**
 * App-wide drop guard (add-videos UX rework).
 *
 * An unhandled drop must never navigate the SPA into the dropped file.
 * In local mode a file drop shows a toast pointing at the picker;
 * hosted and unresolved modes stay silent (hosted has real drop
 * surfaces; unresolved cannot know what to say yet).
 */
import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DropGuard } from "@/components/DropGuard";
import { useDeploymentMode } from "@/lib/features";

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return {
    ...actual,
    useDeploymentMode: vi.fn(() => ({ mode: "local" as const, resolved: true })),
  };
});

function dropOnWindow(): DragEvent {
  const ev = new Event("drop", { bubbles: true, cancelable: true }) as DragEvent;
  Object.defineProperty(ev, "dataTransfer", {
    value: { types: ["Files"], files: [] },
  });
  act(() => {
    window.dispatchEvent(ev);
  });
  return ev;
}

describe("DropGuard", () => {
  beforeEach(() => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: true });
  });

  it("prevents default on unhandled drops so the browser never navigates", () => {
    render(<DropGuard />);
    const ev = dropOnWindow();
    expect(ev.defaultPrevented).toBe(true);
  });

  it("prevents default on dragover (required for drop to be cancellable)", () => {
    render(<DropGuard />);
    const ev = new Event("dragover", { bubbles: true, cancelable: true });
    act(() => {
      window.dispatchEvent(ev);
    });
    expect(ev.defaultPrevented).toBe(true);
  });

  it("shows the local-mode toast on a file drop", async () => {
    render(<DropGuard />);
    dropOnWindow();
    expect(
      await screen.findByText(/drops can't be added in local mode/i),
    ).toBeInTheDocument();
  });

  it("shows no toast in hosted mode", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });
    render(<DropGuard />);
    const ev = dropOnWindow();
    expect(ev.defaultPrevented).toBe(true);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText(/drops can't be added/i)).not.toBeInTheDocument();
  });

  it("shows no toast before the mode resolves (still prevents default)", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: false });
    render(<DropGuard />);
    const ev = dropOnWindow();
    expect(ev.defaultPrevented).toBe(true);
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText(/drops can't be added/i)).not.toBeInTheDocument();
  });

  it("does not toast when another handler already handled the drop", async () => {
    render(<DropGuard />);
    const ev = new Event("drop", { bubbles: true, cancelable: true }) as DragEvent;
    Object.defineProperty(ev, "dataTransfer", {
      value: { types: ["Files"], files: [] },
    });
    ev.preventDefault(); // simulate an inner handler having consumed it
    act(() => {
      window.dispatchEvent(ev);
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText(/drops can't be added/i)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm test src/components/DropGuard.test.tsx`
Expected: FAIL - module `@/components/DropGuard` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `src/splitsmith/ui_static/src/components/DropGuard.tsx`:

```tsx
/**
 * App-wide drag/drop guard (add-videos UX rework).
 *
 * A drop on any unhandled element makes the browser navigate into the
 * dropped file, destroying SPA session state. This guard preventDefaults
 * dragover + drop at the window level so an unhandled drop is inert.
 *
 * In local mode a file drop additionally shows a short toast pointing
 * at the picker - a browser drop cannot expose absolute host paths, so
 * local (path-based) registration can never be fed by a drop.
 *
 * Handled drops are unaffected: element-level dropzones that consume a
 * drop call stopPropagation() (so this listener never sees it), and the
 * hosted Ingest page's window-level drop handler runs independently -
 * this guard checks defaultPrevented BEFORE preventDefaulting and never
 * stops propagation itself.
 *
 * Toast follows the SaveToast pattern (Audit.tsx): body Portal, z-toast
 * token, role="status" live region rendered unconditionally so screen
 * readers pick up the change.
 */

import { useEffect, useState } from "react";

import { Portal } from "@/components/ui/Portal";
import { dragHasFiles } from "@/lib/dragDepth";
import { useDeploymentMode } from "@/lib/features";

const TOAST_MS = 4000;

export function DropGuard() {
  const { mode, resolved } = useDeploymentMode();
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    const onDragOver = (e: DragEvent) => {
      e.preventDefault();
    };
    const onDrop = (e: DragEvent) => {
      const unhandled = !e.defaultPrevented;
      e.preventDefault();
      if (unhandled && resolved && mode === "local" && dragHasFiles(e)) {
        setToast("Drops can't be added in local mode - use Pick a folder");
      }
    };
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("drop", onDrop);
    };
  }, [mode, resolved]);

  useEffect(() => {
    if (toast === null) return;
    const id = window.setTimeout(() => setToast(null), TOAST_MS);
    return () => window.clearTimeout(id);
  }, [toast]);

  return (
    <Portal>
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 right-4 z-toast"
      >
        {toast ? (
          <div className="pointer-events-auto rounded-md border border-rule-strong bg-surface px-3 py-2 text-sm text-ink shadow-md">
            {toast}
          </div>
        ) : null}
      </div>
    </Portal>
  );
}
```

Mount it in `src/splitsmith/ui_static/src/App.tsx`: add `import { DropGuard } from "@/components/DropGuard";` to the import block, and inside `UploadProvider` change

```tsx
          <UploadProvider>
            <UploadDock />
```

to

```tsx
          <UploadProvider>
            <UploadDock />
            <DropGuard />
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm test src/components/DropGuard.test.tsx && pnpm test`
Expected: PASS (6 tests); full suite green (App.routes.test.tsx still passes - DropGuard renders an empty live region there).

- [ ] **Step 5: Commit**

```bash
cd /Users/mathias/work/splitsmith
git add src/splitsmith/ui_static/src/components/DropGuard.tsx \
  src/splitsmith/ui_static/src/components/DropGuard.test.tsx \
  src/splitsmith/ui_static/src/App.tsx
git commit -m "feat(ui): guard the SPA against unhandled file drops"
```

---

### Task 4: Mode-gated Ingest empty state + hosted full-page drop

**Files:**
- Modify: `src/splitsmith/ui_static/src/pages/Ingest.tsx` - imports (lines 19-50), `IngestInner` mode/uploads wiring (lines 66-147), relink-button gate (line 415), intro copy (lines 389-407), empty-state branch (lines 448-452), and the `EmptyState`/`DropZone`/`RecentSources` block (lines 500-639; `DropZone` is deleted outright)
- Test (create): `src/splitsmith/ui_static/src/pages/Ingest.emptyState.test.tsx`

**Interfaces:**
- Consumes: `useDeploymentMode()` -> `{ mode, resolved }` (Task 1); `useWindowFileDrag(enabled: boolean): boolean` (Task 2); `useUploads().enqueue(files: FileList | File[], ctx: { slug: string; stages: { stage_number: number; stage_name: string }[] }): void` (exists in `@/lib/uploads`); `Portal` (existing). DropGuard (Task 3) already preventDefaults window drops; this page's window drop listener runs regardless and must NOT gate on `defaultPrevented`.
- Produces: Ingest renders `AddFootageSkeleton` until `resolved`; local empty state = "Add footage" card with "Pick a folder" button (opens `showAddFootage` state, still the AddFootageModal until Task 6); hosted empty state = "Browse files" button + full-page drop that enqueues. Later tasks reuse `setShowAddFootage(true)` as the single "open the add-footage surface" entry point.

- [ ] **Step 1: Write the failing test**

Create `src/splitsmith/ui_static/src/pages/Ingest.emptyState.test.tsx`:

```tsx
/**
 * Ingest empty-state mode gating + hosted full-page drop
 * (add-videos UX rework, spec 2026-08-08).
 *
 * - nothing mode-specific renders before the deployment mode resolves
 *   (neutral skeleton only);
 * - local renders "Pick a folder" and no drop affordance at all;
 * - hosted renders "Browse files" and no picker affordance;
 * - a window-level drop on the hosted page enqueues into useUploads;
 * - the same drop in local mode enqueues nothing (DropGuard owns the
 *   toast; covered in DropGuard.test.tsx).
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import { api, type MatchProject, type ServerHealth } from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import { useUploads } from "@/lib/uploads";
import { Ingest } from "@/pages/Ingest";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn(),
      getHealth: vi.fn(),
      listMatchShooters: vi.fn(),
      getBeepQueue: vi.fn(),
    },
  };
});

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return {
    ...actual,
    useDeploymentMode: vi.fn(() => ({ mode: "local" as const, resolved: true })),
  };
});

vi.mock("@/lib/uploads", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/uploads")>();
  return { ...actual, useUploads: vi.fn() };
});

const emptyProject = {
  name: "Test Match",
  stages: [],
  unassigned_videos: [],
  last_scanned_dir: null,
} as unknown as MatchProject;

const health = {
  status: "ok",
  bound: true,
  project_name: "Test Match",
  project_root: "/tmp/test",
  match_id: "m1",
  kind: "match",
  default_shooter_slug: "alice",
  schema_version: 1,
} as unknown as ServerHealth;

const enqueue = vi.fn();

function mockUploads() {
  vi.mocked(useUploads).mockReturnValue({
    uploads: [],
    enqueue,
    cancel: vi.fn(),
    cancelAll: vi.fn(),
    clearFinished: vi.fn(),
    inFlight: false,
    attachTick: 0,
    probeFor: vi.fn(),
    queue: {},
  } as unknown as ReturnType<typeof useUploads>);
}

function renderIngest() {
  return render(
    <ConfirmProvider>
      <MemoryRouter initialEntries={["/match/m1/ingest/alice"]}>
        <Routes>
          <Route path="/match/:matchId/ingest/:slug" element={<Ingest />} />
        </Routes>
      </MemoryRouter>
    </ConfirmProvider>,
  );
}

describe("Ingest empty state", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUploads();
    vi.mocked(api.getProject).mockResolvedValue(emptyProject);
    vi.mocked(api.getHealth).mockResolvedValue(health);
    vi.mocked(api.listMatchShooters).mockResolvedValue({ shooters: [] });
    vi.mocked(api.getBeepQueue).mockResolvedValue({ pending_count: 0 });
  });

  it("renders a neutral skeleton until the mode resolves", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: false });
    renderIngest();
    expect(
      await screen.findByRole("status", { name: /checking how footage can be added/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /pick a folder/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /browse files/i })).not.toBeInTheDocument();
  });

  it("local mode renders the picker card and no drop affordance", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: true });
    renderIngest();
    expect(await screen.findByRole("button", { name: /pick a folder/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /browse files/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/drop/i)).not.toBeInTheDocument();
  });

  it("hosted mode renders the upload card and no picker affordance", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });
    renderIngest();
    expect(await screen.findByRole("button", { name: /browse files/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /pick a folder/i })).not.toBeInTheDocument();
  });

  it("hosted mode enqueues a window-level drop", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "hosted", resolved: true });
    renderIngest();
    await screen.findByRole("button", { name: /browse files/i });
    const file = new File(["x"], "GH010001.MP4", { type: "video/mp4" });
    act(() => {
      fireEvent.drop(window, { dataTransfer: { files: [file], types: ["Files"] } });
    });
    expect(enqueue).toHaveBeenCalledTimes(1);
    const [files, ctx] = enqueue.mock.calls[0];
    expect(Array.from(files as FileList)).toHaveLength(1);
    expect(ctx).toEqual({ slug: "alice", stages: [] });
  });

  it("local mode never enqueues a window-level drop", async () => {
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: true });
    renderIngest();
    await screen.findByRole("button", { name: /pick a folder/i });
    const file = new File(["x"], "GH010001.MP4", { type: "video/mp4" });
    act(() => {
      fireEvent.drop(window, { dataTransfer: { files: [file], types: ["Files"] } });
    });
    expect(enqueue).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm test src/pages/Ingest.emptyState.test.tsx`
Expected: FAIL - no skeleton status role exists, local mode still renders the decorative "Drop a folder of videos" copy (so `queryByText(/drop/i)` matches), hosted mode has no "Browse files" button, and the window drop never enqueues.

- [ ] **Step 3: Write minimal implementation**

All edits in `src/splitsmith/ui_static/src/pages/Ingest.tsx`.

(a) Imports: add `Upload` to the lucide import list (line 19-27), add `useRef` is already imported; add:

```tsx
import { Portal } from "@/components/ui/Portal";
import { useWindowFileDrag } from "@/lib/dragDepth";
```

(b) In `IngestInner`, change the mode line (from Task 1) and the uploads destructure:

```tsx
  const { mode, resolved: modeResolved } = useDeploymentMode();
```

and (currently `const { attachTick } = useUploads();` near line 142):

```tsx
  const { attachTick, enqueue } = useUploads();
```

(c) Hosted full-page drop wiring - add directly after the `attachTick` effect (after line 147):

```tsx
  // Hosted mode: the whole Ingest page is a drop target. Window-level
  // dragenter/dragleave with a depth counter drives the full-page
  // overlay; a drop anywhere enqueues into the background upload queue.
  // DropGuard (App root) preventDefaults the same event so the browser
  // never navigates; it does not stop propagation, so this listener
  // always sees the drop.
  const hostedDropActive = modeResolved && mode === "hosted";
  const pageDragActive = useWindowFileDrag(hostedDropActive);
  const stagesRef = useRef<{ stage_number: number; stage_name: string }[]>([]);
  useEffect(() => {
    stagesRef.current = project?.stages ?? [];
  }, [project]);
  useEffect(() => {
    if (!hostedDropActive) return;
    const onDrop = (e: DragEvent) => {
      if (e.dataTransfer && e.dataTransfer.files.length > 0) {
        enqueue(e.dataTransfer.files, { slug, stages: stagesRef.current });
      }
    };
    window.addEventListener("drop", onDrop);
    return () => window.removeEventListener("drop", onDrop);
  }, [hostedDropActive, enqueue, slug]);
```

(d) Intro copy (lines 389-407): change the Kicker line to

```tsx
            Ingest &middot; {isEmpty ? "add footage" : "auto-matched"}
```

and the lead paragraph to a mode-neutral sentence:

```tsx
          <p className="max-w-[40rem] text-[0.875rem] text-muted">
            {isEmpty
              ? "Splitsmith auto-matches each video to a stage by recording timestamp."
              : "Auto-matched to stages by recording timestamp. Review the assignments and confirm to start processing."}
          </p>
```

(e) Relink affordance gate (line 415): `{!isEmpty && mode === "local" && (` becomes `{!isEmpty && modeResolved && mode === "local" && (` (and the matching dialog guard on line 434: `{showRelinkDialog && modeResolved && mode === "local" && (`).

(f) Empty-state branch (lines 448-452) becomes:

```tsx
        {isEmpty ? (
          modeResolved ? (
            <EmptyState
              mode={mode}
              onAdd={() => setShowAddFootage(true)}
              lastScannedDir={lastScannedDir}
            />
          ) : (
            <AddFootageSkeleton />
          )
        ) : project ? (
```

(g) Add the overlay + live region just before the closing `</main>` tag (after the `showAddFootage` block):

```tsx
        {hostedDropActive && (
          <span className="sr-only" role="status" aria-live="polite">
            {pageDragActive ? "Release to add the files to the upload queue" : ""}
          </span>
        )}
        {pageDragActive && (
          <Portal>
            <div
              aria-hidden
              className="pointer-events-none fixed inset-0 z-takeover flex items-center justify-center bg-bg/80 backdrop-blur-sm"
            >
              <div className="rounded-2xl border-2 border-dashed border-led bg-surface px-10 py-8 text-center shadow-[0_0_28px_var(--color-led-glow)]">
                <div className="font-display text-2xl font-bold uppercase tracking-tight text-ink">
                  Drop videos to upload
                </div>
                <div className="mt-1 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
                  They join this shooter's upload queue
                </div>
              </div>
            </div>
          </Portal>
        )}
```

(h) Replace the whole `EmptyState` + `DropZone` block (lines 500-589). `DropZone` is DELETED (dashed border, corner brackets, "Drop a folder of videos" copy - all of it). New code:

```tsx
function EmptyState({
  mode,
  onAdd,
  lastScannedDir,
}: {
  mode: "local" | "hosted";
  onAdd: () => void;
  lastScannedDir: string | null;
}) {
  return (
    <>
      <AddFootageCard mode={mode} onAdd={onAdd} />
      {mode === "local" && lastScannedDir && (
        <RecentSources
          items={[
            {
              path: lastScannedDir,
              label: "Last scanned",
              when: "previously",
            },
          ]}
          onUse={onAdd}
        />
      )}
      <TipCards />
    </>
  );
}

/** Neutral placeholder while /api/server/features is in flight - the
 *  local picker and the hosted upload surface must not flash at the
 *  wrong audience (spec: deployment-mode resolution). */
function AddFootageSkeleton() {
  return (
    <div
      role="status"
      aria-label="Checking how footage can be added"
      className="mb-5 animate-pulse rounded-2xl border border-rule bg-surface px-10 py-14 text-center"
    >
      <div className="mx-auto mb-4 size-[72px] rounded-2xl bg-surface-3" />
      <div className="mx-auto mb-3 h-8 w-64 rounded bg-surface-3" />
      <div className="mx-auto h-9 w-40 rounded-md bg-surface-3" />
    </div>
  );
}

function AddFootageCard({
  mode,
  onAdd,
}: {
  mode: "local" | "hosted";
  onAdd: () => void;
}) {
  return (
    <div className="relative mb-5 overflow-hidden rounded-2xl border border-rule-strong bg-surface px-10 py-14 text-center">
      <div className="mx-auto mb-4 inline-flex size-[72px] items-center justify-center rounded-2xl border border-led-deep bg-led/10 text-led shadow-[0_0_24px_var(--color-led-glow)]">
        {mode === "local" ? (
          <Folder className="size-9" strokeWidth={1.6} />
        ) : (
          <Upload className="size-9" strokeWidth={1.6} />
        )}
      </div>
      <h2 className="mb-3 font-display text-3xl font-bold uppercase tracking-tight text-ink">
        Add footage
      </h2>
      <p className="mx-auto mb-5 max-w-xl text-[0.9375rem] leading-relaxed text-muted">
        {mode === "local"
          ? "Pick the folder your camera footage lives in. Splitsmith scans it for video files and groups them by camera."
          : "Drop video files anywhere on this page, or browse for them. Uploads land in your hosted storage and attach to this shooter."}
      </p>
      <div className="inline-flex gap-2.5">
        <Button
          onClick={onAdd}
          className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink"
        >
          <Folder className="size-3.5" />
          <span className="font-display uppercase tracking-[0.1em]">
            {mode === "local" ? "Pick a folder" : "Browse files"}
          </span>
        </Button>
      </div>
      <p className="mt-5 font-mono text-[0.625rem] tabular-nums text-subtle">
        Supported:{" "}
        <code className="rounded border border-rule bg-surface-3 px-1.5 py-0.5 text-[0.6875rem] text-ink-2">
          .mp4
        </code>{" "}
        &middot;{" "}
        <code className="rounded border border-rule bg-surface-3 px-1.5 py-0.5 text-[0.6875rem] text-ink-2">
          .mov
        </code>{" "}
        &middot;{" "}
        <code className="rounded border border-rule bg-surface-3 px-1.5 py-0.5 text-[0.6875rem] text-ink-2">
          .mkv
        </code>{" "}
        &middot;{" "}
        <code className="rounded border border-rule bg-surface-3 px-1.5 py-0.5 text-[0.6875rem] text-ink-2">
          .360
        </code>
      </p>
    </div>
  );
}
```

(i) In `RecentSources` (line 605): the header hint `Drop the same folder again` becomes `Scan the same folder again` (decorative drop copy dies everywhere). Remove the now-unused `Package` lucide import; keep `Folder`, `Camera`, `Clock`, `Info`, `ArrowLeft`, `ArrowRight`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm test src/pages/Ingest.emptyState.test.tsx && pnpm typecheck`
Expected: PASS (5 tests); typecheck clean.

- [ ] **Step 5: Commit**

```bash
cd /Users/mathias/work/splitsmith
git add src/splitsmith/ui_static/src/pages/Ingest.tsx \
  src/splitsmith/ui_static/src/pages/Ingest.emptyState.test.tsx
git commit -m "feat(ui): mode-gate the ingest empty state and add hosted full-page drop"
```

---
### Task 5: Extract HostedUploadModal with a depth-counted dropzone

**Files:**
- Create: `src/splitsmith/ui_static/src/components/HostedUploadModal.tsx`
- Modify: `src/splitsmith/ui_static/src/components/AddFootageModal.tsx` (delete the hosted branch: lines 101-108 comment + `hostedMode`, lines 225-234 early return, lines 766-1301 `HostedUploadSurface` / `HostedUploadBody` / `UploadRow` / `ExistingRow` / `formatRelative`; prune imports)
- Modify: `src/splitsmith/ui_static/src/pages/Ingest.tsx` (mode-gate which modal `showAddFootage` opens)
- Test (create): `src/splitsmith/ui_static/src/components/HostedUploadModal.test.tsx`

**Interfaces:**
- Consumes: `useElementFileDrag()` from `@/lib/dragDepth` (Task 2); `useUploads()` from `@/lib/uploads`; `api.listRawUploads` / `api.deleteRawUpload` / `api.attachRawVideo` (existing); `UploadQueueSummary`, `CoverageSelect`, `Portal`, `useDialogFocus`, `formatBytes` (existing).
- Produces: `export function HostedUploadModal(props: { slug: string; onClose: () => void; onImported: (imported: number, paths: string[]) => void; stages: { stage_number: number; stage_name: string }[] }): JSX.Element` from `@/components/HostedUploadModal`. Task 6 keeps this exact signature when it deletes AddFootageModal.

- [ ] **Step 1: Write the failing test**

Create `src/splitsmith/ui_static/src/components/HostedUploadModal.test.tsx`:

```tsx
/**
 * HostedUploadModal (extracted from AddFootageModal's hosted branch).
 *
 * Pins the two behaviors the extraction changes:
 * - the dropzone uses the depth counter (dragging over a child keeps
 *   the highlight on);
 * - a drop on the dropzone enqueues exactly once and stops
 *   propagation, so the hosted Ingest page's window-level drop target
 *   behind the modal cannot double-enqueue.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HostedUploadModal } from "@/components/HostedUploadModal";
import { api } from "@/lib/api";
import { useUploads } from "@/lib/uploads";
import { queueStats } from "@/lib/uploadStats";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listRawUploads: vi.fn().mockResolvedValue({ uploads: [] }),
    },
  };
});

vi.mock("@/lib/uploads", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/uploads")>();
  return { ...actual, useUploads: vi.fn() };
});

const enqueue = vi.fn();

describe("HostedUploadModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listRawUploads).mockResolvedValue({ uploads: [] });
    vi.mocked(useUploads).mockReturnValue({
      uploads: [],
      enqueue,
      cancel: vi.fn(),
      cancelAll: vi.fn(),
      clearFinished: vi.fn(),
      inFlight: false,
      attachTick: 0,
      probeFor: vi.fn(),
      queue: queueStats([], [], Date.now()),
    } as unknown as ReturnType<typeof useUploads>);
  });

  function renderModal() {
    return render(
      <HostedUploadModal
        slug="alice"
        onClose={vi.fn()}
        onImported={vi.fn()}
        stages={[]}
      />,
    );
  }

  it("keeps the drag highlight while crossing children (depth counter)", async () => {
    renderModal();
    const zone = await screen.findByTestId("hosted-dropzone");
    const inner = screen.getByText(/drop video files here/i);
    const fileDrag = { dataTransfer: { types: ["Files"] } };
    fireEvent.dragEnter(zone, fileDrag);
    fireEvent.dragEnter(inner, fileDrag);
    fireEvent.dragLeave(inner, fileDrag);
    expect(zone.className).toContain("bg-led-tint");
    fireEvent.dragLeave(zone, fileDrag);
    expect(zone.className).not.toContain("bg-led-tint");
  });

  it("enqueues a drop once and stops propagation to the window", async () => {
    const windowDrop = vi.fn();
    window.addEventListener("drop", windowDrop);
    try {
      renderModal();
      const zone = await screen.findByTestId("hosted-dropzone");
      const file = new File(["x"], "GH010001.MP4", { type: "video/mp4" });
      fireEvent.drop(zone, {
        dataTransfer: { files: [file], types: ["Files"] },
      });
      expect(enqueue).toHaveBeenCalledTimes(1);
      expect(windowDrop).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("drop", windowDrop);
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm test src/components/HostedUploadModal.test.tsx`
Expected: FAIL - module `@/components/HostedUploadModal` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `src/splitsmith/ui_static/src/components/HostedUploadModal.tsx`. Its content is a MOVE of the hosted half of `AddFootageModal.tsx` (current lines 766-1301: `HostedUploadBody`, `UploadRow`, `ExistingRow`, `formatRelative` - the `HostedUploadSurface` indirection wrapper is dropped, and `shooterInitials` stays behind in AddFootageModal). Concretely:

1. File header comment:

```tsx
/**
 * Hosted-mode browser upload surface (extracted from AddFootageModal).
 *
 * Drag-and-drop / file-pick, per-file progress, list of what's already
 * uploaded, prune via delete. Files land in S3 under
 * ``users/<id>/raw/`` via ``POST /api/me/raw/upload``; the SPA never
 * sees a host filesystem path. Uploaded objects are then attached to
 * the project (``attachToProject`` ->
 * ``POST /api/shooters/{slug}/raw-videos/attach``), which is what makes
 * them visible to the worker pipeline (#523).
 */
```

2. Imports:

```tsx
import { Check, FolderOpen, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { UploadQueueSummary } from "@/components/UploadQueueSummary";
import { CoverageSelect } from "@/components/ingest/CoverageSelect";
import { Portal } from "@/components/ui/Portal";
import { ApiError, api, type RawUploadEntry } from "@/lib/api";
import { useDialogFocus } from "@/lib/dialogFocus";
import { useElementFileDrag } from "@/lib/dragDepth";
import { formatBytes } from "@/lib/format";
import { useUploads, type PendingUpload } from "@/lib/uploads";
import { cn } from "@/lib/utils";
```

3. Rename `HostedUploadBody` -> `export function HostedUploadModal` with the same props (`{ slug, onClose, onImported, stages }`, types exactly as in the old `HostedUploadBody` signature). Copy its body verbatim EXCEPT the dropzone:

- Delete the line `const [isDragging, setIsDragging] = useState(false);` and the old `onDrop` function (lines 902-908 of the old file).
- Add after the `doEnqueue` declaration:

```tsx
  // Depth-counted drag highlight - the naive isDragging boolean
  // flickered off whenever the cursor crossed a child of the zone.
  const { dragging, reset, handlers } = useElementFileDrag();
```

- Replace the dropzone `<div onDragOver=... onDragLeave=... onDrop=...>` opening tag and handlers (old lines 1009-1021) with:

```tsx
          <div
            data-testid="hosted-dropzone"
            {...handlers}
            onDrop={(e) => {
              // The hosted Ingest page behind this modal listens for
              // window-level drops; stopPropagation keeps a drop on
              // this zone from also enqueueing there (double enqueue).
              e.preventDefault();
              e.stopPropagation();
              reset();
              if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                doEnqueue(e.dataTransfer.files);
              }
            }}
            className={cn(
              "flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors",
              dragging
                ? "border-led bg-led-tint"
                : "border-rule bg-surface-2 hover:border-rule-strong",
            )}
          >
```

(the children of the zone - icon, "Drop video files here", "or", "Choose files...", hidden input - are copied unchanged).

4. `UploadRow`, `ExistingRow`, `formatRelative` move verbatim below the component.

Then shrink `src/splitsmith/ui_static/src/components/AddFootageModal.tsx`:
- Delete the `hostedMode` block: the comment + `const { mode: deploymentMode } = useDeploymentMode();` + `const hostedMode = deploymentMode === "hosted";` (lines 101-108), and the `if (hostedMode) { return <HostedUploadSurface .../>; }` early return (lines 225-234).
- Change `useDialogFocus(!hostedMode, panelRef, onClose, {...})` to `useDialogFocus(true, panelRef, onClose, {...})`.
- Delete `HostedUploadSurface`, `HostedUploadBody`, `UploadRow`, `ExistingRow`, `formatRelative` (lines 766-1301 except `shooterInitials`, which the local header still uses).
- Prune now-unused imports: `UploadQueueSummary`, `CoverageSelect`, `useDeploymentMode`, `useUploads`/`PendingUpload`, `formatBytes`, `RawUploadEntry`.

Then in `src/splitsmith/ui_static/src/pages/Ingest.tsx`, add `import { HostedUploadModal } from "@/components/HostedUploadModal";` and replace the `showAddFootage` block (anchor: `{showAddFootage && (`):

```tsx
        {showAddFootage &&
          modeResolved &&
          (mode === "hosted" ? (
            <HostedUploadModal
              slug={slug}
              onClose={() => setShowAddFootage(false)}
              onImported={(imported, paths) => {
                void afterImport(imported, paths);
              }}
              stages={project?.stages ?? []}
            />
          ) : (
            <AddFootageModal
              slug={slug}
              initialStorage={storage}
              initialPath={lastScannedDir}
              onClose={() => setShowAddFootage(false)}
              onImported={(imported, paths) => {
                void afterImport(imported, paths);
              }}
              onStorageChange={setStorage}
              shooterName={activeShooterName}
              stages={project?.stages ?? []}
            />
          ))}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm test src/components/HostedUploadModal.test.tsx && pnpm typecheck && pnpm test`
Expected: new tests PASS; typecheck clean (catches any missed import prune); full suite green.

- [ ] **Step 5: Commit**

```bash
cd /Users/mathias/work/splitsmith
git add src/splitsmith/ui_static/src/components/HostedUploadModal.tsx \
  src/splitsmith/ui_static/src/components/HostedUploadModal.test.tsx \
  src/splitsmith/ui_static/src/components/AddFootageModal.tsx \
  src/splitsmith/ui_static/src/pages/Ingest.tsx
git commit -m "refactor(ui): extract HostedUploadModal with depth-counted dropzone"
```

---

### Task 6: One-shot local add-footage; delete queue machinery and picker facades

This is a transitional commit that stays on FolderPicker's CURRENT prop API (Task 7 replaces it). It removes: the multi-source queue (`QueueView`, `QueueItem`, two-phase Import, `ResultsView`), the folder/file mutex (`pickerFolderAlreadyWhole` / `pickerFolderHasFileChecks`), the auto-commit sync (`autoCommitFiles` + `onFolderFilesChange` call site), and the `DirectoryPickerModal` facade. Transitional regressions accepted inside this branch only (both restored by Task 7): the storage toggle has no UI (state remains, default `"symlink"`), and a scan error closes the picker and lands in the page-level error banner.

**Files:**
- Delete: `src/splitsmith/ui_static/src/components/AddFootageModal.tsx` (whole file - the local queue wizard; hosted half already moved in Task 5)
- Delete: `src/splitsmith/ui_static/src/components/DirectoryPickerModal.tsx`
- Modify: `src/splitsmith/ui_static/src/pages/Ingest.tsx` (local branch of the add-footage block; add `commitFolder` / `commitFiles`)
- Modify: `src/splitsmith/ui_static/src/pages/CreateMatch.tsx` (import at line 36; picker block anchored at `<DirectoryPickerModal`)
- Modify: `src/splitsmith/ui_static/src/components/RelinkDialog.tsx` (picker block at lines 251-283; `useDialogFocus` peel at lines 88-94)

**Interfaces:**
- Consumes: current `FolderPicker` props (`slug`, `unbound`, `contentMode`, `shell`, `modalTitle`, `modalSubtitle`, `initialPath`, `onSelect`, `onSelectFiles`, `onCancel`, `allowEmptyFolder`, `selectLabel`); `api.scanVideos(slug, sourceDir, autoAssignPrimary, linkMode)` and `api.scanFiles(slug, sourcePaths, autoAssignPrimary, linkMode)` returning `ScanResponse { registered: string[]; auto_assigned: Record<string, string>; skipped: string[] }`; `HostedUploadModal` (Task 5).
- Produces: Ingest owns `commitFolder(path: string)` / `commitFiles(files: { path: string; mtime: number | null }[])`; CreateMatch and RelinkDialog import `FolderPicker` directly. Task 7 rewrites exactly these three call sites.

- [ ] **Step 1: No new tests - transitional refactor**

Nothing new is testable that survives Task 7 (which replaces the picker API these call sites use and carries the real behavior tests). Pre-branch grep confirmed no existing vitest file references `AddFootageModal`, `QueueView`, `DirectoryPickerModal`, `autoCommitFiles`, or the mutex flags, so there are no obsolete tests to delete either. Gates for this task: `pnpm typecheck` + full `pnpm test`.

- [ ] **Step 2: Implement Ingest**

In `src/splitsmith/ui_static/src/pages/Ingest.tsx`:

1. Remove `import { AddFootageModal } from "@/components/AddFootageModal";` and add `import { FolderPicker } from "@/components/FolderPicker";`.
2. Add the commit helpers next to `afterImport` (anchor: `async function afterImport`):

```tsx
  // One source per pass: commit runs the scan immediately, no queue.
  // Errors land in the page-level banner for now; Task 7 moves them
  // inline into the picker footer.
  async function commitFolder(path: string) {
    setShowAddFootage(false);
    setBusy(true);
    try {
      const result = await api.scanVideos(slug, path, true, storage);
      await afterImport(result.registered.length, result.registered);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function commitFiles(files: { path: string; mtime: number | null }[]) {
    setShowAddFootage(false);
    setBusy(true);
    try {
      const result = await api.scanFiles(
        slug,
        files.map((f) => f.path),
        true,
        storage,
      );
      await afterImport(result.registered.length, result.registered);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }
```

3. Replace the local branch of the `showAddFootage` block (the `<AddFootageModal .../>` element from Task 5) with:

```tsx
            <FolderPicker
              slug={slug}
              shell="modal"
              modalTitle="Add footage"
              modalSubtitle={
                activeShooterName ? `Adding to ${activeShooterName}` : undefined
              }
              initialPath={lastScannedDir}
              onSelect={(path) => void commitFolder(path)}
              onSelectFiles={(files) => void commitFiles(files)}
              onCancel={() => setShowAddFootage(false)}
              selectLabel="Add this folder"
              allowEmptyFolder
            />
```

Note: `allowEmptyFolder` stays ON here - per the amended spec a whole-folder commit is valid even when the folder shows no top-level videos, because the backend scan walks recursively (an SD-card root whose clips sit under `DCIM/` is a legitimate pick). This preserves the old queue picker's `allowEmptyFolder` behavior.

- [ ] **Step 3: Implement CreateMatch and RelinkDialog; delete the two files**

`src/splitsmith/ui_static/src/pages/CreateMatch.tsx`: replace line 36 `import { DirectoryPickerModal } from "@/components/DirectoryPickerModal";` with `import { FolderPicker } from "@/components/FolderPicker";`, and replace the block at lines 908-917:

```tsx
      {pickerOpen && !hostedMode && (
        <FolderPicker
          unbound
          contentMode="directories"
          shell="modal"
          modalTitle="Pick a parent folder"
          modalSubtitle="The project folder will be created inside the directory you choose."
          initialPath={parentDir.startsWith("~") ? null : parentDir}
          onSelect={(picked) => {
            setParentDir(picked);
            setPickerOpen(false);
          }}
          onCancel={() => setPickerOpen(false)}
          selectLabel="Use this folder"
          allowEmptyFolder
        />
      )}
```

`src/splitsmith/ui_static/src/components/RelinkDialog.tsx`:

1. The dialog no longer hosts the picker inline, so the Escape peel is obsolete. Replace lines 88-94 with:

```tsx
  // Escape / focus trap / restore. The folder picker is a stacked
  // modal with its own useDialogFocus registration - the dialog stack
  // in dialogFocus.ts routes Escape to the topmost surface, so no
  // manual peeling is needed here.
  useDialogFocus(true, panelRef, onClose, { disableEscape: busy });
```

2. Replace the `{pickerOpen ? ( <div className="rounded-md border border-rule p-2"> ... ) : ( <div className="flex flex-wrap items-center gap-2"> ... )}` block (lines 251-283) so the button row always renders and the picker stacks as a modal:

```tsx
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setPickerOpen(true)}
              disabled={busy}
            >
              <FolderSearch className="size-4" />
              {scannedRoots.length === 0 ? "Pick search folder..." : "Add another folder..."}
            </Button>
            {scannedRoots.length > 0 ? (
              <span className="text-xs text-muted">
                Scanned: {scannedRoots.join(" · ")}
              </span>
            ) : null}
          </div>

          {pickerOpen ? (
            <FolderPicker
              slug={slug}
              shell="modal"
              contentMode="directories"
              modalTitle="Pick a search folder"
              modalSubtitle="Scanned recursively to find the moved originals."
              onSelect={(path) => {
                setPickerOpen(false);
                void runScan(path);
              }}
              onCancel={() => setPickerOpen(false)}
              allowEmptyFolder
              selectLabel="Scan this folder"
            />
          ) : null}
```

3. Delete the files:

```bash
cd /Users/mathias/work/splitsmith
git rm src/splitsmith/ui_static/src/components/AddFootageModal.tsx \
  src/splitsmith/ui_static/src/components/DirectoryPickerModal.tsx
```

- [ ] **Step 4: Run gates**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm typecheck && pnpm test && pnpm exec eslint src`
Expected: all green. Typecheck is the enforcement that nothing still imports the deleted modules (`grep -rn "AddFootageModal\|DirectoryPickerModal" src` must return nothing).

- [ ] **Step 5: Commit**

```bash
cd /Users/mathias/work/splitsmith
git add src/splitsmith/ui_static/src/pages/Ingest.tsx \
  src/splitsmith/ui_static/src/pages/CreateMatch.tsx \
  src/splitsmith/ui_static/src/components/RelinkDialog.tsx
git commit -m "refactor(ui): one-shot local add-footage, drop queue and picker facades"
```

(The `git rm` in Step 3 already staged the deletions; the `git add` above stages the three edits. Verify with `git status` that ONLY these five paths are staged before committing.)

---
### Task 7: FolderPicker rewrite - single-scroll dialog, Places sidebar, footer commit

**Files:**
- Modify (rewrite): `src/splitsmith/ui_static/src/components/FolderPicker.tsx` (916 lines at branch start; keep the helper functions listed below, replace everything else)
- Modify: `src/splitsmith/ui_static/src/pages/Ingest.tsx` (local picker block + commit helpers from Task 6)
- Modify: `src/splitsmith/ui_static/src/pages/CreateMatch.tsx` (picker block from Task 6)
- Modify: `src/splitsmith/ui_static/src/components/RelinkDialog.tsx` (picker block from Task 6; `runScan` now throws)
- Test (create): `src/splitsmith/ui_static/src/components/FolderPicker.test.tsx`
- Test (create): `src/splitsmith/ui_static/src/pages/Ingest.addFootage.test.tsx`

**Interfaces:**
- Consumes: `api.listFolder(slug, path?, { probe? })` / `api.listFolderUnbound(path?)` returning `FsListing { path; parent; entries: FsEntry[]; suggested_starts: SuggestedStart[] }`; `SuggestedStart { path; label; kind: "recent" | "home" | "removable" | "network" }`; `api.probeFile`; `ApiError` (all existing in `@/lib/api`); `Portal`, `useDialogFocus`, `Button`, `cn` (existing).
- Produces (the FINAL picker API - all three call sites updated in this same commit):

```ts
export interface FolderPickerCommitFile {
  path: string;
  mtime: number | null;
}

interface FolderPickerProps {
  /** Shooter slug for shooter-scoped fs endpoints. Required unless unbound. */
  slug?: string;
  /** Browse via /api/fs/list-dirs (no project bound; dirs only on the wire). */
  unbound?: boolean;
  /** "directories" hides video files entirely. Default "directories+files". */
  contentMode?: "directories" | "directories+files";
  /** Dialog title (header + aria-label). */
  title: string;
  /** Call-site subtitle under the title. */
  subtitle?: string;
  initialPath?: string | null;
  /** Highlight entries modified inside the match window (epoch seconds). */
  matchWindow?: { startEpoch: number; endEpoch: number } | null;
  /** Keep the folder commit enabled when the folder has no direct video
   *  children (callers whose commit walks recursively). Default false. */
  allowEmptyFolder?: boolean;
  /** Primary-action label when no files are checked. Default "Add this folder".
   *  With N files checked the label is always "Add N files". */
  folderLabel?: string;
  /** Render the storage toggle in the footer (add-footage call site only). */
  storage?: {
    value: "symlink" | "copy";
    onChange: (mode: "symlink" | "copy") => void;
  };
  /** Commit the current folder. Runs immediately; the footer shows inline
   *  progress. Resolving closes the dialog (via onClose); throwing keeps
   *  it open with the error inline. */
  onCommitFolder: (path: string) => Promise<void>;
  /** Commit the checked files. Omit to hide file checkboxes entirely. */
  onCommitFiles?: (files: FolderPickerCommitFile[]) => Promise<void>;
  onClose: () => void;
}
```

Deleted from the old API (no aliases, no fallbacks): `shell`, `mode`, `modalTitle`, `modalSubtitle`, `onSelect`, `onSelectFiles`, `autoCommitFiles`, `onFolderFilesChange`, `onCancel`, `selectLabel`, `onPathChange`, `addWholeFolderDisabled(+Reason)`, `filesDisabled(+Reason)`. The `PathBar` component is deleted (folded into the breadcrumb bar's edit mode).

- [ ] **Step 1: Write the failing tests**

Create `src/splitsmith/ui_static/src/components/FolderPicker.test.tsx`:

```tsx
/**
 * FolderPicker dialog (add-videos UX rework, spec 2026-08-08).
 *
 * Covers: whole-folder commit, N-files commit, commit error staying
 * open, selection reset on navigation, sidebar navigation (volume +
 * Computer), empty-folder rules (allowEmptyFolder on/off), and the
 * single-scroll-container layout contract.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FolderPicker } from "@/components/FolderPicker";
import { ApiError, api, type FsEntry, type FsListing } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listFolder: vi.fn(),
      listFolderUnbound: vi.fn(),
      probeFile: vi.fn().mockResolvedValue({
        duration: null,
        thumbnail_url: null,
        width: null,
        height: null,
        codec: null,
        size_bytes: null,
      }),
    },
  };
});

function entry(over: Partial<FsEntry> & { name: string; kind: FsEntry["kind"] }): FsEntry {
  return {
    video_count: null,
    size_bytes: null,
    mtime: null,
    duration: null,
    thumbnail_url: null,
    ...over,
  };
}

const moviesListing: FsListing = {
  path: "/Users/op/Movies",
  parent: "/Users/op",
  entries: [
    entry({ name: "match-day", kind: "dir", video_count: 3, mtime: 1754600000 }),
    entry({ name: "GH010001.MP4", kind: "video", size_bytes: 1024, mtime: 1754600100 }),
    entry({ name: "GH010002.MP4", kind: "video", size_bytes: 2048, mtime: 1754600200 }),
  ],
  suggested_starts: [
    { path: "/Users/op", label: "Home", kind: "home" },
    { path: "/Volumes/SDCARD", label: "SDCARD", kind: "removable" },
  ],
};

const matchDayListing: FsListing = {
  path: "/Users/op/Movies/match-day",
  parent: "/Users/op/Movies",
  entries: [entry({ name: "GH019999.MP4", kind: "video", mtime: 1754600300 })],
  suggested_starts: moviesListing.suggested_starts,
};

const dirsOnlyListing: FsListing = {
  path: "/Users/op/Empty",
  parent: "/Users/op",
  entries: [entry({ name: "sub", kind: "dir", video_count: 2 })],
  suggested_starts: moviesListing.suggested_starts,
};

function defaultProps() {
  return {
    slug: "alice",
    title: "Add footage",
    onCommitFolder: vi.fn<(path: string) => Promise<void>>().mockResolvedValue(undefined),
    onCommitFiles: vi
      .fn<(files: { path: string; mtime: number | null }[]) => Promise<void>>()
      .mockResolvedValue(undefined),
    onClose: vi.fn(),
  };
}

describe("FolderPicker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listFolder).mockResolvedValue(moviesListing);
    vi.mocked(api.listFolderUnbound).mockResolvedValue(dirsOnlyListing);
  });

  it("commits the whole folder and closes on success", async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<FolderPicker {...props} />);
    const button = await screen.findByRole("button", { name: /add this folder/i });
    await user.click(button);
    expect(props.onCommitFolder).toHaveBeenCalledWith("/Users/op/Movies");
    await waitFor(() => expect(props.onClose).toHaveBeenCalled());
  });

  it("commits N checked files with paths + mtimes", async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<FolderPicker {...props} />);
    await user.click(await screen.findByRole("checkbox", { name: /select GH010001/i }));
    await user.click(screen.getByRole("checkbox", { name: /select GH010002/i }));
    await user.click(screen.getByRole("button", { name: /add 2 files/i }));
    expect(props.onCommitFiles).toHaveBeenCalledWith([
      { path: "/Users/op/Movies/GH010001.MP4", mtime: 1754600100 },
      { path: "/Users/op/Movies/GH010002.MP4", mtime: 1754600200 },
    ]);
    await waitFor(() => expect(props.onClose).toHaveBeenCalled());
  });

  it("surfaces a commit error inline and stays open", async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.onCommitFolder.mockRejectedValue(new ApiError(400, "scan blew up"));
    render(<FolderPicker {...props} />);
    await user.click(await screen.findByRole("button", { name: /add this folder/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("scan blew up");
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("resets the file selection when navigating into a folder", async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<FolderPicker {...props} />);
    await user.click(await screen.findByRole("checkbox", { name: /select GH010001/i }));
    expect(screen.getByRole("button", { name: /add 1 file/i })).toBeInTheDocument();
    vi.mocked(api.listFolder).mockResolvedValue(matchDayListing);
    await user.click(screen.getByRole("button", { name: /match-day/i }));
    expect(
      await screen.findByRole("button", { name: /add this folder/i }),
    ).toBeInTheDocument();
  });

  it("navigates to a mounted volume from the Places sidebar", async () => {
    const user = userEvent.setup();
    render(<FolderPicker {...defaultProps()} />);
    await screen.findByRole("button", { name: /add this folder/i });
    await user.click(screen.getByRole("button", { name: "SDCARD" }));
    await waitFor(() =>
      expect(api.listFolder).toHaveBeenLastCalledWith(
        "alice",
        "/Volumes/SDCARD",
        expect.anything(),
      ),
    );
  });

  it("always offers a Computer entry that navigates to /", async () => {
    const user = userEvent.setup();
    render(<FolderPicker {...defaultProps()} />);
    await screen.findByRole("button", { name: /add this folder/i });
    await user.click(screen.getByRole("button", { name: "Computer" }));
    await waitFor(() =>
      expect(api.listFolder).toHaveBeenLastCalledWith("alice", "/", expect.anything()),
    );
  });

  it("disables the folder commit on a video-less folder unless allowEmptyFolder", async () => {
    vi.mocked(api.listFolderUnbound).mockResolvedValue(dirsOnlyListing);
    const props = defaultProps();
    const { unmount } = render(
      <FolderPicker
        {...props}
        slug={undefined}
        unbound
        contentMode="directories"
        onCommitFiles={undefined}
        title="Pick a parent folder"
        folderLabel="Use this folder"
      />,
    );
    expect(await screen.findByRole("button", { name: /use this folder/i })).toBeDisabled();
    unmount();

    render(
      <FolderPicker
        {...props}
        slug={undefined}
        unbound
        contentMode="directories"
        onCommitFiles={undefined}
        title="Pick a parent folder"
        folderLabel="Use this folder"
        allowEmptyFolder
      />,
    );
    expect(await screen.findByRole("button", { name: /use this folder/i })).toBeEnabled();
  });

  it("has exactly one scroll container (the listing) and no max-h-80 cap", async () => {
    const { baseElement } = render(<FolderPicker {...defaultProps()} />);
    await screen.findByRole("button", { name: /add this folder/i });
    expect(baseElement.querySelector(".max-h-80")).toBeNull();
    const listing = baseElement.querySelector("ul.overflow-y-auto");
    expect(listing).not.toBeNull();
    expect(listing!.className).toContain("flex-1");
    expect(listing!.className).toContain("min-h-0");
  });

  it("swaps the breadcrumb bar for a path input on the pencil affordance", async () => {
    const user = userEvent.setup();
    render(<FolderPicker {...defaultProps()} />);
    await screen.findByRole("button", { name: /add this folder/i });
    await user.click(screen.getByRole("button", { name: /edit path/i }));
    const input = screen.getByRole("textbox", { name: /folder path/i });
    await user.clear(input);
    await user.type(input, "/Volumes/SDCARD{Enter}");
    await waitFor(() =>
      expect(api.listFolder).toHaveBeenLastCalledWith(
        "alice",
        "/Volumes/SDCARD",
        expect.anything(),
      ),
    );
  });
});
```

Create `src/splitsmith/ui_static/src/pages/Ingest.addFootage.test.tsx`:

```tsx
/**
 * Ingest local add-footage flow through the rewritten FolderPicker:
 * open picker, commit, scan fires with the chosen storage mode, the
 * dialog closes, the project reloads. Also pins allowEmptyFolder ON
 * for this call site (spec: whole-folder commits stay valid when no
 * top-level videos show - the backend scan walks recursively).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import {
  api,
  type FsListing,
  type MatchProject,
  type ServerHealth,
} from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import { useUploads } from "@/lib/uploads";
import { Ingest } from "@/pages/Ingest";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getProject: vi.fn(),
      getHealth: vi.fn(),
      listMatchShooters: vi.fn(),
      getBeepQueue: vi.fn(),
      listFolder: vi.fn(),
      scanVideos: vi.fn(),
      scanFiles: vi.fn(),
      probeFile: vi.fn().mockResolvedValue({
        duration: null,
        thumbnail_url: null,
        width: null,
        height: null,
        codec: null,
        size_bytes: null,
      }),
    },
  };
});

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return {
    ...actual,
    useDeploymentMode: vi.fn(() => ({ mode: "local" as const, resolved: true })),
  };
});

vi.mock("@/lib/uploads", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/uploads")>();
  return { ...actual, useUploads: vi.fn() };
});

const emptyProject = {
  name: "Test Match",
  stages: [],
  unassigned_videos: [],
  last_scanned_dir: null,
} as unknown as MatchProject;

const health = {
  status: "ok",
  bound: true,
  project_name: "Test Match",
  project_root: "/tmp/test",
  match_id: "m1",
  kind: "match",
  default_shooter_slug: "alice",
  schema_version: 1,
} as unknown as ServerHealth;

const listing: FsListing = {
  path: "/Users/op/Movies",
  parent: "/Users/op",
  entries: [
    {
      name: "GH010001.MP4",
      kind: "video",
      video_count: null,
      size_bytes: 1024,
      mtime: 1754600100,
      duration: null,
      thumbnail_url: null,
    },
  ],
  suggested_starts: [],
};

const emptyListing: FsListing = { ...listing, entries: [] };

function renderIngest() {
  return render(
    <ConfirmProvider>
      <MemoryRouter initialEntries={["/match/m1/ingest/alice"]}>
        <Routes>
          <Route path="/match/:matchId/ingest/:slug" element={<Ingest />} />
        </Routes>
      </MemoryRouter>
    </ConfirmProvider>,
  );
}

describe("Ingest add-footage (local)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useDeploymentMode).mockReturnValue({ mode: "local", resolved: true });
    vi.mocked(useUploads).mockReturnValue({
      uploads: [],
      enqueue: vi.fn(),
      cancel: vi.fn(),
      cancelAll: vi.fn(),
      clearFinished: vi.fn(),
      inFlight: false,
      attachTick: 0,
      probeFor: vi.fn(),
      queue: {},
    } as unknown as ReturnType<typeof useUploads>);
    vi.mocked(api.getProject).mockResolvedValue(emptyProject);
    vi.mocked(api.getHealth).mockResolvedValue(health);
    vi.mocked(api.listMatchShooters).mockResolvedValue({ shooters: [] });
    vi.mocked(api.getBeepQueue).mockResolvedValue({ pending_count: 0 });
    vi.mocked(api.listFolder).mockResolvedValue(listing);
    vi.mocked(api.scanVideos).mockResolvedValue({
      registered: ["/Users/op/Movies/GH010001.MP4"],
      auto_assigned: {},
      skipped: [],
    });
  });

  it("commits a folder with the picked storage mode and closes", async () => {
    const user = userEvent.setup();
    renderIngest();
    await user.click(await screen.findByRole("button", { name: /pick a folder/i }));
    const dialog = await screen.findByRole("dialog", { name: /add footage/i });
    expect(dialog).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /copy into project/i }));
    await user.click(screen.getByRole("button", { name: /add this folder/i }));
    await waitFor(() =>
      expect(api.scanVideos).toHaveBeenCalledWith(
        "alice",
        "/Users/op/Movies",
        true,
        "copy",
      ),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: /add footage/i })).not.toBeInTheDocument(),
    );
    // Reloaded after import: initial load + afterImport reload.
    expect(vi.mocked(api.getProject).mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("keeps the folder commit enabled when the folder shows no direct videos (allowEmptyFolder on)", async () => {
    vi.mocked(api.listFolder).mockResolvedValue(emptyListing);
    const user = userEvent.setup();
    renderIngest();
    await user.click(await screen.findByRole("button", { name: /pick a folder/i }));
    await screen.findByRole("dialog", { name: /add footage/i });
    const commit = await screen.findByRole("button", { name: /add this folder/i });
    expect(commit).toBeEnabled();
    await user.click(commit);
    await waitFor(() =>
      expect(api.scanVideos).toHaveBeenCalledWith(
        "alice",
        "/Users/op/Movies",
        true,
        "symlink",
      ),
    );
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm test src/components/FolderPicker.test.tsx src/pages/Ingest.addFootage.test.tsx`
Expected: FAIL - `title` / `onCommitFolder` / `onClose` props do not exist yet (TS-level and runtime: the picker renders nothing recognizable, `Add this folder` default label missing, no `alert` role, no Computer entry, `.max-h-80` still present, no storage radio inside the dialog).

- [ ] **Step 3: Rewrite FolderPicker.tsx**

KEEP these existing helpers verbatim (they are already in the file): `SortMode`, `sortEntries`, `SortHeader`, `isInMatchWindow`, `formatMtime`, `formatDuration`, `formatBytes`, `buildBreadcrumb`, `joinPath`, `ThumbnailFloat`. DELETE: `PathBar`, `SuggestedStartsSidebar`, the old `SidebarIcon`, the old props interface, and the whole old component body. `VideoRowMulti` keeps its current body but its props lose `disabledReason` (delete the prop, its doc comment, and the three `disabledReason` usages inside - the `cn(...)` disabled styling branch and the `title` ternary collapse to the `inMatchWindow` cases only).

New file top (imports + props):

```tsx
/**
 * FolderPicker - the one modal picker dialog for choosing a server-side
 * folder (or files within it). Used by the Ingest add-footage flow,
 * CreateMatch's parent-folder picker, and RelinkDialog.
 *
 * Shape (spec 2026-08-08): fixed-height dialog, three fixed regions,
 * exactly ONE scroll container (the listing). Header carries title +
 * breadcrumb bar (pencil or "/" swaps in an editable path input).
 * Left: permanent Places sidebar (Recent / Home / Places incl. every
 * mounted volume and a static Computer -> "/" entry). Right: the
 * listing. Footer: selection summary, optional storage toggle, Cancel,
 * and ONE primary action that commits immediately with inline progress;
 * the dialog closes on success and stays open on failure with the
 * server detail inline.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDownAZ,
  ArrowDownNarrowWide,
  ArrowUpNarrowWide,
  ChevronRight,
  Clock,
  Cloud,
  Film,
  Folder,
  FolderOpen,
  HardDrive,
  Home,
  Loader2,
  Monitor,
  Pencil,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Portal } from "@/components/ui/Portal";
import { useDialogFocus } from "@/lib/dialogFocus";
import {
  ApiError,
  api,
  type FsEntry,
  type FsListing,
  type SuggestedStart,
} from "@/lib/api";
import { cn } from "@/lib/utils";

export interface FolderPickerCommitFile {
  path: string;
  mtime: number | null;
}

interface FolderPickerProps {
  /** Shooter slug for shooter-scoped fs endpoints. Required unless
   *  ``unbound`` is true. */
  slug?: string;
  /** Browse via /api/fs/list-dirs (no project bound; dirs only). */
  unbound?: boolean;
  /** ``directories`` hides video files entirely - the caller is picking
   *  a parent dir, not files within it. */
  contentMode?: "directories" | "directories+files";
  /** Dialog title (header + aria-label). */
  title: string;
  /** Call-site subtitle under the title. */
  subtitle?: string;
  initialPath?: string | null;
  /** Highlight entries modified inside the match window (epoch secs). */
  matchWindow?: { startEpoch: number; endEpoch: number } | null;
  /** Keep the folder commit enabled when the folder has no direct
   *  video children (callers whose commit walks recursively). */
  allowEmptyFolder?: boolean;
  /** Primary label when no files are checked. Default "Add this
   *  folder"; with N files checked the label is always "Add N files". */
  folderLabel?: string;
  /** Render the storage toggle in the footer (add-footage only). */
  storage?: {
    value: "symlink" | "copy";
    onChange: (mode: "symlink" | "copy") => void;
  };
  /** Commit the current folder. Resolving closes the dialog; throwing
   *  keeps it open with the error rendered inline in the footer. */
  onCommitFolder: (path: string) => Promise<void>;
  /** Commit the checked files. Omit to hide file checkboxes. */
  onCommitFiles?: (files: FolderPickerCommitFile[]) => Promise<void>;
  onClose: () => void;
}

type CommitState =
  | { phase: "idle" }
  | { phase: "running"; label: string }
  | { phase: "error"; message: string };
```

New component body:

```tsx
export function FolderPicker({
  slug,
  unbound = false,
  contentMode = "directories+files",
  title,
  subtitle,
  initialPath,
  matchWindow = null,
  allowEmptyFolder = false,
  folderLabel = "Add this folder",
  storage,
  onCommitFolder,
  onCommitFiles,
  onClose,
}: FolderPickerProps) {
  const [listing, setListing] = useState<FsListing | null>(null);
  const [path, setPath] = useState<string | null>(initialPath ?? null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [sortMode, setSortMode] = useState<SortMode>("name");
  const [editingPath, setEditingPath] = useState(false);
  const [commit, setCommit] = useState<CommitState>({ phase: "idle" });
  const committing = commit.phase === "running";

  // ``directories``-mode and unbound pickers skip metadata probing -
  // no video rows means the duration/thumbnail sidecars are wasted.
  const wantMetadata =
    !unbound && contentMode === "directories+files" && onCommitFiles !== undefined;

  const load = useCallback(
    async (next?: string | null) => {
      setBusy(true);
      setError(null);
      try {
        const data = unbound
          ? await api.listFolderUnbound(next ?? undefined)
          : await api.listFolder(slug!, next ?? undefined, { probe: wantMetadata });
        setListing(data);
        setPath(data.path);
        // Selection resets on navigation (existing behavior, kept).
        setSelectedFiles(new Set());
        // A stale commit error belongs to the folder it happened in.
        setCommit({ phase: "idle" });
      } catch (e) {
        setError(e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [slug, unbound, wantMetadata],
  );

  useEffect(() => {
    void load(initialPath ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dirEntries = useMemo(
    () => sortEntries(listing?.entries.filter((e) => e.kind === "dir") ?? [], sortMode),
    [listing, sortMode],
  );
  const videoEntries = useMemo(
    () =>
      contentMode === "directories"
        ? []
        : sortEntries(listing?.entries.filter((e) => e.kind === "video") ?? [], sortMode),
    [listing, sortMode, contentMode],
  );
  const videosHere = videoEntries.length;
  const multiFileMode = contentMode !== "directories" && onCommitFiles !== undefined;
  const selectedCount = selectedFiles.size;

  const toggleSelect = (name: string) => {
    setSelectedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const selectAll = () => {
    setSelectedFiles(new Set(videoEntries.map((e) => e.name)));
  };

  const selectInMatchWindow = () => {
    setSelectedFiles(
      new Set(
        videoEntries
          .filter((e) => isInMatchWindow(e.mtime, matchWindow))
          .map((e) => e.name),
      ),
    );
  };

  const inWindowVideoCount = matchWindow
    ? videoEntries.filter((e) => isInMatchWindow(e.mtime, matchWindow)).length
    : 0;

  const runCommit = async (label: string, fn: () => Promise<void>) => {
    setCommit({ phase: "running", label });
    try {
      await fn();
      onClose();
    } catch (e) {
      setCommit({
        phase: "error",
        message:
          e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e),
      });
    }
  };

  const handleCommit = async () => {
    if (!path || committing) return;
    if (selectedCount > 0 && onCommitFiles) {
      const files = videoEntries
        .filter((e) => selectedFiles.has(e.name))
        .map((e) => ({ path: joinPath(path, e.name), mtime: e.mtime }));
      await runCommit(
        `Adding ${files.length} file${files.length === 1 ? "" : "s"}...`,
        () => onCommitFiles(files),
      );
    } else {
      await runCommit("Adding folder...", () => onCommitFolder(path));
    }
  };

  const primaryDisabled =
    busy ||
    committing ||
    !path ||
    (selectedCount === 0 && !allowEmptyFolder && videosHere === 0);

  const panelRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus(
    true,
    panelRef,
    () => {
      if (committing) return; // a stray Escape must not abandon a running scan
      onClose();
    },
    { disableEscape: committing },
  );

  return (
    <Portal>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="fixed inset-0 z-modal flex items-center justify-center bg-bg/70 p-4 backdrop-blur-sm"
        onClick={committing ? undefined : onClose}
      >
        <div
          ref={panelRef}
          className="relative flex h-[min(680px,90vh)] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-rule-strong bg-surface text-ink shadow-[0_24px_48px_-12px_rgba(0,0,0,0.7)]"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            // "/" jumps to the editable path input (rare manual case).
            if (e.key !== "/" || editingPath) return;
            const t = e.target as HTMLElement;
            if (t.tagName === "INPUT" || t.tagName === "TEXTAREA") return;
            e.preventDefault();
            setEditingPath(true);
          }}
        >
          <header className="shrink-0 border-b border-rule">
            <div className="flex items-center justify-between gap-4 px-5 py-3.5">
              <div>
                <h2 className="font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
                  {title}
                </h2>
                {subtitle && (
                  <p className="mt-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
                    {subtitle}
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                disabled={committing}
                className="rounded-md p-1.5 text-subtle hover:bg-surface-2 hover:text-ink disabled:opacity-50"
              >
                <X className="size-4" />
              </button>
            </div>
            <BreadcrumbBar
              path={path}
              busy={busy || committing}
              editing={editingPath}
              onNavigate={(p) => void load(p)}
              onEditStart={() => setEditingPath(true)}
              onEditEnd={() => setEditingPath(false)}
            />
          </header>

          <div className="flex min-h-0 flex-1">
            <aside className="w-[200px] shrink-0 overflow-y-auto border-r border-rule px-3 py-3">
              <PlacesSidebar
                starts={listing?.suggested_starts ?? []}
                currentPath={path}
                disabled={busy || committing}
                onPick={(p) => void load(p)}
              />
            </aside>

            <div className="relative flex min-h-0 flex-1 flex-col">
              {busy && listing ? (
                <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-bg/70 backdrop-blur-[1px]">
                  <Loader2 className="size-5 animate-spin text-muted" />
                </div>
              ) : null}
              {busy && !listing ? (
                <div className="flex h-full items-center justify-center gap-2 p-6 text-sm text-muted">
                  <Loader2 className="size-4 animate-spin" />
                  <span>Reading folder...</span>
                </div>
              ) : error ? (
                <div className="p-4 text-sm">
                  <p className="text-destructive">{error}</p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="mt-2"
                    onClick={() => void load(path)}
                  >
                    Retry
                  </Button>
                </div>
              ) : !listing ? null : dirEntries.length === 0 && videoEntries.length === 0 ? (
                <div className="p-4 text-sm text-muted">Empty folder.</div>
              ) : (
                <>
                  <SortHeader mode={sortMode} onChange={setSortMode} />
                  <ul className="min-h-0 flex-1 divide-y divide-rule overflow-y-auto">
                    {dirEntries.map((entry) => {
                      const childPath = path ? joinPath(path, entry.name) : entry.name;
                      const inWindow = isInMatchWindow(entry.mtime, matchWindow);
                      return (
                        <li key={`d-${entry.name}`}>
                          <button
                            type="button"
                            className={cn(
                              "flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-surface-3 hover:text-ink",
                              inWindow &&
                                "border-l-2 border-l-status-info bg-status-info/5",
                            )}
                            onClick={() => void load(childPath)}
                            disabled={busy || committing}
                            title={inWindow ? "Modified during the match window" : undefined}
                          >
                            <span className="flex min-w-0 items-center gap-2">
                              <Folder className="size-4 shrink-0 text-muted" />
                              <span className="truncate">{entry.name}</span>
                            </span>
                            {entry.video_count ? (
                              <span className="flex items-center gap-1 text-xs text-muted">
                                <Film className="size-3" />
                                {entry.video_count}
                              </span>
                            ) : null}
                          </button>
                        </li>
                      );
                    })}
                    {multiFileMode
                      ? videoEntries.map((entry) => {
                          const checked = selectedFiles.has(entry.name);
                          const fullPath = path ? joinPath(path, entry.name) : entry.name;
                          return (
                            <VideoRowMulti
                              key={`v-${entry.name}`}
                              slug={slug!}
                              entry={entry}
                              fullPath={fullPath}
                              checked={checked}
                              busy={busy || committing}
                              inMatchWindow={isInMatchWindow(entry.mtime, matchWindow)}
                              onToggle={() => toggleSelect(entry.name)}
                              onProbed={(duration, thumbnail_url) => {
                                setListing((prev) =>
                                  prev
                                    ? {
                                        ...prev,
                                        entries: prev.entries.map((e) =>
                                          e.name === entry.name && e.kind === "video"
                                            ? { ...e, duration, thumbnail_url }
                                            : e,
                                        ),
                                      }
                                    : prev,
                                );
                              }}
                            />
                          );
                        })
                      : null}
                  </ul>
                </>
              )}
            </div>
          </div>

          <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-rule bg-surface-2 px-5 py-3">
            <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-muted">
              {commit.phase === "error" ? (
                <span role="alert" className="text-led">
                  {commit.message}
                </span>
              ) : (
                <>
                  {selectedCount > 0 ? (
                    <span>
                      {selectedCount} file{selectedCount === 1 ? "" : "s"} selected
                    </span>
                  ) : videosHere > 0 ? (
                    <span className="inline-flex items-center gap-1">
                      <Film className="size-3" />
                      {videosHere} video{videosHere === 1 ? "" : "s"} in this folder
                    </span>
                  ) : allowEmptyFolder ? (
                    <span>No videos directly here - subfolders will be scanned.</span>
                  ) : (
                    <span>No videos directly here. Drill into a subfolder.</span>
                  )}
                  {multiFileMode && videosHere > 0 ? (
                    <button
                      type="button"
                      className="rounded px-1.5 py-0.5 underline-offset-2 hover:underline disabled:opacity-50"
                      onClick={
                        selectedCount === videosHere
                          ? () => setSelectedFiles(new Set())
                          : selectAll
                      }
                      disabled={busy || committing}
                    >
                      {selectedCount === videosHere ? "Clear selection" : "Select all"}
                    </button>
                  ) : null}
                  {multiFileMode && inWindowVideoCount > 0 ? (
                    <button
                      type="button"
                      className="rounded px-1.5 py-0.5 text-status-info underline-offset-2 hover:underline disabled:opacity-50"
                      onClick={selectInMatchWindow}
                      disabled={busy || committing}
                      title="Select videos whose modified time falls inside the match window"
                    >
                      Select {inWindowVideoCount} in match window
                    </button>
                  ) : null}
                </>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {storage ? (
                <StorageToggle
                  value={storage.value}
                  onChange={storage.onChange}
                  disabled={committing}
                />
              ) : null}
              <Button variant="ghost" type="button" onClick={onClose} disabled={committing}>
                Cancel
              </Button>
              <Button
                type="button"
                disabled={primaryDisabled}
                onClick={() => void handleCommit()}
                title={
                  !allowEmptyFolder && selectedCount === 0 && videosHere === 0
                    ? "Select a folder that contains video files, or drill in."
                    : path
                      ? `Use ${path}`
                      : undefined
                }
              >
                {committing ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <FolderOpen />
                )}
                {committing
                  ? commit.label
                  : selectedCount > 0
                    ? `Add ${selectedCount} file${selectedCount === 1 ? "" : "s"}`
                    : folderLabel}
              </Button>
            </div>
          </footer>
        </div>
      </div>
    </Portal>
  );
}
```

New subcomponents (placed between the main component and the kept helpers):

```tsx
function BreadcrumbBar({
  path,
  busy,
  editing,
  onNavigate,
  onEditStart,
  onEditEnd,
}: {
  path: string | null;
  busy: boolean;
  editing: boolean;
  onNavigate: (p: string) => void;
  onEditStart: () => void;
  onEditEnd: () => void;
}) {
  const [draft, setDraft] = useState(path ?? "");
  useEffect(() => {
    setDraft(path ?? "");
  }, [path, editing]);
  const crumbs = buildBreadcrumb(path);

  if (editing) {
    return (
      <form
        className="flex items-center gap-2 px-5 py-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (draft.trim()) onNavigate(draft.trim());
          onEditEnd();
        }}
      >
        <input
          autoFocus
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              // Peel edit mode only - the dialog's own Escape stays
              // one level up.
              e.stopPropagation();
              onEditEnd();
            }
          }}
          className="h-8 flex-1 rounded-md border border-rule bg-bg px-3 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
          placeholder="/path/to/folder"
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          aria-label="Folder path"
        />
        <Button type="submit" variant="outline" size="sm" disabled={busy || !draft.trim()}>
          Go
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onEditEnd}>
          Cancel
        </Button>
      </form>
    );
  }

  return (
    <div className="flex items-center gap-1 px-5 py-2 text-sm text-muted">
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
        {crumbs.map((seg, i) => (
          <span key={`${seg.path}-${i}`} className="flex items-center gap-1">
            {i > 0 ? <ChevronRight className="size-3 shrink-0" /> : null}
            <button
              type="button"
              className="rounded px-1.5 py-0.5 font-mono text-xs hover:bg-surface-3 hover:text-ink"
              onClick={() => onNavigate(seg.path)}
              disabled={busy}
            >
              {seg.label}
            </button>
          </span>
        ))}
      </div>
      <button
        type="button"
        onClick={onEditStart}
        aria-label="Edit path"
        title='Type a path (or press "/")'
        className="rounded-md p-1.5 text-subtle hover:bg-surface-2 hover:text-ink"
      >
        <Pencil className="size-3.5" />
      </button>
    </div>
  );
}

type PlaceEntry = {
  path: string;
  label: string;
  kind: SuggestedStart["kind"] | "computer";
};

/** Permanent Places sidebar. Groups: Recent (last scanned), Home
 *  (~ and friends), Places (every removable/network mount from the
 *  server's _discover_mounts PLUS a static Computer -> "/" entry so
 *  any location is reachable by clicking, never by typing). */
function PlacesSidebar({
  starts,
  currentPath,
  disabled,
  onPick,
}: {
  starts: SuggestedStart[];
  currentPath: string | null;
  disabled: boolean;
  onPick: (path: string) => void;
}) {
  const groups: { title: string; items: PlaceEntry[] }[] = [
    { title: "Recent", items: starts.filter((s) => s.kind === "recent") },
    { title: "Home", items: starts.filter((s) => s.kind === "home") },
    {
      title: "Places",
      items: [
        ...starts.filter((s) => s.kind === "removable" || s.kind === "network"),
        { path: "/", label: "Computer", kind: "computer" },
      ],
    },
  ];
  return (
    <nav aria-label="Places" className="flex flex-col gap-3 text-sm">
      {groups.map((g) =>
        g.items.length === 0 ? null : (
          <div key={g.title} className="space-y-1">
            <div className="px-1 text-[10px] font-medium uppercase tracking-wider text-muted/70">
              {g.title}
            </div>
            {g.items.map((s) => (
              <button
                key={s.path}
                type="button"
                aria-current={currentPath === s.path ? "true" : undefined}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-surface-3 hover:text-ink",
                  currentPath === s.path && "bg-surface-3 text-ink",
                )}
                onClick={() => onPick(s.path)}
                disabled={disabled}
                title={s.path}
              >
                <SidebarIcon kind={s.kind} />
                <span className="truncate text-xs">{s.label}</span>
              </button>
            ))}
          </div>
        ),
      )}
    </nav>
  );
}

function SidebarIcon({ kind }: { kind: PlaceEntry["kind"] }) {
  const className = "size-3.5 shrink-0";
  if (kind === "recent") return <Clock className={className} />;
  if (kind === "removable") return <HardDrive className={className} />;
  if (kind === "network") return <Cloud className={className} />;
  if (kind === "computer") return <Monitor className={className} />;
  return <Home className={className} />;
}

/** Symlink-vs-copy storage choice, rendered in the footer for the
 *  add-footage call site only. Styling matches the old AddFootageModal
 *  StorageTab pair. */
function StorageToggle({
  value,
  onChange,
  disabled,
}: {
  value: "symlink" | "copy";
  onChange: (mode: "symlink" | "copy") => void;
  disabled: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="Storage mode"
      className="inline-flex rounded-full border border-rule bg-surface p-0.5"
    >
      {(
        [
          ["symlink", "Reference in place"],
          ["copy", "Copy into project"],
        ] as const
      ).map(([mode, label]) => (
        <button
          key={mode}
          type="button"
          role="radio"
          aria-checked={value === mode}
          disabled={disabled}
          onClick={() => onChange(mode)}
          className={cn(
            "inline-flex items-center rounded-full px-3 py-1 font-display text-[0.625rem] font-bold uppercase tracking-[0.08em] transition-colors",
            value === mode
              ? "bg-led-tint text-led-text shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--color-led)_55%,transparent)]"
              : "text-muted hover:text-ink-2",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Update the three call sites to the new API**

`src/splitsmith/ui_static/src/pages/Ingest.tsx` - replace the Task 6 `commitFolder`/`commitFiles` with throwing versions (the picker now owns progress + inline errors; a zero-registered result also stays open with an explanation, so the dialog never closes silently), and add the type import:

```tsx
import { FolderPicker, type FolderPickerCommitFile } from "@/components/FolderPicker";
```

```tsx
  // Commit runs the scan immediately; the picker footer shows progress.
  // Throwing keeps the dialog open with the message inline - including
  // the "success but nothing imported" case, which must not look like a
  // silent no-op (the page behind is unchanged when 0 register).
  async function commitFolder(path: string): Promise<void> {
    const result = await api.scanVideos(slug, path, true, storage);
    await afterImport(result.registered.length, result.registered);
    if (result.registered.length === 0) {
      throw new Error(
        result.skipped.length > 0
          ? `No new videos - ${result.skipped.length} skipped (already imported or unsupported)`
          : "No video files found in this folder",
      );
    }
  }

  async function commitFiles(files: FolderPickerCommitFile[]): Promise<void> {
    const result = await api.scanFiles(
      slug,
      files.map((f) => f.path),
      true,
      storage,
    );
    await afterImport(result.registered.length, result.registered);
    if (result.registered.length === 0) {
      throw new Error("Nothing imported - the selected files were skipped");
    }
  }
```

and replace the local picker element (from Task 6) with:

```tsx
            <FolderPicker
              slug={slug}
              title="Add footage"
              subtitle={
                activeShooterName ? `Adding to ${activeShooterName}` : undefined
              }
              initialPath={lastScannedDir}
              allowEmptyFolder
              folderLabel="Add this folder"
              storage={{ value: storage, onChange: setStorage }}
              onCommitFolder={commitFolder}
              onCommitFiles={commitFiles}
              onClose={() => setShowAddFootage(false)}
            />
```

(the Task 6 `setBusy`/`setError`/`setShowAddFootage` wrapping disappears; `busy` state keeps its other users).

`src/splitsmith/ui_static/src/pages/CreateMatch.tsx` - the Task 6 block becomes:

```tsx
      {pickerOpen && !hostedMode && (
        <FolderPicker
          unbound
          contentMode="directories"
          title="Pick a parent folder"
          subtitle="The project folder will be created inside the directory you choose."
          initialPath={parentDir.startsWith("~") ? null : parentDir}
          allowEmptyFolder
          folderLabel="Use this folder"
          onCommitFolder={(picked) => {
            setParentDir(picked);
            return Promise.resolve();
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}
```

`src/splitsmith/ui_static/src/components/RelinkDialog.tsx` - `runScan` must throw so the picker can render the failure inline (it is now the only caller). Replace its `catch` block (anchor `} catch (e) {` inside `runScan`) and keep `finally { setBusy(false); }`:

```tsx
  // Throws on failure - the FolderPicker commit surface renders the
  // error inline and stays open. Dialog-level ``error`` is reserved
  // for applyAll.
  const runScan = async (root: string) => {
    setBusy(true);
    setError(null);
    try {
      const resp = await api.relinkScan(slug, root);
      setRows((prev) =>
        prev.map((row) => {
          const found = resp.entries.find((e) => e.video_id === row.link.video_id);
          if (!found) return row;
          const picked =
            row.picked ?? (found.chosen_path && !found.ambiguous ? found.chosen_path : null);
          return { ...row, scan: found, picked };
        }),
      );
      setSearchRoot(resp.search_root);
      setScannedRoots((prev) =>
        prev.includes(resp.search_root) ? prev : [...prev, resp.search_root],
      );
    } finally {
      setBusy(false);
    }
  };
```

and the Task 6 picker element becomes:

```tsx
          {pickerOpen ? (
            <FolderPicker
              slug={slug}
              contentMode="directories"
              title="Pick a search folder"
              subtitle="Scanned recursively to find the moved originals."
              allowEmptyFolder
              folderLabel="Scan this folder"
              onCommitFolder={(path) => runScan(path)}
              onClose={() => setPickerOpen(false)}
            />
          ) : null}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm test src/components/FolderPicker.test.tsx src/pages/Ingest.addFootage.test.tsx && pnpm typecheck && pnpm test`
Expected: all PASS; typecheck clean (it enforces that no call site still passes a deleted prop).

- [ ] **Step 6: Commit**

```bash
cd /Users/mathias/work/splitsmith
git add src/splitsmith/ui_static/src/components/FolderPicker.tsx \
  src/splitsmith/ui_static/src/components/FolderPicker.test.tsx \
  src/splitsmith/ui_static/src/pages/Ingest.tsx \
  src/splitsmith/ui_static/src/pages/Ingest.addFootage.test.tsx \
  src/splitsmith/ui_static/src/pages/CreateMatch.tsx \
  src/splitsmith/ui_static/src/components/RelinkDialog.tsx
git commit -m "feat(ui): rewrite FolderPicker as a single-scroll picker dialog"
```

---
### Task 8: Removed-symbol sweep and copy/token audit

**Files:**
- Modify: only if the sweep finds stragglers (fix in place); otherwise no diff and no commit.

**Interfaces:**
- Consumes: the finished state of Tasks 1-7.
- Produces: certainty that no parallel legacy path survived (repo rule: redesigns delete, they don't shim).

- [ ] **Step 1: Sweep for deleted symbols and props**

```bash
cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static
grep -rn "AddFootageModal\|DirectoryPickerModal\|QueueView\|autoCommitFiles\|onFolderFilesChange\|pickerFolderAlreadyWhole\|pickerFolderHasFileChecks\|addWholeFolderDisabled\|filesDisabled\|HostedUploadSurface\|HostedUploadBody" src
grep -rn 'shell="inline"\|shell="modal"\|mode="inline"\|mode="compact"\|selectLabel\|modalTitle\|modalSubtitle\|onPathChange' src/components src/pages
grep -rn "max-h-80" src/components/FolderPicker.tsx
```

Expected: every command returns nothing. Any hit is a straggler - delete it (do not alias or re-export).

- [ ] **Step 2: Confirm the obsolete-test situation**

```bash
grep -rln "AddFootageModal\|DirectoryPickerModal\|QueueView\|autoCommitFiles" src --include='*.test.*'
```

Expected: nothing. (Verified at plan time: no vitest file ever covered the queue, the mutex, the inline shell, or the facade - so "delete obsolete tests with the behavior" is satisfied vacuously. If the implementation added any test that now targets removed behavior, delete that test here.)

- [ ] **Step 3: Copy and token audit on the branch diff**

```bash
cd /Users/mathias/work/splitsmith
git diff main...HEAD -- src/splitsmith/ui_static | grep '^+' | grep -nE '\-\-|—' | grep -v '^\+\+\+' | grep -viE 'eslint-disable|^\+.*//.*``|--dist|--color|var\(--|data-|aria-|z-\[|\-\->' || echo CLEAN
git diff main...HEAD -- src/splitsmith/ui_static | grep '^+' | grep -oE 'var\(--[a-z0-9-]+\)' | sort -u
```

First command: added prose/copy lines must not contain "--" or an em dash (the exclusions allow CSS custom properties and CLI flags); fix any hit by rewording with a single "-". Second command: for every emitted token name, confirm it exists in `src/splitsmith/ui_static/src/styles/index.css` (`grep -n -- '--color-led-glow' src/splitsmith/ui_static/src/styles/index.css` etc.). The tokens this plan uses (`--color-led`, `--color-led-fill`, `--color-led-glow`, `--color-done-glow`, `--color-bg-glow`, `--color-bg`, `--color-surface`, `--color-surface-2`, `--color-rule-strong`) all exist today; verify nothing new crept in.

- [ ] **Step 4: Run the suite**

Run: `cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static && pnpm typecheck && pnpm test`
Expected: green.

- [ ] **Step 5: Commit (only if Step 1-3 produced fixes)**

```bash
cd /Users/mathias/work/splitsmith
git add <each fixed file, enumerated>
git commit -m "refactor(ui): sweep stragglers from the add-videos rework"
```

---

### Task 9: Full local gate and layout screenshot verification

**Files:**
- None (verification only; fixes go through the normal edit + enumerated-add + commit loop).

**Interfaces:**
- Consumes: the completed branch.
- Produces: evidence the branch is PR-ready (gates green, dialog layout visually confirmed).

- [ ] **Step 1: Run the full frontend gate**

```bash
cd /Users/mathias/work/splitsmith/src/splitsmith/ui_static
pnpm typecheck
pnpm test
pnpm exec eslint src
```

Expected: all three exit 0. No Python was touched by this plan (`git diff main...HEAD --name-only | grep -v ui_static` should list nothing outside `docs/`); if that grep surprises you with a `.py` file, also run repo-root `uv run ruff check . && uv run black --check . && uv run pytest` before the PR.

- [ ] **Step 2: Screenshot verification of the dialog layout**

Playwright MCP `browser_navigate` hangs on this SPA (live SSE) - use a bounded headless capture with `domcontentloaded` instead. Start the local UI against a real match (any project with a shooter), note the port. Install playwright in the SCRATCHPAD directory only (`cd <scratchpad> && pnpm add playwright && pnpm exec playwright install chromium`) - it lives outside the repo, so the no-new-deps rule is untouched. Then:

```js
// shot.mjs - run: node shot.mjs "http://127.0.0.1:5173/match/<matchId>/ingest/<slug>" out.png
import { chromium } from "playwright";
const [url, out] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(url, { waitUntil: "domcontentloaded", timeout: 15000 });
await page.waitForTimeout(1500);
// Open the picker before the second shot: click "Pick a folder".
await page.getByRole("button", { name: /pick a folder/i }).click().catch(() => {});
await page.waitForTimeout(1200);
await page.screenshot({ path: out, fullPage: false });
await browser.close();
```

Routes are singular: the match overview is `/match/:matchId`, ingest is `/match/:matchId/ingest/:slug`. READ the screenshots (do not just confirm the file exists - a fix can be real and still invisible) and verify:
- the dialog is `h-[min(680px,90vh)]` with header, sidebar, and footer all visible;
- exactly ONE scrollbar exists, on the listing (scroll the list; the dialog body and page behind must not move);
- the Places sidebar shows Recent / Home / Places with a Computer entry, permanently visible (not collapsed into the grid);
- the footer carries the storage toggle, Cancel, and one primary action;
- the empty Ingest page shows the "Add footage" card with NO dashed border, corner brackets, or drop copy.

- [ ] **Step 3: Wrap up**

Do not open a PR or merge as part of this plan - report the branch state (commits, gate output, screenshot findings) back to the operator. Any defect found in Step 2 is fixed with a normal `fix(ui):` commit plus, where testable, a regression test that fails against the pre-fix code.

---

## Spec coverage map (self-check)

| Spec requirement | Task |
| --- | --- |
| `useDeploymentMode` resolved/loading state | 1 |
| Neutral skeleton until mode resolves | 4 (skeleton), 5-7 (modal render gated on `modeResolved`) |
| App-wide dragover/drop guard, no SPA navigation | 3 |
| Local drop toast "use Pick a folder" (existing toast pattern = Audit SaveToast) | 3 |
| Local empty state: Add footage card + Pick a folder; decorative DropZone deleted | 4 |
| Hosted: full-page drop target, depth counter, overlay, enqueue via useUploads; card offers Browse files | 4 |
| HostedUploadBody naive isDragging replaced by counter util | 2 (util), 5 (usage) |
| Picker modal-only shell, h-[min(680px,90vh)] max-w-3xl, one scroll container, max-h-80 dies | 7 |
| Keeps useDialogFocus + Portal | 7 |
| Header title/subtitle/close; breadcrumb bar with pencil / "/" path edit | 7 |
| Permanent Places sidebar (own overflow): Recent / Home / Places = removable+network mounts + Computer "/" | 7 |
| Footer: selection summary + storage toggle (add-footage only) + Cancel + one primary ("Add this folder" / "Add N files", label overridable) | 7 |
| Commit runs scan immediately, inline progress, closes on success, inline server error stays open | 7 |
| Selection resets on navigation (preserved) | 7 (code + test) |
| Listing errors inline with retry | 7 |
| Drag overlay announced via visually-hidden live region; row states not color-only (border + background kept) | 4, 7 |
| Delete inline shell, mode="inline", QueueView/queue/QueueItem, two-phase Import, autoCommitFiles + onFolderFilesChange, mutex flags, DirectoryPickerModal, decorative DropZone | 4 (DropZone), 5-6 (queue/hosted split/facade), 7 (inline shell + props), 8 (sweep) |
| AddFootageModal fate | Deleted (Task 6); hosted half lives on as HostedUploadModal (Task 5) - decided from the code, see Task 6 preamble |
| CreateMatch: same dialog, directories, allowEmptyFolder, "Use this folder" | 6 (transitional), 7 (final) |
| Whole-folder commit valid with no top-level videos: allowEmptyFolder ON for add-footage as well as CreateMatch (recursive backend scan, current behavior preserved) | 6 (transitional), 7 (final + test) |
| RelinkDialog: same dialog, directories mode, existing commit | 6 (transitional), 7 (final) |
| Delete obsolete tests of removed behavior | 8 (verified vacuous: none exist) |
| Vitest: mode gating incl. skeleton; drop guard; local toast; hosted enqueue; commit flows incl. allowEmptyFolder (component on/off contract + ON at the add-footage call site); selection reset; sidebar volume + Computer navigation | 1, 3, 4, 5, 7 |
| Screenshot layout pass (bounded headless, domcontentloaded) | 9 |
| Out of scope honored: no backend changes, Computer entry is frontend-only | all tasks (no `src/splitsmith/**/*.py` edits anywhere) |
