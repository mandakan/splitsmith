/**
 * HostedUploadModal (extracted from AddFootageModal's hosted branch).
 *
 * Pins the two behaviors the extraction changes:
 * - the dropzone uses the depth counter (dragging over a child keeps
 *   the highlight on);
 * - a drop on the dropzone enqueues exactly once and stops
 *   propagation, so the hosted Ingest page's window-level drop target
 *   behind the modal cannot double-enqueue.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HostedUploadModal } from "@/components/HostedUploadModal";
import { api } from "@/lib/api";
import { useUploads } from "@/lib/uploads";
import { queueStats } from "@/lib/uploadStats";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listRawUploads: vi.fn().mockResolvedValue({ uploads: [] }),
    },
  };
});

vi.mock("@/lib/uploads", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/uploads")>();
  return { ...actual, useUploads: vi.fn() };
});

const enqueue = vi.fn();

describe("HostedUploadModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listRawUploads).mockResolvedValue({ uploads: [] });
    vi.mocked(useUploads).mockReturnValue({
      uploads: [],
      enqueue,
      cancel: vi.fn(),
      cancelAll: vi.fn(),
      clearFinished: vi.fn(),
      inFlight: false,
      attachTick: 0,
      probeFor: vi.fn(),
      queue: queueStats([], [], Date.now()),
    } as unknown as ReturnType<typeof useUploads>);
  });

  function renderModal() {
    return render(
      <HostedUploadModal
        slug="alice"
        onClose={vi.fn()}
        onImported={vi.fn()}
        stages={[]}
      />,
    );
  }

  it("keeps the drag highlight while crossing children (depth counter)", async () => {
    renderModal();
    const zone = await screen.findByTestId("hosted-dropzone");
    const inner = screen.getByText(/drop video files here/i);
    const fileDrag = { dataTransfer: { types: ["Files"] } };
    fireEvent.dragEnter(zone, fileDrag);
    fireEvent.dragEnter(inner, fileDrag);
    fireEvent.dragLeave(inner, fileDrag);
    expect(zone.className).toContain("bg-led-tint");
    fireEvent.dragLeave(zone, fileDrag);
    expect(zone.className).not.toContain("bg-led-tint");
  });

  it("enqueues a drop once and stops propagation to the window", async () => {
    const windowDrop = vi.fn();
    window.addEventListener("drop", windowDrop);
    try {
      renderModal();
      const zone = await screen.findByTestId("hosted-dropzone");
      const file = new File(["x"], "GH010001.MP4", { type: "video/mp4" });
      fireEvent.drop(zone, {
        dataTransfer: { files: [file], types: ["Files"] },
      });
      expect(enqueue).toHaveBeenCalledTimes(1);
      expect(windowDrop).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("drop", windowDrop);
    }
  });
});
