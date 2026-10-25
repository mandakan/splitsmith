import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ExportProgress } from "@/components/export/ExportProgress";
import { ConfirmProvider } from "@/components/useConfirm";
import type { ExportRow, ExportView } from "@/lib/api";

function row(over: Partial<ExportRow>): ExportRow {
  return {
    job_id: "j1",
    key: "render",
    label: "Stage 1",
    status: "running",
    progress: 0.4,
    elapsed_s: 75,
    remaining_s: 120,
    overrun: false,
    retryable: false,
    ...over,
  } as ExportRow;
}

function view(over: Partial<ExportView> = {}): ExportView {
  return {
    export_id: "e1",
    kind: "compare-grid",
    label: "Compare grid",
    match_id: "m1",
    status: "running",
    started_at: "2026-10-25T12:00:00Z",
    time_left_s: 540,
    finish_at: "2026-10-25T12:32:00Z",
    approximate: true,
    rows: [
      row({}),
      row({ job_id: "j2", label: "Stage 2", status: "waiting", progress: null, elapsed_s: null }),
      row({ job_id: "g", key: "stitch", label: "Joining", status: "waiting", progress: null, elapsed_s: null }),
    ],
    ...over,
  } as ExportView;
}

function mount(v: ExportView, onCancel = vi.fn(), onRetry = vi.fn()) {
  render(
    <ConfirmProvider>
      <ExportProgress view={v} onCancel={onCancel} onRetry={onRetry} />
    </ConfirmProvider>,
  );
  return { onCancel, onRetry };
}

describe("ExportProgress", () => {
  it("shows the export, its time left and a row per part", () => {
    mount(view());
    expect(screen.getByText("Compare grid")).toBeTruthy();
    expect(screen.getByText(/^About 9 min left, done by/)).toBeTruthy();
    expect(screen.getAllByRole("progressbar")).toHaveLength(3);
    expect(screen.getByText("Stage 2")).toBeTruthy();
    expect(screen.getByText("1:15")).toBeTruthy();
  });

  it("says taking longer when every open part is past its estimate", () => {
    mount(view({ time_left_s: 0, rows: [row({ overrun: true, remaining_s: 0 })] }));
    expect(screen.getByText("Taking longer than usual")).toBeTruthy();
  });

  it("retries a failed part", () => {
    const { onRetry } = mount(
      view({ status: "succeeded", time_left_s: null, rows: [row({ status: "failed", retryable: true })] }),
    );
    fireEvent.click(screen.getByRole("button", { name: /retry stage 1/i }));
    expect(onRetry).toHaveBeenCalledWith("j1");
  });

  it("cancels only after the confirm", async () => {
    const { onCancel } = mount(view());
    fireEvent.click(screen.getByRole("button", { name: /cancel export/i }));
    expect(onCancel).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: /^stop export$/i }));
    await waitFor(() => expect(onCancel).toHaveBeenCalled());
  });

  it("offers Dismiss on an export that ended", () => {
    const onDismiss = vi.fn();
    render(
      <ConfirmProvider>
        <ExportProgress
          view={view({ status: "succeeded", time_left_s: null, rows: [row({ status: "failed", retryable: true })] })}
          onCancel={vi.fn()}
          onRetry={vi.fn()}
          onDismiss={onDismiss}
        />
      </ConfirmProvider>,
    );
    expect(screen.queryByRole("button", { name: /cancel export/i })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalled();
  });
});
