import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { NOTE_SAVE_DELAY_MS, useNoteAutosave } from "@/lib/useNoteAutosave";

const conflict = () => new ApiError(409, "version_conflict", { code: "version_conflict" });

function setup(serverValue: string, save: (t: string) => Promise<void>, reload: () => Promise<string | null>) {
  return renderHook((props: { serverValue: string }) => useNoteAutosave({ serverValue: props.serverValue, save, reload }), {
    initialProps: { serverValue },
  });
}

async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(NOTE_SAVE_DELAY_MS);
  });
}

describe("useNoteAutosave", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not save an unchanged note on blur", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { result } = setup("a", save, vi.fn());
    await act(async () => result.current.flush());
    expect(save).not.toHaveBeenCalled();
  });

  it("sends text typed during a save after it, in order", async () => {
    let finish: () => void = () => {};
    const save = vi
      .fn()
      .mockImplementationOnce(() => new Promise<void>((r) => { finish = r; }))
      .mockResolvedValue(undefined);
    const { result } = setup("", save, vi.fn());
    act(() => result.current.onChange("a"));
    await settle();
    expect(save).toHaveBeenCalledWith("a");
    act(() => result.current.onChange("ab"));
    act(() => result.current.flush());
    expect(save).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish();
    });
    expect(save).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenLastCalledWith("ab");
  });

  it("keeps the draft while a foreign response changes the server value mid-edit", async () => {
    const save = vi.fn(() => new Promise<void>(() => {}));
    const { result, rerender } = setup("old", save, vi.fn());
    act(() => result.current.onChange("mine"));
    rerender({ serverValue: "theirs" });
    expect(result.current.draft).toBe("mine");
  });

  it("keeps the draft after a failed save when the server value then changes", async () => {
    const save = vi.fn().mockRejectedValue(new ApiError(500, "boom", {}));
    const { result, rerender } = setup("old", save, vi.fn());
    act(() => result.current.onChange("mine"));
    await settle();
    expect(result.current.issue).toMatchObject({ kind: "failed" });
    // A coach response for another reason (a flag) arrives with a new note.
    rerender({ serverValue: "theirs" });
    expect(result.current.draft).toBe("mine");
  });

  it("follows the server when nothing local is unsaved", () => {
    const { result, rerender } = setup("old", vi.fn(), vi.fn());
    rerender({ serverValue: "new" });
    expect(result.current.draft).toBe("new");
  });

  it("a conflict whose reload still has the note this edit started from re-sends once", async () => {
    const save = vi.fn().mockRejectedValueOnce(conflict()).mockResolvedValueOnce(undefined);
    const reload = vi.fn().mockResolvedValue("base");
    const { result } = setup("base", save, reload);
    act(() => result.current.onChange("mine"));
    await settle();
    expect(reload).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenLastCalledWith("mine");
    expect(result.current.issue).toBeNull();
    expect(result.current.saving).toBe(false);
  });

  it("a conflict over another writer's note gives way to it and says so", async () => {
    const save = vi.fn().mockRejectedValue(conflict());
    const reload = vi.fn().mockResolvedValue("theirs");
    const { result } = setup("base", save, reload);
    act(() => result.current.onChange("mine"));
    await settle();
    expect(save).toHaveBeenCalledTimes(1);
    expect(result.current.draft).toBe("theirs");
    expect(result.current.issue).toEqual({ kind: "discarded" });
  });

  it("a second conflict on the re-send gives way too", async () => {
    const save = vi.fn().mockRejectedValue(conflict());
    const reload = vi.fn().mockResolvedValueOnce("base").mockResolvedValueOnce("base");
    const { result } = setup("base", save, reload);
    act(() => result.current.onChange("mine"));
    await settle();
    expect(save).toHaveBeenCalledTimes(2);
    expect(result.current.issue).toEqual({ kind: "discarded" });
    expect(result.current.draft).toBe("base");
  });

  it("a conflict whose reload fails keeps the draft and offers Retry", async () => {
    const save = vi.fn().mockRejectedValueOnce(conflict()).mockResolvedValueOnce(undefined);
    const reload = vi.fn().mockResolvedValue(null);
    const { result } = setup("base", save, reload);
    act(() => result.current.onChange("mine"));
    await settle();
    expect(result.current.issue).toMatchObject({ kind: "failed" });
    expect(result.current.draft).toBe("mine");
    await act(async () => result.current.retry());
    expect(save).toHaveBeenLastCalledWith("mine");
    expect(result.current.issue).toBeNull();
  });

  it("saves pending typing when it unmounts", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const { result, unmount } = setup("", save, vi.fn());
    act(() => result.current.onChange("left behind"));
    unmount();
    expect(save).toHaveBeenCalledWith("left behind");
  });
});
