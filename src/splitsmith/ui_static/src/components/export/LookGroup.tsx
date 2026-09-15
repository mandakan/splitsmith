/**
 * Look -- the cards and the overlay (spec 2026-09-15 s1). The cards
 * panel is shared by the timeline and the grid; the overlay control is
 * the mode's own (the grid's carries its summary hold). Part 2 of the
 * spec turns this group into the gallery.
 */
import { RenderOptionsPanel, Seconds } from "@/components/render/RenderOptionsPanel";
import { Field } from "@/components/ui/Field";
import { Segmented } from "@/components/ui/Segmented";
import { bareHint } from "@/lib/exportPlan";
import type { ExportSettings } from "@/lib/exportPresets";

export interface LookGroupProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  bareSelected: number;
}

export function LookGroup({ settings, patch, busy, bareSelected }: LookGroupProps) {
  const { mode, outputFormat, renderOptions, includeOverlay, gridOverlay, gridHoldSeconds } = settings;
  const compare = mode === "compare";
  return (
    <>
      <RenderOptionsPanel
        value={renderOptions}
        onChange={(v) => patch({ renderOptions: v })}
        surface={compare ? "grid" : "single"}
        outputFormat={compare ? "mp4" : outputFormat}
        busy={busy}
        summaryHint={compare ? null : bareHint("summary", bareSelected)}
      />
      {compare ? (
        <Field
          label="Overlay"
          help={
            gridOverlay
              ? "Per-tile shot counter and split with the running clock; the summary holds each tile's own stage figures after its last shot, 0 is off."
              : "Off: a faster render with no counters on the tiles."
          }
        >
          <div className="flex flex-wrap items-center gap-3">
            <Segmented<"off" | "on">
              label="Grid overlay"
              value={gridOverlay ? "on" : "off"}
              onChange={(v) => patch({ gridOverlay: v === "on" })}
              options={[
                { value: "off", label: "None" },
                { value: "on", label: "Counter + splits" },
              ]}
            />
            {gridOverlay ? (
              <Seconds
                id="export-grid-hold"
                label="Grid summary hold seconds"
                value={gridHoldSeconds}
                min={0}
                disabled={busy}
                onChange={(v) => patch({ gridHoldSeconds: v })}
              />
            ) : null}
          </div>
        </Field>
      ) : (
        <Field
          label="Overlay"
          help={
            includeOverlay
              ? `Burned-in shot counter and splits; a slower render. The codec is under Output.${bareHint("overlay", bareSelected) ? ` ${bareHint("overlay", bareSelected)}` : ""}`
              : "Off: a faster export; the FCPXML still carries shot markers."
          }
        >
          <Segmented<"off" | "on">
            label="Overlay"
            value={includeOverlay ? "on" : "off"}
            onChange={(v) => patch({ includeOverlay: v === "on" })}
            options={[
              { value: "off", label: "None" },
              { value: "on", label: "Shot counter + splits" },
            ]}
          />
        </Field>
      )}
    </>
  );
}
