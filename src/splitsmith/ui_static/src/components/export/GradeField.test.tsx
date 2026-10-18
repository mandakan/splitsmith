import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GradeField } from "@/components/export/GradeField";
import { api, type LookInfo } from "@/lib/api";
import { DEFAULT_EXPORT_SETTINGS } from "@/lib/exportPresets";
import { BUILTIN_LOOKS } from "@/lib/looks";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, listGrades: vi.fn(), listUserGrades: vi.fn(), uploadGrade: vi.fn() },
  };
});

const freedoom: LookInfo = { ...BUILTIN_LOOKS[0], name: "freedoom", label: "Freedoom", source: "user", grade: true };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listGrades).mockResolvedValue({
    presets: [{ id: "preset:noir", label: "Noir", preview: "/api/grades/preview?grade=preset:noir" }],
  });
  vi.mocked(api.listUserGrades).mockResolvedValue({ user: [] });
  vi.mocked(api.uploadGrade).mockReset();
});

function show(props: Partial<React.ComponentProps<typeof GradeField>> = {}) {
  const patch = vi.fn();
  render(
    <GradeField
      settings={{ ...DEFAULT_EXPORT_SETTINGS, look: "freedoom" }}
      patch={patch}
      looks={[BUILTIN_LOOKS[0], freedoom]}
      busy={false}
      hosted={false}
      {...props}
    />,
  );
  return patch;
}

describe("GradeField", () => {
  it("offers the Look's own grade, selected by default, then the presets", async () => {
    const patch = show();
    await screen.findByRole("radio", { name: "Noir" });
    expect(screen.getByRole("radio", { name: "Freedoom's grade" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("slider", { name: "Grade strength" })).toHaveValue("100");
    fireEvent.click(screen.getByRole("radio", { name: "Noir" }));
    expect(patch).toHaveBeenCalledWith({ grade: "preset:noir" });
  });

  it("hides the strength when nothing grades, and the upload on hosted", async () => {
    show({ settings: { ...DEFAULT_EXPORT_SETTINGS, look: "freedoom", grade: "none" }, hosted: true });
    await screen.findByRole("radio", { name: "Noir" });
    expect(screen.queryByRole("slider", { name: "Grade strength" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add LUT" })).toBeNull();
    expect(api.listUserGrades).not.toHaveBeenCalled();
  });

  it("adds your LUT and chooses it", async () => {
    vi.mocked(api.uploadGrade).mockResolvedValue({ id: "user:film-0123456789ab.cube", label: "film", preview: "/p" });
    const patch = show();
    const input = screen.getByLabelText("LUT file");
    fireEvent.change(input, { target: { files: [new File(["LUT_3D_SIZE 2"], "film.cube")] } });
    await waitFor(() => expect(patch).toHaveBeenCalledWith({ grade: "user:film-0123456789ab.cube" }));
    expect(await screen.findByRole("radio", { name: "film" })).toBeInTheDocument();
  });
});
