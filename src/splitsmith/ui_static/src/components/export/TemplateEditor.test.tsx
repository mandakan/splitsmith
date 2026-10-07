/**
 * The Templates tab on the desktop (#1265): edit a template, see the
 * preview draw the unsaved text, check the draft, save it into the Look,
 * start from a starter, read what the template receives.
 * CodeMirror is a textarea here; jsdom has no layout for it.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LookAdvanced } from "@/components/export/LookAdvanced";
import { DRAFT_PREVIEW_DEBOUNCE_MS } from "@/components/export/LookEditor";
import { ConfirmProvider } from "@/components/useConfirm";
import { api, type LookInfo, type StoredLook } from "@/lib/api";
import { BUILTIN_LOOKS } from "@/lib/looks";

vi.mock("@/components/export/CodeEditor", () => ({
  default: ({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) => (
    <textarea aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getLook: vi.fn(),
      putLook: vi.fn(),
      exportPreview: vi.fn(),
      listTemplates: vi.fn(),
      saveTemplate: vi.fn(),
      templateSamples: vi.fn(),
      checkLook: vi.fn(),
      revealLook: vi.fn(),
    },
  };
});

vi.mock("@/lib/useLooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/useLooks")>();
  return { ...actual, refreshLooks: vi.fn(async () => ({ looks: [], transitions: [], loaded: true, failed: false })) };
});

const COLORS = Object.fromEntries(
  ["ink", "ink_2", "muted", "subtle", "surface", "rule", "stroke", "accent", "accent_fill", "accent_text", "split", "split_good"].map(
    (t) => [t, [100, 100, 100]],
  ),
) as StoredLook["body"]["colors"];

const club: LookInfo = { ...BUILTIN_LOOKS[0], name: "club", label: "Club", source: "user", editable: true };

beforeEach(() => {
  vi.mocked(api.getLook).mockResolvedValue({
    name: "club",
    updated_at: "2026-10-07T10:00:00Z",
    body: { label: "Club", base: "clean", colors: COLORS, accent_series: [], styles: {} },
  });
  vi.mocked(api.listTemplates).mockResolvedValue({
    templates: [
      { slot: "title_page", variant: "default", file: "card.html", own: false, content: "<p>shipped title</p>" },
      { slot: "slate", variant: "rise", file: "card-rise.html", own: false, content: "<p>shipped rise</p>" },
    ],
    starters: [{ name: "still", content: "<!doctype html><p>starter</p>" }],
  });
  vi.mocked(api.templateSamples).mockResolvedValue({
    cases: [{ case: "one shooter with a logo", context: { data: { card: { text: "Bromma Classifier" } } } }],
  });
  vi.mocked(api.checkLook).mockResolvedValue({
    items: [{ subject: "title_page default title_page-default.html", level: "error", message: "line 3: boom" }],
    errors: 1,
    warnings: 0,
  });
  vi.mocked(api.saveTemplate).mockImplementation(async (_name, edit) => ({ ...edit, file: "x.html", own: true }));
  vi.mocked(api.exportPreview).mockResolvedValue(new Blob(["png"], { type: "image/png" }));
  globalThis.URL.createObjectURL = vi.fn(() => "blob:preview");
  globalThis.URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.clearAllMocks();
});

async function openTemplates() {
  render(
    <ConfirmProvider>
      <LookAdvanced looks={[club]} look="club" onChooseLook={vi.fn()} slug="me" stageNumber={1} hosted={false} busy={false} />
    </ConfirmProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Edit Look…" }));
  await screen.findByLabelText("accent");
  fireEvent.click(screen.getByRole("button", { name: "Templates" }));
  return (await screen.findByLabelText("Title page template")) as HTMLTextAreaElement;
}

describe("TemplateEditor", () => {
  it("edits a borrowed template, previews the unsaved text and saves it", async () => {
    vi.mocked(api.checkLook).mockResolvedValue({ items: [], errors: 0, warnings: 0 });
    const editor = await openTemplates();
    expect(editor.value).toBe("<p>shipped title</p>");
    expect(screen.getByText(/Borrowed: card.html/)).toBeTruthy();
    fireEvent.change(editor, { target: { value: "<p>mine</p>" } });
    await waitFor(
      () => {
        const bodies = vi.mocked(api.exportPreview).mock.calls.map((c) => c[1]);
        expect(
          bodies.some((b) => b.card === "title" && b.templates?.[0]?.content === "<p>mine</p>"),
        ).toBe(true);
      },
      { timeout: DRAFT_PREVIEW_DEBOUNCE_MS + 1500 },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save Look" }));
    await waitFor(() =>
      expect(api.saveTemplate).toHaveBeenCalledWith("club", {
        slot: "title_page",
        variant: "default",
        content: "<p>mine</p>",
      }),
    );
    expect(api.putLook).not.toHaveBeenCalled();
  });

  it("checks the draft and shows the findings with their line", async () => {
    const editor = await openTemplates();
    fireEvent.change(editor, { target: { value: "<script>BOOM</script>" } });
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText("line 3: boom")).toBeTruthy();
    expect(vi.mocked(api.checkLook).mock.calls[0][2]).toEqual([
      { slot: "title_page", variant: "default", content: "<script>BOOM</script>" },
    ]);
  });

  it("starts from a starter, previews the chosen style and shows the sample data", async () => {
    await openTemplates();
    expect(await screen.findByText(/Bromma Classifier/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Template"), { target: { value: "slate/rise" } });
    const editor = (await screen.findByLabelText("Stage slate, Rise template")) as HTMLTextAreaElement;
    fireEvent.click(screen.getByRole("button", { name: "Start from this starter" }));
    expect(editor.value).toBe("<!doctype html><p>starter</p>");
    await waitFor(
      () => {
        const bodies = vi.mocked(api.exportPreview).mock.calls.map((c) => c[1]);
        expect(bodies.some((b) => b.card === "slate" && b.variant === "rise")).toBe(true);
      },
      { timeout: DRAFT_PREVIEW_DEBOUNCE_MS + 1500 },
    );
  });

  it("checks before saving and asks before saving a template that fails", async () => {
    const editor = await openTemplates();
    fireEvent.change(editor, { target: { value: "<script>BOOM</script>" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Look" }));
    expect(await screen.findByRole("button", { name: "Save anyway" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("line 3: boom");
    expect(api.saveTemplate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save anyway" }));
    await waitFor(() => expect(api.saveTemplate).toHaveBeenCalled());
  });

  it("asks again once the text changes after a failed check", async () => {
    const editor = await openTemplates();
    fireEvent.change(editor, { target: { value: "<script>BOOM</script>" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Look" }));
    await screen.findByRole("button", { name: "Save anyway" });
    fireEvent.change(editor, { target: { value: "<script>BOOM 2</script>" } });
    expect(screen.queryByRole("button", { name: "Save anyway" })).toBeNull();
    expect(screen.getByRole("button", { name: "Save Look" })).toBeTruthy();
  });
});
