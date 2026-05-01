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

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronRight,
  Clock,
  Film,
  Folder,
  FolderOpen,
  Home,
  Loader2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError, api, type FsListing } from "@/lib/api";
import { cn } from "@/lib/utils";

interface FolderPickerProps {
  initialPath?: string | null;
  onSelect: (path: string) => void;
  onCancel?: () => void;
  /** Render mode: inline (e.g. inside a card) vs. compact. */
  mode?: "inline" | "compact";
}

export function FolderPicker({ initialPath, onSelect, onCancel, mode = "inline" }: FolderPickerProps) {
  const [listing, setListing] = useState<FsListing | null>(null);
  const [path, setPath] = useState<string | null>(initialPath ?? null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (next?: string | null) => {
    setBusy(true);
    setError(null);
    try {
      const data = await api.listFolder(next ?? undefined);
      setListing(data);
      setPath(data.path);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load(initialPath ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const breadcrumb = useMemo(() => buildBreadcrumb(path), [path]);
  const dirEntries = listing?.entries.filter((e) => e.kind === "dir") ?? [];
  const videoEntries = listing?.entries.filter((e) => e.kind === "video") ?? [];
  const videosHere = videoEntries.length;

  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-lg border border-border bg-card",
        mode === "compact" ? "p-3" : "p-4",
      )}
    >
      <PathBar path={path} onChange={(p) => void load(p)} disabled={busy} />

      <div className="flex flex-wrap items-center gap-1 text-sm text-muted-foreground">
        {breadcrumb.map((seg, i) => (
          <span key={`${seg.path}-${i}`} className="flex items-center gap-1">
            {i > 0 ? <ChevronRight className="size-3" /> : null}
            <button
              type="button"
              className="rounded px-1.5 py-0.5 font-mono text-xs hover:bg-accent hover:text-accent-foreground"
              onClick={() => void load(seg.path)}
              disabled={busy}
            >
              {seg.label}
            </button>
          </span>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-[180px_1fr]">
        <aside className="flex flex-col gap-1 text-sm">
          {(listing?.suggested_starts ?? []).slice(0, 6).map((s, i) => (
            <button
              key={s}
              type="button"
              className={cn(
                "flex items-center gap-2 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-accent hover:text-accent-foreground",
                path === s && "bg-accent text-accent-foreground",
              )}
              onClick={() => void load(s)}
              disabled={busy}
              title={s}
            >
              {i === 0 ? <Clock className="size-3.5" /> : <Home className="size-3.5" />}
              <span className="truncate text-xs">{s.split("/").filter(Boolean).pop() || "/"}</span>
            </button>
          ))}
        </aside>

        <div className="min-h-[12rem] rounded-md border border-border bg-background">
          {busy && !listing ? (
            <div className="flex h-full items-center justify-center p-6 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
            </div>
          ) : error ? (
            <div className="p-4 text-sm text-destructive">{error}</div>
          ) : !listing ? null : dirEntries.length === 0 ? (
            <div className="p-4 text-sm text-muted-foreground">
              {videosHere > 0
                ? `No subfolders here. ${videosHere} video${videosHere === 1 ? "" : "s"} ready to scan.`
                : "Empty folder."}
            </div>
          ) : (
            <ul className="max-h-72 divide-y divide-border overflow-y-auto">
              {dirEntries.map((entry) => {
                const childPath = path ? joinPath(path, entry.name) : entry.name;
                return (
                  <li key={entry.name}>
                    <button
                      type="button"
                      className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-accent hover:text-accent-foreground"
                      onClick={() => void load(childPath)}
                      disabled={busy}
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <Folder className="size-4 shrink-0 text-muted-foreground" />
                        <span className="truncate">{entry.name}</span>
                      </span>
                      {entry.video_count ? (
                        <span className="flex items-center gap-1 text-xs text-muted-foreground">
                          <Film className="size-3" />
                          {entry.video_count}
                        </span>
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between gap-2">
        <div className="text-xs text-muted-foreground">
          {videosHere > 0 ? (
            <span className="inline-flex items-center gap-1">
              <Film className="size-3" />
              {videosHere} video{videosHere === 1 ? "" : "s"} in this folder
            </span>
          ) : (
            <span>No videos directly here. Drill into a subfolder.</span>
          )}
        </div>
        <div className="flex gap-2">
          {onCancel ? (
            <Button variant="ghost" type="button" onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
          ) : null}
          <Button
            type="button"
            disabled={busy || !path || videosHere === 0}
            onClick={() => path && onSelect(path)}
            title={
              videosHere === 0
                ? "Select a folder that contains video files."
                : `Use ${path}`
            }
          >
            <FolderOpen />
            Use this folder
          </Button>
        </div>
      </div>
    </div>
  );
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
        className="flex h-9 flex-1 rounded-md border border-input bg-background px-3 py-1 font-mono text-xs shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
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
