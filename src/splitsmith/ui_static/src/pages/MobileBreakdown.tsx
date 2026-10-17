/**
 * Breakdown on a phone (spec 2026-10-17, wireframes lumet.dev/mobile-breakdown):
 * review the regions the seeder proposed, one at a time. The open region
 * sits in a focus strip (its time plus 2 s each side); Keep, Delete and
 * Kind act on it, and a handle opens edge mode, where a sideways drag on
 * the video or the pad moves that edge relative to the finger and the
 * picture follows it. Edge mode saves on Done only; Cancel and an
 * interrupted gesture restore the region.
 *
 * Every rule is shared with the desktop: the queue, the window and the
 * drag are pure (``lib/proposalQueue``, ``lib/focusWindow``,
 * ``lib/edgeDrag``) over ``lib/events`` and ``laneDrag``; saves are
 * ``useStageEvents``'; the player is the workspace's. A match without the
 * edit capability (a desktop mirror on hosted) reads only.
 */
import { ArrowLeft, ArrowRight, Loader2, MoreHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useLocation, useOutletContext, useParams } from "react-router-dom";

import { EdgePad } from "@/components/breakdown/mobile/EdgePad";
import { FocusStrip } from "@/components/breakdown/mobile/FocusStrip";
import { PhoneVideo } from "@/components/breakdown/mobile/PhoneVideo";
import { LANE_LABEL, LANES, sameEvents } from "@/components/coach/laneDrag";
import { SaveNotice } from "@/components/coach/SaveNotice";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import { PageHeader } from "@/components/ui/PageHeader";
import { Segmented } from "@/components/ui/Segmented";
import { Sheet } from "@/components/ui/Sheet";
import type { StageEvent, StageEventKind } from "@/lib/api";
import { clipFps } from "@/lib/clipFps";
import { beginEdge, edgeFrame, restoreRegion, snappedTo, stepEdge, type EdgeGesture, type EdgeTarget } from "@/lib/edgeDrag";
import { keepAllEvents, keepEvent, withKind } from "@/lib/events";
import { focusWindow, playWindow } from "@/lib/focusWindow";
import { useMatchHref } from "@/lib/matchHref";
import { nextOpenId, prevOpenId, proposals, queuePosition } from "@/lib/proposalQueue";
import { useSnapToShots } from "@/lib/timelinePrefs";
import { deriveStageView, useStageWorkspace } from "@/lib/useStageWorkspace";
import { cn } from "@/lib/utils";
import { FirstStageRedirect } from "@/pages/Breakdown";

export function MobileBreakdown() {
  const { slug, stage } = useParams<{ slug?: string; stage?: string }>();
  const href = useMatchHref();
  if (!slug) return <Navigate to={href("breakdown")} replace />;
  if (stage == null) return <FirstStageRedirect slug={slug} />;
  const n = Number(stage);
  if (!Number.isFinite(n)) return <div className="px-4 py-4 text-sm text-muted">Bad stage.</div>;
  return <MobileBreakdownStage key={`${slug}-${n}`} slug={slug} stage={n} />;
}

interface EdgeEdit {
  g: EdgeGesture;
  /** The edge as it will be saved, and what snapped it, while it moves. */
  pill: { t: number; snapped: string | null } | null;
}

function MobileBreakdownStage({ slug, stage }: { slug: string; stage: number }) {
  const href = useMatchHref();
  const prefix = href("breakdown", slug);
  const shell = useOutletContext<MatchShellOutletContext | undefined>();
  const { search } = useLocation();
  const ws = useStageWorkspace(slug, stage, {
    onRegionsSaved: shell?.refreshProject,
    link: search,
    seed: true,
    peaks: false,
  });
  const { regions, coach, error } = ws;
  const [openId, setOpenId] = useState<string | null>(null);
  const [edit, setEdit] = useState<EdgeEdit | null>(null);
  const [kindOpen, setKindOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [snapOn, setSnapOn] = useSnapToShots();
  const stopAt = useRef<number | null>(null);
  // The region as it stood when the finger went down on a pad: every frame
  // of that gesture is judged from it, and an interrupted gesture puts it back.
  const gestureBase = useRef<StageEvent | null>(null);
  const view = deriveStageView(ws, slug, stage);

  // Open the first proposal once the regions are there, and move on
  // whenever the open one is gone (deleted here, or by another writer).
  const events = regions.events;
  const open = events.find((e) => e.id === openId) ?? null;
  useEffect(() => {
    if (open == null && !edit) setOpenId(nextOpenId(events, -Infinity));
  }, [open, edit, events]);

  // Play region: stop half a second after it.
  const tFromBeep = view?.tFromBeep ?? 0;
  const { videoRef } = ws;
  useEffect(() => {
    if (stopAt.current != null && tFromBeep >= stopAt.current) {
      stopAt.current = null;
      videoRef.current?.pause();
    }
  }, [tFromBeep, videoRef]);

  if (error) {
    return (
      <div className="px-4 py-4">
        <PageHeader ordinal={pad2(stage)} title="Breakdown" />
        <p role="alert" className="text-sm text-led-text">
          {error}
        </p>
      </div>
    );
  }
  if (!coach || !view) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-md text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading stage...
      </div>
    );
  }

  const fps = clipFps(coach.videos);
  const shotTimes = coach.shots.map((s) => s.time_from_beep);
  const readOnly = view.eventsReadOnly;
  const pos = queuePosition(events, openId);
  const queue = proposals(events);

  const play = (from: number, to: number | null) => {
    ws.seekFromBeep(from);
    stopAt.current = to;
    void videoRef.current?.play().catch(() => {});
  };
  const moveOn = (next: StageEvent[], from: StageEvent) => setOpenId(nextOpenId(next, from.start));
  const keep = () => {
    if (!open) return;
    const next = keepEvent(events, open.id);
    regions.change(next, true);
    moveOn(next, open);
  };
  const remove = () => {
    if (!open) return;
    const next = events.filter((e) => e.id !== open.id);
    regions.change(next, true);
    moveOn(next, open);
  };
  const setKind = (kind: StageEventKind) => {
    if (!open) return;
    const next = withKind(events, open.id, kind);
    if (next) regions.change(next, true);
    setKindOpen(false);
  };

  // Edge mode: frames are local, Done commits once, Cancel restores.
  const startEdge = (target: EdgeTarget) => {
    if (!open || readOnly) return;
    const g = beginEdge(events, open.id, target);
    if (!g) return;
    setEdit({ g, pill: null });
    ws.seekFromBeep(target === "end" ? g.before.end : g.before.start);
  };
  const switchTarget = (target: EdgeTarget) => {
    if (!edit) return;
    const g = beginEdge(regions.events, edit.g.id, target);
    if (g) setEdit({ g: { ...g, before: edit.g.before }, pill: null });
  };
  const onPadStart = () => {
    if (!edit) return;
    gestureBase.current = regions.events.find((e) => e.id === edit.g.id) ?? edit.g.before;
  };
  const onPadMove = (dxFrac: number, padPx: number) => {
    if (!edit || !gestureBase.current) return;
    const base = { ...edit.g, before: gestureBase.current };
    const frame = edgeFrame(base, regions.events, dxFrac, { snap: snapOn, shotTimes, duration: view.stageTime, padPx });
    if (!frame) return;
    regions.change(frame.events, false);
    ws.seekFromBeep(frame.seek);
    setEdit({ ...edit, pill: { t: frame.edge, snapped: edit.g.target === "body" ? null : snappedTo(frame.edge, frame.seek, coach.shots) } });
  };
  const restore = () => {
    if (!edit) return;
    regions.change(restoreRegion(regions.events, edit.g), false);
    regions.cancel();
  };
  const done = () => {
    if (!edit) return;
    const startEvents = regions.events.map((e) => (e.id === edit.g.id ? edit.g.before : e));
    if (sameEvents(startEvents, regions.events)) regions.cancel();
    else regions.change(regions.events, true);
    setEdit(null);
  };
  const cancel = () => {
    restore();
    setEdit(null);
  };
  const step = (dir: -1 | 1) => {
    if (!edit || edit.g.target === "body") return;
    const next = stepEdge(regions.events, edit.g.id, edit.g.target, dir, fps);
    if (!next) return;
    regions.change(next, false);
    const me = next.find((e) => e.id === edit.g.id);
    if (!me) return;
    const t = edit.g.target === "start" ? me.start : me.end;
    ws.seekFromBeep(t);
    setEdit({ ...edit, pill: { t, snapped: null } });
  };

  // A gesture on the pad that the OS took back (a call, an edge swipe) puts
  // the region back where this gesture found it; Cancel restores the edit.
  const onPadEnd = () => {
    gestureBase.current = null;
  };
  const onPadCancel = () => {
    const before = gestureBase.current;
    gestureBase.current = null;
    if (!edit || !before) return;
    regions.change(
      regions.events.map((e) => (e.id === before.id ? before : e)),
      false,
    );
    setEdit({ ...edit, pill: null });
  };

  const kindChip = (e: StageEvent) => (
    <Chip tick={e.kind}>{LANE_LABEL[e.kind]}</Chip>
  );
  const header = (
    <PageHeader
      className="mb-0"
      ordinal={pad2(stage)}
      title={coach.stage_name || "Stage"}
      actions={
        <span className="relative inline-flex items-center gap-1">
          {view.prevStage != null ? (
            <Button asChild size="icon" aria-label="Previous stage">
              <Link to={`${prefix}/${view.prevStage}`}>
                <ArrowLeft className="size-4" />
              </Link>
            </Button>
          ) : null}
          {view.nextStage != null ? (
            <Button asChild size="icon" aria-label="Next stage">
              <Link to={`${prefix}/${view.nextStage}`}>
                <ArrowRight className="size-4" />
              </Link>
            </Button>
          ) : null}
          <span className="relative shrink-0">
            <Button
              type="button"
              size="icon"
              variant="ghost"
              aria-label="Breakdown options"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((v) => !v)}
            >
              <MoreHorizontal className="size-4" aria-hidden />
            </Button>
            <Menu open={menuOpen} onClose={() => setMenuOpen(false)} align="right">
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={snapOn}
                className={menuItemClass}
                onClick={() => setSnapOn(!snapOn)}
              >
                Snap to shots
                <span className="ml-auto text-sm text-muted">{snapOn ? "on" : "off"}</span>
              </button>
              {!readOnly && queue.length > 0 ? (
                <button
                  type="button"
                  role="menuitem"
                  className={menuItemClass}
                  onClick={() => {
                    regions.change(keepAllEvents(events), true);
                    setMenuOpen(false);
                  }}
                >
                  Keep all {queue.length}
                </button>
              ) : null}
            </Menu>
          </span>
        </span>
      }
    />
  );

  const pill = edit?.pill ? (
    <div className="pointer-events-none absolute inset-x-2 top-2 flex justify-between gap-2">
      <span className="numeral rounded bg-black/70 px-2 py-0.5 text-md text-white">
        {edit.pill.t.toFixed(2)} s <span className="text-sm opacity-75">f {Math.round(edit.pill.t * fps)}</span>
      </span>
      {edit.pill.snapped ? (
        <span className="rounded bg-black/70 px-2 py-0.5 text-sm text-white">snapped to {edit.pill.snapped}</span>
      ) : null}
    </div>
  ) : null;

  return (
    <div data-testid="mobile-breakdown" className="flex flex-col gap-3 px-4 py-3">
      {header}

      {edit ? (
        <div className="flex items-center gap-2">
          <span className="text-md text-ink">
            {LANE_LABEL[edit.g.before.kind]}, {edit.g.target === "body" ? "move" : edit.g.target}
          </span>
          <span className="flex-1" />
          <Button type="button" className="min-h-11" onClick={cancel}>
            Cancel
          </Button>
          <Button type="button" variant="primary" className="min-h-11" onClick={done}>
            Done
          </Button>
        </div>
      ) : pos ? (
        <div className="flex flex-col gap-1.5">
          <div className="flex gap-1" aria-hidden>
            {queue.map((e, i) => (
              <i
                key={e.id}
                className={cn("h-1.5 flex-1 rounded-full", i === pos.index ? "bg-led" : i < pos.index ? "bg-ink" : "bg-rule")}
              />
            ))}
          </div>
          <span className="text-sm text-muted">
            Proposal {pos.index + 1} of {pos.total}
          </span>
        </div>
      ) : null}

      <PhoneVideo ws={ws} view={view}>
        {edit ? (
          <EdgePad
            className="absolute inset-0"
            label="Drag sideways on the picture to move the edge"
            onStart={onPadStart}
            onMove={onPadMove}
            onEnd={onPadEnd}
            onCancel={onPadCancel}
          >
            {pill}
          </EdgePad>
        ) : null}
      </PhoneVideo>

      {open == null ? (
        <div className="flex flex-col items-start gap-2">
          <p className="text-md text-muted">No proposals on this stage</p>
          <Button type="button" className="min-h-11" onClick={() => play(0, null)}>
            Play stage
          </Button>
        </div>
      ) : (
        <>
          <div data-testid="open-region" className="flex flex-wrap items-center gap-2">
            {kindChip(open)}
            {open.source === "auto" ? <Chip tick="muted">Auto</Chip> : <Chip tick="neutral">Confirmed</Chip>}
            <span className="flex-1" />
            <span className="numeral text-md text-ink-2">{(open.end - open.start).toFixed(2)} s</span>
          </div>
          <FocusStrip
            window={focusWindow(open, view.stageTime)}
            events={events}
            region={open}
            shots={coach.shots}
            tFromBeep={view.tFromBeep}
            readOnly={readOnly}
            activeEdge={edit && edit.g.target !== "body" ? edit.g.target : null}
            onEdge={(edge) => (edit ? switchTarget(edge) : startEdge(edge))}
          />

          {edit ? (
            <>
              <Segmented
                label="Edge"
                value={edit.g.target}
                onChange={switchTarget}
                options={[
                  { value: "start", label: "Start" },
                  { value: "end", label: "End" },
                  { value: "body", label: "Move" },
                ]}
                className="self-start"
              />
              <EdgePad
                className="flex h-28 items-center justify-center rounded-md border-2 border-dashed border-rule text-sm text-muted"
                label="Drag sideways to move the edge"
                onStart={onPadStart}
                onMove={onPadMove}
                onEnd={onPadEnd}
                onCancel={onPadCancel}
              >
                Drag sideways: one width is one second
              </EdgePad>
              <div className="flex gap-2">
                <Button type="button" className="min-h-11 flex-1" disabled={edit.g.target === "body"} onClick={() => step(-1)}>
                  -1 frame
                </Button>
                <Button type="button" className="min-h-11 flex-1" disabled={edit.g.target === "body"} onClick={() => step(1)}>
                  +1 frame
                </Button>
              </div>
              <p className="text-sm text-muted">
                Snap to shots: {snapOn ? "on" : "off"}, 1 frame = {(1000 / fps).toFixed(0)} ms
              </p>
            </>
          ) : (
            <>
              {readOnly ? null : (
                <div className="flex gap-2">
                  <Button type="button" variant="destructive" className="min-h-11 flex-1" onClick={remove}>
                    Delete
                  </Button>
                  <Button type="button" className="min-h-11 flex-1" onClick={() => setKindOpen(true)}>
                    Kind
                  </Button>
                  <Button type="button" variant="primary" className="min-h-11 flex-1" onClick={keep}>
                    Keep
                  </Button>
                </div>
              )}
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  className="min-h-11 flex-1"
                  disabled={queue.length === 0}
                  onClick={() => setOpenId(prevOpenId(events, open.start))}
                >
                  Prev
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  className="min-h-11 flex-1"
                  onClick={() => {
                    const w = playWindow(open, view.stageTime);
                    play(w.from, w.to);
                  }}
                >
                  Play region
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  className="min-h-11 flex-1"
                  disabled={queue.length === 0}
                  onClick={() => setOpenId(nextOpenId(events, open.start))}
                >
                  Next
                </Button>
              </div>
              {readOnly ? (
                <p className="text-sm text-muted">Edit regions on the desktop: this match is synced from it.</p>
              ) : null}
            </>
          )}
        </>
      )}

      {regions.issue ? (
        <SaveNotice issue={regions.issue} busy={regions.busy} onRetry={regions.retry} onDismiss={regions.dismiss} />
      ) : null}

      <Sheet open={kindOpen && open != null} onClose={() => setKindOpen(false)} label="Kind">
        <div className="flex flex-col gap-2 p-4">
          {open
            ? LANES.map((kind) => {
                const blocked = kind !== open.kind && withKind(events, open.id, kind) == null;
                return (
                  <Button
                    key={kind}
                    type="button"
                    className="min-h-11 justify-start"
                    aria-pressed={kind === open.kind}
                    disabled={blocked}
                    title={blocked ? `Would overlap another ${LANE_LABEL[kind].toLowerCase()} region` : undefined}
                    onClick={() => setKind(kind)}
                  >
                    {LANE_LABEL[kind]}
                  </Button>
                );
              })
            : null}
        </div>
      </Sheet>
    </div>
  );
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}
