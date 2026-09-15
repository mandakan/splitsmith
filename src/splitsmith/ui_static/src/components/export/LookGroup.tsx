/**
 * Look -- the cards, the overlay and the transitions as a gallery (spec
 * 2026-09-15 s2). What it offers and which mode and format can draw it
 * is ``lib/lookGallery``'s; this maps the page's bare-stage hints onto
 * the slots.
 */
import { LookGallery } from "@/components/export/LookGallery";
import { bareHint } from "@/lib/exportPlan";
import type { LookFocus } from "@/lib/exportPreview";
import type { ExportSettings } from "@/lib/exportPresets";

export interface LookGroupProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  bareSelected: number;
  onHover?: (focus: LookFocus | null) => void;
  onSelect?: (focus: LookFocus) => void;
}

export function LookGroup({ settings, patch, busy, bareSelected, onHover, onSelect }: LookGroupProps) {
  const compare = settings.mode === "compare";
  return (
    <LookGallery
      settings={settings}
      patch={patch}
      busy={busy}
      bareHints={
        compare ? {} : { summaryHold: bareHint("summary", bareSelected), overlay: bareHint("overlay", bareSelected) }
      }
      onHover={onHover}
      onSelect={onSelect}
    />
  );
}
