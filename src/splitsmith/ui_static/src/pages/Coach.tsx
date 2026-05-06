/**
 * Coach screen (#161).
 *
 * Read-only on shot timing -- the user can't drag markers here. The page
 * is built around the Coach API (#161): it fetches the per-stage coach
 * payload, lets the user override classifications, flag improvement
 * shots, and leave coaching notes. All edits round-trip through the
 * audit JSON so Audit and Coach see each other's writes.
 *
 * Click any shot row -> the primary video seeks to that shot's source
 * time and every synced secondary follows. Stale badges surface
 * auto-classifications whose stored class disagrees with the current
 * rule (typical after an Audit timestamp edit); click to accept the
 * recompute.
 *
 * Multi-camera: VideoPanel handles the tab/grid layout. Coach owns the
 * single playback source (primary) and offsets each secondary by
 * ``(secondary.beep_time - primary.beep_time)`` so the beep aligns and
 * shots appear at the same scrub position across every camera.
 */

import {
  ClipboardCheck,
  Flag,
  Pause,
  Play,
  Radio,
  RefreshCw,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { VideoPanel } from "@/components/VideoPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  api,
  type CoachIntervalClass,
  type CoachIntervalDistribution,
  type CoachShot,
  type CoachShotPatch,
  type CoachStageDistributions,
  type CoachStageResponse,
  type MatchProject,
  type StageVideo,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const CLASS_OPTIONS: { value: CoachIntervalClass; label: string }[] = [
  { value: "first_shot", label: "First shot" },
  { value: "split", label: "Split" },
  { value: "transition", label: "Transition" },
  { value: "movement", label: "Movement" },
  { value: "reload", label: "Reload" },
  { value: "activation", label: "Activation" },
];

export function Coach() {
  const { stage: stageParam } = useParams();
  const navigate = useNavigate();

  const [project, setProject] = useState<MatchProject | null>(null);
  const [coach, setCoach] = useState<CoachStageResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reclassifying, setReclassifying] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [activeShotNumber, setActiveShotNumber] = useState<number | null>(null);

  const [listHeight, setListHeight] = useState<number | null>(null);
  const videoCardRef = useCallback((el: HTMLDivElement | null) => {
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      setListHeight(el.offsetHeight);
    });
    ro.observe(el);
  }, []);

  // Multi-camera state. VideoPanel renders both modes; Coach owns the
  // single playback source (the primary) and the secondary refs/offsets
  // so click-to-scrub seeks every synced camera in lockstep. Grid is
  // the default because side-by-side comparison is the entire point of
  // the Coach view.
  const [activeVideoIndex, setActiveVideoIndex] = useState(0);
  const [gridMode, setGridMode] = useState(true);
  const secondaryRefs = useRef<Map<string, HTMLVideoElement>>(new Map());
  // path -> (secondary.beep_in_clip - primary.beep_in_clip). When the
  // primary sits at clipTime T, the synced secondary should be at
  // T + offset so both cameras show the same real-world instant.
  // Computed in clip coords so the page works off the project-local
  // trimmed cache; source files don't need to be reachable.
  const secondaryOffsets = useRef<Map<string, number>>(new Map());

  const stageNumber = useMemo(() => {
    if (!stageParam) return null;
    const n = Number.parseInt(stageParam, 10);
    return Number.isFinite(n) ? n : null;
  }, [stageParam]);

  // Project (stage list).
  useEffect(() => {
    let alive = true;
    api
      .getProject()
      .then((p) => {
        if (alive) setProject(p);
      })
      .catch((err) => {
        if (alive) setLoadError(String(err));
      });
    return () => {
      alive = false;
    };
  }, []);

  const auditedStages = useMemo(() => {
    if (!project) return [];
    return project.stages.filter((s) => s.videos.some((v) => v.role === "primary"));
  }, [project]);

  // Auto-pick first stage if URL has no stage.
  useEffect(() => {
    if (stageNumber != null) return;
    if (auditedStages.length === 0) return;
    navigate(`/coach/${auditedStages[0].stage_number}`, { replace: true });
  }, [stageNumber, auditedStages, navigate]);

  const stage = useMemo(() => {
    if (!project || stageNumber == null) return null;
    return project.stages.find((s) => s.stage_number === stageNumber) ?? null;
  }, [project, stageNumber]);

  // VideoPanel expects [primary, ...secondaries]. Sort secondaries by
  // added_at to match Audit's tab order so the user's mental model of
  // "Cam 2 / Cam 3" stays stable across pages.
  const videos = useMemo<StageVideo[]>(() => {
    if (!stage) return [];
    const primary = stage.videos.find((v) => v.role === "primary");
    const secondaries = stage.videos
      .filter((v) => v.role === "secondary")
      .slice()
      .sort((a, b) => a.added_at.localeCompare(b.added_at));
    return primary ? [primary, ...secondaries] : [...secondaries];
  }, [stage]);

  const primaryVideo = videos[0] ?? null;
  const activeVideo = videos[activeVideoIndex] ?? primaryVideo;
  const videoSrc = activeVideo ? api.videoStreamUrl(activeVideo.path) : "";

  // Offsets: derive from the Coach API's per-video ``beep_in_clip``
  // values. Each camera's served clip has its own coordinate system
  // (trimmed clips are cut around their own beep), so the offset that
  // keeps two clips visually aligned is
  //   secondary.beep_in_clip - primary.beep_in_clip.
  // Only secondaries with a beep are synced; cameras without one stay
  // disabled in VideoPanel's tab list.
  useEffect(() => {
    const next = new Map<string, number>();
    const coachVideos = coach?.videos ?? [];
    const primaryEntry = coachVideos.find((v) => v.role === "primary");
    const primaryClipBeep = primaryEntry?.beep_in_clip ?? null;
    if (primaryClipBeep != null) {
      for (const v of coachVideos) {
        if (v.role === "primary") continue;
        if (v.beep_in_clip == null) continue;
        next.set(v.path, v.beep_in_clip - primaryClipBeep);
      }
    }
    secondaryOffsets.current = next;
  }, [coach]);

  // Reset active tab to primary on stage swap so the user lands on the
  // headcam by default; grid stays sticky across stages because that's
  // usually the workflow ("compare these two angles for every shot").
  useEffect(() => {
    setActiveVideoIndex(0);
  }, [stageNumber]);

  const handleSecondaryRef = useCallback(
    (path: string, el: HTMLVideoElement | null) => {
      if (el) {
        secondaryRefs.current.set(path, el);
        const off = secondaryOffsets.current.get(path);
        const v = videoRef.current;
        if (off != null && v != null) {
          const target = v.currentTime + off;
          if (el.readyState >= 1) {
            el.currentTime = target;
          } else {
            el.addEventListener(
              "loadedmetadata",
              () => {
                el.currentTime = target;
              },
              { once: true },
            );
          }
        }
      } else {
        secondaryRefs.current.delete(path);
      }
    },
    [],
  );

  // Coach is paused-by-default review; we don't need the play/pause loop
  // logic Audit uses. timeupdate just keeps secondaries glued to the
  // primary if the user hits the native controls. Clamp to each
  // secondary's content range so we don't seek past a shorter clip.
  // Also track which shot the playhead is currently inside so the table
  // highlights and auto-scrolls along with the video -- the user expects
  // the rows to follow as the recording plays out.
  const coachShotsRef = useRef<CoachShot[]>([]);
  const beepTimeRef = useRef<number>(0);
  useEffect(() => {
    coachShotsRef.current = coach?.shots ?? [];
    beepTimeRef.current = coach?.beep_time ?? 0;
  }, [coach]);

  const handlePrimaryTimeUpdate = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    for (const [path, sv] of secondaryRefs.current) {
      const off = secondaryOffsets.current.get(path);
      if (off == null) continue;
      const expected = v.currentTime + off;
      const dur = Number.isFinite(sv.duration) ? sv.duration : null;
      if (expected < 0) continue;
      if (dur != null && expected > dur) continue;
      // Only re-seek when drift exceeds ~80 ms; cheap timeupdate firings
      // would otherwise cause continuous re-seeks during native playback.
      if (Math.abs(sv.currentTime - expected) > 0.08) {
        sv.currentTime = expected;
      }
    }

    // Active-shot tracking: the row whose time_absolute most recently
    // passed under the playhead. ``shot 0`` (beep) covers the lead-in
    // before shot 1 fires.
    const shots = coachShotsRef.current;
    const t = v.currentTime;
    let nextActive: number | null = t >= beepTimeRef.current ? 0 : null;
    for (const s of shots) {
      if (s.time_absolute <= t + 0.01) {
        nextActive = s.shot_number;
      } else {
        break;
      }
    }
    setActiveShotNumber((prev) => (prev === nextActive ? prev : nextActive));
  }, []);

  const handleSecondaryBuffering = useCallback(
    (_path: string, _buffering: boolean) => {
      // Coach doesn't surface a panel-level buffering state -- the
      // SecondarySlot's own overlay is enough for review.
    },
    [],
  );

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      void v.play().catch(() => {});
      for (const sv of secondaryRefs.current.values()) {
        if (sv.paused) void sv.play().catch(() => {});
      }
    } else {
      v.pause();
      for (const sv of secondaryRefs.current.values()) sv.pause();
    }
  }, [videoRef, secondaryRefs]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      const tag = (document.activeElement?.tagName ?? "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      e.preventDefault();
      togglePlay();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [togglePlay]);

  // Load coach payload whenever the stage changes; on first hit we
  // auto-reclassify if any shot is unclassified, so the user always
  // lands on a populated table.
  useEffect(() => {
    if (stageNumber == null) return;
    let alive = true;
    setLoading(true);
    setLoadError(null);
    setActiveShotNumber(null);
    (async () => {
      try {
        const initial = await api.getStageCoach(stageNumber);
        if (!alive) return;
        if (!initial) {
          setCoach(null);
          setLoadError("This stage has no audit JSON yet -- audit it first.");
          return;
        }
        const needsAuto = initial.shots.some((s) => s.interval_class === null);
        if (needsAuto) {
          const populated = await api.reclassifyStageCoach(stageNumber);
          if (!alive) return;
          setCoach(populated);
        } else {
          setCoach(initial);
        }
      } catch (err) {
        if (alive) setLoadError(String(err));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [stageNumber]);

  const onReclassify = useCallback(async () => {
    if (stageNumber == null) return;
    setReclassifying(true);
    try {
      const updated = await api.reclassifyStageCoach(stageNumber);
      setCoach(updated);
    } catch (err) {
      setLoadError(String(err));
    } finally {
      setReclassifying(false);
    }
  }, [stageNumber]);

  const seekTo = useCallback((shot: CoachShot) => {
    setActiveShotNumber(shot.shot_number);
    const v = videoRef.current;
    if (v) {
      v.currentTime = shot.time_absolute;
    }
    for (const [path, sv] of secondaryRefs.current) {
      const off = secondaryOffsets.current.get(path);
      if (off == null) continue;
      const expected = shot.time_absolute + off;
      const dur = Number.isFinite(sv.duration) ? sv.duration : null;
      if (expected < 0) continue;
      if (dur != null && expected > dur) continue;
      sv.currentTime = expected;
    }
  }, []);

  const patchShot = useCallback(
    async (shotNumber: number, patch: CoachShotPatch) => {
      if (stageNumber == null) return;
      try {
        const updated = await api.patchStageShotCoach(stageNumber, shotNumber, patch);
        setCoach(updated);
      } catch (err) {
        setLoadError(String(err));
      }
    },
    [stageNumber],
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <ClipboardCheck className="size-6 text-primary" />
            Coach
          </h1>
          <p className="text-sm text-muted-foreground">
            Review shots, classify intervals, flag improvements. Marker timing is read-only here -- edit in Audit.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={onReclassify}
          disabled={reclassifying || stageNumber == null}
        >
          <RefreshCw className={cn("size-4", reclassifying && "animate-spin")} />
          Reclassify
        </Button>
      </div>

      <StagePicker stages={auditedStages} active={stageNumber} />

      {loadError ? (
        <Card>
          <CardHeader>
            <CardTitle>Can't load coach view</CardTitle>
            <CardDescription>{loadError}</CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      {coach && (
        <div className="space-y-6">
          {/* Top row: video grid + condensed event list, locked to the
              same height so the user can scrub the list while watching
              the video without either disappearing. The event list is
              fixed-width on lg+; on narrow viewports it stacks below. */}
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(260px,340px)] lg:items-start">
            <Card ref={videoCardRef} className="flex flex-col">
              <CardHeader>
                <CardTitle>
                  Stage {coach.stage_number} -- {coach.stage_name}
                </CardTitle>
                <CardDescription>
                  Click a shot to seek every synced camera.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex-1">
                {videos.length === 0 ? (
                  <div className="rounded-md border border-dashed border-border p-6 text-sm text-muted-foreground">
                    No primary video bound to this stage.
                  </div>
                ) : (
                  <>
                    <VideoPanel
                      ref={videoRef}
                      videos={videos}
                      primaryBeepTime={primaryVideo?.beep_time ?? null}
                      activeIndex={activeVideoIndex}
                      onActiveIndexChange={setActiveVideoIndex}
                      videoSrc={videoSrc}
                      gridMode={gridMode}
                      onGridModeToggle={() => setGridMode((g) => !g)}
                      onSecondaryRef={handleSecondaryRef}
                      onSecondaryBuffering={handleSecondaryBuffering}
                      onPrimaryTimeUpdate={handlePrimaryTimeUpdate}
                    />
                    <PlaybackBar videoRef={videoRef} onTogglePlay={togglePlay} />
                  </>
                )}
              </CardContent>
            </Card>

            <CondensedShotList
              shots={coach.shots}
              beepTime={coach.beep_time}
              activeShotNumber={activeShotNumber}
              heightPx={listHeight}
              onRowClick={seekTo}
              onSeekToBeep={() => {
                setActiveShotNumber(0);
                const v = videoRef.current;
                if (v) v.currentTime = coach.beep_time;
                for (const [path, sv] of secondaryRefs.current) {
                  const off = secondaryOffsets.current.get(path);
                  if (off == null) continue;
                  const expected = coach.beep_time + off;
                  const dur = Number.isFinite(sv.duration) ? sv.duration : null;
                  if (expected < 0) continue;
                  if (dur != null && expected > dur) continue;
                  sv.currentTime = expected;
                }
              }}
            />
          </div>

          {/* Bottom strip: full edit surface for the active event. */}
          <ActiveShotDetail
            shot={
              activeShotNumber == null || activeShotNumber === 0
                ? null
                : coach.shots.find((s) => s.shot_number === activeShotNumber) ?? null
            }
            beepActive={activeShotNumber === 0}
            beepTime={coach.beep_time}
            onPatch={patchShot}
          />

          {stageNumber != null ? <DistributionsPanel stageNumber={stageNumber} /> : null}
        </div>
      )}

      {loading && !coach ? (
        <Card>
          <CardHeader>
            <CardDescription>Loading coach view...</CardDescription>
          </CardHeader>
        </Card>
      ) : null}
    </div>
  );
}

function StagePicker({
  stages,
  active,
}: {
  stages: { stage_number: number; stage_name: string }[];
  active: number | null;
}) {
  const navigate = useNavigate();
  if (stages.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {stages.map((s) => {
        const isActive = s.stage_number === active;
        return (
          <button
            key={s.stage_number}
            type="button"
            onClick={() => navigate(`/coach/${s.stage_number}`)}
            className={cn(
              "rounded-md border px-3 py-1.5 text-sm transition-colors",
              isActive
                ? "border-primary bg-primary text-primary-foreground"
                : "border-border bg-card text-foreground hover:bg-accent",
            )}
          >
            <span className="font-mono text-xs text-muted-foreground/70 mr-2">
              #{s.stage_number}
            </span>
            {s.stage_name}
          </button>
        );
      })}
    </div>
  );
}

const CLASS_SHORT_LABEL: Record<CoachIntervalClass, string> = {
  first_shot: "draw",
  split: "split",
  transition: "trans",
  movement: "move",
  reload: "reload",
  activation: "activ",
};

const CLASS_FULL_LABEL: Record<CoachIntervalClass, string> = {
  first_shot: "First shot (draw)",
  split: "Split -- same position, different target",
  transition: "Transition -- short move between positions",
  movement: "Movement -- longer repositioning",
  reload: "Reload",
  activation: "Activation -- activating a target (e.g. mover)",
};

function CondensedShotList({
  shots,
  beepTime,
  activeShotNumber,
  heightPx,
  onRowClick,
  onSeekToBeep,
}: {
  shots: CoachShot[];
  beepTime: number;
  activeShotNumber: number | null;
  heightPx: number | null;
  onRowClick: (s: CoachShot) => void;
  onSeekToBeep: () => void;
}) {
  const tableRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (activeShotNumber == null) return;
    const container = tableRef.current;
    if (!container) return;
    const row = container.querySelector<HTMLElement>(
      `[data-active-shot="${activeShotNumber}"]`,
    );
    if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeShotNumber]);

  return (
    <Card
      className="flex flex-col overflow-hidden"
      style={heightPx != null ? { height: heightPx } : undefined}
    >
      <CardHeader className="shrink-0 pb-2">
        <CardTitle className="text-base">Shots ({shots.length + 1})</CardTitle>
        <CardDescription className="text-xs">
          Click a row to seek. Select to annotate below.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex-1 overflow-hidden p-0">
        <div ref={tableRef} className="h-full overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 border-b border-border bg-muted/30 text-[10px] uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-2 py-1.5 text-left">#</th>
                <th className="px-2 py-1.5 text-right">T</th>
                <th className="px-2 py-1.5 text-right">Split</th>
                <th className="px-2 py-1.5 text-left">Class</th>
              </tr>
            </thead>
            <tbody>
              <CondensedBeepRow
                beepTime={beepTime}
                active={activeShotNumber === 0}
                onClick={onSeekToBeep}
              />
              {shots.map((s) => (
                <CondensedShotRow
                  key={s.shot_number}
                  shot={s}
                  active={s.shot_number === activeShotNumber}
                  onClick={() => onRowClick(s)}
                />
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function CondensedBeepRow({
  beepTime,
  active,
  onClick,
}: {
  beepTime: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <tr
      className={cn(
        "cursor-pointer border-b border-border/50 bg-muted/20 transition-colors hover:bg-accent/40",
        active && "bg-accent",
      )}
      onClick={onClick}
      data-active-shot={0}
      title={`Seek to the start signal (source t = ${beepTime.toFixed(3)} s)`}
    >
      <td className="px-2 py-1.5 font-mono text-[11px] text-primary">
        <Radio className="inline size-3 align-text-bottom" aria-hidden /> beep
      </td>
      <td className="px-2 py-1.5 text-right font-mono tabular-nums text-[11px] text-muted-foreground">
        0.000
      </td>
      <td className="px-2 py-1.5 text-right font-mono tabular-nums text-[11px] text-muted-foreground/60">
        --
      </td>
      <td className="px-2 py-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">
        start
      </td>
    </tr>
  );
}

function CondensedShotRow({
  shot,
  active,
  onClick,
}: {
  shot: CoachShot;
  active: boolean;
  onClick: () => void;
}) {
  const cls = shot.interval_class;
  return (
    <tr
      className={cn(
        "cursor-pointer border-b border-border/50 transition-colors hover:bg-accent/40",
        active && "bg-accent",
        shot.improvement_flag && "border-l-2 border-l-amber-500/70",
      )}
      onClick={onClick}
      data-active-shot={active ? shot.shot_number : undefined}
      title={shot.coaching_note ?? undefined}
    >
      <td className="px-2 py-1.5 font-mono text-[11px]">{shot.shot_number}</td>
      <td className="px-2 py-1.5 text-right font-mono tabular-nums text-[11px]">
        {shot.time_from_beep.toFixed(3)}
      </td>
      <td className="px-2 py-1.5 text-right font-mono tabular-nums text-[11px]">
        {shot.split.toFixed(3)}
      </td>
      <td className="px-2 py-1.5">
        <span className="flex items-center gap-1 text-[11px]">
          {cls ? CLASS_SHORT_LABEL[cls] : "--"}
          {shot.stale ? (
            <span
              className="rounded-sm bg-amber-500/15 px-1 text-[9px] font-medium text-amber-600 dark:text-amber-400"
              title="Stored class disagrees with the current rule"
            >
              !
            </span>
          ) : null}
          {shot.coaching_note ? (
            <span className="text-[9px] text-muted-foreground" title={shot.coaching_note}>
              ●
            </span>
          ) : null}
        </span>
      </td>
    </tr>
  );
}

function ActiveShotDetail({
  shot,
  beepActive,
  beepTime,
  onPatch,
}: {
  shot: CoachShot | null;
  beepActive: boolean;
  beepTime: number;
  onPatch: (shotNumber: number, patch: CoachShotPatch) => void;
}) {
  const [noteDraft, setNoteDraft] = useState("");
  useEffect(() => {
    setNoteDraft(shot?.coaching_note ?? "");
  }, [shot?.shot_number, shot?.coaching_note]);

  if (beepActive) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>
            <Radio className="inline size-4 align-text-bottom text-primary" /> Start signal
          </CardTitle>
          <CardDescription>
            Source time: {beepTime.toFixed(3)} s. Click a shot row to edit it.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }
  if (!shot) {
    return (
      <Card>
        <CardHeader>
          <CardDescription>
            Click an event in the list to flag, classify, or annotate it.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const onClassChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const val = e.target.value;
    if (val === "_auto") {
      onPatch(shot.shot_number, { clear_class: true });
      return;
    }
    onPatch(shot.shot_number, {
      interval_class: val as CoachIntervalClass,
      interval_class_source: "manual",
    });
  };

  const saveNote = () => {
    const trimmed = noteDraft.trim();
    if (trimmed === (shot.coaching_note ?? "")) return;
    if (trimmed === "") {
      onPatch(shot.shot_number, { clear_note: true });
    } else {
      onPatch(shot.shot_number, { coaching_note: trimmed });
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-baseline gap-3">
          <span>Shot {shot.shot_number}</span>
          <span className="font-mono text-sm font-normal tabular-nums text-muted-foreground">
            T {shot.time_from_beep.toFixed(3)} s · split {shot.split.toFixed(3)} s
          </span>
        </CardTitle>
        <CardDescription>
          Edits round-trip through the audit JSON; manual classifications survive reclassify.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-[auto_auto_1fr]">
        <div className="space-y-1">
          <label className="text-xs uppercase tracking-wide text-muted-foreground">Class</label>
          <div className="flex items-center gap-2">
            <select
              className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              value={shot.interval_class ?? ""}
              onChange={onClassChange}
            >
              <option value="" disabled>
                -- unset --
              </option>
              {CLASS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
              {shot.interval_class_source === "manual" ? (
                <option value="_auto">Reset to auto</option>
              ) : null}
            </select>
            {shot.interval_class && shot.interval_class_source === "auto" ? (
              <Badge variant="outline" className="text-[10px]">
                auto
              </Badge>
            ) : null}
            {shot.interval_class && shot.interval_class_source === "manual" ? (
              <Badge variant="default" className="text-[10px]">
                manual
              </Badge>
            ) : null}
            {shot.stale ? (
              <button
                type="button"
                onClick={() => onPatch(shot.shot_number, { clear_class: true })}
                className="rounded-md border border-amber-500/60 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-600 hover:bg-amber-500/20 dark:text-amber-400"
                title="Rule disagrees with the stored class. Click to accept the recompute."
              >
                Accept rule
              </button>
            ) : null}
            {shot.reload_hint && shot.interval_class !== "reload" ? (
              <span
                className="text-xs text-muted-foreground"
                title="Long gap -- could be a reload"
              >
                reload?
              </span>
            ) : null}
          </div>
          {shot.interval_class ? (
            <p className="text-xs text-muted-foreground">{CLASS_FULL_LABEL[shot.interval_class]}</p>
          ) : null}
        </div>

        <div className="space-y-1">
          <label className="text-xs uppercase tracking-wide text-muted-foreground">
            Improvement
          </label>
          <button
            type="button"
            onClick={() =>
              onPatch(shot.shot_number, { improvement_flag: !shot.improvement_flag })
            }
            className={cn(
              "flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm transition-colors",
              shot.improvement_flag
                ? "border-amber-500/60 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                : "border-border bg-background text-muted-foreground hover:bg-accent",
            )}
          >
            <Flag className="size-4" />
            {shot.improvement_flag ? "Flagged" : "Flag"}
          </button>
        </div>

        <div className="space-y-1">
          <label className="text-xs uppercase tracking-wide text-muted-foreground">Note</label>
          <input
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            placeholder="What to remember about this shot"
            value={noteDraft}
            onChange={(e) => setNoteDraft(e.target.value)}
            onBlur={saveNote}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") {
                setNoteDraft(shot.coaching_note ?? "");
                e.currentTarget.blur();
              }
            }}
          />
        </div>
      </CardContent>
    </Card>
  );
}

// Minimal playback control: play/pause + a current-time display.
function PlaybackBar({
  videoRef,
  onTogglePlay,
}: {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  onTogglePlay: () => void;
}) {
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState<number | null>(null);

  // Subscribe to playback state on the primary so the toggle button
  // reflects what the user sees. Re-binds on ref churn (stage switch).
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onTime = () => setCurrentTime(v.currentTime);
    const onMeta = () => {
      setDuration(Number.isFinite(v.duration) ? v.duration : null);
      setCurrentTime(v.currentTime);
    };
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("loadedmetadata", onMeta);
    if (v.readyState >= 1) onMeta();
    return () => {
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("loadedmetadata", onMeta);
    };
    // The ref identity is stable but the underlying element swaps on
    // tab change; tying the effect to `videoRef.current` keeps the
    // listeners pointed at the live element. Reading `.current` inside
    // the effect deliberately on each run.
  }, [videoRef, videoRef.current]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="mt-3 flex items-center gap-3">
      <Button type="button" variant="outline" size="sm" onClick={onTogglePlay}>
        {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
        {playing ? "Pause" : "Play"}
      </Button>
      <div className="font-mono text-xs tabular-nums text-muted-foreground">
        {formatTime(currentTime)}
        {duration != null ? ` / ${formatTime(duration)}` : ""}
      </div>
    </div>
  );
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "00:00.000";
  const mins = Math.floor(seconds / 60);
  const rest = seconds - mins * 60;
  const wholeSec = Math.floor(rest);
  const ms = Math.round((rest - wholeSec) * 1000);
  const mm = String(mins).padStart(2, "0");
  const ss = String(wholeSec).padStart(2, "0");
  const mmm = String(ms).padStart(3, "0");
  return `${mm}:${ss}.${mmm}`;
}

// ---------------------------------------------------------------------------
// Distributions panel (#163)
// ---------------------------------------------------------------------------

function DistributionsPanel({ stageNumber }: { stageNumber: number }) {
  const [data, setData] = useState<CoachStageDistributions | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    api
      .getStageCoachDistributions(stageNumber)
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((err) => {
        if (alive) setError(String(err));
      });
    return () => {
      alive = false;
    };
  }, [stageNumber]);

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Distributions</CardTitle>
          <CardDescription>{error}</CardDescription>
        </CardHeader>
      </Card>
    );
  }
  if (!data) return null;
  // Splits + transitions are the coachable bread-and-butter; movement
  // and reload distributions usually have a handful of values per stage
  // and read better at the match level. Show the top two here, defer
  // the rest to the (future) match view (#162 / #163 follow-up).
  const focus = data.distributions.filter(
    (d) => d.interval_class === "split" || d.interval_class === "transition",
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle>Distributions</CardTitle>
        <CardDescription>
          {data.first_shot_s != null
            ? `Draw: ${data.first_shot_s.toFixed(3)} s. Histograms below cover splits + transitions for this stage.`
            : "Histograms cover splits + transitions for this stage."}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2">
        {focus.map((d) => (
          <Histogram key={d.interval_class} dist={d} />
        ))}
      </CardContent>
    </Card>
  );
}

function Histogram({ dist }: { dist: CoachIntervalDistribution }) {
  const maxCount = Math.max(0, ...dist.buckets.map((b) => b.count));
  const label =
    dist.interval_class === "split"
      ? "Splits"
      : dist.interval_class === "transition"
        ? "Transitions"
        : dist.interval_class === "movement"
          ? "Movements"
          : dist.interval_class === "reload"
            ? "Reloads"
            : dist.interval_class;
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium">{label}</span>
        <span className="font-mono text-xs text-muted-foreground">
          n={dist.count}
          {dist.mean_s != null ? ` · mean ${dist.mean_s.toFixed(3)} s` : ""}
          {dist.median_s != null ? ` · med ${dist.median_s.toFixed(3)} s` : ""}
          {dist.p90_s != null ? ` · p90 ${dist.p90_s.toFixed(3)} s` : ""}
        </span>
      </div>
      {dist.count === 0 ? (
        <div className="rounded-md border border-dashed border-border p-4 text-xs text-muted-foreground">
          No values on this stage.
        </div>
      ) : (
        <div className="space-y-1 font-mono text-[11px]">
          {dist.buckets.map((b) => {
            const w = maxCount > 0 ? (b.count / maxCount) * 100 : 0;
            return (
              <div key={`${b.lo}-${b.hi}`} className="flex items-center gap-2">
                <span className="w-20 shrink-0 text-right tabular-nums text-muted-foreground">
                  {b.lo.toFixed(2)}-{b.hi.toFixed(2)} s
                </span>
                <div className="flex-1">
                  <div
                    className="h-3 rounded-sm bg-primary/70"
                    style={{ width: `${w}%` }}
                  />
                </div>
                <span className="w-6 text-right tabular-nums text-muted-foreground">
                  {b.count}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
