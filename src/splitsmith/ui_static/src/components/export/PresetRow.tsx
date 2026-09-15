/**
 * PresetRow -- the Export page's preset picker (spec 2026-09-15 s1):
 * the built-ins, the user's own, and "Custom" once the form differs
 * from the active one. One `Segmented` (a closed choice) and a menu
 * for the actions. Red stays with the page's Export button.
 */
import { MoreHorizontal } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import { Segmented } from "@/components/ui/Segmented";
import type { ExportPreset } from "@/lib/api";
import { CUSTOM } from "@/lib/exportPresets";

export interface PresetRowProps {
  presets: ExportPreset[];
  /** The preset the form was last set from; null before any was applied. */
  activeId: string | null;
  dirty: boolean;
  busy: boolean;
  onApply: (id: string) => void;
  onSave: (id: string) => void;
  onSaveAs: () => void;
  onRename: (id: string) => void;
  onDelete: (id: string) => void;
}

export function PresetRow({
  presets,
  activeId,
  dirty,
  busy,
  onApply,
  onSave,
  onSaveAs,
  onRename,
  onDelete,
}: PresetRowProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const active = presets.find((p) => p.preset_id === activeId) ?? null;
  const own = active !== null && !active.builtin;
  const showCustom = dirty || active === null;
  const options = [
    ...presets.map((p) => ({ value: p.preset_id, label: p.name })),
    ...(showCustom ? [{ value: CUSTOM, label: "Custom" }] : []),
  ];
  const value = showCustom || active === null ? CUSTOM : active.preset_id;

  return (
    <div className="flex flex-wrap items-center gap-3 px-1">
      <Label>Preset</Label>
      <Segmented
        label="Preset"
        value={value}
        onChange={(id) => {
          if (id !== CUSTOM) onApply(id);
        }}
        options={options}
        disabled={busy}
      />
      {showCustom && active ? <span className="text-sm text-muted">from {active.name}</span> : null}
      <div className="relative">
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label="Preset actions"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((v) => !v)}
          disabled={busy}
        >
          <MoreHorizontal className="size-4" aria-hidden />
        </Button>
        <Menu open={menuOpen} onClose={() => setMenuOpen(false)} align="right">
          {own ? (
            <button
              type="button"
              role="menuitem"
              className={menuItemClass}
              disabled={!dirty}
              onClick={() => {
                setMenuOpen(false);
                onSave(active.preset_id);
              }}
            >
              Save
            </button>
          ) : null}
          <button
            type="button"
            role="menuitem"
            className={menuItemClass}
            onClick={() => {
              setMenuOpen(false);
              onSaveAs();
            }}
          >
            Save as...
          </button>
          {own ? (
            <>
              <button
                type="button"
                role="menuitem"
                className={menuItemClass}
                onClick={() => {
                  setMenuOpen(false);
                  onRename(active.preset_id);
                }}
              >
                Rename
              </button>
              <button
                type="button"
                role="menuitem"
                className={menuItemClass}
                onClick={() => {
                  setMenuOpen(false);
                  onDelete(active.preset_id);
                }}
              >
                Delete
              </button>
            </>
          ) : null}
        </Menu>
      </div>
    </div>
  );
}
