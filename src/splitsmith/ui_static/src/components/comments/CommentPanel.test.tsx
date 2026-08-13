import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CommentPanel } from "./CommentPanel";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listStageComments: vi.fn(),
      createStageComment: vi.fn(),
      deleteStageComment: vi.fn(),
    },
  };
});

function comment(over: Partial<import("@/lib/api").Comment> = {}) {
  return {
    id: "c1",
    anchor_t: 4.32,
    anchor_kind: "time" as const,
    anchor_shot_id: null,
    author_kind: "handle" as const,
    author_handle: "Prone Popper 47",
    body: "reload looks early",
    created_at: "2026-08-13T10:00:00Z",
    mine: false,
    ...over,
  };
}

const SHOTS = [
  {
    id: "cand-3",
    shot_number: 3,
    ms_after_beep: 5000,
    time_from_beep: 5.0,
    time_absolute: 15.0,
    split: 0.2,
    interval_class: null,
    interval_class_source: null,
    improvement_flag: false,
    coaching_note: null,
    stale: false,
    reload_hint: false,
  },
];

function renderPanel(over = {}) {
  return render(
    <CommentPanel
      slug="alice"
      stage={3}
      shots={SHOTS}
      beepTime={10}
      currentTime={14.32}
      canComment
      onSeek={vi.fn()}
      {...over}
    />,
  );
}

describe("CommentPanel", () => {
  beforeEach(() => {
    vi.mocked(api.listStageComments).mockResolvedValue({ comments: [comment()] });
    vi.mocked(api.createStageComment).mockResolvedValue(comment({ id: "c2", mine: true }));
  });

  it("renders the handle and body", async () => {
    renderPanel();
    expect(await screen.findByText("Prone Popper 47")).toBeInTheDocument();
    expect(screen.getByText("reload looks early")).toBeInTheDocument();
  });

  it("labels a time anchor with seconds and a shot anchor with the shot", async () => {
    vi.mocked(api.listStageComments).mockResolvedValue({
      comments: [
        comment({ id: "a", anchor_t: 4.32 }),
        comment({ id: "b", anchor_kind: "shot", anchor_shot_id: "cand-3", anchor_t: 5.0 }),
      ],
    });
    renderPanel();
    expect(await screen.findByText("4.32 s")).toBeInTheDocument();
    expect(screen.getByText("Shot 3")).toBeInTheDocument();
  });

  it("renders a shot anchor whose shot no longer resolves as a time pin", async () => {
    vi.mocked(api.listStageComments).mockResolvedValue({
      comments: [comment({ anchor_kind: "shot", anchor_shot_id: "cand-99", anchor_t: 7.5 })],
    });
    renderPanel();
    expect(await screen.findByText("7.50 s")).toBeInTheDocument();
    expect(screen.queryByText(/Shot/)).not.toBeInTheDocument();
  });

  it("posts with the snapped anchor when the playhead is on a shot", async () => {
    renderPanel({ currentTime: 15.02 });
    await screen.findByText("Prone Popper 47");
    await userEvent.type(screen.getByRole("textbox", { name: /comment/i }), "nice");
    await userEvent.click(screen.getByRole("button", { name: /post/i }));
    await waitFor(() =>
      expect(api.createStageComment).toHaveBeenCalledWith("alice", 3, {
        body: "nice",
        anchor_t: 5.02,
        anchor_kind: "shot",
        anchor_shot_id: "cand-3",
      }),
    );
  });

  it("posts a time anchor when the playhead is between shots", async () => {
    renderPanel({ currentTime: 12.5 });
    await screen.findByText("Prone Popper 47");
    await userEvent.type(screen.getByRole("textbox", { name: /comment/i }), "nice");
    await userEvent.click(screen.getByRole("button", { name: /post/i }));
    await waitFor(() =>
      expect(api.createStageComment).toHaveBeenCalledWith("alice", 3, {
        body: "nice",
        anchor_t: 2.5,
        anchor_kind: "time",
        anchor_shot_id: null,
      }),
    );
  });

  it("hides the compose box when commenting is not permitted", async () => {
    renderPanel({ canComment: false });
    await screen.findByText("Prone Popper 47");
    expect(screen.queryByRole("button", { name: /post/i })).not.toBeInTheDocument();
  });

  it("offers delete only on your own comment", async () => {
    vi.mocked(api.listStageComments).mockResolvedValue({
      comments: [comment({ id: "a", mine: false }), comment({ id: "b", mine: true })],
    });
    renderPanel();
    await screen.findAllByText("Prone Popper 47");
    expect(screen.getAllByRole("button", { name: /delete/i })).toHaveLength(1);
  });

  it("renders delete on every comment when the caller may moderate", async () => {
    // I3: the owner never posted the comments they need to moderate, so
    // `mine` is false for all of them and there was no button at all -
    // owner delete shipped as a curl command against a working backend.
    vi.mocked(api.listStageComments).mockResolvedValue({
      comments: [comment({ id: "a", mine: false }), comment({ id: "b", mine: false })],
    });
    renderPanel({ canComment: false, canModerate: true });
    await screen.findAllByText("Prone Popper 47");
    expect(screen.getAllByRole("button", { name: /delete/i })).toHaveLength(2);
  });

  it("still offers delete on your own comment when you may not moderate", async () => {
    vi.mocked(api.listStageComments).mockResolvedValue({
      comments: [comment({ id: "a", mine: false }), comment({ id: "b", mine: true })],
    });
    renderPanel({ canModerate: false });
    await screen.findAllByText("Prone Popper 47");
    expect(screen.getAllByRole("button", { name: /delete/i })).toHaveLength(1);
  });

  it("deletes through the moderation button", async () => {
    vi.mocked(api.deleteStageComment).mockResolvedValue(undefined as never);
    vi.mocked(api.listStageComments).mockResolvedValue({
      comments: [comment({ id: "a", mine: false })],
    });
    renderPanel({ canComment: false, canModerate: true });
    await userEvent.click(await screen.findByRole("button", { name: /delete/i }));
    expect(api.deleteStageComment).toHaveBeenCalledWith("alice", 3, "a");
    await waitFor(() => expect(screen.queryByText("reload looks early")).not.toBeInTheDocument());
  });

  it("appends a new comment, matching the server's oldest-first order", async () => {
    // M3: prepending showed a different order than the next reload.
    vi.mocked(api.listStageComments).mockResolvedValue({
      comments: [comment({ id: "a", body: "first" }), comment({ id: "b", body: "second" })],
    });
    vi.mocked(api.createStageComment).mockResolvedValue(comment({ id: "c", body: "third", mine: true }));
    renderPanel();
    await screen.findByText("first");

    await userEvent.type(screen.getByRole("textbox", { name: /comment/i }), "third");
    await userEvent.click(screen.getByRole("button", { name: /post/i }));

    await screen.findByText("third");
    const bodies = screen.getAllByText(/first|second|third/).map((el) => el.textContent);
    expect(bodies).toEqual(["first", "second", "third"]);
  });

  it("seeks when a comment is activated", async () => {
    const onSeek = vi.fn();
    renderPanel({ onSeek });
    await userEvent.click(await screen.findByText("reload looks early"));
    expect(onSeek).toHaveBeenCalledWith(14.32);
  });

  it("degrades to an inline retry when the thread fails to load", async () => {
    vi.mocked(api.listStageComments).mockRejectedValue(new Error("boom"));
    renderPanel();
    expect(await screen.findByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("clears the retry banner and shows the new comment after a post that follows a failed load", async () => {
    vi.mocked(api.listStageComments).mockRejectedValue(new Error("boom"));
    vi.mocked(api.createStageComment).mockResolvedValue(comment({ id: "c2", body: "nice", mine: true }));
    renderPanel();
    await screen.findByRole("button", { name: /retry/i });

    await userEvent.type(screen.getByRole("textbox", { name: /comment/i }), "nice");
    await userEvent.click(screen.getByRole("button", { name: /post/i }));

    expect(await screen.findByText("nice")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });
});
