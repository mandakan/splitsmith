/**
 * Audit screen v2 (#15).
 *
 * Step 1 -- waveform substrate + scrubbing.
 *
 * What lands here in this step:
 *   - Stage selector (which primary's audio to audit)
 *   - <Waveform> rendering server-computed peaks for the primary's WAV
 *   - Hidden <audio> element wired to the waveform: drag-scrubbing the
 *     waveform updates audio.currentTime in real time
 *
 * Markers, multi-video tabs, the stepper, save flow, and standalone
 * /review mode arrive in subsequent steps. Until then, this screen
 * exercises the playback contract: one element drives time, the
 * waveform scrubs it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Crosshair, Loader2, Pause, Play } from "lucide-react";

import { Waveform } from "@/components/Waveform";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError, api, type MatchProject, type PeaksResult } from "@/lib/api";

const PEAK_BINS = 1500;

export function Audit() {
  const { stage: stageParam } = useParams();
  const navigate = useNavigate();

  const [project, setProject] = useState<MatchProject | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);

  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [peaksLoading, setPeaksLoading] = useState(false);
  const [peaksError, setPeaksError] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const rafRef = useRef<number | null>(null);

  const stageNumber = useMemo(() => {
    if (stageParam == null) return null;
    const n = Number.parseInt(stageParam, 10);
    return Number.isFinite(n) ? n : null;
  }, [stageParam]);

  // Load project once.
  useEffect(() => {
    let alive = true;
    api
      .getProject()
      .then((p) => {
        if (alive) setProject(p);
      })
      .catch((err) => {
        if (alive) setProjectError(err instanceof ApiError ? err.detail : String(err));
      });
    return () => {
      alive = false;
    };
  }, []);

  const stagesWithPrimary = useMemo(() => {
    if (!project) return [];
    return project.stages.filter((s) => s.videos.some((v) => v.role === "primary"));
  }, [project]);

  // If no stage selected, default to the first one with a primary.
  useEffect(() => {
    if (stageNumber != null) return;
    if (stagesWithPrimary.length === 0) return;
    navigate(`/audit/${stagesWithPrimary[0].stage_number}`, { replace: true });
  }, [stageNumber, stagesWithPrimary, navigate]);

  const stage = useMemo(() => {
    if (!project || stageNumber == null) return null;
    return project.stages.find((s) => s.stage_number === stageNumber) ?? null;
  }, [project, stageNumber]);

  const primary = useMemo(() => {
    if (!stage) return null;
    return stage.videos.find((v) => v.role === "primary") ?? null;
  }, [stage]);

  // Load peaks whenever the stage changes.
  useEffect(() => {
    if (stageNumber == null || !primary) {
      setPeaks(null);
      return;
    }
    let alive = true;
    setPeaksLoading(true);
    setPeaksError(null);
    api
      .getStagePeaks(stageNumber, PEAK_BINS)
      .then((p) => {
        if (alive) setPeaks(p);
      })
      .catch((err) => {
        if (alive) {
          setPeaksError(err instanceof ApiError ? err.detail : String(err));
          setPeaks(null);
        }
      })
      .finally(() => {
        if (alive) setPeaksLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [stageNumber, primary]);

  // rAF loop pulls audio.currentTime into state while playing.
  useEffect(() => {
    if (!isPlaying) return;
    const tick = () => {
      const a = audioRef.current;
      if (a) setCurrentTime(a.currentTime);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [isPlaying]);

  const audioUrl = stageNumber != null ? api.stageAudioUrl(stageNumber) : null;

  // Reset playback state on stage change.
  useEffect(() => {
    setCurrentTime(0);
    setIsPlaying(false);
    const a = audioRef.current;
    if (a) {
      a.pause();
      a.currentTime = 0;
    }
  }, [stageNumber]);

  const handleScrub = useCallback((t: number) => {
    const a = audioRef.current;
    if (a) a.currentTime = t;
    setCurrentTime(t);
  }, []);

  const togglePlay = useCallback(() => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) {
      void a.play();
      setIsPlaying(true);
    } else {
      a.pause();
      setIsPlaying(false);
    }
  }, []);

  // Spacebar play/pause when focus is anywhere outside form fields.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) return;
      e.preventDefault();
      togglePlay();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePlay]);

  if (projectError) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
        <Card>
          <CardHeader>
            <CardTitle>Failed to load project</CardTitle>
            <CardDescription>{projectError}</CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
        <Card>
          <CardContent className="flex items-center gap-2 py-6 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading project...
          </CardContent>
        </Card>
      </div>
    );
  }

  if (stagesWithPrimary.length === 0) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Crosshair className="size-5" /> Nothing to audit yet
            </CardTitle>
            <CardDescription>
              Assign a primary video to at least one stage on the Ingest screen.
              Audit always operates on a stage's primary audio.
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
          <p className="text-sm text-muted-foreground">
            Drag the waveform to scrub. Spacebar plays / pauses.
          </p>
        </div>
        <StageSelector
          stages={stagesWithPrimary.map((s) => ({
            stageNumber: s.stage_number,
            stageName: s.stage_name,
          }))}
          selected={stageNumber ?? null}
          onSelect={(n) => navigate(`/audit/${n}`)}
        />
      </div>

      {stage && primary ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-3">
              Stage {stage.stage_number} -- {stage.stage_name}
              {primary.beep_time != null ? (
                <Badge variant="outline">beep at {primary.beep_time.toFixed(3)}s</Badge>
              ) : (
                <Badge variant="destructive">no beep yet</Badge>
              )}
            </CardTitle>
            <CardDescription>
              Primary: <code className="text-xs">{primary.path}</code>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {peaksLoading ? (
              <div className="flex h-32 items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> Computing waveform...
              </div>
            ) : peaksError ? (
              <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
                Couldn't load peaks: {peaksError}
              </div>
            ) : peaks ? (
              <>
                <Waveform
                  peaks={peaks.peaks}
                  duration={peaks.duration}
                  currentTime={currentTime}
                  beepTime={primary.beep_time}
                  onScrub={handleScrub}
                  height={140}
                />
                <div className="flex items-center gap-3 text-sm">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={togglePlay}
                    aria-label={isPlaying ? "Pause" : "Play"}
                  >
                    {isPlaying ? (
                      <Pause className="size-4" />
                    ) : (
                      <Play className="size-4" />
                    )}
                  </Button>
                  <span className="font-mono text-muted-foreground">
                    {formatTime(currentTime)} / {formatTime(peaks.duration)}
                  </span>
                </div>
                {audioUrl ? (
                  <audio
                    ref={audioRef}
                    src={audioUrl}
                    preload="auto"
                    onEnded={() => setIsPlaying(false)}
                    className="sr-only"
                  />
                ) : null}
              </>
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

interface StageSelectorProps {
  stages: { stageNumber: number; stageName: string }[];
  selected: number | null;
  onSelect: (n: number) => void;
}

function StageSelector({ stages, selected, onSelect }: StageSelectorProps) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground">Stage</span>
      <select
        className="rounded-md border border-input bg-background px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        value={selected ?? ""}
        onChange={(e) => onSelect(Number.parseInt(e.target.value, 10))}
      >
        {stages.map((s) => (
          <option key={s.stageNumber} value={s.stageNumber}>
            {s.stageNumber} -- {s.stageName}
          </option>
        ))}
      </select>
    </label>
  );
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00.000";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds - Math.floor(seconds)) * 1000);
  return `${m}:${s.toString().padStart(2, "0")}.${ms.toString().padStart(3, "0")}`;
}
