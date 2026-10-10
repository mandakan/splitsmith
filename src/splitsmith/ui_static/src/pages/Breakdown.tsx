/**
 * Breakdown (#1371, epic #1370): the optional editor for a stage's regions
 * (movement, reload, activation) and its interval classes, in a fixed
 * workspace that never scrolls on a desktop.
 *
 * - ``/breakdown/:slug/:stage`` -- the workspace.
 * - ``/breakdown/:slug`` -- the first stage with a time, else the first stage.
 * - ``/breakdown`` -- the default shooter (``DefaultShooterRedirect``).
 *
 * Rows: the header, the workspace (viewer, then the 360 px inspector), the
 * band (``StageBand``: ruler, Audio, Shots and the region lanes, the hints).
 * The inspector shows the selected region's card, else the active shot's
 * interval class, with the shot list scrolling under it.
 *
 * Every rule is the shared one: ``useStageWorkspace`` owns the payloads and
 * the shot PATCH, ``useStageEvents`` the region saves, the lane editor its
 * geometry. Nothing outside this page reads Breakdown work: Overview, the
 * next step, readiness and the export gate never count regions.
 */
import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";

import { CoachShotTable } from "@/components/coach/CoachShotTable";
import { EventList } from "@/components/coach/EventList";
import { SaveNotice } from "@/components/coach/SaveNotice";
import { SelectedRegionCard } from "@/components/coach/SelectedRegionCard";
import { ShotIntervalCard } from "@/components/coach/ShotIntervalCard";
import { StageBand } from "@/components/coach/StageBand";
import { StageTransport, StageVideo } from "@/components/coach/StageViewer";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api } from "@/lib/api";
import { regionCounts } from "@/lib/breakdown";
import { useMatchHref } from "@/lib/matchHref";
import { gapTier } from "@/lib/splits";
import { deriveStageView, useStageWorkspace } from "@/lib/useStageWorkspace";

export function Breakdown() {
  const { slug, stage } = useParams<{ slug?: string; stage?: string }>();
  const href = useMatchHref();
  if (!slug) return <Navigate to={href("breakdown")} replace />;
  if (stage == null) return <FirstStageRedirect slug={slug} />;
  const n = Number(stage);
  if (!Number.isFinite(n)) {
    return <div className="px-7 py-8 text-sm text-muted">Bad stage.</div>;
  }
  return <BreakdownStage key={`${slug}-${n}`} slug={slug} stage={n} />;
}

/** ``/breakdown/:slug``: the first stage with a stage time (the ones with
 *  shots to break down), else the first stage. */
function FirstStageRedirect({ slug }: { slug: string }) {
  const href = useMatchHref();
  const [target, setTarget] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .getProject(slug)
      .then((p) => {
        if (!alive) return;
        const ordered = [...p.stages].sort((a, b) => a.stage_number - b.stage_number);
        const timed = ordered.find((s) => !s.skipped && s.time_seconds > 0);
        const first = timed ?? ordered[0];
        if (first) setTarget(first.stage_number);
        else setError("This shooter has no stages yet.");
      })
      .catch((e) => {
        if (alive) setError(e instanceof ApiError ? e.detail : String(e));
      });
    return () => {
      alive = false;
    };
  }, [slug]);
  if (error) {
    return (
      <div className="px-4 py-4 md:px-7 md:py-5">
        <PageHeader title="Breakdown" />
        <p role="alert" className="text-sm text-muted">
          {error}
        </p>
      </div>
    );
  }
  if (target == null) return null;
  return <Navigate to={href("breakdown", slug, String(target))} replace />;
}

function BreakdownStage({ slug, stage }: { slug: string; stage: number }) {
  const href = useMatchHref();
  const prefix = href("breakdown", slug);
  const coachPrefix = href("coach", slug);
  const ws = useStageWorkspace(slug, stage);
  const { project, coach, baselines, error, regions } = ws;

  if (error) {
    return (
      <div className="px-4 py-4 md:px-7 md:py-5">
        <PageHeader ordinal={pad2(stage)} title="Breakdown" />
        <p role="alert" className="text-sm text-led-text">
          {error}
        </p>
      </div>
    );
  }
  const view = deriveStageView(ws, slug, stage);
  if (!coach || !project || !view) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-md text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading stage...
      </div>
    );
  }

  const { activeShot, selectedEvent, eventsReadOnly, prevStage, nextStage } = view;
  const counts = regionCounts(regions.events);
  const stepButton = (label: string, to: number | null, icon: React.ReactNode) =>
    to != null ? (
      <Button asChild size="icon" aria-label={label}>
        <Link to={`${prefix}/${to}`}>{icon}</Link>
      </Button>
    ) : (
      <Button type="button" size="icon" disabled aria-label={label}>
        {icon}
      </Button>
    );

  return (
    <div
      data-testid="breakdown-workspace"
      className="flex h-[calc(100dvh-var(--shell-header-h,86px))] min-h-0 flex-col overflow-hidden"
    >
      <div className="shrink-0 border-b border-rule px-4 py-2 md:px-7">
        <PageHeader
          className="mb-0"
          ordinal={pad2(stage)}
          title={coach.stage_name || "Stage"}
          sub={
            <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
              {project.competitor_name ? <span>{project.competitor_name}</span> : null}
              <Chip tick="muted">
                {counts.confirmed} {counts.confirmed === 1 ? "region" : "regions"}
              </Chip>
              {counts.proposed > 0 ? <Chip tick="muted">{counts.proposed} proposed</Chip> : null}
            </span>
          }
          actions={
            <>
              <Button asChild>
                <Link to={`${coachPrefix}/${stage}`}>Review in Coach</Link>
              </Button>
              {stepButton("Previous stage", prevStage, <ArrowLeft className="size-4" />)}
              {stepButton("Next stage", nextStage, <ArrowRight className="size-4" />)}
            </>
          }
        />
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-h-0 flex-col border-r border-rule">
          <StageVideo ws={ws} view={view} className="min-h-0 w-full flex-1 object-contain" />
          <StageTransport ws={ws} view={view} className="shrink-0 border-t border-rule" />
        </div>
        {/* The column scrolls only when the card and a usable shot list do not
            both fit (a short window with a region selected); the page never does. */}
        <aside aria-label="Inspector" className="flex min-h-0 flex-col gap-2 overflow-y-auto px-3 py-2">
          {regions.issue ? (
            <SaveNotice issue={regions.issue} busy={regions.busy} onRetry={regions.retry} onDismiss={regions.dismiss} />
          ) : null}
          <div className="shrink-0">
            {selectedEvent ? (
              <SelectedRegionCard event={selectedEvent} regions={regions} />
            ) : activeShot ? (
              <ShotIntervalCard
                shot={activeShot}
                tier={gapTier(activeShot.split, activeShot.interval_class, baselines)}
                onClassify={(cls) =>
                  void ws.patchShot(activeShot, { interval_class: cls, interval_class_source: "manual" })
                }
              />
            ) : null}
          </div>
          {eventsReadOnly ? (
            <div className="max-h-[40%] shrink-0 overflow-y-auto">
              <EventList events={regions.events} shots={coach.shots} />
            </div>
          ) : null}
          <CoachShotTable
            fill
            className="min-h-40 flex-1"
            shots={coach.shots}
            activeShotNumber={ws.activeShotNumber}
            baselines={baselines}
            onSelect={ws.seekToShot}
          />
        </aside>
      </div>

      <div className="shrink-0 border-t border-rule">
        <StageBand ws={ws} view={view} />
      </div>
    </div>
  );
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}
