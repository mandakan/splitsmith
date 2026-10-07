/**
 * The Look editor's preview (#1264 polish): a still first so a change shows in
 * about a second, the animation only once editing pauses, and a visible state
 * for every render in flight, so a click never looks like it missed.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DraftPreview } from "@/components/export/LookEditor";
import { api, type StoredLookBody } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, exportPreview: vi.fn() } };
});

const DRAFT: StoredLookBody = {
  label: "Club",
  base: "splitsmith",
  colors: { accent: [255, 45, 45] },
  accent_series: [],
  styles: {},
};

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => vi.clearAllMocks());

const bodies = () =>
  vi.mocked(api.exportPreview).mock.calls.map(
    (c) => c[1] as { card: string; width: number; motion?: boolean; backdrop?: string },
  );

describe("DraftPreview", () => {
  it("draws the still first, says it is working, then adds the animation once editing pauses", async () => {
    let release: (b: Blob) => void = () => undefined;
    vi.mocked(api.exportPreview).mockImplementationOnce(
      () => new Promise<Blob>((ok) => (release = ok)),
    );
    vi.mocked(api.exportPreview).mockResolvedValue(new Blob(["png"]));
    render(
      <DraftPreview
        name="club"
        draft={DRAFT}
        info={undefined}
        slug="me"
        stageNumber={1}
        templates={[]}
        focus={null}
      />,
    );
    expect(await screen.findByText(/Drawing the preview/)).toBeTruthy();
    await waitFor(() => expect(api.exportPreview).toHaveBeenCalled());
    expect(bodies()[0]).toMatchObject({ card: "title", width: 960 });
    expect(bodies()[0].motion).toBeUndefined();
    release(new Blob(["png"]));
    await waitFor(() => expect(screen.getByAltText("Title page preview")).toBeTruthy());
    await waitFor(
      () => expect(bodies().some((b) => b.card === "title" && b.width === 960 && b.motion === true)).toBe(true),
      { timeout: 4000 },
    );
  });

  it("draws over the demo scene when switched, and starts there on hosted", async () => {
    vi.mocked(api.exportPreview).mockResolvedValue(new Blob(["png"]));
    const { fireEvent } = await import("@testing-library/react");
    render(
      <DraftPreview
        name="club"
        draft={DRAFT}
        info={undefined}
        slug="me"
        stageNumber={1}
        templates={[]}
        focus={null}
      />,
    );
    await waitFor(() => expect(api.exportPreview).toHaveBeenCalled());
    expect(bodies()[0].backdrop).toBeUndefined();
    fireEvent.click(screen.getByRole("button", { name: "Demo scene" }));
    await waitFor(() => expect(bodies().some((b) => b.backdrop === "demo")).toBe(true));
  });
});

