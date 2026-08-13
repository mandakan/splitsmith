import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CommentPanel } from "./CommentPanel";
import { api, type Comment } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listStageComments: vi.fn(),
      createStageComment: vi.fn(),
      deleteStageComment: vi.fn(),
      listCommentAuthors: vi.fn(),
    },
  };
});

// Real author_code values are 6-char uppercase Crockford base32 (see
// AUTHOR_CODE_ALPHABET / AUTHOR_CODE_LEN in comment_identity.py) -- no
// I/L/O/U. One per comment id used in this file, so distinct comment ids
// never share a code by copy-paste accident: a future name-collision test
// relies on fixtures keeping different authors apart even when the
// display handle matches.
const AUTHOR_CODES: Record<string, string> = {
  c1: "A7K2M9",
  c2: "B3P8QR",
  a: "C5T4VW",
  b: "D6X1YZ",
  c: "E9N7JH",
};

function comment(over: Partial<import("@/lib/api").Comment> = {}) {
  return {
    id: "c1",
    anchor_t: 4.32,
    anchor_kind: "time" as const,
    anchor_shot_id: null,
    author_kind: "handle" as const,
    author_handle: "Prone Popper 47",
    author_code: AUTHOR_CODES[over.id ?? "c1"],
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

// Shared prop bag for tests that build their own <CommentPanel /> element
// directly (needed for `rerender`, which `renderPanel` below doesn't
// support). Kept in lockstep with renderPanel's defaults.
const baseProps = {
  slug: "alice",
  stage: 3,
  shots: SHOTS,
  beepTime: 10,
  currentTime: 14.32,
  canComment: true,
  onSeek: vi.fn(),
};

function renderPanel(over = {}) {
  return render(<CommentPanel {...baseProps} {...over} />);
}

/** Stub the thread load with a fixed comment list. */
function mockList(comments: Comment[]) {
  vi.mocked(api.listStageComments).mockResolvedValue({ comments });
}

describe("CommentPanel", () => {
  beforeEach(() => {
    vi.mocked(api.listStageComments).mockResolvedValue({ comments: [comment()] });
    vi.mocked(api.createStageComment).mockResolvedValue(comment({ id: "c2", mine: true }));
    vi.mocked(api.listCommentAuthors).mockResolvedValue({ authors: [] });
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

describe("CommentPanel author codes (#867)", () => {
  beforeEach(() => {
    // Call counts on this mock persist across tests (nothing in
    // testSetup.ts resets them) - several tests below assert
    // `.not.toHaveBeenCalled()`, so a stale count from an earlier test's
    // click would produce a false failure. Clear before each test, then
    // set the default implementation fresh.
    vi.mocked(api.listCommentAuthors).mockClear();
    vi.mocked(api.listCommentAuthors).mockResolvedValue({ authors: [] });
  });

  it("puts every author code in the DOM and in a tooltip", async () => {
    mockList([comment({ author_handle: "Anders Berg", author_code: "AAA111" })]);
    render(<CommentPanel {...baseProps} />);

    const author = await screen.findByText("Anders Berg");
    expect(author).toHaveAttribute("data-author-code", "AAA111");
    expect(author).toHaveAttribute("title", expect.stringContaining("AAA111"));
  });

  it("does not show a code when every name is distinct", async () => {
    mockList([
      comment({ id: "c1", author_handle: "Anders Berg", author_code: "AAA111" }),
      comment({ id: "c2", author_handle: "Bertil Lund", author_code: "BBB222" }),
    ]);
    render(<CommentPanel {...baseProps} />);

    await screen.findByText("Anders Berg");
    expect(screen.queryByText("AAA111")).not.toBeInTheDocument();
  });

  it("shows both codes when two authors share a name", async () => {
    mockList([
      comment({ id: "c1", author_handle: "Anders Berg", author_code: "AAA111" }),
      comment({ id: "c2", author_handle: "anders  berg", author_code: "BBB222" }),
    ]);
    render(<CommentPanel {...baseProps} />);

    expect(await screen.findByText("AAA111")).toBeInTheDocument();
    expect(await screen.findByText("BBB222")).toBeInTheDocument();
  });

  it("shows no code when one author posts twice", async () => {
    mockList([
      comment({ id: "c1", author_handle: "Anders Berg", author_code: "AAA111" }),
      comment({ id: "c2", author_handle: "Anders Berg", author_code: "AAA111" }),
    ]);
    render(<CommentPanel {...baseProps} />);

    await screen.findAllByText("Anders Berg");
    expect(screen.queryByText("AAA111")).not.toBeInTheDocument();
  });

  it("offers author detail only to a moderator", async () => {
    mockList([comment({ author_handle: "Anders Berg", author_code: "AAA111" })]);
    const { rerender } = render(<CommentPanel {...baseProps} canModerate={false} />);
    await screen.findByText("Anders Berg");
    expect(screen.queryByRole("button", { name: /author detail/i })).not.toBeInTheDocument();

    rerender(<CommentPanel {...baseProps} canModerate />);
    expect(
      await screen.findByRole("button", { name: /author detail/i }),
    ).toBeInTheDocument();
  });

  it("marks the author-detail trigger as a disclosure toggling an aria-controls target", async () => {
    mockList([comment({ author_handle: "Anders Berg", author_code: "AAA111" })]);
    render(<CommentPanel {...baseProps} canModerate />);

    const trigger = await screen.findByRole("button", { name: /author detail/i });
    // Closed on first render.
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    const controlsId = trigger.getAttribute("aria-controls");
    expect(controlsId).toBeTruthy();

    await userEvent.click(trigger);

    // Open: aria-expanded flips, and aria-controls now resolves to the
    // actual detail panel that appeared - a screen reader gets both "this
    // is a toggle" and "here is what it reveals".
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const panel = document.getElementById(controlsId!);
    expect(panel).not.toBeNull();
    expect(panel).toHaveTextContent(/unavailable|comments since/i);
  });

  it("shows every name a code posted under when a moderator opens detail", async () => {
    mockList([comment({ author_handle: "Bertil Lund", author_code: "AAA111" })]);
    vi.mocked(api.listCommentAuthors).mockResolvedValue({
      authors: [
        {
          author_code: "AAA111",
          author_kind: "account",
          first_comment_at: "2026-08-13T10:00:00Z",
          comment_count: 2,
          handles: ["Anders Berg", "Bertil Lund"],
        },
      ],
    });
    render(<CommentPanel {...baseProps} canModerate />);

    fireEvent.click(await screen.findByRole("button", { name: /author detail/i }));

    expect(await screen.findByText(/Anders Berg/)).toBeInTheDocument();
    expect(await screen.findByText(/2 comments/i)).toBeInTheDocument();
  });

  it("does not fetch author detail on mount for a non-moderator", async () => {
    mockList([comment({ author_handle: "Anders Berg", author_code: "AAA111" })]);
    render(<CommentPanel {...baseProps} canModerate={false} />);
    await screen.findByText("Anders Berg");
    expect(api.listCommentAuthors).not.toHaveBeenCalled();
  });

  it("does not fetch author detail on mount even for a moderator - only on open", async () => {
    mockList([comment({ author_handle: "Anders Berg", author_code: "AAA111" })]);
    render(<CommentPanel {...baseProps} canModerate />);
    await screen.findByText("Anders Berg");
    expect(api.listCommentAuthors).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: /author detail/i }));
    expect(api.listCommentAuthors).toHaveBeenCalledTimes(1);
  });

  it("degrades gracefully when author detail fails to load", async () => {
    mockList([comment({ author_handle: "Anders Berg", author_code: "AAA111" })]);
    vi.mocked(api.listCommentAuthors).mockRejectedValue(new Error("boom"));
    render(<CommentPanel {...baseProps} canModerate />);

    // The code and tooltip are unaffected by a detail-fetch failure -
    // they come from the comment itself, not the aggregate endpoint.
    const author = await screen.findByText("Anders Berg");
    expect(author).toHaveAttribute("data-author-code", "AAA111");
    expect(author).toHaveAttribute("title", expect.stringContaining("AAA111"));

    await userEvent.click(screen.getByRole("button", { name: /author detail/i }));
    expect(await screen.findByText(/unavailable/i)).toBeInTheDocument();
  });

  it("does not nest a button inside the seek button, and clicks route correctly", async () => {
    // React warns via console.error when it rejects a <button> nested
    // inside a <button> - that warning is the proof the row restructure
    // actually worked, not just that the new button exists.
    //
    // The exact wording is not stable across React versions: 18 said
    // "validateDOMNesting(...): <button> cannot appear as a descendant
    // of <button>"; 19.2 dropped the internal function name entirely and
    // says "In HTML, %s cannot be a descendant of <%s>" (or "cannot be
    // a child of", for a direct-parent violation), with the tag names
    // passed as separate %s arguments rather than interpolated into the
    // string - so a check against one literal sentence, or even one
    // single string argument, silently stops matching the moment the
    // wording changes and the test goes on "passing" over real invalid
    // nesting. Instead this keys on the semantics any phrasing of the
    // warning carries: the offending tag ("button") plus "descendant" or
    // "child", found anywhere across a call's arguments once they are
    // all stringified and joined.
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const onSeek = vi.fn();
    mockList([comment({ author_handle: "Anders Berg", author_code: "AAA111" })]);
    render(<CommentPanel {...baseProps} canModerate onSeek={onSeek} />);
    await screen.findByText("Anders Berg");

    const nestingWarning = errorSpy.mock.calls.some((call) => {
      const joined = call.map((arg) => String(arg)).join(" ");
      return /button/i.test(joined) && /descendant|child/i.test(joined);
    });
    expect(nestingWarning).toBe(false);
    errorSpy.mockRestore();

    // The detail trigger must not also seek the player.
    await userEvent.click(screen.getByRole("button", { name: /author detail/i }));
    expect(onSeek).not.toHaveBeenCalled();

    // The comment body still does.
    await userEvent.click(screen.getByText("reload looks early"));
    expect(onSeek).toHaveBeenCalledWith(14.32);
  });
});
