/**
 * FolderPicker — server-side directory browser for selecting a video folder.
 *
 * Uses GET /api/fs/list (server has full filesystem access; we don't try to
 * use the browser's File System Access API because that doesn't expose
 * server-side absolute paths needed for our symlink workflow).
 *
 * UX:
 *   - Breadcrumb at top, click any segment to jump up
 *   - Sidebar of bookmarks (last-scanned, ~/Movies, ~/Videos, ~/Downloads, home)
 *   - Subdirectory list with video counts ("match-day · 7 videos")
 *   - "Use this folder" confirms the current path
 *   - Path input visible at top for keyboard / paste workflows
 *   - Keyboard navigable: Tab through, Enter on a row to drill in
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

interface FolderPickerProps {
  /** Shooter slug for shooter-scoped fs endpoints. Required when
   *  ``unbound !== true``; the picker browses the host filesystem
   *  boundary-checked against this shooter's project root. Ignored
   *  when ``unbound`` is true. */
  slug?: string;
  /** When true the picker uses :func:`api.listFolderUnbound` (no
   *  project required, no video probing) instead of the shooter-bound
   *  endpoint. Used by the create-match flow's parent-folder picker
   *  which runs before any project exists. */
  unbound?: boolean;
  /** Which row kinds to render in the listing. Mirrors the design
   *  brief's ``mode`` prop; renamed locally to avoid colliding with
   *  the legacy ``mode: "inline" | "compact"`` density toggle below.
   *
   *  ``directories``        Show subfolders only. Video files are
   *                         silently hidden -- the caller is picking a
   *                         parent dir, not files within it.
   *  ``directories+files``  Default. Subfolders + video files. When
   *                         combined with ``onSelectFiles`` /
   *                         ``autoCommitFiles`` the file rows render
   *                         with checkboxes.
   */
  contentMode?: "directories" | "directories+files";
  /** Render chrome. ``inline`` returns a card-shaped block suitable
   *  for embedding inside a page or another modal. ``modal`` wraps
   *  the body in a fixed-position dialog with backdrop, header (title
   *  + close button) and footer (Cancel + primary action). */
  shell?: "inline" | "modal";
  /** Modal-specific chrome. Only used when ``shell === "modal"``. */
  modalTitle?: string;
  modalSubtitle?: string;
  initialPath?: string | null;
  onSelect: (path: string) => void;
  /** Optional callback for multi-file selection. When provided, video rows
   * gain a checkbox and the action button switches to "Use N files" when any
   * files are selected. The callback receives the selected files with their
   * filesystem mtime so the parent can pre-fill date hints. */
  onSelectFiles?: (files: { path: string; mtime: number | null }[]) => void;
  /** When true, file checkboxes auto-commit on every toggle: the picker
   *  calls :prop:`onFolderFilesChange` with the current folder and the
   *  current selection (including empty selections) each time. The
   *  "Use N files" footer button is hidden because commit is implicit.
   *  Used by the Add-Footage modal where the picker feeds a queue
   *  instead of a one-shot import. */
  autoCommitFiles?: boolean;
  /** Callback for the auto-commit mode. Fires on every selection
   *  change, including empty -> the caller maintains one queue entry
   *  per folder and removes it when ``files`` is empty. */
  onFolderFilesChange?: (folder: string, files: { path: string; mtime: number | null }[]) => void;
  onCancel?: () => void;
  /** Render mode: inline (e.g. inside a card) vs. compact. */
  mode?: "inline" | "compact";
  /** Optional match window (epoch seconds, inclusive). Files whose
   *  ``mtime`` falls inside this window are highlighted as likely
   *  candidates so the user can spot them in a folder full of mixed
   *  clips. Computed by the caller from the project's stage analysis;
   *  null when no scoreboard times are loaded yet. The window already
   *  includes whatever margin the caller wants (typically a couple of
   *  hours on each side to cover warm-up + drive home with the cam). */
  matchWindow?: { startEpoch: number; endEpoch: number } | null;
  /** When true, the "Use this folder" button stays enabled even when
   *  the current folder has no direct video children. Set by callers
   *  that walk recursively (e.g. relink scan), where the user is
   *  expected to pick a top-level folder whose videos live in
   *  subdirectories. */
  allowEmptyFolder?: boolean;
  /** Override the action button label. Default: "Use this folder". */
  selectLabel?: string;
  /** Reports the picker's current path each time the operator navigates
   *  to a new folder. Used by the Add-Footage modal to compute the
   *  folder-vs-file mutex flags below. */
  onPathChange?: (path: string | null) => void;
  /** Disable the "Use this folder" CTA (and surface the reason). Used
   *  when the current folder is mutually exclusive with something the
   *  caller already has -- e.g. the queue already has individual files
   *  picked inside it, so importing the whole folder would double up. */
  addWholeFolderDisabled?: boolean;
  addWholeFolderDisabledReason?: string;
  /** Disable file checkboxes (and the select-all helpers). Used when the
   *  current folder is already queued as a whole-folder source. */
  filesDisabled?: boolean;
  filesDisabledReason?: string;
}

export function FolderPicker({
  slug,
  unbound = false,
  contentMode = "directories+files",
  shell = "inline",
  modalTitle,
  modalSubtitle,
  initialPath,
  onSelect,
  onSelectFiles,
  autoCommitFiles = false,
  onFolderFilesChange,
  onCancel,
  mode = "inline",
  matchWindow = null,
  allowEmptyFolder = false,
  selectLabel = "Use this folder",
  onPathChange,
  addWholeFolderDisabled = false,
  addWholeFolderDisabledReason,
  filesDisabled = false,
  filesDisabledReason,
}: FolderPickerProps) {
  const [listing, setListing] = useState<FsListing | null>(null);
  const [path, setPath] = useState<string | null>(initialPath ?? null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  // ``name`` keeps directories above videos in alphabetical order (the
  // historical default). ``date-desc`` puts the most recent video at
  // the top, which is what the cam-over-USB workflow wants -- you
  // typically just shot the match, plug in, and want today's clips
  // first. Toggling cycles name -> date-desc -> date-asc -> name.
  const [sortMode, setSortMode] = useState<SortMode>("name");

  // ``directories``-mode pickers skip metadata probing -- they never
  // render video rows, so the thumbnail/duration sidecars would be
  // wasted bandwidth. ``unbound`` pickers also skip (they run before
  // any project exists; no probe endpoint is reachable).
  const wantMetadata =
    !unbound && contentMode === "directories+files" && onSelectFiles !== undefined;

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
        // Reset multi-file selection when navigating to a new directory.
        setSelectedFiles(new Set());
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

  // Surface the current path to the parent. AddFootageModal uses this
  // to compute folder/file mutex flags without owning a duplicate
  // navigation state.
  useEffect(() => {
    onPathChange?.(path);
  }, [path, onPathChange]);

  const breadcrumb = useMemo(() => buildBreadcrumb(path), [path]);
  const dirEntries = useMemo(
    () => sortEntries(listing?.entries.filter((e) => e.kind === "dir") ?? [], sortMode),
    [listing, sortMode],
  );
  // ``directories``-mode pickers don't render video rows at all even
  // when the listing carries them -- the caller is picking a parent
  // folder, not files within it.
  const videoEntries = useMemo(
    () =>
      contentMode === "directories"
        ? []
        : sortEntries(listing?.entries.filter((e) => e.kind === "video") ?? [], sortMode),
    [listing, sortMode, contentMode],
  );
  const videosHere = videoEntries.length;
  const multiFileMode =
    contentMode !== "directories" && onSelectFiles !== undefined;
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

  const confirmFiles = () => {
    if (!path || selectedCount === 0) return;
    const files = videoEntries
      .filter((e) => selectedFiles.has(e.name))
      .map((e) => ({ path: joinPath(path, e.name), mtime: e.mtime }));
    onSelectFiles!(files);
  };

  // Auto-commit mode: surface every selection change to the parent so a
  // queue-style UI (Add-Footage modal) updates in lock-step with the
  // user's clicks. Including the empty-selection case is important --
  // unchecking the last box has to remove the per-folder queue entry,
  // otherwise the user "deselects" but the queue still says it's there.
  useEffect(() => {
    if (!autoCommitFiles || !onFolderFilesChange || !path) return;
    const files = videoEntries
      .filter((e) => selectedFiles.has(e.name))
      .map((e) => ({ path: joinPath(path, e.name), mtime: e.mtime }));
    onFolderFilesChange(path, files);
    // ``videoEntries`` isn't a dep because it derives from ``listing``;
    // listing-driven changes go through the ``load`` reset of
    // selectedFiles, which retriggers this effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFiles, path, autoCommitFiles]);

  // Modal-shell dialog behavior: Escape, focus entry, Tab trap, focus
  // restore. Inline shells sit inside a host dialog that owns these.
  const modalPanelRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus(shell === "modal", modalPanelRef, () => onCancel?.());

  const body = (
    <div
      className={cn(
        "flex flex-col gap-3",
        shell === "modal"
          ? "min-h-0 flex-1"
          : "rounded-lg border border-rule bg-surface",
        shell !== "modal" && (mode === "compact" ? "p-3" : "p-4"),
      )}
    >
      <PathBar path={path} onChange={(p) => void load(p)} disabled={busy} />

      <div className="flex flex-wrap items-center gap-1 text-sm text-muted">
        {breadcrumb.map((seg, i) => (
          <span key={`${seg.path}-${i}`} className="flex items-center gap-1">
            {i > 0 ? <ChevronRight className="size-3" /> : null}
            <button
              type="button"
              className="rounded px-1.5 py-0.5 font-mono text-xs hover:bg-surface-3 hover:text-ink"
              onClick={() => void load(seg.path)}
              disabled={busy}
            >
              {seg.label}
            </button>
          </span>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-[200px_1fr]">
        <aside className="flex flex-col gap-3 text-sm">
          <SuggestedStartsSidebar
            starts={listing?.suggested_starts ?? []}
            currentPath={path}
            disabled={busy}
            onPick={(p) => void load(p)}
          />
        </aside>

        <div className="relative min-h-[12rem] rounded-md border border-rule bg-bg">
          {/* When ``busy && listing`` (we're navigating into a slow
              folder while an old listing is still on screen), overlay a
              translucent spinner instead of swapping the whole panel.
              Keeps the user oriented and signals that the next listing
              is coming. The first-load spinner case below renders
              directly when there's no listing yet. */}
          {busy && listing ? (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-md bg-bg/70 backdrop-blur-[1px]">
              <Loader2 className="size-5 animate-spin text-muted" />
            </div>
          ) : null}
          {busy && !listing ? (
            <div className="flex h-full items-center justify-center gap-2 p-6 text-sm text-muted">
              <Loader2 className="size-4 animate-spin" />
              <span>Reading folder...</span>
            </div>
          ) : error ? (
            <div className="p-4 text-sm text-destructive">{error}</div>
          ) : !listing ? null : dirEntries.length === 0 && videoEntries.length === 0 ? (
            <div className="p-4 text-sm text-muted">Empty folder.</div>
          ) : (
            <>
              <SortHeader mode={sortMode} onChange={setSortMode} />
              <ul className="max-h-80 divide-y divide-rule overflow-y-auto">
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
                      disabled={busy}
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
                        busy={busy || filesDisabled}
                        disabledReason={filesDisabled ? filesDisabledReason : undefined}
                        inMatchWindow={isInMatchWindow(entry.mtime, matchWindow)}
                        onToggle={() => toggleSelect(entry.name)}
                        onProbed={(duration, thumbnail_url) => {
                          // Patch the listing in-place so the row remembers
                          // its on-demand probe result without forcing a
                          // refresh.
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

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs text-muted">
          {videosHere > 0 ? (
            <span className="inline-flex items-center gap-1">
              <Film className="size-3" />
              {videosHere} video{videosHere === 1 ? "" : "s"} in this folder
            </span>
          ) : allowEmptyFolder ? (
            <span>No videos directly here -- subfolders will be scanned.</span>
          ) : (
            <span>No videos directly here. Drill into a subfolder.</span>
          )}
          {multiFileMode && videosHere > 0 ? (
            <button
              type="button"
              className="rounded px-1.5 py-0.5 underline-offset-2 hover:underline disabled:opacity-50"
              onClick={selectedCount === videosHere ? () => setSelectedFiles(new Set()) : selectAll}
              disabled={busy || filesDisabled}
              title={filesDisabled ? filesDisabledReason : undefined}
            >
              {selectedCount === videosHere ? "Clear selection" : "Select all"}
            </button>
          ) : null}
          {multiFileMode && inWindowVideoCount > 0 ? (
            <button
              type="button"
              className="rounded px-1.5 py-0.5 text-status-info underline-offset-2 hover:underline disabled:opacity-50"
              onClick={selectInMatchWindow}
              disabled={busy || filesDisabled}
              title={
                filesDisabled
                  ? filesDisabledReason
                  : "Select videos whose modified time falls inside the match window"
              }
            >
              Select {inWindowVideoCount} in match window
            </button>
          ) : null}
          {filesDisabled && filesDisabledReason ? (
            <span className="text-muted">{filesDisabledReason}</span>
          ) : null}
        </div>
        <div className="flex gap-2">
          {onCancel ? (
            <Button variant="ghost" type="button" onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
          ) : null}
          {multiFileMode && selectedCount > 0 && !autoCommitFiles ? (
            <Button type="button" disabled={busy} onClick={confirmFiles}>
              <FolderOpen />
              Use {selectedCount} file{selectedCount === 1 ? "" : "s"}
            </Button>
          ) : (
            <Button
              type="button"
              disabled={
                busy ||
                !path ||
                (!allowEmptyFolder && videosHere === 0) ||
                addWholeFolderDisabled
              }
              onClick={() => path && onSelect(path)}
              title={
                addWholeFolderDisabled && addWholeFolderDisabledReason
                  ? addWholeFolderDisabledReason
                  : !allowEmptyFolder && videosHere === 0
                    ? "Select a folder that contains video files, or drill in."
                    : `Use ${path}`
              }
            >
              <FolderOpen />
              {selectLabel}
            </Button>
          )}
        </div>
      </div>
    </div>
  );

  // Inline shell: the body is the entire output. Used when the picker
  // is embedded in a page or larger modal (e.g. AddFootageModal).
  if (shell !== "modal") return body;

  // Modal shell: wrap the body in a backdrop + card with a header
  // (title + close) and a footer (path readout + cancel/use buttons).
  // Replaces the legacy DirectoryPickerModal which had ~80% identical
  // code paths; the parent-folder picker in CreateMatch now renders
  // through this branch with ``contentMode="directories"`` and
  // ``unbound``.
  return (
    <Portal>
    <div
      role="dialog"
      aria-modal="true"
      aria-label={modalTitle ?? "Pick a folder"}
      className="fixed inset-0 z-modal flex items-center justify-center bg-bg/70 p-4 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        ref={modalPanelRef}
        className="relative flex h-[min(640px,88vh)] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-rule-strong bg-surface text-ink shadow-[0_24px_48px_-12px_rgba(0,0,0,0.7)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-4 border-b border-rule px-5 py-3.5">
          <div>
            <h2 className="font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
              {modalTitle ?? "Pick a folder"}
            </h2>
            {modalSubtitle && (
              <p className="mt-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
                {modalSubtitle}
              </p>
            )}
          </div>
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              aria-label="Cancel"
              className="rounded-md p-1.5 text-subtle hover:bg-surface-2 hover:text-ink"
            >
              <X className="size-4" />
            </button>
          )}
        </header>
        <div className="flex min-h-0 flex-1 flex-col px-5 py-4">{body}</div>
      </div>
    </div>
    </Portal>
  );
}

/** Sidebar bookmarks, grouped by ``kind`` so the user can scan
 *  recent / home / removable+network sections separately. The wire
 *  shape carries one entry per bookmark; we group client-side to keep
 *  the contract simple. */
function SuggestedStartsSidebar({
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
  const groups: { title: string; kinds: SuggestedStart["kind"][]; }[] = [
    { title: "Recent", kinds: ["recent"] },
    { title: "Home", kinds: ["home"] },
    { title: "Removable & network", kinds: ["removable", "network"] },
  ];
  return (
    <>
      {groups.map((g) => {
        const items = starts.filter((s) => g.kinds.includes(s.kind));
        if (items.length === 0) return null;
        return (
          <div key={g.title} className="space-y-1">
            <div className="px-1 text-[10px] font-medium uppercase tracking-wider text-muted/70">
              {g.title}
            </div>
            {items.map((s) => (
              <button
                key={s.path}
                type="button"
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
        );
      })}
    </>
  );
}

function SidebarIcon({ kind }: { kind: SuggestedStart["kind"] }) {
  const className = "size-3.5 shrink-0";
  if (kind === "recent") return <Clock className={className} />;
  if (kind === "removable") return <HardDrive className={className} />;
  if (kind === "network") return <Cloud className={className} />;
  return <Home className={className} />;
}

function VideoRowMulti({
  slug,
  entry,
  fullPath,
  checked,
  busy,
  disabledReason,
  inMatchWindow,
  onToggle,
  onProbed,
}: {
  slug: string;
  entry: FsEntry;
  fullPath: string;
  checked: boolean;
  busy: boolean;
  /** When set the row is treated as disabled; we surface this as a
   *  tooltip so the operator knows *why* the checkbox is off. */
  disabledReason?: string;
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
          disabledReason && "cursor-not-allowed opacity-50 hover:bg-transparent",
        )}
        title={
          disabledReason
            ? disabledReason
            : inMatchWindow
              ? "Modified during the match window"
              : undefined
        }
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

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unit]}`;
}

function PathBar({
  path,
  onChange,
  disabled,
}: {
  path: string | null;
  onChange: (p: string) => void;
  disabled: boolean;
}) {
  const [draft, setDraft] = useState(path ?? "");
  useEffect(() => {
    setDraft(path ?? "");
  }, [path]);

  return (
    <form
      className="flex gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (draft.trim()) onChange(draft.trim());
      }}
    >
      <input
        type="text"
        className="flex h-9 flex-1 rounded-md border border-rule bg-bg px-3 py-1 font-mono text-xs shadow-sm transition-colors placeholder:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led disabled:cursor-not-allowed disabled:opacity-50"
        placeholder="/path/to/folder"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        disabled={disabled}
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
        aria-label="Folder path"
      />
      <Button type="submit" variant="outline" size="sm" disabled={disabled || !draft.trim()}>
        Go
      </Button>
    </form>
  );
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
