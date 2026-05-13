import { Crosshair, FolderOpen, FolderPlus, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { ApiError, api, type RecentProject } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Project picker route (/pick).
 *
 * Rendered when ``splitsmith ui`` boots without ``--project`` so the
 * server is unbound, and reachable from any bound state via the header
 * "Switch project..." action. The picker reads
 * ``GET /api/user/recent-projects``, lets the user filter / pick / forget
 * entries, then binds the chosen project via
 * ``POST /api/user/recent-projects/bind`` and routes to the home page.
 *
 * Keyboard model: ArrowUp/Down moves selection, Enter opens, Cmd/Ctrl+
 * Backspace forgets the selected entry. The filter input owns focus; the
 * page-level keydown listener handles arrows + Enter even when the input
 * has focus so users don't have to mouse around.
 */
export function Pick() {
  const navigate = useNavigate();
  const [recents, setRecents] = useState<RecentProject[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [opening, setOpening] = useState<string | null>(null);
  const [openPath, setOpenPath] = useState("");
  const [importDest, setImportDest] = useState("");
  const [importArchive, setImportArchive] = useState<File | null>(null);
  const [importOverwrite, setImportOverwrite] = useState(false);
  const [importing, setImporting] = useState(false);
  const [createPath, setCreatePath] = useState("");
  const [createName, setCreateName] = useState("");
  const [creating, setCreating] = useState(false);
  const filterInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getRecentProjects()
      .then((rs) => {
        if (alive) setRecents(rs);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof ApiError ? e.detail : String(e));
      });
    return () => {
      alive = false;
    };
  }, []);

  // Auto-focus the filter on mount: most users either type to find or
  // hit Enter to open the most-recent.
  useEffect(() => {
    filterInputRef.current?.focus();
  }, []);

  const filtered = useMemo(() => {
    if (!recents) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return recents;
    return recents.filter(
      (r) =>
        r.name.toLowerCase().includes(q) || r.path.toLowerCase().includes(q),
    );
  }, [recents, filter]);

  // Keep selection in range as the filter narrows the list.
  useEffect(() => {
    if (selectedIdx >= filtered.length) {
      setSelectedIdx(Math.max(0, filtered.length - 1));
    }
  }, [filtered.length, selectedIdx]);

  async function open(target: RecentProject) {
    setOpening(target.path);
    setError(null);
    try {
      await api.bindProject(target.path, target.name);
      navigate("/", { replace: true });
    } catch (e: unknown) {
      setOpening(null);
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function forget(target: RecentProject) {
    try {
      const resp = await api.forgetRecentProject(target.path);
      setRecents(resp.projects);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function openExplicitPath() {
    const trimmed = openPath.trim();
    if (!trimmed) return;
    setOpening(trimmed);
    setError(null);
    try {
      await api.bindProject(trimmed);
      navigate("/", { replace: true });
    } catch (e: unknown) {
      setOpening(null);
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function runImport() {
    if (!importArchive || !importDest.trim()) return;
    setImporting(true);
    setError(null);
    try {
      await api.importProject(importArchive, importDest.trim(), {
        overwrite: importOverwrite,
        bind: true,
      });
      navigate("/", { replace: true });
    } catch (e: unknown) {
      setImporting(false);
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function createProject() {
    const path = createPath.trim();
    if (!path) return;
    setCreating(true);
    setError(null);
    try {
      await api.bindProject(path, createName.trim() || undefined, { create: true });
      navigate("/", { replace: true });
    } catch (e: unknown) {
      setCreating(false);
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (filtered.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIdx((i) => Math.min(filtered.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIdx((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      void open(filtered[selectedIdx]);
    } else if ((e.metaKey || e.ctrlKey) && e.key === "Backspace") {
      e.preventDefault();
      void forget(filtered[selectedIdx]);
    }
  }

  return (
    <div
      className="flex min-h-screen flex-col bg-background text-foreground"
      onKeyDown={onKeyDown}
    >
      <header className="flex h-14 items-center gap-3 border-b border-border px-6">
        <Crosshair className="size-5 text-primary" />
        <div className="flex flex-col">
          <div className="text-sm font-semibold tracking-tight">splitsmith</div>
          <div className="text-xs text-muted-foreground">
            Pick a match project to open
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-8">
        <div className="space-y-4">
          <input
            ref={filterInputRef}
            type="text"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setSelectedIdx(0);
            }}
            placeholder="Filter by name or path..."
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none ring-offset-background focus:ring-2 focus:ring-ring"
          />

          {error ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          ) : null}

          {recents === null ? (
            <div className="text-sm text-muted-foreground">Loading...</div>
          ) : filtered.length === 0 ? (
            <EmptyState hasRecents={recents.length > 0} filtering={!!filter} />
          ) : (
            <ul className="divide-y divide-border rounded-md border border-border bg-card">
              {filtered.map((r, idx) => (
                <ProjectRow
                  key={r.path}
                  project={r}
                  selected={idx === selectedIdx}
                  busy={opening === r.path}
                  onOpen={() => open(r)}
                  onForget={() => forget(r)}
                  onHover={() => setSelectedIdx(idx)}
                />
              ))}
            </ul>
          )}

          <div className="rounded-md border border-border bg-card p-4">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium">
              <Upload className="size-4" />
              Import from backup
            </div>
            <p className="mb-2 text-xs text-muted-foreground">
              Restore a <code className="font-mono text-xs">.tar.gz</code> produced
              by the Download backup button. The archive's top-level folder
              is restored under the destination directory.
            </p>
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                void runImport();
              }}
            >
              <input
                type="file"
                accept=".tar.gz,.tgz,application/gzip,application/x-tar"
                onChange={(e) =>
                  setImportArchive(e.target.files?.[0] ?? null)
                }
                className="block w-full text-xs"
              />
              <div className="flex gap-2">
                <input
                  type="text"
                  value={importDest}
                  onChange={(e) => setImportDest(e.target.value)}
                  placeholder="Destination directory (e.g. /Volumes/X9/matches)"
                  className="flex-1 rounded-md border border-input bg-background px-3 py-1.5 font-mono text-xs outline-none focus:ring-2 focus:ring-ring"
                />
                <Button
                  type="submit"
                  size="sm"
                  disabled={
                    !importArchive || !importDest.trim() || importing
                  }
                >
                  {importing ? "Importing..." : "Import"}
                </Button>
              </div>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  checked={importOverwrite}
                  onChange={(e) => setImportOverwrite(e.target.checked)}
                />
                Overwrite if the target folder already exists
              </label>
            </form>
          </div>

          <div className="rounded-md border border-border bg-card p-4">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium">
              <FolderPlus className="size-4" />
              Create a new project
            </div>
            <p className="mb-2 text-xs text-muted-foreground">
              Paste an absolute path. If the folder does not exist it is
              created. Standard subdirs (audit, scoreboard, trimmed, ...)
              and an empty <code className="font-mono text-xs">project.json</code>{" "}
              are scaffolded; the project opens immediately.
            </p>
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                void createProject();
              }}
            >
              <div className="flex gap-2">
                <input
                  type="text"
                  value={createPath}
                  onChange={(e) => setCreatePath(e.target.value)}
                  placeholder="/Volumes/X9/matches/new-match"
                  className="flex-1 rounded-md border border-input bg-background px-3 py-1.5 font-mono text-xs outline-none focus:ring-2 focus:ring-ring"
                />
                <Button
                  type="submit"
                  size="sm"
                  disabled={!createPath.trim() || creating}
                >
                  {creating ? "Creating..." : "Create"}
                </Button>
              </div>
              <input
                type="text"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder="Display name (optional, defaults to folder name)"
                className="w-full rounded-md border border-input bg-background px-3 py-1.5 text-xs outline-none focus:ring-2 focus:ring-ring"
              />
            </form>
          </div>

          <div className="rounded-md border border-border bg-card p-4">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium">
              <FolderOpen className="size-4" />
              Open a folder by path
            </div>
            <p className="mb-2 text-xs text-muted-foreground">
              Paste an absolute path to an existing project directory.
              Pointing at an existing folder without a{" "}
              <code className="font-mono text-xs">project.json</code> scaffolds
              one in place. Use the create form above for a fresh folder.
            </p>
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void openExplicitPath();
              }}
            >
              <input
                type="text"
                value={openPath}
                onChange={(e) => setOpenPath(e.target.value)}
                placeholder="/Users/you/matches/..."
                className="flex-1 rounded-md border border-input bg-background px-3 py-1.5 font-mono text-xs outline-none focus:ring-2 focus:ring-ring"
              />
              <Button type="submit" size="sm" disabled={!openPath.trim()}>
                Open
              </Button>
            </form>
          </div>

          <div className="text-xs text-muted-foreground">
            <kbd className="rounded border border-border bg-muted px-1">Up</kbd>
            /
            <kbd className="rounded border border-border bg-muted px-1">Down</kbd>{" "}
            to select,{" "}
            <kbd className="rounded border border-border bg-muted px-1">
              Enter
            </kbd>{" "}
            to open,{" "}
            <kbd className="rounded border border-border bg-muted px-1">
              Cmd
            </kbd>
            +
            <kbd className="rounded border border-border bg-muted px-1">
              Backspace
            </kbd>{" "}
            to forget.
          </div>
        </div>
      </main>
    </div>
  );
}

function ProjectRow({
  project,
  selected,
  busy,
  onOpen,
  onForget,
  onHover,
}: {
  project: RecentProject;
  selected: boolean;
  busy: boolean;
  onOpen: () => void;
  onForget: () => void;
  onHover: () => void;
}) {
  const lastOpened = useMemo(
    () => formatRelative(new Date(project.last_opened_at)),
    [project.last_opened_at],
  );

  return (
    <li
      className={cn(
        "flex items-center gap-3 px-4 py-3 transition-colors",
        selected ? "bg-accent" : "hover:bg-accent/40",
      )}
      onMouseEnter={onHover}
      onClick={onOpen}
      role="button"
      tabIndex={-1}
    >
      <div className="flex-1 min-w-0">
        <div className="font-medium tracking-tight truncate">
          {project.name}
        </div>
        <div className="font-mono text-xs text-muted-foreground truncate">
          {project.path}
        </div>
      </div>
      <div className="text-xs text-muted-foreground whitespace-nowrap">
        {busy ? "opening..." : `opened ${lastOpened}`}
      </div>
      <button
        type="button"
        title="Forget this project"
        className="rounded p-1.5 text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
        onClick={(e) => {
          e.stopPropagation();
          onForget();
        }}
      >
        <Trash2 className="size-4" />
      </button>
    </li>
  );
}

function EmptyState({
  hasRecents,
  filtering,
}: {
  hasRecents: boolean;
  filtering: boolean;
}) {
  if (filtering) {
    return (
      <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
        No projects match the filter.
      </div>
    );
  }
  return (
    <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
      {hasRecents
        ? "All projects filtered out."
        : (
            <>
              No projects yet. Open one by pasting a path below, or run{" "}
              <code className="font-mono text-xs">
                splitsmith ui --project &lt;path&gt;
              </code>{" "}
              from your shell.
            </>
          )}
    </div>
  );
}

/** Compact relative time ("2 min ago", "3 days ago"). Intentionally
 *  small -- a heavy date library isn't worth pulling in for one row. */
function formatRelative(then: Date): string {
  const now = Date.now();
  const ms = now - then.getTime();
  const sec = Math.round(ms / 1000);
  if (sec < 45) return "just now";
  const min = Math.round(sec / 60);
  if (min < 45) return `${min} min ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} hr ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} day${day === 1 ? "" : "s"} ago`;
  return then.toLocaleDateString();
}
