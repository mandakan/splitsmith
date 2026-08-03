import { useState } from "react";
import { ChevronDown, ChevronUp, X } from "lucide-react";

import { Portal } from "@/components/ui/Portal";
import { UploadQueueSummary } from "@/components/UploadQueueSummary";
import { useUploads, type PendingUpload } from "@/lib/uploads";

export function UploadDock() {
  const { uploads, cancel, cancelAll, clearFinished, inFlight, queue } = useUploads();
  const [expanded, setExpanded] = useState(true);

  if (uploads.length === 0) return null;

  return (
    <Portal>
      <div className="fixed bottom-4 right-4 z-drawer w-[min(360px,calc(100vw-2rem))] overflow-hidden rounded-xl border border-rule-strong bg-surface text-ink shadow-[0_24px_48px_-12px_rgba(0,0,0,0.7)]">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex w-full items-center justify-between gap-3 border-b border-rule bg-surface-2 px-4 py-2.5 text-left"
        >
          <UploadQueueSummary
            queue={queue}
            inFlight={inFlight}
            showBar={inFlight}
            layout="stacked"
            className="flex-1"
          />
          {expanded ? <ChevronDown className="size-4 text-muted" /> : <ChevronUp className="size-4 text-muted" />}
        </button>
        {expanded && (
          <div className="flex max-h-[40vh] flex-col gap-1.5 overflow-y-auto px-3 py-3">
            {uploads.map((u) => (
              <DockRow key={u.id} upload={u} onCancel={() => cancel(u.id)} />
            ))}
            <p className="mt-1 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
              Uploads run in the background, but don't reload the page until they finish.
            </p>
            <div className="flex justify-end gap-2 pt-1">
              {inFlight && (
                <button
                  type="button"
                  onClick={cancelAll}
                  className="rounded-md border border-rule px-2.5 py-1 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-muted hover:text-ink"
                >
                  Cancel all
                </button>
              )}
              {!inFlight && (
                <button
                  type="button"
                  onClick={clearFinished}
                  className="rounded-md border border-rule px-2.5 py-1 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-muted hover:text-ink"
                >
                  Dismiss
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </Portal>
  );
}

function DockRow({ upload, onCancel }: { upload: PendingUpload; onCancel: () => void }) {
  const pct =
    upload.file.size > 0
      ? Math.min(100, Math.round((upload.bytesSent / upload.file.size) * 100))
      : 0;
  const active = upload.status === "queued" || upload.status === "uploading";
  return (
    <div className="rounded-md border border-rule bg-surface-2 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 flex-1 truncate font-mono text-[0.6875rem] text-ink">
          {upload.file.name}
        </span>
        {active && (
          <button type="button" onClick={onCancel} aria-label="Cancel upload" className="text-subtle hover:text-led">
            <X className="size-3.5" />
          </button>
        )}
      </div>
      <div className="mt-0.5 font-mono text-[0.5625rem] uppercase tracking-[0.06em] text-muted">
        {upload.status === "queued" && "queued"}
        {upload.status === "uploading" && `${pct}%`}
        {upload.status === "done" && upload.attach === "attached" && "done . attached"}
        {upload.status === "done" && upload.attach === "attaching" && "done . attaching"}
        {upload.status === "done" && upload.attach === "failed" && "done . attach failed"}
        {upload.status === "cancelled" && "cancelled"}
        {upload.status === "error" && `error - ${upload.errorMessage ?? "failed"}`}
      </div>
    </div>
  );
}
