import { useEffect, useRef, useState } from "react";
import { Trash2 } from "lucide-react";

import { StatusPill } from "@/components/ui/StatusPill";
import { Button } from "@/components/ui/button";
import { RegisterWorkerDialog } from "@/components/admin/RegisterWorkerDialog";
import { ApiError, api, type WorkerView } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function workerTone(
  status: WorkerView["status"],
): "exported" | "awaiting" | "archived" | "in-progress" {
  switch (status) {
    case "online":
      return "exported";
    case "offline":
      return "awaiting";
    case "disabled":
      return "archived";
    case "pending":
      return "in-progress";
  }
}

interface WorkerRowProps {
  worker: WorkerView;
  onRefetch: () => void;
}

function WorkerRow({ worker, onRefetch }: WorkerRowProps) {
  const [priorityDraft, setPriorityDraft] = useState(String(worker.priority));
  const [patching, setPatching] = useState(false);
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [rowError, setRowError] = useState<string | null>(null);

  async function patchPriority() {
    const n = parseInt(priorityDraft, 10);
    if (Number.isNaN(n) || n === worker.priority) {
      setPriorityDraft(String(worker.priority));
      return;
    }
    setPatching(true);
    setRowError(null);
    try {
      await api.adminUpdateWorker(worker.id, { priority: n });
      onRefetch();
    } catch (e) {
      setRowError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setPatching(false);
    }
  }

  async function toggleEnabled() {
    setPatching(true);
    setRowError(null);
    try {
      await api.adminUpdateWorker(worker.id, { enabled: !worker.enabled });
      onRefetch();
    } catch (e) {
      setRowError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setPatching(false);
    }
  }

  async function deleteWorker() {
    setDeleting(true);
    setRowError(null);
    try {
      await api.adminDeleteWorker(worker.id);
      onRefetch();
    } catch (e) {
      setRowError(e instanceof ApiError ? e.detail : String(e));
      setDeleting(false);
    }
  }

  return (
    <article
      className={cn(
        "flex flex-col gap-2 border-b border-rule px-4 py-4 last:border-b-0",
        "md:flex-row md:items-center md:gap-6",
      )}
    >
      {/* Name + kind */}
      <div className="min-w-0 flex-1">
        <div className="font-display text-sm font-semibold uppercase tracking-tight text-ink">
          {worker.name}
        </div>
        <div className="mt-0.5 font-mono text-xs uppercase tracking-[0.08em] text-subtle">
          {worker.kind === "self_hosted" ? "self-hosted" : "railway"}
          {!worker.registered && (
            <span className="ml-2 text-amber-500">unregistered</span>
          )}
        </div>
      </div>

      {/* Status pill */}
      <div className="shrink-0">
        <StatusPill tone={workerTone(worker.status)}>
          {worker.status}
        </StatusPill>
      </div>

      {/* Priority */}
      <div className="flex shrink-0 items-center gap-1.5">
        <label
          htmlFor={`priority-${worker.id}`}
          className="font-mono text-xs uppercase tracking-[0.08em] text-subtle"
        >
          Priority
        </label>
        <input
          id={`priority-${worker.id}`}
          type="number"
          value={priorityDraft}
          onChange={(e) => setPriorityDraft(e.target.value)}
          onBlur={() => void patchPriority()}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.currentTarget.blur();
            }
          }}
          disabled={patching}
          className="w-16 rounded border border-rule bg-bg px-2 py-0.5 text-center font-mono text-xs disabled:opacity-50"
          aria-label={`Priority for ${worker.name}`}
        />
      </div>

      {/* Last seen */}
      <div
        className="shrink-0 font-mono text-xs text-subtle"
        title={worker.last_seen_at ?? undefined}
      >
        {relativeTime(worker.last_seen_at)}
      </div>

      {/* Enabled toggle */}
      <div className="flex shrink-0 items-center gap-1.5">
        <label
          htmlFor={`enabled-${worker.id}`}
          className="font-mono text-xs uppercase tracking-[0.08em] text-subtle"
        >
          Enabled
        </label>
        <input
          id={`enabled-${worker.id}`}
          type="checkbox"
          checked={worker.enabled}
          onChange={() => void toggleEnabled()}
          disabled={patching}
          aria-label={`${worker.enabled ? "Disable" : "Enable"} ${worker.name}`}
          className="h-4 w-4 cursor-pointer accent-done disabled:cursor-not-allowed disabled:opacity-50"
        />
      </div>

      {/* Delete */}
      <div className="flex shrink-0 items-center gap-1">
        {deleteArmed ? (
          <>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              onClick={() => void deleteWorker()}
              disabled={deleting}
              aria-label={`Confirm delete ${worker.name}`}
            >
              {deleting ? "Deleting..." : "Confirm"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setDeleteArmed(false)}
              disabled={deleting}
            >
              Cancel
            </Button>
          </>
        ) : (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setDeleteArmed(true)}
            disabled={patching}
            aria-label={`Delete ${worker.name}`}
          >
            <Trash2 className="size-3.5" aria-hidden="true" />
          </Button>
        )}
      </div>

      {/* Inline error */}
      {rowError ? (
        <div className="w-full font-mono text-xs text-destructive md:col-span-full">
          {rowError}
        </div>
      ) : null}
    </article>
  );
}

export function AdminWorkers() {
  const { user } = useAuth();
  const [workers, setWorkers] = useState<WorkerView[] | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [showRegister, setShowRegister] = useState(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const loadWorkers = async () => {
    setFetchError(null);
    try {
      const resp = await api.adminListWorkers();
      if (mountedRef.current) setWorkers(resp.workers);
    } catch (e) {
      if (mountedRef.current) {
        setFetchError(e instanceof ApiError ? e.detail : String(e));
      }
    }
  };

  useEffect(() => {
    if (!user?.is_admin) return;
    void loadWorkers();
  }, [user?.is_admin]); // loadWorkers closes over only stable refs (setters, refs, module api)

  if (!user?.is_admin) {
    return (
      <div className="py-8 text-center font-mono text-sm text-muted">
        Admin access required.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="font-display text-2xl font-bold uppercase tracking-tight text-ink">
          Workers
        </h1>
        <Button
          type="button"
          size="sm"
          onClick={() => setShowRegister(true)}
        >
          Register worker
        </Button>
      </div>

      {fetchError ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 font-mono text-xs text-destructive">
          {fetchError}
        </div>
      ) : null}

      <section aria-label="Worker list">
        {workers === null && !fetchError ? (
          <div className="py-6 text-center font-mono text-xs text-subtle">
            Loading...
          </div>
        ) : workers !== null && workers.length === 0 ? (
          <div className="rounded-md border border-dashed border-rule px-4 py-6 text-center font-mono text-xs text-subtle">
            No workers registered.
          </div>
        ) : workers !== null ? (
          <div className="rounded-md border border-rule bg-surface">
            {workers.map((w) => (
              <WorkerRow key={w.id} worker={w} onRefetch={() => void loadWorkers()} />
            ))}
          </div>
        ) : null}
      </section>

      {showRegister ? (
        <RegisterWorkerDialog
          onClose={() => {
            setShowRegister(false);
            void loadWorkers();
          }}
        />
      ) : null}
    </div>
  );
}
