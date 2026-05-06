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
  ChevronRight,
  ClipboardCheck,
  Flag,
  Layers,
  Pause,
  Play,
  Radio,
  RefreshCw,
} from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  type CoachShot,
  type CoachShotPatch,
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

const CLASS_VARIANT: Record<CoachIntervalClass, "default" | "secondary" | "outline"> = {
  first_shot: "outline",
  split: "default",
  transition: "secondary",
  movement: "outline",
  reload: "outline",
  activation: "outline",
};

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

  // Multi-camera state. VideoPanel renders both modes; Coach owns the
  // single playback source (the primary) and the secondary refs/offsets
  // so click-to-scrub seeks every synced camera in lockstep.
  const [activeVideoIndex, setActiveVideoIndex] = useState(0);
  const [gridMode, setGridMode] = useState(false);
  const secondaryRefs = useRef<Map<string, HTMLVideoElement>>(new Map());
  // path -> (secondary.beep_time - primary.beep_time). When the primary
  // sits at sourceTime T, the synced secondary should be at T + offset
  // so the beep aligns. Recomputed whenever the videos array changes.
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

  // Offsets: secondaries with a beep_time can be synced; the rest stay
  // disabled in VideoPanel's tab list. Rebuild on every videos change so
  // a stage swap doesn't leave stale entries from the previous stage.
  useEffect(() => {
    const next = new Map<string, number>();
    const primaryBeep = primaryVideo?.beep_time ?? null;
    if (primaryBeep != null) {
      for (const v of videos.slice(1)) {
        if (v.beep_time != null) {
          next.set(v.path, v.beep_time - primaryBeep);
        }
      }
    }
    secondaryOffsets.current = next;
  }, [videos, primaryVideo]);

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
  }, []);

  const handleSecondaryBuffering = useCallback(
    (_path: string, _buffering: boolean) => {
      // Coach doesn't surface a panel-level buffering state -- the
      // SecondarySlot's own overlay is enough for review.
    },
    [],
  );

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
          <Card>
            <CardHeader>
              <CardTitle>
                Stage {coach.stage_number} -- {coach.stage_name}
              </CardTitle>
              <CardDescription>
                Click a shot to seek every synced camera. Beep is at {coach.beep_time.toFixed(2)} s
                in source.
              </CardDescription>
            </CardHeader>
            <CardContent>
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
                  <PlaybackBar
                    videoRef={videoRef}
                    secondaryRefs={secondaryRefs}
                  />
                </>
              )}
            </CardContent>
          </Card>

          <ShotTable
            shots={coach.shots}
            beepTime={coach.beep_time}
            activeShotNumber={activeShotNumber}
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
            onPatch={patchShot}
          />
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

// Group consecutive shots that share a class so a long "array on one
// target" doesn't fill the table with 6 visually identical split rows.
// First-shot is always its own group. We only collapse groups of >= 2;
// a singleton transition stays on its own row.
interface ShotGroup {
  key: string;
  cls: CoachIntervalClass | null;
  shots: CoachShot[];
}

function groupShots(shots: CoachShot[]): ShotGroup[] {
  const groups: ShotGroup[] = [];
  for (const s of shots) {
    const last = groups[groups.length - 1];
    if (
      last &&
      last.cls === s.interval_class &&
      // Never merge across first_shot -- it's a stage-level event, not
      // a coachable interval to be grouped.
      s.interval_class !== "first_shot"
    ) {
      last.shots.push(s);
    } else {
      groups.push({
        key: `${s.interval_class ?? "_"}-${s.shot_number}`,
        cls: s.interval_class,
        shots: [s],
      });
    }
  }
  return groups;
}

function ShotTable({
  shots,
  beepTime,
  activeShotNumber,
  onRowClick,
  onSeekToBeep,
  onPatch,
}: {
  shots: CoachShot[];
  beepTime: number;
  activeShotNumber: number | null;
  onRowClick: (s: CoachShot) => void;
  onSeekToBeep: () => void;
  onPatch: (shotNumber: number, patch: CoachShotPatch) => void;
}) {
  const [compact, setCompact] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const groups = useMemo(() => groupShots(shots), [shots]);

  const toggleGroup = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle>Shots ({shots.length})</CardTitle>
            <CardDescription>
              Class chips are inline-editable. Manual stays sticky across reclassify.
            </CardDescription>
          </div>
          <Button
            type="button"
            variant={compact ? "default" : "outline"}
            size="sm"
            onClick={() => setCompact((c) => !c)}
            title={
              compact
                ? "Show every shot individually"
                : "Collapse runs of same-class shots (e.g. arrays of splits)"
            }
          >
            <Layers className="size-4" />
            {compact ? "Compact" : "Detail"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-muted/30 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="w-6 px-2 py-2"></th>
                <th className="px-3 py-2 text-left">#</th>
                <th className="px-3 py-2 text-right">T</th>
                <th className="px-3 py-2 text-right">Split</th>
                <th className="px-3 py-2 text-left">Class</th>
                <th className="px-3 py-2 text-center">Flag</th>
                <th className="px-3 py-2 text-left">Note</th>
              </tr>
            </thead>
            <tbody>
              <BeepRow
                beepTime={beepTime}
                active={activeShotNumber === 0}
                onClick={onSeekToBeep}
              />
              {compact
                ? groups.map((g) => {
                    const isOpen = expanded.has(g.key);
                    if (g.shots.length < 2) {
                      return (
                        <ShotRow
                          key={g.key}
                          shot={g.shots[0]}
                          active={g.shots[0].shot_number === activeShotNumber}
                          onRowClick={onRowClick}
                          onPatch={onPatch}
                        />
                      );
                    }
                    return (
                      <Fragment key={g.key}>
                        <GroupRow
                          group={g}
                          expanded={isOpen}
                          activeShotNumber={activeShotNumber}
                          onToggle={() => toggleGroup(g.key)}
                          onRowClick={onRowClick}
                        />
                        {isOpen
                          ? g.shots.map((s) => (
                              <ShotRow
                                key={s.shot_number}
                                shot={s}
                                indented
                                active={s.shot_number === activeShotNumber}
                                onRowClick={onRowClick}
                                onPatch={onPatch}
                              />
                            ))
                          : null}
                      </Fragment>
                    );
                  })
                : shots.map((s) => (
                    <ShotRow
                      key={s.shot_number}
                      shot={s}
                      active={s.shot_number === activeShotNumber}
                      onRowClick={onRowClick}
                      onPatch={onPatch}
                    />
                  ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function BeepRow({
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
      title="Seek to the start signal"
    >
      <td className="w-6 px-2 py-2"></td>
      <td className="px-3 py-2 font-mono text-xs">
        <Radio className="inline size-3.5 text-primary" aria-hidden /> beep
      </td>
      <td className="px-3 py-2 text-right font-mono tabular-nums text-muted-foreground">
        0.000
      </td>
      <td className="px-3 py-2 text-right font-mono tabular-nums text-muted-foreground/60">
        --
      </td>
      <td className="px-3 py-2 text-xs uppercase tracking-wide text-muted-foreground">
        start signal
      </td>
      <td className="px-3 py-2"></td>
      <td className="px-3 py-2 text-xs text-muted-foreground/70 italic">
        source t = {beepTime.toFixed(3)} s
      </td>
    </tr>
  );
}

function GroupRow({
  group,
  expanded,
  activeShotNumber,
  onToggle,
  onRowClick,
}: {
  group: ShotGroup;
  expanded: boolean;
  activeShotNumber: number | null;
  onToggle: () => void;
  onRowClick: (s: CoachShot) => void;
}) {
  const first = group.shots[0];
  const last = group.shots[group.shots.length - 1];
  const totalSplit = group.shots.reduce((acc, s) => acc + s.split, 0);
  const containsActive = group.shots.some((s) => s.shot_number === activeShotNumber);
  const label = group.cls ? CLASS_LABELS[group.cls] : "unset";
  return (
    <tr
      className={cn(
        "cursor-pointer border-b border-border/50 transition-colors hover:bg-accent/40",
        containsActive && !expanded && "bg-accent/60",
      )}
      onClick={() => onRowClick(first)}
      title="Click to seek to the first shot in the group"
    >
      <td className="w-6 px-2 py-2 text-center" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          onClick={onToggle}
          className="inline-flex size-5 items-center justify-center rounded-md text-muted-foreground hover:bg-accent"
          aria-expanded={expanded}
          aria-label={expanded ? "Collapse group" : "Expand group"}
        >
          <ChevronRight
            className={cn("size-4 transition-transform", expanded && "rotate-90")}
          />
        </button>
      </td>
      <td className="px-3 py-2 font-mono text-xs">
        {first.shot_number}-{last.shot_number}
      </td>
      <td className="px-3 py-2 text-right font-mono tabular-nums">
        {first.time_from_beep.toFixed(3)}
      </td>
      <td className="px-3 py-2 text-right font-mono tabular-nums">
        {totalSplit.toFixed(3)}
      </td>
      <td className="px-3 py-2">
        <Badge variant={group.cls ? CLASS_VARIANT[group.cls] : "outline"}>
          {label} x{group.shots.length}
        </Badge>
      </td>
      <td className="px-3 py-2"></td>
      <td className="px-3 py-2 text-xs text-muted-foreground italic">
        click to seek -- expand to edit individual shots
      </td>
    </tr>
  );
}

const CLASS_LABELS: Record<CoachIntervalClass, string> = {
  first_shot: "First shot",
  split: "Splits",
  transition: "Transitions",
  movement: "Movements",
  reload: "Reloads",
  activation: "Activations",
};

function ShotRow({
  shot,
  active,
  indented = false,
  onRowClick,
  onPatch,
}: {
  shot: CoachShot;
  active: boolean;
  /** True when rendered as a child of an expanded group; renders with a
   *  faint left rail so the visual hierarchy reads at a glance. */
  indented?: boolean;
  onRowClick: (s: CoachShot) => void;
  onPatch: (shotNumber: number, patch: CoachShotPatch) => void;
}) {
  const [editingNote, setEditingNote] = useState(false);
  const [noteDraft, setNoteDraft] = useState(shot.coaching_note ?? "");
  useEffect(() => {
    if (!editingNote) setNoteDraft(shot.coaching_note ?? "");
  }, [shot.coaching_note, editingNote]);

  const saveNote = () => {
    setEditingNote(false);
    const trimmed = noteDraft.trim();
    if (trimmed === (shot.coaching_note ?? "")) return;
    if (trimmed === "") {
      onPatch(shot.shot_number, { clear_note: true });
    } else {
      onPatch(shot.shot_number, { coaching_note: trimmed });
    }
  };

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

  const onAcceptStale = () => {
    onPatch(shot.shot_number, { clear_class: true });
  };

  return (
    <tr
      className={cn(
        "cursor-pointer border-b border-border/50 transition-colors hover:bg-accent/40",
        active && "bg-accent",
        indented && "bg-muted/10",
      )}
      onClick={() => onRowClick(shot)}
    >
      <td
        className={cn(
          "w-6 px-2 py-2",
          indented && "border-l-2 border-border/40",
        )}
      ></td>
      <td className="px-3 py-2 font-mono text-xs">{shot.shot_number}</td>
      <td className="px-3 py-2 text-right font-mono tabular-nums">
        {shot.time_from_beep.toFixed(3)}
      </td>
      <td className="px-3 py-2 text-right font-mono tabular-nums">
        {shot.split.toFixed(3)}
      </td>
      <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-1.5">
          <select
            className="rounded-md border border-border bg-background px-2 py-1 text-xs"
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
            <Badge variant={CLASS_VARIANT[shot.interval_class]} className="text-[10px]">
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
              onClick={onAcceptStale}
              className="rounded-md border border-amber-500/60 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 hover:bg-amber-500/20 dark:text-amber-400"
              title="The rule disagrees with the stored class. Click to accept the recompute."
            >
              stale
            </button>
          ) : null}
          {shot.reload_hint && shot.interval_class !== "reload" ? (
            <span
              className="text-[10px] text-muted-foreground"
              title="Gap is large enough that this might be a reload"
            >
              reload?
            </span>
          ) : null}
        </div>
      </td>
      <td className="px-3 py-2 text-center" onClick={(e) => e.stopPropagation()}>
        <button
          type="button"
          onClick={() => onPatch(shot.shot_number, { improvement_flag: !shot.improvement_flag })}
          className={cn(
            "inline-flex size-7 items-center justify-center rounded-md border transition-colors",
            shot.improvement_flag
              ? "border-amber-500/60 bg-amber-500/10 text-amber-600 dark:text-amber-400"
              : "border-transparent text-muted-foreground hover:bg-accent",
          )}
          title={shot.improvement_flag ? "Unflag" : "Flag for improvement"}
        >
          <Flag className="size-4" />
        </button>
      </td>
      <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
        {editingNote ? (
          <input
            autoFocus
            className="w-full rounded-md border border-border bg-background px-2 py-1 text-xs"
            value={noteDraft}
            onChange={(e) => setNoteDraft(e.target.value)}
            onBlur={saveNote}
            onKeyDown={(e) => {
              if (e.key === "Enter") saveNote();
              if (e.key === "Escape") {
                setNoteDraft(shot.coaching_note ?? "");
                setEditingNote(false);
              }
            }}
          />
        ) : (
          <button
            type="button"
            onClick={() => setEditingNote(true)}
            className="block w-full truncate rounded-md px-1 py-1 text-left text-xs text-muted-foreground hover:bg-accent"
            title={shot.coaching_note ?? "Add a note"}
          >
            {shot.coaching_note || (
              <span className="italic text-muted-foreground/60">add note...</span>
            )}
          </button>
        )}
      </td>
    </tr>
  );
}

// Minimal playback control: play/pause + a current-time display. The
// shot table is the primary scrub UX; this just lets the user roll the
// video forward to watch a sequence between two clicks. Seeking via the
// shot rows already pauses-via-implicit by setting currentTime.
function PlaybackBar({
  videoRef,
  secondaryRefs,
}: {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  secondaryRefs: React.RefObject<Map<string, HTMLVideoElement>>;
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

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      void v.play().catch(() => {});
      // Start synced secondaries too. Each one was offset by the
      // primary timeupdate handler; just call .play() so they roll.
      const refs = secondaryRefs.current;
      if (refs) {
        for (const sv of refs.values()) {
          if (sv.paused) void sv.play().catch(() => {});
        }
      }
    } else {
      v.pause();
      const refs = secondaryRefs.current;
      if (refs) {
        for (const sv of refs.values()) sv.pause();
      }
    }
  }, [videoRef, secondaryRefs]);

  return (
    <div className="mt-3 flex items-center gap-3">
      <Button type="button" variant="outline" size="sm" onClick={togglePlay}>
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
