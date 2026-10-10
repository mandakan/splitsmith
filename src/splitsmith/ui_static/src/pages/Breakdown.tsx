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
 * The inspector (``BreakdownInspector``, #1372) shows the selected region's
 * card, else the active shot's interval class, with the shot list scrolling
 * under it; it folds to a rail that gives the viewer the width. Escape drops
 * a region selection back to the shot view. A splitter (``BandSplitter``,
 * ``useBandSplit``, #1373) trades video height for band height, remembered
 * per browser; with nothing remembered the band keeps its own height.
 *
 * Every rule is the shared one: ``useStageWorkspace`` owns the payloads and
 * the shot PATCH, ``useStageEvents`` the region saves, the lane editor its
 * geometry. Nothing outside this page reads Breakdown work: Overview, the
 * next step, readiness and the export gate never count regions.
 */
import { ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useOutletContext, useParams } from "react-router-dom";

import { BandSplitter } from "@/components/coach/BandSplitter";
import { BreakdownInspector } from "@/components/coach/BreakdownInspector";
import { StageBand } from "@/components/coach/StageBand";
import { StageTransport, StageVideo } from "@/components/coach/StageViewer";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api } from "@/lib/api";
import { isTypingTextTarget } from "@/lib/audit-input";
import { regionCounts } from "@/lib/breakdown";
import { useInspectorFolded } from "@/lib/breakdownPrefs";
import { useMatchHref } from "@/lib/matchHref";
import { useBandSplit } from "@/lib/useBandSplit";
import { useShortViewport } from "@/lib/useShortViewport";
import { deriveStageView, useStageWorkspace } from "@/lib/useStageWorkspace";
import { cn } from "@/lib/utils";

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
  // A saved region moves the nav's count: the shell refetches the project.
  // Optional: outside the match shell (a test) there is no outlet context.
  const shell = useOutletContext<MatchShellOutletContext | undefined>();
  const ws = useStageWorkspace(slug, stage, { onRegionsSaved: shell?.refreshProject });
  const { project, coach, error, regions } = ws;
  const compact = useShortViewport();
  const [inspectorFolded] = useInspectorFolded();
  const split = useBandSplit();
  const inspectorRef = useRef<HTMLElement | null>(null);
  // A new selection (a region, or a shot in place of one) opens the
  // inspector at its top, so the card is never left scrolled out of view.
  useEffect(() => {
    if (inspectorRef.current) inspectorRef.current.scrollTop = 0;
  }, [regions.selectedId, ws.activeShotNumber]);
  // Escape drops a region selection back to the shot view. Decided after the
  // event has reached every listener, by defaultPrevented: a live lane drag
  // (the lane editor), an open menu or sheet (Menu, Sheet) and a dialog
  // (dialogFocus) each claim the press they consume, and win it. Not a DOM
  // query for an open menu: by the time a window listener runs, React has
  // already committed the menu's close. A text field keeps its own Esc.
  const { selectedId, select } = regions;
  useEffect(() => {
    if (selectedId == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || isTypingTextTarget(e.target)) return;
      window.setTimeout(() => {
        if (!e.defaultPrevented) select(null);
      }, 0);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId, select]);

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

  const { prevStage, nextStage } = view;
  const counts = regionCounts(regions.events);
  const regionChips = (
    <>
      <Chip tick="muted">
        {counts.confirmed} {counts.confirmed === 1 ? "region" : "regions"}
      </Chip>
      {counts.proposed > 0 ? <Chip tick="muted">{counts.proposed} proposed</Chip> : null}
    </>
  );
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

  // Short windows (under 900 px tall) go dense rather than shrink the video:
  // the transport moves into the band's header row, the region card is
  // compact, and the lane hints wait behind the band's menu. Under the
  // min-height floor (a window under ~640 px) the shell scrolls instead.
  const transport = <StageTransport ws={ws} view={view} className={compact ? "p-0" : "shrink-0 border-t border-rule"} />;
  return (
    <div
      data-testid="breakdown-workspace"
      data-compact={compact || undefined}
      className="flex h-[calc(100dvh-var(--shell-header-h,86px))] min-h-[520px] flex-col overflow-hidden"
    >
      <div className="shrink-0 border-b border-rule px-4 py-2 md:px-7">
        <PageHeader
          className="mb-0"
          ordinal={pad2(stage)}
          title={coach.stage_name || "Stage"}
          sub={
            compact ? undefined : (
              <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
                {project.competitor_name ? <span>{project.competitor_name}</span> : null}
                {regionChips}
              </span>
            )
          }
          actions={
            <>
              {/* Short window: one header line. The shooter strip above names the shooter. */}
              {compact ? <span className="mr-2 inline-flex items-center gap-2">{regionChips}</span> : null}
              {/* Moved from Coach (#1374): re-running the classifier is interval work. */}
              <Button
                type="button"
                onClick={() => void ws.reclassify()}
                disabled={ws.reclassifying}
                title="Re-run the auto-classifier; manual overrides survive"
              >
                {ws.reclassifying ? "Reclassifying…" : "Reclassify"}
              </Button>
              <Button asChild>
                <Link to={`${coachPrefix}/${stage}`}>Review in Coach</Link>
              </Button>
              {stepButton("Previous stage", prevStage, <ArrowLeft className="size-4" />)}
              {stepButton("Next stage", nextStage, <ArrowRight className="size-4" />)}
            </>
          }
        />
      </div>

      <div ref={split.roomRef} data-testid="breakdown-room" className="flex min-h-0 flex-1 flex-col">
        <div
          className={cn(
            "grid min-h-0 flex-1",
            inspectorFolded ? "grid-cols-[minmax(0,1fr)_40px]" : "grid-cols-[minmax(0,1fr)_360px]",
          )}
        >
          <div ref={split.viewerRef} className="flex min-h-0 flex-col border-r border-rule">
            <StageVideo ws={ws} view={view} className="min-h-0 w-full flex-1 object-contain" />
            {compact ? null : transport}
          </div>
          <BreakdownInspector ws={ws} view={view} compact={compact} scrollRef={inspectorRef} />
        </div>

        {split.limits ? (
          <BandSplitter
            band={split.current}
            limits={split.limits}
            room={split.room}
            onDrag={split.drag}
            onCommit={split.commit}
            onCancel={split.cancel}
            onToggleLarge={split.toggleLarge}
          />
        ) : (
          <div className="h-1.5 shrink-0 border-y border-rule bg-surface-2" />
        )}
        {/* Sized by the split when one is set; a band under its natural
            height gives its rows less (they scroll under the fixed header
            and ruler), a taller one grows the Audio row. */}
        <div
          ref={split.bandRef}
          data-testid="breakdown-band"
          className={cn("shrink-0", split.band != null && "overflow-hidden")}
          style={split.band != null ? { height: split.band } : undefined}
        >
          <div ref={split.contentRef}>
            <StageBand
              ws={ws}
              view={view}
              compact={compact}
              toolbar={compact ? transport : undefined}
              audioHeight={split.audioHeight}
              rowsHeight={split.rowsHeight ?? undefined}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}
