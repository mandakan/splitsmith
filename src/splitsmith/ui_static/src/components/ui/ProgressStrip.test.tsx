import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Job } from "@/lib/api";
import type { JobsState } from "@/lib/jobs";

import { ProgressStrip } from "./ProgressStrip";

function job(over: Partial<Job>): Job {
  return {
    id: "j1",
    kind: "shot_detect",
    match_id: "m",
    stage_number: 9,
    shooter_slug: "s",
    video_id: null,
    status: "running",
    progress: 0.6,
    message: null,
    error: null,
    cancel_requested: false,
    acknowledged: false,
    ...over,
  } as Job;
}

function state(jobs: Job[]): JobsState {
  return {
    jobs,
    running: jobs.filter((j) => j.status === "running"),
    pending: jobs.filter((j) => j.status === "pending"),
    failed: jobs.filter((j) => j.status === "failed" && !j.acknowledged),
    error: null,
    refresh: vi.fn(),
    acknowledge: vi.fn(),
    acknowledgeAll: vi.fn(),
    cancel: vi.fn(),
    retry: vi.fn(),
  };
}

describe("ProgressStrip", () => {
  it("renders nothing when idle", () => {
    const { container } = render(
      <ProgressStrip state={state([])} onOpen={() => {}} onDismissFailed={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("names the running job and stage, shows progress and the count", () => {
    render(
      <ProgressStrip
        state={state([job({}), job({ id: "j2", status: "pending", stage_number: 10 })])}
        onOpen={() => {}}
        onDismissFailed={() => {}}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/detect shots.*stage 9/i);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "60");
    expect(screen.getByText(/1 of 2/)).toBeInTheDocument();
  });

  it("shows a failed job until dismissed", () => {
    const dismiss = vi.fn();
    const failed = job({ status: "failed", error: "boom", progress: null });
    render(
      <ProgressStrip state={state([failed])} onOpen={() => {}} onDismissFailed={dismiss} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(/failed/i);
    screen.getByRole("button", { name: /dismiss/i }).click();
    expect(dismiss).toHaveBeenCalledWith(failed);
  });
});
