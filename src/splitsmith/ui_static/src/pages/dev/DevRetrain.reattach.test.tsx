/**
 * The rebuild runs server-side, so remounting the Retrain page must
 * re-attach to an in-flight ``rebuild_calibration`` job instead of
 * presenting a fresh "Run build" while the jobs rail shows one running
 * (the reported bug: navigate away and back, progress "lost").
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DeveloperShellOutletContext } from "@/components/developer/DeveloperShell";
import { api } from "@/lib/api";
import { DevRetrain } from "@/pages/dev/DevRetrain";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listJobs: vi.fn(),
      getJob: vi.fn(),
      rebuildLabCalibration: vi.fn(),
    },
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderRetrain() {
  const outletContext: DeveloperShellOutletContext = { model: null, refresh: () => {} };
  return render(
    <MemoryRouter initialEntries={["/dev/retrain"]}>
      <Routes>
        <Route element={<Outlet context={outletContext} />}>
          <Route path="dev/retrain" element={<DevRetrain />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("DevRetrain re-attach", () => {
  it("adopts an in-flight rebuild job on mount", async () => {
    vi.mocked(api.listJobs).mockResolvedValue([
      {
        id: "job-42",
        kind: "rebuild_calibration",
        status: "running",
        progress: 0.4,
        message: "GBDT 5-fold CV",
      } as never,
    ]);
    vi.mocked(api.getJob).mockResolvedValue({
      id: "job-42",
      kind: "rebuild_calibration",
      status: "running",
      progress: 0.4,
      message: "GBDT 5-fold CV",
    } as never);

    renderRetrain();

    // The Run button reflects the adopted job instead of offering a
    // fresh start, and the log says what happened.
    expect(await screen.findByRole("button", { name: /building/i })).toBeDisabled();
    expect(screen.getByText(/re-attached to the running build/i)).toBeInTheDocument();
    expect(api.rebuildLabCalibration).not.toHaveBeenCalled();
  });

  it("stays idle when no rebuild job is running", async () => {
    vi.mocked(api.listJobs).mockResolvedValue([
      { id: "j1", kind: "shot_detect", status: "running" } as never,
      { id: "j2", kind: "rebuild_calibration", status: "succeeded" } as never,
    ]);

    renderRetrain();

    expect(await screen.findByRole("button", { name: /run build/i })).toBeEnabled();
  });
});

describe("DevRetrain compare strip", () => {
  it("shows previous vs new-build numbers from the job's snapshot", async () => {
    const userEvent = (await import("@testing-library/user-event")).default;
    vi.mocked(api.listJobs).mockResolvedValue([]);
    vi.mocked(api.rebuildLabCalibration).mockResolvedValue({
      id: "job-7",
      kind: "rebuild_calibration",
      status: "running",
    } as never);
    const snap = (builtAt: string, fixtures: number, f1: number) => ({
      built_at: builtAt,
      fixture_count: fixtures,
      target_recall: 0.95,
      metrics_by_class: {
        handheld: { voter_c_precision_cv: 0.99, voter_c_recall_cv: 0.95, voter_c_f1_cv: f1 },
      },
    });
    vi.mocked(api.getJob).mockResolvedValue({
      id: "job-7",
      kind: "rebuild_calibration",
      status: "succeeded",
      progress: 1,
      message: "calibration rebuilt",
      result: {
        before: snap("2026-05-13T00:00:00+00:00", 64, 0.917),
        after: snap("2026-08-17T00:00:00+00:00", 124, 0.947),
      },
    } as never);

    renderRetrain();
    await userEvent.click(await screen.findByRole("button", { name: /run build/i }));

    // Poll interval is 1s; the strip flips once the succeeded job lands.
    expect(await screen.findByText(/new build \(live\)/i, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText(/previous/i)).toBeInTheDocument();
    expect(screen.getByText("0.917")).toBeInTheDocument();
    expect(screen.getByText("0.947")).toBeInTheDocument();
    // And the honest CTA replaced the promote-to-shipped fiction.
    expect(screen.getByRole("button", { name: /validate the new build/i })).toBeEnabled();
    expect(screen.queryByText(/promote to shipped/i)).toBeNull();
  });
});
