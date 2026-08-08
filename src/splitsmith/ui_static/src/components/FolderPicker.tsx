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
import { formatBytes } from "@/lib/format";
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
    !listing ||
    error != null ||
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
    // While the path editor is open, Escape belongs to it (it exits edit
    // mode and restores the breadcrumb bar) - the dialog-level Escape
    // must not also fire, or the first press would close the whole
    // dialog instead of just canceling the edit. A second Escape (once
    // editingPath is back to false) reaches this handler normally.
    { disableEscape: committing || editingPath },
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
            if (e.key !== "/" || editingPath || committing) return;
            const t = e.target as HTMLElement;
            if (t.tagName === "INPUT" || t.tagName === "TEXTAREA") return;
            e.preventDefault();
            setEditingPath(true);
          }}
        >
          <span className="sr-only" role="status" aria-live="polite">
            {committing ? commit.label : busy ? "Reading folder..." : ""}
          </span>
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
                  error != null
                    ? "This folder could not be read"
                    : !allowEmptyFolder && selectedCount === 0 && videosHere === 0
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

  // Restore focus to the pencil affordance whenever edit mode closes
  // (Escape or Cancel), matching the dialog-focus restore-on-close
  // convention instead of leaving focus stranded on an unmounted input.
  const pencilRef = useRef<HTMLButtonElement | null>(null);
  const wasEditingRef = useRef(editing);
  useEffect(() => {
    if (wasEditingRef.current && !editing) {
      pencilRef.current?.focus();
    }
    wasEditingRef.current = editing;
  }, [editing]);

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
              // Peel edit mode only - the parent gates useDialogFocus's
              // Escape-to-close on editingPath (disableEscape), so the
              // document-level handler is inert while we're here and
              // this is the only thing that runs. A second Escape,
              // pressed once editingPath is back to false, reaches the
              // dialog's own handler and closes it.
              e.preventDefault();
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
        ref={pencilRef}
        type="button"
        onClick={onEditStart}
        aria-label="Edit path"
        title='Type a path (or press "/")'
        disabled={busy}
        className="rounded-md p-1.5 text-subtle hover:bg-surface-2 hover:text-ink disabled:opacity-50"
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

const STORAGE_OPTIONS = [
  ["symlink", "Reference in place"],
  ["copy", "Copy into project"],
] as const;

/** Symlink-vs-copy storage choice, rendered in the footer for the
 *  add-footage call site only. Buttons toggle between reference-in-place
 *  and copy-into-project modes.
 *
 *  Roving tabindex per the APG radio group pattern: only the checked
 *  option is in the tab order; any arrow key moves focus to (and
 *  selects) the other option since there are exactly two. */
function StorageToggle({
  value,
  onChange,
  disabled,
}: {
  value: "symlink" | "copy";
  onChange: (mode: "symlink" | "copy") => void;
  disabled: boolean;
}) {
  const btnRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
    e.preventDefault();
    // Only two options - every arrow direction moves to the other one.
    const nextIndex = index === 0 ? 1 : 0;
    onChange(STORAGE_OPTIONS[nextIndex][0]);
    btnRefs.current[nextIndex]?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-label="Storage mode"
      className="inline-flex rounded-full border border-rule bg-surface p-0.5"
    >
      {STORAGE_OPTIONS.map(([mode, label], index) => (
        <button
          key={mode}
          ref={(el) => {
            btnRefs.current[index] = el;
          }}
          type="button"
          role="radio"
          aria-checked={value === mode}
          tabIndex={value === mode ? 0 : -1}
          disabled={disabled}
          onClick={() => onChange(mode)}
          onKeyDown={(e) => handleKeyDown(e, index)}
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

function VideoRowMulti({
  slug,
  entry,
  fullPath,
  checked,
  busy,
  inMatchWindow,
  onToggle,
  onProbed,
}: {
  slug: string;
  entry: FsEntry;
  fullPath: string;
  checked: boolean;
  busy: boolean;
  inMatchWindow: boolean;
  onToggle: () => void;
  onProbed: (duration: number | null, thumbnail_url: string | null) => void;
}) {
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [probing, setProbing] = useState(false);
  const liRef = useRef<HTMLLIElement | null>(null);

  const ensureProbe = useCallback(async () => {
    if (entry.duration != null && entry.thumbnail_url != null) return;
    if (probing) return;
    setProbing(true);
    try {
      const r = await api.probeFile(slug, fullPath);
      onProbed(r.duration, r.thumbnail_url);
    } catch {
      // Best effort; leave fields null so the row still shows what it can.
    } finally {
      setProbing(false);
    }
  }, [entry.duration, entry.thumbnail_url, fullPath, onProbed, probing, slug]);

  return (
    <li
      ref={liRef}
      onMouseEnter={() => {
        setRect(liRef.current?.getBoundingClientRect() ?? null);
        void ensureProbe();
      }}
      onMouseLeave={() => setRect(null)}
    >
      <label
        className={cn(
          "flex cursor-pointer items-center justify-between gap-2 border-l-2 border-l-transparent px-3 py-2 text-sm hover:bg-surface-3/40",
          checked && "bg-surface-3/30",
          inMatchWindow && !checked && "border-l-status-info bg-status-info/5",
          inMatchWindow && checked && "border-l-status-info",
        )}
        title={inMatchWindow ? "Modified during the match window" : undefined}
      >
        <span className="flex min-w-0 items-center gap-2">
          <input
            type="checkbox"
            className="size-4 accent-led"
            checked={checked}
            onChange={onToggle}
            disabled={busy}
            aria-label={`Select ${entry.name}`}
          />
          <Film className="size-4 shrink-0 text-muted" />
          <span className="truncate font-mono text-xs">{entry.name}</span>
        </span>
        <span className="flex shrink-0 items-center gap-3 text-xs text-muted tabular-nums">
          {entry.mtime != null ? <span>{formatMtime(entry.mtime)}</span> : null}
          {entry.duration != null ? <span>{formatDuration(entry.duration)}</span> : null}
          {entry.size_bytes != null ? <span>{formatBytes(entry.size_bytes)}</span> : null}
        </span>
      </label>
      {rect && entry.thumbnail_url ? (
        <ThumbnailFloat anchor={rect} src={entry.thumbnail_url} alt={entry.name} />
      ) : null}
    </li>
  );
}

function ThumbnailFloat({ anchor, src, alt }: { anchor: DOMRect; src: string; alt: string }) {
  // Fixed positioning escapes the picker's overflow:auto clip so rows near
  // the bottom of the list still render their preview. We anchor the
  // thumbnail to the right edge of the row, flip it to the left if the
  // viewport's right side wouldn't fit, and clamp the vertical position so
  // it never paints off-screen.
  const W = 320; // matches max-w used below
  const H = 192; // h-48 -> 12rem -> 192px; rough cap to keep clamping math simple
  const margin = 8;
  const flipLeft = anchor.right + W + margin > window.innerWidth;
  const left = flipLeft ? Math.max(margin, anchor.left - W - margin) : anchor.right + margin;
  const desiredTop = anchor.top + anchor.height / 2 - H / 2;
  const top = Math.max(margin, Math.min(window.innerHeight - H - margin, desiredTop));
  return (
    <div
      role="presentation"
      style={{ position: "fixed", top, left, width: W, zIndex: 50 }}
      className="pointer-events-none rounded-md border border-rule bg-surface-2 p-1 shadow-xl"
    >
      <img src={src} alt={`${alt} thumbnail`} className="w-full rounded" />
    </div>
  );
}

type SortMode = "name" | "date-desc" | "date-asc";

/** Sort directory + video entries together. Directories without an
 *  ``mtime`` fall back to name order so they don't bunch at the
 *  bottom of a date sort. */
function sortEntries<T extends { name: string; mtime: number | null }>(
  entries: T[],
  mode: SortMode,
): T[] {
  if (mode === "name") {
    return [...entries].sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }),
    );
  }
  const factor = mode === "date-desc" ? -1 : 1;
  return [...entries].sort((a, b) => {
    const am = a.mtime;
    const bm = b.mtime;
    if (am == null && bm == null) {
      return a.name.localeCompare(b.name, undefined, { numeric: true });
    }
    if (am == null) return 1; // entries without mtime sink to the end
    if (bm == null) return -1;
    return (am - bm) * factor;
  });
}

function SortHeader({
  mode,
  onChange,
}: {
  mode: SortMode;
  onChange: (next: SortMode) => void;
}) {
  // Click cycles name -> date-desc -> date-asc -> name. Two icons so
  // the user can see at a glance which axis is active without a
  // dropdown.
  const cycle: Record<SortMode, SortMode> = {
    name: "date-desc",
    "date-desc": "date-asc",
    "date-asc": "name",
  };
  const labels: Record<SortMode, string> = {
    name: "Name",
    "date-desc": "Date (newest)",
    "date-asc": "Date (oldest)",
  };
  const icons: Record<SortMode, React.ReactNode> = {
    name: <ArrowDownAZ className="size-3.5" />,
    "date-desc": <ArrowDownNarrowWide className="size-3.5" />,
    "date-asc": <ArrowUpNarrowWide className="size-3.5" />,
  };
  return (
    <div className="flex items-center justify-end gap-2 border-b border-rule px-2 py-1 text-[11px] text-muted">
      <span>Sort:</span>
      <button
        type="button"
        className="flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-surface-3 hover:text-ink"
        onClick={() => onChange(cycle[mode])}
        title="Click to cycle: Name -> Date (newest) -> Date (oldest)"
      >
        {icons[mode]}
        <span>{labels[mode]}</span>
      </button>
    </div>
  );
}

function isInMatchWindow(
  mtime: number | null | undefined,
  win: { startEpoch: number; endEpoch: number } | null,
): boolean {
  if (!win || mtime == null) return false;
  return mtime >= win.startEpoch && mtime <= win.endEpoch;
}

function formatMtime(epochSeconds: number): string {
  // Render in local-time ISO-8601 (``YYYY-MM-DD HH:MM``) so dates sort
  // correctly as strings and don't read as gibberish for users with
  // non-US locales (the previous ``toLocaleDateString`` flipped to
  // ``DD/MM/YY`` or ``YY-MM-DD`` depending on system locale, which made
  // the column harder to scan).
  const d = new Date(epochSeconds * 1000);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "?";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

function buildBreadcrumb(path: string | null): { label: string; path: string }[] {
  if (!path) return [];
  if (path === "/") return [{ label: "/", path: "/" }];
  // Windows paths come through as "C:\..." -- treat the drive as the root.
  const isWin = /^[A-Za-z]:[\\/]/.test(path);
  const segs: { label: string; path: string }[] = [];
  if (isWin) {
    const drive = path.slice(0, 2);
    segs.push({ label: drive, path: drive + "\\" });
    const rest = path.slice(3).split(/[\\/]/).filter(Boolean);
    let acc = drive + "\\";
    for (const part of rest) {
      acc = acc.endsWith("\\") ? acc + part : acc + "\\" + part;
      segs.push({ label: part, path: acc });
    }
    return segs;
  }
  // POSIX
  segs.push({ label: "/", path: "/" });
  const parts = path.split("/").filter(Boolean);
  let acc = "";
  for (const p of parts) {
    acc = `${acc}/${p}`;
    segs.push({ label: p, path: acc });
  }
  return segs;
}

function joinPath(base: string, child: string): string {
  if (/^[A-Za-z]:[\\/]/.test(base)) {
    return base.endsWith("\\") || base.endsWith("/") ? base + child : `${base}\\${child}`;
  }
  return base.endsWith("/") ? base + child : `${base}/${child}`;
}
