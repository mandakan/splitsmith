/**
 * BrandPicker -- your brand in the Look editor's Card styles tab (the
 * branding work): a logo and a line the title page and the closing card
 * draw in their top left corner, for a club, a personal brand or a sponsor.
 * The logo is a file in the Look's folder (desktop; ``look_brand``), the
 * line is text; both ride the draft and reach the cards on Save. Hosted
 * Looks keep the line only until Looks have a file store.
 */
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/ui/Field";
import { Label } from "@/components/ui/Label";
import type { StoredLookBody } from "@/lib/api";
import { cn } from "@/lib/utils";

const LINE_MAX = 60;

export function BrandPicker({
  name,
  draft,
  setDraft,
  upload,
  hosted,
}: {
  name: string;
  draft: StoredLookBody;
  setDraft: (d: StoredLookBody) => void;
  /** Stores the file in the Look; rejects with the server's reason. */
  upload: (file: File) => Promise<{ logo: string; url: string }>;
  hosted: boolean;
}) {
  const inputId = useId();
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const brand = draft.brand ?? { logo: null, line: "" };
  const write = (next: { logo: string | null; line: string }) =>
    setDraft({ ...draft, brand: next.logo || next.line ? next : null });

  async function add(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setRefused(null);
    try {
      const stored = await upload(file);
      write({ ...brand, logo: stored.logo });
    } catch (err) {
      setRefused(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mb-4 flex flex-col gap-3 border-b border-rule pb-4">
      <div className="flex items-baseline gap-2">
        <Label>Your brand</Label>
        <span className="text-sm text-muted">
          The title page and the closing card, top left corner
        </span>
      </div>
      {hosted ? (
        <p className="text-sm text-muted">
          A brand logo is added in the desktop app for now; the line works here.
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <span className="flex h-16 w-28 items-center justify-center overflow-hidden rounded-md border border-rule bg-surface-3">
            {brand.logo ? (
              <img
                src={`/api/looks/${encodeURIComponent(name)}/brand/${encodeURIComponent(brand.logo)}`}
                alt="Brand logo"
                className="max-h-full max-w-full object-contain"
              />
            ) : (
              <span className="text-sm text-subtle">No logo</span>
            )}
          </span>
          <label
            htmlFor={inputId}
            className={cn(
              "cursor-pointer rounded-md border border-rule-strong px-3 py-1.5 text-sm text-ink hover:border-ink-2",
              busy && "pointer-events-none opacity-60",
            )}
          >
            {brand.logo ? "Replace logo" : "Add a logo"}
            <input
              id={inputId}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              aria-label="Brand logo file"
              className="sr-only"
              disabled={busy}
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                void add(file);
              }}
            />
          </label>
          {brand.logo ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => write({ ...brand, logo: null })}
            >
              Remove logo
            </Button>
          ) : null}
        </div>
      )}
      <label className="flex flex-col gap-1">
        <span className="text-sm text-muted">
          Line under it: a club, a sponsor, a name
        </span>
        <input
          aria-label="Brand line"
          className={cn(inputClass, "max-w-[320px]")}
          value={brand.line}
          maxLength={LINE_MAX}
          onChange={(e) => write({ ...brand, line: e.target.value })}
        />
      </label>
      {!hosted ? (
        <p className="text-sm text-subtle">
          PNG, JPEG or WebP, up to 2 MB. A transparent PNG looks best.
        </p>
      ) : null}
      {refused ? <p className="text-sm text-destructive">{refused}</p> : null}
    </section>
  );
}
