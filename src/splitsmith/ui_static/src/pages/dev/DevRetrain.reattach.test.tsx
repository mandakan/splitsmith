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
