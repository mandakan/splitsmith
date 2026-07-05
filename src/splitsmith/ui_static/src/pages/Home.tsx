/**
 * MatchOverview route (/) -- the per-match mission-briefing dashboard (#323).
 *
 * Two variants picked from the bound project's state:
 *
 *   Active -- resume hero (jump back into the in-progress stage),
 *   headline stats, shooter strip, stage grid, recent activity log.
 *   Renders when at least one stage has a primary video assigned.
 *
 *   Empty -- "just created" hero with the ingest CTA, empty shooter
 *   slots, awaiting-footage stage tiles, three help cards. Renders
 *   when no stage has footage yet (e.g. immediately after a
 *   create-manual flow).
 *
 * Mounted under <MatchShell />, so the shell owns the brand header,
 * mode switch, breadcrumb, and the per-match sidebar (Overview /
 * Coach / Shooters / Export + per-stage status). This file only
 * handles the workspace below the page head.
 */

import {
  ArrowDownToLine,
  ArrowRight,
  Plus,
  Timer,
  UploadCloud,
  Users,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useOutletContext } from "react-router-dom";

import { Avatar, Kicker } from "@/components/ui";
import { Button } from "@/components/ui/button";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import {
  api,
  type MatchProject,
  type ShooterListEntry,
  type StageEntry,
  type StageStatus,
} from "@/lib/api";
import {
  deriveStageStatus,
  isNextUpCandidate,
  statusLabel,
} from "@/lib/stageStatus";
import {
  buildStageMatrix,
  matchTotals,
  type MatchTotals,
  type StageMatrixRow,
} from "@/lib/stageMatrix";
import { useMatchHref } from "@/lib/matchHref";
import { cn } from "@/lib/utils";

// Visual tone the home stage card switches on. Derived from the
// canonical :type:`StageStatus` via :func:`statusTone` -- we don't
// recompute "audited" client-side. "flagged" is a future visual tier
// the design system reserves for stages that need attention (e.g.
// failed detection); no backend status maps to it today.
type StagePillTone = "done" | "in_progress" | "ready" | "partial" | "flagged" | "todo" | "skipped";

interface StageView {
  stage: StageEntry;
  status: StageStatus;
  shotCount: number;
  expectedShots: number | null;
  tone: StagePillTone;
  isNextUp: boolean;
}

export function Home() {
  const navigate = useNavigate();
  const href = useMatchHref();
  const ctx = useOutletContext<MatchShellOutletContext>();
  const project = ctx?.project ?? null;
  const [shooters, setShooters] = useState<ShooterListEntry[]>([]);
  // Slug used for slug-bearing nav links from this page. The
  // ``/api/health.default_shooter_slug`` field this used to read was
  // retired with the bound-state singleton (doc 10 Tier 1 step 4) and
  // now always returns null, which made "Add shooter footage" silently
  // fall back to the Shooters page even on single-shooter matches.
  // Pick the alphabetically-first shooter to match the server's old
  // ``default_shooter_slug`` derivation in ``_register_response``.
  const navSlug = useMemo<string | null>(() => {
    if (shooters.length === 0) return null;
    return [...shooters].sort((a, b) => a.slug.localeCompare(b.slug))[0].slug;
  }, [shooters]);

  useEffect(() => {
    let alive = true;
    api
      .listMatchShooters()
      .then((r) => {
        if (alive) setShooters(r.shooters);
      })
      .catch(() => {
        // 409 no_match: bound project is a legacy single-shooter project.
        // The active project itself is the shooter -- leave shooters empty
        // and the variants will fall back to the legacy single-card view.
        if (alive) setShooters([]);
      });
    // Cancel on unmount / dep change so a late response can't setState on a
    // stale render (this cleanup was missing).
    return () => {
      alive = false;
    };
  }, [project?.name]);

  const stageRows = useMemo<StageMatrixRow[]>(
    () => (project ? buildStageMatrix(project.stages, shooters) : []),
    [project, shooters],
  );
  const totals = useMemo<MatchTotals>(
    () => matchTotals(stageRows, shooters),
    [stageRows, shooters],
  );

  const stageViews = useMemo<StageView[]>(() => {
    if (!project) return [];
    const views = project.stages.map<StageView>((s) => {
      const status = deriveStageStatus(s);
      const hasVideo = (s.videos ?? []).some((v) => v.role === "primary");
      const expected = expectedShotsFromStage(s);
      return {
        stage: s,
        status,
        // Note: shotCount here is a placeholder estimate (stage time in
        // seconds, floored) until we surface real shot counts from the
        // audit JSON. The status tells the truth; this number is a
        // cosmetic value rendered next to the chip.
        shotCount: hasVideo ? Math.max(0, Math.floor(s.time_seconds)) : 0,
        expectedShots: expected,
        tone: toneForStatus(status),
        isNextUp: false,
      };
    });
    const nextIdx = views.findIndex((v) => isNextUpCandidate(v.status));
    if (nextIdx >= 0) views[nextIdx].isNextUp = true;
    return views;
  }, [project]);

  // Aggregate gate: the Overview is "empty" only when NO shooter in the
  // match has footage. A single footage-less shooter no longer blanks the
  // whole page (the pre-aggregate bug). Legacy single-shooter projects
  // (empty roster) fall back to the per-project stage check.
  const isEmpty =
    !project ||
    stageViews.length === 0 ||
    (shooters.length > 0
      ? !totals.hasAnyFootage
      : stageViews.every((v) => v.status === "todo"));

  if (!project) {
    return (
      <div className="px-8 py-16 text-center text-muted">
        <Kicker>Loading</Kicker>
        <p className="mt-4 text-sm">Reading match state...</p>
      </div>
    );
  }

  return (
    <>
      <div className="border-b border-rule bg-gradient-to-b from-surface to-transparent px-8 pb-6 pt-7">
        <Kicker className="mb-2.5">
          {isEmpty ? "Just created" : "Match Overview"}
        </Kicker>
        <div className="mb-3 flex flex-wrap items-center gap-3.5">
          <h1 className="font-display text-[2.75rem] font-bold uppercase leading-none tracking-tight text-ink">
            {project.name}
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2 font-mono text-xs uppercase tracking-[0.04em] text-muted">
          {project.match_date ? (
            <time
              dateTime={project.match_date}
              className="border-r border-rule pr-3 font-bold text-ink-2"
            >
              {formatDate(project.match_date)}
            </time>
          ) : null}
          {project.scoreboard_match_id && (
            <>
              <span className="text-whisper">&middot;</span>
              <a
                href={`https://scoreboard.urdr.dev/${project.scoreboard_content_type}/${project.scoreboard_match_id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-led hover:text-led-soft"
              >
                View on scoreboard
                <svg
                  width="11"
                  height="11"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M7 17L17 7M7 7h10v10" />
                </svg>
              </a>
            </>
          )}
          {isEmpty && (
            <span className="inline-flex items-center rounded border border-beep/40 bg-beep-tint px-2.5 py-1 font-display text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-beep">
              Awaiting footage
            </span>
          )}
        </div>
        <div className="mt-4 inline-flex gap-2.5">
          <Button
            variant="outline"
            onClick={() =>
              navigate(navSlug ? href("export", navSlug) : href("shooters"))
            }
          >
            <ArrowDownToLine className="size-3.5" />
            <span className="font-display uppercase tracking-[0.08em]">
              Export Match
            </span>
          </Button>
        </div>
      </div>

      <div className="mx-auto max-w-[1280px] px-8 pb-20 pt-6">
        {isEmpty ? (
          <EmptyVariant
            project={project}
            stageViews={stageViews}
            shooters={shooters}
            navSlug={navSlug}
          />
        ) : (
          <ActiveVariant
            project={project}
            rows={stageRows}
            totals={totals}
            shooters={shooters}
          />
        )}
      </div>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Active variant                                                             */
/* -------------------------------------------------------------------------- */

function ActiveVariant({
  project,
  rows,
  totals,
  shooters,
}: {
  project: MatchProject;
  rows: StageMatrixRow[];
  totals: MatchTotals;
  shooters: ShooterListEntry[];
}) {
  const navigate = useNavigate();
  const href = useMatchHref();
  // Shooter-stages stalled on a missing stage time: footage is attached
  // but time_seconds <= 0, so trim and shot detection silently wait
  // (the backend's ``partial`` status). Legacy single-shooter projects
  // (empty roster -> zero-cell rows) don't feed this banner.
  const partialCells = rows.flatMap((row) =>
    row.cells
      .filter((cell) => cell.status === "partial")
      .map((cell) => ({ slug: cell.shooter.slug, stage: row.stageNumber })),
  );
  return (
    <>
      {partialCells.length > 0 && (
        <div className="mb-6 flex flex-wrap items-center gap-3.5 rounded-xl border border-beep/40 bg-beep-tint px-5 py-3">
          <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-full border border-beep/40 bg-surface-2 text-beep">
            <Timer className="size-3.5" />
          </span>
          <span className="flex-1 font-mono text-[0.75rem] uppercase tracking-[0.06em] text-ink-2">
            <b className="font-bold text-beep">{partialCells.length}</b>{" "}
            shooter-stage{partialCells.length === 1 ? "" : "s"} missing a
            stage time &middot;{" "}
            <span className="text-muted">
              trim and shot detection wait until a time is entered
            </span>
          </span>
          <button
            type="button"
            onClick={() =>
              navigate(
                href(
                  "audit",
                  partialCells[0].slug,
                  String(partialCells[0].stage),
                ),
              )
            }
            className="inline-flex items-center gap-1.5 font-display text-[0.6875rem] font-bold uppercase tracking-[0.1em] text-beep hover:text-ink"
          >
            Enter stage time <ArrowRight className="size-3" />
          </button>
        </div>
      )}
      {/* Match-progress summary */}
      <section
        className="relative mb-6 overflow-hidden rounded-2xl border border-rule-strong p-7 shadow-[inset_0_1px_0_rgba(255,255,255,0.04),0_24px_48px_-24px_rgba(0,0,0,0.6)]"
        style={{
          backgroundImage:
            "radial-gradient(900px 220px at 20% 30%, rgba(255,45,45,0.10), transparent 65%), linear-gradient(135deg, var(--color-surface) 0%, var(--color-surface-2) 100%)",
        }}
      >
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-[3px] bg-led shadow-[0_0_16px_var(--color-led-glow)]"
        />
        <div className="relative z-10">
          <div className="mb-2.5 inline-flex items-center gap-2.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.2em] text-led">
            <span
              aria-hidden
              className="inline-block size-1.5 rounded-full bg-led shadow-[0_0_8px_var(--color-led-glow)]"
            />
            Match Overview
          </div>
          <h2 className="mb-3 font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
            {totals.auditedShooterStages} of {totals.totalShooterStages}{" "}
            <span className="text-led">shooter-stages</span> audited
          </h2>
          <div
            className="mb-4 h-2 w-full max-w-xl overflow-hidden rounded-full bg-surface-3"
            role="progressbar"
            aria-valuenow={totals.auditedPct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Match audited percentage"
          >
            <span
              className="block h-full rounded-full bg-led shadow-[0_0_12px_var(--color-led-glow)]"
              style={{ width: `${totals.auditedPct}%` }}
            />
          </div>
          <div className="inline-flex flex-wrap md:flex-nowrap overflow-hidden rounded-[10px] border border-rule bg-surface-3">
            <HeroStat
              label="Match audited"
              value={`${totals.auditedPct}%`}
              tone="led"
            />
            <HeroStat
              label="Fully done"
              value={pad2(totals.stagesFullyDone)}
              tone={totals.stagesFullyDone > 0 ? "led" : undefined}
            />
            <HeroStat
              label="In progress"
              value={pad2(totals.stagesInProgress)}
              tone={totals.stagesInProgress > 0 ? "live" : undefined}
            />
            <HeroStat label="Untouched" value={pad2(totals.stagesUntouched)} />
          </div>
        </div>
      </section>

      <SectionHead
        title="Shooters"
        count={
          <>
            <b className="font-bold text-ink-2">{pad2(shooters.length || 1)}</b>{" "}
            in this match
          </>
        }
        action={
          <button
            type="button"
            onClick={() => navigate(href("shooters"))}
            className="link-led-fill inline-flex items-center gap-1.5"
          >
            Manage shooters
            <ArrowRight className="size-3.5" />
          </button>
        }
      />
      <div className="mb-7 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[repeat(auto-fill,minmax(240px,1fr))]">
        {shooters.length > 0 ? (
          // No `you` ring here yet: the "currently bound" shooter is a
          // session detail, not the operator's identity. Coaching workflows
          // mean the operator may not be a shooter in this match at all.
          // See #350 for the real operator-vs-shooter split.
          shooters.map((s) => (
            <ShooterCard
              key={s.slug}
              name={s.name}
              // "{audited}/{total} audited" instead of a bare "{total}
              // stages" -- the latter read as "this shooter has N stages"
              // and clashed with the match's stage count elsewhere. The
              // ratio names the metric so it can't be misread.
              stats={
                s.stages_total > 0
                  ? `${s.stages_audited}/${s.stages_total} audited`
                  : "no stages yet"
              }
              progress={
                s.stages_total > 0 ? s.stages_audited / s.stages_total : 0
              }
            />
          ))
        ) : (
          // Legacy single-shooter project: no /api/match/shooters listing,
          // fall back to the bound MatchProject's competitor.
          <ShooterCard
            name={project.competitor_name ?? "You"}
            stats={`${rows.length} stages`}
            progress={
              totals.totalShooterStages > 0
                ? totals.auditedShooterStages / totals.totalShooterStages
                : 0
            }
          />
        )}
        <AddShooterCard onClick={() => navigate(href("shooters"))} />
      </div>

      <SectionHead
        title="Stages"
        count={
          <>
            <b className="font-bold text-ink-2">{pad2(totals.stagesFullyDone)}</b>{" "}
            fully done <span className="text-whisper">&middot;</span>{" "}
            <b className="font-bold text-ink-2">{pad2(totals.stagesInProgress)}</b>{" "}
            in progress <span className="text-whisper">&middot;</span>{" "}
            <b className="font-bold text-ink-2">{pad2(totals.stagesUntouched)}</b>{" "}
            untouched
          </>
        }
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[repeat(auto-fill,minmax(240px,1fr))]">
        {rows.map((row) => (
          <AggregateStageTile
            key={row.stageNumber}
            row={row}
            hrefFor={(slug, stage) => href("audit", slug, String(stage))}
          />
        ))}
      </div>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Empty variant                                                              */
/* -------------------------------------------------------------------------- */

function EmptyVariant({
  project,
  stageViews,
  shooters,
  navSlug,
}: {
  project: MatchProject;
  stageViews: StageView[];
  shooters: ShooterListEntry[];
  navSlug: string | null;
}) {
  const navigate = useNavigate();
  const href = useMatchHref();
  const ingestHref = navSlug ? href("ingest", navSlug) : href("shooters");
  return (
    <>
      <section
        className="relative mb-8 overflow-hidden rounded-2xl border border-rule-strong px-12 py-14 text-center shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_24px_48px_-24px_rgba(0,0,0,0.7)]"
        style={{
          backgroundImage:
            "radial-gradient(800px 300px at 30% 30%, rgba(255,45,45,0.10), transparent 65%), linear-gradient(180deg, var(--color-surface) 0%, var(--color-surface-2) 100%)",
        }}
      >
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-[3px] bg-led shadow-[0_0_16px_var(--color-led-glow)]"
        />
        <div className="mx-auto mb-4 inline-flex size-[72px] items-center justify-center rounded-2xl border border-led-deep bg-led/10 text-led shadow-[0_0_24px_var(--color-led-glow)]">
          <UploadCloud className="size-9" strokeWidth={1.6} />
        </div>
        <h2 className="mb-3 font-display text-3xl font-bold uppercase tracking-tight text-ink">
          Add footage to get started
        </h2>
        <p className="mx-auto mb-6 max-w-xl text-[0.9375rem] leading-relaxed text-muted">
          {project.scoreboard_match_id ? (
            <>
              This match was set up from scoreboard.urdr.dev --{" "}
              {stageViews.length || "no"} stages registered. Drop a folder of
              videos to begin auditing.
            </>
          ) : (
            <>
              This match is freshly created with {stageViews.length} stages.
              Drop a folder of videos to begin auditing, comparing, and
              exporting.
            </>
          )}
        </p>
        <div className="inline-flex gap-2.5">
          <Button
            onClick={() => navigate(ingestHref)}
            className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink"
          >
            <ArrowDownToLine className="size-3.5" />
            <span className="font-display uppercase tracking-[0.1em]">
              Add shooter footage
            </span>
          </Button>
        </div>
      </section>

      <SectionHead
        title="Shooters"
        count={
          <>
            <b className="font-bold text-ink-2">
              {pad2(shooters.length || 1)}
            </b>{" "}
            added
          </>
        }
        action={
          <button
            type="button"
            onClick={() => navigate(href("shooters"))}
            className="link-led-fill inline-flex items-center gap-1.5"
          >
            Manage shooters
            <ArrowRight className="size-3.5" />
          </button>
        }
      />
      <div className="mb-8 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {shooters.length > 0 ? (
          // No `you` ring yet -- see #350.
          shooters.map((s) => (
            <ShooterCard
              key={s.slug}
              name={s.name}
              stats={
                s.video_count > 0
                  ? `${s.video_count} ${s.video_count === 1 ? "video" : "videos"}`
                  : "No footage yet"
              }
              addLink={s.video_count === 0 ? "Add footage" : undefined}
              onAddLink={() => navigate(href("ingest", s.slug))}
            />
          ))
        ) : (
          // Legacy single-shooter fallback.
          <ShooterCard
            name={project.competitor_name ?? "Shooter"}
            stats="No footage yet"
            addLink="Add footage"
            onAddLink={() => navigate(ingestHref)}
          />
        )}
        <div
          className="flex items-center gap-3.5 rounded-xl border border-dashed border-rule-strong bg-transparent px-4 py-4 text-led"
          role="button"
          aria-label="Add a squadmate"
          onClick={() => navigate(href("shooters"))}
        >
          <span className="inline-flex size-11 items-center justify-center rounded-full border border-dashed border-rule-strong bg-surface-3 text-led">
            <Plus className="size-5" />
          </span>
          <span className="font-display text-[0.8125rem] font-semibold uppercase tracking-[0.1em] text-led">
            Add a squadmate
          </span>
        </div>
      </div>

      <SectionHead
        title="Stages"
        count={<>{stageViews.length} {project.scoreboard_match_id ? "from scoreboard" : "in the editor"}</>}
        action={
          <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            Tiles wake up once footage is attached
          </span>
        }
      />
      <div className="mb-8 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {stageViews.map((v) => (
          <EmptyStageTile key={v.stage.stage_number} view={v} />
        ))}
      </div>

      <SectionHead title="Get going" />
      <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2">
        <HelpCard
          icon={<UploadCloud className="size-4" />}
          title="Drop your SD card"
          desc="Drag a folder of head-cam videos. Splitsmith auto-matches each video to a stage by recording time."
          cta="Start ingest"
          onClick={() => navigate(ingestHref)}
        />
        <HelpCard
          icon={<Users className="size-4" />}
          title="Bring squadmates"
          desc="Add up to 4 shooters' footage for multi-shooter compare grids and side-by-side exports."
          cta="Add shooter"
          onClick={() => navigate(href("shooters"))}
        />
      </div>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Subcomponents                                                              */
/* -------------------------------------------------------------------------- */

function HeroStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "led" | "live";
}) {
  return (
    <div className="flex flex-col gap-1 border-r border-rule px-5 py-2.5 last:border-r-0">
      <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
        {label}
      </span>
      <span
        className={cn(
          "font-mono text-xl font-bold leading-none tabular-nums",
          tone === "led" && "text-led drop-shadow-[0_0_12px_var(--color-led-glow)]",
          tone === "live" && "text-live drop-shadow-[0_0_12px_var(--color-live-glow)]",
          !tone && "text-ink",
        )}
      >
        {value}
      </span>
    </div>
  );
}

function SectionHead({
  title,
  count,
  action,
}: {
  title: string;
  count?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="mb-3.5 mt-6 flex items-baseline justify-between gap-4">
      <div className="inline-flex items-baseline gap-3.5 font-display text-[0.9375rem] font-bold uppercase tracking-[0.1em] text-ink">
        {title}
        {count && (
          <span className="font-mono text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-subtle">
            {count}
          </span>
        )}
      </div>
      {action}
    </div>
  );
}

function ShooterCard({
  you = false,
  name,
  stats,
  progress,
  addLink,
  onAddLink,
}: {
  you?: boolean;
  name: string;
  stats: ReactNode;
  progress?: number;
  addLink?: string;
  onAddLink?: () => void;
}) {
  return (
    <div
      className={cn(
        "relative flex items-center gap-3 overflow-hidden rounded-xl border bg-surface p-3.5",
        you
          ? "border-led-deep shadow-[inset_0_0_0_1px_var(--color-led-deep)]"
          : "border-rule hover:border-rule-strong",
      )}
    >
      {you && (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_0%_0%,rgba(255,45,45,0.05),transparent_60%)]"
        />
      )}
      <Avatar
        size="lg"
        tone={you ? "you" : undefined}
        initials={initials(name)}
        name={name}
      />
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-1.5 font-display text-sm font-bold uppercase leading-tight tracking-tight text-ink">
          <span className="truncate">{name}</span>
          {you && (
            <span className="rounded border border-led-deep bg-led/10 px-1.5 py-px font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-led">
              You
            </span>
          )}
        </div>
        <div className="mb-2 font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted">
          {stats}
        </div>
        {typeof progress === "number" ? (
          <div className="h-1 overflow-hidden rounded-full bg-surface-3">
            <span
              className={cn(
                "block h-full rounded-full transition-all duration-500",
                progress >= 1
                  ? "bg-done shadow-[0_0_6px_var(--color-done-glow)]"
                  : progress > 0
                    ? "bg-live shadow-[0_0_6px_var(--color-live-glow)]"
                    : "bg-led shadow-[0_0_6px_var(--color-led-glow)]",
              )}
              style={{ width: `${Math.round(progress * 100)}%` }}
            />
          </div>
        ) : addLink ? (
          <button
            type="button"
            onClick={onAddLink}
            className="inline-flex items-center gap-1 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-led hover:text-led-soft"
          >
            + {addLink}
            <ArrowRight className="size-3" />
          </button>
        ) : null}
      </div>
    </div>
  );
}

function AddShooterCard({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center justify-center gap-2.5 rounded-xl border border-dashed border-rule-strong bg-transparent p-4 font-display text-[0.8125rem] font-semibold uppercase tracking-[0.1em] text-muted transition-all hover:border-led-deep hover:bg-led/10 hover:text-led"
    >
      <span className="inline-flex size-6 items-center justify-center rounded-full border-[1.5px] border-dashed border-current">
        <Plus className="size-3" />
      </span>
      Add Shooter
    </button>
  );
}

function EmptyStageTile({ view }: { view: StageView }) {
  return (
    <div className="rounded-xl border border-rule-strong bg-surface p-4 opacity-85">
      <div className="mb-3.5 flex items-start justify-between gap-2.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="inline-flex size-7 items-center justify-center rounded-md border border-rule-strong bg-surface-3 font-mono text-xs font-bold tabular-nums text-ink-2">
            {pad2(view.stage.stage_number)}
          </span>
          <span className="truncate font-display text-[0.8125rem] font-bold uppercase tracking-tight text-ink">
            {view.stage.stage_name}
          </span>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1.5 rounded border border-dashed border-rule-strong bg-surface-3 px-2 py-0.5 font-mono text-[0.5rem] font-bold uppercase tracking-[0.12em] text-subtle">
          Awaiting
        </span>
      </div>
      <div className="font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
        {view.expectedShots ? (
          <>
            <b className="text-base font-bold text-ink">
              {pad2(view.expectedShots)}
            </b>{" "}
            shots expected
          </>
        ) : (
          <span className="text-subtle">ready to record</span>
        )}
      </div>
    </div>
  );
}

function AggregateStageTile({
  row,
  hrefFor,
}: {
  row: StageMatrixRow;
  hrefFor: (slug: string, stage: number) => string;
}) {
  // Per-shooter chip color by status tone. Mirrors the tile tones used
  // elsewhere; kept local so the chip palette is obvious at a glance.
  const chipTone: Record<string, string> = {
    done: "border-led-deep bg-led/15 text-led",
    in_progress: "border-live/50 bg-live/10 text-live",
    ready: "border-rule-strong bg-surface-3 text-ink-2",
    // Distinct from "ready": the pipeline is stalled on a missing stage
    // time, not waiting its turn. The chip title carries the words
    // ("Stage time missing"); the tone is the redundant cue.
    partial: "border-beep/40 bg-beep-tint text-beep",
    todo: "border-rule bg-surface-2 text-whisper",
    skipped: "border-rule bg-surface-2 text-muted",
  };
  // Whole-tile border accent by the rolled-up stage tone, so the grid is
  // scannable at a glance. rollupTone() only yields done/in_progress/todo;
  // the rest fall back to the neutral border. Colour is a redundant cue --
  // the chips and the "K of N audited" line carry the state in text too.
  const shellBorder: Record<string, string> = {
    done: "border-led-deep",
    in_progress: "border-live/40",
    todo: "border-rule-strong",
    ready: "border-rule-strong",
    partial: "border-rule-strong",
    skipped: "border-rule-strong",
  };
  return (
    <div
      className={`rounded-xl border bg-surface-2 p-4 ${shellBorder[row.rollupTone] ?? "border-rule-strong"}`}
    >
      <div className="mb-3 flex items-center gap-2.5">
        <span className="font-mono text-xs font-bold text-muted">
          {pad2(row.stageNumber)}
        </span>
        <span className="truncate font-display text-sm font-semibold uppercase tracking-[0.04em] text-ink">
          {row.stageName}
        </span>
      </div>
      <div className="mb-2.5 flex flex-wrap gap-1.5">
        {row.cells.map((cell) => (
          <Link
            key={cell.shooter.slug}
            to={hrefFor(cell.shooter.slug, row.stageNumber)}
            title={`${cell.shooter.name} -- ${statusLabel(cell.status)}`}
            className={`inline-flex size-7 items-center justify-center rounded-full border font-mono text-[0.625rem] font-bold uppercase transition-transform hover:-translate-y-0.5 ${chipTone[cell.tone] ?? chipTone.todo}`}
          >
            {initials(cell.shooter.name)}
          </Link>
        ))}
      </div>
      <div className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted">
        {row.auditedCount} of {row.cells.length} audited
      </div>
    </div>
  );
}

/** Map a backend ``StageStatus`` to the local ``StagePillTone``. The
 *  tone IS the visual switch this file uses; the status is the truth.
 *  audited -> done because "done" is the design system tone for green-
 *  done, not because audited == complete in any other sense. Skipped
 *  has its own tone so the visual reads "operator decided to skip" not
 *  "operator completed". */
function toneForStatus(status: StageStatus): StagePillTone {
  switch (status) {
    case "audited":
      return "done";
    case "skipped":
      return "skipped";
    case "in_progress":
      return "in_progress";
    case "ready":
      return "ready";
    case "partial":
      return "partial";
    case "todo":
      return "todo";
  }
}

function HelpCard({
  icon,
  title,
  desc,
  cta,
  onClick,
}: {
  icon: ReactNode;
  title: string;
  desc: string;
  cta: string;
  onClick?: () => void;
}) {
  return (
    <div className="rounded-xl border border-rule-strong bg-surface p-4">
      <div className="mb-3 inline-flex size-9 items-center justify-center rounded-md border border-led-deep bg-surface-3 text-led shadow-[0_0_12px_var(--color-led-glow)]">
        {icon}
      </div>
      <div className="mb-1.5 font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
        {title}
      </div>
      <p className="mb-2.5 text-[0.8125rem] leading-relaxed text-muted">{desc}</p>
      <button
        type="button"
        onClick={onClick}
        className="inline-flex items-center gap-1 font-display text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-led hover:text-led-soft"
      >
        {cta} &rarr;
      </button>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 0 || !parts[0]) return "??";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function formatDate(iso: string): string {
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

function expectedShotsFromStage(s: StageEntry): number | null {
  // The legacy StageEntry does not carry expected_rounds on the SPA shape
  // today; derive nothing here and return null so the empty/active tiles
  // gracefully omit the "N expected" line.
  void s;
  return null;
}
