/**
 * ClipSheet -- one video's detail on the Footage page (UX PR 6): the
 * preview, the stage it belongs to, its role, the beep with Detect, the
 * take's stage coverage when the recording spans stages, the shooter
 * it belongs to, and Remove. The shipped ClipDetail's writes, in a
 * sheet so the coverage matrix stays in view. In "assign" mode with no
 * clip the body is the unassigned list to pick from.
 */
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { CoverageSelect } from "@/components/ingest/CoverageSelect";
import { RoleToggles } from "@/components/ingest/RoleToggles";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import { Sheet } from "@/components/ui/Sheet";
import {
  ApiError,
  api,
  type MatchProject,
  type RawVideoManifestEntry,
  type ShooterListEntry,
  type StageEntry,
  type VideoRole,
} from "@/lib/api";
import { shortName, type UnassignedItem } from "@/lib/footage";
import { useSpacePlayPause } from "@/lib/keyboard";
import { takeHref } from "@/lib/matchHref";
import { findTakeForPath, takeFilename } from "@/lib/takes";
import type { ClipItem } from "@/pages/ingest/model";
import { pad2 } from "@/pages/ingest/model";

export interface ClipSheetProps {
  open: boolean;
  onClose: () => void;
  /** The shooter whose project the clip belongs to. */
  slug: string;
  shooterName: string;
  clip: ClipItem | null;
  /** Assign mode: no clip yet; `assignStage` is the empty cell that asked. */
  assignStage: number | null;
  unassigned: UnassignedItem[];
  allStages: StageEntry[];
  shooters: ShooterListEntry[];
  rawVideos: RawVideoManifestEntry[];
  mediaOnDesktop: boolean;
  busy: boolean;
  editDenied: boolean;
  auditHref: (slug: string, stage: number) => string;
  onMove: (videoPath: string, toStage: number | null, role: VideoRole) => Promise<void>;
  onRemove: (videoPath: string) => Promise<void>;
  onMoveShooter: (targetSlug: string, videoPaths: string[]) => Promise<void>;
  onPickUnassigned: (item: UnassignedItem, stage: number) => void;
  onError: (msg: string | null) => void;
  onReload?: (project?: MatchProject) => Promise<void>;
}

const SELECT = "min-w-0 rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 text-md text-ink disabled:opacity-50";
const ROLE_TICK: Record<VideoRole, "draw" | "muted" | undefined> = { primary: "draw", secondary: "muted", ignored: undefined };

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[88px_minmax(0,1fr)] items-center gap-3 text-md">
      <span className="text-sm text-muted">{label}</span>
      <div className="flex min-w-0 flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

export function ClipSheet(props: ClipSheetProps) {
  const { open, onClose, slug, shooterName, clip, assignStage, unassigned, allStages, shooters, rawVideos } = props;
  const { mediaOnDesktop, busy, editDenied, auditHref, onMove, onRemove, onMoveShooter, onPickUnassigned, onError, onReload } = props;
  const videoRef = useRef<HTMLVideoElement>(null);
  const { matchId } = useParams<{ matchId?: string }>();
  const [rowBusy, setRowBusy] = useState(false);
  const [detecting, setDetecting] = useState(false);
  // Coverage: `saved` is what the server has, `draft` the chip selection;
  // the server suggestion pre-fills the draft only when nothing is saved,
  // so Apply never silently replaces a persisted coverage.
  const [coverageSaved, setCoverageSaved] = useState<number[]>([]);
  const [coverageDraft, setCoverageDraft] = useState<number[]>([]);
  const [coverageSuggested, setCoverageSuggested] = useState<number[] | undefined>();
  const [coverageBusy, setCoverageBusy] = useState(false);

  useSpacePlayPause(() => {
    const el = videoRef.current;
    if (!el) return;
    if (el.paused) void el.play();
    else el.pause();
  }, open && clip != null);

  useEffect(() => {
    const persisted = findTakeForPath(rawVideos, clip?.video.path ?? "");
    const persistedStages = persisted?.covers_stages ?? [];
    setCoverageSaved(persistedStages);
    setCoverageDraft(persistedStages);
    setCoverageSuggested(undefined);
    if (!clip || allStages.length === 0) return;
    let alive = true;
    void api
      .suggestCoverage(slug, { path: clip.video.path })
      .then((s) => {
        if (!alive || s.covers_stages.length === 0) return;
        setCoverageSuggested(s.covers_stages);
        if (persistedStages.length === 0) setCoverageDraft(s.covers_stages);
      })
      .catch(() => {
        /* non-fatal: coverage starts empty */
      });
    return () => {
      alive = false;
    };
  }, [clip, slug, allStages.length, rawVideos]);

  if (!open) return null;

  if (!clip) {
    const stageName = allStages.find((s) => s.stage_number === assignStage)?.stage_name ?? "";
    return (
      <Sheet open onClose={onClose} label="Assign a video">
        <div className="flex items-center gap-3 border-b border-rule px-4 py-3">
          <span className="min-w-0 flex-1 text-md font-medium text-ink">
            Pick a video for {assignStage != null ? `${pad2(assignStage)} · ${stageName}` : "this stage"}
          </span>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close">
            &#10005;
          </Button>
        </div>
        <div className="flex flex-col overflow-y-auto">
          {unassigned.length === 0 ? (
            <p className="px-4 py-3 text-md text-muted">No unassigned videos. Add footage first, or move a file from another stage through its chip.</p>
          ) : (
            unassigned.map((item) => (
              <button
                key={`${item.slug}:${item.video.path}`}
                type="button"
                disabled={assignStage == null || busy}
                onClick={() => assignStage != null && onPickUnassigned(item, assignStage)}
                className="flex items-center gap-3 border-b border-rule px-4 py-2.5 text-left text-md hover:bg-surface-2 disabled:opacity-50"
              >
                <span className="min-w-0 flex-1 truncate font-mono text-sm text-ink">{shortName(item.video.path)}</span>
                <span className="text-sm text-muted">{item.shooterName}</span>
              </button>
            ))
          )}
        </div>
      </Sheet>
    );
  }

  const video = clip.video;
  const currentStage = clip.stageNumber;
  const filename = video.path.split("/").pop() ?? video.path;
  const take = findTakeForPath(rawVideos, video.path);
  const takeName = take != null ? takeFilename(take) : null;
  const cameraDetail = [clip.camera?.label, clip.camera?.model, clip.camera?.mount].filter(Boolean).join(" · ");
  const coverageDirty = JSON.stringify(coverageDraft) !== JSON.stringify(coverageSaved);
  const canDetect = video.role !== "ignored" && currentStage != null && !editDenied;

  async function changeStage(next: string) {
    setRowBusy(true);
    try {
      if (next === "unassigned") await onMove(video.path, null, video.role);
      else await onMove(video.path, Number(next), video.role);
    } finally {
      setRowBusy(false);
    }
  }
  async function setRole(next: VideoRole) {
    setRowBusy(true);
    try {
      await onMove(video.path, currentStage, next);
    } finally {
      setRowBusy(false);
    }
  }
  async function detectBeep() {
    if (currentStage == null) return;
    setDetecting(true);
    onError(null);
    try {
      await api.detectBeepForVideo(slug, currentStage, video.video_id);
    } catch (e) {
      onError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setDetecting(false);
    }
  }
  async function applyCoverage() {
    if (coverageBusy) return;
    setCoverageBusy(true);
    onError(null);
    try {
      await api.setRawVideoCoverage(slug, { filename, covers_stages: coverageDraft });
      setCoverageSaved(coverageDraft);
      await onReload?.();
    } catch (e) {
      onError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setCoverageBusy(false);
    }
  }
  const locked = busy || rowBusy || editDenied;

  return (
    <Sheet open onClose={onClose} label={filename}>
      <div className="flex items-center gap-3 border-b border-rule px-4 py-3">
        <span className="min-w-0 flex-1 truncate font-mono text-md text-ink" title={filename}>
          {filename}
        </span>
        <Chip tick={ROLE_TICK[video.role]}>{video.role}</Chip>
        <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close">
          &#10005;
        </Button>
      </div>
      <div className="flex flex-col gap-4 overflow-y-auto px-4 py-4">
        <div className="overflow-hidden rounded-[10px] bg-black">
          <video
            key={`${video.path}:${video.proxy_ready ? "p" : "s"}`}
            ref={videoRef}
            controls
            preload="metadata"
            src={api.shooterVideoStreamUrl(slug, video.path, "proxy")}
            className="aspect-video w-full object-contain"
          />
        </div>
        <div className="numeral flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted">
          <span>{shooterName}</span>
          {video.match_timestamp ? <span>{new Date(video.match_timestamp).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</span> : null}
          {cameraDetail ? <span>{cameraDetail}</span> : null}
          {video.proxy_ready === false ? <Chip tone="warn">{mediaOnDesktop ? "video on desktop" : "proxy generating"}</Chip> : null}
        </div>
        <Row label="Stage">
          <select
            aria-label="Stage"
            value={currentStage === null ? "unassigned" : String(currentStage)}
            onChange={(e) => void changeStage(e.target.value)}
            disabled={locked}
            className={SELECT}
          >
            <option value="unassigned">Unassigned</option>
            {allStages
              .filter((s) => !s.placeholder)
              .map((s) => (
                <option key={s.stage_number} value={s.stage_number}>
                  {pad2(s.stage_number)} &middot; {s.stage_name}
                  {s.time_seconds > 0 ? ` · ${s.time_seconds.toFixed(2)} s` : ""}
                  {s.stage_rounds?.expected != null ? ` · ${s.stage_rounds.expected} rounds` : ""}
                </option>
              ))}
          </select>
        </Row>
        <Row label="Role">
          {currentStage === null ? (
            <span className="text-sm text-muted">Pick a stage first; the first video on a stage becomes its primary.</span>
          ) : (
            <RoleToggles value={video.role} onChange={(r) => void setRole(r)} disabled={locked} />
          )}
        </Row>
        <Row label="Beep">
          {video.beep_time != null ? (
            <Chip tick="movement">
              {video.beep_time.toFixed(2)} &middot; {video.beep_reviewed ? "confirmed" : "unconfirmed"}
            </Chip>
          ) : (
            <span className="text-sm text-muted">{video.role === "ignored" ? "ignored" : "none yet"}</span>
          )}
          {canDetect ? (
            <Button size="sm" onClick={() => void detectBeep()} disabled={busy || detecting}>
              {detecting ? "Queuing…" : video.beep_time != null ? "Re-detect" : "Detect beep"}
            </Button>
          ) : null}
          {take != null && takeName != null ? (
            <Link to={takeHref(matchId, slug, takeName)} className="text-sm text-ink-2 hover:text-ink">
              Take overview &middot; {take.covers_stages.length} stages
            </Link>
          ) : null}
        </Row>
        {allStages.length > 0 ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <Label>Covers stages</Label>
              <Button size="sm" onClick={() => void applyCoverage()} disabled={coverageBusy || !coverageDirty || editDenied}>
                {coverageBusy ? "Saving…" : "Apply coverage"}
              </Button>
            </div>
            <CoverageSelect stages={allStages} value={coverageDraft} onChange={setCoverageDraft} suggested={coverageSuggested} />
          </div>
        ) : null}
        {shooters.length > 1 ? (
          <Row label="Shooter">
            <select
              aria-label="Shooter"
              value={slug}
              disabled={locked}
              onChange={(e) => {
                if (e.target.value === slug) return;
                setRowBusy(true);
                onError(null);
                void onMoveShooter(e.target.value, [video.path]).finally(() => setRowBusy(false));
              }}
              className={SELECT}
            >
              {shooters.map((s) => (
                <option key={s.slug} value={s.slug}>
                  {s.name}
                </option>
              ))}
            </select>
            <span className="text-sm text-muted">moves the file</span>
          </Row>
        ) : null}
      </div>
      <div className="mt-auto flex items-center gap-2 border-t border-rule px-4 py-3">
        <Button variant="destructive" size="sm" onClick={() => void onRemove(video.path)} disabled={locked}>
          Remove video
        </Button>
        <span className="flex-1" />
        {currentStage != null && video.role === "primary" ? (
          <Button size="sm" asChild>
            <Link to={auditHref(slug, currentStage)}>Open in Audit</Link>
          </Button>
        ) : null}
      </div>
    </Sheet>
  );
}
