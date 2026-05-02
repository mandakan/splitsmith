/**
 * Audit screen v2 (#15).
 *
 * Through Step 2 -- single playback source + multi-video viewing tabs.
 *
 * Contract:
 *   - Audit truth = primary's audio. The waveform is always the primary's.
 *   - The active <video> element drives playback time. There is no separate
 *     <audio> element; audio you hear comes from the active video.
 *   - The Audit page exposes "primary timeline" times to children. When the
 *     active video is a secondary, we offset its `currentTime` by
 *     (secondary.beep_time - primary.beep_time) so the audit timeline lines up.
 *   - Switching active tab preserves play state and the primary-timeline
 *     position; the new video.currentTime is set on `loadedmetadata`.
 *
 * Markers, the stepper, save flow, and standalone /review come in later steps.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Crosshair, Loader2, Pause, Play } from "lucide-react";

import { VideoPanel } from "@/components/VideoPanel";
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
import {
  ApiError,
  api,
  type MatchProject,
  type PeaksResult,
  type StageVideo,
} from "@/lib/api";

const PEAK_BINS = 1500;

export function Audit() {
  const { stage: stageParam } = useParams();
  const navigate = useNavigate();

  const [project, setProject] = useState<MatchProject | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);

  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [peaksLoading, setPeaksLoading] = useState(false);
  const [peaksError, setPeaksError] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  // currentTime is in the **primary's** timeline. Secondary tabs map this
  // to their own video.currentTime via beep-offset math.
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [activeVideoIndex, setActiveVideoIndex] = useState(0);
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

  // Default to the first stage with a primary if no stage in URL.
  useEffect(() => {
    if (stageNumber != null) return;
    if (stagesWithPrimary.length === 0) return;
    navigate(`/audit/${stagesWithPrimary[0].stage_number}`, { replace: true });
  }, [stageNumber, stagesWithPrimary, navigate]);

  const stage = useMemo(() => {
    if (!project || stageNumber == null) return null;
    return project.stages.find((s) => s.stage_number === stageNumber) ?? null;
  }, [project, stageNumber]);

  // Order videos: primary first, secondaries by added_at.
  const videos = useMemo<StageVideo[]>(() => {
    if (!stage) return [];
    const primary = stage.videos.find((v) => v.role === "primary");
    const secondaries = stage.videos
      .filter((v) => v.role === "secondary")
      .slice()
      .sort((a, b) => a.added_at.localeCompare(b.added_at));
    return primary ? [primary, ...secondaries] : [...secondaries];
  }, [stage]);

  const primary = videos[0] ?? null;
  const activeVideo = videos[activeVideoIndex] ?? primary;
  const primaryBeep = primary?.beep_time ?? null;
  const activeBeep = activeVideo?.beep_time ?? null;
  const beepOffset = useMemo(() => {
    if (activeVideoIndex === 0) return 0;
    if (activeBeep == null || primaryBeep == null) return 0;
    return activeBeep - primaryBeep;
  }, [activeBeep, primaryBeep, activeVideoIndex]);

  // Reset state on stage change.
  useEffect(() => {
    setCurrentTime(0);
    setIsPlaying(false);
    setActiveVideoIndex(0);
    const v = videoRef.current;
    if (v) {
      v.pause();
      v.currentTime = 0;
    }
  }, [stageNumber]);

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

  // Tab change: when the <video> src swaps, browsers reset currentTime to 0.
  // Restore the primary-timeline position (mapped to the new video's clock)
  // once metadata is loaded so visuals stay in sync with the audit.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const targetVideoTime = currentTime + beepOffset;
    const seekWhenReady = () => {
      if (Number.isFinite(targetVideoTime) && targetVideoTime >= 0) {
        v.currentTime = targetVideoTime;
      }
      if (isPlaying) void v.play();
    };
    if (v.readyState >= 1) {
      seekWhenReady();
    } else {
      v.addEventListener("loadedmetadata", seekWhenReady, { once: true });
      return () => v.removeEventListener("loadedmetadata", seekWhenReady);
    }
    // Intentionally only react to active video changes; currentTime updates
    // come from the rAF loop below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeVideoIndex]);

  // rAF loop: pull video.currentTime into state while playing, mapped back
  // to the primary timeline.
  useEffect(() => {
    if (!isPlaying) return;
    const tick = () => {
      const v = videoRef.current;
      if (v) setCurrentTime(v.currentTime - beepOffset);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [isPlaying, beepOffset]);

  const handleScrub = useCallback(
    (primaryTime: number) => {
      const v = videoRef.current;
      if (v) v.currentTime = primaryTime + beepOffset;
      setCurrentTime(primaryTime);
    },
    [beepOffset],
  );

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      void v.play();
      setIsPlaying(true);
    } else {
      v.pause();
      setIsPlaying(false);
    }
  }, []);

  // Spacebar play/pause when not editing a form field.
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

  const videoSrc = activeVideo ? api.videoStreamUrl(activeVideo.path) : "";

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
            Drag the waveform to scrub. Spacebar plays / pauses. Tabs switch
            the viewing angle without changing the audit timeline.
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
              {videos.length > 1 ? (
                <Badge variant="secondary">{videos.length} videos</Badge>
              ) : null}
            </CardTitle>
            <CardDescription>
              Primary: <code className="text-xs">{primary.path}</code>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <VideoPanel
              ref={videoRef}
              videos={videos}
              primaryBeepTime={primaryBeep}
              activeIndex={activeVideoIndex}
              onActiveIndexChange={setActiveVideoIndex}
              videoSrc={videoSrc}
            />
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
                  {beepOffset !== 0 ? (
                    <span className="text-xs text-muted-foreground">
                      (cam offset {beepOffset >= 0 ? "+" : ""}
                      {beepOffset.toFixed(3)}s)
                    </span>
                  ) : null}
                </div>
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
