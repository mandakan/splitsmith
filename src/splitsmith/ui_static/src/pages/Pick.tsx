/**
 * Matches (/pick) -- spec 2026-09-13 s4.1.
 *
 * Job: get back into the match I was working on, or start one. The
 * Continue card names the next action ("Audit stage 05 B5 Rear") for the
 * most recently touched match with work left; under it, search, the
 * status chips and one table (match, date, shooters, stages, progress,
 * touched, Open). "Open by path" and "Import backup" name filesystem
 * paths, so they render only on a local install.
 *
 * Every derivation lives in lib/matches.ts. This file keeps the
 * plumbing (recent-projects fetch, bind, explicit-path open, backup
 * import) and the keyboard model: ArrowUp/Down select, Enter opens,
 * `/` focuses search. Deleting a match moved to the Export page.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";

import { AvatarStack, Kbd } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import { inputClass } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { PipelineDots } from "@/components/ui/PipelineDots";
import { Table, Td, Th, Tr } from "@/components/ui/DataTable";
import { useShellContextSlot } from "@/components/layout/shellChromeContext";
import {
  ApiError,
  api,
  type RecentProjectDetail,
  type ScoreboardIdentity,
  type ServerHealth,
} from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import {
  continueHref,
  continueLabel,
  continueVerb,
  filterMatches,
  formatMatchDate,
  formatRelative,
  matchCounts,
  pickContinue,
  progressStates,
  touchedAt,
  type StatusFilter,
} from "@/lib/matches";
import { useMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

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
  const { mode } = useDeploymentMode();
  const { mode: appMode } = useMode();
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
  const [serverVersion, setServerVersion] = useState<string | null>(null);
  const filterInputRef = useRef<HTMLInputElement | null>(null);
  const localFs = mode !== "hosted";

  // RootLayout's header slot -- Pick portals its context row there
  // instead of rendering its own <header> (#550). Pick has no nav drawer,
  // so unlike MatchShell it must not call useShellOwnsMobileAccount():
  // the global bar's account chip is the only one it has.
  const slot = useShellContextSlot();

  useEffect(() => {
    let alive = true;
    api
      .getHealth()
      .then((h) => {
        if (alive) setServerVersion(h.version ?? null);
      })
      .catch(() => {
        if (alive) setServerVersion(null);
      });
    return () => {
      alive = false;
    };
  }, []);

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

  // Page-level "/" focuses search.
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

  const counts = useMemo(() => matchCounts(recents ?? []), [recents]);
  const filtered = useMemo(
    () => filterMatches(recents ?? [], filter, statusFilter, { localFs }),
    [recents, filter, statusFilter, localFs],
  );
  const continueMatch = useMemo(() => pickContinue(recents ?? []), [recents]);

  useEffect(() => {
    if (selectedIdx >= filtered.length) {
      setSelectedIdx(Math.max(0, filtered.length - 1));
    }
  }, [filtered.length, selectedIdx]);

  /** Where a successful bind should land. In Developer mode the picker
   *  is a detour from dev work (reached via the Splitsmith breadcrumb
   *  or an unbound ``--lab`` boot), so return to the dev corpus with the
   *  chosen match pinned as ``?match=`` instead of dropping the
   *  operator into match mode. Legacy projects have no match_id the
   *  corpus page could use, so they keep the match-home destination. */
  function postBindTarget(health: ServerHealth): string {
    if (appMode === "developer" && health.match_id) {
      return `/dev/corpus?match=${encodeURIComponent(health.match_id)}`;
    }
    return matchHome(health);
  }

  async function open(target: RecentProjectDetail, to?: string) {
    setOpening(target.path);
    setError(null);
    try {
      const health = await api.bindProject(target.path, target.name);
      const dev = appMode === "developer";
      navigate(to && !dev ? to : postBindTarget(health), { replace: true });
    } catch (e: unknown) {
      setOpening(null);
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
      navigate(postBindTarget(health), { replace: true });
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
    } else if (e.key === "Enter" && document.activeElement?.tagName !== "INPUT") {
      e.preventDefault();
      void open(filtered[selectedIdx]);
    }
  }

  const legacyCount = recents?.filter((r) => r.kind === "legacy").length ?? 0;

  // The context row under the top bar: no match is open, how many are on
  // file, the server version, the scoreboard identity when there is one.
  const contextRow = (
    <div className="flex flex-wrap items-center gap-4 border-t border-rule bg-bg px-7 py-2 text-sm text-muted">
      <span>No match open</span>
      <span className="ml-auto">
        <span className="numeral text-ink-2">{counts.all}</span> {counts.all === 1 ? "match" : "matches"}
      </span>
      {serverVersion ? <span className="font-mono">v{serverVersion}</span> : null}
      {identity?.display_name ? <span className="text-ink-2">{identity.display_name}</span> : null}
    </div>
  );

  return (
    <div className="min-h-[calc(100dvh-var(--shell-header-h,86px))] text-ink" onKeyDown={onKeyDown}>
      {slot ? createPortal(contextRow, slot) : null}
      <main className="mx-auto max-w-[1100px] px-7 pb-14 pt-6">
        <PageHeader
          title="Matches"
          sub={
            recents === null
              ? "Loading..."
              : `${counts.all} ${counts.all === 1 ? "match" : "matches"} · ${counts.in_progress} in progress`
          }
          actions={
            <>
              {localFs ? (
                <Button
                  type="button"
                  onClick={() =>
                    document.getElementById("import-backup")?.scrollIntoView({ behavior: "smooth" })
                  }
                >
                  Import backup
                </Button>
              ) : null}
              {/* Merge shows only when there is something to merge: 2+
                  legacy single-shooter projects in recents. */}
              {legacyCount >= 2 ? (
                <Button
                  type="button"
                  onClick={() => navigate("/pick/merge")}
                  title="Combine multiple single-shooter projects into one match folder"
                >
                  Merge legacy
                </Button>
              ) : null}
              <Button
                type="button"
                variant={continueMatch ? "default" : "primary"}
                onClick={() => navigate("/pick/new")}
              >
                New match
                <Kbd className="border-current/40">&#8984;N</Kbd>
              </Button>
            </>
          }
        />

        {continueMatch && continueMatch.next_step ? (
          <section
            aria-label="Continue"
            className="relative mb-5 grid grid-cols-1 items-center gap-4 rounded-[10px] border border-rule-strong bg-surface px-5 py-4 sm:grid-cols-[minmax(0,1fr)_auto]"
          >
            <span aria-hidden className="absolute bottom-2.5 left-0 top-2.5 w-0.5 bg-led" />
            <div className="min-w-0">
              <Label>Continue</Label>
              <h2 className="mt-0.5 truncate text-lg font-semibold leading-tight text-ink">
                {continueMatch.name}
              </h2>
              <p className="mt-1 text-md text-ink-2">
                Next: <b className="font-medium text-ink">{continueLabel(continueMatch.next_step)}</b>
                {continueMatch.shooter_names.length === 1 ? ` · ${continueMatch.shooter_names[0]}` : null}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-3.5 text-sm text-muted">
                {continueMatch.stage_count > 0 ? (
                  <PipelineDots
                    states={progressStates(continueMatch)}
                    label={`${continueMatch.stages_audited} of ${continueMatch.stage_count} stages audited`}
                  />
                ) : null}
                {continueMatch.stage_count > 0 ? (
                  <span>
                    <span className="numeral">{continueMatch.stages_audited}</span> /{" "}
                    <span className="numeral">{continueMatch.stage_count}</span> stages audited
                  </span>
                ) : null}
                <span>touched {formatRelative(touchedAt(continueMatch))}</span>
              </div>
            </div>
            <Button
              type="button"
              variant="primary"
              disabled={opening !== null}
              onClick={() => void open(continueMatch, continueHref(continueMatch))}
            >
              {opening === continueMatch.path ? "Opening..." : continueVerb(continueMatch.next_step)}
            </Button>
          </section>
        ) : null}

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <label className="flex min-w-[220px] flex-1 items-center gap-2 rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 focus-within:border-led">
            <input
              ref={filterInputRef}
              type="text"
              value={filter}
              onChange={(e) => {
                setFilter(e.target.value);
                setSelectedIdx(0);
              }}
              placeholder="Search matches"
              aria-label="Search matches"
              className="flex-1 bg-transparent text-md text-ink outline-none placeholder:text-subtle"
            />
            <Kbd>/</Kbd>
          </label>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter matches by status">
            <FilterChip active={statusFilter === "all"} count={counts.all} onClick={() => setStatusFilter("all")}>
              All
            </FilterChip>
            <FilterChip
              active={statusFilter === "in_progress"}
              count={counts.in_progress}
              tick="live"
              onClick={() => setStatusFilter("in_progress")}
            >
              In progress
            </FilterChip>
            {counts.awaiting_footage > 0 ? (
              <FilterChip
                active={statusFilter === "awaiting_footage"}
                count={counts.awaiting_footage}
                onClick={() => setStatusFilter("awaiting_footage")}
              >
                Awaiting footage
              </FilterChip>
            ) : null}
            <FilterChip
              active={statusFilter === "exported"}
              count={counts.exported}
              tick="done"
              onClick={() => setStatusFilter("exported")}
            >
              Exported
            </FilterChip>
            {counts.archived > 0 ? (
              <FilterChip
                active={statusFilter === "archived"}
                count={counts.archived}
                onClick={() => setStatusFilter("archived")}
              >
                Archived
              </FilterChip>
            ) : null}
          </div>
        </div>

        {error ? (
          <p role="alert" className="mb-3 rounded-md border border-destructive/45 px-3 py-2 text-md text-destructive">
            {error}
          </p>
        ) : null}

        {recents === null ? (
          <div className="rounded-[10px] border border-rule bg-surface p-10 text-center text-md text-muted">
            Loading...
          </div>
        ) : filtered.length === 0 ? (
          <div className="rounded-[10px] border border-dashed border-rule-strong px-6 py-12 text-center text-md text-muted">
            {recents.length === 0 ? "No matches yet. Create one to start adding footage." : "No matches match the filter."}
          </div>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Match</Th>
                <Th>Date</Th>
                <Th>Shooters</Th>
                <Th align="right">Stages</Th>
                <Th>Progress</Th>
                <Th>Touched</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, idx) => (
                <MatchRow
                  key={r.path}
                  project={r}
                  current={idx === selectedIdx && opening !== r.path}
                  busy={opening === r.path}
                  onOpen={() => open(r)}
                  onHover={() => setSelectedIdx(idx)}
                />
              ))}
            </tbody>
          </Table>
        )}

        {/* Open by path + Import backup both name filesystem paths, so
            they render only against a local install. */}
        {localFs ? (
          <div className="mt-4 grid grid-cols-1 gap-px overflow-hidden rounded-[10px] border border-rule bg-rule lg:grid-cols-2">
            <form
              className="bg-surface px-3.5 py-3"
              onSubmit={(e) => {
                e.preventDefault();
                void openExplicitPath();
              }}
            >
              <Label>Open by path</Label>
              <div className="mt-2 flex gap-2">
                <input
                  type="text"
                  value={openPath}
                  onChange={(e) => setOpenPath(e.target.value)}
                  placeholder="/Users/you/matches/..."
                  aria-label="Project folder path"
                  className={cn(inputClass, "font-mono text-sm")}
                />
                <Button type="submit" size="sm" disabled={!openPath.trim()}>
                  Open
                </Button>
              </div>
            </form>
            <form
              id="import-backup"
              className="bg-surface px-3.5 py-3"
              onSubmit={(e) => {
                e.preventDefault();
                void runImport();
              }}
            >
              <Label>Import backup</Label>
              <div className="mt-2 flex flex-wrap gap-2">
                <input
                  type="file"
                  accept=".tar.gz,.tgz,application/gzip,application/x-tar"
                  aria-label="Backup archive"
                  onChange={(e) => setImportArchive(e.target.files?.[0] ?? null)}
                  className="min-w-0 flex-1 text-sm text-ink-2"
                />
                <input
                  type="text"
                  value={importDest}
                  onChange={(e) => setImportDest(e.target.value)}
                  placeholder="Destination directory"
                  aria-label="Destination directory"
                  className={cn(inputClass, "min-w-[160px] flex-1 font-mono text-sm")}
                />
                <Button type="submit" size="sm" disabled={!importArchive || !importDest.trim() || importing}>
                  {importing ? "Importing..." : "Import"}
                </Button>
              </div>
              <label className="mt-2 flex items-center gap-2 text-sm text-muted">
                <input
                  type="checkbox"
                  checked={importOverwrite}
                  onChange={(e) => setImportOverwrite(e.target.checked)}
                />
                Overwrite if the target folder already exists
              </label>
            </form>
          </div>
        ) : null}

        <div className="mt-4 flex items-center justify-between text-sm text-subtle">
          <span className="inline-flex items-center gap-1.5">
            <Kbd>&uarr;</Kbd>
            <Kbd>&darr;</Kbd> select <Kbd className="ml-2">Enter</Kbd> open <Kbd className="ml-2">/</Kbd> search
          </span>
          {serverVersion ? <span className="font-mono">v{serverVersion}</span> : null}
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
  tick,
  onClick,
  children,
}: {
  active: boolean;
  count: number;
  tick?: "live" | "done";
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
    >
      <Chip
        tick={tick === "live" ? "reload" : tick === "done" ? "fire" : undefined}
        className={cn(active && "border-ink-2 text-ink")}
      >
        {children}
        <span className="numeral text-muted">{count}</span>
      </Chip>
    </button>
  );
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

function MatchRow({
  project,
  current,
  busy,
  onOpen,
  onHover,
}: {
  project: RecentProjectDetail;
  current: boolean;
  busy: boolean;
  onOpen: () => void;
  onHover: () => void;
}) {
  const missing = project.kind === "missing";
  const archived = project.status === "archived";
  const states = progressStates(project);
  return (
    <Tr
      current={current}
      onMouseEnter={onHover}
      onClick={() => {
        if (!missing) onOpen();
      }}
      className={cn("cursor-pointer", archived && "text-subtle")}
    >
      <Td kind="name" className={cn(archived && "font-normal text-muted")}>
        {project.name}
        {project.manual ? <Chip className="ml-2 align-middle">Manual</Chip> : null}
      </Td>
      <Td dim>{project.match_date ? formatMatchDate(project.match_date) : "—"}</Td>
      <Td>
        <AvatarStack
          size="sm"
          avatars={project.shooter_names.slice(0, 4).map((n) => ({
            initials: shooterInitials(n),
            name: n,
            seed: `${project.path}-${n}`,
          }))}
          overflow={project.shooter_count > 4 ? project.shooter_count - 4 : undefined}
        />
      </Td>
      <Td kind="num" dim={archived}>
        {project.stage_count > 0 ? project.stage_count : "—"}
      </Td>
      <Td>
        {missing ? (
          <span className="text-destructive">Folder not found</span>
        ) : project.status === "awaiting_footage" ? (
          <Chip>Awaiting footage</Chip>
        ) : archived ? (
          <span className="text-muted">Archived</span>
        ) : (
          <span className="inline-flex items-center gap-2">
            {states.length > 0 ? (
              <PipelineDots
                states={states}
                label={`${project.stages_audited} of ${project.stage_count} stages audited`}
              />
            ) : null}
            {project.status === "exported" ? (
              <Chip tone="ok" tick="fire">
                Exported
              </Chip>
            ) : null}
          </span>
        )}
      </Td>
      <Td dim>{missing ? "—" : formatRelative(touchedAt(project))}</Td>
      <Td className="text-right">
        <Button
          type="button"
          size="sm"
          disabled={busy || missing}
          aria-label={`Open ${project.name}`}
          onClick={(e) => {
            e.stopPropagation();
            onOpen();
          }}
        >
          {busy ? "Opening..." : archived ? "Restore" : "Open"}
        </Button>
      </Td>
    </Tr>
  );
}
