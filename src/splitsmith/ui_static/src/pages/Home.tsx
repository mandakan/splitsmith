/**
 * Home -- the match Overview (spec 2026-09-13 s4.2, UX PR 3).
 *
 * Answers "what is blocking this match and what do I do next" in one
 * screen: a PageHeader whose primary button is the loop's next step, the
 * hosted-sync row on desktop installs, five stats, and the stage pipeline
 * table (one row per stage, one action per row). Beep review's Confirm
 * and Triage's Accept are row actions here; those pages stay until PR 5
 * and PR 8 retire them.
 *
 * Everything the table shows comes from one ``GET /api/match/triage``
 * (whose cells carry footage, beep, shot and split figures) derived
 * through lib/overview.ts. Accept returns the fresh triage list, so the
 * page never re-derives state on its own.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useOutletContext, useParams } from "react-router-dom";

import { EditStagesDrawer } from "@/components/match/EditStagesDrawer";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { SyncCard } from "@/components/match/SyncCard";
import { OverviewCards } from "@/components/overview/OverviewCards";
import { OverviewTable, type OverviewHrefs } from "@/components/overview/OverviewTable";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Stat, StatStrip } from "@/components/ui/Stat";
import {
  api,
  apiErrorText,
  capabilityDenied,
  type MatchStageDefinition,
  type TriageResponse,
} from "@/lib/api";
import { pickDefaultShooterSlug } from "@/lib/defaultShooter";
import { useDeploymentMode } from "@/lib/features";
import { isJobActive } from "@/lib/jobs";
import { useMatchHref } from "@/lib/matchHref";
import {
  buildOverviewRows,
  formatClock,
  nextAction,
  overviewStats,
} from "@/lib/overview";
import { useIsMobile } from "@/lib/useIsMobile";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function formatDate(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d.getUTCDate()} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

function fmt(v: number | null, digits: number): string {
  return v == null ? "\u2014" : v.toFixed(digits);
}

export function Home() {
  const href = useMatchHref();
  const ctx = useOutletContext<MatchShellOutletContext>();
  const project = ctx?.project ?? null;
  const shooters = useMemo(() => ctx?.shooters ?? [], [ctx?.shooters]);
  const jobs = useMemo(() => ctx?.jobs ?? [], [ctx?.jobs]);
  const { matchId } = useParams<{ matchId: string }>();
  const { mode: deploymentMode } = useDeploymentMode();
  const isMobile = useIsMobile();
  const editDenied = capabilityDenied(ctx?.capabilities, "edit");

  const [triage, setTriage] = useState<TriageResponse | null>(null);
  const [triageError, setTriageError] = useState<string | null>(null);
  const [filterSlug, setFilterSlug] = useState<string | null>(null);
  const [matchStages, setMatchStages] = useState<MatchStageDefinition[] | null>(null);
  const [stagesLoading, setStagesLoading] = useState(false);
  const [stagesError, setStagesError] = useState<string | null>(null);
  const [editStagesOpen, setEditStagesOpen] = useState(false);
  const [acceptError, setAcceptError] = useState<string | null>(null);

  const loadTriage = useCallback(async () => {
    try {
      setTriage(await api.getTriage());
      setTriageError(null);
    } catch (e) {
      setTriageError(apiErrorText(e, "Could not load the stage list."));
    }
  }, []);

  useEffect(() => {
    void loadTriage();
  }, [loadTriage, project?.name]);

  // Refetch when a pipeline job settles: the shell's poller is the
  // signal (one poller per shell), the settled id set is the trigger.
  const prevActiveRef = useRef<ReadonlySet<string>>(new Set());
  useEffect(() => {
    const activeNow = new Set(jobs.filter(isJobActive).map((j) => j.id));
    const settled = [...prevActiveRef.current].some((id) => !activeNow.has(id));
    prevActiveRef.current = activeNow;
    if (settled) void loadTriage();
  }, [jobs, loadTriage]);

  const leadSlug = pickDefaultShooterSlug(shooters) ?? null;
  const rows = useMemo(
    () => (triage ? buildOverviewRows({ triage, shooters, leadSlug, jobs }) : []),
    [triage, shooters, leadSlug, jobs],
  );
  const stats = useMemo(() => overviewStats(rows), [rows]);
  const next = useMemo(() => nextAction(rows), [rows]);
  const noFootage = rows.length > 0 && stats.needsFootage === stats.total;

  const hrefs = useMemo<OverviewHrefs>(
    () => ({
      audit: (slug, stage) => href("audit", slug, String(stage)),
      splits: (slug, stage) => href("results", slug, String(stage)),
      footage: (slug) => href("ingest", slug),
      beep: (_slug, stage) => `${href("beep-review")}?stage=${stage}`,
    }),
    [href],
  );

  async function accept(slug: string, stage: number) {
    setAcceptError(null);
    try {
      setTriage(await api.acceptStage(slug, stage));
      ctx?.refresh();
    } catch (e) {
      setAcceptError(apiErrorText(e, "Could not accept the stage."));
    }
  }

  async function openEditStages() {
    setStagesLoading(true);
    setStagesError(null);
    try {
      const r = await api.getMatchStages();
      setMatchStages(r.stages);
      setEditStagesOpen(true);
    } catch (e) {
      setStagesError(apiErrorText(e, "Could not load the stage list."));
    } finally {
      setStagesLoading(false);
    }
  }

  function handleStagesSaved() {
    ctx?.refresh();
    void loadTriage();
    api
      .getMatchStages()
      .then((r) => setMatchStages(r.stages))
      .catch(() => {});
  }

  if (!project) {
    return <p className="px-7 py-10 text-md text-muted">Reading match state...</p>;
  }

  const shooterLine =
    shooters.length > 1
      ? `${shooters.length} shooters`
      : (shooters[0]?.name ?? project.competitor_name ?? null);

  // The one primary action on the page.
  const primary = next ? (
    <Button variant="primary" asChild>
      <Link
        to={
          next.cell.action.kind === "confirm_beep"
            ? hrefs.beep(next.cell.slug, next.row.stageNumber)
            : hrefs.audit(next.cell.slug, next.row.stageNumber)
        }
      >
        {next.cell.action.kind === "confirm_beep" ? "Confirm beep" : "Audit"} {pad2(next.row.stageNumber)}{" "}
        {next.row.stageName}
        {shooters.length > 1 && filterSlug == null ? ` \u00b7 ${next.cell.shooterName}` : ""}
      </Link>
    </Button>
  ) : noFootage ? (
    <Button variant="primary" asChild>
      <Link to={leadSlug ? hrefs.footage(leadSlug) : href("shooters")}>Add footage</Link>
    </Button>
  ) : rows.length > 0 ? (
    <Button variant="primary" asChild>
      <Link to={href("results")}>Splits</Link>
    </Button>
  ) : null;

  return (
    <div className="px-4 py-4 md:px-7 md:py-5">
      <PageHeader
        title={project.name}
        sub={
          <>
            {project.match_date ? <time dateTime={project.match_date}>{formatDate(project.match_date)}</time> : null}
            {shooterLine ? <> &middot; {shooterLine}</> : null}
            {project.scoreboard_match_id ? (
              <>
                {" "}
                &middot;{" "}
                <a
                  href={`https://scoreboard.urdr.dev/match/${project.scoreboard_content_type}/${project.scoreboard_match_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-ink-2 hover:text-ink"
                >
                  View on scoreboard &#8599;
                </a>
              </>
            ) : null}
          </>
        }
        actions={
          <>
            {editDenied ? null : (
              <Button type="button" onClick={() => void openEditStages()} disabled={stagesLoading}>
                Edit stages
              </Button>
            )}
            {primary}
          </>
        }
      >
        {shooters.length > 1 ? (
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by shooter">
            <button type="button" onClick={() => setFilterSlug(null)} aria-pressed={filterSlug == null}>
              <Chip tone={filterSlug == null ? "ok" : "neutral"}>All</Chip>
            </button>
            {shooters.map((s) => (
              <button
                key={s.slug}
                type="button"
                onClick={() => setFilterSlug(s.slug === filterSlug ? null : s.slug)}
                aria-pressed={filterSlug === s.slug}
              >
                <Chip tone={filterSlug === s.slug ? "ok" : "neutral"} tick={s.slug === leadSlug ? "draw" : "muted"}>
                  {s.name}
                </Chip>
              </button>
            ))}
          </div>
        ) : null}
      </PageHeader>

      {stagesError ? (
        <p role="alert" className="mb-3 text-sm text-led-text">
          {stagesError}
        </p>
      ) : null}
      {acceptError ? (
        <p role="alert" className="mb-3 text-sm text-led-text">
          {acceptError}
        </p>
      ) : null}
      {triageError ? (
        <p role="alert" className="mb-3 text-sm text-led-text">
          {triageError}
        </p>
      ) : null}

      {deploymentMode === "local" ? <SyncCard jobs={jobs} matchId={matchId} /> : null}

      <StatStrip lead className="mb-4">
        <Stat label="Audited" value={String(stats.audited)} unit={`/ ${stats.total}`} />
        <Stat label="Needs footage" value={String(stats.needsFootage)} />
        <Stat label="Avg draw" value={fmt(stats.avgDraw, 2)} unit={stats.avgDraw != null ? "s" : undefined} tone={stats.avgDraw == null ? "dim" : "ink"} />
        <Stat label="Avg split" value={fmt(stats.avgSplit, 3)} unit={stats.avgSplit != null ? "s" : undefined} tone={stats.avgSplit == null ? "dim" : "ink"} />
        <Stat label="Scored time" value={stats.scoredTime != null ? formatClock(stats.scoredTime) : "\u2014"} tone={stats.scoredTime == null ? "dim" : "ink"} />
      </StatStrip>

      {triage == null ? (
        <p className="py-6 text-center text-md text-muted">Loading stages...</p>
      ) : noFootage ? (
        <div className="rounded-[10px] border border-dashed border-rule-strong px-6 py-7 text-center text-md text-muted">
          <p className="mx-auto max-w-[52ch]">
            {stats.total} {stats.total === 1 ? "stage" : "stages"}, no footage yet. Drop your camera&apos;s files on the
            Footage page and each one is matched to its stage by recording time.
          </p>
          <div className="mt-4 inline-flex">
            <Button variant="primary" asChild>
              <Link to={leadSlug ? hrefs.footage(leadSlug) : href("shooters")}>Add footage</Link>
            </Button>
          </div>
        </div>
      ) : isMobile ? (
        <OverviewCards
          rows={rows}
          filterSlug={filterSlug}
          currentStage={next?.row.stageNumber ?? null}
          editDenied={editDenied}
          hrefs={hrefs}
          onAccept={(slug, stage) => void accept(slug, stage)}
        />
      ) : (
        <OverviewTable
          rows={rows}
          multi={shooters.length > 1}
          filterSlug={filterSlug}
          currentStage={next?.row.stageNumber ?? null}
          threshold={triage.beep_low_confidence_threshold}
          editDenied={editDenied}
          hrefs={hrefs}
          onAccept={(slug, stage) => void accept(slug, stage)}
        />
      )}

      {matchStages && !editDenied ? (
        <EditStagesDrawer
          open={editStagesOpen}
          onClose={() => setEditStagesOpen(false)}
          stages={matchStages}
          shooterCount={shooters.length || 1}
          onSaved={handleStagesSaved}
        />
      ) : null}
    </div>
  );
}
