/**
 * Multi-source ingest wizard for the Ingest page (#322 follow-up).
 *
 * Previously the Ingest page exposed an inline ``FolderPicker`` whose
 * ``onSelect`` triggered a scan immediately. That hid three things from
 * the user:
 *   1. You could only ingest one folder at a time -- picking a second
 *      folder meant going back and re-doing the flow.
 *   2. The storage choice (symlink vs copy) lived on the page above the
 *      drop area, easy to miss.
 *   3. If the picked folder had no matching videos, the modal closed
 *      and the page looked unchanged. No feedback.
 *
 * This modal fixes all three. Pick folders or files into a queue, see
 * per-source video counts, choose storage once, hit Import, and watch
 * per-source results land. The modal never closes silently -- a zero-
 * video result is surfaced as a row in the result view.
 */

import {
  Check,
  ChevronRight,
  Film,
  FolderOpen,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { FolderPicker } from "@/components/FolderPicker";
import { CoverageSelect } from "@/components/ingest/CoverageSelect";
import { Avatar } from "@/components/ui";
import { Portal } from "@/components/ui/Portal";
import {
  ApiError,
  api,
  type RawUploadEntry,
  type ScanResponse,
} from "@/lib/api";
import { useDialogFocus } from "@/lib/dialogFocus";
import { useDeploymentMode } from "@/lib/features";
import { cn } from "@/lib/utils";

/** Probe a File object for its duration and compute the recording start
 *  from file.lastModified (modification time) minus duration. Returns
 *  a timezone-aware UTC ISO string so the backend's AwareDatetime field
 *  does not 422. */
function probeFile(
  file: File,
): Promise<{ duration_s: number | null; recorded_start: string | null }> {
  return new Promise((resolve) => {
    const el = document.createElement("video");
    el.preload = "metadata";
    const url = URL.createObjectURL(file);
    // Guard: revoke exactly once across all three exit paths.
    let revoked = false;
    function revoke() {
      if (revoked) return;
      revoked = true;
      URL.revokeObjectURL(url);
    }
    // Timeout guard: if neither onloadedmetadata nor onerror fires (e.g.
    // the codec is unsupported and the browser stalls silently), revoke
    // the URL and resolve nulls after 5 seconds.
    const timer = setTimeout(() => {
      revoke();
      resolve({ duration_s: null, recorded_start: null });
    }, 5000);
    el.onloadedmetadata = () => {
      clearTimeout(timer);
      const duration = Number.isFinite(el.duration) ? el.duration : null;
      revoke();
      resolve({
        duration_s: duration,
        recorded_start:
          duration != null && file.lastModified
            ? new Date(file.lastModified - duration * 1000).toISOString()
            : null,
      });
    };
    el.onerror = () => {
      clearTimeout(timer);
      revoke();
      resolve({ duration_s: null, recorded_start: null });
    };
    el.src = url;
  });
}

export type StorageMode = "symlink" | "copy";

type QueueItem =
  | {
      kind: "folder";
      path: string;
    }
  | {
      kind: "files";
      /** Parent folder the picks came from. Used as a stable key so
       *  toggling checkboxes in the picker updates the same queue
       *  entry instead of stacking duplicates. */
      folder: string;
      paths: string[];
    };

type ScanState =
  | { status: "pending" }
  | { status: "running" }
  | { status: "ok"; result: ScanResponse }
  | { status: "error"; message: string };

interface AddFootageModalProps {
  slug: string;
  initialStorage: StorageMode;
  initialPath?: string | null;
  onClose: () => void;
  /** Fires after the import finishes (even if empty), so the parent
   *  can reload its project state. ``imported`` is the total videos
   *  registered across all sources; ``paths`` is the flat list of all
   *  registered paths (for the post-import batch banner B1). */
  onImported: (imported: number, paths: string[]) => void;
  /** Fires when the user changes the storage mode in the modal, so the
   *  parent can remember their pick across ingests. Optional. */
  onStorageChange?: (mode: StorageMode) => void;
  /** Name of the active shooter displayed in the modal header as a
   *  visibility cue (A2). Omit for single-shooter or when unknown. */
  shooterName?: string;
  /** Match stages for the coverage multi-select at attach time. Pass
   *  the project's ``stages`` array; an empty array skips the widget. */
  stages?: { stage_number: number; stage_name: string }[];
}

export function AddFootageModal({
  slug,
  initialStorage,
  initialPath,
  onClose,
  onImported,
  onStorageChange,
  shooterName,
  stages = [],
}: AddFootageModalProps) {
  // Hosted mode: the SPA upload UX hasn't shipped yet (deferred to the
  // tus migration in doc 05). Render a placeholder explaining the
  // curl-only path so the operator isn't dead-ended into a filesystem
  // picker against the container's ephemeral disk (#425). Hooks below
  // still run so the Rules of Hooks are satisfied; the branch is in
  // the returned JSX.
  const deploymentMode = useDeploymentMode();
  const hostedMode = deploymentMode === "hosted";

  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [storage, setStorageState] = useState<StorageMode>(initialStorage);
  const setStorage = useCallback(
    (mode: StorageMode) => {
      setStorageState(mode);
      onStorageChange?.(mode);
    },
    [onStorageChange],
  );
  const [showPicker, setShowPicker] = useState(true);
  const [phase, setPhase] = useState<"queue" | "running" | "result">("queue");
  const [scanStates, setScanStates] = useState<ScanState[]>([]);
  // Track the FolderPicker's current path so we can compute mutex flags
  // (folder-check vs file-check) per the design's "prevent double-import
  // semantics" rule. ``null`` until the picker reports its first load.
  const [pickerPath, setPickerPath] = useState<string | null>(null);
  // True when the current picker path is already queued as a "whole
  // folder". Disables file checkboxes inside it so the operator can't
  // also queue specific files at the same level.
  const pickerFolderAlreadyWhole =
    pickerPath != null && queue.some((q) => q.kind === "folder" && q.path === pickerPath);
  // True when the current picker path has files queued inside it.
  // Disables the "Use this folder" button so the operator can't also
  // queue the whole folder at the same level.
  const pickerFolderHasFileChecks =
    pickerPath != null && queue.some((q) => q.kind === "files" && q.folder === pickerPath);

  const panelRef = useRef<HTMLDivElement | null>(null);

  // Escape / focus trap / restore. Escape is blocked only while a scan
  // is RUNNING (a stray keystroke must not abandon it); the queue and
  // result phases both close -- there is nothing in-flight to protect
  // once results are on screen.
  useDialogFocus(!hostedMode, panelRef, onClose, {
    disableEscape: phase === "running",
  });

  const addFolder = useCallback((path: string) => {
    setQueue((prev) => {
      // Dedupe identical paths -- adding the same folder twice would
      // double-import every video on the SECOND scan since the first
      // has already registered them.
      if (prev.some((q) => q.kind === "folder" && q.path === path)) {
        return prev;
      }
      return [...prev, { kind: "folder", path }];
    });
    setShowPicker(false);
  }, []);

  // Picker calls this on every file-selection change (including
  // empty). We maintain one ``kind: "files"`` queue entry per folder,
  // keyed by ``folder``, so toggling boxes in the picker updates the
  // same entry rather than stacking duplicates -- and unchecking the
  // last box removes the entry entirely.
  const syncFolderFiles = useCallback(
    (folder: string, files: { path: string; mtime: number | null }[]) => {
      setQueue((prev) => {
        const others = prev.filter(
          (q) => !(q.kind === "files" && q.folder === folder),
        );
        if (files.length === 0) return others;
        return [
          ...others,
          {
            kind: "files",
            folder,
            paths: files.map((f) => f.path),
          },
        ];
      });
    },
    [],
  );

  const removeItem = useCallback((index: number) => {
    setQueue((prev) => prev.filter((_, i) => i !== index));
  }, []);

  async function runImport() {
    if (queue.length === 0) return;
    setPhase("running");
    const states: ScanState[] = queue.map(() => ({ status: "pending" }));
    setScanStates(states);

    let totalImported = 0;
    const allRegistered: string[] = [];
    for (let i = 0; i < queue.length; i++) {
      const item = queue[i];
      // Mark running.
      states[i] = { status: "running" };
      setScanStates([...states]);
      try {
        let result: ScanResponse;
        if (item.kind === "folder") {
          result = await api.scanVideos(slug, item.path, true, storage);
        } else {
          result = await api.scanFiles(slug, item.paths, true, storage);
        }
        states[i] = { status: "ok", result };
        totalImported += result.registered.length;
        allRegistered.push(...result.registered);
      } catch (e) {
        states[i] = {
          status: "error",
          message: e instanceof ApiError ? e.detail : String(e),
        };
      }
      setScanStates([...states]);
    }

    setPhase("result");
    onImported(totalImported, allRegistered);
  }

  if (hostedMode) {
    return (
      <HostedUploadSurface
        slug={slug}
        onClose={onClose}
        onImported={onImported}
        stages={stages}
      />
    );
  }

  return (
    <Portal>
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Add footage"
      className="fixed inset-0 z-modal flex items-center justify-center bg-bg/70 p-4 backdrop-blur-sm"
      onClick={phase === "queue" ? onClose : undefined}
    >
      <div
        ref={panelRef}
        className="relative flex h-[min(720px,90vh)] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-rule-strong bg-surface text-ink shadow-[0_24px_48px_-12px_rgba(0,0,0,0.7)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-4 border-b border-rule px-5 py-3.5">
          <div>
            <h2 className="font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
              {phase === "queue" && "Add footage"}
              {phase === "running" && "Importing..."}
              {phase === "result" && "Import results"}
            </h2>
            <p className="mt-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
              {phase === "queue" &&
                "Queue one or more sources, choose how to store them, then import."}
              {phase === "running" && "Scanning each source for video files..."}
              {phase === "result" &&
                "All sources processed. Empty rows mean no videos were found."}
            </p>
            {/* A2: shooter identity echo -- visibility cue, not a confirm gate */}
            {shooterName && (
              <div className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-led-deep bg-led-tint px-2 py-0.5">
                <Avatar
                  size="xs"
                  initials={shooterInitials(shooterName)}
                  seed={slug}
                  name={shooterName}
                />
                <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.12em] text-led-text">
                  Adding to {shooterName}
                </span>
              </div>
            )}
          </div>
          {phase !== "running" && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded-md p-1.5 text-subtle hover:bg-surface-2 hover:text-ink"
            >
              <X className="size-4" />
            </button>
          )}
        </header>

        {/* Dense storage subheader -- storage is a one-time decision so
            it doesn't deserve hero placement inside the queue body. A
            40px row directly under the title surfaces the active mode
            with one-click toggles; the body below gets the full
            remaining height for the queue + sources picker. */}
        {phase === "queue" && (
          <div className="flex h-10 items-center gap-3 border-b border-rule bg-surface-2 px-5">
            <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
              Storage
            </span>
            <div
              role="radiogroup"
              aria-label="Storage mode"
              className="inline-flex rounded-full border border-rule bg-surface p-0.5"
            >
              <StorageTab
                checked={storage === "symlink"}
                onClick={() => setStorage("symlink")}
              >
                Reference in place
              </StorageTab>
              <StorageTab
                checked={storage === "copy"}
                onClick={() => setStorage("copy")}
              >
                Copy into project
              </StorageTab>
            </div>
            <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
              {storage === "symlink"
                ? "originals stay where they are -- zero extra disk"
                : "self-contained -- survives unmounting source media"}
            </span>
          </div>
        )}

        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          {phase === "queue" && (
            <QueueView
              slug={slug}
              queue={queue}
              showPicker={showPicker}
              initialPath={initialPath ?? undefined}
              pickerFolderAlreadyWhole={pickerFolderAlreadyWhole}
              pickerFolderHasFileChecks={pickerFolderHasFileChecks}
              onAddFolder={addFolder}
              onFolderFilesChange={syncFolderFiles}
              onRemove={removeItem}
              onTogglePicker={() => setShowPicker((p) => !p)}
              onPickerPathChange={setPickerPath}
            />
          )}
          {(phase === "running" || phase === "result") && (
            <ResultsView queue={queue} states={scanStates} />
          )}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-rule bg-surface-2 px-5 py-3.5">
          <div className="min-w-0 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
            {phase === "queue" && queue.length > 0 && (
              <>
                <b className="text-ink-2">{queue.length}</b> source
                {queue.length === 1 ? "" : "s"} queued &middot;{" "}
                {storage === "symlink" ? "reference in place" : "copy into project"}
              </>
            )}
            {phase === "running" && "Don't close the window."}
            {phase === "result" &&
              (() => {
                const ok = scanStates.filter((s) => s.status === "ok").length;
                const errors = scanStates.filter((s) => s.status === "error").length;
                const totalImported = scanStates.reduce(
                  (sum, s) =>
                    s.status === "ok" ? sum + s.result.registered.length : sum,
                  0,
                );
                return (
                  <>
                    <b className="text-ink-2">{totalImported}</b> videos imported
                    &middot; {ok} ok
                    {errors > 0 ? (
                      <span className="ml-1 text-led">&middot; {errors} failed</span>
                    ) : null}
                  </>
                );
              })()}
          </div>
          <div className="flex items-center gap-2">
            {phase === "queue" && (
              <>
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-md border border-rule px-3 py-1.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:border-rule-strong hover:bg-surface-3"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void runImport()}
                  disabled={queue.length === 0}
                  className="inline-flex items-center gap-2 rounded-md bg-led-fill px-3.5 py-1.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink shadow-[0_0_0_1px_var(--color-led-fill),0_0_18px_var(--color-led-glow)] hover:bg-led disabled:opacity-50 disabled:shadow-none"
                >
                  Import {queue.length > 0 ? `${queue.length} source${queue.length === 1 ? "" : "s"}` : ""}
                  <ChevronRight className="size-3.5" />
                </button>
              </>
            )}
            {phase === "result" && (
              <button
                type="button"
                onClick={onClose}
                className="rounded-md bg-led-fill px-3.5 py-1.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink shadow-[0_0_0_1px_var(--color-led-fill),0_0_18px_var(--color-led-glow)] hover:bg-led"
              >
                Done
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
    </Portal>
  );
}

function QueueView({
  slug,
  queue,
  showPicker,
  initialPath,
  pickerFolderAlreadyWhole,
  pickerFolderHasFileChecks,
  onAddFolder,
  onFolderFilesChange,
  onRemove,
  onTogglePicker,
  onPickerPathChange,
}: {
  slug: string;
  queue: QueueItem[];
  showPicker: boolean;
  initialPath?: string;
  /** The current picker folder is already queued as a whole-folder
   *  source. File checkboxes inside it should be disabled to prevent
   *  double-import semantics. */
  pickerFolderAlreadyWhole: boolean;
  /** The current picker folder has file checks queued. The
   *  "Add whole folder" affordance should be disabled. */
  pickerFolderHasFileChecks: boolean;
  onAddFolder: (path: string) => void;
  onFolderFilesChange: (folder: string, files: { path: string; mtime: number | null }[]) => void;
  onRemove: (index: number) => void;
  onTogglePicker: () => void;
  onPickerPathChange: (path: string | null) => void;
}) {
  return (
    <div className="flex flex-col gap-5 px-5 py-5">
      {/* Storage choice now lives in the 40px subheader above this
          body, freeing the queue + picker to use the full height. */}

      {/* Sources queue */}
      <section>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="font-display text-xs font-bold uppercase tracking-[0.1em] text-ink-2">
            Sources to import
          </h3>
          {queue.length > 0 && !showPicker && (
            <button
              type="button"
              onClick={onTogglePicker}
              className="inline-flex items-center gap-1.5 rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1 font-display text-[0.625rem] font-bold uppercase tracking-[0.1em] text-ink-2 hover:border-led-deep hover:bg-led-tint hover:text-led"
            >
              <Plus className="size-3" />
              Add another
            </button>
          )}
        </div>
        {queue.length === 0 ? (
          <div className="rounded-lg border border-dashed border-rule px-4 py-3 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-subtle">
            No sources yet. Pick a folder or files below.
          </div>
        ) : (
          <ul className="overflow-hidden rounded-lg border border-rule bg-bg-glow">
            {queue.map((item, i) => {
              const primary = item.kind === "folder" ? item.path : item.folder;
              const detail =
                item.kind === "folder"
                  ? "Whole folder"
                  : `${item.paths.length} file${item.paths.length === 1 ? "" : "s"}`;
              return (
                <li
                  key={`${item.kind}-${primary}`}
                  className="flex items-center gap-3 border-b border-rule px-4 py-2.5 last:border-b-0"
                >
                  <FolderOpen className="size-4 shrink-0 text-subtle" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-mono text-[0.8125rem] tabular-nums text-ink">
                      {primary}
                    </div>
                    <div className="mt-0.5 font-mono text-[0.625rem] uppercase tracking-[0.08em] text-subtle">
                      {detail}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => onRemove(i)}
                    aria-label={`Remove ${primary}`}
                    className="rounded-md p-1 text-subtle hover:bg-surface-2 hover:text-led"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* Inline picker */}
      {showPicker && (
        <section>
          <h3 className="mb-2 font-display text-xs font-bold uppercase tracking-[0.1em] text-ink-2">
            {queue.length === 0 ? "Pick a source" : "Add another source"}
          </h3>
          <div className="rounded-lg border border-rule-strong bg-bg-glow p-3">
            <FolderPicker
              slug={slug}
              initialPath={initialPath ?? null}
              onSelect={onAddFolder}
              autoCommitFiles
              onSelectFiles={() => {
                /* unused in autoCommit mode; onFolderFilesChange handles it */
              }}
              onFolderFilesChange={onFolderFilesChange}
              onPathChange={onPickerPathChange}
              // Mutex: once any file inside foo/ is queued, "Add whole
              // folder" on foo/ is disabled; once foo/ is queued whole,
              // file checkboxes inside it are disabled. Prevents double-
              // import semantics entirely.
              addWholeFolderDisabled={pickerFolderHasFileChecks}
              addWholeFolderDisabledReason={
                pickerFolderHasFileChecks
                  ? "Some files in this folder are already queued. Remove them first to import the whole folder."
                  : undefined
              }
              filesDisabled={pickerFolderAlreadyWhole}
              filesDisabledReason={
                pickerFolderAlreadyWhole
                  ? "This folder is already queued as a whole. Remove it from the queue to pick individual files."
                  : undefined
              }
              onCancel={queue.length > 0 ? onTogglePicker : undefined}
              mode="inline"
              selectLabel="Add whole folder"
              // The scan endpoint walks recursively (``source.rglob``)
              // so a folder with only subdirectories is a valid pick --
              // don't gate the button on direct video children.
              allowEmptyFolder
            />
          </div>
        </section>
      )}
    </div>
  );
}

function StorageTab({
  checked,
  onClick,
  children,
}: {
  checked: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  // Dense radio-tab pair for the storage subheader. Active tab fills
  // with --led-tint so the active mode reads instantly even at small
  // size; inactive stays neutral with a hover lift.
  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      onClick={onClick}
      className={cn(
        "inline-flex items-center rounded-full px-3 py-1 font-display text-[0.625rem] font-bold uppercase tracking-[0.08em] transition-colors",
        checked
          ? "bg-led-tint text-led-text shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--color-led)_55%,transparent)]"
          : "text-muted hover:text-ink-2",
      )}
    >
      {children}
    </button>
  );
}

function ResultsView({
  queue,
  states,
}: {
  queue: QueueItem[];
  states: ScanState[];
}) {
  const totalImported = states.reduce(
    (sum, s) => (s.status === "ok" ? sum + s.result.registered.length : sum),
    0,
  );
  const errors = states.filter((s) => s.status === "error").length;
  const allDone = states.every(
    (s) => s.status === "ok" || s.status === "error",
  );

  return (
    <div className="flex flex-col gap-4 px-5 py-5">
      {/* Hero summary -- the user shouldn't have to read row labels to
         know whether the import worked. Green when successful, red when
         every source failed, neutral while running. */}
      {allDone && (
        <div
          className={cn(
            "flex items-center gap-3 rounded-lg border px-4 py-3",
            errors === states.length
              ? "border-led/50 bg-led-tint"
              : "border-done/40 bg-done/10",
          )}
        >
          <span
            className={cn(
              "inline-flex size-9 items-center justify-center rounded-full",
              errors === states.length
                ? "bg-led text-ink"
                : "bg-done text-bg shadow-[0_0_12px_var(--color-done-glow)]",
            )}
          >
            {errors === states.length ? (
              <X className="size-5" strokeWidth={3} />
            ) : (
              <Check className="size-5" strokeWidth={3} />
            )}
          </span>
          <div>
            <div className="font-display text-lg font-bold uppercase tracking-tight text-ink">
              {totalImported} video{totalImported === 1 ? "" : "s"} imported
            </div>
            <div className="mt-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
              {errors === 0
                ? "All sources processed. Click Done to review."
                : errors === states.length
                  ? "Every source failed -- see details below."
                  : `${errors} source${errors === 1 ? "" : "s"} failed -- see details below.`}
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-col gap-2.5">
        {queue.map((item, i) => {
          const state = states[i] ?? { status: "pending" };
          const primary = item.kind === "folder" ? item.path : item.folder;
          return (
            <ResultRow key={`${item.kind}-${primary}`} item={item} state={state} />
          );
        })}
      </div>
    </div>
  );
}

function ResultRow({
  item,
  state,
}: {
  item: QueueItem;
  state: ScanState;
}) {
  const label =
    item.kind === "folder"
      ? item.path
      : `${item.folder} (${item.paths.length} file${item.paths.length === 1 ? "" : "s"})`;
  return (
    <div
      className={cn(
        "grid items-center gap-3 rounded-lg border px-4 py-3",
        state.status === "ok"
          ? "border-done/40 bg-done/5"
          : state.status === "error"
            ? "border-led/40 bg-led-tint"
            : state.status === "running"
              ? "border-rule-strong bg-surface-2"
              : "border-rule bg-surface-2",
      )}
      style={{ gridTemplateColumns: "24px 1fr auto" }}
    >
      <StatusIcon state={state} />
      <div className="min-w-0">
        <div className="truncate font-mono text-[0.8125rem] tabular-nums text-ink">
          {label}
        </div>
        <div className="mt-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
          {state.status === "pending" && "Waiting..."}
          {state.status === "running" && "Scanning..."}
          {state.status === "ok" && (
            <>
              {state.result.registered.length === 0
                ? "No videos found"
                : `${state.result.registered.length} imported`}
              {Object.keys(state.result.auto_assigned).length > 0 && (
                <span>
                  {" "}
                  &middot; {Object.keys(state.result.auto_assigned).length} matched
                  to stages
                </span>
              )}
              {state.result.skipped.length > 0 && (
                <span>
                  {" "}
                  &middot; {state.result.skipped.length} skipped
                </span>
              )}
            </>
          )}
          {state.status === "error" && (
            <span className="text-led">{state.message}</span>
          )}
        </div>
      </div>
      <div className="shrink-0 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-subtle">
        {state.status === "ok" && state.result.registered.length > 0 && (
          <span className="inline-flex items-center gap-1.5 text-done">
            <Film className="size-3" />
            {state.result.registered.length}
          </span>
        )}
      </div>
    </div>
  );
}

function StatusIcon({ state }: { state: ScanState }) {
  if (state.status === "ok") {
    return (
      <span
        aria-label="done"
        className="inline-flex size-5 items-center justify-center rounded-full bg-done text-bg"
      >
        <Check className="size-3" strokeWidth={3} />
      </span>
    );
  }
  if (state.status === "error") {
    return (
      <span
        aria-label="error"
        className="inline-flex size-5 items-center justify-center rounded-full bg-led text-ink"
      >
        <X className="size-3" strokeWidth={3} />
      </span>
    );
  }
  if (state.status === "running") {
    return (
      <span
        aria-label="scanning"
        className="inline-block size-4 animate-spin rounded-full border-2 border-rule-strong border-t-led"
      />
    );
  }
  return (
    <span
      aria-label="pending"
      className="inline-block size-2 rounded-full bg-subtle"
    />
  );
}

/** Hosted-mode browser upload surface: drag-and-drop / file-pick,
 *  per-file progress, list of what's already uploaded, prune via
 *  delete. Files land in S3 under ``users/<id>/raw/`` via
 *  ``POST /api/me/raw/upload``; the SPA never sees a host filesystem
 *  path. Today the upload terminates at object storage -- attaching
 *  to a project happens once the worker pipeline can read from S3
 *  (separate chunk per the saas-readiness roadmap). */
function HostedUploadSurface({
  slug,
  onClose,
  onImported,
  stages,
}: {
  slug: string;
  onClose: () => void;
  onImported: (imported: number, paths: string[]) => void;
  stages: { stage_number: number; stage_name: string }[];
}) {
  return (
    <HostedUploadBody slug={slug} onClose={onClose} onImported={onImported} stages={stages} />
  );
}

interface PendingUpload {
  /** Stable per-upload id so re-renders keep progress bars aligned
   *  with the right file. ``crypto.randomUUID`` is fine -- we don't
   *  persist this. */
  id: string;
  file: File;
  status: "queued" | "uploading" | "done" | "error" | "cancelled";
  bytesSent: number;
  errorMessage?: string;
  controller?: AbortController;
}

function HostedUploadBody({
  slug,
  onClose,
  onImported,
  stages,
}: {
  slug: string;
  onClose: () => void;
  onImported: (imported: number, paths: string[]) => void;
  stages: { stage_number: number; stage_name: string }[];
}) {
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const [existing, setExisting] = useState<RawUploadEntry[] | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  // Track which uploaded filenames the operator has attached to the
  // current shooter's project this session, plus any inflight / error
  // state. Persistent attachment lives on match.json (raw_videos[]);
  // this state is just the UI flash so the operator sees the action
  // succeed before closing the modal.
  const [attachState, setAttachState] = useState<
    Record<
      string,
      | { status: "attaching" }
      | { status: "attached" }
      | { status: "error"; message: string }
    >
  >({});
  // Client-side probe results keyed by filename. Populated when the file
  // is enqueued so attach-after-upload still has duration + recorded_start.
  const probeByFilenameRef = useRef<
    Record<string, { duration_s: number | null; recorded_start: string | null }>
  >({});
  // Server-suggested coverage keyed by filename. Populated after upload
  // success via suggestCoverage.
  const [suggestionByFilename, setSuggestionByFilename] = useState<Record<string, number[]>>({});
  // User-selected coverage keyed by filename. Pre-filled from suggestion.
  const [coverageByFilename, setCoverageByFilename] = useState<Record<string, number[]>>({});

  // Initial list -- so the surface opens with a real "you've already
  // uploaded X" view instead of looking empty until the operator
  // touches something.
  useEffect(() => {
    let alive = true;
    api
      .listRawUploads()
      .then((r) => {
        if (alive) setExisting(r.uploads);
      })
      .catch(() => {
        if (alive) setExisting([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  // Refresh the existing list whenever any upload finishes so the
  // user sees their freshly-uploaded entry land in the bottom panel.
  const refreshExisting = useCallback(async () => {
    try {
      const r = await api.listRawUploads();
      setExisting(r.uploads);
    } catch {
      // Non-fatal -- the just-completed upload is still in the
      // pending list, the user knows it succeeded.
    }
  }, []);

  const updateOne = useCallback(
    (id: string, patch: Partial<PendingUpload>) => {
      setUploads((prev) =>
        prev.map((u) => (u.id === id ? { ...u, ...patch } : u)),
      );
    },
    [],
  );

  const enqueue = useCallback(
    (files: FileList | File[]) => {
      const next: PendingUpload[] = [];
      for (const f of Array.from(files)) {
        next.push({
          id: crypto.randomUUID(),
          file: f,
          status: "queued",
          bytesSent: 0,
        });
        // Probe duration + recorded_start client-side so the data is
        // ready when the user clicks Attach after upload finishes.
        void probeFile(f).then((result) => {
          probeByFilenameRef.current[f.name] = result;
        });
      }
      setUploads((prev) => [...prev, ...next]);
    },
    [],
  );

  // Pump the queue: for every upload still in ``queued``, kick off the
  // upload. Runs serially per file (one XHR at a time) so a slow uplink
  // doesn't get starved by concurrent multi-hundred-MB transfers. The
  // browser opens one TCP connection per upload anyway and S3
  // backpressures the rest.
  //
  // No client-side hashing: footage runs to many GB, and hashing the
  // whole file in the browser (one ``arrayBuffer`` + ``crypto.subtle``)
  // blocks/OOMs the tab. The server computes its own digest on receipt,
  // so the integrity check lives there.
  useEffect(() => {
    const next = uploads.find((u) => u.status === "queued");
    if (!next) return;
    let cancelled = false;

    void (async () => {
      const controller = new AbortController();
      updateOne(next.id, {
        status: "uploading",
        bytesSent: 0,
        controller,
      });
      try {
        await api.uploadRawFile(next.file, {
          signal: controller.signal,
          onProgress: (loaded) => {
            if (cancelled) return;
            updateOne(next.id, { bytesSent: loaded });
          },
        });
        if (cancelled) return;
        updateOne(next.id, {
          status: "done",
          bytesSent: next.file.size,
        });
        await refreshExisting();
        // After upload completes, fetch a coverage suggestion using the
        // client-side probe data. Non-fatal: coverage stays empty if this
        // fails or returns no stages.
        const probe = probeByFilenameRef.current[next.file.name];
        if (probe && stages.length > 0) {
          void api
            .suggestCoverage(slug, {
              recorded_start: probe.recorded_start,
              duration_s: probe.duration_s,
            })
            .then((s) => {
              if (cancelled || s.covers_stages.length === 0) return;
              const suggestion = s.covers_stages;
              setSuggestionByFilename((prev) => ({
                ...prev,
                [next.file.name]: suggestion,
              }));
              // Pre-fill coverage from suggestion if the user hasn't set
              // anything yet for this file.
              setCoverageByFilename((prev) => ({
                ...prev,
                [next.file.name]: prev[next.file.name] ?? suggestion,
              }));
            })
            .catch(() => {
              /* non-fatal */
            });
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.detail === "upload cancelled") {
          updateOne(next.id, { status: "cancelled" });
          return;
        }
        const msg = err instanceof ApiError ? err.detail : String(err);
        updateOne(next.id, { status: "error", errorMessage: msg });
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploads.length, uploads.find((u) => u.status === "queued")?.id]);

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) enqueue(e.target.files);
    // Reset so picking the same file twice in a row still fires.
    e.target.value = "";
  };

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      enqueue(e.dataTransfer.files);
    }
  };

  const cancel = (id: string) => {
    const u = uploads.find((x) => x.id === id);
    if (u?.controller) u.controller.abort();
  };

  const removeUploaded = async (filename: string) => {
    try {
      await api.deleteRawUpload(filename);
      await refreshExisting();
    } catch {
      // Surface inline -- a delete failure is non-fatal; the operator
      // can retry. We don't blow away the row.
    }
  };

  const attachToProject = useCallback(
    async (entry: RawUploadEntry, coverage: number[]) => {
      setAttachState((prev) => ({
        ...prev,
        [entry.filename]: { status: "attaching" },
      }));
      try {
        const probe = probeByFilenameRef.current[entry.filename];
        await api.attachRawVideo(slug, {
          filename: entry.filename,
          sha256: entry.etag,
          size_bytes: entry.size,
          covers_stages: coverage.length > 0 ? coverage : undefined,
          duration_seconds: probe?.duration_s ?? undefined,
          recorded_start: probe?.recorded_start ?? undefined,
        });
        setAttachState((prev) => ({
          ...prev,
          [entry.filename]: { status: "attached" },
        }));
        // The video now lives in unassigned_videos on the project; tell
        // the parent so the ingest page refreshes and the operator sees
        // the new row in the tray when they close the modal. The hosted
        // upload path uses the storage key as the path placeholder; the
        // caller uses this for the batch-move banner (B1) if needed.
        onImported(1, [entry.filename]);
      } catch (err) {
        const msg = err instanceof ApiError ? err.detail : String(err);
        setAttachState((prev) => ({
          ...prev,
          [entry.filename]: { status: "error", message: msg },
        }));
      }
    },
    [slug, onImported],
  );

  const inFlight = uploads.some(
    (u) => u.status === "queued" || u.status === "uploading",
  );

  // Escape / focus trap / restore; Escape locked while uploads run.
  useDialogFocus(true, panelRef, onClose, { disableEscape: inFlight });

  return (
    <Portal>
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Upload raw footage"
      className="fixed inset-0 z-modal flex items-center justify-center bg-bg/70 p-4 backdrop-blur-sm"
      onClick={!inFlight ? onClose : undefined}
    >
      <div
        ref={panelRef}
        className="relative flex h-[min(720px,90vh)] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-rule-strong bg-surface text-ink shadow-[0_24px_48px_-12px_rgba(0,0,0,0.7)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-4 border-b border-rule px-5 py-3.5">
          <div>
            <h2 className="font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
              Upload raw footage
            </h2>
            <p className="mt-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
              Files land in your hosted storage. Attach to this project
              to drop them into the unassigned tray.
            </p>
          </div>
          {!inFlight && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded-md p-1.5 text-subtle hover:bg-surface-2 hover:text-ink"
            >
              <X className="size-4" />
            </button>
          )}
        </header>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4">
          {/* Dropzone */}
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={onDrop}
            className={cn(
              "flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors",
              isDragging
                ? "border-led bg-led-tint"
                : "border-rule bg-surface-2 hover:border-rule-strong",
            )}
          >
            <FolderOpen className="size-6 text-muted" />
            <div className="font-display text-[0.8125rem] font-bold uppercase tracking-[0.08em] text-ink">
              Drop video files here
            </div>
            <div className="font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
              or
            </div>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="rounded-md border border-rule-strong bg-surface px-3 py-1.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:border-led-deep hover:bg-led-tint hover:text-led"
            >
              Choose files...
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="video/*"
              onChange={onPick}
              className="hidden"
            />
          </div>

          {/* Pending queue */}
          {uploads.length > 0 && (
            <section className="flex flex-col gap-2">
              <h3 className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
                This session ({uploads.length})
              </h3>
              <ul className="flex flex-col gap-1.5">
                {uploads.map((u) => (
                  <UploadRow key={u.id} upload={u} onCancel={() => cancel(u.id)} />
                ))}
              </ul>
            </section>
          )}

          {/* Existing uploads */}
          <section className="flex flex-col gap-2">
            <h3 className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
              Already in storage{existing ? ` (${existing.length})` : ""}
            </h3>
            {existing === null ? (
              <p className="font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
                Loading...
              </p>
            ) : existing.length === 0 ? (
              <p className="rounded-md border border-rule bg-surface-2 px-3 py-2 font-mono text-[0.75rem] text-muted">
                Nothing uploaded yet. Files added here persist across
                browser sessions.
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {existing.map((e) => (
                  <ExistingRow
                    key={e.path}
                    entry={e}
                    attachState={attachState[e.filename]}
                    onDelete={() => removeUploaded(e.filename)}
                    onAttach={() =>
                      attachToProject(e, coverageByFilename[e.filename] ?? [])
                    }
                    stages={stages}
                    coverage={coverageByFilename[e.filename] ?? []}
                    suggestion={suggestionByFilename[e.filename] ?? []}
                    onCoverageChange={(v) =>
                      setCoverageByFilename((prev) => ({
                        ...prev,
                        [e.filename]: v,
                      }))
                    }
                  />
                ))}
              </ul>
            )}
          </section>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-rule bg-surface-2 px-5 py-3.5">
          <button
            type="button"
            onClick={onClose}
            disabled={inFlight}
            className="rounded-md bg-led-fill px-3.5 py-1.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink shadow-[0_0_0_1px_var(--color-led-fill),0_0_18px_var(--color-led-glow)] hover:bg-led disabled:opacity-50 disabled:shadow-none"
          >
            {inFlight ? "Uploading..." : "Done"}
          </button>
        </footer>
      </div>
    </div>
    </Portal>
  );
}

function UploadRow({
  upload,
  onCancel,
}: {
  upload: PendingUpload;
  onCancel: () => void;
}) {
  const pct =
    upload.file.size > 0
      ? Math.min(100, Math.round((upload.bytesSent / upload.file.size) * 100))
      : 0;
  return (
    <li className="rounded-md border border-rule bg-surface-2 px-3 py-2">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[0.75rem] text-ink">
            {upload.file.name}
          </div>
          <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            {formatBytes(upload.file.size)}
            {upload.status === "queued" && " . queued"}
            {upload.status === "uploading" && ` . ${pct}%`}
            {upload.status === "done" && " . done"}
            {upload.status === "cancelled" && " . cancelled"}
            {upload.status === "error" && (
              <span className="text-led-text"> . {upload.errorMessage}</span>
            )}
          </div>
        </div>
        {(upload.status === "queued" || upload.status === "uploading") && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md p-1 text-subtle hover:bg-surface-3 hover:text-ink"
            aria-label="Cancel upload"
          >
            <X className="size-3.5" />
          </button>
        )}
        {upload.status === "done" && (
          <span
            aria-hidden
            className="inline-flex size-5 items-center justify-center rounded-full bg-done text-bg"
          >
            <Check className="size-3" strokeWidth={3} />
          </span>
        )}
      </div>
      {upload.status === "uploading" && (
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-surface">
          <div
            className="h-full bg-led transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </li>
  );
}

function ExistingRow({
  entry,
  attachState,
  onDelete,
  onAttach,
  stages,
  coverage,
  suggestion,
  onCoverageChange,
}: {
  entry: RawUploadEntry;
  attachState:
    | { status: "attaching" }
    | { status: "attached" }
    | { status: "error"; message: string }
    | undefined;
  onDelete: () => void;
  onAttach: () => void;
  stages: { stage_number: number; stage_name: string }[];
  coverage: number[];
  suggestion: number[];
  onCoverageChange: (v: number[]) => void;
}) {
  const isAttaching = attachState?.status === "attaching";
  const isAttached = attachState?.status === "attached";
  const attachError =
    attachState?.status === "error" ? attachState.message : null;
  return (
    <li className="rounded-md border border-rule bg-surface-2">
      {/* File info row */}
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[0.75rem] text-ink">
            {entry.filename}
          </div>
          <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            {formatBytes(entry.size)}
            {entry.last_modified && ` . ${formatRelative(entry.last_modified)}`}
            {isAttached && (
              <span className="text-done"> . attached to project</span>
            )}
            {attachError && (
              <span className="text-led-text"> . {attachError}</span>
            )}
          </div>
        </div>
        {isAttached ? (
          <span
            aria-hidden
            className="inline-flex size-5 items-center justify-center rounded-full bg-done text-bg"
          >
            <Check className="size-3" strokeWidth={3} />
          </span>
        ) : (
          <button
            type="button"
            onClick={onAttach}
            disabled={isAttaching}
            aria-label={`Attach ${entry.filename} to project`}
            className="rounded-md border border-rule-strong bg-surface px-2.5 py-1 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:border-led-deep hover:bg-led-tint hover:text-led disabled:opacity-50"
          >
            {isAttaching ? "Attaching..." : "Attach"}
          </button>
        )}
        <button
          type="button"
          onClick={onDelete}
          disabled={isAttaching}
          aria-label={`Delete ${entry.filename}`}
          className="rounded-md p-1 text-subtle hover:bg-led-tint hover:text-led-text disabled:opacity-50"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
      {/* Coverage select - only when not attached and there are stages */}
      {!isAttached && stages.length > 0 && (
        <div className="border-t border-rule px-3 pb-2.5 pt-2">
          <div className="mb-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.12em] text-subtle">
            Covers stages
          </div>
          <CoverageSelect
            stages={stages}
            value={coverage}
            onChange={onCoverageChange}
            suggested={suggestion}
          />
        </div>
      )}
    </li>
  );
}

function shooterInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const secs = Math.max(0, Math.round((now - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}
