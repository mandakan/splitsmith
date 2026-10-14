/**
 * Derived audio a job has not made yet (spec 2026-10-14). A request never
 * cuts audio itself: when the WAV is missing the server queues the job and
 * answers 202 `{ pending: true, active_job, job_id }`. `whenReady` asks
 * again until the answer is ready, and the page shows "Preparing audio"
 * meanwhile instead of drawing anything partial.
 */

export interface Pending {
  pending: true;
  active_job?: boolean;
  job_id?: string | null;
}

export function isPending(x: unknown): x is Pending {
  return typeof x === "object" && x !== null && (x as { pending?: unknown }).pending === true;
}

/** Wait before asking again: 1, 2, 4 s, then every 5 s. */
export function pendingDelay(attempt: number): number {
  return attempt < 3 ? 1000 * 2 ** attempt : 5000;
}

/** Long enough for a job behind a queue; past it, the page says so. */
export const PENDING_GIVE_UP_MS = 120_000;

/** Still pending once the budget ran out. */
export class StillPendingError extends Error {
  constructor() {
    super("The audio is still being prepared.");
    this.name = "StillPendingError";
  }
}

export interface WhenReadyOptions {
  /** Called once, on the first pending answer. */
  onPending?: () => void;
  signal?: AbortSignal;
  giveUpMs?: number;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

const realSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export async function whenReady<T>(fetch: () => Promise<T | Pending>, opts: WhenReadyOptions = {}): Promise<T> {
  const sleep = opts.sleep ?? realSleep;
  const now = opts.now ?? Date.now;
  const giveUp = now() + (opts.giveUpMs ?? PENDING_GIVE_UP_MS);
  for (let attempt = 0; ; attempt++) {
    const answer = await fetch();
    if (!isPending(answer)) return answer;
    if (attempt === 0) opts.onPending?.();
    if (opts.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    if (now() >= giveUp) throw new StillPendingError();
    await sleep(pendingDelay(attempt));
    if (opts.signal?.aborted) throw new DOMException("Aborted", "AbortError");
  }
}
