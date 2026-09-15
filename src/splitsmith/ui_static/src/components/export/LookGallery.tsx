/**
 * LookGallery -- the Look group as tiles (spec 2026-09-15 s2). One Field
 * per slot the mode and format can draw: a radio group of tiles
 * (thumbnail plus name, neutral outline, a tick on the selected one),
 * the selected variant's parameters beside them, and its help line
 * under the row. Everything it offers comes from ``lib/lookGallery``;
 * it owns no state and knows no endpoint.
 */
import { Seconds } from "@/components/export/Seconds";
import { Field } from "@/components/ui/Field";
import type { ExportSettings } from "@/lib/exportPresets";
import {
  thumbnailUrl,
  variantHelp,
  visibleSlots,
  visibleVariants,
  type LookSlot,
  type LookSlotId,
  type LookVariant,
} from "@/lib/lookGallery";
import { cn } from "@/lib/utils";

export interface LookGalleryProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  /** Per slot, the line about the selected stages exporting without
   *  splits (``bareHint``); appended to the help while the variant is on. */
  bareHints: Partial<Record<LookSlotId, string | null>>;
}

export function LookGallery({ settings, patch, busy, bareHints }: LookGalleryProps) {
  const format = settings.mode === "compare" ? "mp4" : settings.outputFormat;
  return (
    <>
      {visibleSlots(settings.mode, format).map((slot) => (
        <SlotRow
          key={slot.id}
          slot={slot}
          variants={visibleVariants(slot, settings.mode, format)}
          settings={settings}
          patch={patch}
          busy={busy}
          bareHint={bareHints[slot.id] ?? null}
        />
      ))}
    </>
  );
}

function SlotRow({
  slot,
  variants,
  settings,
  patch,
  busy,
  bareHint,
}: {
  slot: LookSlot;
  variants: LookVariant[];
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  bareHint: string | null;
}) {
  const selectedId = slot.read(settings);
  const selected = variants.find((v) => v.id === selectedId) ?? variants[0];
  const on = selected.id !== slot.variants[0].id;
  const params = selected.params.filter((p) => !p.modes || p.modes.includes(settings.mode));
  const help = variantHelp(selected, settings.mode);
  return (
    <Field label={slot.label} help={on && bareHint ? `${help} ${bareHint}` : help}>
      <div className="flex flex-wrap items-start gap-3">
        <div role="radiogroup" aria-label={slot.label} className="flex flex-wrap gap-2">
          {variants.map((v) => {
            const checked = v.id === selected.id;
            return (
              <button
                key={v.id}
                type="button"
                role="radio"
                aria-checked={checked}
                disabled={busy}
                onClick={() => {
                  if (!checked) patch(slot.write(settings, v.id));
                }}
                className={cn(
                  "flex w-40 flex-col gap-1.5 rounded-md border p-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led disabled:opacity-50",
                  checked ? "border-ink bg-surface-2" : "border-rule-strong hover:border-ink-2",
                )}
              >
                <img
                  src={thumbnailUrl(v.thumbnail)}
                  alt=""
                  className="aspect-video w-full rounded-sm bg-surface-3 object-cover"
                />
                <span className="inline-flex items-center gap-1.5 text-sm text-ink-2">
                  {checked ? <i data-tick aria-hidden className="size-1.5 shrink-0 rounded-full bg-ink" /> : null}
                  {v.name}
                </span>
              </button>
            );
          })}
        </div>
        {params.length > 0 ? (
          <div className="flex flex-wrap items-center gap-3 sm:pt-2">
            {params.map((p) => (
              <Seconds
                key={p.id}
                id={`look-${slot.id}-${p.id}`}
                label={p.label}
                value={p.read(settings)}
                min={p.min}
                disabled={busy}
                onChange={(n) => patch(p.write(settings, n))}
              />
            ))}
          </div>
        ) : null}
      </div>
    </Field>
  );
}
