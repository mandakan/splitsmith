import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { api, ApiError } from "@/lib/api";

// --- probeFile: MOVED verbatim from AddFootageModal.tsx (~lines 49-89). ---
// Reads duration + recorded_start from a hidden <video> so attach-after-upload
// has the metadata. Paste the existing implementation here unchanged.
async function probeFile(
  file: File,
): Promise<{ duration_s: number | null; recorded_start: string | null }> {
  return new Promise((resolve) => {
    const el = document.createElement("video");
    el.preload = "metadata";
    const url = URL.createObjectURL(file);
    // Guard: revoke exactly once across all three exit paths.
    let revoked = false;
    function revoke() {
      if (revoked) return;
      revoked = true;
      URL.revokeObjectURL(url);
    }
    // Timeout guard: if neither onloadedmetadata nor onerror fires (e.g.
    // the codec is unsupported and the browser stalls silently), revoke
    // the URL and resolve nulls after 5 seconds.
    const timer = setTimeout(() => {
      revoke();
      resolve({ duration_s: null, recorded_start: null });
    }, 5000);
    el.onloadedmetadata = () => {
      clearTimeout(timer);
      const duration = Number.isFinite(el.duration) ? el.duration : null;
      revoke();
      resolve({
        duration_s: duration,
        recorded_start:
          duration != null && file.lastModified
            ? new Date(file.lastModified - duration * 1000).toISOString()
            : null,
      });
    };
    el.onerror = () => {
      clearTimeout(timer);
      revoke();
      resolve({ duration_s: null, recorded_start: null });
    };
    el.src = url;
  });
}

export interface PendingUpload {
  id: string;
  file: File;
  slug: string;
  stages: { stage_number: number; stage_name: string }[];
  status: "queued" | "uploading" | "done" | "error" | "cancelled";
  attach?: "attaching" | "attached" | "failed";
  bytesSent: number;
  errorMessage?: string;
  controller?: AbortController;
}

interface UploadContextValue {
  uploads: PendingUpload[];
  enqueue: (
    files: FileList | File[],
    ctx: { slug: string; stages: { stage_number: number; stage_name: string }[] },
  ) => void;
  cancel: (id: string) => void;
  cancelAll: () => void;
  clearFinished: () => void;
  inFlight: boolean;
  attachTick: number;
}

const UploadContext = createContext<UploadContextValue | null>(null);

export function useUploads(): UploadContextValue {
  const ctx = useContext(UploadContext);
  if (!ctx) throw new Error("useUploads must be used within <UploadProvider>");
  return ctx;
}

export function UploadProvider({ children }: { children: ReactNode }) {
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const [attachTick, setAttachTick] = useState(0);
  const probeByFilenameRef = useRef<
    Record<string, { duration_s: number | null; recorded_start: string | null }>
  >({});
  const pumpingRef = useRef(false);
  const activeControllerRef = useRef<AbortController | null>(null);
  const [pumpTick, setPumpTick] = useState(0);
  // Mirror of `uploads` so cancel/cancelAll can read controllers without a
  // stale closure and without abusing setState as a getter.
  const uploadsRef = useRef<PendingUpload[]>([]);
  useEffect(() => {
    uploadsRef.current = uploads;
  }, [uploads]);

  const updateOne = useCallback((id: string, patch: Partial<PendingUpload>) => {
    setUploads((prev) => prev.map((u) => (u.id === id ? { ...u, ...patch } : u)));
  }, []);

  const enqueue = useCallback<UploadContextValue["enqueue"]>((files, ctx) => {
    const next: PendingUpload[] = [];
    for (const f of Array.from(files)) {
      next.push({
        id: crypto.randomUUID(),
        file: f,
        slug: ctx.slug,
        stages: ctx.stages,
        status: "queued",
        bytesSent: 0,
      });
      void probeFile(f).then((result) => {
        probeByFilenameRef.current[f.name] = result;
      });
    }
    setUploads((prev) => [...prev, ...next]);
  }, []);

  const cancel = useCallback((id: string) => {
    uploadsRef.current.find((x) => x.id === id)?.controller?.abort();
  }, []);

  const cancelAll = useCallback(() => {
    activeControllerRef.current?.abort();
    uploadsRef.current.forEach((u) => u.controller?.abort());
    setUploads((prev) =>
      prev.map((u) => (u.status === "queued" ? { ...u, status: "cancelled" } : u)),
    );
  }, []);

  const clearFinished = useCallback(() => {
    setUploads((prev) =>
      prev.filter(
        (u) => u.status === "queued" || u.status === "uploading",
      ),
    );
  }, []);

  // Auto-attach a finished object to its shooter's project immediately, so a
  // completed upload is never orphaned. covers_stages omitted -> unassigned
  // tray. Bumps attachTick on success so the Ingest page reloads. Never throws.
  const autoAttach = useCallback(
    async (
      slug: string,
      result: { filename: string; sha256: string | null; size: number },
      probe: { duration_s: number | null; recorded_start: string | null } | undefined,
      id: string,
    ) => {
      updateOne(id, { attach: "attaching" });
      try {
        await api.attachRawVideo(slug, {
          filename: result.filename,
          sha256: result.sha256,
          size_bytes: result.size,
          duration_seconds: probe?.duration_s ?? undefined,
          recorded_start: probe?.recorded_start ?? undefined,
        });
        updateOne(id, { attach: "attached" });
        setAttachTick((t) => t + 1);
      } catch {
        updateOne(id, { attach: "failed" });
      }
    },
    [updateOne],
  );

  // Pump one file at a time (single active XHR). pumpingRef is a load-bearing
  // re-entrancy lock: starting a file flips queued -> uploading, which mutates
  // uploads and re-runs this effect; without the lock the re-run starts the
  // next file too. Abort ONLY on the per-row cancel / cancelAll, never on a
  // pump re-run. No client-side hashing (multi-GB files OOM the tab; the server
  // digests on receipt).
  useEffect(() => {
    if (pumpingRef.current) return;
    const next = uploads.find((u) => u.status === "queued");
    if (!next) return;
    pumpingRef.current = true;

    void (async () => {
      const controller = new AbortController();
      activeControllerRef.current = controller;
      updateOne(next.id, { status: "uploading", bytesSent: 0, controller });
      try {
        const result = await api.uploadRawFile(next.file, {
          signal: controller.signal,
          onProgress: (loaded) => updateOne(next.id, { bytesSent: loaded }),
        });
        updateOne(next.id, { status: "done", bytesSent: next.file.size });
        const probe = probeByFilenameRef.current[next.file.name];
        await autoAttach(next.slug, result, probe, next.id);
      } catch (err) {
        if (err instanceof ApiError && err.detail === "upload cancelled") {
          updateOne(next.id, { status: "cancelled" });
        } else {
          const msg = err instanceof ApiError ? err.detail : String(err);
          updateOne(next.id, { status: "error", errorMessage: msg });
        }
      } finally {
        activeControllerRef.current = null;
        pumpingRef.current = false;
        setPumpTick((t) => t + 1);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploads, pumpTick]);

  const inFlight = uploads.some(
    (u) => u.status === "queued" || u.status === "uploading",
  );

  // Warn before reload / tab-close while uploads run. The queue is in-memory
  // with no resume, so a stray navigation loses in-flight and queued files.
  useEffect(() => {
    if (!inFlight) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [inFlight]);

  return (
    <UploadContext.Provider
      value={{ uploads, enqueue, cancel, cancelAll, clearFinished, inFlight, attachTick }}
    >
      {children}
    </UploadContext.Provider>
  );
}
