/**
 * What's new: the sheet that opens once when there is something you have
 * not seen, the bar's button that reopens it, and the "New" chip a feature
 * wears until you use it. Data and rules: ``lib/whatsNew``,
 * ``lib/useWhatsNew``; entries: ``data/whats_new.json``.
 */
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import { Sheet } from "@/components/ui/Sheet";
import { closeWhatsNew, openWhatsNew, useWhatsNew } from "@/lib/useWhatsNew";
import { byMonth, unseen } from "@/lib/whatsNew";

export function WhatsNewSheet() {
  const { payload, open } = useWhatsNew();
  if (!payload) return null;
  const fresh = new Set(unseen(payload.entries, payload.seen).map((e) => e.id));
  // Unseen first; with nothing new, the whole history.
  const shown = fresh.size > 0 ? payload.entries.filter((e) => fresh.has(e.id)) : payload.entries;
  return (
    <Sheet open={open} onClose={closeWhatsNew} label="What's new">
      <div className="border-b border-rule px-5 py-4">
        <PageHeader title="What's new" className="mb-0" />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {byMonth(shown).map((group) => (
          <section key={group.month} className="mb-5">
            <Label>{group.month}</Label>
            <ul className="mt-2">
              {group.entries.map((e) => (
                <li key={e.id} className="border-b border-rule py-3 last:border-b-0">
                  <h3 className="text-base font-medium text-ink">{e.title}</h3>
                  <p className="mt-1 text-md text-ink-2">{e.body}</p>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <div className="flex justify-end border-t border-rule px-5 py-3">
        <Button variant="primary" onClick={closeWhatsNew}>
          Got it
        </Button>
      </div>
    </Sheet>
  );
}

/** The bar's way back to the sheet; a dot while something is unseen. */
export function WhatsNewButton() {
  const { payload, unseenCount } = useWhatsNew();
  if (!payload) return null;
  return (
    <Button variant="ghost" size="sm" onClick={openWhatsNew} aria-label={unseenCount > 0 ? `What's new, ${unseenCount} unseen` : "What's new"}>
      What&apos;s new
      {unseenCount > 0 ? <span aria-hidden className="size-1.5 rounded-full bg-live" /> : null}
    </Button>
  );
}

/** "New" beside a feature until it is used (``dismissNewChip``) or its entry is old. */
export function NewChip({ feature }: { feature: string }) {
  const { chip } = useWhatsNew();
  return chip(feature) ? <Chip>New</Chip> : null;
}
