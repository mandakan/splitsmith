import { describe, expect, it } from "vitest";

import type { BeepQueueItem } from "./api";
import {
  nextInQueue,
  queueLine,
  queueOrder,
  queuePlace,
  reviewEntryText,
} from "./beepQueue";

function item(
  slug: string,
  stage: number,
  role: "primary" | "secondary",
  status: BeepQueueItem["status"] = "unreviewed",
): BeepQueueItem {
  return {
    slug,
    shooter_name:
      slug === "mathias"
        ? "Mathias Axell"
        : slug === "martin"
          ? "Martin Engström"
          : "Anton Johansson",
    stage_number: stage,
    stage_name: `S${stage}`,
    role,
    video_id: `${slug}-${stage}-${role}`,
    video_path: `raw/${slug}_${stage}_${role}.MOV`,
    beep_time: 4.2,
    beep_confidence: 0.9,
    beep_reviewed: status === "confirmed",
    status,
    alt_candidates: [],
    proxy_ready: true,
    snippet_ready: false,
    trim_stale: false,
  } as BeepQueueItem;
}

const key = (it: BeepQueueItem) => ({
  slug: it.slug,
  stageNumber: it.stage_number,
  videoId: it.video_id,
});

// As the server lists them: stage by stage, the match's shooters in order.
const ITEMS = [
  item("mathias", 1, "primary"),
  item("mathias", 1, "secondary"),
  item("martin", 1, "primary"),
  item("anton", 1, "primary", "confirmed"),
  item("mathias", 2, "primary", "missing"),
  item("anton", 2, "secondary"),
  item("martin", 2, "primary", "low_confidence"),
];

describe("beep review queue", () => {
  it("puts every primary first (stage, then shooter), secondaries after, confirmed ones out", () => {
    expect(queueOrder(ITEMS).map((it) => it.video_id)).toEqual([
      "mathias-1-primary",
      "martin-1-primary",
      "mathias-2-primary",
      "martin-2-primary",
      "mathias-1-secondary",
      "anton-2-secondary",
    ]);
  });

  it("knows where a beep sits and says so", () => {
    const order = queueOrder(ITEMS);
    expect(queuePlace(order, key(ITEMS[4]))).toEqual({ position: 3, total: 6 });
    expect(queuePlace(order, key(ITEMS[3]))).toBeNull();
    expect(queueLine(ITEMS[1], { position: 5, total: 6 })).toBe(
      "Beep 5 of 6 · Mathias Axell · Stage 01 · mathias_1_secondary.MOV · secondary",
    );
    expect(reviewEntryText(14)).toBe("Review beeps · 14 to confirm");
  });

  it("moves to the next beep, crossing stages and shooters, wrapping to the one left for later", () => {
    const order = queueOrder(ITEMS);
    expect(nextInQueue(order, key(ITEMS[0]))?.video_id).toBe(
      "martin-1-primary",
    );
    expect(nextInQueue(order, key(ITEMS[6]))?.video_id).toBe(
      "mathias-1-secondary",
    );
    // The last one wraps to the front, where a beep left for later waits.
    expect(nextInQueue(order, key(ITEMS[5]))?.video_id).toBe(
      "mathias-1-primary",
    );
    // A beep no longer waiting starts from the front.
    expect(nextInQueue(order, key(ITEMS[3]))?.video_id).toBe(
      "mathias-1-primary",
    );
  });

  it("has no next once the current beep is the only one waiting", () => {
    const one = [item("martin", 3, "primary")];
    expect(nextInQueue(queueOrder(one), key(one[0]))).toBeNull();
  });
});
