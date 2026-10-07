/**
 * useLooks (#1246): one catalog fetch per page however many surfaces
 * mount the hook, and a failed fetch is reported, not hidden.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { BUILTIN_LOOKS } from "@/lib/looks";
import { refreshLooks, resetLooksForTests, useLooks } from "@/lib/useLooks";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, listLooks: vi.fn() } };
});

afterEach(() => {
  resetLooksForTests();
  vi.mocked(api.listLooks).mockReset();
});

describe("useLooks", () => {
  it("fetches once for three mounts and hands every one the catalog", async () => {
    const looks = [{ ...BUILTIN_LOOKS[0], label: "Fetched" }];
    vi.mocked(api.listLooks).mockResolvedValue({ looks });
    const a = renderHook(() => useLooks());
    const b = renderHook(() => useLooks());
    const c = renderHook(() => useLooks());
    expect(a.result.current.loaded).toBe(false);
    await waitFor(() => expect(c.result.current.loaded).toBe(true));
    expect(api.listLooks).toHaveBeenCalledTimes(1);
    expect(a.result.current.looks[0].label).toBe("Fetched");
    expect(b.result.current).toEqual({ looks, transitions: [], loaded: true, failed: false });
  });

  it("reports a failed fetch and keeps the built-in catalog", async () => {
    vi.mocked(api.listLooks).mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useLooks());
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current).toEqual({ looks: BUILTIN_LOOKS, transitions: [], loaded: true, failed: true });
  });

  it("refreshes every mounted surface after a Look is saved", async () => {
    vi.mocked(api.listLooks).mockResolvedValueOnce({ looks: BUILTIN_LOOKS });
    const a = renderHook(() => useLooks());
    const b = renderHook(() => useLooks());
    await waitFor(() => expect(a.result.current.loaded).toBe(true));
    const club = { ...BUILTIN_LOOKS[0], name: "club", label: "Club", source: "user" as const, editable: true };
    vi.mocked(api.listLooks).mockResolvedValueOnce({ looks: [...BUILTIN_LOOKS, club] });
    await act(async () => {
      await refreshLooks();
    });
    expect(a.result.current.looks.map((l) => l.name)).toEqual(["splitsmith", "club"]);
    expect(b.result.current.looks.map((l) => l.name)).toEqual(["splitsmith", "club"]);
    expect(api.listLooks).toHaveBeenCalledTimes(2);
  });
});
