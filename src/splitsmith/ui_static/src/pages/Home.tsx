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
  Layers,
  Plus,
  Settings as SettingsIcon,
  UploadCloud,
  Users,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";

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
  isTerminal,
} from "@/lib/stageStatus";
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
  const ctx = useOutletContext<MatchShellOutletContext>();
  const project = ctx?.project ?? null;
  // Slug used for slug-bearing nav links from this page. Lifted off the
  // health snapshot the shell already loaded, so Home doesn't need its
  // own fetch. Falls back to "/shooters" routing when no default exists.
  const navSlug = ctx?.health?.default_shooter_slug ?? null;
  const [shooters, setShooters] = useState<ShooterListEntry[]>([]);

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
  }, [project?.name]);

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

  // Per-tone counts for the headline stats + progress bar. Initialised
  // with every key so consumers can index without optional chaining.
  const totalsByTone = useMemo<Record<StagePillTone, number>>(() => {
    const counts: Record<StagePillTone, number> = {
      done: 0,
      in_progress: 0,
      ready: 0,
      partial: 0,
      flagged: 0,
      todo: 0,
      skipped: 0,
    };
    for (const v of stageViews) counts[v.tone] += 1;
    return counts;
  }, [stageViews]);

  const terminalCount = useMemo(
    () => stageViews.filter((v) => isTerminal(v.status)).length,
    [stageViews],
  );

  // Audited percentage = terminal stages (audited + skipped) / total.
  // Skipped stages count as closed-out work because the operator made
  // a deliberate decision to skip them. Stages still in `in_progress`
  // / `ready` / `partial` / `todo` are pending.
  const auditedPct =
    stageViews.length > 0
      ? Math.round((terminalCount / stageViews.length) * 100)
      : 0;

  const nextUp = stageViews.find((v) => v.isNextUp);
  const isEmpty =
    !project ||
    stageViews.length === 0 ||
    stageViews.every((v) => v.status === "todo");

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
          {project.competitor_name && <span>{project.competitor_name}</span>}
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
              navigate(navSlug ? `/export/${navSlug}` : "/shooters")
            }
          >
            <ArrowDownToLine className="size-3.5" />
            <span className="font-display uppercase tracking-[0.08em]">
              Export Match
            </span>
          </Button>
          <Button variant="ghost">
            <SettingsIcon className="size-3.5" />
            <span className="font-display uppercase tracking-[0.08em]">
              Match Settings
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
            stageViews={stageViews}
            shooters={shooters}
            nextUp={nextUp ?? null}
            totalsByTone={totalsByTone}
            auditedPct={auditedPct}
            navSlug={navSlug}
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
  stageViews,
  shooters,
  nextUp,
  totalsByTone,
  auditedPct,
  navSlug,
}: {
  project: MatchProject;
  stageViews: StageView[];
  shooters: ShooterListEntry[];
  nextUp: StageView | null;
  totalsByTone: Record<StagePillTone, number>;
  auditedPct: number;
  navSlug: string | null;
}) {
  const navigate = useNavigate();
  const stageHref = (n: number) =>
    navSlug ? `/audit/${navSlug}/${n}` : "/shooters";
  return (
    <>
      {/* Resume hero */}
      <section
        className="relative mb-6 grid grid-cols-1 items-center gap-6 overflow-hidden rounded-2xl border border-rule-strong p-7 shadow-[inset_0_1px_0_rgba(255,255,255,0.04),0_24px_48px_-24px_rgba(0,0,0,0.6)] lg:grid-cols-[1fr_auto]"
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
              className="inline-block size-1.5 animate-pulse rounded-full bg-led shadow-[0_0_8px_var(--color-led-glow)]"
            />
            {nextUp ? "Pick up where you left off" : "All stages audited"}
          </div>
          <h2 className="mb-3 font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
            {nextUp ? (
              <>
                Stage <span className="text-led">{pad2(nextUp.stage.stage_number)}</span>{" "}
                <span className="text-ink">&middot;</span>{" "}
                {nextUp.stage.stage_name}
              </>
            ) : (
              <>Match complete</>
            )}
          </h2>
          <p className="mb-5 max-w-xl text-sm text-ink-2">
            {nextUp ? (
              <>
                <b className="font-bold text-ink">{totalsByTone.done}</b> of{" "}
                <b className="font-bold text-ink">{stageViews.length}</b>{" "}
                stages audited. Resume the next stage to keep moving, or jump
                to any tile below.
              </>
            ) : (
              <>
                All stages closed out. Run an export from match settings, or
                revisit a stage for a recheck.
              </>
            )}
          </p>
          <div className="inline-flex overflow-hidden rounded-[10px] border border-rule bg-surface-3">
            <HeroStat label="Match audited" value={`${auditedPct}%`} tone="led" />
            <HeroStat
              label="Stages flagged"
              value={pad2(totalsByTone.flagged)}
              tone={totalsByTone.flagged > 0 ? "led" : undefined}
            />
            <HeroStat
              label="In progress"
              value={pad2(totalsByTone.partial)}
              tone={totalsByTone.partial > 0 ? "live" : undefined}
            />
          </div>
        </div>
        {nextUp && (
          <div className="relative z-10">
            <button
              type="button"
              onClick={() => navigate(stageHref(nextUp.stage.stage_number))}
              className="inline-flex min-h-[60px] items-center gap-3.5 rounded-[11px] border border-led-deep bg-led-fill px-6 py-4 font-display text-base font-bold uppercase tracking-[0.06em] text-ink shadow-[0_0_0_1px_var(--color-led),0_0_32px_var(--color-led-glow),inset_0_1px_0_rgba(255,255,255,0.2)] transition-all hover:bg-led hover:-translate-y-0.5"
            >
              <div className="flex flex-col items-start gap-1">
                <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] opacity-70">
                  Continue auditing
                </span>
                <span className="text-[1.0625rem] font-bold">
                  Resume Stage {pad2(nextUp.stage.stage_number)}
                </span>
              </div>
              <ArrowRight className="size-5" />
            </button>
          </div>
        )}
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
            onClick={() => navigate("/shooters")}
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
              stats={`${s.stages_total} stages`}
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
            stats={`${stageViews.length} stages`}
            progress={
              stageViews.length > 0 ? totalsByTone.done / stageViews.length : 0
            }
          />
        )}
        <AddShooterCard />
      </div>

      <SectionHead
        title="Stages"
        count={
          <>
            <b className="font-bold text-ink-2">{pad2(totalsByTone.done)}</b>{" "}
            audited <span className="text-whisper">&middot;</span>{" "}
            <b className="font-bold text-ink-2">
              {pad2(totalsByTone.partial + totalsByTone.flagged)}
            </b>{" "}
            in progress <span className="text-whisper">&middot;</span>{" "}
            <b className="font-bold text-ink-2">{pad2(totalsByTone.todo)}</b>{" "}
            pending
          </>
        }
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-[repeat(auto-fill,minmax(240px,1fr))]">
        {stageViews.map((v) => (
          <StageTile
            key={v.stage.stage_number}
            view={v}
            onClick={() => navigate(stageHref(v.stage.stage_number))}
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
  const ingestHref = navSlug ? `/ingest/${navSlug}` : "/shooters";
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
          <Button variant="outline">
            <Layers className="size-3.5" />
            <span className="font-display uppercase tracking-[0.1em]">
              Edit stage list
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
            onClick={() => navigate("/shooters")}
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
              onAddLink={() => navigate(ingestHref)}
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
          onClick={() => navigate("/shooters")}
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
      <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-3">
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
        />
        <HelpCard
          icon={<Layers className="size-4" />}
          title="Adjust the stage list"
          desc="Reality differs from scoreboard? Add, remove, or rename stages without losing audit progress."
          cta="Edit stages"
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

function AddShooterCard() {
  return (
    <button
      type="button"
      className="flex items-center justify-center gap-2.5 rounded-xl border border-dashed border-rule-strong bg-transparent p-4 font-display text-[0.8125rem] font-semibold uppercase tracking-[0.1em] text-muted transition-all hover:border-led-deep hover:bg-led/10 hover:text-led"
    >
      <span className="inline-flex size-6 items-center justify-center rounded-full border-[1.5px] border-dashed border-current">
        <Plus className="size-3" />
      </span>
      Add Shooter
    </button>
  );
}

function StageTile({
  view,
  onClick,
}: {
  view: StageView;
  onClick: () => void;
}) {
  const { stage, tone, isNextUp } = view;
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group relative flex min-h-[110px] flex-col justify-between overflow-hidden rounded-xl border bg-surface p-4 text-left transition-all hover:-translate-y-0.5 hover:bg-surface-2",
        isNextUp
          ? "border-led shadow-[0_0_0_1px_var(--color-led),0_0_28px_var(--color-led-glow)]"
          : "border-rule hover:border-rule-strong",
        tone === "flagged" && !isNextUp && "border-led/30",
      )}
      style={
        isNextUp
          ? {
              backgroundImage:
                "radial-gradient(circle at 0% 0%, var(--color-led-tint), transparent 65%), var(--color-surface)",
            }
          : undefined
      }
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 left-0 w-[2px] transition-all",
          isNextUp
            ? "bg-led shadow-[0_0_8px_var(--color-led-glow)]"
            : "bg-transparent group-hover:bg-led group-hover:shadow-[0_0_8px_var(--color-led-glow)]",
        )}
      />
      <div className="flex items-center justify-between">
        <div className="inline-flex items-center gap-2.5">
          <span
            className={cn(
              "inline-flex size-7 items-center justify-center rounded-md font-mono text-xs font-bold tabular-nums",
              isNextUp
                ? "bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_8px_var(--color-led-glow)]"
                : "bg-surface-3 text-ink-2",
            )}
          >
            {pad2(stage.stage_number)}
          </span>
          <span className="font-display text-base font-bold uppercase tracking-tight text-ink">
            {stage.stage_name}
          </span>
        </div>
        {isNextUp ? (
          <span className="inline-flex items-center gap-1.5 rounded border border-led-deep bg-led/10 px-2 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-led">
            <span
              aria-hidden
              className="inline-block size-1 animate-pulse rounded-full bg-led shadow-[0_0_5px_var(--color-led-glow)]"
            />
            Next up
          </span>
        ) : (
          <StagePill tone={tone} />
        )}
      </div>
      <div className="flex items-center justify-between font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
        <span>
          {view.expectedShots ? (
            <>
              <b className="font-bold text-ink">{pad2(view.expectedShots)}</b>{" "}
              expected
            </>
          ) : stage.time_seconds > 0 ? (
            <>
              <b className="font-bold text-ink">{stage.time_seconds.toFixed(2)}s</b>{" "}
              stage time
            </>
          ) : (
            <span className="text-subtle">No video yet</span>
          )}
        </span>
        <span
          className={cn(
            tone === "flagged" ? "text-led" : "text-subtle",
            tone === "in_progress" && "text-live",
            tone === "ready" && "text-led",
          )}
        >
          {tone === "todo" && "awaiting"}
          {tone === "partial" && "stage time missing"}
          {tone === "ready" && "ready"}
          {tone === "in_progress" && "in progress"}
          {tone === "done" && "audited"}
          {tone === "skipped" && "skipped"}
          {tone === "flagged" && "flagged"}
        </span>
      </div>
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

function StagePill({ tone }: { tone: StagePillTone }) {
  const label =
    tone === "done"
      ? "Audited"
      : tone === "skipped"
        ? "Skipped"
        : tone === "in_progress"
          ? "In progress"
          : tone === "ready"
            ? "Ready"
            : tone === "partial"
              ? "Stage time missing"
              : tone === "flagged"
                ? "Flagged"
                : "Not started";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded px-2 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em]",
        tone === "done" && "bg-done/10 text-done",
        tone === "skipped" && "border border-rule bg-surface-3 text-subtle",
        tone === "in_progress" && "bg-live/10 text-live",
        tone === "ready" && "border border-led/40 bg-led-tint text-led",
        tone === "partial" && "border border-dashed border-live bg-live/10 text-live",
        tone === "flagged" && "bg-led/10 text-led",
        tone === "todo" && "border border-rule bg-surface-2 text-subtle",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block size-1 rounded-full",
          tone === "done" && "bg-done shadow-[0_0_4px_var(--color-done-glow)]",
          tone === "skipped" && "bg-subtle",
          tone === "in_progress" && "bg-live shadow-[0_0_4px_var(--color-live-glow)]",
          tone === "ready" && "bg-led shadow-[0_0_4px_var(--color-led-glow)]",
          tone === "partial" && "bg-live shadow-[0_0_4px_var(--color-live-glow)]",
          tone === "flagged" && "bg-led shadow-[0_0_4px_var(--color-led-glow)]",
          tone === "todo" && "border border-subtle bg-transparent",
        )}
      />
      {label}
    </span>
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
