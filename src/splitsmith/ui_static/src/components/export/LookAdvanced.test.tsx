/**
 * The Look group's Advanced row and the editor sheet (#1264): Edit only on
 * your own Looks, Duplicate copies then opens the editor on the copy, the
 * draft previews before Save, an error disables Save, hosted explains the
 * missing template editor.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LookAdvanced } from "@/components/export/LookAdvanced";
import { DRAFT_PREVIEW_DEBOUNCE_MS } from "@/components/export/LookEditor";
import { ConfirmProvider } from "@/components/useConfirm";
import { api, type LookInfo, type StoredLook } from "@/lib/api";
import { BUILTIN_LOOKS } from "@/lib/looks";
import { refreshLooks } from "@/lib/useLooks";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getLook: vi.fn(),
      putLook: vi.fn(),
      deleteLook: vi.fn(),
      duplicateLook: vi.fn(),
      exportPreview: vi.fn(),
    },
  };
});

vi.mock("@/lib/useLooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/useLooks")>();
  return { ...actual, refreshLooks: vi.fn(async () => ({ looks: [], transitions: [], loaded: true, failed: false })) };
});

const COLORS = {
  ink: [244, 244, 245],
  ink_2: [201, 204, 210],
  muted: [142, 147, 155],
  subtle: [107, 112, 121],
  surface: [20, 23, 28],
  rule: [38, 43, 51],
  stroke: [10, 11, 13],
  accent: [255, 45, 45],
  accent_fill: [220, 38, 38],
  accent_text: [255, 255, 255],
  split: [251, 191, 36],
  split_good: [74, 222, 128],
} as StoredLook["body"]["colors"];

const stored = (name: string): StoredLook => ({
  name,
  updated_at: "2026-10-07T10:00:00Z",
  body: { label: "Club", base: "splitsmith", colors: COLORS, accent_series: ["#ff2d2d"], styles: {} },
});

const club: LookInfo = {
  ...BUILTIN_LOOKS[0],
  name: "club",
  label: "Club",
  source: "user",
  editable: true,
  slots: { ...BUILTIN_LOOKS[0].slots, title_page: [{ name: "default", preview: null }, { name: "rise", preview: null }] },
};
const shipped: LookInfo = { ...BUILTIN_LOOKS[0], editable: false };

function renderRow(props: Partial<React.ComponentProps<typeof LookAdvanced>> = {}) {
  const onChooseLook = vi.fn();
  render(
    <ConfirmProvider>
      <LookAdvanced
        looks={[shipped, club]}
        look="club"
        onChooseLook={onChooseLook}
        slug="me"
        stageNumber={2}
        hosted={false}
        busy={false}
        {...props}
      />
    </ConfirmProvider>,
  );
  return { onChooseLook };
}

beforeEach(() => {
  vi.mocked(api.getLook).mockImplementation(async (name) => stored(name));
  vi.mocked(api.putLook).mockImplementation(async (name, body) => ({ ...stored(name), body }));
  vi.mocked(api.exportPreview).mockResolvedValue(new Blob(["png"], { type: "image/png" }));
  vi.mocked(api.duplicateLook).mockImplementation(async (name) => stored(name));
  globalThis.URL.createObjectURL = vi.fn(() => "blob:preview");
  globalThis.URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("LookAdvanced", () => {
  it("offers Edit only on your own Look", () => {
    renderRow({ look: "splitsmith" });
    expect(screen.queryByRole("button", { name: "Edit Look…" })).toBeNull();
    expect(screen.getByRole("button", { name: "Duplicate Look…" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "How Looks work" }).getAttribute("href")).toMatch(/authoring\.md$/);
  });

  it("duplicates the chosen Look, selects the copy and opens the editor on it", async () => {
    const { onChooseLook } = renderRow({ look: "splitsmith" });
    fireEvent.click(screen.getByRole("button", { name: "Duplicate Look…" }));
    await waitFor(() => expect(api.duplicateLook).toHaveBeenCalledWith("splitsmith-copy", "splitsmith"));
    expect(refreshLooks).toHaveBeenCalled();
    expect(onChooseLook).toHaveBeenCalledWith("splitsmith-copy");
    expect(await screen.findByRole("dialog", { name: "Edit Look splitsmith-copy" })).toBeTruthy();
  });

  it("saves an edited colour and previews the draft first", async () => {
    renderRow();
    fireEvent.click(screen.getByRole("button", { name: "Edit Look…" }));
    const accent = (await screen.findByLabelText("Highlight colour")) as HTMLInputElement;
    const save = screen.getByRole("button", { name: "Save Look" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(accent, { target: { value: "#0ac81e" } });
    expect(save.disabled).toBe(false);
    await waitFor(
      () => {
        const bodies = vi.mocked(api.exportPreview).mock.calls.map((c) => c[1]);
        expect(bodies.some((b) => b.draft?.colors.accent?.join() === "10,200,30" && b.look === "club")).toBe(true);
      },
      { timeout: DRAFT_PREVIEW_DEBOUNCE_MS + 1000 },
    );
    fireEvent.click(save);
    await waitFor(() => expect(api.putLook).toHaveBeenCalled());
    expect(vi.mocked(api.putLook).mock.calls[0][1].colors.accent).toEqual([10, 200, 30]);
    expect(refreshLooks).toHaveBeenCalled();
  });

  it("disables Save while the name is too long and says why", async () => {
    renderRow();
    fireEvent.click(screen.getByRole("button", { name: "Edit Look…" }));
    const label = (await screen.findByLabelText("Look name")) as HTMLInputElement;
    fireEvent.change(label, { target: { value: "x".repeat(61) } });
    expect((screen.getByRole("button", { name: "Save Look" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toMatch(/under 60 characters/);
  });

  it("stores a card style choice", async () => {
    renderRow();
    fireEvent.click(screen.getByRole("button", { name: "Edit Look…" }));
    await screen.findByLabelText("Highlight colour");
    fireEvent.click(screen.getByRole("button", { name: "Card styles" }));
    const group = screen.getByRole("group", { name: "Title page style" });
    fireEvent.click(group.querySelector("button:nth-child(2)") as HTMLButtonElement);
    fireEvent.click(screen.getByRole("button", { name: "Save Look" }));
    await waitFor(() => expect(api.putLook).toHaveBeenCalled());
    expect(vi.mocked(api.putLook).mock.calls[0][1].styles).toEqual({ title_page: "rise" });
  });

  it("has no template tab on splitsmith.app and says why under Card styles", async () => {
    renderRow({ hosted: true });
    fireEvent.click(screen.getByRole("button", { name: "Edit Look…" }));
    await screen.findByLabelText("Highlight colour");
    expect(screen.queryByRole("button", { name: /Templates/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Card styles" }));
    expect(screen.getByText(/picks from the shipped templates/)).toBeTruthy();
  });
});
