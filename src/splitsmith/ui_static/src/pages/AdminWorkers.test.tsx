/**
 * AdminWorkers - GPU capability badges (#796) + outdated-version highlight.
 * Covers: advertised capabilities render as badges; an all-false bundle reads
 * as CPU; a worker behind the server version gets the "update available"
 * affordance while a current one does not.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkerListResponse, WorkerView } from "@/lib/api";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    user: { id: "u1", email: "m@thias.se", display_name: null, is_admin: true },
  }),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      adminListWorkers: vi.fn(),
      adminUpdateWorker: vi.fn(),
    },
  };
});

import { api } from "@/lib/api";
import { AdminWorkers } from "@/pages/AdminWorkers";

function worker(over: Partial<WorkerView> = {}): WorkerView {
  return {
    id: "w1",
    name: "home-wsl-gpu",
    kind: "self_hosted",
    enabled: true,
    priority: 1000,
    status: "online",
    registered: true,
    last_seen_at: new Date().toISOString(),
    last_wake_at: null,
    version: "0.25.0",
    info: null,
    ...over,
  };
}

function listResp(
  workers: WorkerView[],
  server_version = "0.25.0",
): WorkerListResponse {
  return { workers, server_version };
}

describe("AdminWorkers", () => {
  beforeEach(() => {
    vi.mocked(api.adminListWorkers).mockReset();
  });

  it("renders GPU / NVENC / CUDA capability badges", async () => {
    vi.mocked(api.adminListWorkers).mockResolvedValue(
      listResp([
        worker({
          info: {
            capabilities: {
              gpu_name: "NVIDIA GeForce RTX 2070 SUPER",
              cuda_ep: true,
              nvenc_h264: true,
            },
          },
        }),
      ]),
    );
    render(<AdminWorkers />);
    expect(
      await screen.findByText("NVIDIA GeForce RTX 2070 SUPER"),
    ).toBeInTheDocument();
    expect(screen.getByText("CUDA")).toBeInTheDocument();
    expect(screen.getByText("NVENC")).toBeInTheDocument();
  });

  it("labels a probed CPU-only worker (all-false bundle)", async () => {
    vi.mocked(api.adminListWorkers).mockResolvedValue(
      listResp([
        worker({
          info: {
            capabilities: { gpu_name: null, cuda_ep: false, nvenc_h264: false },
          },
        }),
      ]),
    );
    render(<AdminWorkers />);
    expect(await screen.findByText("CPU")).toBeInTheDocument();
  });

  it("shows no capability badges when none were advertised", async () => {
    vi.mocked(api.adminListWorkers).mockResolvedValue(
      listResp([worker({ kind: "railway", info: {} })]),
    );
    render(<AdminWorkers />);
    await screen.findByText("v0.25.0");
    expect(screen.queryByText("CPU")).not.toBeInTheDocument();
    expect(screen.queryByText("CUDA")).not.toBeInTheDocument();
  });

  it("flags a worker running behind the server version", async () => {
    vi.mocked(api.adminListWorkers).mockResolvedValue(
      listResp([worker({ id: "old", name: "stale", version: "0.23.1" })], "0.25.0"),
    );
    render(<AdminWorkers />);
    expect(
      await screen.findByTitle(/update available - server is on v0\.25\.0/i),
    ).toBeInTheDocument();
  });

  it("does not flag a worker on the current version", async () => {
    vi.mocked(api.adminListWorkers).mockResolvedValue(
      listResp([worker({ version: "0.25.0" })], "0.25.0"),
    );
    render(<AdminWorkers />);
    expect(await screen.findByText("v0.25.0")).toBeInTheDocument();
    expect(screen.queryByTitle(/update available/i)).not.toBeInTheDocument();
  });

  it("does not flag a worker with no reported version", async () => {
    vi.mocked(api.adminListWorkers).mockResolvedValue(
      listResp([worker({ version: null, status: "pending", registered: false })], "0.25.0"),
    );
    render(<AdminWorkers />);
    expect(await screen.findByText("unknown")).toBeInTheDocument();
    expect(screen.queryByTitle(/update available/i)).not.toBeInTheDocument();
  });

  it("applies the WorkerView returned by the enabled toggle without refetching the list", async () => {
    // Regression for the #579 sweep: the row PATCHed then blind-refetched
    // the whole roster, discarding the WorkerView the PATCH returned.
    vi.mocked(api.adminListWorkers).mockResolvedValue(listResp([worker()]));
    vi.mocked(api.adminUpdateWorker).mockResolvedValue(worker({ enabled: false }));

    render(<AdminWorkers />);

    const toggle = await screen.findByRole("checkbox", {
      name: /disable home-wsl-gpu/i,
    });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);

    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(api.adminUpdateWorker).toHaveBeenCalledWith("w1", { enabled: false });
    expect(api.adminListWorkers).toHaveBeenCalledTimes(1);
  });

  it("applies the WorkerView returned by a priority edit without refetching the list", async () => {
    vi.mocked(api.adminListWorkers).mockResolvedValue(listResp([worker()]));
    vi.mocked(api.adminUpdateWorker).mockResolvedValue(worker({ priority: 5 }));

    render(<AdminWorkers />);

    const input = await screen.findByLabelText(/priority for home-wsl-gpu/i);
    fireEvent.change(input, { target: { value: "5" } });
    fireEvent.blur(input);

    await waitFor(() =>
      expect(api.adminUpdateWorker).toHaveBeenCalledWith("w1", { priority: 5 }),
    );
    await waitFor(() => expect(input).not.toBeDisabled());
    expect(api.adminListWorkers).toHaveBeenCalledTimes(1);
  });
});
