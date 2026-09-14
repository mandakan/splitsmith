import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import type { BeepQueueItem } from "@/lib/api";
import * as hook from "@/lib/useBeepQueue";

import { BeepStep } from "./BeepStep";

vi.mock("@/lib/useBeepQueue", async (orig) => ({
  ...(await orig<typeof import("@/lib/useBeepQueue")>()),
  useBeepQueue: vi.fn(),
}));
vi.mock("@/components/BeepSection", () => ({
  BeepWaveformPicker: ({ onPick }: { onPick: (t: number) => void }) => (
    <button type="button" data-testid="waveform-picker" onClick={() => onPick(9.87)}>
      picker
    </button>
  ),
}));
vi.mock("@/lib/api", () => ({
  api: {
    videoStreamUrl: () => "/proxy.mp4",
    getBeepQueue: vi.fn(),
  },
}));

const { api } = await import("@/lib/api");

const item = (over: Partial<BeepQueueItem> = {}): BeepQueueItem => ({
  slug: "alice",
  shooter_name: "Alice",
  stage_number: 10,
  stage_name: "B3",
  role: "primary",
  video_id: "v1",
  video_path: "raw/x.mp4",
  beep_time: 5.32,
  beep_confidence: 0.42,
  beep_reviewed: false,
  status: "low_confidence",
  alt_candidates: [
    { time: 0.9, confidence: 0.21 },
    { time: 12.4, confidence: 0.12 },
  ],
  proxy_ready: true,
  snippet_ready: false,
  trim_stale: false,
  ...over,
});

const OTHER_STAGE = item({ stage_number: 11, stage_name: "B2 Right", video_id: "v9" });

function hookState(items: BeepQueueItem[], over: Record<string, unknown> = {}) {
  return {
    data: { total_items: items.length, pending_count: items.length, confirmed_count: 0, origin: "local", stages: [] },
    flatItems: items,
    pendingItems: items.filter((it) => it.status !== "confirmed"),
    active: items[0] ?? null,
    activeKey: null,
    setActiveKey: vi.fn(),
    isMirror: false,
    editDenied: false,
    busy: false,
    error: null,
    setError: vi.fn(),
    redetecting: false,
    redetectPct: null,
    reload: vi.fn().mockResolvedValue(undefined),
    confirm: vi.fn().mockResolvedValue(undefined),
    redetect: vi.fn().mockResolvedValue(undefined),
    skip: vi.fn(),
    prevItem: vi.fn(),
    nextItem: vi.fn(),
    ...over,
  } as unknown as ReturnType<typeof hook.useBeepQueue>;
}

const HEADER = { ordinal: "10", title: "B3", sub: "Alice" };

function renderStep(state: ReturnType<typeof hook.useBeepQueue>, props: Partial<React.ComponentProps<typeof BeepStep>> = {}) {
  vi.mocked(hook.useBeepQueue).mockReturnValue(state);
  const onConfirmed = vi.fn();
  render(
    <ConfirmProvider>
      <BeepStep slug="alice" stageNumber={10} onConfirmed={onConfirmed} header={HEADER} mediaOnDesktop={false} {...props} />
    </ConfirmProvider>,
  );
  return { onConfirmed };
}

describe("BeepStep", () => {
  beforeEach(() => {
    vi.mocked(api.getBeepQueue).mockResolvedValue({ stages: [{ items: [OTHER_STAGE] }] } as never);
  });

  it("renders this stage's item only, the header and the candidates with the detected one selected", () => {
    renderStep(hookState([item(), OTHER_STAGE]));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("10B3");
    expect(screen.queryByText("B2 Right")).toBeNull();
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
    expect(radios[0]).toHaveTextContent("5.32");
    expect(screen.getByRole("button", { name: /Confirm & next/ })).toBeEnabled();
  });

  it("a candidate click becomes the draft Confirm sends; the next pending stage is reported", async () => {
    const state = hookState([item(), OTHER_STAGE]);
    const { onConfirmed } = renderStep(state);
    fireEvent.click(screen.getAllByRole("radio")[1]);
    fireEvent.click(screen.getByRole("button", { name: /Confirm & next/ }));
    await vi.waitFor(() => expect(state.confirm).toHaveBeenCalledWith(expect.objectContaining({ video_id: "v1" }), 0.9));
    await vi.waitFor(() => expect(onConfirmed).toHaveBeenCalledWith({ slug: "alice", stageNumber: 11 }));
  });

  it("confirming the detector's time as-is sends no override", async () => {
    const state = hookState([item()]);
    vi.mocked(api.getBeepQueue).mockResolvedValue({ stages: [] } as never);
    const { onConfirmed } = renderStep(state);
    fireEvent.click(screen.getByRole("button", { name: /Confirm & next/ }));
    await vi.waitFor(() => expect(state.confirm).toHaveBeenCalledWith(expect.objectContaining({ video_id: "v1" }), undefined));
    await vi.waitFor(() => expect(onConfirmed).toHaveBeenCalledWith(null));
  });

  it("a waveform pick shows as its own selected row", () => {
    renderStep(hookState([item()]));
    fireEvent.click(screen.getByTestId("waveform-picker"));
    expect(screen.getByText("9.87")).toBeInTheDocument();
    expect(screen.getByText("picked on the waveform")).toBeInTheDocument();
  });

  it("with a pending secondary, confirming the primary stays on the stage and selects the secondary", async () => {
    const sec = item({ role: "secondary", video_id: "v2", status: "unreviewed" });
    const state = hookState([item(), sec]);
    const { onConfirmed } = renderStep(state);
    expect(screen.getByRole("tab", { name: /Head cam/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(screen.getByRole("button", { name: /Confirm & next/ }));
    await vi.waitFor(() => expect(state.confirm).toHaveBeenCalled());
    await vi.waitFor(() =>
      expect(screen.getByRole("tab", { name: /Secondary cam/ })).toHaveAttribute("aria-selected", "true"),
    );
    expect(onConfirmed).not.toHaveBeenCalled();
  });

  it("repick offers Cancel, and confirming an unchanged confirmed beep just cancels", () => {
    const state = hookState([item({ beep_reviewed: true, status: "confirmed" })]);
    const onCancel = vi.fn();
    renderStep(state, { repick: true, onCancel });
    fireEvent.click(screen.getByRole("button", { name: /Confirm & next/ }));
    expect(state.confirm).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(2);
  });
});
