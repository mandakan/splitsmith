/**
 * Ingest route (/ingest) -- redesigned in the Shot Timer aesthetic (#325).
 *
 * Two states selected from project state:
 *
 *   Empty -- no videos assigned to any stage. Renders polished/18:
 *   dashed drop zone, storage-choice radio, tip cards. Picking a folder
 *   triggers the scan and transitions to Review.
 *
 *   Review -- post-drop. Renders polished/05: drop summary, storage
 *   choice (compact), cameras card derived from probed metadata, per-
 *   stage assignment cards with per-video role toggles + reassignment
 *   dropdown, unassigned tray, footer with Confirm.
 *
 * Storage choice (reference-in-place vs copy-into-project) is honored
 * end-to-end via the existing /api/videos/scan ``link_mode`` parameter.
 */

import {
  ArrowLeft,
  ArrowRight,
  Camera,
  ChevronUp,
  Clock,
  Folder,
  Info,
  Loader2,
  MoreVertical,
  Package,
  Play,
  Plus,
  Video,
  X,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { AddFootageModal } from "@/components/AddFootageModal";
import { ShooterPickerPopover } from "@/components/ingest/ShooterPickerPopover";
import { StageReference } from "@/components/ingest/StageReference";
import { RelinkDialog } from "@/components/RelinkDialog";
import { ShooterChipStrip } from "@/components/match/ShooterChipStrip";
import { Brand, Kicker } from "@/components/ui";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  CAMERA_MOUNTS,
  api,
  type BulkCameraSetItem,
  type CalibratedCameraModel,
  type CameraMount,
  type MatchProject,
  type MoveShooterBlocked,
  type ServerHealth,
  type ShooterListEntry,
  type StageEntry,
  type StageVideo,
  type VideoRole,
} from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";
import { cn } from "@/lib/utils";

type StorageMode = "symlink" | "copy";

export function Ingest() {
  const { slug, matchId } = useParams<{ slug: string; matchId?: string }>();
  if (!slug)
    return (
      <Navigate
        to={matchId ? `/match/${matchId}/shooters` : "/shooters"}
        replace
      />
    );
  return <IngestInner slug={slug} />;
}

function IngestInner({ slug }: { slug: string }) {
  const navigate = useNavigate();
  const href = useMatchHref();
  const [project, setProject] = useState<MatchProject | null>(null);
  const [health, setHealth] = useState<ServerHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Default storage mode for the ingest modal. Stays on the page state
  // so flipping it between two ingests doesn't get lost when the modal
  // closes; the modal seeds its own picker from this value.
  const [storage, setStorage] = useState<StorageMode>("symlink");
  const [showAddFootage, setShowAddFootage] = useState(false);
  const [showRelinkDialog, setShowRelinkDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [lastScannedDir, setLastScannedDir] = useState<string | null>(null);
  // Beep-review pending count -- drives the "Review N beeps" CTA in the
  // review state header so the operator has a clear next step from the
  // videos page (no more digging for /beep-review by URL).
  const [beepPending, setBeepPending] = useState<number>(0);
  // A1: Shooter list for the ShooterChipStrip + move UI.
  const [shooters, setShooters] = useState<ShooterListEntry[]>([]);
  // B1: Paths from the most recent import batch. Cleared on banner dismiss
  // or after a successful move. Not persisted across reloads.
  const [lastImportedPaths, setLastImportedPaths] = useState<string[] | null>(null);
  // B1: Blocked stages surfaced after a move attempt.
  const [moveBlocked, setMoveBlocked] = useState<MoveShooterBlocked[]>([]);

  async function reload() {
    setError(null);
    try {
      const [p, h] = await Promise.all([
        api.getProject(slug),
        api.getHealth(),
      ]);
      setProject(p);
      setHealth(h);
      if (p.last_scanned_dir) setLastScannedDir(p.last_scanned_dir);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
    // A1: Shooter list. Errors here are silent -- the strip just hides.
    try {
      const r = await api.listMatchShooters();
      setShooters(r.shooters);
    } catch {
      // non-fatal; leave existing list
    }
    // Refresh the beep-queue summary in parallel; errors here are silent
    // (the CTA just hides when the count is zero or unknown).
    try {
      const q = await api.getBeepQueue();
      setBeepPending(q.pending_count);
    } catch {
      setBeepPending(0);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  const assignedCount = useMemo(() => {
    if (!project) return 0;
    return project.stages.reduce((sum, s) => sum + (s.videos?.length ?? 0), 0);
  }, [project]);
  // Count unassigned too -- a successful import where nothing auto-
  // matched a stage still produces visible work for the user (the
  // unassigned tray in ReviewState). Without this the page sits on
  // the EmptyState placeholder and reads as "nothing happened" after
  // the modal closes.
  const unassignedCount = project?.unassigned_videos?.length ?? 0;
  const isEmpty =
    (project?.stages.length ?? 0) === 0 ||
    assignedCount + unassignedCount === 0;

  // Active shooter name for A2 modal header echo.
  const activeShooterName = shooters.find((s) => s.slug === slug)?.name;

  async function afterImport(_imported: number, paths: string[]) {
    // Reload regardless of count -- partial successes also need a refresh
    // for the user's stage tray to reflect the new videos.
    setError(null);
    // B1: capture the batch for the post-import banner.
    if (paths.length > 0) {
      setLastImportedPaths(paths);
      setMoveBlocked([]);
    }
    try {
      await reload();
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function moveShooterBatch(targetSlug: string, videoPaths: string[]) {
    setBusy(true);
    setError(null);
    try {
      const resp = await api.moveShooter(slug, targetSlug, videoPaths);
      setMoveBlocked(resp.outcome.blocked);
      setLastImportedPaths(null);
      await reload();
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function moveAssignment(
    videoPath: string,
    toStage: number | null,
    role: VideoRole,
  ) {
    setBusy(true);
    setError(null);
    try {
      await api.moveAssignment(slug, videoPath, toStage, role);
      await reload();
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function removeVideo(videoPath: string) {
    setBusy(true);
    setError(null);
    try {
      await api.removeVideo(slug, videoPath, false);
      await reload();
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="relative min-h-screen text-ink"
      style={{
        backgroundImage:
          "radial-gradient(1400px 600px at 50% -100px, rgba(255,45,45,0.04), transparent 60%), linear-gradient(to bottom, var(--color-bg-glow), var(--color-bg))",
        backgroundAttachment: "fixed",
      }}
    >
      <header className="sticky top-0 z-40 border-b border-rule bg-gradient-to-b from-surface to-bg">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 -bottom-px h-px"
          style={{
            background:
              "linear-gradient(to right, transparent, var(--color-led) 18%, var(--color-led) 22%, var(--color-rule-strong) 30%, var(--color-rule-strong) 70%, var(--color-led) 78%, var(--color-led) 82%, transparent)",
            opacity: 0.55,
          }}
        />
        <div className="mx-auto flex max-w-[1240px] items-center gap-6 px-8 py-3.5">
          <Brand variant="compact" />
          <div className="ml-auto inline-flex items-center gap-3 text-[0.8125rem]">
            <span className="text-muted">{health?.project_name ?? ""}</span>
          </div>
        </div>
        <div className="border-t border-rule bg-bg">
          <div className="mx-auto flex max-w-[1240px] items-center gap-3 px-8 py-2.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-subtle">
            <button
              type="button"
              // Replace: same reasoning as MatchShell's breadcrumb --
              // picking a different match would otherwise leave a
              // stale stage URL in history pointing at the wrong
              // project.
              onClick={() => navigate("/pick", { replace: true })}
              className="inline-flex items-center gap-1.5 text-subtle transition-colors hover:text-ink-2"
            >
              <ArrowLeft className="size-3" />
              Matches
            </button>
            <span className="text-whisper">/</span>
            {/* The match crumb returns to THIS match's overview -- not "/"
             *  (app root), which ejected the user out of the match they were
             *  viewing back toward the global picker. */}
            <Link to={href("")} className="text-subtle hover:text-ink-2">
              {health?.project_name ?? "..."}
            </Link>
            <span className="text-whisper">/</span>
            <span className="font-bold text-ink">Add footage</span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1240px] px-8 pb-20 pt-9">
        <div className="mb-6">
          <Kicker className="mb-2.5">
            Ingest &middot; {isEmpty ? "drop state" : "auto-matched"}
          </Kicker>
          <h1 className="mb-2.5 font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
            Add footage
          </h1>
          {/* A1: shooter identity strip -- hides itself for single-shooter */}
          <ShooterChipStrip
            shooters={shooters}
            activeSlug={slug}
            urlBase="ingest"
            label="Adding to"
            count={(s) => String(s.video_count)}
          />
          <p className="max-w-[40rem] text-[0.875rem] text-muted">
            {isEmpty
              ? "Drop a folder of videos. Splitsmith auto-matches each video to a stage by recording timestamp."
              : "Auto-matched to stages by recording timestamp. Review the assignments and confirm to start processing."}
          </p>
        </div>

        {/* Top-level actions. The relink dialog handles the "I moved my
         *  source videos and the project's symlinks are now broken"
         *  JTBD. It scans a folder recursively, matches by basename, and
         *  rewrites the per-video symlinks. Reachable here so the user
         *  doesn't have to dig through Settings or the CLI. */}
        {!isEmpty && (
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setShowRelinkDialog(true)}
            >
              <Folder className="size-3.5" />
              <span className="font-display uppercase tracking-[0.08em]">
                Find moved videos
              </span>
            </Button>
            <span className="text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
              Use this when source files have moved and the project's symlinks are broken.
            </span>
          </div>
        )}

        {showRelinkDialog && (
          <RelinkDialog
            slug={slug}
            onClose={() => setShowRelinkDialog(false)}
            onApplied={() => void reload()}
          />
        )}

        {error && (
          <div className="mb-4 rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
            {error}
          </div>
        )}

        {isEmpty ? (
          <EmptyState
            onPickFolder={() => setShowAddFootage(true)}
            lastScannedDir={lastScannedDir}
          />
        ) : project ? (
          <ReviewState
            slug={slug}
            project={project}
            shooters={shooters}
            lastImportedPaths={lastImportedPaths}
            moveBlocked={moveBlocked}
            onDismissBanner={() => {
              setLastImportedPaths(null);
              setMoveBlocked([]);
            }}
            onMoveShooter={moveShooterBatch}
            onAddMore={() => setShowAddFootage(true)}
            onMoveAssignment={moveAssignment}
            onRemoveVideo={removeVideo}
            onConfirm={() => navigate(href(""), { replace: true })}
            onSaved={reload}
            busy={busy}
            lastScannedDir={lastScannedDir}
            onError={setError}
            beepPending={beepPending}
          />
        ) : null}

        {showAddFootage && (
          <AddFootageModal
            slug={slug}
            initialStorage={storage}
            initialPath={lastScannedDir}
            onClose={() => setShowAddFootage(false)}
            onImported={(imported, paths) => {
              void afterImport(imported, paths);
            }}
            onStorageChange={setStorage}
            shooterName={activeShooterName}
          />
        )}
      </main>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Empty state                                                                */
/* -------------------------------------------------------------------------- */

function EmptyState({
  onPickFolder,
  lastScannedDir,
}: {
  onPickFolder: () => void;
  lastScannedDir: string | null;
}) {
  return (
    <>
      <DropZone onPickFolder={onPickFolder} />
      {lastScannedDir && (
        <RecentSources
          items={[
            {
              path: lastScannedDir,
              label: "Last scanned",
              when: "previously",
            },
          ]}
          onUse={onPickFolder}
        />
      )}
      <TipCards />
    </>
  );
}

function DropZone({ onPickFolder }: { onPickFolder: () => void }) {
  return (
    <div
      className="relative mb-5 overflow-hidden rounded-2xl border-2 border-dashed border-led-deep bg-surface px-10 py-14 text-center transition-all hover:border-led hover:shadow-[0_0_0_1px_var(--color-led),0_0_28px_var(--color-led-glow)]"
      style={{
        backgroundImage:
          "radial-gradient(800px 320px at 50% 30%, rgba(255,45,45,0.10), transparent 65%), linear-gradient(180deg, var(--color-surface) 0%, var(--color-surface-2) 100%)",
      }}
    >
      <span
        aria-hidden
        className="absolute left-[18px] top-[18px] size-20 rounded-tl-[14px] border-t-2 border-l-2 border-led opacity-60"
      />
      <span
        aria-hidden
        className="absolute bottom-[18px] right-[18px] size-20 rounded-br-[14px] border-b-2 border-r-2 border-led opacity-60"
      />
      <div className="mx-auto mb-4 inline-flex size-[72px] items-center justify-center rounded-2xl border border-led-deep bg-led/10 text-led shadow-[0_0_24px_var(--color-led-glow)]">
        <Package className="size-9" strokeWidth={1.6} />
      </div>
      <h2 className="mb-3 font-display text-3xl font-bold uppercase tracking-tight text-ink">
        Drop a folder of videos
      </h2>
      <p className="mx-auto mb-5 max-w-xl text-[0.9375rem] leading-relaxed text-muted">
        Drag and drop your SD-card folder, or pick videos manually.
        Splitsmith will scan for camera-prefixed files (e.g.{" "}
        <code className="rounded border border-rule bg-surface-3 px-1.5 py-0.5 font-mono text-xs text-ink-2">
          GH010032.MP4
        </code>
        ) and group them by camera.
      </p>
      <div className="inline-flex gap-2.5">
        <Button
          onClick={onPickFolder}
          className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink"
        >
          <Folder className="size-3.5" />
          <span className="font-display uppercase tracking-[0.1em]">
            Pick a folder
          </span>
        </Button>
      </div>
      <p className="mt-5 font-mono text-[0.625rem] tabular-nums text-subtle">
        Supported:{" "}
        <code className="rounded border border-rule bg-surface-3 px-1.5 py-0.5 text-[0.6875rem] text-ink-2">
          .mp4
        </code>{" "}
        &middot;{" "}
        <code className="rounded border border-rule bg-surface-3 px-1.5 py-0.5 text-[0.6875rem] text-ink-2">
          .mov
        </code>{" "}
        &middot;{" "}
        <code className="rounded border border-rule bg-surface-3 px-1.5 py-0.5 text-[0.6875rem] text-ink-2">
          .mkv
        </code>{" "}
        &middot;{" "}
        <code className="rounded border border-rule bg-surface-3 px-1.5 py-0.5 text-[0.6875rem] text-ink-2">
          .360
        </code>
      </p>
    </div>
  );
}

function RecentSources({
  items,
  onUse,
}: {
  items: { path: string; label: string; when: string }[];
  onUse: () => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="mb-5 overflow-hidden rounded-xl border border-rule-strong bg-surface">
      <div className="flex items-center justify-between border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-5 py-3.5">
        <span className="font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
          Recent sources
        </span>
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          Drop the same folder again
        </span>
      </div>
      {items.map((it, i) => (
        <div
          key={i}
          className="grid grid-cols-[32px_1fr_120px_100px] items-center gap-3.5 border-t border-rule px-5 py-3 first:border-t-0 hover:bg-surface-2"
        >
          <span className="inline-flex size-8 items-center justify-center rounded-md border border-rule-strong bg-surface-3 text-muted">
            <Folder className="size-3.5" />
          </span>
          <div>
            <div className="truncate font-mono text-[0.8125rem] font-semibold text-ink">
              {it.path}
            </div>
            <div className="mt-1 font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-muted">
              {it.label}
            </div>
          </div>
          <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
            {it.when}
          </span>
          <button
            type="button"
            onClick={onUse}
            className="inline-flex items-center justify-center gap-1.5 rounded-md border border-rule-strong bg-surface-2 px-3 py-2 font-display text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-ink transition-colors hover:border-led hover:bg-led/10 hover:text-led"
          >
            Use this <ArrowRight className="size-3" />
          </button>
        </div>
      ))}
    </div>
  );
}

function TipCards() {
  return (
    <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-3">
      <TipCard
        icon={<Clock className="size-3.5" />}
        title="Use recording timestamps"
        body="Splitsmith reads each video's mtime to suggest the right stage. Don't rename files before import."
      />
      <TipCard
        icon={<Camera className="size-3.5" />}
        title="Filename prefix = camera"
        body="Files starting with GH01, GX01, etc. group into per-camera lanes automatically."
      />
      <TipCard
        icon={<Info className="size-3.5" />}
        title="Detection runs in background"
        body="The jobs drawer shows beep detection per video as soon as you confirm -- keep working while it processes."
      />
    </div>
  );
}

function TipCard({
  icon,
  title,
  body,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <div className="flex gap-3 rounded-xl border border-rule-strong bg-surface p-4">
      <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-led-deep bg-led/10 text-led">
        {icon}
      </span>
      <div className="text-[0.8125rem] leading-relaxed text-ink-2">
        <b className="font-display font-bold uppercase tracking-[0.04em] text-ink">
          {title}.
        </b>{" "}
        {body}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Review state                                                               */
/* -------------------------------------------------------------------------- */

function ReviewState({
  slug,
  project,
  shooters,
  lastImportedPaths,
  moveBlocked,
  onDismissBanner,
  onMoveShooter,
  onAddMore,
  onMoveAssignment,
  onRemoveVideo,
  onConfirm,
  onSaved,
  busy,
  lastScannedDir,
  onError,
  beepPending,
}: {
  slug: string;
  project: MatchProject;
  shooters: ShooterListEntry[];
  lastImportedPaths: string[] | null;
  moveBlocked: MoveShooterBlocked[];
  onDismissBanner: () => void;
  onMoveShooter: (targetSlug: string, videoPaths: string[]) => Promise<void>;
  onAddMore: () => void;
  onMoveAssignment: (
    videoPath: string,
    toStage: number | null,
    role: VideoRole,
  ) => Promise<void>;
  onRemoveVideo: (videoPath: string) => Promise<void>;
  onConfirm: () => void;
  onSaved: () => Promise<void>;
  busy: boolean;
  lastScannedDir: string | null;
  onError: (msg: string | null) => void;
  beepPending: number;
}) {
  const assignedVideos: { video: StageVideo; stage: StageEntry }[] = useMemo(
    () =>
      project.stages.flatMap((s) =>
        (s.videos ?? []).map((video) => ({ video, stage: s })),
      ),
    [project],
  );
  // Capture-time order so videos line up with shooting/stage sequence next
  // to the stage reference. Stable: timestamp-less videos keep their order
  // and sink below timestamped ones. (#ingest-stage-reference)
  const unassignedVideos = useMemo(() => {
    const list = (project.unassigned_videos ?? []).map((v, i) => ({ v, i }));
    list.sort((a, b) => {
      const ta = a.v.match_timestamp;
      const tb = b.v.match_timestamp;
      if (ta && tb) {
        const cmp = ta.localeCompare(tb);
        return cmp !== 0 ? cmp : a.i - b.i;
      }
      if (ta) return -1;
      if (tb) return 1;
      return a.i - b.i;
    });
    return list.map((x) => x.v);
  }, [project]);

  // Camera grouping: by camera_model+camera_mount. Label A/B/C in order.
  const cameras = useMemo(() => groupByCamera(assignedVideos), [assignedVideos]);

  // Total scan summary
  const totalVideos = assignedVideos.length + unassignedVideos.length;
  const willProcess = assignedVideos.filter(
    (v) => v.video.role !== "ignored",
  ).length;
  const ignoredCount = assignedVideos.filter(
    (v) => v.video.role === "ignored",
  ).length + unassignedVideos.filter((v) => v.role === "ignored").length;
  const href = useMatchHref();

  // Name of the current (source) shooter for the banner label.
  const activeShooterName = shooters.find((s) => s.slug === slug)?.name ?? slug;
  // B1: show the banner when there are other shooters and we have a batch.
  const showBanner = lastImportedPaths != null && lastImportedPaths.length > 0 && shooters.length > 1;

  return (
    <>
      <StageReference stages={project.stages} />

      {/* B1: Post-import batch move banner */}
      {showBanner && (
        <IngestMoveBanner
          shooterName={activeShooterName}
          videoPaths={lastImportedPaths}
          shooters={shooters}
          excludeSlug={slug}
          blocked={moveBlocked}
          busy={busy}
          onMove={onMoveShooter}
          onDismiss={onDismissBanner}
        />
      )}
      {/* B1: Blocked-stage notice after a batch move (persists until dismissed) */}
      {moveBlocked.length > 0 && !showBanner && (
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-live/40 bg-live/10 px-4 py-3 text-[0.8125rem]">
          <span className="mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-live font-mono text-xs font-bold text-bg">
            !
          </span>
          <div className="flex-1 font-mono text-[0.6875rem] leading-relaxed text-ink-2">
            <b className="font-display font-bold uppercase tracking-[0.06em] text-live">
              {moveBlocked.length} stage{moveBlocked.length === 1 ? "" : "s"} not moved
            </b>{" "}
            -- the destination already had reviewed footage. Resolve manually.
          </div>
          <button
            type="button"
            onClick={() => onDismissBanner()}
            aria-label="Dismiss"
            className="rounded p-0.5 text-subtle hover:text-ink"
          >
            <X className="size-4" />
          </button>
        </div>
      )}

      <div className="overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
        {/* Drop summary */}
        <div className="relative flex items-center gap-4 border-b border-rule bg-gradient-to-r from-led/10 to-transparent px-6 py-4">
          <span
            aria-hidden
            className="absolute inset-y-0 left-0 w-0.5 bg-led shadow-[0_0_12px_var(--color-led-glow)]"
          />
          <span className="inline-flex size-11 items-center justify-center rounded-[10px] bg-led-fill text-ink shadow-[0_0_16px_var(--color-led-glow)]">
            <Package className="size-5" strokeWidth={2.2} />
          </span>
          <div className="flex-1">
            <div className="font-display text-[0.9375rem] font-bold uppercase tracking-[0.04em] text-ink tabular-nums">
              <b className="text-led">{totalVideos}</b>{" "}
              {totalVideos === 1 ? "video" : "videos"} detected &middot;{" "}
              <b className="text-led">{cameras.length}</b>{" "}
              {cameras.length === 1 ? "camera" : "cameras"} inferred
            </div>
            {lastScannedDir && (
              <div className="mt-1 truncate font-mono text-[0.6875rem] tracking-[0.04em] text-muted">
                {lastScannedDir}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={onAddMore}
            className="inline-flex items-center gap-1.5 rounded-md border border-rule-strong bg-surface-2 px-3.5 py-2 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-ink transition-colors hover:border-ink-2 hover:bg-surface-3"
          >
            <Plus className="size-3" /> Add more
          </button>
        </div>

        {/* Beep review CTA. Auto-detection runs after ingest and parks
            uncertain beeps on /beep-review for confirm/adjust. Surfacing
            it from the videos page is the missing handoff -- without
            this row the user lands on Audit and sees the chip warn
            "likely wrong" with nowhere obvious to fix it.
            Tone is beep-cyan, not amber: this is positive pending work,
            not a warning. Amber would say "something is wrong"; cyan
            says "almost-done work waits for you". */}
        {beepPending > 0 && (
          <Link
            to={href("beep-review")}
            className="flex items-center gap-3.5 border-b border-rule bg-gradient-to-r from-beep/10 to-transparent px-6 py-3 font-mono text-[0.75rem] uppercase tracking-[0.06em] text-ink-2 transition-colors hover:bg-beep/15"
          >
            <span className="inline-flex size-7 items-center justify-center rounded-full border border-beep/40 bg-beep-tint text-beep shadow-[0_0_10px_var(--color-beep-glow)]">
              <Video className="size-3.5" />
            </span>
            <span className="flex-1">
              <b className="font-bold text-beep">{beepPending}</b>{" "}
              beep{beepPending === 1 ? "" : "s"} need
              {beepPending === 1 ? "s" : ""} confirmation &middot;{" "}
              <span className="text-muted">
                detect found candidates but isn't sure
              </span>
            </span>
            <span className="inline-flex items-center gap-1.5 font-display text-[0.6875rem] font-bold uppercase tracking-[0.1em] text-beep">
              Review beeps <ArrowRight className="size-3" />
            </span>
          </Link>
        )}

        {/* Cameras */}
        {cameras.length > 0 && (
          <div className="border-b border-rule">
            <div className="flex items-center justify-between border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-5 py-3.5">
              <div className="font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
                Cameras{" "}
                <span className="ml-2 font-mono text-[0.625rem] font-medium tracking-[0.04em] text-muted">
                  detected from probe metadata
                </span>
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3.5 p-5 md:grid-cols-2">
              {cameras.map((cam) => (
                <CameraCard key={cam.id} camera={cam} slug={slug} onSaved={onSaved} />
              ))}
            </div>
          </div>
        )}

        {/* Stage assignments */}
        <div>
          <div className="flex items-center justify-between border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-5 py-3.5">
            <div className="font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
              Stage assignments{" "}
              <span className="ml-2 font-mono text-[0.625rem] font-medium tracking-[0.04em] text-muted">
                auto-matched by timestamp &middot; review and confirm
              </span>
            </div>
          </div>

          {project.stages.map((stage) => {
            const videos = stage.videos ?? [];
            if (videos.length === 0) return null;
            return (
              <StageBlock
                key={stage.stage_number}
                slug={slug}
                stage={stage}
                allStages={project.stages}
                videos={videos}
                cameras={cameras}
                shooters={shooters}
                onMove={onMoveAssignment}
                onRemove={onRemoveVideo}
                onMoveShooter={onMoveShooter}
                busy={busy}
                onError={onError}
              />
            );
          })}

          {unassignedVideos.length > 0 && (
            <UnassignedBlock
              slug={slug}
              videos={unassignedVideos}
              allStages={project.stages}
              cameras={cameras}
              shooters={shooters}
              onMove={onMoveAssignment}
              onRemove={onRemoveVideo}
              onMoveShooter={onMoveShooter}
              busy={busy}
              onError={onError}
            />
          )}
        </div>

        {/* Footer */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-rule bg-surface px-6 py-4">
          <div className="font-mono text-[0.75rem] uppercase tracking-[0.06em] text-muted tabular-nums">
            <b className="font-bold text-ink">{totalVideos}</b> videos &middot;{" "}
            <b className="font-bold text-ink">{willProcess}</b> will process{" "}
            {ignoredCount > 0 && (
              <>
                &middot;{" "}
                <b className="font-bold text-ink">{ignoredCount}</b> ignored
              </>
            )}
          </div>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={busy || willProcess === 0}
            className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink"
          >
            <span className="font-display uppercase tracking-[0.08em]">
              Confirm &amp; start processing
            </span>
            <ArrowRight className="size-3.5" />
          </Button>
        </div>
      </div>

      <div className="mt-5 rounded-xl border border-dashed border-live/40 bg-live/10 px-5 py-4 font-mono text-[0.75rem] leading-relaxed tracking-[0.04em] text-ink-2">
        <b className="block font-display font-bold uppercase tracking-[0.1em] text-live">
          What happens after confirm
        </b>
        Background jobs queue immediately (audio extract + beep detect per
        video). Stages with confirmed beeps become available for audit.
        Continue to <Link to={href("")} className="text-led hover:text-led-soft">match overview</Link>{" "}
        or open the <Link to={href("audit", slug)} className="text-led hover:text-led-soft">audit</Link>{" "}
        page to start reviewing.
      </div>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Stage block                                                                */
/* -------------------------------------------------------------------------- */

interface CameraGroup {
  id: string;
  label: string;
  make: string | null;
  model: string | null;
  mount: CameraMount | null;
  videoCount: number;
  videoPaths: Set<string>;
  members: BulkCameraSetItem[];
}

function groupByCamera(
  assigned: { video: StageVideo; stage: StageEntry }[],
): CameraGroup[] {
  const map = new Map<string, CameraGroup>();
  for (const { video, stage } of assigned) {
    const key = `${video.camera_make ?? ""}|${video.camera_model ?? ""}|${video.camera_mount ?? ""}`;
    let g = map.get(key);
    if (!g) {
      g = {
        id: key,
        label: "",
        make: video.camera_make,
        model: video.camera_model,
        mount: video.camera_mount,
        videoCount: 0,
        videoPaths: new Set(),
        members: [],
      };
      map.set(key, g);
    }
    g.videoCount += 1;
    g.videoPaths.add(video.path);
    g.members.push({ stage_number: stage.stage_number, video_id: video.video_id });
  }
  const groups = Array.from(map.values());
  groups.forEach((g, i) => {
    g.label = `Camera ${String.fromCharCode(65 + i)}`;
  });
  return groups;
}

function StageBlock({
  slug,
  stage,
  allStages,
  videos,
  cameras,
  shooters,
  onMove,
  onRemove,
  onMoveShooter,
  busy,
  onError,
}: {
  slug: string;
  stage: StageEntry;
  allStages: StageEntry[];
  videos: StageVideo[];
  cameras: CameraGroup[];
  shooters: ShooterListEntry[];
  onMove: (
    videoPath: string,
    toStage: number | null,
    role: VideoRole,
  ) => Promise<void>;
  onRemove: (videoPath: string) => Promise<void>;
  onMoveShooter: (targetSlug: string, videoPaths: string[]) => Promise<void>;
  busy: boolean;
  onError: (msg: string | null) => void;
}) {
  const primaryCount = videos.filter((v) => v.role === "primary").length;
  const status: "ok" | "warn" =
    primaryCount === 0 ? "warn" : "ok";
  return (
    <div className="border-b border-rule last:border-b-0">
      <div className="flex flex-wrap items-center gap-3 border-b border-rule bg-surface-2 px-5 py-3">
        <span className="inline-flex h-7 w-8 items-center justify-center rounded-md border border-led-deep bg-led/10 font-mono text-xs font-bold tabular-nums text-led">
          {pad2(stage.stage_number)}
        </span>
        <span className="font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
          {stage.stage_name}
        </span>
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted">
          {videos.length} video{videos.length === 1 ? "" : "s"}
        </span>
        <span
          className={cn(
            "ml-auto inline-flex items-center gap-1.5 rounded border px-2 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em]",
            status === "ok"
              ? "border-done/40 bg-done/10 text-done"
              : "border-live/40 bg-live/10 text-live",
          )}
        >
          <span
            aria-hidden
            className={cn(
              "inline-block size-1 rounded-full",
              status === "ok"
                ? "bg-done shadow-[0_0_5px_var(--color-done-glow)]"
                : "bg-live shadow-[0_0_5px_var(--color-live-glow)]",
            )}
          />
          {status === "ok" ? "Primary set" : "No primary"}
        </span>
      </div>
      {videos.map((v) => (
        <VideoRow
          key={v.video_id}
          slug={slug}
          video={v}
          camera={cameras.find((c) => c.videoPaths.has(v.path))}
          currentStage={stage.stage_number}
          allStages={allStages}
          shooters={shooters}
          onMove={onMove}
          onRemove={onRemove}
          onMoveShooter={onMoveShooter}
          busy={busy}
          onError={onError}
        />
      ))}
    </div>
  );
}

function UnassignedBlock({
  slug,
  videos,
  allStages,
  cameras,
  shooters,
  onMove,
  onRemove,
  onMoveShooter,
  busy,
  onError,
}: {
  slug: string;
  videos: StageVideo[];
  allStages: StageEntry[];
  cameras: CameraGroup[];
  shooters: ShooterListEntry[];
  onMove: (
    videoPath: string,
    toStage: number | null,
    role: VideoRole,
  ) => Promise<void>;
  onRemove: (videoPath: string) => Promise<void>;
  onMoveShooter: (targetSlug: string, videoPaths: string[]) => Promise<void>;
  busy: boolean;
  onError: (msg: string | null) => void;
}) {
  return (
    <div className="border-y border-live/30 bg-gradient-to-r from-live/[0.06] to-transparent">
      <div className="flex items-center gap-3.5 px-5 py-3.5">
        <span className="inline-flex size-8 items-center justify-center rounded-md bg-live font-mono font-bold text-bg shadow-[0_0_12px_var(--color-live-glow)]">
          !
        </span>
        <div>
          <div className="font-display text-sm font-bold uppercase tracking-[0.06em] text-live">
            Unassigned &middot; {videos.length} video{videos.length === 1 ? "" : "s"}
          </div>
          <div className="mt-1 font-mono text-[0.6875rem] tracking-[0.04em] text-muted">
            No stage matched by timestamp -- likely pre/post-match footage.
            Assign manually or leave ignored.
          </div>
        </div>
      </div>
      {videos.map((v) => (
        <VideoRow
          key={v.video_id}
          slug={slug}
          video={v}
          camera={cameras.find((c) => c.videoPaths.has(v.path))}
          currentStage={null}
          allStages={allStages}
          shooters={shooters}
          onMove={onMove}
          onRemove={onRemove}
          onMoveShooter={onMoveShooter}
          busy={busy}
          onError={onError}
        />
      ))}
    </div>
  );
}

function VideoRow({
  slug,
  video,
  camera,
  currentStage,
  allStages,
  shooters,
  onMove,
  onRemove,
  onMoveShooter,
  busy,
  onError,
}: {
  slug: string;
  video: StageVideo;
  camera?: CameraGroup;
  currentStage: number | null;
  allStages: StageEntry[];
  shooters: ShooterListEntry[];
  onMove: (
    videoPath: string,
    toStage: number | null,
    role: VideoRole,
  ) => Promise<void>;
  onRemove: (videoPath: string) => Promise<void>;
  onMoveShooter: (targetSlug: string, videoPaths: string[]) => Promise<void>;
  busy: boolean;
  onError: (msg: string | null) => void;
}) {
  const [detecting, setDetecting] = useState(false);
  // Local per-row pending: ``onMove`` (the page-level moveAssignment)
  // awaits the move + a full project reload before resolving, so this
  // spans the whole operation and gives the row a spinner instead of the
  // silent input-freeze the page-level ``busy`` produced on its own.
  const [rowBusy, setRowBusy] = useState(false);
  // Inline preview: the leading icon toggles a scrubbable <video>. For
  // timestamp-less footage the operator can't tell which stage it is
  // without watching, and the stage dropdown lives on this same row --
  // so watch-and-assign stays one motion. Lazy: the <video> only mounts
  // (and only then streams) while open.
  const [previewOpen, setPreviewOpen] = useState(false);
  // B2: kebab overflow menu state.
  const [kebabOpen, setKebabOpen] = useState(false);
  const kebabRef = useRef<HTMLDivElement>(null);
  const hasOtherShooters = shooters.length > 1;

  // Close kebab on outside click.
  useEffect(() => {
    if (!kebabOpen) return;
    function onOutside(e: MouseEvent) {
      if (kebabRef.current && !kebabRef.current.contains(e.target as Node)) {
        setKebabOpen(false);
      }
    }
    document.addEventListener("mousedown", onOutside);
    return () => document.removeEventListener("mousedown", onOutside);
  }, [kebabOpen]);
  const needsBeep =
    video.role !== "ignored" &&
    video.beep_time == null &&
    currentStage != null;

  async function detectBeep() {
    if (currentStage == null) return;
    setDetecting(true);
    onError(null);
    try {
      // Server-side dedupe: if a detect_beep job is already in flight for
      // this video, ``_submit_detect_beep`` returns the existing job
      // without spawning a parallel one. jobs rail surfaces progress;
      // we leave the user here.
      await api.detectBeepForVideo(slug, currentStage, video.video_id);
    } catch (e) {
      onError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setDetecting(false);
    }
  }
  const filename = video.path.split("/").pop() ?? video.path;
  const cameraLabel = camera?.label ?? "Camera";
  const cameraDetail = [
    camera?.model ?? null,
    camera?.mount ?? null,
  ]
    .filter(Boolean)
    .join(" · ");
  const recordedAt =
    video.match_timestamp &&
    new Date(video.match_timestamp).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });

  async function setRole(next: VideoRole) {
    setRowBusy(true);
    try {
      await onMove(video.path, currentStage, next);
    } finally {
      setRowBusy(false);
    }
  }

  async function changeStage(next: string) {
    setRowBusy(true);
    try {
      if (next === "unassigned") {
        await onMove(video.path, null, video.role);
      } else {
        const n = Number(next);
        if (!Number.isNaN(n)) await onMove(video.path, n, video.role);
      }
    } finally {
      setRowBusy(false);
    }
  }

  return (
    <div className="border-b border-rule last:border-b-0">
      <div
        className={cn(
          "grid items-center gap-3.5 px-5 py-2.5 hover:bg-surface-2",
          hasOtherShooters
            ? "grid-cols-[36px_minmax(0,1.6fr)_120px_180px_220px_minmax(160px,auto)_36px_36px]"
            : "grid-cols-[36px_minmax(0,1.6fr)_120px_180px_220px_minmax(160px,auto)_36px]",
        )}
      >
      <button
        type="button"
        onClick={() => setPreviewOpen((o) => !o)}
        title={previewOpen ? "Hide preview" : "Preview video"}
        aria-label={previewOpen ? "Hide preview" : "Preview video"}
        aria-expanded={previewOpen}
        className="group inline-flex size-8 items-center justify-center rounded-md border border-rule-strong text-ink-2 transition-colors hover:border-led-deep hover:text-led"
        style={{
          background:
            camera && camera.id.includes("|")
              ? "linear-gradient(135deg, var(--color-surface-3), var(--color-surface-4))"
              : "var(--color-surface-3)",
        }}
      >
        {previewOpen ? (
          <ChevronUp className="size-3.5" />
        ) : (
          <Play className="size-3.5 transition-transform group-hover:scale-110" />
        )}
      </button>
      <div className="min-w-0">
        <div className="truncate font-mono text-[0.75rem] font-semibold text-ink">
          {filename}
        </div>
        <div className="mt-0.5 font-mono text-[0.5625rem] uppercase tracking-[0.06em] text-muted">
          {cameraLabel}
          {cameraDetail && <> &middot; {cameraDetail}</>}
        </div>
      </div>
      <div className="font-mono text-[0.6875rem] tabular-nums text-muted">
        {recordedAt ?? "no timestamp"}
      </div>
      <div className="relative">
        <select
          value={currentStage === null ? "unassigned" : String(currentStage)}
          onChange={(e) => void changeStage(e.target.value)}
          disabled={busy}
          className="min-h-9 w-full rounded-md border border-rule bg-surface-3 px-3 py-1.5 pr-8 font-mono text-[0.6875rem] text-ink outline-none focus:border-led focus:shadow-[0_0_0_2px_var(--color-led-tint)]"
        >
          <option value="unassigned">-- Unassigned --</option>
          {allStages.map((s) => (
            <option key={s.stage_number} value={s.stage_number}>
              Stage {pad2(s.stage_number)} -- {s.stage_name}
            </option>
          ))}
        </select>
        {rowBusy && (
          <Loader2
            aria-label="Saving assignment"
            className="pointer-events-none absolute right-2 top-1/2 size-3.5 -translate-y-1/2 animate-spin text-led"
          />
        )}
      </div>
      {currentStage === null ? (
        <span
          className="font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-muted"
          title="A video needs a stage before it can have a role. The first video assigned to a stage becomes its primary automatically."
        >
          pick a stage &rarr; auto-primary
        </span>
      ) : (
        <RoleToggles
          value={video.role}
          onChange={(r) => void setRole(r)}
          disabled={busy || rowBusy}
        />
      )}
      {/* Beep status + manual retry. Auto-queue fires at scan / move /
       *  swap-primary time; if a job never ran or failed silently, the
       *  user needs an explicit affordance to kick detection. Clicking
       *  hits ``detectBeepForVideo``; the server dedupes against an
       *  in-flight job so double-clicks are safe. jobs rail surfaces
       *  progress; on success the row re-renders via project reload. */}
      <div className="flex items-center justify-end gap-2">
        {video.beep_time != null ? (
          <span className="inline-flex items-center gap-1.5 rounded border border-beep/40 bg-beep-tint px-2 py-0.5 font-mono text-[0.625rem] font-bold tabular-nums text-beep">
            beep {video.beep_time.toFixed(2)}s
          </span>
        ) : video.role === "ignored" ? (
          <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            ignored
          </span>
        ) : null}
        {needsBeep ? (
          <button
            type="button"
            onClick={() => void detectBeep()}
            disabled={busy || detecting}
            title="Detect beep on this video"
            className="inline-flex items-center gap-1.5 rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 font-display text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-ink-2 transition-colors hover:border-led-deep hover:bg-led-tint hover:text-led disabled:opacity-50"
          >
            {detecting ? "Queuing..." : "Detect beep"}
          </button>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => void onRemove(video.path)}
        disabled={busy}
        title="Remove video"
        aria-label="Remove video"
        className="inline-flex size-8 items-center justify-center rounded-md text-subtle transition-colors hover:bg-led/10 hover:text-led"
      >
        <XCircle className="size-4" />
      </button>
      {/* B2: kebab overflow menu -- only shown for multi-shooter matches */}
      {hasOtherShooters && (
        <div ref={kebabRef} className="relative">
          <button
            type="button"
            onClick={() => setKebabOpen((o) => !o)}
            disabled={busy || rowBusy}
            title="More actions"
            aria-label="More actions"
            aria-expanded={kebabOpen}
            className="inline-flex size-8 items-center justify-center rounded-md text-subtle transition-colors hover:bg-surface-2 hover:text-ink-2 disabled:opacity-50"
          >
            <MoreVertical className="size-4" />
          </button>
          {kebabOpen && (
            <div className="absolute right-0 top-full z-20 mt-1 w-48 overflow-hidden rounded-lg border border-rule-strong bg-surface shadow-[0_8px_24px_-4px_rgba(0,0,0,0.5)]">
              <div className="border-b border-rule px-3 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-subtle">
                Move to shooter
              </div>
              <div className="p-2">
                <ShooterPickerPopover
                  shooters={shooters}
                  excludeSlug={slug}
                  busy={busy || rowBusy}
                  onPick={async (targetSlug) => {
                    setKebabOpen(false);
                    setRowBusy(true);
                    onError(null);
                    try {
                      await onMoveShooter(targetSlug, [video.path]);
                    } finally {
                      setRowBusy(false);
                    }
                  }}
                />
              </div>
            </div>
          )}
        </div>
      )}
      </div>
      {previewOpen && (
        <div className="border-t border-rule/60 bg-bg px-5 py-3">
          {/* source footage has no caption track */}
          <video
            controls
            preload="metadata"
            src={api.shooterVideoStreamUrl(slug, video.path)}
            className="max-h-[420px] w-full rounded-md border border-rule bg-black"
          />
          <p className="mt-1.5 font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-subtle">
            Streaming source &middot; scrub to identify the stage
          </p>
        </div>
      )}
    </div>
  );
}

function RoleToggles({
  value,
  onChange,
  disabled,
}: {
  value: VideoRole;
  onChange: (r: VideoRole) => void;
  disabled?: boolean;
}) {
  const opts: { v: VideoRole; label: string }[] = [
    { v: "primary", label: "Primary" },
    { v: "secondary", label: "Secondary" },
    { v: "ignored", label: "Ignore" },
  ];
  return (
    <div className="inline-flex gap-0.5 rounded-md border border-rule bg-surface-2 p-0.5">
      {opts.map((o) => {
        const on = value === o.v;
        return (
          <button
            key={o.v}
            type="button"
            onClick={() => onChange(o.v)}
            disabled={disabled}
            className={cn(
              "rounded px-2.5 py-1 font-display text-[0.625rem] font-semibold uppercase tracking-[0.06em] transition-all",
              on && o.v === "primary" && "border border-led-deep bg-led/10 text-led",
              on && o.v === "secondary" && "bg-surface-4 text-ink",
              on && o.v === "ignored" && "bg-surface-4 text-muted line-through",
              !on && "text-muted hover:text-ink",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

function CameraCard({
  camera,
  slug,
  onSaved,
}: {
  camera: CameraGroup;
  slug: string;
  onSaved: () => Promise<void>;
}) {
  const [models, setModels] = useState<CalibratedCameraModel[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getCalibratedCameraModels()
      .then((resp) => {
        if (!cancelled) setModels(resp.models);
      })
      .catch(() => {
        if (!cancelled) setModels([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function applyMount(value: string) {
    const mount = value === "" ? null : (value as CameraMount);
    setBusy(true);
    setError(null);
    try {
      await api.bulkSetCamera(slug, {
        items: camera.members,
        set_mount: true,
        mount,
      });
      await onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function applyModel(value: string) {
    const OTHER_VALUE = "__other__";
    let make: string | null = null;
    let model: string | null = null;
    if (value !== OTHER_VALUE && models) {
      const found = models.find((m) => m.key === value);
      if (found) {
        make = found.make;
        model = found.model;
      }
    }
    setBusy(true);
    setError(null);
    try {
      await api.bulkSetCamera(slug, {
        items: camera.members,
        set_model: true,
        make,
        model,
      });
      await onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const OTHER_VALUE = "__other__";
  const currentModelKey =
    camera.make && camera.model
      ? `${camera.make.trim().toLowerCase().split(/\s+/).join(" ")} ${camera.model.trim().toLowerCase().split(/\s+/).join(" ")}`
      : null;
  const modelSelectValue =
    currentModelKey && models?.some((m) => m.key === currentModelKey)
      ? currentModelKey
      : OTHER_VALUE;

  return (
    <div className="rounded-xl border border-rule bg-bg-glow px-4 py-3.5">
      <div className="flex items-center gap-3.5">
        <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-[9px] border border-rule-strong bg-surface-3 text-ink-2">
          <Camera className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="mb-1 inline-flex items-center gap-2.5">
            <span className="font-display text-sm font-bold uppercase tracking-[0.04em] text-ink">
              {camera.label}
            </span>
            {camera.mount && (
              <span className="rounded border border-rule-strong bg-surface-3 px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.1em] text-ink-2">
                {camera.mount}
              </span>
            )}
          </div>
          <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            {camera.videoCount} file{camera.videoCount === 1 ? "" : "s"}
            {(camera.make || camera.model) && (
              <>
                {" "}
                <span className="text-whisper">&middot;</span>{" "}
                {[camera.make, camera.model].filter(Boolean).join(" ")}
              </>
            )}
          </div>
        </div>
      </div>

      {/* Edit controls */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-muted">
          Mount
        </span>
        <select
          value={camera.mount ?? ""}
          disabled={busy}
          title="Camera mount -- routes these videos through the matching ensemble threshold class (handheld vs headcam)"
          className="rounded-md border border-rule bg-surface-3 px-2 py-1 font-mono text-[0.6875rem] text-ink outline-none focus:border-led focus:shadow-[0_0_0_2px_var(--color-led-tint)] disabled:opacity-50"
          onChange={(e) => void applyMount(e.target.value)}
        >
          <option value="">(auto)</option>
          {CAMERA_MOUNTS.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>

        <span className="font-mono text-[0.5625rem] uppercase tracking-[0.08em] text-muted">
          Model
        </span>
        <select
          value={modelSelectValue}
          disabled={busy || models === null}
          title="Camera model -- routes these videos through the matching per-model amplitude floor"
          className="rounded-md border border-rule bg-surface-3 px-2 py-1 font-mono text-[0.6875rem] text-ink outline-none focus:border-led focus:shadow-[0_0_0_2px_var(--color-led-tint)] disabled:opacity-50"
          onChange={(e) => void applyModel(e.target.value)}
        >
          <option value={OTHER_VALUE}>Other (generic headcam)</option>
          {(models ?? []).map((m) => (
            <option key={m.key} value={m.key}>
              {m.make} {m.model}
            </option>
          ))}
        </select>

        {busy && (
          <Loader2 className="size-3.5 animate-spin text-led" aria-label="Saving" />
        )}
      </div>

      {error && (
        <div className="mt-2 font-mono text-[0.5625rem] text-led">
          {error}
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* B1 -- Post-import batch move banner                                        */
/* -------------------------------------------------------------------------- */

function IngestMoveBanner({
  shooterName,
  videoPaths,
  shooters,
  excludeSlug,
  blocked,
  busy,
  onMove,
  onDismiss,
}: {
  shooterName: string;
  videoPaths: string[];
  shooters: ShooterListEntry[];
  excludeSlug: string;
  blocked: MoveShooterBlocked[];
  busy: boolean;
  onMove: (targetSlug: string, paths: string[]) => Promise<void>;
  onDismiss: () => void;
}) {
  return (
    <div className="mb-4 overflow-hidden rounded-xl border border-beep/40 bg-beep-tint">
      <div className="relative flex flex-wrap items-center gap-3 px-4 py-3">
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-0.5 bg-beep shadow-[0_0_8px_var(--color-beep-glow)]"
        />
        <span className="font-mono text-[0.75rem] text-ink-2">
          <b className="font-bold text-beep">{videoPaths.length}</b>{" "}
          video{videoPaths.length === 1 ? "" : "s"} added to{" "}
          <b className="text-ink">{shooterName}</b>.{" "}
          <span className="text-muted">Wrong shooter?</span>
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted">
            Move all to
          </span>
          <ShooterPickerPopover
            shooters={shooters}
            excludeSlug={excludeSlug}
            busy={busy}
            onPick={(targetSlug) => void onMove(targetSlug, videoPaths)}
          />
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss banner"
          className="ml-auto rounded p-0.5 text-subtle hover:text-ink"
        >
          <X className="size-4" />
        </button>
      </div>
      {blocked.length > 0 && (
        <div className="border-t border-beep/20 bg-live/10 px-4 py-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-live">
          {blocked.length} stage{blocked.length === 1 ? "" : "s"} already had reviewed footage
          -- not moved. Resolve manually.
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}
