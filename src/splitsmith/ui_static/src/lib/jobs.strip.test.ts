import { describe, expect, it } from "vitest";

import type { Job } from "./api";
import { stripVisible } from "./jobs";

const job = (kind: string, status: Job["status"]): Job =>
  ({ id: `${kind}-${status}`, kind, status, match_id: "m" }) as Job;

describe("stripVisible", () => {
  it("hides automatic syncs unless they failed", () => {
    const jobs = [
      job("auto_sync", "running"),
      job("auto_sync", "pending"),
      job("auto_sync", "failed"),
      job("sync_match", "running"),
      job("trim", "running"),
    ];
    expect(stripVisible(jobs).map((j) => j.id)).toEqual([
      "auto_sync-failed",
      "sync_match-running",
      "trim-running",
    ]);
  });
});
