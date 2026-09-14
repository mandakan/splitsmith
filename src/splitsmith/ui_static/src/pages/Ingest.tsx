/**
 * Footage (/match/:id/ingest/:slug) -- the Prepare phase in one screen
 * (spec 2026-09-13 s4.3, UX PR 6): every stage-by-shooter cell with its
 * files and the primary's beep state, the videos not yet placed, the
 * shooters, the cameras. Add footage is the one primary and the drop
 * zone is always present, so adding more never needs another screen.
 *
 * Two deployment modes feed the same page: local mode opens the
 * FolderPicker (scan, link-in-place or copy), hosted mode the upload
 * modal and the window-level drop (files land in object storage and
 * attach through the upload dock). Assign / role / remove are applied
 * optimistically and serialised on one write chain (the backend saves
 * the project doc under optimistic version locking). The clip's detail
 * opens in ClipSheet; shooters are added through AddShooterSheet.
 */
import { Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useOutletContext, useParams } from "react-router-dom";

import { FolderPicker, type FolderPickerCommitFile } from "@/components/FolderPicker";
import { AddShooterSheet } from "@/components/footage/AddShooterSheet";
import { CamerasPanel } from "@/components/footage/CamerasPanel";
import { ClipSheet } from "@/components/footage/ClipSheet";
import { CoverageMatrix, type FootageHrefs } from "@/components/footage/CoverageMatrix";
import { FootageCards } from "@/components/footage/FootageCards";
import { ShootersPanel } from "@/components/footage/ShootersPanel";
import { UnassignedPanel } from "@/components/footage/UnassignedPanel";
import { HostedUploadModal } from "@/components/HostedUploadModal";
import { IngestMoveBanner } from "@/components/ingest/IngestMoveBanner";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { RelinkDialog } from "@/components/RelinkDialog";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Portal } from "@/components/ui/Portal";
import { useConfirm } from "@/components/useConfirm";
import {
  ApiError,
  api,
  capabilityDenied,
  READ_ONLY_MIRROR_MESSAGE,
  type MatchProject,
  type MoveShooterBlocked,
  type ShooterListEntry,
  type StageVideo,
  type VideoRole,
} from "@/lib/api";
import { useWindowFileDrag } from "@/lib/dragDepth";
import { useDeploymentMode } from "@/lib/features";
import { buildFootageRows, footageStats, unassignedVideos, type UnassignedItem } from "@/lib/footage";
import { pickDefaultShooterSlug } from "@/lib/defaultShooter";
import { useMatchHref } from "@/lib/matchHref";
import { useUploads } from "@/lib/uploads";
import { useIsMobile } from "@/lib/useIsMobile";
import { applyAssignmentLocally, buildClipModel, removeVideoLocally, type ClipItem } from "@/pages/ingest/model";

type StorageMode = "symlink" | "copy";

export function Ingest() {
  const { slug } = useParams<{ slug: string }>();
  if (!slug) return <FootageEntry />;
  return <IngestInner key={slug} slug={slug} />;
}

/** The slug-less ``/ingest`` route: the default shooter's Footage, or,
 *  on a match with no shooter yet, the page reduced to Add shooter --
 *  the one place a first shooter gets created (UX PR 6). */
function FootageEntry() {
  const outletCtx = useOutletContext<MatchShellOutletContext | undefined>();
  const href = useMatchHref();
  const shooters = outletCtx?.shooters ?? [];
  const editDenied = capabilityDenied(outletCtx?.capabilities, "edit");
  const [open, setOpen] = useState(false);
  const slug = pickDefaultShooterSlug(shooters);
  if (slug) return <Navigate to={href("ingest", slug)} replace />;
  if (!outletCtx?.project && shooters.length === 0 && outletCtx?.shooters == null) {
    return <p className="px-7 py-10 text-md text-muted">Reading match state...</p>;
  }
  return (
    <div className="px-4 py-4 md:px-7 md:py-5">
      <PageHeader
        title="Footage"
        sub="No shooters yet"
        actions={
          <Button variant="primary" onClick={() => setOpen(true)} disabled={editDenied}>
            Add shooter
          </Button>
        }
      />
      <p className="max-w-[52ch] text-md text-muted">
        Add the first shooter, then drop their camera files here; each one is matched to its stage by recording time.
      </p>
      <AddShooterSheet
        open={open}
        onClose={() => setOpen(false)}
        project={outletCtx?.project ?? null}
        shooters={shooters}
        editDenied={editDenied}
        onChanged={() => {
          setOpen(false);
          outletCtx?.refresh();
        }}
      />
    </div>
  );
}

function IngestInner({ slug }: { slug: string }) {
  const href = useMatchHref();
  const isMobile = useIsMobile();
  const confirm = useConfirm();
  // Relink rewrites on-disk raw/ symlinks, a local-filesystem concept.
  // In hosted mode the container FS is ephemeral and sources live in object
  // storage, so the "Find moved videos" affordance is meaningless there.
  const { mode, resolved: modeResolved } = useDeploymentMode();
  const [project, setProject] = useState<MatchProject | null>(null);
  const [error, setError] = useState<string | null>(null);
  // #756: mirrors (and any future non-editable match) get a read-only
  // Ingest - the page's whole surface is edit-class writes. Disable
  // with the banner's reason rather than hide: an Ingest page with no
  // controls at all would read as broken (the issue's per-surface rule).
  const editDenied = capabilityDenied(project?.capabilities, "edit");
  // Default storage mode for the ingest modal. Rendered as a toggle in
  // the FolderPicker footer (add-footage call site only).
  const [storage, setStorage] = useState<StorageMode>("symlink");
  const [showAddFootage, setShowAddFootage] = useState(false);
  const [showRelinkDialog, setShowRelinkDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [lastScannedDir, setLastScannedDir] = useState<string | null>(null);
  // The shooter list: the shell's when mounted under MatchShell, else our
  // own fetch (the page also refetches after a move).
  const outletCtx = useOutletContext<MatchShellOutletContext | undefined>();
  const [ownShooters, setOwnShooters] = useState<ShooterListEntry[]>([]);
  const shooters = outletCtx?.shooters?.length ? outletCtx.shooters : ownShooters;
  const jobs = useMemo(() => outletCtx?.jobs ?? [], [outletCtx?.jobs]);
  // B1: Paths from the most recent import batch. Cleared on banner dismiss
  // or after a successful move. Not persisted across reloads.
  const [lastImportedPaths, setLastImportedPaths] = useState<string[] | null>(null);
  // B1: Blocked stages surfaced after a move attempt.
  const [moveBlocked, setMoveBlocked] = useState<MoveShooterBlocked[]>([]);
  // Stage assignments are applied optimistically (instant UI) but their POSTs
  // are serialized: the backend saves the project doc under optimistic version
  // locking, so overlapping writes would 409. moveChain threads each write
  // after the previous one; inflight tracks the burst so we only reconcile with
  // the authoritative server doc once it drains (a mid-burst response predates
  // the later optimistic moves and would drop them).
  const moveChain = useRef<Promise<void>>(Promise.resolve());
  const inflight = useRef(0);

  async function reload() {
    setError(null);
    try {
      const p = await api.getProject(slug);
      setProject(p);
      if (p.last_scanned_dir) setLastScannedDir(p.last_scanned_dir);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
    // A1: Shooter list. Errors here are silent -- the strip just hides.
    try {
      const r = await api.listMatchShooters();
      setOwnShooters(r.shooters);
    } catch {
      // non-fatal; leave existing list
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  // Background uploads auto-attach in the provider and bump attachTick;
  // reload the tray so freshly landed videos appear even after the
  // upload sheet has closed, while this page stays mounted.
  const { attachTick, enqueue } = useUploads();
  useEffect(() => {
    if (attachTick === 0) return;
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachTick]);

  // Hosted mode: the whole Ingest page is a drop target. Window-level
  // dragenter/dragleave with a depth counter drives the full-page
  // overlay; a drop anywhere enqueues into the background upload queue.
  // DropGuard (App root) preventDefaults the same event so the browser
  // never navigates; it does not stop propagation, so this listener
  // always sees the drop.
  // #756: also require !editDenied -- a mirror is "hosted" too, and
  // arming this on mode alone let the overlay invite a drop that then
  // 403s on attachRawVideo. Gating the arming (not just the enqueue)
  // means the misleading overlay never shows at all.
  const hostedDropActive = modeResolved && mode === "hosted" && !editDenied;
  const pageDragActive = useWindowFileDrag(hostedDropActive);
  const stagesRef = useRef<{ stage_number: number; stage_name: string }[]>([]);
  useEffect(() => {
    stagesRef.current = project?.stages ?? [];
  }, [project]);
  useEffect(() => {
    if (!hostedDropActive) return;
    const onDrop = (e: DragEvent) => {
      if (e.dataTransfer && e.dataTransfer.files.length > 0) {
        enqueue(e.dataTransfer.files, { slug, stages: stagesRef.current });
      }
    };
    window.addEventListener("drop", onDrop);
    return () => window.removeEventListener("drop", onDrop);
  }, [hostedDropActive, enqueue, slug]);

  const assignedCount = useMemo(() => {
    if (!project) return 0;
    return project.stages.reduce((sum, s) => sum + (s.videos?.length ?? 0), 0);
  }, [project]);

  // Poll for proxy generation: while any video has proxy_ready === false,
  // refetch the project every ~5s so badges update without SSE.
  const anyProxyPending = useMemo(() => {
    if (!project) return false;
    // A proxy can only be pending when one is actually coming: mirror
    // matches never get proxies (raw media stays on desktop), so an
    // honest proxy_ready=false there must not arm the poll (#821).
    if (project.origin === "desktop") return false;
    const allVideos = [
      ...project.stages.flatMap((s) => s.videos ?? []),
      ...(project.unassigned_videos ?? []),
    ];
    return allVideos.some((v) => v.proxy_ready === false);
  }, [project]);

  useEffect(() => {
    if (!anyProxyPending) return;
    const id = window.setInterval(async () => {
      try {
        setProject(await api.getProject(slug));
      } catch {
        /* transient; next tick retries */
      }
    }, 5000);
    return () => window.clearInterval(id);
  }, [anyProxyPending, slug]);
  const activeShooterName = shooters.find((s) => s.slug === slug)?.name;
  void assignedCount;

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

  // Commit runs the scan immediately; the picker footer shows progress.
  // Throwing keeps the dialog open with the message inline - including
  // the "success but nothing imported" case, which must not look like a
  // silent no-op (the page behind is unchanged when 0 register).
  async function commitFolder(path: string): Promise<void> {
    if (editDenied) throw new Error(READ_ONLY_MIRROR_MESSAGE);
    const result = await api.scanVideos(slug, path, true, storage);
    await afterImport(result.registered.length, result.registered);
    if (result.registered.length === 0) {
      throw new Error(
        result.skipped.length > 0
          ? `No new videos - ${result.skipped.length} skipped (already imported or unsupported)`
          : "No video files found in this folder",
      );
    }
  }

  async function commitFiles(files: FolderPickerCommitFile[]): Promise<void> {
    if (editDenied) throw new Error(READ_ONLY_MIRROR_MESSAGE);
    const result = await api.scanFiles(
      slug,
      files.map((f) => f.path),
      true,
      storage,
    );
    await afterImport(result.registered.length, result.registered);
    if (result.registered.length === 0) {
      throw new Error("Nothing imported - the selected files were skipped");
    }
  }

  async function moveShooterBatch(targetSlug: string, videoPaths: string[]) {
    if (editDenied) {
      setError(READ_ONLY_MIRROR_MESSAGE);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const resp = await api.moveShooter(slug, targetSlug, videoPaths);
      setMoveBlocked(resp.outcome.blocked);
      setLastImportedPaths(null);
      // The move already returns the updated source project (the videos that
      // moved away are gone from it); use it instead of a blocking full
      // reload(). The shooter chip counts + beep CTA are refreshed out of band
      // so neither gates the move round-trip.
      setProject(resp.source_project);
      void api
        .listMatchShooters()
        .then((r) => setOwnShooters(r.shooters))
        .catch(() => {});
      outletCtx?.refresh();
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  // Assigning a video to a stage is applied optimistically: the local project
  // updates on click (mirroring the backend's assign_video), so the UI never
  // sits behind the round-trip - the freeze that made users re-click and land
  // wrong assignments. The POST runs in the background, serialized via
  // moveChain; the returned doc reconciles once the burst drains, and any
  // failure resyncs authoritatively via reload(). Resolves true immediately so
  // the layout can auto-advance selection without waiting on the network.
  function moveAssignment(
    videoPath: string,
    toStage: number | null,
    role: VideoRole,
  ): Promise<boolean> {
    if (editDenied) {
      setError(READ_ONLY_MIRROR_MESSAGE);
      return Promise.resolve(false);
    }
    setError(null);
    // Build on the latest state (functional update) so a burst of clicks stacks
    // correctly - each move layers onto the previous optimistic result.
    setProject((cur) =>
      cur ? applyAssignmentLocally(cur, videoPath, toStage, role) : cur,
    );
    inflight.current += 1;
    moveChain.current = moveChain.current.then(async () => {
      try {
        const updated = await api.moveAssignment(slug, videoPath, toStage, role);
        // Only the last write in the burst reconciles; an earlier response
        // predates the still-queued optimistic moves and would revert them.
        if (inflight.current === 1) setProject(updated);
      } catch (e: unknown) {
        setError(e instanceof ApiError ? e.detail : String(e));
        // Optimistic state may be ahead of the server now; pull the truth back.
        void reload();
      } finally {
        inflight.current -= 1;
      }
    });
    return Promise.resolve(true);
  }

  // Removing a video is applied optimistically - the clip disappears on click
  // (mirroring the backend's remove_video via removeVideoLocally) instead of
  // after the round-trip, so the delete never feels like the freeze that made
  // users re-click. Its POST is serialized on the same moveChain as the
  // assignment writes so a delete racing an assign can't 409 on the shared
  // optimistic version lock; only the last write in a burst reconciles with the
  // authoritative project the backend returns (an earlier response predates the
  // later optimistic edits and would revert them). Any failure resyncs via
  // reload(). Returns a resolved promise so callers that `void` it stay simple.
  async function removeVideo(videoPath: string): Promise<void> {
    if (editDenied) {
      setError(READ_ONLY_MIRROR_MESSAGE);
      return;
    }
    // Guard the destructive action: removal drops the video from the project
    // and clears its regenerable caches. It's recoverable in both deployment
    // modes: local unlinks only the raw/ symlink (source on disk untouched);
    // hosted clears the ephemeral local mirror but retains the uploaded object.
    // So the copy is mode-neutral ("original footage isn't deleted") rather
    // than the shooter dialog's "cannot be undone".
    const ok = await confirm({
      title: "Remove this video?",
      body: "It's removed from this project and its cached audio and trims are cleared. Your original footage isn't deleted, so you can re-add it to bring it back.",
      confirmLabel: "Remove video",
    });
    if (!ok.confirmed) return;
    setError(null);
    setProject((cur) => (cur ? removeVideoLocally(cur, videoPath) : cur));
    inflight.current += 1;
    moveChain.current = moveChain.current.then(async () => {
      try {
        const resp = await api.removeVideo(slug, videoPath, false);
        if (inflight.current === 1) setProject(resp.project);
      } catch (e: unknown) {
        setError(e instanceof ApiError ? e.detail : String(e));
        // Optimistic state may be ahead of the server now; pull the truth back.
        void reload();
      } finally {
        inflight.current -= 1;
      }
    });
    return Promise.resolve();
  }

  // Detail-pane saves (camera set, coverage) can hand back the updated project
  // the mutation already returned; splice it in directly and skip the refetch.
  // A bare call (no project) still falls back to a full reload().
  function handleSaved(updated?: MatchProject): Promise<void> {
    if (updated) {
      setProject(updated);
      return Promise.resolve();
    }
    return reload();
  }

  // ---- Footage page state (UX PR 6) ----------------------------------------

  // Every other shooter's project feeds the matrix; the ``slug`` project
  // above is the write target and refreshes through the write paths.
  const [others, setOthers] = useState<Record<string, MatchProject | null>>({});
  const otherSlugs = shooters.filter((s) => s.slug !== slug).map((s) => s.slug);
  const otherKey = otherSlugs.join(",");
  const [othersTick, setOthersTick] = useState(0);
  useEffect(() => {
    if (!otherKey) {
      setOthers({});
      return;
    }
    let alive = true;
    Promise.all(
      otherKey.split(",").map((s) =>
        api
          .getProject(s)
          .then((p) => [s, p] as const)
          .catch(() => [s, null] as const),
      ),
    ).then((entries) => {
      if (alive) setOthers(Object.fromEntries(entries));
    });
    return () => {
      alive = false;
    };
  }, [otherKey, othersTick]);
  const projects = useMemo<Record<string, MatchProject | null>>(() => ({ ...others, [slug]: project }), [others, slug, project]);

  const rows = useMemo(() => buildFootageRows({ projects, shooters, jobs }), [projects, shooters, jobs]);
  const unassigned = useMemo(() => unassignedVideos({ projects, shooters }), [projects, shooters]);
  const stats = useMemo(() => footageStats(rows, unassigned, shooters.length), [rows, unassigned, shooters.length]);
  const clipModel = useMemo(() => (project ? buildClipModel(project) : null), [project]);

  // The open clip: a path on a shooter's project, or an assign request
  // for an empty cell.
  const [sheet, setSheet] = useState<{ slug: string; videoId: string | null; assignStage: number | null } | null>(null);
  const [addShooterOpen, setAddShooterOpen] = useState(false);
  const sheetProject = sheet ? projects[sheet.slug] : null;
  const sheetClip: ClipItem | null = useMemo(() => {
    if (!sheet?.videoId || !sheetProject) return null;
    // By id, not path: one source file covering several stages is one
    // path with a video id per stage.
    const model = buildClipModel(sheetProject);
    return model.order.find((c) => c.video.video_id === sheet.videoId) ?? null;
  }, [sheet, sheetProject]);
  useEffect(() => {
    // A clip that vanished (removed, moved away) closes its sheet.
    if (sheet?.videoId && sheetProject && !sheetClip) setSheet(null);
  }, [sheet, sheetProject, sheetClip]);

  const hrefs = useMemo<FootageHrefs & { footage: (s: string) => string; audit1: (s: string) => string }>(
    () => ({
      audit: (s, n) => href("audit", s, String(n)),
      footage: (s) => href("ingest", s),
      audit1: (s) => href("audit", s),
    }),
    [href],
  );

  // Writes on another shooter's clip go through the same endpoints with
  // that slug; the matrix refetches that project afterwards.
  async function moveOn(targetSlug: string, videoPath: string, toStage: number | null, role: VideoRole): Promise<void> {
    if (targetSlug === slug) {
      await moveAssignment(videoPath, toStage, role);
      return;
    }
    if (editDenied) {
      setError(READ_ONLY_MIRROR_MESSAGE);
      return;
    }
    try {
      const updated = await api.moveAssignment(targetSlug, videoPath, toStage, role);
      setOthers((cur) => ({ ...cur, [targetSlug]: updated }));
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }
  async function removeOn(targetSlug: string, videoPath: string): Promise<void> {
    if (targetSlug === slug) {
      await removeVideo(videoPath);
      return;
    }
    if (editDenied) {
      setError(READ_ONLY_MIRROR_MESSAGE);
      return;
    }
    const ok = await confirm({
      title: "Remove this video?",
      body: "It's removed from this project and its cached audio and trims are cleared. Your original footage isn't deleted, so you can re-add it to bring it back.",
      confirmLabel: "Remove video",
    });
    if (!ok.confirmed) return;
    try {
      const resp = await api.removeVideo(targetSlug, videoPath, false);
      setOthers((cur) => ({ ...cur, [targetSlug]: resp.project }));
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }
  async function moveShooterFrom(fromSlug: string, targetSlug: string, videoPaths: string[]): Promise<void> {
    if (fromSlug === slug) {
      await moveShooterBatch(targetSlug, videoPaths);
      setOthersTick((n) => n + 1);
      return;
    }
    if (editDenied) {
      setError(READ_ONLY_MIRROR_MESSAGE);
      return;
    }
    try {
      const resp = await api.moveShooter(fromSlug, targetSlug, videoPaths);
      setOthers((cur) => ({ ...cur, [fromSlug]: resp.source_project }));
      if (targetSlug === slug) await reload();
      else setOthersTick((n) => n + 1);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }
  async function detectBeepOn(targetSlug: string, stage: number) {
    const prim = projects[targetSlug]?.stages.find((s) => s.stage_number === stage)?.videos.find((v) => v.role === "primary");
    if (!prim) return;
    setError(null);
    try {
      await api.detectBeepForVideo(targetSlug, stage, prim.video_id);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }
  async function removeShooter(s: ShooterListEntry) {
    const ok = await confirm({
      title: `Remove ${s.name}?`,
      body: "Their footage, audit, and exports inside the match folder will be deleted. This cannot be undone.",
      confirmLabel: "Remove shooter",
    });
    if (!ok.confirmed) return;
    setError(null);
    try {
      await api.removeMatchShooter(s.slug);
      outletCtx?.refresh();
      await reload();
      setOthersTick((n) => n + 1);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }
  async function rebuildTrims(s: ShooterListEntry) {
    setError(null);
    try {
      const result = await api.buildShooterTrimCaches(s.slug);
      if (result.jobs_submitted.length === 0) {
        setError(`No trim jobs to run for ${s.name}: every eligible angle was already cached, missing prerequisites, or already queued.`);
      }
    } catch (e: unknown) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  const openClip = (targetSlug: string, _stage: number, video: StageVideo) => setSheet({ slug: targetSlug, videoId: video.video_id, assignStage: null });
  const openAssign = (targetSlug: string, stage: number) => setSheet({ slug: targetSlug, videoId: null, assignStage: stage });
  const openUnassigned = (item: UnassignedItem) => setSheet({ slug: item.slug, videoId: item.video.video_id, assignStage: null });
  const assignUnassigned = (item: UnassignedItem, stage: number) => {
    void moveOn(item.slug, item.video.path, stage, "secondary");
    // The id changes with the stage (path + stage hash); the sheet finds
    // the clip again by path once the optimistic move has landed.
    setSheet(null);
  };

  const showBanner = lastImportedPaths != null && lastImportedPaths.length > 0 && shooters.length > 1;
  const subLine = [
    `${stats.shooters} ${stats.shooters === 1 ? "shooter" : "shooters"}`,
    `${stats.videos} ${stats.videos === 1 ? "video" : "videos"}`,
    `${stats.covered} of ${stats.total} ${shooters.length > 1 ? "stage takes" : "stages"} covered`,
    stats.unassigned > 0 ? `${stats.unassigned} unassigned` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const dropZone = !modeResolved ? (
    <div role="status" aria-label="Checking how footage can be added" className="mb-4 h-11 rounded-[10px] border border-dashed border-rule-strong" />
  ) : (
    <div className="mb-4 flex flex-wrap items-center gap-3 rounded-[10px] border border-dashed border-rule-strong bg-surface px-4 py-2.5 text-md text-ink-2">
      <Upload className="size-4 text-muted" aria-hidden />
      {mode === "hosted" ? (
        editDenied ? (
          <span className="text-muted">Footage for this match is added on the desktop install and syncs here.</span>
        ) : (
          <>
            <span>Drop video files anywhere on this page, or browse.</span>
            <span className="text-muted">
              Files are matched to stages by recording time
              {shooters.length > 1 && activeShooterName ? (
                <>
                  {" "}
                  and added to <b className="font-medium text-ink">{activeShooterName}</b>
                </>
              ) : null}
              .
            </span>
          </>
        )
      ) : (
        <>
          <span>Add footage from a folder on this machine.</span>
          <span className="text-muted">
            Files are matched to stages by recording time
            {shooters.length > 1 && activeShooterName ? (
              <>
                {" "}
                and added to <b className="font-medium text-ink">{activeShooterName}</b>
              </>
            ) : null}
            .
          </span>
        </>
      )}
      <span className="ml-auto flex items-center gap-2">
        {mode === "local" && !editDenied ? (
          <button
            type="button"
            onClick={() => setStorage((v) => (v === "symlink" ? "copy" : "symlink"))}
            aria-pressed={storage === "copy"}
            title="Link videos in place, or copy them into the match folder"
          >
            <Chip tick="muted">{storage === "symlink" ? "Link in place" : "Copy files"}</Chip>
          </button>
        ) : null}
        <Button size="sm" onClick={() => setShowAddFootage(true)} disabled={editDenied}>
          {mode === "hosted" ? "Browse files" : "Pick a folder"}
        </Button>
      </span>
    </div>
  );

  return (
    <div className="px-4 py-4 md:px-7 md:py-5">
      <PageHeader
        title="Footage"
        sub={subLine}
        actions={
          <>
            {modeResolved && mode === "local" && !editDenied ? (
              <Button onClick={() => setShowRelinkDialog(true)} title="Use this when source files have moved and the project's links are broken">
                Find moved videos
              </Button>
            ) : null}
            <Button onClick={() => setAddShooterOpen(true)} disabled={editDenied}>
              Add shooter
            </Button>
            <Button variant="primary" onClick={() => setShowAddFootage(true)} disabled={!modeResolved || editDenied}>
              Add footage
            </Button>
          </>
        }
      >
        {shooters.length > 1 ? (
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Adding footage to">
            {shooters.map((s) => (
              <Link key={s.slug} to={hrefs.footage(s.slug)} aria-current={s.slug === slug ? "true" : undefined}>
                <Chip tone={s.slug === slug ? "ok" : "neutral"} tick={s.slug === slug ? "draw" : "muted"}>
                  {s.name}
                </Chip>
              </Link>
            ))}
          </div>
        ) : null}
      </PageHeader>

      {editDenied ? <p className="mb-3 text-sm text-muted">{READ_ONLY_MIRROR_MESSAGE}</p> : null}
      {error ? (
        <p role="alert" className="mb-3 text-sm text-led-text">
          {error}
        </p>
      ) : null}

      {dropZone}

      {showBanner ? (
        <div className="mb-4">
          <IngestMoveBanner
            shooterName={activeShooterName ?? slug}
            videoPaths={lastImportedPaths}
            shooters={shooters}
            excludeSlug={slug}
            blocked={moveBlocked}
            busy={busy}
            onMove={moveShooterBatch}
            onDismiss={() => {
              setLastImportedPaths(null);
              setMoveBlocked([]);
            }}
          />
        </div>
      ) : moveBlocked.length > 0 ? (
        <p role="status" className="mb-4 text-sm text-live">
          {moveBlocked.length} {moveBlocked.length === 1 ? "stage" : "stages"} not moved: the destination already had reviewed footage.{" "}
          <button type="button" onClick={() => setMoveBlocked([])} className="text-ink-2 hover:text-ink">
            Dismiss
          </button>
        </p>
      ) : null}

      {!project ? (
        <p className="py-6 text-md text-muted">Reading footage...</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
          {isMobile ? (
            <FootageCards rows={rows} hrefs={hrefs} onOpen={openClip} />
          ) : (
            <CoverageMatrix
              rows={rows}
              currentVideoId={sheet?.videoId ?? null}
              currentStage={sheet?.assignStage ?? sheetClip?.stageNumber ?? null}
              hrefs={hrefs}
              onOpen={openClip}
              onAssign={openAssign}
              onDetectBeep={(s, n) => void detectBeepOn(s, n)}
              editDenied={editDenied}
            />
          )}
          <div className="flex flex-col gap-4">
            <UnassignedPanel
              items={unassigned}
              stages={project.stages}
              multi={shooters.length > 1}
              editDenied={editDenied}
              onOpen={openUnassigned}
              onAssign={assignUnassigned}
              onRemove={(item) => void removeOn(item.slug, item.video.path)}
            />
            <ShootersPanel
              shooters={shooters}
              activeSlug={slug}
              editDenied={editDenied}
              hrefs={{ footage: hrefs.footage, audit: hrefs.audit1 }}
              onAdd={() => setAddShooterOpen(true)}
              onRemove={(s) => void removeShooter(s)}
              onRebuildTrims={(s) => void rebuildTrims(s)}
            />
            {clipModel ? <CamerasPanel slug={slug} cameras={clipModel.cameras} editDenied={editDenied} onSaved={handleSaved} /> : null}
          </div>
        </div>
      )}

      <ClipSheet
        open={sheet != null}
        onClose={() => setSheet(null)}
        slug={sheet?.slug ?? slug}
        shooterName={shooters.find((s) => s.slug === sheet?.slug)?.name ?? activeShooterName ?? ""}
        clip={sheetClip}
        assignStage={sheet?.assignStage ?? null}
        unassigned={unassigned}
        allStages={sheetProject?.stages ?? project?.stages ?? []}
        shooters={shooters}
        rawVideos={sheetProject?.raw_videos ?? []}
        mediaOnDesktop={project?.origin === "desktop"}
        busy={busy}
        editDenied={editDenied}
        auditHref={hrefs.audit}
        onMove={(path, toStage, role) => moveOn(sheet?.slug ?? slug, path, toStage, role)}
        onRemove={(path) => removeOn(sheet?.slug ?? slug, path)}
        onMoveShooter={(target, paths) => moveShooterFrom(sheet?.slug ?? slug, target, paths)}
        onPickUnassigned={assignUnassigned}
        onError={setError}
        onReload={handleSaved}
      />
      <AddShooterSheet
        open={addShooterOpen}
        onClose={() => setAddShooterOpen(false)}
        project={project}
        shooters={shooters}
        editDenied={editDenied}
        onChanged={() => {
          outletCtx?.refresh();
          void reload();
          setOthersTick((n) => n + 1);
        }}
      />

      {showRelinkDialog && modeResolved && mode === "local" ? (
        <RelinkDialog slug={slug} onClose={() => setShowRelinkDialog(false)} onApplied={() => void reload()} />
      ) : null}

      {showAddFootage &&
        modeResolved &&
        (mode === "hosted" ? (
          <HostedUploadModal
            slug={slug}
            onClose={() => setShowAddFootage(false)}
            onImported={(imported, paths) => {
              void afterImport(imported, paths);
            }}
            stages={project?.stages ?? []}
          />
        ) : (
          <FolderPicker
            slug={slug}
            title="Add footage"
            subtitle={activeShooterName ? `Adding to ${activeShooterName}` : undefined}
            initialPath={lastScannedDir}
            allowEmptyFolder
            folderLabel="Add this folder"
            storage={{ value: storage, onChange: setStorage }}
            onCommitFolder={commitFolder}
            onCommitFiles={commitFiles}
            onClose={() => setShowAddFootage(false)}
          />
        ))}

      {hostedDropActive ? (
        <span className="sr-only" role="status" aria-live="polite">
          {pageDragActive ? "Release to add the files to the upload queue" : ""}
        </span>
      ) : null}
      {pageDragActive ? (
        <Portal>
          <div aria-hidden className="pointer-events-none fixed inset-0 z-takeover flex items-center justify-center bg-bg/80">
            <div className="rounded-[10px] border border-dashed border-led bg-surface px-10 py-8 text-center">
              <div className="text-lg font-semibold text-ink">Drop videos to upload</div>
              <div className="mt-1 text-sm text-muted">They join {activeShooterName ? `${activeShooterName}'s` : "this shooter's"} upload queue</div>
            </div>
          </div>
        </Portal>
      ) : null}
    </div>
  );
}
