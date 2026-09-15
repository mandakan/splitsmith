/**
 * Cut -- how each stage is trimmed (spec 2026-09-15 s1). Single-shooter
 * timeline only; trims pad with the project's own buffers and the grid
 * has no cut to shape. Transitions are a Look slot since part 2.
 */
import { Field, inputClass } from "@/components/ui/Field";
import { Segmented } from "@/components/ui/Segmented";
import { PADDING_PRESETS, type ExportSettings, type PaddingPreset } from "@/lib/exportPresets";
import { cn } from "@/lib/utils";

export interface CutGroupProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
}

export function CutGroup({ settings, patch, busy }: CutGroupProps) {
  const { paddingPreset, headPad, tailPad } = settings;
  function selectPadding(next: PaddingPreset) {
    if (next === "custom") patch({ paddingPreset: next });
    else patch({ paddingPreset: next, headPad: PADDING_PRESETS[next].head, tailPad: PADDING_PRESETS[next].tail });
  }
  return (
    <>
      <Field
        label="Padding"
        help={`${headPad.toFixed(1)} s before the beep · ${tailPad.toFixed(1)} s after the last shot`}
      >
        <div className="flex flex-wrap items-center gap-3">
          <Segmented<PaddingPreset>
            label="Trim padding"
            value={paddingPreset}
            onChange={selectPadding}
            disabled={busy}
            options={[
              ...(Object.keys(PADDING_PRESETS) as Array<Exclude<PaddingPreset, "custom">>).map((k) => ({
                value: k as PaddingPreset,
                label: PADDING_PRESETS[k].label,
              })),
              { value: "custom" as PaddingPreset, label: "Custom" },
            ]}
          />
          {paddingPreset === "custom" ? (
            <>
              <NumInput label="Before beep (s)" value={headPad} step={0.1} min={0} onChange={(v) => patch({ headPad: v })} />
              <NumInput
                label="After last shot (s)"
                value={tailPad}
                step={0.1}
                min={0}
                onChange={(v) => patch({ tailPad: v })}
              />
            </>
          ) : null}
        </div>
      </Field>
    </>
  );
}

function NumInput({
  label,
  value,
  step,
  min,
  onChange,
}: {
  label: string;
  value: number;
  step?: number;
  min?: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="inline-flex items-center gap-2 text-sm text-muted">
      {label}
      <input
        type="number"
        value={value}
        step={step}
        min={min}
        onChange={(e) => {
          const n = parseFloat(e.target.value);
          if (Number.isFinite(n)) onChange(n);
        }}
        className={cn(inputClass, "w-20 font-mono text-sm")}
      />
    </label>
  );
}
