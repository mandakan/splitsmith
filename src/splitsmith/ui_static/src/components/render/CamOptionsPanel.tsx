/**
 * CamOptionsPanel -- the secondary-camera controls of the single-shooter
 * match export (#193; #974 item A): include the per-cam trims or not, and
 * stacked versus picture-in-picture corners.
 *
 * Self-contained and props-driven like RenderOptionsPanel: no state of
 * its own, no endpoint, no button; the rebuilt Export page (PR 8) mounts
 * it and feeds `syncedSecondaryCount` from the project. It renders
 * nothing at all when that count is zero -- a control for cameras the
 * shooter does not have is a question with no answer, not a disabled
 * row -- which is the "only offered when the shooter has a stage with a
 * secondary role" rule in one place.
 */
import * as React from "react";

import { Label } from "@/components/ui/Label";
import { cn } from "@/lib/utils";
import type { CamOptions, PipLayout } from "@/lib/camOptions";

export interface CamOptionsPanelProps {
  value: CamOptions;
  onChange: (next: CamOptions) => void;
  /** `syncedSecondaryCount(...)` for the stages the export will take. */
  secondaryCount: number;
  busy?: boolean;
  className?: string;
}

const LAYOUTS: ReadonlyArray<{ value: PipLayout; label: string }> = [
  { value: "stacked", label: "Stacked full-frame" },
  { value: "pip-corners", label: "Picture-in-picture corners" },
];

const FIELD = "rounded border border-rule bg-bg px-3 py-1.5 text-sm text-ink disabled:opacity-50";

export function CamOptionsPanel({
  value,
  onChange,
  secondaryCount,
  busy = false,
  className,
}: CamOptionsPanelProps) {
  const id = React.useId();
  if (secondaryCount <= 0) return null;
  const cams = secondaryCount === 1 ? "1 synced camera" : `${secondaryCount} synced cameras`;
  return (
    <section aria-labelledby={`${id}-heading`} data-testid="cam-options" className={cn("flex flex-col", className)}>
      <div className="flex items-baseline justify-between border-b border-rule py-2">
        <Label id={`${id}-heading`} tone="ink">
          Secondary cams
        </Label>
        <span className="text-sm tabular-nums text-muted">{cams}</span>
      </div>
      <div className="grid grid-cols-1 gap-2 border-b border-rule py-3 sm:grid-cols-[9rem_1fr] sm:gap-4">
        <Label>Include</Label>
        <div className="flex flex-wrap items-center gap-3">
          <label
            htmlFor={`${id}-include`}
            className={cn("inline-flex items-center gap-2 text-sm", busy && "opacity-50")}
          >
            <input
              id={`${id}-include`}
              type="checkbox"
              className="size-4 accent-[color:var(--color-ink)]"
              checked={value.includeSecondaries}
              disabled={busy}
              onChange={(e) => onChange({ ...value, includeSecondaries: e.target.checked })}
            />
            Ship the per-cam trims with the primary
          </label>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-2 border-b border-rule py-3 sm:grid-cols-[9rem_1fr] sm:gap-4">
        <Label>Layout</Label>
        <select
          id={`${id}-layout`}
          aria-label="Secondary cam layout"
          className={cn(FIELD, "w-fit")}
          value={value.pipLayout}
          disabled={busy || !value.includeSecondaries}
          onChange={(e) => onChange({ ...value, pipLayout: e.target.value as PipLayout })}
        >
          {LAYOUTS.map((layout) => (
            <option key={layout.value} value={layout.value}>
              {layout.label}
            </option>
          ))}
        </select>
      </div>
    </section>
  );
}
