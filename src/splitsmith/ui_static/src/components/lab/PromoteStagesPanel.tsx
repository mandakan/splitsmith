/**
 * Batch-promote every eligible stage of a match's shooters as primary
 * fixtures. Moved out of legacy ``Lab.tsx`` (#886 follow-up) so the
 * Corpus page can host it as a full-width expandable section instead of
 * a 640px popover; Lab.tsx still renders this component (``variant``
 * defaults to the legacy popover) so its behavior -- and the tests
 * pinning it -- stay identical until the legacy page is deleted.
 *
 * The Lab/Corpus surfaces live on /dev/* URLs, outside the
 * ``/match/:matchId/`` URL scoping that match-mode surfaces ride on --
 * and since #353 Tier 1 there is no process-level bind for the bare
 * paths to fall back on. The panel therefore picks its own match:
 * recent projects feed a selector, the choice is pinned in ``?match=``
 * (so a reload or a shared URL lands on the same match), and every call
 * below addresses that id through the ``/api/matches/{id}/...`` alias
 * explicitly.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  CheckCircle2,
  FlaskConical,
  Loader2,
  XCircle,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  api,
  type LabFixtureRecord,
  type MatchProject,
  type RecentProject,
  type StageExportStatus,
} from "@/lib/api";
import { exportSlugify } from "@/lib/slugify";
import { cn } from "@/lib/utils";

interface BatchRow {
  /** Registry slug of the shooter this row's run belongs to. */
  shooterSlug: string;
  shooterName: string;
  stageNumber: number;
  stageName: string;
  slug: string;
  exists: boolean;
  blockers: string[];
  selected: boolean;
  status: "idle" | "running" | "ok" | "error";
  message: string | null;
}

/** One shooter's loaded project + export overview, cached so toggling
 *  the shooter checkboxes rebuilds rows without re-fetching. */
interface ShooterBatch {
  slug: string;
  name: string;
  project: MatchProject;
  overview: StageExportStatus[];
}

function buildBatchRows(
  shooter: ShooterBatch,
  catalog: LabFixtureRecord[],
): BatchRow[] {
  const { project } = shooter;
  const existing = new Set(catalog.map((f) => f.slug));
  const token = project.shooter_token;
  // Must agree with the backend reader (`/api/lab/promote-from-anchor`
  // composes the same slug via export_naming.slugify with this fallback),
  // or the secondary promote 409s on a fixture the primary just wrote.
  const projectSlug = exportSlugify(project.name, "stage");
  const overviewByStage = new Map(shooter.overview.map((s) => [s.stage_number, s]));
  const rows: BatchRow[] = [];
  for (const stage of project.stages) {
    if (stage.placeholder || stage.skipped) continue;
    const ov = overviewByStage.get(stage.stage_number);
    const primary = stage.videos[0] ?? null;
    const blockers: string[] = [];
    // Per-shooter, not panel-wide: in a multi-shooter match one
    // unpinned shooter must not block promoting everyone else's runs.
    if (project.selected_shooter_id == null) {
      blockers.push("no SSI shooter pinned (Ingest page)");
    }
    if (!ov || !ov.audit_path) blockers.push("no audit JSON (run shot-detect)");
    if (!primary) blockers.push("no primary video");
    else {
      if (primary.beep_time == null) blockers.push("primary has no beep_time");
      if (!primary.camera_mount) blockers.push("primary has no camera_mount");
    }
    const slug = token
      ? `stage-shots-${projectSlug}-stage${stage.stage_number}-${token}`
      : `stage-shots-${projectSlug}-stage${stage.stage_number}`;
    rows.push({
      shooterSlug: shooter.slug,
      shooterName: shooter.name,
      stageNumber: stage.stage_number,
      stageName: stage.stage_name,
      slug,
      exists: existing.has(slug),
      blockers,
      selected: blockers.length === 0,
      status: "idle",
      message: null,
    });
  }
  return rows;
}

export function PromoteStagesPanel({
  catalog,
  onCatalogChanged,
  variant = "popover",
}: {
  catalog: LabFixtureRecord[];
  onCatalogChanged: (next: LabFixtureRecord[]) => void;
  /** ``popover``: legacy Lab.tsx's absolutely-positioned 640px dropdown.
   *  ``section``: layout-neutral full-width block for the Corpus page --
   *  the wrapper renders as `display:contents` so the trigger button and
   *  the (optional) expanded panel become independent flex items of
   *  whatever row the caller lays them out in. */
  variant?: "popover" | "section";
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Every shooter's project + overview, loaded up front so the shooter
  // checkboxes toggle instantly. Rows are derived per selected shooter.
  const [shooterData, setShooterData] = useState<ShooterBatch[]>([]);
  const [selectedShooters, setSelectedShooters] = useState<Set<string>>(
    new Set(),
  );
  const [rows, setRows] = useState<BatchRow[]>([]);
  const [overwrite, setOverwrite] = useState(false);
  const [running, setRunning] = useState(false);

  const [searchParams, setSearchParams] = useSearchParams();
  const [matches, setMatches] = useState<RecentProject[] | null>(null);
  const [matchesError, setMatchesError] = useState<string | null>(null);
  const urlMatchId = searchParams.get("match");
  const matchId = useMemo(() => {
    if (matches === null) return null;
    if (urlMatchId && matches.some((m) => m.match_id === urlMatchId)) {
      return urlMatchId;
    }
    return matches[0]?.match_id ?? null;
  }, [matches, urlMatchId]);

  useEffect(() => {
    if (!open || matches !== null) return;
    let alive = true;
    api
      .getRecentProjects()
      .then((all) => {
        if (!alive) return;
        setMatches(all.filter((p) => p.kind === "match" && p.match_id));
      })
      .catch((err) => {
        if (alive) setMatchesError(String(err));
      });
    return () => {
      alive = false;
    };
  }, [open, matches]);

  // Re-derive rows when the catalog changes (so "exists" badges update after
  // a successful batch run without re-fetching the projects).
  useEffect(() => {
    if (shooterData.length === 0) return;
    setRows((prev) => {
      const existing = new Set(catalog.map((f) => f.slug));
      return prev.map((r) => ({ ...r, exists: existing.has(r.slug) }));
    });
  }, [catalog, shooterData]);

  const load = useCallback(
    async (mid: string) => {
      setLoading(true);
      setLoadError(null);
      setShooterData([]);
      setSelectedShooters(new Set());
      setRows([]);
      try {
        // Every shooter runs the same stage list, but the audits, beeps
        // and videos are per shooter -- so load each shooter's project
        // and offer rows for all of them (all selected by default).
        const shooters = await api.listMatchShootersIn(mid);
        const data = await Promise.all(
          shooters.shooters.map(async (s) => {
            const [proj, ov] = await Promise.all([
              api.getProjectIn(mid, s.slug),
              api.getExportOverviewIn(mid, s.slug),
            ]);
            return {
              slug: s.slug,
              name: s.name,
              project: proj,
              overview: ov.stages,
            } satisfies ShooterBatch;
          }),
        );
        setShooterData(data);
        setSelectedShooters(new Set(data.map((d) => d.slug)));
        setRows(data.flatMap((d) => buildBatchRows(d, catalog)));
      } catch (err) {
        setLoadError(String(err));
      } finally {
        setLoading(false);
      }
    },
    [catalog],
  );

  // Toggling a shooter rebuilds the row set from the cached per-shooter
  // data. Per-row manual (de)selections reset on toggle -- acceptable
  // for a batch tool, and it keeps one source of truth for row state.
  const toggleShooter = useCallback(
    (slug: string) => {
      setSelectedShooters((prev) => {
        const next = new Set(prev);
        if (next.has(slug)) next.delete(slug);
        else next.add(slug);
        setRows(
          shooterData
            .filter((d) => next.has(d.slug))
            .flatMap((d) => buildBatchRows(d, catalog)),
        );
        return next;
      });
    },
    [shooterData, catalog],
  );

  // (Re)load whenever the panel is open on a resolved match -- covers
  // first open, a selector change, and the ?match= default resolving
  // once the recents list arrives.
  useEffect(() => {
    if (!open || matchId === null) return;
    void load(matchId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, matchId]);

  const toggleOpen = useCallback(() => {
    setOpen((v) => !v);
  }, []);

  const selectMatch = useCallback(
    (mid: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("match", mid);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const toggleRow = useCallback((shooterSlug: string, stageNumber: number) => {
    setRows((prev) =>
      prev.map((r) =>
        r.shooterSlug === shooterSlug &&
        r.stageNumber === stageNumber &&
        r.blockers.length === 0
          ? { ...r, selected: !r.selected }
          : r,
      ),
    );
  }, []);

  const setAllSelected = useCallback((selected: boolean) => {
    setRows((prev) =>
      prev.map((r) => (r.blockers.length === 0 ? { ...r, selected } : r)),
    );
  }, []);

  const submit = useCallback(async () => {
    if (matchId === null) return;
    setRunning(true);
    const queue = rows.filter((r) => r.selected && r.blockers.length === 0);
    const sameRow = (a: BatchRow, b: BatchRow) =>
      a.shooterSlug === b.shooterSlug && a.stageNumber === b.stageNumber;
    setRows((prev) =>
      prev.map((r) =>
        queue.find((q) => sameRow(q, r))
          ? { ...r, status: "running", message: null }
          : r,
      ),
    );
    for (const row of queue) {
      try {
        const rec = await api.promoteFixtureIn(matchId, {
          stage_number: row.stageNumber,
          slug: row.slug,
          shooter_slug: row.shooterSlug,
          overwrite,
        });
        setRows((prev) =>
          prev.map((r) =>
            sameRow(r, row) ? { ...r, status: "ok", message: rec.audit_path } : r,
          ),
        );
      } catch (err) {
        setRows((prev) =>
          prev.map((r) =>
            sameRow(r, row) ? { ...r, status: "error", message: String(err) } : r,
          ),
        );
      }
    }
    try {
      const next = await api.listLabFixtures();
      onCatalogChanged(next);
    } catch {
      // Catalog refresh is best-effort; row-level "ok" status already
      // confirms the server accepted each promote.
    }
    setRunning(false);
  }, [rows, overwrite, onCatalogChanged, matchId]);

  const eligibleCount = rows.filter((r) => r.blockers.length === 0).length;
  const selectedCount = rows.filter(
    (r) => r.selected && r.blockers.length === 0,
  ).length;
  const allEligibleSelected = eligibleCount > 0 && selectedCount === eligibleCount;

  const isSection = variant === "section";

  return (
    <div className={isSection ? "contents" : "relative"}>
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5"
        onClick={toggleOpen}
        title="Promote every eligible stage in this project as a primary fixture"
      >
        <FlaskConical className="size-3.5" />
        Promote all stages
      </Button>
      {open && (
        <div
          className={
            isSection
              ? "mt-3 w-full rounded-md border border-rule bg-surface p-5"
              : "absolute right-0 top-full z-20 mt-1 w-[640px] rounded-md border border-rule bg-surface-2 p-4 shadow-md"
          }
          style={isSection ? { boxShadow: "inset 0 1px 0 rgba(6,182,212,0.1)" } : undefined}
        >
          <div
            className={
              isSection
                ? "mb-3 flex items-center gap-2.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.18em] text-beep"
                : "text-xs font-semibold uppercase tracking-wide text-muted mb-2"
            }
          >
            {isSection && <span aria-hidden className="h-px w-6 bg-beep" />}
            Promote all stages
          </div>
          <p className="text-[11px] text-muted mb-3">
            Batch-runs the per-stage primary-fixture promote against every
            stage that has an audit JSON, a primary beep, and a camera mount.
            Same write path as the single-stage button on the Audit page.
          </p>
          {matchesError && (
            <div className="flex gap-1.5 rounded bg-destructive/10 px-2 py-1.5 text-[11px] text-destructive">
              <AlertCircle className="size-3.5 mt-0.5 shrink-0" />
              {matchesError}
            </div>
          )}
          {matches !== null && matches.length === 0 && (
            <div className="rounded bg-muted px-2 py-1.5 text-[11px] text-muted">
              No matches in the recent-projects list. Open one in Match mode
              first so it lands there.
            </div>
          )}
          {matches !== null && matches.length > 0 && (
            <label className="mb-3 flex items-center gap-2 text-[11px] text-muted">
              Match
              <select
                value={matchId ?? ""}
                onChange={(e) => selectMatch(e.target.value)}
                disabled={running}
                className="min-w-0 flex-1 rounded border border-rule bg-bg px-2 py-1 text-xs text-ink"
              >
                {matches.map((m) => (
                  <option key={m.match_id ?? m.path} value={m.match_id ?? ""}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {(loading || (matches === null && !matchesError)) && (
            <div className="flex items-center gap-2 py-6 text-xs text-muted">
              <Loader2 className="size-3.5 animate-spin" />
              Loading project + export overview...
            </div>
          )}
          {!loading && loadError && (
            <div className="flex gap-1.5 rounded bg-destructive/10 px-2 py-1.5 text-[11px] text-destructive">
              <AlertCircle className="size-3.5 mt-0.5 shrink-0" />
              {loadError}
            </div>
          )}
          {!loading && !loadError && shooterData.length > 0 && (
            <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted">
              <span>Shooters</span>
              {shooterData.map((s) => (
                <label
                  key={s.slug}
                  className="flex items-center gap-1.5 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={selectedShooters.has(s.slug)}
                    onChange={() => toggleShooter(s.slug)}
                    disabled={running}
                  />
                  {s.name}
                </label>
              ))}
            </div>
          )}
          {!loading && !loadError && shooterData.length > 0 && rows.length === 0 && (
            <div className="rounded bg-muted px-2 py-1.5 text-[11px] text-muted">
              No non-placeholder stages for the selected shooters.
            </div>
          )}
          {!loading && !loadError && rows.length > 0 && (
            <>
              <div className="mb-2 flex items-center justify-between text-[11px] text-muted">
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={allEligibleSelected}
                    onChange={(e) => setAllSelected(e.target.checked)}
                    disabled={running || eligibleCount === 0}
                  />
                  Select all eligible ({eligibleCount})
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={overwrite}
                    onChange={(e) => setOverwrite(e.target.checked)}
                    disabled={running}
                  />
                  Overwrite if exists
                </label>
              </div>
              <div className="max-h-80 overflow-y-auto rounded border border-rule">
                <table className="w-full text-[11px]">
                  <thead className="sticky top-0 bg-muted/60 text-left text-muted">
                    <tr>
                      <th className="px-2 py-1.5 w-6"></th>
                      <th className="px-2 py-1.5 w-10">#</th>
                      <th className="px-2 py-1.5 w-28">Shooter</th>
                      <th className="px-2 py-1.5">Stage / slug</th>
                      <th className="px-2 py-1.5 w-32">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const eligible = row.blockers.length === 0;
                      const willOverwrite = row.exists && overwrite;
                      const blocked = !eligible;
                      return (
                        <tr
                          key={`${row.shooterSlug}-${row.stageNumber}`}
                          className={cn(
                            "border-t border-rule align-top",
                            blocked && "opacity-60",
                          )}
                        >
                          <td className="px-2 py-1.5">
                            <input
                              type="checkbox"
                              checked={row.selected && eligible}
                              disabled={!eligible || running}
                              onChange={() =>
                                toggleRow(row.shooterSlug, row.stageNumber)
                              }
                            />
                          </td>
                          <td className="px-2 py-1.5 font-mono">
                            {row.stageNumber}
                          </td>
                          <td className="px-2 py-1.5 truncate max-w-28">
                            {row.shooterName}
                          </td>
                          <td className="px-2 py-1.5">
                            <div className="font-medium">{row.stageName}</div>
                            <div className="mt-0.5 font-mono text-[10px] text-muted break-all">
                              {row.slug}
                            </div>
                            {row.blockers.length > 0 && (
                              <div className="mt-0.5 text-[10px] text-amber-700 dark:text-amber-300">
                                blocked: {row.blockers.join("; ")}
                              </div>
                            )}
                            {row.status === "error" && row.message && (
                              <div className="mt-0.5 text-[10px] text-destructive break-words">
                                {row.message}
                              </div>
                            )}
                            {row.status === "ok" && row.message && (
                              <div className="mt-0.5 font-mono text-[10px] text-emerald-700 dark:text-emerald-300 break-all">
                                {row.message}
                              </div>
                            )}
                          </td>
                          <td className="px-2 py-1.5">
                            {row.status === "running" && (
                              <span className="inline-flex items-center gap-1 text-muted">
                                <Loader2 className="size-3 animate-spin" />
                                promoting
                              </span>
                            )}
                            {row.status === "ok" && (
                              <span className="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-300">
                                <CheckCircle2 className="size-3" />
                                promoted
                              </span>
                            )}
                            {row.status === "error" && (
                              <span className="inline-flex items-center gap-1 text-destructive">
                                <XCircle className="size-3" />
                                failed
                              </span>
                            )}
                            {row.status === "idle" && eligible && row.exists && (
                              <span
                                className={cn(
                                  "inline-flex items-center gap-1",
                                  willOverwrite
                                    ? "text-amber-700 dark:text-amber-300"
                                    : "text-muted",
                                )}
                                title={
                                  willOverwrite
                                    ? "A fixture with this slug exists; overwrite is on so it will be replaced."
                                    : "A fixture with this slug exists; toggle 'Overwrite if exists' to replace it. Otherwise the server will reject this row."
                                }
                              >
                                exists
                                {willOverwrite ? " (overwrite)" : ""}
                              </span>
                            )}
                            {row.status === "idle" && eligible && !row.exists && (
                              <span className="text-muted">
                                ready
                              </span>
                            )}
                            {row.status === "idle" && !eligible && (
                              <span className="text-muted">
                                blocked
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <div className="flex items-center gap-2 pt-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setOpen(false)}
              disabled={running}
            >
              Close
            </Button>
            <span className="ml-auto text-[11px] text-muted">
              {selectedCount} selected
            </span>
            <Button
              size="sm"
              onClick={submit}
              disabled={running || selectedCount === 0}
            >
              {running ? (
                <Loader2 className="size-3.5 animate-spin mr-1" />
              ) : null}
              Promote selected
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
