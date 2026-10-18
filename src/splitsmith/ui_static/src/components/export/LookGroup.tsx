/**
 * Look -- the cards, the overlay and the transitions as a gallery (spec
 * 2026-09-15 s2). What it offers and which mode and format can draw it
 * is ``lib/lookGallery``'s; this maps the page's bare-stage hints onto
 * the slots.
 */
import { LookAdvanced } from "@/components/export/LookAdvanced";
import { LookGallery } from "@/components/export/LookGallery";
import { Field } from "@/components/ui/Field";
import { bareHint } from "@/lib/exportPlan";
import { lookSoundsOffered } from "@/lib/looks";
import type { LookFocus } from "@/lib/exportPreview";
import type { ExportSettings } from "@/lib/exportPresets";
import { useMatchHref } from "@/lib/matchHref";
import { useLooks } from "@/lib/useLooks";

export interface LookGroupProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  bareSelected: number;
  onHover?: (focus: LookFocus | null) => void;
  onSelect?: (focus: LookFocus) => void;
  /** The Advanced row's editor previews on this shooter's stage (#1264). */
  slug: string;
  stageNumber: number;
  hosted: boolean;
}

export function LookGroup({
  settings,
  patch,
  busy,
  bareSelected,
  onHover,
  onSelect,
  slug,
  stageNumber,
  hosted,
}: LookGroupProps) {
  const compare = settings.mode === "compare";
  const { looks, transitions } = useLooks();
  const href = useMatchHref();
  const sounds = lookSoundsOffered(looks, settings.look, settings.mode, settings.outputFormat);
  return (
    <>
      <LookGallery
      looks={looks}
      transitions={transitions}
      settings={settings}
      patch={patch}
      busy={busy}
      bareHints={
        compare ? {} : { summaryHold: bareHint("summary", bareSelected), overlay: bareHint("overlay", bareSelected) }
      }
      onHover={onHover}
      onSelect={onSelect}
      lookExtras={
        <LookAdvanced
          looks={looks}
          look={settings.look}
          onChooseLook={(look) => patch({ look })}
          slug={slug}
          stageNumber={stageNumber}
          hosted={hosted}
          busy={busy}
          identityHref={`${href("ingest")}?identity=${encodeURIComponent(slug)}`}
        />
      }
      />
      {sounds ? (
        <Field label="Sounds" help="The Look's music and effects, mixed over the stage audio.">
          <label className="flex items-center gap-2 text-md text-ink-2">
            <input
              type="checkbox"
              aria-label="Look sounds"
              checked={settings.renderOptions.lookSounds}
              disabled={busy}
              onChange={(e) => patch({ renderOptions: { ...settings.renderOptions, lookSounds: e.target.checked } })}
              className="accent-[var(--color-ink)]"
            />
            Look sounds
          </label>
        </Field>
      ) : null}
    </>
  );
}
