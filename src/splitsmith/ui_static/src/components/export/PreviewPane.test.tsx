/**
 * The rail preview (spec 2026-09-15 s3): the generic thumbnail at once on
 * hover, the real still after a debounce on select and on every edit,
 * the previous image kept until the next lands, one line per failure,
 * and never anything that blocks the page.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PreviewPane } from "@/components/export/PreviewPane";
import { ApiError, api } from "@/lib/api";
import { DEFAULT_EXPORT_SETTINGS } from "@/lib/exportPresets";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, exportPreview: vi.fn(), installChromium: vi.fn(), getJob: vi.fn() } };
});

vi.mock("@/lib/features", () => ({ useDeploymentMode: () => ({ mode: "local", resolved: true }) }));

const blob = (tag: string) => new Blob([tag], { type: "image/png" });

beforeEach(() => {
  vi.useFakeTimers();
  let n = 0;
  globalThis.URL.createObjectURL = vi.fn(() => `blob:${n++}`);
  globalThis.URL.revokeObjectURL = vi.fn();
  vi.mocked(api.exportPreview).mockResolvedValue(blob("a"));
});
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

const settings = { ...DEFAULT_EXPORT_SETTINGS, outputFormat: "mp4" as const };
const TITLE = { slotId: "titlePage" as const, variantId: "on" };

async function settle(ms = 400) {
  await act(async () => {
    vi.advanceTimersByTime(ms);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("PreviewPane", () => {
  it("requests the frame for the stage after the debounce and captions it", async () => {
    render(<PreviewPane slug="me" stageNumber={3} settings={settings} projectName="Bromma" focus={null} hover={null} enabled />);
    expect(api.exportPreview).not.toHaveBeenCalled();
    await settle();
    expect(api.exportPreview).toHaveBeenCalledWith(
      "me",
      expect.objectContaining({ card: "frame", stage_number: 3 }),
      expect.anything(),
    );
    expect(screen.getByRole("img", { name: "Stage 03" })).toHaveAttribute("src", "blob:0");
  });

  it("shows the generic thumbnail at once on hover, without a request", async () => {
    render(
      <PreviewPane
        slug="me"
        stageNumber={3}
        settings={settings}
        projectName="Bromma"
        focus={null}
        hover={{ slotId: "stageCard", variantId: "slate" }}
        enabled
      />,
    );
    expect(screen.getByRole("img", { name: "Slate · Stage 03" })).toHaveAttribute(
      "src",
      expect.stringMatching(/stage-card-slate/),
    );
    await settle();
    // The still for the focus (the frame) is still requested underneath.
    expect(api.exportPreview).toHaveBeenCalledTimes(1);
    expect(vi.mocked(api.exportPreview).mock.calls[0][1]).toMatchObject({ card: "frame" });
  });

  it("keeps the previous still until the next one lands, and coalesces edits", async () => {
    const view = render(
      <PreviewPane slug="me" stageNumber={3} settings={settings} projectName="Bromma" focus={TITLE} hover={null} enabled />,
    );
    await settle();
    expect(screen.getByRole("img")).toHaveAttribute("src", "blob:0");
    vi.mocked(api.exportPreview).mockResolvedValue(blob("b"));
    const edited = { ...settings, renderOptions: { ...settings.renderOptions, titleInfo: "L" } };
    view.rerender(<PreviewPane slug="me" stageNumber={3} settings={edited} projectName="Bromma" focus={TITLE} hover={null} enabled />);
    const edited2 = { ...settings, renderOptions: { ...settings.renderOptions, titleInfo: "L3" } };
    view.rerender(<PreviewPane slug="me" stageNumber={3} settings={edited2} projectName="Bromma" focus={TITLE} hover={null} enabled />);
    expect(screen.getByRole("img")).toHaveAttribute("src", "blob:0");
    await settle();
    expect(api.exportPreview).toHaveBeenCalledTimes(2);
    expect(vi.mocked(api.exportPreview).mock.calls[1][1]).toMatchObject({
      card: "title",
      title_info: "L3",
      project_name: "Bromma",
    });
    expect(screen.getByRole("img")).toHaveAttribute("src", "blob:1");
    // The superseded still's object URL is released once the new one shows.
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:0");
  });

  it("says why when the server cannot, and the image stays", async () => {
    const OVERLAY = { slotId: "overlay" as const, variantId: "on" };
    const view = render(
      <PreviewPane slug="me" stageNumber={3} settings={settings} projectName="Bromma" focus={null} hover={null} enabled />,
    );
    await settle();
    vi.mocked(api.exportPreview).mockRejectedValue(new ApiError(503, "no browser"));
    view.rerender(<PreviewPane slug="me" stageNumber={3} settings={settings} projectName="Bromma" focus={OVERLAY} hover={null} enabled />);
    await settle();
    expect(screen.getByText("Preview needs a browser")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("src", "blob:0");
    vi.mocked(api.exportPreview).mockRejectedValue(new ApiError(409, "no shots"));
    view.rerender(<PreviewPane slug="me" stageNumber={4} settings={settings} projectName="Bromma" focus={OVERLAY} hover={null} enabled />);
    await settle();
    expect(screen.getByText("Overlay needs audited shots")).toBeInTheDocument();
  });

  it("a transition tile shows its generic thumbnail and requests nothing", async () => {
    render(
      <PreviewPane
        slug="me"
        stageNumber={3}
        settings={{ ...settings, outputFormat: "fcpxml" }}
        projectName="Bromma"
        focus={{ slotId: "transition", variantId: "zoom" }}
        hover={null}
        enabled
      />,
    );
    await settle();
    expect(api.exportPreview).not.toHaveBeenCalled();
    expect(screen.getByRole("img", { name: "Zoom blur" })).toHaveAttribute(
      "src",
      expect.stringMatching(/transition-zoom/),
    );
  });

  it("discards a response that arrives after a newer request was made", async () => {
    let resolveFirst: (b: Blob) => void = () => {};
    vi.mocked(api.exportPreview).mockImplementationOnce(
      () => new Promise<Blob>((resolve) => (resolveFirst = resolve)),
    );
    const view = render(
      <PreviewPane slug="me" stageNumber={3} settings={settings} projectName="Bromma" focus={TITLE} hover={null} enabled />,
    );
    await settle();
    expect(api.exportPreview).toHaveBeenCalledTimes(1);
    vi.mocked(api.exportPreview).mockResolvedValue(blob("second"));
    const edited = { ...settings, renderOptions: { ...settings.renderOptions, titleInfo: "L3" } };
    view.rerender(
      <PreviewPane slug="me" stageNumber={3} settings={edited} projectName="Bromma" focus={TITLE} hover={null} enabled />,
    );
    await settle();
    expect(api.exportPreview).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("img")).toHaveAttribute("src", "blob:0");
    await act(async () => {
      resolveFirst(blob("first, late"));
      await Promise.resolve();
      await Promise.resolve();
    });
    // The late first response never replaces the newer still.
    expect(screen.getByRole("img")).toHaveAttribute("src", "blob:0");
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("releases its object URL on unmount", async () => {
    const view = render(
      <PreviewPane slug="me" stageNumber={3} settings={settings} projectName="Bromma" focus={null} hover={null} enabled />,
    );
    await settle();
    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:0");
  });

  it("renders nothing and requests nothing while disabled", async () => {
    const { container } = render(
      <PreviewPane slug="me" stageNumber={3} settings={settings} projectName="Bromma" focus={null} hover={null} enabled={false} />,
    );
    await settle();
    expect(container).toBeEmptyDOMElement();
    expect(api.exportPreview).not.toHaveBeenCalled();
  });

  it("offers to install the renderer when the preview needs a browser, and retries once it lands", async () => {
    const OVERLAY = { slotId: "overlay" as const, variantId: "on" };
    vi.mocked(api.exportPreview).mockRejectedValue(new ApiError(503, "no browser"));
    vi.mocked(api.installChromium).mockResolvedValue({ id: "j1", kind: "chromium_install", status: "pending" } as never);
    vi.mocked(api.getJob).mockResolvedValue({ id: "j1", kind: "chromium_install", status: "succeeded" } as never);
    render(
      <PreviewPane slug="me" stageNumber={3} settings={settings} projectName="Bromma" focus={OVERLAY} hover={null} enabled />,
    );
    await settle();
    expect(screen.getByText("Preview needs a browser")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Install renderer (260 MB)" }));
    await settle(0);
    expect(api.installChromium).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Installing renderer")).toBeInTheDocument();
    vi.mocked(api.exportPreview).mockResolvedValue(blob("b"));
    await settle(1000);
    await settle();
    expect(api.exportPreview).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Installing renderer")).not.toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("src", "blob:0");
  });
});
