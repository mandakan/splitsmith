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
 * time. Stale badges surface auto-classifications whose stored class
 * disagrees with the current rule (typical after an Audit timestamp
 * edit); click to accept the recompute.
 *
 * Video grid + secondaries are deferred to a follow-up; this v1 plays
 * the primary only.
 */

import { ClipboardCheck, Flag, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

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

  const primaryVideo = useMemo<StageVideo | null>(() => {
    if (!stage) return null;
    return stage.videos.find((v) => v.role === "primary") ?? null;
  }, [stage]);

  const videoSrc = primaryVideo ? api.videoStreamUrl(primaryVideo.path) : "";

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
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
          <Card>
            <CardHeader>
              <CardTitle>
                Stage {coach.stage_number} -- {coach.stage_name}
              </CardTitle>
              <CardDescription>
                Click a shot to seek the video. Beep is at {coach.beep_time.toFixed(2)} s in source.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {videoSrc ? (
                <video
                  ref={videoRef}
                  src={videoSrc}
                  controls
                  preload="metadata"
                  className="aspect-video w-full rounded-md bg-black"
                />
              ) : (
                <div className="rounded-md border border-dashed border-border p-6 text-sm text-muted-foreground">
                  No primary video bound to this stage.
                </div>
              )}
            </CardContent>
          </Card>

          <ShotTable
            shots={coach.shots}
            activeShotNumber={activeShotNumber}
            onRowClick={seekTo}
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

function ShotTable({
  shots,
  activeShotNumber,
  onRowClick,
  onPatch,
}: {
  shots: CoachShot[];
  activeShotNumber: number | null;
  onRowClick: (s: CoachShot) => void;
  onPatch: (shotNumber: number, patch: CoachShotPatch) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Shots ({shots.length})</CardTitle>
        <CardDescription>
          Class chips are inline-editable. Manual stays sticky across reclassify.
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-border bg-muted/30 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-3 py-2 text-left">#</th>
                <th className="px-3 py-2 text-right">T</th>
                <th className="px-3 py-2 text-right">Split</th>
                <th className="px-3 py-2 text-left">Class</th>
                <th className="px-3 py-2 text-center">Flag</th>
                <th className="px-3 py-2 text-left">Note</th>
              </tr>
            </thead>
            <tbody>
              {shots.map((s) => (
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

function ShotRow({
  shot,
  active,
  onRowClick,
  onPatch,
}: {
  shot: CoachShot;
  active: boolean;
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
      )}
      onClick={() => onRowClick(shot)}
    >
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
