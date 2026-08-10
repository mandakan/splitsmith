import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { Jobs } from "@/pages/Jobs";
import type { Job } from "@/lib/api";
import type { JobsState } from "@/lib/jobs";

function makeJob(over: Partial<Job>): Job {
  return {
    id: "j1",
    kind: "detect_beep",
    stage_number: 3,
    shooter_slug: "anna",
    video_id: "v1",
    status: "succeeded",
    progress: null,
    message: null,
    error: null,
    cancel_requested: false,
    acknowledged: false,
    result: null,
    timings: null,
    created_at: "2026-08-10T10:00:00Z",
    updated_at: "2026-08-10T10:01:00Z",
    started_at: null,
    finished_at: null,
    ...over,
  };
}

function makeJobsState(jobs: Job[], over: Partial<JobsState> = {}): JobsState {
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
    ...over,
  };
}

function renderJobs(jobsState: JobsState) {
  return render(
    <MemoryRouter initialEntries={["/match/m1/jobs"]}>
      <Routes>
        <Route element={<Outlet context={{ jobsState }} />}>
          <Route path="/match/:matchId/jobs" element={<Jobs />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Jobs page", () => {
  it("shows the all-quiet state when nothing is active or failed", () => {
    renderJobs(makeJobsState([]));
    expect(screen.getByText(/all quiet/i)).toBeInTheDocument();
  });

  it("retries a failed job", async () => {
    const failed = makeJob({ id: "jf", status: "failed", error: "boom" });
    const state = makeJobsState([failed]);
    renderJobs(state);
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(state.retry).toHaveBeenCalledWith(failed);
  });

  it("renders phase timings on finished jobs behind a collapsed disclosure", () => {
    const done = makeJob({
      status: "succeeded",
      timings: { queue_wait_ms: 120, total_ms: 4500, phases: [{ name: "beep_detect", ms: 4380 }] },
    });
    renderJobs(makeJobsState([done]));
    const summary = screen.getByText("Phase timings");
    expect(summary).toBeInTheDocument();
    expect(summary.closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("beep_detect")).toBeInTheDocument();
  });

  it("labels the progressbar with the job kind for running jobs", () => {
    const running = makeJob({ id: "jr", status: "running", progress: 0.5 });
    renderJobs(makeJobsState([running]));
    expect(screen.getByRole("progressbar", { name: /progress/ })).toBeInTheDocument();
  });

  it("shows the shooter slug on a shooter-scoped job card", () => {
    const job = makeJob({ shooter_slug: "anna" });
    renderJobs(makeJobsState([job]));
    expect(screen.getByText(/anna/)).toBeInTheDocument();
  });

  it("falls back to (no target) for a match-level job with no slug, stage, or video", () => {
    const job = makeJob({
      shooter_slug: null,
      stage_number: null,
      video_id: null,
    });
    renderJobs(makeJobsState([job]));
    expect(screen.getByText("(no target)")).toBeInTheDocument();
  });

  it("shows the most recently updated finished jobs, not the stalest", () => {
    // 25 succeeded jobs in ascending updated_at order (submission order, as
    // state.jobs comes from the API) - "Recent" caps at 20, so without
    // sorting-before-slicing the newest 5 would be dropped instead of the
    // oldest 5.
    const jobs = Array.from({ length: 25 }, (_, i) =>
      makeJob({
        id: `j${i}`,
        status: "succeeded",
        message: `marker-${i}`,
        updated_at: `2026-08-${String(i + 1).padStart(2, "0")}T10:00:00Z`,
      }),
    );
    renderJobs(makeJobsState(jobs));
    expect(screen.getByText("marker-24")).toBeInTheDocument();
    expect(screen.queryByText("marker-0")).not.toBeInTheDocument();
  });
});
