/**
 * LogoPlanField -- the Logos row under Details (spec 2026-10-09 logo spots):
 * Cards only, Polished, or Choose with one checkbox per spot. The rules and
 * the copy are ``lib/logoPlan``; this owns the control and whether Choose is
 * open, which a preset click closes.
 */
import { useState } from "react";

import { Field } from "@/components/ui/Field";
import { Segmented } from "@/components/ui/Segmented";
import {
  LOGO_SPOTS,
  SPOT_COPY,
  logoPlanHelp,
  presetFor,
  spotsForPreset,
  toggleSpot,
  type LogoPreset,
  type LogoSpot,
} from "@/lib/logoPlan";

const OPTIONS = [
  { value: "cards", label: "Cards only" },
  { value: "polished", label: "Polished" },
  { value: "custom", label: "Choose" },
] as const;

export function LogoPlanField({
  spots,
  onChange,
  busy,
}: {
  spots: LogoSpot[];
  onChange: (spots: LogoSpot[]) => void;
  busy: boolean;
}) {
  const [choosing, setChoosing] = useState(() => presetFor(spots) === "custom");
  const preset: LogoPreset = choosing ? "custom" : presetFor(spots);
  return (
    <Field label="Logos" help={logoPlanHelp(spots)}>
      <div className="flex flex-col gap-2">
        <Segmented<LogoPreset>
          label="Logos"
          value={preset}
          options={OPTIONS}
          disabled={busy}
          onChange={(next) => {
            setChoosing(next === "custom");
            onChange(spotsForPreset(next, spots));
          }}
        />
        {preset === "custom" ? (
          <div className="flex flex-col gap-1.5">
            {LOGO_SPOTS.map((spot) => (
              <label key={spot} className="flex items-start gap-2 text-md text-ink-2">
                <input
                  type="checkbox"
                  aria-label={SPOT_COPY[spot].label}
                  checked={spots.includes(spot)}
                  disabled={busy}
                  onChange={(e) => onChange(toggleSpot(spots, spot, e.target.checked))}
                  className="mt-1 accent-[var(--color-ink)]"
                />
                <span>
                  <span className="text-ink">{SPOT_COPY[spot].label}</span>
                  <span className="block text-sm text-muted">{SPOT_COPY[spot].help}</span>
                </span>
              </label>
            ))}
          </div>
        ) : null}
      </div>
    </Field>
  );
}
