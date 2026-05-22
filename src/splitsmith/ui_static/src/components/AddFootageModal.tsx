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
import { useCallback, useEffect, useState } from "react";

import { FolderPicker } from "@/components/FolderPicker";
import {
  ApiError,
  api,
  type ScanResponse,
} from "@/lib/api";
import { cn } from "@/lib/utils";

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
   *  registered across all sources. */
  onImported: (imported: number) => void;
  /** Fires when the user changes the storage mode in the modal, so the
   *  parent can remember their pick across ingests. Optional. */
  onStorageChange?: (mode: StorageMode) => void;
}

export function AddFootageModal({
  slug,
  initialStorage,
  initialPath,
  onClose,
  onImported,
  onStorageChange,
}: AddFootageModalProps) {
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

  // Esc closes the modal -- but only while in the "queue" phase. Once
  // a scan starts we don't want a stray keystroke to abandon it.
  useEffect(() => {
    if (phase !== "queue") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, phase]);

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
      } catch (e) {
        states[i] = {
          status: "error",
          message: e instanceof ApiError ? e.detail : String(e),
        };
      }
      setScanStates([...states]);
    }

    setPhase("result");
    onImported(totalImported);
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Add footage"
      className="fixed inset-0 z-50 flex items-center justify-center bg-bg/70 p-4 backdrop-blur-sm"
      onClick={phase === "queue" ? onClose : undefined}
    >
      <div
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
