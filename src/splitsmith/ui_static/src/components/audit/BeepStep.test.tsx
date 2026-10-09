import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import type { BeepQueueItem } from "@/lib/api";
import * as hook from "@/lib/useBeepQueue";
import { zoomActionForKey } from "@/lib/zoomKeys";

import { BeepStep } from "./BeepStep";

vi.mock("@/lib/useBeepQueue", async (orig) => ({
  ...(await orig<typeof import("@/lib/useBeepQueue")>()),
  useBeepQueue: vi.fn(),
}));
vi.mock("@/components/audit/BeepTimeline", () => ({
  BeepTimeline: (props: {
    videoId: string;
    videoBeepTime: number | null;
    draftSourceTime: number | null;
    candidates: { time: number; detected: boolean }[];
    onPick: (t: number) => void;
  }) => (
    <div data-testid="beep-timeline">
      <span data-testid="timeline-props">
        {JSON.stringify({
          videoId: props.videoId,
          videoBeepTime: props.videoBeepTime,
          draftSourceTime: props.draftSourceTime,
        })}
      </span>
      <button
        type="button"
        data-testid="timeline-pick"
        onClick={() => props.onPick(9.87)}
      >
        pick
      </button>
      {props.candidates.map((c) => (
        <button
          key={c.time}
          type="button"
          data-testid={`timeline-pick-${c.time}`}
          onClick={() => props.onPick(c.time)}
        >
          candidate {c.time}
        </button>
      ))}
      {props.candidates
        .filter((c) => c.detected)
        .map((c) => (
          // A pick 3 ms off the detected candidate's own time -- still
          // "within 5 ms" of it, never exactly equal, so BeepStep's own
          // epsilon match (not a `===`) is what has to clear the draft.
          <button
            key={`near-${c.time}`}
            type="button"
            data-testid="timeline-pick-near-detected"
            onClick={() => props.onPick(c.time + 0.003)}
          >
            near detected
          </button>
        ))}
    </div>
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

const OTHER_STAGE = item({
  stage_number: 11,
  stage_name: "B2 Right",
  video_id: "v9",
});

function hookState(items: BeepQueueItem[], over: Record<string, unknown> = {}) {
  return {
    data: {
      total_items: items.length,
      pending_count: items.length,
      confirmed_count: 0,
      origin: "local",
      stages: [],
    },
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

function renderStep(
  state: ReturnType<typeof hook.useBeepQueue>,
  props: Partial<React.ComponentProps<typeof BeepStep>> = {},
) {
  vi.mocked(hook.useBeepQueue).mockReturnValue(state);
  const onConfirmed = vi.fn();
  render(
    <ConfirmProvider>
      <BeepStep
        slug="alice"
        stageNumber={10}
        onConfirmed={onConfirmed}
        header={HEADER}
        mediaOnDesktop={false}
        {...props}
      />
    </ConfirmProvider>,
  );
  return { onConfirmed };
}

describe("BeepStep", () => {
  beforeEach(() => {
    vi.mocked(api.getBeepQueue).mockResolvedValue({
      stages: [{ items: [OTHER_STAGE] }],
    } as never);
  });

  it("renders this stage's item only, the header and the candidates with the detected one selected", () => {
    renderStep(hookState([item(), OTHER_STAGE]));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("10B3");
    expect(screen.queryByText("B2 Right")).toBeNull();
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(3);
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
    expect(radios[0]).toHaveTextContent("5.32");
    expect(
      screen.getByRole("button", { name: /Confirm & next/ }),
    ).toBeEnabled();
  });

  it("a candidate click becomes the draft Confirm sends; the next beep in the queue is reported", async () => {
    const state = hookState([item(), OTHER_STAGE]);
    const { onConfirmed } = renderStep(state);
    fireEvent.click(screen.getAllByRole("radio")[1]);
    fireEvent.click(screen.getByRole("button", { name: /Confirm & next/ }));
    await vi.waitFor(() =>
      expect(state.confirm).toHaveBeenCalledWith(
        expect.objectContaining({ video_id: "v1" }),
        0.9,
      ),
    );
    await vi.waitFor(() =>
      expect(onConfirmed).toHaveBeenCalledWith({
        slug: "alice",
        stageNumber: 11,
        videoId: "v9",
      }),
    );
  });

  it("confirming the detector's time as-is sends no override", async () => {
    const state = hookState([item()]);
    vi.mocked(api.getBeepQueue).mockResolvedValue({ stages: [] } as never);
    const { onConfirmed } = renderStep(state);
    fireEvent.click(screen.getByRole("button", { name: /Confirm & next/ }));
    await vi.waitFor(() =>
      expect(state.confirm).toHaveBeenCalledWith(
        expect.objectContaining({ video_id: "v1" }),
        undefined,
      ),
    );
    await vi.waitFor(() => expect(onConfirmed).toHaveBeenCalledWith("done"));
  });

  it("a pick from the timeline shows as its own row, and Confirm sends it", async () => {
    const state = hookState([item()]);
    vi.mocked(api.getBeepQueue).mockResolvedValue({ stages: [] } as never);
    const { onConfirmed } = renderStep(state);
    fireEvent.click(screen.getByTestId("timeline-pick"));
    expect(screen.getByText("9.87")).toBeInTheDocument();
    expect(screen.getByText("picked on the timeline")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Confirm & next/ }));
    await vi.waitFor(() =>
      expect(state.confirm).toHaveBeenCalledWith(
        expect.objectContaining({ video_id: "v1" }),
        9.87,
      ),
    );
    await vi.waitFor(() => expect(onConfirmed).toHaveBeenCalledWith("done"));
  });

  it("picking the detected time from the timeline clears the draft (no override sent)", async () => {
    const state = hookState([item()]);
    vi.mocked(api.getBeepQueue).mockResolvedValue({ stages: [] } as never);
    const { onConfirmed } = renderStep(state);
    // Draft away from the detected time first, via the candidate list.
    const radios = screen.getAllByRole("radio");
    fireEvent.click(radios[1]);
    expect(radios[1]).toHaveAttribute("aria-checked", "true");
    // The timeline reports a pick at the detected time -- same as clicking
    // its own row -- and that clears the draft back to "no override". The
    // prop BeepStep hands back down to the band must itself be null: a
    // naive `onPick={setDraft}` would echo 5.32 here (and item.beep_time
    // is also 5.32) and still pass selectedTime/aria-checked/confirm
    // assertions, since draft=5.32 and draft=null both read as "the
    // detected time" everywhere else.
    fireEvent.click(screen.getByTestId(`timeline-pick-${item().beep_time}`));
    expect(
      JSON.parse(screen.getByTestId("timeline-props").textContent!),
    ).toMatchObject({ draftSourceTime: null });
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
    expect(radios[1]).toHaveAttribute("aria-checked", "false");
    // The operator's-own-pick row only renders for a draft that isn't one
    // of the listed candidates; a cleared draft must not show it.
    expect(screen.queryByText("picked on the timeline")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Confirm & next/ }));
    await vi.waitFor(() =>
      expect(state.confirm).toHaveBeenCalledWith(
        expect.objectContaining({ video_id: "v1" }),
        undefined,
      ),
    );
    await vi.waitFor(() => expect(onConfirmed).toHaveBeenCalledWith("done"));
  });

  it("a pick 3 ms from the detected time also clears the draft", () => {
    const state = hookState([item()]);
    renderStep(state);
    const radios = screen.getAllByRole("radio");
    fireEvent.click(radios[1]);
    expect(radios[1]).toHaveAttribute("aria-checked", "true");

    fireEvent.click(screen.getByTestId("timeline-pick-near-detected"));

    expect(
      JSON.parse(screen.getByTestId("timeline-props").textContent!),
    ).toMatchObject({ draftSourceTime: null });
    expect(radios[0]).toHaveAttribute("aria-checked", "true");
    expect(radios[1]).toHaveAttribute("aria-checked", "false");
    expect(screen.queryByText("picked on the timeline")).not.toBeInTheDocument();
  });

  it("the timeline renders outside the two-column grid, after it, with the preview left and candidates right", () => {
    renderStep(hookState([item()]));
    const topRow = screen.getByTestId("beep-top-row");
    const video = screen.getByTitle("Space toggles play/pause");
    const radiogroup = screen.getByRole("radiogroup", {
      name: "Beep candidates",
    });
    const timeline = screen.getByTestId("beep-timeline");

    expect(topRow.contains(video)).toBe(true);
    expect(topRow.contains(radiogroup)).toBe(true);
    expect(topRow.children[0]?.contains(video)).toBe(true);
    expect(topRow.children[1]).toBe(radiogroup);

    expect(topRow.contains(timeline)).toBe(false);
    expect(
      topRow.compareDocumentPosition(timeline) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("'+' typed in a focused text field never reaches the band, and Cmd/Ctrl+Enter still confirms", async () => {
    const input = document.createElement("input");
    input.type = "text";
    expect(
      zoomActionForKey({
        key: "+",
        metaKey: false,
        ctrlKey: false,
        altKey: false,
        target: input,
      } as unknown as KeyboardEvent),
    ).toBeNull();

    const state = hookState([item()]);
    vi.mocked(api.getBeepQueue).mockResolvedValue({ stages: [] } as never);
    const { onConfirmed } = renderStep(state);
    fireEvent.keyDown(window, { key: "Enter", metaKey: true });
    await vi.waitFor(() => expect(state.confirm).toHaveBeenCalled());
    await vi.waitFor(() => expect(onConfirmed).toHaveBeenCalledWith("done"));
  });

  it("with a pending secondary, confirming the primary stays on the stage and selects the secondary", async () => {
    const sec = item({
      role: "secondary",
      video_id: "v2",
      status: "unreviewed",
    });
    const state = hookState([item(), sec]);
    const { onConfirmed } = renderStep(state);
    expect(screen.getByRole("tab", { name: /Head cam/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: /Confirm & next/ }));
    await vi.waitFor(() => expect(state.confirm).toHaveBeenCalled());
    await vi.waitFor(() =>
      expect(
        screen.getByRole("tab", { name: /Secondary cam/ }),
      ).toHaveAttribute("aria-selected", "true"),
    );
    expect(onConfirmed).not.toHaveBeenCalled();
  });

  it("repick offers Cancel, and confirming an unchanged confirmed beep just cancels", () => {
    const state = hookState([
      item({ beep_reviewed: true, status: "confirmed" }),
    ]);
    const onCancel = vi.fn();
    renderStep(state, { repick: true, onCancel });
    fireEvent.click(screen.getByRole("button", { name: /Confirm & next/ }));
    expect(state.confirm).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(2);
  });

  it("shows its place in the queue and walks every primary before the secondaries", async () => {
    const sec = item({
      role: "secondary",
      video_id: "v2",
      status: "unreviewed",
    });
    const state = hookState([item(), sec, OTHER_STAGE]);
    const { onConfirmed } = renderStep(state);

    expect(
      screen.getByText("Beep 1 of 3 · Alice · Stage 10 · x.mp4 · primary"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Confirm & next/ }));

    // The other stage's primary comes before this stage's secondary.
    await vi.waitFor(() =>
      expect(onConfirmed).toHaveBeenCalledWith({
        slug: "alice",
        stageNumber: 11,
        videoId: "v9",
      }),
    );
  });

  it("Later moves on without confirming", () => {
    const state = hookState([item(), OTHER_STAGE]);
    const { onConfirmed } = renderStep(state);

    fireEvent.click(screen.getByRole("button", { name: "Later" }));

    expect(state.confirm).not.toHaveBeenCalled();
    expect(onConfirmed).toHaveBeenCalledWith({
      slug: "alice",
      stageNumber: 11,
      videoId: "v9",
    });
  });
});
