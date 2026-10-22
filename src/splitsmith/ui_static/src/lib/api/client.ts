/** The HTTP client under ``api``: request, errors, scoping, uploads (split out of ``lib/api.ts``, #1580). */

import type {
  Job,
  RawUploadResult,
} from "../api";

/** The error of a derive job a 202 named, once it failed (``lib/pending``).
 *  An unreadable job reads as still running: the wait's own budget ends it. */
export async function failedJobError(jobId: string): Promise<string | null> {
  try {
    const job = await request<Job>(`/api/me/jobs/${encodeURIComponent(jobId)}`);
    return job.status === "failed" ? job.error || "Preparing the audio failed." : null;
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    public body: unknown = null,
  ) {
    super(`${status}: ${detail}`);
  }
}

/** True when ``err`` is a 401 from the API -- i.e. the caller is not
 *  authenticated. The auth context uses this to distinguish "logged out"
 *  (expected, render the login surface) from a real server error. */
export function isUnauthorized(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}

/** Match id parsed from the current URL.
 *
 * The SPA's match-scoped routes live under ``/match/:matchId/...`` (#353
 * Phase 3). When the operator is on one of those URLs we route API
 * traffic through ``/api/matches/{matchId}/...`` so the request lands
 * on the right match without depending on the server's bound singleton.
 * Returns ``null`` for non-match URLs (picker, dev mode, root).
 *
 * Exported for the rare test / debug case; production code consults it
 * via :func:`request` automatically. */
export function currentMatchIdFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  const m = window.location.pathname.match(/^\/match\/([^/]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

/** Share token parsed from the current URL.
 *
 * Public share routes live under ``/share/:token/...``. When the viewer
 * is on one of those URLs, match-scoped API traffic routes through
 * ``/api/share/{token}/...`` - the anonymous, token-authorized read
 * path - instead of the session-authorized match prefix. */
export function currentShareTokenFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  const m = window.location.pathname.match(/^\/share\/([^/]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

/** Prefixes that ride on the per-request match scope. Other ``/api/`` paths
 *  (``/api/health``, ``/api/me/*``, ``/api/server/*``, ``/api/lab/*``,
 *  ``/api/files/*``, ``/api/fs/*``, ``/api/dev/*``, etc.) stay on their
 *  legacy bare paths because they are operator-global or unbound. Jobs are
 *  per-user resources, not per-match: the jobs list is intentionally
 *  cross-match, so ``/api/me/jobs/*`` (get/cancel/acknowledge/retry) is
 *  deliberately NOT scoped here - retry rebinds the job's own original
 *  match server-side instead (see api.retryJob), so scoping the request to
 *  whatever match the SPA happens to be viewing would be both wrong and
 *  unnecessary. */
export const MATCH_SCOPED_PREFIXES = ["/api/shooters/", "/api/match/"];

export function scopeRequestPath(path: string): string {
  if (!MATCH_SCOPED_PREFIXES.some((p) => path.startsWith(p))) return path;
  const shareToken = currentShareTokenFromLocation();
  if (shareToken) {
    return `/api/share/${encodeURIComponent(shareToken)}${path.substring(4)}`;
  }
  const matchId = currentMatchIdFromLocation();
  if (!matchId) return path;
  return `/api/matches/${encodeURIComponent(matchId)}${path.substring(4)}`;
}

export async function request<T>(
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<T> {
  const { json, ...rest } = init ?? {};
  const headers: HeadersInit = {
    Accept: "application/json",
    ...(rest.headers ?? {}),
  };
  if (json !== undefined) {
    (headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const resp = await fetch(scopeRequestPath(path), {
    ...rest,
    headers,
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    let rawDetail: unknown = null;
    try {
      const body = await resp.json();
      if (body && typeof body === "object" && "detail" in body) {
        rawDetail = (body as { detail: unknown }).detail;
        detail =
          typeof rawDetail === "string" ? rawDetail : JSON.stringify(rawDetail);
      }
    } catch {
      /* ignore */
    }
    // Server-state drift recovery: a 409 with ``code: "no_project"`` means
    // the SPA's view of the world is stale -- the server has no bound
    // project (dev-server restart, explicit unbind, etc.). Fire a custom
    // event so the match shell can force a health refresh and redirect
    // to /pick. Without this, the page just shows broken-everything with
    // no explanation and the jobs rail quietly disappears. ``not_a_match``
    // doesn't trigger the redirect; that's a per-route concern (the
    // bound project is fine, just legacy).
    if (
      resp.status === 409 &&
      rawDetail &&
      typeof rawDetail === "object" &&
      (rawDetail as { code?: unknown }).code === "no_project" &&
      typeof window !== "undefined"
    ) {
      window.dispatchEvent(new CustomEvent("splitsmith:no-project"));
    }
    throw new ApiError(resp.status, detail, rawDetail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

/** Files at or above this size use the presigned multipart path (direct
 *  browser -> R2); smaller files take the single-shot serve-proxied
 *  upload. Cloud Run refuses an HTTP/1 request body over 32 MiB before it
 *  reaches serve, so the threshold sits well below that (and equals the
 *  16 MiB part size, so a multipart upload is never smaller than a part). */
export const MULTIPART_THRESHOLD_BYTES = 16 * 1024 * 1024;

/** PUT one part's bytes straight to R2 via its presigned URL. Resolves
 *  with the part's ETag (which the complete call needs). The ETag header
 *  is only readable when the R2 bucket CORS exposes it
 *  (Access-Control-Expose-Headers: ETag). */
export function putUploadPart(
  url: string,
  blob: Blob,
  opts: { onProgress?: (bytesSent: number) => void; signal?: AbortSignal },
): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url, true);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) opts.onProgress?.(e.loaded);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const etag = xhr.getResponseHeader("ETag");
        if (!etag) {
          reject(
            new ApiError(
              0,
              "R2 returned no ETag for the part -- check the bucket CORS exposes the ETag header",
              null,
            ),
          );
          return;
        }
        resolve(etag);
        return;
      }
      reject(new ApiError(xhr.status, `part upload failed: ${xhr.statusText}`, null));
    };
    xhr.onerror = () => reject(new ApiError(0, "network error during part upload", null));
    xhr.onabort = () => reject(new ApiError(0, "upload cancelled", null));
    if (opts.signal) {
      if (opts.signal.aborted) {
        reject(new ApiError(0, "upload cancelled", null));
        return;
      }
      opts.signal.addEventListener("abort", () => xhr.abort());
    }
    xhr.send(blob);
  });
}

/** Upload a large file by splitting it into parts and PUTting each
 *  straight to R2 via presigned URLs, so no bytes pass through the API
 *  process. ``onProgress`` reports cumulative bytes across all parts. On
 *  any failure the in-progress multipart is best-effort aborted so R2
 *  doesn't keep staged parts. */
export async function uploadRawMultipart(
  file: File,
  opts: { onProgress?: (bytesSent: number, total: number) => void; signal?: AbortSignal },
): Promise<RawUploadResult> {
  const create = await request<{
    upload_id: string;
    filename: string;
    key: string;
    part_size: number;
  }>("/api/me/raw/upload/multipart/create", {
    method: "POST",
    json: { filename: file.name },
  });
  const { upload_id, filename, part_size } = create;
  const partCount = Math.max(1, Math.ceil(file.size / part_size));
  const parts: { part_number: number; etag: string }[] = [];
  let uploadedBytes = 0;

  try {
    for (let i = 0; i < partCount; i++) {
      if (opts.signal?.aborted) throw new ApiError(0, "upload cancelled", null);
      const start = i * part_size;
      const blob = file.slice(start, Math.min(start + part_size, file.size));
      const partNumber = i + 1;
      const { url } = await request<{ url: string }>(
        "/api/me/raw/upload/multipart/part-url",
        { method: "POST", json: { filename, upload_id, part_number: partNumber } },
      );
      const etag = await putUploadPart(url, blob, {
        signal: opts.signal,
        onProgress: (sent) => opts.onProgress?.(uploadedBytes + sent, file.size),
      });
      uploadedBytes += blob.size;
      opts.onProgress?.(uploadedBytes, file.size);
      parts.push({ part_number: partNumber, etag });
    }
  } catch (err) {
    try {
      await request("/api/me/raw/upload/multipart/abort", {
        method: "POST",
        json: { filename, upload_id },
      });
    } catch {
      /* best-effort: R2's lifecycle rule also sweeps abandoned multiparts */
    }
    throw err;
  }

  return request<RawUploadResult>("/api/me/raw/upload/multipart/complete", {
    method: "POST",
    json: { filename, upload_id, parts },
  });
}

/** Fired on window after any mutation that changes the linked hosted
 *  account (settings save, unlink, device link). HostedAccountChip
 *  renders twice (GlobalBar and the mobile nav drawer) with independent
 *  state; the event is what keeps the copies in agreement (#736). */
export const HOSTED_ACCOUNT_CHANGED_EVENT = "splitsmith:hosted-account-changed";

export function notifyHostedAccountChanged(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(HOSTED_ACCOUNT_CHANGED_EVENT));
  }
}

/** Header carrying the anonymous commenter's opaque per-browser key
 *  (see lib/authorKey.ts). The server derives the display handle from
 *  it via HMAC - the client never sends a name. */
export const AUTHOR_KEY_HEADER = "X-Splitsmith-Author-Key";

