/**
 * MatchPicker route (/pick) -- redesigned in the Shot Timer aesthetic (#322).
 *
 * Carries the same plumbing as the legacy picker (recent-projects list,
 * bind, delete, import-backup) but renders in the polished "Match Archive"
 * style: telemetry readout, search + status chips, match rows with shooter
 * stack + tick progress + status pill.
 *
 * Keyboard model: ArrowUp/Down moves selection, Enter opens,
 * Cmd/Ctrl+Backspace opens the delete confirm for the selected entry,
 * `/` focuses search.
 */

import {
  ArrowRight,
  Crosshair,
  FolderOpen,
  GitMerge,
  Plus,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  AvatarStack,
  Brand,
  Kbd,
  Kicker,
  Readout,
  StatusPill,
  TickStrip,
  type TickState,
} from "@/components/ui";
import { AccountChip } from "@/components/AccountChip";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/useConfirm";
import {
  ApiError,
  api,
  type RecentProjectDetail,
  type ScoreboardIdentity,
  type ServerHealth,
} from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import { cn } from "@/lib/utils";

type StatusFilter = "all" | "awaiting_footage" | "in_progress" | "exported" | "archived";

/** Build the URL the picker should navigate to after a successful bind.
 *
 * Prefers the match-id-prefixed home (#353 Phase 3 PR B) so each tab
 * carries its match in the URL. Falls back to ``/`` when the server
 * doesn't surface an id (legacy single-shooter projects, or a future
 * unbound state). */
function matchHome(health: ServerHealth): string {
  return health.match_id ? `/match/${health.match_id}/` : "/";
}

export function Pick() {
  const navigate = useNavigate();
  const confirm = useConfirm();
  const mode = useDeploymentMode();
  const [recents, setRecents] = useState<RecentProjectDetail[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [opening, setOpening] = useState<string | null>(null);
  const [openPath, setOpenPath] = useState("");
  const [importDest, setImportDest] = useState("");
  const [importArchive, setImportArchive] = useState<File | null>(null);
  const [importOverwrite, setImportOverwrite] = useState(false);
  const [importing, setImporting] = useState(false);
  const [identity, setIdentity] = useState<ScoreboardIdentity | null>(null);
  const filterInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getScoreboardIdentity()
      .then((id) => {
        if (alive) setIdentity(id);
      })
      .catch(() => {
        if (alive) setIdentity(null);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    api
      .getRecentProjectsDetail()
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

  useEffect(() => {
    filterInputRef.current?.focus();
  }, []);

  // Page-level "/" focuses search, matching the polished kbd hint.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "/" && document.activeElement?.tagName !== "INPUT") {
        e.preventDefault();
        filterInputRef.current?.focus();
        filterInputRef.current?.select();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const counts = useMemo(() => {
    const c = {
      all: 0,
      awaiting_footage: 0,
      in_progress: 0,
      exported: 0,
      archived: 0,
    };
    if (!recents) return c;
    for (const r of recents) {
      if (r.kind === "missing") continue;
      c.all += 1;
      if (r.status === "awaiting_footage") c.awaiting_footage += 1;
      else if (r.status === "in_progress") c.in_progress += 1;
      else if (r.status === "exported") c.exported += 1;
      else if (r.status === "archived") c.archived += 1;
    }
    return c;
  }, [recents]);

  const filtered = useMemo(() => {
    if (!recents) return [];
    const q = filter.trim().toLowerCase();
    return recents.filter((r) => {
      if (r.kind === "missing" && statusFilter !== "all") return false;
      if (statusFilter !== "all" && r.status !== statusFilter) return false;
      if (!q) return true;
      return (
        r.name.toLowerCase().includes(q) ||
        r.path.toLowerCase().includes(q) ||
        (r.club ?? "").toLowerCase().includes(q)
      );
    });
  }, [recents, filter, statusFilter]);

  const active = filtered.filter((r) => r.status !== "archived");
  const archived = filtered.filter((r) => r.status === "archived");

  useEffect(() => {
    if (selectedIdx >= filtered.length) {
      setSelectedIdx(Math.max(0, filtered.length - 1));
    }
  }, [filtered.length, selectedIdx]);

  async function open(target: RecentProjectDetail) {
    setOpening(target.path);
    setError(null);
    try {
      const health = await api.bindProject(target.path, target.name);
      navigate(matchHome(health), { replace: true });
    } catch (e: unknown) {
      setOpening(null);
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function deleteProject(target: RecentProjectDetail) {
    // Mode-specific opt-in extras. Desktop can wipe the folder on disk;
    // hosted can additionally drop raw uploads that fed only this match.
    const checkboxes =
      mode === "local"
        ? [
            {
              key: "deleteLocalFiles",
              label: "Also delete the project folder on disk",
              help: "Permanently removes the footage, audit work, and exports under this match's folder. This cannot be undone.",
            },
          ]
        : [
            {
              key: "deleteRawUploads",
              label: "Also delete raw uploads that fed only this match",
              help: "Uploaded videos still attached to another match are kept.",
            },
          ];

    const result = await confirm({
      title: `Delete ${target.name}?`,
      body: "This removes the match and every resource it owns -- detection state, trims, exports, and any running jobs. This cannot be undone.",
      confirmLabel: "Delete project",
      checkboxes,
    });
    if (!result.confirmed) return;

    try {
      const resp = await api.deleteProject(target.path, {
        deleteLocalFiles: Boolean(result.checked.deleteLocalFiles),
        deleteRawUploads: Boolean(result.checked.deleteRawUploads),
      });
      // Re-fetch the enriched list (the delete endpoint returns the base
      // shape so we'd lose `kind` etc. if we trusted its response).
      const full = await api.getRecentProjectsDetail();
      setRecents(full);
      // Best-effort teardown: surface any partial-failure detail so a
      // half-cleaned match isn't silently reported as fully gone.
      if (resp.summary.errors.length > 0) {
        setError(
          `Deleted with ${resp.summary.errors.length} issue${
            resp.summary.errors.length === 1 ? "" : "s"
          }: ${resp.summary.errors.join("; ")}`,
        );
      }
      return resp;
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
      const health = await api.bindProject(trimmed);
      navigate(matchHome(health), { replace: true });
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
      // Import returns a manifest, not a HealthResponse -- read /api/health
      // to pick up the just-bound match's id for the URL prefix.
      const health = await api.getHealth();
      navigate(matchHome(health), { replace: true });
    } catch (e: unknown) {
      setImporting(false);
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
      // Opens the confirm dialog rather than deleting on the keystroke --
      // this is destructive and irreversible.
      void deleteProject(filtered[selectedIdx]);
    }
  }

  return (
    <div
      className="relative min-h-screen text-ink"
      style={{
        backgroundImage:
          "radial-gradient(1200px 600px at 50% -100px, rgba(255,45,45,0.04), transparent 60%), linear-gradient(to bottom, var(--color-bg-glow), var(--color-bg))",
        backgroundAttachment: "fixed",
      }}
      onKeyDown={onKeyDown}
    >
      {/* ============================== Shell ============================== */}
      <header className="sticky top-0 z-chrome border-b border-rule bg-gradient-to-b from-surface to-bg">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 -bottom-px h-px"
          style={{
            background:
              "linear-gradient(to right, transparent, var(--color-led) 18%, var(--color-led) 22%, var(--color-rule-strong) 30%, var(--color-rule-strong) 70%, var(--color-led) 78%, var(--color-led) 82%, transparent)",
            opacity: 0.55,
          }}
        />
        <div className="mx-auto flex max-w-[1440px] items-center gap-7 px-8 py-4">
          <Brand
            serial={
              <>
                SS &middot; PICKER
                <br />
                <b className="font-semibold text-ink-2">
                  Standby &middot; no match bound
                </b>
              </>
            }
          />
          <div className="ml-auto flex items-center gap-3">
            {identity?.display_name && (
              <span className="inline-flex items-center gap-2 rounded-full border border-rule bg-surface-2 py-1.5 pl-1.5 pr-4">
                <span
                  className="inline-flex size-7 items-center justify-center rounded-full font-mono text-[0.6875rem] font-bold text-ink"
                  style={{
                    background:
                      "linear-gradient(135deg, var(--color-led), var(--color-led-deep))",
                    boxShadow:
                      "0 0 0 1px rgba(255,45,45,0.4), 0 0 12px var(--color-led-glow)",
                  }}
                >
                  {shooterInitials(identity.display_name).toUpperCase()}
                </span>
                <span className="text-[0.8125rem] font-medium text-ink-2">
                  {identity.display_name}
                </span>
              </span>
            )}
            <AccountChip />
          </div>
        </div>
        <div className="border-t border-rule bg-bg">
          <div className="mx-auto flex max-w-[1440px] items-center gap-4 px-8 py-2.5 text-xs text-muted">
            <span
              aria-hidden
              className="inline-block size-1.5 rounded-full bg-led shadow-[0_0_8px_var(--color-led-glow)]"
            />
            <span>
              <strong className="font-mono font-medium tracking-wider text-ink-2">
                STANDBY
              </strong>{" "}
              &middot; No match in session -- pick one or begin a new record
            </span>
            <span className="ml-auto font-mono text-[0.625rem] uppercase tracking-[0.16em] text-subtle">
              <b className="font-semibold text-ink-2">{pad2(counts.all)}</b>{" "}
              &middot; matches register
            </span>
          </div>
        </div>
      </header>

      {/* ============================== Main ============================== */}
      <main className="mx-auto max-w-[1440px] px-8 pb-16 pt-14">
        {/* Page head */}
        <div className="mb-9 grid grid-cols-1 items-end gap-10 lg:grid-cols-[1fr_auto]">
          <div>
            <Kicker className="mb-5">
              Project Register
              <span className="ml-2 font-medium tracking-[0.14em] text-subtle">
                VOL. 01 &middot; ED. 04
              </span>
            </Kicker>
            <h1
              className="mb-5 font-display text-6xl font-bold uppercase leading-[0.92] tracking-tight text-ink lg:text-[5.5rem]"
            >
              Match{" "}
              <span className="relative inline-block text-led">
                Archive
                <span
                  aria-hidden
                  className="absolute bottom-[0.12em] left-0 h-[0.06em] w-full bg-led"
                  style={{ boxShadow: "0 0 12px var(--color-led-glow)" }}
                />
              </span>
            </h1>
            <p className="max-w-[36rem] text-[0.9375rem] text-ink-2">
              Recent first.{" "}
              <span className="font-mono font-semibold tabular-nums text-ink">
                {String(counts.all).padStart(2, "0")}
              </span>{" "}
              records on file.{" "}
              <span className="text-led">Splits</span> are the central data
              primitive -- every record traces back to the beep, the shots,
              and the time between them.
            </p>
          </div>

          <aside
            className="hidden grid-cols-3 gap-7 rounded-2xl border border-rule bg-surface px-6 py-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)] lg:grid"
            aria-label="Quick stats"
          >
            <Readout label="Records" value={pad2(counts.all)} />
            <Readout
              label="In Progress"
              value={pad2(counts.in_progress)}
              tone="live"
            />
            <Readout
              label="Exported"
              value={pad2(counts.exported)}
              tone="done"
            />
          </aside>
        </div>

        {/* Action row */}
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              document
                .getElementById("import-backup")
                ?.scrollIntoView({ behavior: "smooth" })
            }
            className="font-display uppercase tracking-[0.06em]"
          >
            <Upload className="size-3.5" /> Import Backup
          </Button>
          {/* Merge CTA only shows when there's something to merge -- 2+ legacy
              single-shooter projects in recents. Hidden otherwise so the
              picker stays uncluttered for users who started post-redesign. */}
          {(recents?.filter((r) => r.kind === "legacy").length ?? 0) >= 2 && (
            <Button
              type="button"
              variant="outline"
              onClick={() => navigate("/pick/merge")}
              className="font-display uppercase tracking-[0.06em]"
              title="Combine multiple single-shooter projects into one match folder"
            >
              <GitMerge className="size-3.5" /> Merge legacy
            </Button>
          )}
          <Button
            type="button"
            onClick={() => navigate("/pick/new")}
            className="btn-led-fill"
          >
            <Plus className="size-3.5" />
            <span>New Match</span>
            <Kbd className="border-current/40">&#8984;N</Kbd>
          </Button>
        </div>

        {/* Toolbar */}
        <div className="mb-6 flex flex-wrap items-stretch gap-2.5">
          <label
            className={cn(
              "flex flex-1 items-center gap-3 rounded-[10px] border border-rule bg-surface px-4 py-3 transition-all",
              "focus-within:border-led focus-within:bg-surface-2 focus-within:shadow-[0_0_0_3px_var(--color-led-tint)]",
            )}
          >
            <Search
              aria-hidden
              className="size-4 text-subtle group-focus-within:text-led"
            />
            <input
              ref={filterInputRef}
              type="text"
              value={filter}
              onChange={(e) => {
                setFilter(e.target.value);
                setSelectedIdx(0);
              }}
              placeholder="Search by match, club, or path..."
              aria-label="Search matches"
              className="flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-subtle"
            />
            <Kbd>/</Kbd>
          </label>

          <div
            className="inline-flex max-w-full items-stretch overflow-x-auto md:overflow-hidden rounded-[10px] border border-rule bg-surface"
            role="tablist"
            aria-label="Filter matches by status"
          >
            <FilterChip
              active={statusFilter === "all"}
              count={counts.all}
              onClick={() => setStatusFilter("all")}
            >
              All
            </FilterChip>
            {counts.awaiting_footage > 0 && (
              <FilterChip
                active={statusFilter === "awaiting_footage"}
                count={counts.awaiting_footage}
                onClick={() => setStatusFilter("awaiting_footage")}
              >
                Awaiting footage
              </FilterChip>
            )}
            <FilterChip
              active={statusFilter === "in_progress"}
              count={counts.in_progress}
              onClick={() => setStatusFilter("in_progress")}
            >
              In progress
            </FilterChip>
            <FilterChip
              active={statusFilter === "exported"}
              count={counts.exported}
              onClick={() => setStatusFilter("exported")}
            >
              Exported
            </FilterChip>
            <FilterChip
              active={statusFilter === "archived"}
              count={counts.archived}
              onClick={() => setStatusFilter("archived")}
            >
              Archived
            </FilterChip>
          </div>
        </div>

        {error ? (
          <div className="mb-4 rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
            {error}
          </div>
        ) : null}

        {/* Matches */}
        {recents === null ? (
          <div className="rounded-2xl border border-rule bg-surface p-12 text-center text-sm text-muted">
            Loading...
          </div>
        ) : active.length === 0 && archived.length === 0 ? (
          <EmptyState onNew={() => navigate("/pick/new")} />
        ) : (
          <>
            {active.length > 0 && (
              <section
                aria-label="Active matches"
                className="overflow-hidden rounded-[14px] border border-rule bg-surface shadow-[inset_0_1px_0_rgba(255,255,255,0.02),0_18px_40px_-24px_rgba(0,0,0,0.7)]"
              >
                {active.map((r, idx) => (
                  <MatchRow
                    key={r.path}
                    project={r}
                    index={idx + 1}
                    selected={
                      filtered.indexOf(r) === selectedIdx && opening !== r.path
                    }
                    busy={opening === r.path}
                    onOpen={() => open(r)}
                    onDelete={() => deleteProject(r)}
                    onHover={() => setSelectedIdx(filtered.indexOf(r))}
                  />
                ))}
              </section>
            )}

            {archived.length > 0 && (
              <>
                <div className="my-12 flex items-center gap-5">
                  <span className="inline-flex items-center gap-2.5 font-display text-[0.6875rem] font-bold uppercase tracking-[0.22em] text-cold">
                    <span
                      aria-hidden
                      className="inline-block size-1.5 border border-cold"
                    />
                    Archive
                  </span>
                  <span className="h-px flex-1 bg-gradient-to-r from-rule via-rule-strong to-transparent" />
                  <span className="font-mono text-[0.625rem] uppercase tracking-[0.14em] text-subtle">
                    {pad2(archived.length)} dormant record
                    {archived.length === 1 ? "" : "s"}
                  </span>
                </div>
                <section
                  aria-label="Archived matches"
                  className="overflow-hidden rounded-[14px] border border-rule bg-surface opacity-75 transition-opacity hover:opacity-100"
                >
                  {archived.map((r, idx) => (
                    <MatchRow
                      key={r.path}
                      project={r}
                      index={active.length + idx + 1}
                      selected={
                        filtered.indexOf(r) === selectedIdx &&
                        opening !== r.path
                      }
                      busy={opening === r.path}
                      onOpen={() => open(r)}
                      onDelete={() => deleteProject(r)}
                      onHover={() => setSelectedIdx(filtered.indexOf(r))}
                      archived
                    />
                  ))}
                </section>
              </>
            )}
          </>
        )}

        {/* Open by path + Import accordions */}
        <div className="mt-10 grid gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-rule bg-surface p-5">
            <div className="mb-2 flex items-center gap-2 font-display text-sm font-semibold uppercase tracking-[0.06em] text-ink-2">
              <FolderOpen className="size-4 text-subtle" /> Open by path
            </div>
            <p className="mb-3 text-xs text-muted">
              Paste an absolute path to an existing project or match folder.
              Pointing at a folder without metadata scaffolds a fresh
              project in place.
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
                className="flex-1 rounded-md border border-rule bg-surface-2 px-3 py-2 font-mono text-xs text-ink outline-none focus:border-led focus:shadow-[0_0_0_3px_var(--color-led-tint)]"
              />
              <Button type="submit" disabled={!openPath.trim()}>
                Open
              </Button>
            </form>
          </div>

          <div
            id="import-backup"
            className="rounded-xl border border-rule bg-surface p-5"
          >
            <div className="mb-2 flex items-center gap-2 font-display text-sm font-semibold uppercase tracking-[0.06em] text-ink-2">
              <Upload className="size-4 text-subtle" /> Import from backup
            </div>
            <p className="mb-3 text-xs text-muted">
              Restore a <code className="font-mono">.tar.gz</code> produced
              by the Download backup button.
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
                onChange={(e) => setImportArchive(e.target.files?.[0] ?? null)}
                className="block w-full text-xs"
              />
              <div className="flex gap-2">
                <input
                  type="text"
                  value={importDest}
                  onChange={(e) => setImportDest(e.target.value)}
                  placeholder="Destination directory"
                  className="flex-1 rounded-md border border-rule bg-surface-2 px-3 py-2 font-mono text-xs text-ink outline-none focus:border-led focus:shadow-[0_0_0_3px_var(--color-led-tint)]"
                />
                <Button
                  type="submit"
                  disabled={!importArchive || !importDest.trim() || importing}
                >
                  {importing ? "Importing..." : "Import"}
                </Button>
              </div>
              <label className="flex items-center gap-2 text-xs text-muted">
                <input
                  type="checkbox"
                  checked={importOverwrite}
                  onChange={(e) => setImportOverwrite(e.target.checked)}
                />
                Overwrite if the target folder already exists
              </label>
            </form>
          </div>
        </div>

        {/* Kbd legend */}
        <div className="mt-10 flex items-center justify-between text-[0.6875rem] uppercase tracking-[0.16em] text-subtle">
          <span className="inline-flex items-center gap-2 font-mono">
            <Kbd>Up</Kbd>/<Kbd>Down</Kbd> to select
            <Kbd>Enter</Kbd> to open
            <Kbd>&#8984;</Kbd>+<Kbd>Backspace</Kbd> to delete
          </span>
          <span className="font-mono">
            Splitsmith <Heartbeat /> Local Worker
          </span>
        </div>
      </main>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Subcomponents                                                              */
/* -------------------------------------------------------------------------- */

function FilterChip({
  active,
  count,
  onClick,
  children,
}: {
  active: boolean;
  count: number;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "relative inline-flex min-h-[48px] items-center gap-2 px-4 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em] transition-colors",
        "border-l border-rule first:border-l-0",
        active
          ? "bg-surface-2 font-bold text-ink"
          : "text-muted hover:bg-surface-2 hover:text-ink",
      )}
    >
      <span>{children}</span>
      <span
        className={cn(
          "font-mono text-[0.625rem] tabular-nums",
          active ? "font-bold text-led" : "font-medium text-subtle",
        )}
      >
        {pad2(count)}
      </span>
      {active && (
        <span
          aria-hidden
          className="absolute bottom-1 left-1/2 h-0.5 w-4 -translate-x-1/2 rounded-sm bg-led shadow-[0_0_6px_var(--color-led-glow)]"
        />
      )}
    </button>
  );
}

interface MatchRowProps {
  project: RecentProjectDetail;
  index: number;
  selected: boolean;
  busy: boolean;
  archived?: boolean;
  onOpen: () => void;
  onDelete: () => void;
  onHover: () => void;
}

function MatchRow({
  project,
  index,
  selected,
  busy,
  archived,
  onOpen,
  onDelete,
  onHover,
}: MatchRowProps) {
  // Build a TickStrip from stage_count + stages_audited. Missing details
  // (kind === "missing"/"unknown") render an empty strip instead of NaN.
  const ticks: TickState[] = useMemo(() => {
    const total = Math.max(0, project.stage_count);
    const done = Math.min(total, Math.max(0, project.stages_audited));
    return Array.from(
      { length: total },
      (_, i) => (i < done ? "done" : "todo") as TickState,
    );
  }, [project.stage_count, project.stages_audited]);

  const isMissing = project.kind === "missing";
  const isManual = project.manual;

  return (
    <article
      role="button"
      tabIndex={0}
      className={cn(
        "group relative flex cursor-pointer flex-col gap-3 border-b border-rule px-4 py-4 transition-colors last:border-b-0",
        "md:grid md:items-center md:gap-6 md:px-7 md:py-5 md:[grid-template-columns:56px_minmax(0,1fr)_180px_220px_160px_152px]",
        "hover:bg-surface-2 focus:outline-none focus:bg-surface-2",
        selected && "bg-surface-2",
      )}
      onMouseEnter={onHover}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onOpen();
        }
      }}
      aria-label={`Open ${project.name}`}
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 left-0 w-[3px] bg-led shadow-[0_0_12px_var(--color-led-glow)] transition-opacity",
          selected
            ? "opacity-100"
            : "opacity-0 group-hover:opacity-100 group-focus:opacity-100",
        )}
      />

      {/* Line 1 on mobile: index number + match name/date; at md+ these are separate grid columns */}
      <div className="flex items-start gap-4 md:contents">
        {/* Index */}
        <div className="font-mono text-[0.6875rem] uppercase tracking-[0.14em] text-subtle">
          No.
          <b className="mt-1 block font-display text-[1.5rem] font-bold leading-none text-ink">
            {pad2(index)}
          </b>
        </div>

        {/* Primary */}
        <div className="min-w-0">
          <h2 className="mb-2 truncate font-display text-2xl font-bold uppercase leading-tight text-ink">
            {project.name}
            {isManual && (
              <span className="ml-2.5 inline-block translate-y-[-0.4em] rounded border border-rule-strong bg-surface-3 px-1.5 py-0.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.14em] text-ink-2">
                Manual
              </span>
            )}
          </h2>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.8125rem] text-muted">
            {project.match_date ? (
              <time
                className="border-r border-rule pr-3 font-mono text-xs font-semibold uppercase tracking-[0.06em] text-ink-2"
                dateTime={project.match_date}
              >
                {formatDate(project.match_date)}
              </time>
            ) : (
              <span className="border-r border-rule pr-3 font-mono text-xs uppercase tracking-[0.06em] text-subtle">
                No date
              </span>
            )}
            <span className="truncate font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-subtle">
              {project.path}
            </span>
          </div>
        </div>
      </div>

      {/* Line 2 on mobile: shooters, progress, status, actions wrap together; at md+ these are separate grid columns */}
      <div className="flex flex-wrap gap-x-4 gap-y-2 md:contents">
        {/* Shooters */}
        <div className="flex flex-col gap-2">
          <div className="font-mono text-[0.625rem] font-semibold uppercase tracking-[0.14em] text-subtle">
            <b className="font-bold text-ink">{pad2(project.shooter_count)}</b>{" "}
            {project.shooter_count === 1 ? "Shooter" : "Shooters"}
            {project.shooter_count === 1 && (
              <span className="ml-1 font-bold tracking-[0.12em] text-led">
                SOLO
              </span>
            )}
          </div>
          <AvatarStack
            size="sm"
            avatars={project.shooter_names
              .slice(0, 4)
              .map((shooterName) => ({
                initials: shooterInitials(shooterName),
                name: shooterName,
                seed: `${project.path}-${shooterName}`,
              }))}
            overflow={
              project.shooter_count > 4 ? project.shooter_count - 4 : undefined
            }
          />
        </div>

        {/* Progress */}
        <div className="flex flex-col gap-2.5">
          <div className="flex items-baseline gap-2 font-mono text-[0.625rem] font-semibold uppercase tracking-[0.14em] text-subtle">
            <span className="font-bold tabular-nums text-ink">
              {pad2(project.stages_audited)} / {pad2(project.stage_count)}
            </span>
            <span>stages</span>
            {project.stage_count > 0 &&
              project.stages_audited >= project.stage_count && (
                <span className="font-bold text-done">complete</span>
              )}
          </div>
          {ticks.length > 0 ? (
            <TickStrip
              states={ticks}
              ariaLabel={`${project.stages_audited} of ${project.stage_count} stages audited`}
            />
          ) : (
            <span className="font-mono text-[0.6875rem] uppercase text-subtle">
              {isMissing ? "Folder not found" : "No stages yet"}
            </span>
          )}
        </div>

        {/* Status */}
        <div className="flex flex-col gap-1.5">
          {isMissing ? (
            <StatusPill tone="led">Missing</StatusPill>
          ) : project.status === "awaiting_footage" ? (
            <StatusPill tone="awaiting">Awaiting Footage</StatusPill>
          ) : project.status === "in_progress" ? (
            <StatusPill tone="in-progress">In Progress</StatusPill>
          ) : project.status === "exported" ? (
            <StatusPill tone="exported">Exported</StatusPill>
          ) : (
            <StatusPill tone="archived">Archived</StatusPill>
          )}
          <span className="font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
            {isMissing ? (
              "path not on disk"
            ) : (
              <>
                touched{" "}
                <span className="font-bold text-ink-2">
                  {formatRelative(
                    new Date(project.last_modified_at ?? project.last_opened_at),
                  )}
                </span>
              </>
            )}
          </span>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-end gap-1.5">
          <button
            type="button"
            className={cn(
              "inline-flex min-h-[40px] items-center gap-2 rounded-lg border px-4 py-2.5 font-display text-xs font-bold uppercase tracking-[0.1em] leading-none transition-all",
              archived
                ? "border-rule-strong bg-transparent text-ink hover:border-led hover:bg-led-fill hover:text-ink"
                : "border-ink bg-ink text-bg hover:border-led-deep hover:bg-led-fill hover:text-ink",
            )}
            onClick={(e) => {
              e.stopPropagation();
              onOpen();
            }}
            aria-label={`Open ${project.name}`}
            disabled={busy || isMissing}
          >
            {busy ? "Opening..." : archived ? "Restore" : "Open"}
            <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
          </button>
          <button
            type="button"
            title="Delete this project"
            className="inline-flex size-9 items-center justify-center rounded-md border border-transparent text-subtle transition-all hover:border-rule hover:bg-surface-3 hover:text-led"
            onClick={(e) => {
              e.stopPropagation();
              onDelete();
            }}
            aria-label={`Delete ${project.name}`}
          >
            <Trash2 className="size-4" />
          </button>
        </div>
      </div>
    </article>
  );
}

function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="flex flex-col items-center gap-4 rounded-2xl border border-dashed border-rule px-6 py-16 text-center">
      <Crosshair className="size-10 text-led/70" />
      <Kicker>Empty Register</Kicker>
      <p className="max-w-md text-sm text-muted">
        No matches on file yet. Create one to start ingesting footage, or
        import a backup if you've worked on this machine before.
      </p>
      <div className="flex gap-3">
        <Button onClick={onNew} className="btn-led-fill">
          <Plus className="size-3.5" /> New Match
        </Button>
      </div>
    </div>
  );
}

function Heartbeat() {
  return (
    <span
      aria-hidden
      className="mx-1 inline-block size-1.5 rounded-full bg-led align-middle shadow-[0_0_6px_var(--color-led-glow)]"
      style={{ animation: "pulse 2.4s ease-in-out infinite" }}
    />
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function shooterInitials(name: string): string {
  // First letter of the first two whitespace-separated parts; falls back
  // to the first two letters of a single-word name. Diacritics are kept
  // (Avatar uppercases for display).
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2);
  return parts[0][0] + parts[1][0];
}

function formatDate(iso: string): string {
  // YYYY-MM-DD -> 12 APR 2026 (DD MON YYYY all-caps)
  const d = new Date(iso + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return iso;
  const day = String(d.getUTCDate()).padStart(2, "0");
  const months = [
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
  ];
  return `${day} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

function formatRelative(then: Date): string {
  if (Number.isNaN(then.getTime())) return "--";
  const now = Date.now();
  const ms = now - then.getTime();
  const sec = Math.round(ms / 1000);
  if (sec < 45) return "just now";
  const min = Math.round(sec / 60);
  if (min < 45) return `${min} MIN AGO`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} HR AGO`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} DAY${day === 1 ? "" : "S"} AGO`;
  const mo = Math.round(day / 30);
  return `${mo} MO AGO`;
}
