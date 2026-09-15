/**
 * Workers (admin) -- the server's compute pool: every self-hosted or
 * Railway worker with its state, advertised capabilities, version,
 * priority and enabled switch, plus registration of a new one.
 *
 * Server-wide rather than match-scoped, so it mounts directly under
 * RootLayout (#550) with no shell nav; the PageHeader back link and the
 * brand in the global bar are its way home. Built on the visual budget
 * primitives (spec 2026-09-13): one PageHeader, one primary action, a
 * hairline Table for the roster, Chips for state and capabilities, a
 * destructive outline for delete.
 */
import { useEffect, useRef, useState } from "react";
import { ArrowUpCircle, Trash2 } from "lucide-react";

import { RegisterWorkerSheet } from "@/components/admin/RegisterWorkerSheet";
import { Button } from "@/components/ui/button";
import { Chip, type ChipTick } from "@/components/ui/Chip";
import { Table, Td, Th, Tr } from "@/components/ui/DataTable";
import { inputClass } from "@/components/ui/Field";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api, type WorkerInfo, type WorkerView } from "@/lib/api";
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

/** One hue per meaning: green is up, amber is waiting on the agent to
 *  connect, everything else is neutral (disabled gets a muted tick so it
 *  reads as switched off rather than merely unreachable). */
function statusChip(status: WorkerView["status"]): { tone: "ok" | "warn" | "neutral"; tick?: ChipTick } {
  switch (status) {
    case "online":
      return { tone: "ok" };
    case "pending":
      return { tone: "warn" };
    case "disabled":
      return { tone: "neutral", tick: "muted" };
    case "offline":
      return { tone: "neutral" };
  }
}

/** True when a worker runs a release behind the server's own version.
 *
 * A missing worker version (pending / never reported) or an unparseable
 * component is treated as "not outdated" -- we never nag on uncertainty, and a
 * worker ahead of the server (self-hosted updated before the server deploy) is
 * not flagged either. */
function isOutdated(version: string | null, serverVersion: string): boolean {
  if (!version || !serverVersion) return false;
  const parse = (v: string) =>
    v.replace(/^v/, "").split(".").map((n) => parseInt(n, 10));
  const a = parse(version);
  const b = parse(serverVersion);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    if (Number.isNaN(x) || Number.isNaN(y)) return false;
    if (x !== y) return x < y;
  }
  return false;
}

/** GPU / NVENC / CUDA chips from a worker's advertised capabilities (#796).
 *
 * ``capabilities`` is absent on workers that never advertised any (e.g. Railway
 * rows) -- render nothing there. A worker that advertised an all-false bundle
 * is a probed CPU-only box, which we label explicitly. */
function CapabilityChips({ info }: { info: WorkerInfo | null }) {
  const caps = info?.capabilities;
  if (!caps) return null;
  const chips = [];
  if (caps.gpu_name) {
    chips.push(
      <Chip key="gpu" title="GPU model" className="whitespace-nowrap">
        {caps.gpu_name}
      </Chip>,
    );
  }
  if (caps.cuda_ep) {
    chips.push(
      <Chip key="cuda" tick="fire" title="onnxruntime CUDA execution provider available">
        CUDA
      </Chip>,
    );
  }
  if (caps.nvenc_h264) {
    chips.push(
      <Chip key="nvenc" tick="fire" title="ffmpeg h264_nvenc usable">
        NVENC
      </Chip>,
    );
  }
  if (chips.length === 0) {
    chips.push(
      <Chip key="cpu" tick="muted" title="No GPU acceleration advertised">
        CPU
      </Chip>,
    );
  }
  return <div className="flex flex-wrap items-center gap-1">{chips}</div>;
}

const COLUMNS = 8;

interface WorkerRowProps {
  worker: WorkerView;
  serverVersion: string;
  /** Splice the WorkerView a PATCH returned back into the roster - the
   *  #579 sweep: no blind list refetch when the mutation already hands
   *  back the updated resource. */
  onUpdated: (worker: WorkerView) => void;
  /** Full roster refetch - only for delete, which returns void. */
  onRefetch: () => void;
}

function WorkerRow({ worker, serverVersion, onUpdated, onRefetch }: WorkerRowProps) {
  const outdated = isOutdated(worker.version, serverVersion);
  const status = statusChip(worker.status);
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
      const updated = await api.adminUpdateWorker(worker.id, { priority: n });
      onUpdated(updated);
      // Re-sync the draft from the response in case the server clamped
      // the value.
      setPriorityDraft(String(updated.priority));
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
      const updated = await api.adminUpdateWorker(worker.id, { enabled: !worker.enabled });
      onUpdated(updated);
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
    <>
      <Tr className={rowError ? "border-b-0" : undefined}>
        <Td kind="name">
          <div className="truncate">{worker.name}</div>
          <div className="mt-0.5 whitespace-nowrap text-sm text-muted">
            {worker.kind === "self_hosted" ? "Self-hosted" : "Railway"}
            {!worker.registered ? <span className="ml-2 text-live">unregistered</span> : null}
          </div>
        </Td>
        <Td>
          <Chip tone={status.tone} tick={status.tick}>
            {worker.status}
          </Chip>
        </Td>
        <Td>
          <CapabilityChips info={worker.info} />
        </Td>
        <Td
          className={cn("numeral whitespace-nowrap", outdated ? "text-live" : "text-ink-2")}
          title={outdated ? `Update available - server is on v${serverVersion}` : "Worker software version"}
        >
          <span className="inline-flex items-center gap-1">
            {worker.version ? `v${worker.version}` : "unknown"}
            {outdated ? (
              <ArrowUpCircle
                className="size-3.5"
                aria-label={`update available (server is on v${serverVersion})`}
              />
            ) : null}
          </span>
        </Td>
        <Td className="numeral whitespace-nowrap text-muted" title={worker.last_seen_at ?? undefined}>
          {relativeTime(worker.last_seen_at)}
        </Td>
        <Td kind="num">
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
            className={cn(inputClass, "numeral w-20 py-1 text-right")}
            aria-label={`Priority for ${worker.name}`}
          />
        </Td>
        <Td className="text-center">
          <input
            id={`enabled-${worker.id}`}
            type="checkbox"
            checked={worker.enabled}
            onChange={() => void toggleEnabled()}
            disabled={patching}
            aria-label={`${worker.enabled ? "Disable" : "Enable"} ${worker.name}`}
            className="size-4 cursor-pointer accent-done disabled:cursor-not-allowed disabled:opacity-50"
          />
        </Td>
        <Td className="whitespace-nowrap text-right">
          {deleteArmed ? (
            <span className="inline-flex items-center gap-1">
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
            </span>
          ) : (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => setDeleteArmed(true)}
              disabled={patching}
              aria-label={`Delete ${worker.name}`}
            >
              <Trash2 aria-hidden="true" />
            </Button>
          )}
        </Td>
      </Tr>
      {rowError ? (
        <Tr>
          <Td colSpan={COLUMNS} role="alert" className="pt-0 text-sm text-destructive">
            {rowError}
          </Td>
        </Tr>
      ) : null}
    </>
  );
}

export function AdminWorkers() {
  const { user } = useAuth();
  const [workers, setWorkers] = useState<WorkerView[] | null>(null);
  const [serverVersion, setServerVersion] = useState("");
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
      if (mountedRef.current) {
        setWorkers(resp.workers);
        setServerVersion(resp.server_version);
      }
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

  const back = { label: "Matches", to: "/pick" };

  if (!user?.is_admin) {
    return (
      <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-4 px-7 py-5">
        <PageHeader title="Workers" back={back} />
        <p className="text-md text-muted">Admin access required.</p>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-4 px-7 py-5">
      <PageHeader
        title="Workers"
        sub={serverVersion ? `Server v${serverVersion}` : undefined}
        back={back}
        actions={
          <Button type="button" variant="primary" size="sm" onClick={() => setShowRegister(true)}>
            Register worker
          </Button>
        }
      />

      {fetchError ? (
        <p role="alert" className="text-sm text-led-text">
          {fetchError}
        </p>
      ) : null}

      <section aria-label="Worker list">
        {workers === null && !fetchError ? (
          <p className="text-sm text-muted">Loading...</p>
        ) : workers !== null && workers.length === 0 ? (
          <p className="rounded-[10px] border border-rule px-3 py-2.5 text-sm text-muted">No workers registered.</p>
        ) : workers !== null ? (
          <Table>
            <thead>
              <tr className="whitespace-nowrap">
                <Th>Worker</Th>
                <Th>Status</Th>
                <Th>Capabilities</Th>
                <Th>Version</Th>
                <Th>Last seen</Th>
                <Th align="right">Priority</Th>
                <Th className="text-center">Enabled</Th>
                <Th align="right">
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {workers.map((w) => (
                <WorkerRow
                  key={w.id}
                  worker={w}
                  serverVersion={serverVersion}
                  onUpdated={(next) =>
                    setWorkers((cur) =>
                      cur ? cur.map((x) => (x.id === next.id ? next : x)) : cur,
                    )
                  }
                  onRefetch={() => void loadWorkers()}
                />
              ))}
            </tbody>
          </Table>
        ) : null}
      </section>

      {/* Mounted only while open so a second registration starts on a
          fresh form rather than the previous one's success step. */}
      {showRegister ? (
        <RegisterWorkerSheet
          open
          onClose={() => {
            setShowRegister(false);
            void loadWorkers();
          }}
        />
      ) : null}
    </div>
  );
}
