/**
 * The Export page's Look preflight (#1276): which cards the chosen Look's
 * own templates would leave out, worded as the UI names cards. Pure.
 */
import type { CheckFinding } from "@/lib/api";
import { templateLabel } from "@/lib/templateEditor";

function joinNames(names: string[]): string {
  return names.length <= 1 ? (names[0] ?? "") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** One line per distinct failure, "<cards>: <why>": the cards one broken
 *  file draws (the shipped ``card.html`` draws four) share a line. Warnings
 *  are the editor's to show. */
export function lookFailures(items: readonly CheckFinding[]): string[] {
  const byMessage = new Map<string, string[]>();
  for (const i of items) {
    if (i.level !== "error" || i.subject.split(" ").length < 2) continue;
    const [slot, variant] = i.subject.split(" ");
    const cards = byMessage.get(i.message) ?? [];
    cards.push(templateLabel({ slot, variant }));
    byMessage.set(i.message, cards);
  }
  return [...byMessage.entries()].map(([message, cards]) => `${joinNames(cards)}: ${message}`);
}
