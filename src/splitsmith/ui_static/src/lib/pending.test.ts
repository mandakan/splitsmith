import { describe, expect, it, vi } from "vitest";

import { isPending, JobFailedError, pendingDelay, StillPendingError, whenReady } from "@/lib/pending";

describe("isPending", () => {
  it("is the server's 202 answer and nothing else", () => {
    expect(isPending({ pending: true, active_job: true, job_id: "j" })).toBe(true);
    expect(isPending({ peaks: [], duration: 1 })).toBe(false);
    expect(isPending(null)).toBe(false);
    expect(isPending({ pending: false })).toBe(false);
  });
});

describe("pendingDelay", () => {
  it("backs off 1, 2, 4 s, then every 5 s", () => {
    expect([0, 1, 2, 3, 4, 10].map(pendingDelay)).toEqual([1000, 2000, 4000, 5000, 5000, 5000]);
  });
});

describe("whenReady", () => {
  const instant = () => Promise.resolve();

  it("resolves at once when the first answer is ready, without calling onPending", async () => {
    const onPending = vi.fn();
    await expect(whenReady(() => Promise.resolve({ ok: 1 }), { onPending, sleep: instant })).resolves.toEqual({
      ok: 1,
    });
    expect(onPending).not.toHaveBeenCalled();
  });

  it("asks again while pending, says so once, and resolves with the ready answer", async () => {
    const answers = [{ pending: true as const }, { pending: true as const }, { ok: 2 }];
    const fetch = vi.fn(() => Promise.resolve(answers.shift()!));
    const onPending = vi.fn();
    const slept: number[] = [];
    const result = await whenReady(fetch, {
      onPending,
      sleep: (ms) => {
        slept.push(ms);
        return Promise.resolve();
      },
    });
    expect(result).toEqual({ ok: 2 });
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(onPending).toHaveBeenCalledTimes(1);
    expect(slept).toEqual([1000, 2000]);
  });

  it("gives up with StillPendingError once the budget is spent", async () => {
    let t = 0;
    const p = whenReady(() => Promise.resolve({ pending: true as const }), {
      sleep: (ms) => {
        t += ms;
        return Promise.resolve();
      },
      now: () => t,
      giveUpMs: 10_000,
    });
    await expect(p).rejects.toBeInstanceOf(StillPendingError);
  });

  it("stops when aborted, so a page that moved on does not keep asking", async () => {
    const ctl = new AbortController();
    const fetch = vi.fn(() => {
      ctl.abort();
      return Promise.resolve({ pending: true as const });
    });
    await expect(whenReady(fetch, { signal: ctl.signal, sleep: instant })).rejects.toThrow(/aborted/i);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("stops with the job's own error once the job it waited on failed, and asks no more", async () => {
    // Review, 2026-10-14: a failed job was invisible; each poll found no
    // active job and queued another, about 25 per open page.
    const fetch = vi.fn(() => Promise.resolve({ pending: true as const, job_id: "job-1" }));
    const jobError = vi.fn(async (id: string) => (id === "job-1" ? "ffmpeg failed: no audio stream" : null));
    const p = whenReady(fetch, { sleep: instant, jobError });
    await expect(p).rejects.toBeInstanceOf(JobFailedError);
    await expect(p).rejects.toThrow("ffmpeg failed: no audio stream");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("keeps asking while the job it waits on is still running", async () => {
    const answers = [{ pending: true as const, job_id: "job-1" }, { ok: 3 }];
    const jobError = vi.fn(async () => null);
    await expect(
      whenReady(() => Promise.resolve(answers.shift()!), { sleep: instant, jobError }),
    ).resolves.toEqual({ ok: 3 });
    expect(jobError).toHaveBeenCalledWith("job-1");
  });
});
