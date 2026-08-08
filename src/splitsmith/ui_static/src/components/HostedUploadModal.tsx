/**
 * Hosted-mode browser upload surface (extracted from AddFootageModal).
 *
 * Drag-and-drop / file-pick, per-file progress, list of what's already
 * uploaded, prune via delete. Files land in S3 under
 * ``users/<id>/raw/`` via ``POST /api/me/raw/upload``; the SPA never
 * sees a host filesystem path. Uploaded objects are then attached to
 * the project (``attachToProject`` ->
 * ``POST /api/shooters/{slug}/raw-videos/attach``), which is what makes
 * them visible to the worker pipeline (#523).
 */

import { Check, FolderOpen, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { UploadQueueSummary } from "@/components/UploadQueueSummary";
import { CoverageSelect } from "@/components/ingest/CoverageSelect";
import { Portal } from "@/components/ui/Portal";
import { ApiError, api, type RawUploadEntry } from "@/lib/api";
import { useDialogFocus } from "@/lib/dialogFocus";
import { useElementFileDrag } from "@/lib/dragDepth";
import { formatBytes } from "@/lib/format";
import { useUploads, type PendingUpload } from "@/lib/uploads";
import { cn } from "@/lib/utils";

export function HostedUploadModal({
  slug,
  onClose,
  onImported,
  stages,
}: {
  slug: string;
  onClose: () => void;
  onImported: (imported: number, paths: string[]) => void;
  stages: { stage_number: number; stage_name: string }[];
}) {
  const {
    uploads: allUploads,
    enqueue,
    cancel,
    attachTick,
    probeFor,
    queue,
    inFlight: queueInFlight,
  } = useUploads();
  // Show only this shooter's pending items in the modal's session list.
  const uploads = allUploads.filter((u) => u.slug === slug);
  const inFlight = uploads.some(
    (u) => u.status === "queued" || u.status === "uploading",
  );
  // The summary above the list reports the whole queue, not this
  // shooter's slice: the pump is global and sequential, so a file queued
  // for another shooter genuinely delays these. Say so when that is
  // actually the case rather than showing numbers that outrun the list.
  const hasOtherShooters = allUploads.some((u) => u.slug !== slug);
  const doEnqueue = (files: FileList | File[]) => enqueue(files, { slug, stages });
  const [existing, setExisting] = useState<RawUploadEntry[] | null>(null);
  // Depth-counted drag highlight - the naive isDragging boolean
  // flickered off whenever the cursor crossed a child of the zone.
  const { dragging, reset, handlers } = useElementFileDrag();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  // Track which uploaded filenames the operator has attached to the
  // current shooter's project this session, plus any inflight / error
  // state. Persistent attachment lives on match.json (raw_videos[]);
  // this state is just the UI flash so the operator sees the action
  // succeed before closing the modal.
  const [attachState, setAttachState] = useState<
    Record<
      string,
      | { status: "attaching" }
      | { status: "attached" }
      | { status: "error"; message: string }
    >
  >({});
  // Server-suggested coverage keyed by filename, sourced from the provider:
  // the pump fetches suggestCoverage on auto-attach failure and stores it on
  // the upload record. Used to pre-fill the manual-attach picker below.
  const suggestionByFilename = useMemo(() => {
    const m: Record<string, number[]> = {};
    for (const u of allUploads) {
      if (u.suggestedStages && u.suggestedStages.length > 0) {
        m[u.file.name] = u.suggestedStages;
      }
    }
    return m;
  }, [allUploads]);
  // User-selected coverage keyed by filename.
  const [coverageByFilename, setCoverageByFilename] = useState<Record<string, number[]>>({});

  // Initial list -- so the surface opens with a real "you've already
  // uploaded X" view instead of looking empty until the operator
  // touches something.
  useEffect(() => {
    let alive = true;
    api
      .listRawUploads()
      .then((r) => {
        if (alive) setExisting(r.uploads);
      })
      .catch(() => {
        if (alive) setExisting([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  // Refresh the existing list whenever any upload finishes so the
  // user sees their freshly-uploaded entry land in the bottom panel.
  const refreshExisting = useCallback(async () => {
    try {
      const r = await api.listRawUploads();
      setExisting(r.uploads);
    } catch {
      // Non-fatal -- the just-completed upload is still in the
      // pending list, the user knows it succeeded.
    }
  }, []);

  useEffect(() => {
    for (const [filename, sugg] of Object.entries(suggestionByFilename)) {
      setCoverageByFilename((prev) =>
        prev[filename] ? prev : { ...prev, [filename]: sugg },
      );
    }
  }, [suggestionByFilename]);

  useEffect(() => {
    if (attachTick === 0) return;
    void refreshExisting();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachTick]);

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) doEnqueue(e.target.files);
    // Reset so picking the same file twice in a row still fires.
    e.target.value = "";
  };

  const removeUploaded = async (filename: string) => {
    try {
      await api.deleteRawUpload(filename);
      await refreshExisting();
    } catch {
      // Surface inline -- a delete failure is non-fatal; the operator
      // can retry. We don't blow away the row.
    }
  };

  const attachToProject = useCallback(
    async (entry: RawUploadEntry, coverage: number[]) => {
      setAttachState((prev) => ({
        ...prev,
        [entry.filename]: { status: "attaching" },
      }));
      try {
        await api.attachRawVideo(slug, {
          filename: entry.filename,
          sha256: entry.etag,
          size_bytes: entry.size,
          covers_stages: coverage.length > 0 ? coverage : undefined,
          duration_seconds: probeFor(entry.filename)?.duration_s ?? undefined,
          recorded_start: probeFor(entry.filename)?.recorded_start ?? undefined,
        });
        setAttachState((prev) => ({
          ...prev,
          [entry.filename]: { status: "attached" },
        }));
        // The video now lives in unassigned_videos on the project; tell
        // the parent so the ingest page refreshes and the operator sees
        // the new row in the tray when they close the modal. The hosted
        // upload path uses the storage key as the path placeholder; the
        // caller uses this for the batch-move banner (B1) if needed.
        onImported(1, [entry.filename]);
      } catch (err) {
        const msg = err instanceof ApiError ? err.detail : String(err);
        setAttachState((prev) => ({
          ...prev,
          [entry.filename]: { status: "error", message: msg },
        }));
      }
    },
    [slug, onImported, probeFor],
  );

  // A raw object belongs to one shooter. Hide uploads already attached to a
  // DIFFERENT shooter so they can't be re-attached here (attach also 409s
  // server-side). Entries with no owner, or owned by this shooter, stay. (#562)
  const availableExisting = (existing ?? []).filter(
    (e) => !e.attached_to || e.attached_to === slug,
  );
  const hiddenExistingCount = (existing?.length ?? 0) - availableExisting.length;

  // Escape / focus trap / restore. The sheet is dismissable mid-upload
  // now that transfers live in the provider, so Escape always closes.
  useDialogFocus(true, panelRef, onClose);

  return (
    <Portal>
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Upload raw footage"
      className="fixed inset-0 z-modal flex items-center justify-center bg-bg/70 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        className="relative flex h-[min(720px,90vh)] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-rule-strong bg-surface text-ink shadow-[0_24px_48px_-12px_rgba(0,0,0,0.7)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-4 border-b border-rule px-5 py-3.5">
          <div>
            <h2 className="font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
              Upload raw footage
            </h2>
            <p className="mt-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
              Files land in your hosted storage. Attach to this project
              to drop them into the unassigned tray.
            </p>
            {inFlight && (
              <p className="mt-1 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-led">
                You can close this and keep working - uploads continue in the background.
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1.5 text-subtle hover:bg-surface-2 hover:text-ink"
          >
            <X className="size-4" />
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4">
          {/* Dropzone */}
          <div
            data-testid="hosted-dropzone"
            {...handlers}
            onDrop={(e) => {
              // The hosted Ingest page behind this modal listens for
              // window-level drops; stopPropagation keeps a drop on
              // this zone from also enqueueing there (double enqueue).
              e.preventDefault();
              e.stopPropagation();
              reset();
              if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                doEnqueue(e.dataTransfer.files);
              }
            }}
            className={cn(
              "flex flex-col items-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors",
              dragging
                ? "border-led bg-led-tint"
                : "border-rule bg-surface-2 hover:border-rule-strong",
            )}
          >
            <FolderOpen className="size-6 text-muted" />
            <div className="font-display text-[0.8125rem] font-bold uppercase tracking-[0.08em] text-ink">
              Drop video files here
            </div>
            <div className="font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
              or
            </div>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="rounded-md border border-rule-strong bg-surface px-3 py-1.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:border-led-deep hover:bg-led-tint hover:text-led"
            >
              Choose files...
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept="video/*"
              onChange={onPick}
              className="hidden"
            />
          </div>

          {/* Pending queue */}
          {uploads.length > 0 && (
            <section className="flex flex-col gap-2">
              <h3 className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
                This session ({uploads.length})
              </h3>
              {/* The dock carries this readout too, but it is a fixed
                  bottom-right portal sitting behind this modal's overlay
                  -- so without a copy here the queue looks frozen on the
                  one surface the operator is actually looking at (#556). */}
              <UploadQueueSummary
                queue={queue}
                inFlight={queueInFlight}
                note={hasOtherShooters ? "all shooters" : undefined}
                className="rounded-md border border-rule bg-surface-2 px-3 py-2 text-ink"
              />
              <ul className="flex flex-col gap-1.5">
                {uploads.map((u) => (
                  <UploadRow key={u.id} upload={u} onCancel={() => cancel(u.id)} />
                ))}
              </ul>
            </section>
          )}

          {/* Existing uploads */}
          <section className="flex flex-col gap-2">
            <h3 className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
              Already in storage{existing ? ` (${availableExisting.length})` : ""}
              {hiddenExistingCount > 0
                ? ` . ${hiddenExistingCount} on other shooters`
                : ""}
            </h3>
            {existing === null ? (
              <p className="font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
                Loading...
              </p>
            ) : existing.length === 0 ? (
              <p className="rounded-md border border-rule bg-surface-2 px-3 py-2 font-mono text-[0.75rem] text-muted">
                Nothing uploaded yet. Files added here persist across
                browser sessions.
              </p>
            ) : availableExisting.length === 0 ? (
              <p className="rounded-md border border-rule bg-surface-2 px-3 py-2 font-mono text-[0.75rem] text-muted">
                All uploads here are already attached to another shooter.
              </p>
            ) : (
              <ul className="flex flex-col gap-2">
                {availableExisting.map((e) => (
                  <ExistingRow
                    key={e.path}
                    entry={e}
                    attachState={attachState[e.filename]}
                    onDelete={() => removeUploaded(e.filename)}
                    onAttach={() =>
                      attachToProject(e, coverageByFilename[e.filename] ?? [])
                    }
                    stages={stages}
                    coverage={coverageByFilename[e.filename] ?? []}
                    suggestion={suggestionByFilename[e.filename] ?? []}
                    onCoverageChange={(v) =>
                      setCoverageByFilename((prev) => ({
                        ...prev,
                        [e.filename]: v,
                      }))
                    }
                  />
                ))}
              </ul>
            )}
          </section>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-rule bg-surface-2 px-5 py-3.5">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md bg-led-fill px-3.5 py-1.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink shadow-[0_0_0_1px_var(--color-led-fill),0_0_18px_var(--color-led-glow)] hover:bg-led"
          >
            Done
          </button>
        </footer>
      </div>
    </div>
    </Portal>
  );
}

function UploadRow({
  upload,
  onCancel,
}: {
  upload: PendingUpload;
  onCancel: () => void;
}) {
  const pct =
    upload.file.size > 0
      ? Math.min(100, Math.round((upload.bytesSent / upload.file.size) * 100))
      : 0;
  return (
    <li className="rounded-md border border-rule bg-surface-2 px-3 py-2">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[0.75rem] text-ink">
            {upload.file.name}
          </div>
          <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            {formatBytes(upload.file.size)}
            {upload.status === "queued" && " . queued"}
            {upload.status === "uploading" && ` . ${pct}%`}
            {upload.status === "done" && " . done"}
            {upload.status === "cancelled" && " . cancelled"}
            {upload.status === "error" && (
              <span className="text-led-text"> . {upload.errorMessage}</span>
            )}
          </div>
        </div>
        {(upload.status === "queued" || upload.status === "uploading") && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md p-1 text-subtle hover:bg-surface-3 hover:text-ink"
            aria-label="Cancel upload"
          >
            <X className="size-3.5" />
          </button>
        )}
        {upload.status === "done" && (
          <span
            aria-hidden
            className="inline-flex size-5 items-center justify-center rounded-full bg-done text-bg"
          >
            <Check className="size-3" strokeWidth={3} />
          </span>
        )}
      </div>
      {upload.status === "uploading" && (
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-surface">
          <div
            className="h-full bg-led transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </li>
  );
}

function ExistingRow({
  entry,
  attachState,
  onDelete,
  onAttach,
  stages,
  coverage,
  suggestion,
  onCoverageChange,
}: {
  entry: RawUploadEntry;
  attachState:
    | { status: "attaching" }
    | { status: "attached" }
    | { status: "error"; message: string }
    | undefined;
  onDelete: () => void;
  onAttach: () => void;
  stages: { stage_number: number; stage_name: string }[];
  coverage: number[];
  suggestion: number[];
  onCoverageChange: (v: number[]) => void;
}) {
  const isAttaching = attachState?.status === "attaching";
  const isAttached = attachState?.status === "attached";
  const attachError =
    attachState?.status === "error" ? attachState.message : null;
  return (
    <li className="rounded-md border border-rule bg-surface-2">
      {/* File info row */}
      <div className="flex items-center justify-between gap-3 px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[0.75rem] text-ink">
            {entry.filename}
          </div>
          <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            {formatBytes(entry.size)}
            {entry.last_modified && ` . ${formatRelative(entry.last_modified)}`}
            {isAttached && (
              <span className="text-done"> . attached to project</span>
            )}
            {attachError && (
              <span className="text-led-text"> . {attachError}</span>
            )}
          </div>
        </div>
        {isAttached ? (
          <span
            aria-hidden
            className="inline-flex size-5 items-center justify-center rounded-full bg-done text-bg"
          >
            <Check className="size-3" strokeWidth={3} />
          </span>
        ) : (
          <button
            type="button"
            onClick={onAttach}
            disabled={isAttaching}
            aria-label={`Attach ${entry.filename} to project`}
            className="rounded-md border border-rule-strong bg-surface px-2.5 py-1 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:border-led-deep hover:bg-led-tint hover:text-led disabled:opacity-50"
          >
            {isAttaching ? "Attaching..." : "Attach"}
          </button>
        )}
        <button
          type="button"
          onClick={onDelete}
          disabled={isAttaching}
          aria-label={`Delete ${entry.filename}`}
          className="rounded-md p-1 text-subtle hover:bg-led-tint hover:text-led-text disabled:opacity-50"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
      {/* Coverage select - only when not attached and there are stages */}
      {!isAttached && stages.length > 0 && (
        <div className="border-t border-rule px-3 pb-2.5 pt-2">
          <div className="mb-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.12em] text-subtle">
            Covers stages
          </div>
          <CoverageSelect
            stages={stages}
            value={coverage}
            onChange={onCoverageChange}
            suggested={suggestion}
          />
        </div>
      )}
    </li>
  );
}

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const secs = Math.max(0, Math.round((now - then) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}
