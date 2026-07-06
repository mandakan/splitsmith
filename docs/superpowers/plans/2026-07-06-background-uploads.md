# Background Uploads (within-session) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the hosted video-upload queue out of the modal into an app-level provider so uploads continue while the user closes the sheet and navigates, surfaced by a persistent app-level upload dock.

**Architecture:** A new `UploadProvider` React context (mounted at the app root, above the router) owns the upload queue, the single-active-XHR pump, client-side probe data, auto-attach-on-completion, and the `beforeunload` guard. `AddFootageModal`/`HostedUploadBody` becomes a thin view that reads queue state from the provider and dispatches `enqueue`/`cancel`. A new `UploadDock` (single app-level fixed overlay, portaled to body) renders live progress from the provider on every route. The Ingest page refreshes its tray via a provider attach signal instead of a captured callback.

**Tech Stack:** React 18 + TypeScript, react-router-dom, Tailwind (instrument-panel token system), Vite/pnpm. SPA under `src/splitsmith/ui_static/`.

## Global Constraints

- **No test runner in the SPA.** Verification for every task is `pnpm typecheck && pnpm lint && pnpm build` run from `src/splitsmith/ui_static/`, plus the manual smoke steps in the final task. Do NOT fabricate a unit-test harness.
- **Scoped lint only.** Whole-repo `eslint .` is red from 4 pre-existing files; run eslint scoped to changed files (e.g. `pnpm exec eslint src/lib/uploads.tsx`), and rely on `pnpm build` (tsc + vite) as the hard gate.
- **Copy rule:** new user-facing strings and comments use a single ASCII dash `-`, never `--` and never an em dash. Match the existing instrument-panel copy idiom (mono, uppercase, tracking) already used in `AddFootageModal.tsx`.
- **Design tokens only:** reuse existing CSS var tokens (`bg`, `surface`, `surface-2`, `rule`, `rule-strong`, `led`, `led-fill`, `led-tint`, `muted`, `subtle`, `ink`, `z-modal`, `z-drawer`). Do not hard-code colors or new z-index values; verify any token exists in `styles/index.css` before referencing it.
- **Package manager:** pnpm only. Never introduce npm/package-lock.json. No new dependencies.
- **Deployment mode:** hosted-mode only. The local-mode scan-queue branch of `AddFootageModal` is out of scope and must keep its current behavior.

## File Structure

- Create: `src/splitsmith/ui_static/src/lib/uploads.tsx` -- `UploadProvider`, `useUploads` hook, `PendingUpload` type, moved `probeFile`, the pump, auto-attach, and the `beforeunload` guard. (`.tsx` because the provider renders children.)
- Create: `src/splitsmith/ui_static/src/components/UploadDock.tsx` -- the app-level fixed dock.
- Modify: `src/splitsmith/ui_static/src/App.tsx` -- wrap the router with `UploadProvider` and render `<UploadDock/>` once.
- Modify: `src/splitsmith/ui_static/src/components/AddFootageModal.tsx` -- thin the hosted body onto the provider; remove local queue/pump/probe/auto-attach and the in-flight locks.
- Modify: `src/splitsmith/ui_static/src/pages/Ingest.tsx` -- subscribe to the provider attach signal and reload the tray.

---

### Task 1: UploadProvider (queue + pump + auto-attach, mounted at app root)

**Files:**
- Create: `src/splitsmith/ui_static/src/lib/uploads.tsx`
- Modify: `src/splitsmith/ui_static/src/App.tsx:127-133` (wrap router)

**Interfaces:**
- Consumes (existing, from `@/lib/api`): `api.uploadRawFile(file, {signal, onProgress})` returning `{ filename: string; sha256: string | null; size: number }`; `api.attachRawVideo(slug, {...})`; `ApiError` with `.detail`.
- Produces (for Tasks 2-3):
  - type `PendingUpload = { id: string; file: File; slug: string; stages: {stage_number:number;stage_name:string}[]; status: "queued"|"uploading"|"done"|"error"|"cancelled"; attach?: "attaching"|"attached"|"failed"; bytesSent: number; errorMessage?: string; controller?: AbortController }`
  - `useUploads()` returning `{ uploads: PendingUpload[]; enqueue(files: FileList|File[], ctx: {slug:string; stages:{stage_number:number;stage_name:string}[]}): void; cancel(id: string): void; cancelAll(): void; clearFinished(): void; inFlight: boolean; attachTick: number }`
  - `attachTick` increments on every successful auto-attach so subscribers can reload.
  - `<UploadProvider>{children}</UploadProvider>`

- [ ] **Step 1: Create the provider module.**

Move `probeFile` verbatim from `AddFootageModal.tsx` (currently ~lines 49-89) into this file and export nothing extra. Create `src/lib/uploads.tsx`:

```tsx
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { api, ApiError } from "@/lib/api";

// --- probeFile: MOVED verbatim from AddFootageModal.tsx (~lines 49-89). ---
// Reads duration + recorded_start from a hidden <video> so attach-after-upload
// has the metadata. Paste the existing implementation here unchanged.
async function probeFile(
  file: File,
): Promise<{ duration_s: number | null; recorded_start: string | null }> {
  // ... existing body, unchanged ...
}

export interface PendingUpload {
  id: string;
  file: File;
  slug: string;
  stages: { stage_number: number; stage_name: string }[];
  status: "queued" | "uploading" | "done" | "error" | "cancelled";
  attach?: "attaching" | "attached" | "failed";
  bytesSent: number;
  errorMessage?: string;
  controller?: AbortController;
}

interface UploadContextValue {
  uploads: PendingUpload[];
  enqueue: (
    files: FileList | File[],
    ctx: { slug: string; stages: { stage_number: number; stage_name: string }[] },
  ) => void;
  cancel: (id: string) => void;
  cancelAll: () => void;
  clearFinished: () => void;
  inFlight: boolean;
  attachTick: number;
}

const UploadContext = createContext<UploadContextValue | null>(null);

export function useUploads(): UploadContextValue {
  const ctx = useContext(UploadContext);
  if (!ctx) throw new Error("useUploads must be used within <UploadProvider>");
  return ctx;
}

export function UploadProvider({ children }: { children: ReactNode }) {
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const [attachTick, setAttachTick] = useState(0);
  const probeByFilenameRef = useRef<
    Record<string, { duration_s: number | null; recorded_start: string | null }>
  >({});
  const pumpingRef = useRef(false);
  const activeControllerRef = useRef<AbortController | null>(null);
  const [pumpTick, setPumpTick] = useState(0);
  // Mirror of `uploads` so cancel/cancelAll can read controllers without a
  // stale closure and without abusing setState as a getter.
  const uploadsRef = useRef<PendingUpload[]>([]);
  useEffect(() => {
    uploadsRef.current = uploads;
  }, [uploads]);

  const updateOne = useCallback((id: string, patch: Partial<PendingUpload>) => {
    setUploads((prev) => prev.map((u) => (u.id === id ? { ...u, ...patch } : u)));
  }, []);

  const enqueue = useCallback<UploadContextValue["enqueue"]>((files, ctx) => {
    const next: PendingUpload[] = [];
    for (const f of Array.from(files)) {
      next.push({
        id: crypto.randomUUID(),
        file: f,
        slug: ctx.slug,
        stages: ctx.stages,
        status: "queued",
        bytesSent: 0,
      });
      void probeFile(f).then((result) => {
        probeByFilenameRef.current[f.name] = result;
      });
    }
    setUploads((prev) => [...prev, ...next]);
  }, []);

  const cancel = useCallback((id: string) => {
    uploadsRef.current.find((x) => x.id === id)?.controller?.abort();
  }, []);

  const cancelAll = useCallback(() => {
    activeControllerRef.current?.abort();
    uploadsRef.current.forEach((u) => u.controller?.abort());
    setUploads((prev) =>
      prev.map((u) => (u.status === "queued" ? { ...u, status: "cancelled" } : u)),
    );
  }, []);

  const clearFinished = useCallback(() => {
    setUploads((prev) =>
      prev.filter(
        (u) => u.status === "queued" || u.status === "uploading",
      ),
    );
  }, []);

  // Auto-attach a finished object to its shooter's project immediately, so a
  // completed upload is never orphaned. covers_stages omitted -> unassigned
  // tray. Bumps attachTick on success so the Ingest page reloads. Never throws.
  const autoAttach = useCallback(
    async (
      slug: string,
      result: { filename: string; sha256: string | null; size: number },
      probe: { duration_s: number | null; recorded_start: string | null } | undefined,
      id: string,
    ) => {
      updateOne(id, { attach: "attaching" });
      try {
        await api.attachRawVideo(slug, {
          filename: result.filename,
          sha256: result.sha256,
          size_bytes: result.size,
          duration_seconds: probe?.duration_s ?? undefined,
          recorded_start: probe?.recorded_start ?? undefined,
        });
        updateOne(id, { attach: "attached" });
        setAttachTick((t) => t + 1);
      } catch {
        updateOne(id, { attach: "failed" });
      }
    },
    [updateOne],
  );

  // Pump one file at a time (single active XHR). pumpingRef is a load-bearing
  // re-entrancy lock: starting a file flips queued -> uploading, which mutates
  // uploads and re-runs this effect; without the lock the re-run starts the
  // next file too. Abort ONLY on the per-row cancel / cancelAll, never on a
  // pump re-run. No client-side hashing (multi-GB files OOM the tab; the server
  // digests on receipt).
  useEffect(() => {
    if (pumpingRef.current) return;
    const next = uploads.find((u) => u.status === "queued");
    if (!next) return;
    pumpingRef.current = true;

    void (async () => {
      const controller = new AbortController();
      activeControllerRef.current = controller;
      updateOne(next.id, { status: "uploading", bytesSent: 0, controller });
      try {
        const result = await api.uploadRawFile(next.file, {
          signal: controller.signal,
          onProgress: (loaded) => updateOne(next.id, { bytesSent: loaded }),
        });
        updateOne(next.id, { status: "done", bytesSent: next.file.size });
        const probe = probeByFilenameRef.current[next.file.name];
        await autoAttach(next.slug, result, probe, next.id);
      } catch (err) {
        if (err instanceof ApiError && err.detail === "upload cancelled") {
          updateOne(next.id, { status: "cancelled" });
        } else {
          const msg = err instanceof ApiError ? err.detail : String(err);
          updateOne(next.id, { status: "error", errorMessage: msg });
        }
      } finally {
        activeControllerRef.current = null;
        pumpingRef.current = false;
        setPumpTick((t) => t + 1);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploads, pumpTick]);

  const inFlight = uploads.some(
    (u) => u.status === "queued" || u.status === "uploading",
  );

  // Warn before reload / tab-close while uploads run. The queue is in-memory
  // with no resume, so a stray navigation loses in-flight and queued files.
  useEffect(() => {
    if (!inFlight) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [inFlight]);

  return (
    <UploadContext.Provider
      value={{ uploads, enqueue, cancel, cancelAll, clearFinished, inFlight, attachTick }}
    >
      {children}
    </UploadContext.Provider>
  );
}
```

Note: the previous per-file `pumpTick` nudge was gated on `mountedRef` because the pump lived in a component that unmounts. The provider never unmounts during a session, so the `mountedRef` guard is dropped intentionally.

- [ ] **Step 2: Mount the provider at the app root.**

In `App.tsx`, import and wrap the router so the provider sits above `Routes` (survives every route change). Place it inside `ConfirmProvider`, around `BrowserRouter`:

```tsx
// App.tsx imports
import { UploadProvider } from "@/lib/uploads";

// in App(), replace the ConfirmProvider body:
        <ConfirmProvider>
          <UploadProvider>
          <BrowserRouter>
            {/* ...unchanged AuthGate + Routes... */}
          </BrowserRouter>
          </UploadProvider>
        </ConfirmProvider>
```

- [ ] **Step 3: Verify typecheck/lint/build.**

Run from `src/splitsmith/ui_static/`:
```bash
pnpm typecheck && pnpm exec eslint src/lib/uploads.tsx src/App.tsx && pnpm build
```
Expected: PASS. `uploads` context is created and mounted; nothing consumes it yet (Task 2), which is fine.

- [ ] **Step 4: Commit.**

```bash
git add src/splitsmith/ui_static/src/lib/uploads.tsx src/splitsmith/ui_static/src/App.tsx
git commit -m "feat(ui): app-level UploadProvider for background uploads"
```

---

### Task 2: Refactor HostedUploadBody onto the provider

**Files:**
- Modify: `src/splitsmith/ui_static/src/components/AddFootageModal.tsx` (hosted body ~816-1329; remove `probeFile` ~49-89 now that it lives in the provider)
- Modify: `src/splitsmith/ui_static/src/pages/Ingest.tsx` (subscribe to `attachTick`)

**Interfaces:**
- Consumes: `useUploads()` and `PendingUpload` from Task 1 (`@/lib/uploads`).
- Produces: a dismissable modal that no longer owns transfer state; the pending list renders `useUploads().uploads` filtered to the current slug.

- [ ] **Step 1: Delete the moved transfer state from `HostedUploadBody`.**

Remove these now-provider-owned pieces from `HostedUploadBody` (lines are approximate, match by content):
- `const [uploads, setUploads] = useState<PendingUpload[]>([]);` (855)
- `probeByFilenameRef` (875-877), `pumpingRef`/`activeControllerRef`/`mountedRef`/`pumpTick` (891-894)
- the mount/unmount abort effect (896-902)
- `updateOne` (934-941), `enqueue` (943-962), the pump effect (979-1045)
- the local `autoAttach` (1084-1110)
- the `inFlight` + `beforeunload` effect (1149-1164)
- the local `PendingUpload` interface (832-842) -- now imported from `@/lib/uploads`
- `probeFile` at the top of the file (~49-89) -- moved to the provider in Task 1

Keep in the modal: `existing`/`refreshExisting`, `attachState`, `suggestionByFilename`/`coverageByFilename`, `attachToProject`, `removeUploaded`, dropzone/drag state, `useDialogFocus`.

- [ ] **Step 2: Wire the modal to the provider.**

At the top of `HostedUploadBody`:

```tsx
const { uploads: allUploads, enqueue, cancel } = useUploads();
// Show only this shooter's pending items in the modal's session list.
const uploads = allUploads.filter((u) => u.slug === slug);
const inFlight = uploads.some(
  (u) => u.status === "queued" || u.status === "uploading",
);
```

Replace the local `enqueue(files)` calls in `onPick`/`onDrop` with the provider call, passing context:

```tsx
const doEnqueue = (files: FileList | File[]) => enqueue(files, { slug, stages });
// onPick:  if (e.target.files?.length) doEnqueue(e.target.files);
// onDrop:  if (e.dataTransfer.files?.length) doEnqueue(e.dataTransfer.files);
```

Replace the per-row cancel wiring: `<UploadRow ... onCancel={() => cancel(u.id)} />`.

After a successful manual attach, keep the existing `onImported` call (unchanged) -- it drives the Ingest banner + reload while the modal is open.

- [ ] **Step 3: Make the modal dismissable while uploads run.**

Remove the in-flight locks so the user can close and keep working:
- Backdrop: `onClick={onClose}` (drop the `!inFlight` guard, line 1184).
- `useDialogFocus(true, panelRef, onClose, { disableEscape: false })` (drop `disableEscape: inFlight`, line 1175). If `disableEscape` has no other use, pass `{}` or omit.
- Always render the X close button (drop the `{!inFlight && ...}` wrapper, lines 1201-1210).
- Footer button: always enabled, label always `Done` (drop `disabled={inFlight}` and the `"Uploading..."` ternary, lines 1319-1322).

Add the within-session hint under the header subtitle (line ~1196-1199 area), shown only while `inFlight`:

```tsx
{inFlight && (
  <p className="mt-1 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-led">
    You can close this and keep working - uploads continue in the background.
  </p>
)}
```

- [ ] **Step 4: Reload the Ingest tray from the attach signal.**

In `Ingest.tsx`, subscribe to the provider so the tray refreshes as background uploads land (even after the modal closes, while the page is still mounted). Add near the other hooks:

```tsx
import { useUploads } from "@/lib/uploads";
// inside Ingest():
const { attachTick } = useUploads();
useEffect(() => {
  if (attachTick === 0) return;
  void reload();
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [attachTick]);
```

Use the existing `reload()` in `Ingest.tsx` (the one `afterImport` already calls, ~line 167-181). Do not remove `afterImport`/`onImported` -- the manual-attach path still uses it.

- [ ] **Step 5: Verify typecheck/lint/build.**

```bash
pnpm typecheck && pnpm exec eslint src/components/AddFootageModal.tsx src/pages/Ingest.tsx && pnpm build
```
Expected: PASS, no unused-symbol errors (confirm `PendingUpload`/`probeFile` no longer referenced locally in `AddFootageModal.tsx`).

- [ ] **Step 6: Commit.**

```bash
git add src/splitsmith/ui_static/src/components/AddFootageModal.tsx src/splitsmith/ui_static/src/pages/Ingest.tsx
git commit -m "refactor(ui): thin AddFootageModal onto UploadProvider, dismissable mid-upload"
```

---

### Task 3: UploadDock (app-level fixed progress surface)

**Files:**
- Create: `src/splitsmith/ui_static/src/components/UploadDock.tsx`
- Modify: `src/splitsmith/ui_static/src/App.tsx` (render `<UploadDock/>` once inside `UploadProvider`)

**Interfaces:**
- Consumes: `useUploads()` (Task 1); `Portal` from the existing portal component (same import `AddFootageModal.tsx` uses -- confirm its path, e.g. `@/components/ui/Portal`).
- Produces: a single fixed dock, visible on every route whenever `uploads.length > 0`.

- [ ] **Step 1: Create the dock component.**

Collapsed pill + expandable per-file list. Fixed bottom-right, portaled to body, `z-drawer`. Hidden entirely when there are no uploads (active or finished-unacknowledged). Aggregate percent = total bytesSent / total size across the queue.

```tsx
import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, X } from "lucide-react";

import { Portal } from "@/components/ui/Portal"; // confirm actual path
import { useUploads, type PendingUpload } from "@/lib/uploads";

export function UploadDock() {
  const { uploads, cancel, cancelAll, clearFinished, inFlight } = useUploads();
  const [expanded, setExpanded] = useState(true);

  const { done, total, pct } = useMemo(() => {
    const totalBytes = uploads.reduce((a, u) => a + u.file.size, 0);
    const sentBytes = uploads.reduce((a, u) => a + u.bytesSent, 0);
    return {
      done: uploads.filter((u) => u.status === "done").length,
      total: uploads.length,
      pct: totalBytes > 0 ? Math.min(100, Math.round((sentBytes / totalBytes) * 100)) : 0,
    };
  }, [uploads]);

  if (uploads.length === 0) return null;

  return (
    <Portal>
      <div className="fixed bottom-4 right-4 z-drawer w-[min(360px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-rule-strong bg-surface text-ink shadow-[0_24px_48px_-12px_rgba(0,0,0,0.7)]">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex w-full items-center justify-between gap-3 border-b border-rule bg-surface-2 px-4 py-2.5 text-left"
        >
          <span className="font-display text-[0.75rem] font-bold uppercase tracking-[0.08em]">
            {inFlight ? `Uploading ${done + 1} of ${total}` : `Uploads ${done}/${total}`} . {pct}%
          </span>
          {expanded ? <ChevronDown className="size-4 text-muted" /> : <ChevronUp className="size-4 text-muted" />}
        </button>
        {!inFlight ? null : (
          <div className="h-1 w-full bg-surface-3">
            <div className="h-full bg-led transition-[width]" style={{ width: `${pct}%` }} />
          </div>
        )}
        {expanded && (
          <div className="flex max-h-[40vh] flex-col gap-1.5 overflow-y-auto px-3 py-3">
            {uploads.map((u) => (
              <DockRow key={u.id} upload={u} onCancel={() => cancel(u.id)} />
            ))}
            <p className="mt-1 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
              Uploads run in the background, but don't reload the page until they finish.
            </p>
            <div className="flex justify-end gap-2 pt-1">
              {inFlight && (
                <button
                  type="button"
                  onClick={cancelAll}
                  className="rounded-md border border-rule px-2.5 py-1 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-muted hover:text-ink"
                >
                  Cancel all
                </button>
              )}
              {!inFlight && (
                <button
                  type="button"
                  onClick={clearFinished}
                  className="rounded-md border border-rule px-2.5 py-1 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-muted hover:text-ink"
                >
                  Dismiss
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </Portal>
  );
}

function DockRow({ upload, onCancel }: { upload: PendingUpload; onCancel: () => void }) {
  const pct =
    upload.file.size > 0
      ? Math.min(100, Math.round((upload.bytesSent / upload.file.size) * 100))
      : 0;
  const active = upload.status === "queued" || upload.status === "uploading";
  return (
    <div className="rounded-md border border-rule bg-surface-2 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 flex-1 truncate font-mono text-[0.6875rem] text-ink">
          {upload.file.name}
        </span>
        {active && (
          <button type="button" onClick={onCancel} aria-label="Cancel upload" className="text-subtle hover:text-led">
            <X className="size-3.5" />
          </button>
        )}
      </div>
      <div className="mt-0.5 font-mono text-[0.5625rem] uppercase tracking-[0.06em] text-muted">
        {upload.status === "queued" && "queued"}
        {upload.status === "uploading" && `${pct}%`}
        {upload.status === "done" && upload.attach === "attached" && "done . attached"}
        {upload.status === "done" && upload.attach === "attaching" && "done . attaching"}
        {upload.status === "done" && upload.attach === "failed" && "done . attach failed"}
        {upload.status === "cancelled" && "cancelled"}
        {upload.status === "error" && `error - ${upload.errorMessage ?? "failed"}`}
      </div>
    </div>
  );
}
```

Confirm before writing: the real `Portal` import path (grep `AddFootageModal.tsx` for its `Portal` import). Confirm `bg-surface-3` and `z-drawer` exist in `styles/index.css`; if `surface-3` is absent use `surface-2`.

- [ ] **Step 2: Render the dock once at the app root.**

In `App.tsx`, render `<UploadDock/>` inside `UploadProvider` so it is present on every route:

```tsx
import { UploadDock } from "@/components/UploadDock";

          <UploadProvider>
            <UploadDock />
            <BrowserRouter>
              {/* ... */}
            </BrowserRouter>
          </UploadProvider>
```

- [ ] **Step 3: Verify typecheck/lint/build.**

```bash
pnpm typecheck && pnpm exec eslint src/components/UploadDock.tsx src/App.tsx && pnpm build
```
Expected: PASS.

- [ ] **Step 4: Commit.**

```bash
git add src/splitsmith/ui_static/src/components/UploadDock.tsx src/splitsmith/ui_static/src/App.tsx
git commit -m "feat(ui): app-level UploadDock surfacing background upload progress"
```

---

### Task 4: Manual smoke verification + PR

**Files:** none (verification + PR).

- [ ] **Step 1: Build the SPA and run the hosted server locally (or the standard hosted dev flow) and smoke-test:**

1. Open the Ingest page for a shooter (hosted mode). Click "Add footage", queue 3+ files.
2. Close the sheet mid-upload -> the modal closes without a warning, the `UploadDock` appears bottom-right and continues advancing. Navigate to another stage/route -> dock persists and keeps progressing.
3. While uploads run, return to Ingest -> finished files appear in the tray (attachTick reload), and you can assign coverage to a finished file while others still upload.
4. Cancel one file from the dock (X on its row); "Cancel all" aborts the rest.
5. After all finish, the dock shows `done . attached` rows and a "Dismiss" action; Dismiss clears the finished rows and hides the dock.
6. With an upload in flight, attempt a browser reload -> the `beforeunload` warning fires; confirming loses the in-flight uploads (expected/documented), cancelling keeps them running.
7. Trigger an attach failure path if feasible (or code-review it): a `done . attach failed` row shows, the object is still uploaded, and re-opening the modal shows it under "Already in storage" for manual attach.

- [ ] **Step 2: Full gate.**

```bash
cd src/splitsmith/ui_static && pnpm typecheck && pnpm lint && pnpm build
```
If whole-repo `pnpm lint` is red only from the 4 known pre-existing files, note that in the PR; the changed files must be clean.

- [ ] **Step 3: Open the PR.**

```bash
git push -u origin feat/background-uploads
gh pr create --title "feat(ui): within-session background uploads" --body "..."
```
PR body: summarize the provider/dock/modal split, the within-session scope (link the spec), the preserved `beforeunload` + auto-attach semantics, and the manual smoke results. Reference the design spec path.
