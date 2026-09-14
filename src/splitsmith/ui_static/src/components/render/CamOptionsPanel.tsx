/**
 * CamOptionsPanel -- the secondary-camera rows of the single-shooter
 * match export (#193; #974 item A): include the per-cam trims or not,
 * and stacked versus picture-in-picture corners.
 *
 * Props-driven like RenderOptionsPanel: no state of its own, no
 * endpoint, no button; the Export page mounts it in its Options section
 * and feeds `syncedSecondaryCount` from the project. It renders nothing
 * at all when that count is zero -- a control for cameras the shooter
 * does not have is a question with no answer, not a disabled row --
 * which is the "only offered when the shooter has a stage with a
 * secondary role" rule in one place.
 */
import { Field } from "@/components/ui/Field";
import { Segmented } from "@/components/ui/Segmented";
import type { CamOptions, PipLayout } from "@/lib/camOptions";

export interface CamOptionsPanelProps {
  value: CamOptions;
  onChange: (next: CamOptions) => void;
  /** `syncedSecondaryCount(...)` for the stages the export will take. */
  secondaryCount: number;
  busy?: boolean;
}

const LAYOUTS: ReadonlyArray<{ value: PipLayout; label: string }> = [
  { value: "stacked", label: "Stacked full-frame" },
  { value: "pip-corners", label: "Picture-in-picture" },
];

export function CamOptionsPanel({ value, onChange, secondaryCount, busy = false }: CamOptionsPanelProps) {
  if (secondaryCount <= 0) return null;
  const cams = secondaryCount === 1 ? "1 synced camera" : `${secondaryCount} synced cameras`;
  return (
    <Field
      label="Secondary cams"
      hint={<span className="numeral">{cams}</span>}
      help={
        value.includeSecondaries
          ? value.pipLayout === "stacked"
            ? "Each cam's trim covers the primary full-frame on its own track."
            : "Each cam scaled into a rotating corner of the primary."
          : "Primary only; the cam trims stay out of the bundle."
      }
    >
      <div className="flex flex-wrap items-center gap-3">
        <Segmented<"on" | "off">
          label="Secondary cams"
          value={value.includeSecondaries ? "on" : "off"}
          disabled={busy}
          onChange={(v) => onChange({ ...value, includeSecondaries: v === "on" })}
          options={[
            { value: "on", label: "Include" },
            { value: "off", label: "Primary only" },
          ]}
        />
        {value.includeSecondaries ? (
          <Segmented<PipLayout>
            label="Secondary cam layout"
            value={value.pipLayout}
            disabled={busy}
            onChange={(layout) => onChange({ ...value, pipLayout: layout })}
            options={LAYOUTS}
          />
        ) : null}
      </div>
    </Field>
  );
}
