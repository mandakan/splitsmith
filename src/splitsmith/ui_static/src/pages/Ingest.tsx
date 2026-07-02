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
  Clock,
  Folder,
  Info,
  Package,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { AddFootageModal } from "@/components/AddFootageModal";
import { RelinkDialog } from "@/components/RelinkDialog";
import { ShooterChipStrip } from "@/components/match/ShooterChipStrip";
import { Brand, Kicker } from "@/components/ui";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  api,
  type MatchProject,
  type MoveShooterBlocked,
  type ServerHealth,
  type ShooterListEntry,
  type VideoRole,
} from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";
import { ReviewLayout } from "@/pages/ingest/ReviewLayout";

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
  // "To assign" queue in the ReviewLayout clip list). Without this the
  // page sits on the EmptyState placeholder and reads as "nothing
  // happened" after the modal closes.
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
  ): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await api.moveAssignment(slug, videoPath, toStage, role);
      await reload();
      return true;
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
      return false;
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
      <header className="sticky top-0 z-chrome border-b border-rule bg-gradient-to-b from-surface to-bg">
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
          <ReviewLayout
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
