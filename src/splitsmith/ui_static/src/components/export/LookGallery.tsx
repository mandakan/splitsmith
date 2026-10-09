/**
 * LookGallery -- the Look group as tiles (spec 2026-09-15 s2). One Field
 * per slot the mode and format can draw: a radio group of tiles
 * (thumbnail plus name, neutral outline, a tick on the selected one),
 * the selected variant's parameters beside them, and its help line
 * under the row. Everything it offers comes from ``lib/lookGallery``;
 * it owns no state and knows no endpoint.
 */
import type { ReactNode } from "react";

import { Seconds } from "@/components/export/Seconds";
import { Field } from "@/components/ui/Field";
import { Segmented } from "@/components/ui/Segmented";
import type { LookInfo, TransitionFamilyInfo } from "@/lib/api";
import type { LookFocus } from "@/lib/exportPreview";
import type { ExportSettings } from "@/lib/exportPresets";
import {
  VARIANT_FIELD,
  slotsForLook,
  thumbnailUrl,
  variantHelp,
  visibleSlots,
  visibleVariants,
  type LookSlot,
  type LookSlotId,
  type LookVariant,
} from "@/lib/lookGallery";
import { previewSrc, variantsFor, visibleVariant } from "@/lib/looks";
import { cn } from "@/lib/utils";

export interface LookGalleryProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  /** Per slot, the line about the selected stages exporting without
   *  splits (``bareHint``); appended to the help while the variant is on. */
  bareHints: Partial<Record<LookSlotId, string | null>>;
  /** The tile under the pointer (or keyboard focus), for the rail's generic preview. */
  onHover?: (focus: LookFocus | null) => void;
  /** A tile was picked; the rail previews it on this match. */
  onSelect?: (focus: LookFocus) => void;
  /** The installed Looks (#1246): the Look tiles, each card slot's Style
   *  and the chosen Look's stings come from it. */
  looks: LookInfo[];
  /** The server's xfade families (#1259): the transition tiles and their directions. */
  transitions?: TransitionFamilyInfo[];
  /** Beside the Look tiles: the way into your own Looks (``LookAdvanced``). */
  lookExtras?: ReactNode;
}

export function LookGallery({
  settings,
  patch,
  busy,
  bareHints,
  onHover,
  onSelect,
  looks,
  transitions = [],
  lookExtras,
}: LookGalleryProps) {
  const format = settings.mode === "compare" ? "mp4" : settings.outputFormat;
  return (
    <>
      {visibleSlots(settings.mode, format, slotsForLook(looks, settings, transitions)).map((slot) => (
        <SlotRow
          key={slot.id}
          slot={slot}
          variants={visibleVariants(slot, settings.mode, format)}
          settings={settings}
          patch={patch}
          busy={busy}
          bareHint={bareHints[slot.id] ?? null}
          onHover={onHover}
          onSelect={onSelect}
          looks={looks}
          extras={slot.id === "look" ? lookExtras : undefined}
        />
      ))}
    </>
  );
}

const capitalise = (name: string) => name.charAt(0).toUpperCase() + name.slice(1).replace(/[-_]/g, " ");

function SlotRow({
  slot,
  variants,
  settings,
  patch,
  busy,
  bareHint,
  onHover,
  onSelect,
  looks,
  extras,
}: {
  slot: LookSlot;
  variants: LookVariant[];
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  bareHint: string | null;
  onHover?: (focus: LookFocus | null) => void;
  onSelect?: (focus: LookFocus) => void;
  looks: LookInfo[];
  extras?: ReactNode;
}) {
  const selectedId = slot.read(settings);
  const selected = variants.find((v) => v.id === selectedId) ?? variants[0];
  const on = selected.id !== slot.variants[0].id;
  const params = selected.params.filter((p) => !p.modes || p.modes.includes(settings.mode));
  const help = variantHelp(selected, settings.mode);
  // The card's template variant (#1246): a Style under the tiles while
  // the card is on and the chosen Look has more than one to offer.
  const styleField = VARIANT_FIELD[slot.id];
  const styles = styleField ? variantsFor(looks, settings.look, styleField.slot) : [];
  const style =
    styleField && on && styles.length > 1
      ? {
          value: visibleVariant(looks, settings.look, styleField.slot, settings[styleField.field]),
          options: styles.map((v) => ({ value: v.name, label: capitalise(v.name) })),
          write: (value: string) => patch({ [styleField.field]: value } as Partial<ExportSettings>),
        }
      : null;
  // A transition family's direction (#1259), under its tile while it is picked.
  const directions = selected.directions ?? [];
  const direction =
    directions.length > 1
      ? {
          value: (directions.find((d) => d.kind === settings.transitionKind) ?? directions[0]).name,
          options: directions.map((d) => ({ value: d.name, label: capitalise(d.name) })),
          write: (value: string) => {
            const hit = directions.find((d) => d.name === value);
            if (hit) patch({ transitionKind: hit.kind });
          },
        }
      : null;
  return (
    <Field label={slot.label} help={on && bareHint ? `${help} ${bareHint}` : help}>
      <div className="flex flex-wrap items-start gap-3">
        <div role="radiogroup" aria-label={slot.label} className="flex flex-wrap gap-2">
          {variants.map((v) => {
            const checked = v.id === selected.id;
            const focus: LookFocus = { slotId: slot.id, variantId: v.id };
            return (
              <button
                key={v.id}
                type="button"
                role="radio"
                aria-checked={checked}
                disabled={busy}
                onClick={() => {
                  if (!checked) patch(slot.write(settings, v.id));
                  onSelect?.(focus);
                }}
                onMouseEnter={() => onHover?.(focus)}
                onMouseLeave={() => onHover?.(null)}
                onFocus={() => onHover?.(focus)}
                onBlur={() => onHover?.(null)}
                className={cn(
                  "flex w-40 flex-col gap-1.5 rounded-md border p-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led disabled:opacity-50",
                  checked ? "border-ink bg-surface-2" : "border-rule-strong hover:border-ink-2",
                )}
              >
                <img
                  src={previewSrc(v.previewUrl ?? null) ?? thumbnailUrl(v.thumbnail)}
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
        {extras}
        {direction ? (
          <div className="sm:pt-2">
            <Segmented
              label={`${slot.label} direction`}
              value={direction.value}
              options={direction.options}
              onChange={direction.write}
              disabled={busy}
            />
          </div>
        ) : null}
        {style ? (
          <div className="sm:pt-2">
            <Segmented
              label={`${slot.label} style`}
              value={style.value}
              options={style.options}
              onChange={style.write}
              disabled={busy}
            />
          </div>
        ) : null}
        {on && selected.choice ? (
          <div className="sm:pt-2">
            <Segmented
              label={selected.choice.label}
              value={selected.choice.read(settings)}
              options={selected.choice.options}
              onChange={(value) => selected.choice && patch(selected.choice.write(settings, value))}
              disabled={busy}
            />
          </div>
        ) : null}
        {on && selected.toggles && selected.toggles.length > 0 ? (
          <div className="flex flex-wrap items-center gap-3 sm:pt-2">
            {selected.toggles.map((t) => (
              <label key={t.id} className="flex items-center gap-2 text-md text-ink-2">
                <input
                  type="checkbox"
                  aria-label={t.label}
                  checked={t.read(settings)}
                  disabled={busy}
                  onChange={(e) => patch(t.write(settings, e.target.checked))}
                  className="accent-[var(--color-ink)]"
                />
                {t.label}
              </label>
            ))}
            {selected.toggles
              .filter((t) => t.hint)
              .map((t) => (
                <p key={`${t.id}-hint`} className="basis-full text-sm text-muted">
                  {t.hint}
                </p>
              ))}
          </div>
        ) : null}
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
