/**
 * The template editor's data (spec 2026-10-07 s6, #1265): how a template is
 * named, which preview card shows it, and the unsaved text per slot and
 * variant that the preview, the check and Save send. Pure.
 */
import type { PreviewCard, TemplateEdit, TemplateInfo } from "@/lib/api";

const SLOT_LABELS: Record<string, string> = {
  title_page: "Title page",
  slate: "Stage slate",
  lower_third: "Lower third",
  closing: "Closing card",
  transition: "Sting",
};

const SLOT_CARDS: Record<string, PreviewCard> = {
  title_page: "title",
  slate: "slate",
  lower_third: "lower-third",
  closing: "closing",
  transition: "sting",
};

const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).replace(/[-_]/g, " ");

/** Unsaved template text, keyed ``slot/variant``. */
export type TemplateEdits = Record<string, string>;

export function templateKey(slot: string, variant: string): string {
  return `${slot}/${variant}`;
}

export function templateLabel(t: Pick<TemplateInfo, "slot" | "variant">): string {
  const slot = SLOT_LABELS[t.slot] ?? t.slot;
  return t.variant === "default" && t.slot !== "transition" ? slot : `${slot}, ${titleCase(t.variant)}`;
}

/** The preview card that shows ``slot`` and the variant it should draw. */
export function previewFocusFor(slot: string, variant: string): { card: PreviewCard; variant: string } {
  return { card: SLOT_CARDS[slot] ?? "title", variant };
}

/** Where ``t``'s unsaved text is kept: one key per own file, so every card
 *  that file draws shows and saves the same text; a borrowed template keys
 *  by its own slot and variant (it gets a file of its own on save). */
export function editKey(templates: readonly TemplateInfo[], t: TemplateInfo): string {
  if (!t.own) return templateKey(t.slot, t.variant);
  const first = templates.find((o) => o.own && o.file === t.file) ?? t;
  return templateKey(first.slot, first.variant);
}

export function templateText(t: TemplateInfo, edits: TemplateEdits, templates: readonly TemplateInfo[] = [t]): string {
  return edits[editKey(templates, t)] ?? t.content;
}

export function editsList(edits: TemplateEdits): TemplateEdit[] {
  return Object.entries(edits).map(([key, content]) => {
    const [slot, variant] = key.split("/");
    return { slot, variant, content };
  });
}

/** The other cards ``t``'s file draws (a Look whose one ``card.html`` is
 *  every card's default): editing it changes them all, so the tab says so.
 *  Only the Look's own files; a borrowed one is copied on first save. */
export function sharedWith(templates: readonly TemplateInfo[], t: TemplateInfo): string[] {
  if (!t.own) return [];
  return templates
    .filter((o) => o.own && o.file === t.file && !(o.slot === t.slot && o.variant === t.variant))
    .map((o) => templateLabel(o));
}
