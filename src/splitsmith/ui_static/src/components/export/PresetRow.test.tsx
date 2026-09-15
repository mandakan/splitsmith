import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PresetRow } from "@/components/export/PresetRow";
import type { ExportPreset } from "@/lib/api";
import { DEFAULT_EXPORT_SETTINGS, settingsToBody } from "@/lib/exportPresets";

function preset(id: string, name: string, builtin = false): ExportPreset {
  return {
    preset_id: id,
    name,
    builtin,
    updated_at: "2026-09-15T00:00:00Z",
    body: settingsToBody(DEFAULT_EXPORT_SETTINGS),
  };
}

const PRESETS = [preset("builtin:final-cut", "Final Cut bundle", true), preset("p1", "Club night")];

function setup(over: Partial<React.ComponentProps<typeof PresetRow>> = {}) {
  const props = {
    presets: PRESETS,
    activeId: "builtin:final-cut",
    dirty: false,
    busy: false,
    onApply: vi.fn(),
    onSave: vi.fn(),
    onSaveAs: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    ...over,
  };
  render(<PresetRow {...props} />);
  return { ...props, user: userEvent.setup() };
}

describe("PresetRow", () => {
  it("lists every preset and marks the active one", () => {
    setup();
    const group = screen.getByRole("group", { name: "Preset" });
    expect(within(group).getByRole("button", { name: "Final Cut bundle" })).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "Club night" })).toHaveAttribute("aria-pressed", "false");
    expect(within(group).queryByRole("button", { name: /custom/i })).toBeNull();
  });

  it("applies on click", async () => {
    const { user, onApply } = setup();
    await user.click(screen.getByRole("button", { name: "Club night" }));
    expect(onApply).toHaveBeenCalledWith("p1");
  });

  it("shows Custom, pressed, when the form is dirty", () => {
    setup({ dirty: true });
    expect(screen.getByRole("button", { name: "Custom" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Final Cut bundle" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("from Final Cut bundle")).toBeInTheDocument();
  });

  it("offers Save as on a built-in and nothing else", async () => {
    const { user, onSaveAs } = setup({ dirty: true });
    await user.click(screen.getByRole("button", { name: "Preset actions" }));
    expect(screen.getByRole("menuitem", { name: "Save as..." })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Delete" })).toBeNull();
    await user.click(screen.getByRole("menuitem", { name: "Save as..." }));
    expect(onSaveAs).toHaveBeenCalled();
  });

  it("on an own preset every action is offered", async () => {
    const { user, onSave, onDelete } = setup({ activeId: "p1", dirty: true });
    await user.click(screen.getByRole("button", { name: "Preset actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Save" }));
    expect(onSave).toHaveBeenCalledWith("p1");
    await user.click(screen.getByRole("button", { name: "Preset actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalledWith("p1");
  });

  it("Save is disabled while clean", async () => {
    const { user } = setup({ activeId: "p1", dirty: false });
    await user.click(screen.getByRole("button", { name: "Preset actions" }));
    expect(screen.getByRole("menuitem", { name: "Save" })).toBeDisabled();
  });

  it("with no active preset the row is Custom alone", () => {
    setup({ activeId: null, presets: [] });
    expect(screen.getByRole("button", { name: "Custom" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText(/^from /)).toBeNull();
  });
});
