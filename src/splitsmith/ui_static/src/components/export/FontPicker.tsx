/**
 * FontPicker -- a Look's faces in the editor's Card styles tab (#1272): one
 * row per role (titles, figures), each face shown in itself. The faces are
 * the bundled catalog (``splitsmith.fonts``), served by
 * ``GET /api/looks/fonts/<id>`` for these samples, and on the desktop the
 * Look's own font files (``own``), which either row may use and an upload
 * adds to; the draft preview draws the choice on the real cards. Rules:
 * ``lib/lookEditor`` (``setFont``).
 */
import { useId, useState } from "react";

import { Label } from "@/components/ui/Label";
import type { FontInfo, OwnFontInfo, StoredLookBody } from "@/lib/api";
import { FONT_DEFAULTS, FONT_ROLES, setFont } from "@/lib/lookEditor";
import { previewSrc } from "@/lib/looks";
import { cn } from "@/lib/utils";

const family = (key: string) => `splitsmith-sample-${key.replace(/[^a-z0-9-]/gi, "-")}`;

interface Face {
  value: string;
  label: string;
  help: string;
  url: string;
}

export interface OwnFonts {
  fonts: OwnFontInfo[];
  /** Store a file in the Look; rejects with the server's reason. */
  upload: (file: File) => Promise<OwnFontInfo>;
}

export function FontPicker({
  draft,
  setDraft,
  fonts,
  own,
}: {
  draft: StoredLookBody;
  setDraft: (d: StoredLookBody) => void;
  fonts: FontInfo[];
  /** The Look's own fonts and the upload; absent where a Look cannot keep a file (hosted). */
  own?: OwnFonts;
}) {
  const [refused, setRefused] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputId = useId();
  if (fonts.length === 0) return null;
  const owned: Face[] = (own?.fonts ?? []).map((f) => ({ value: f.value, label: f.family, help: "Your font file", url: f.url }));
  const all = [...fonts.map((f) => ({ value: f.id, label: f.label, help: f.help, url: f.url })), ...owned];
  const faces = all
    .map((f) => `@font-face { font-family: "${family(f.value)}"; src: url("${previewSrc(f.url)}"); font-weight: 100 900; }`)
    .join("\n");

  async function add(role: string, file: File | undefined) {
    if (!own || !file) return;
    setRefused(null);
    setBusy(true);
    try {
      const added = await own.upload(file);
      setDraft(setFont(draft, role, added.value));
    } catch (err) {
      setRefused(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-4 flex flex-col gap-3 border-t border-rule pt-4">
      <style>{faces}</style>
      <Label>Fonts</Label>
      {FONT_ROLES.map(({ role, label, help }) => {
        const chosen = draft.fonts?.[role] ?? FONT_DEFAULTS[role];
        const choices: Face[] = [
          ...fonts.filter((f) => f.role === role).map((f) => ({ value: f.id, label: f.label, help: f.help, url: f.url })),
          ...owned,
        ];
        return (
          <div key={role} className="flex flex-col gap-1.5">
            <div className="flex items-baseline gap-2">
              <span className="text-md text-ink">{label}</span>
              <span className="text-sm text-muted">{help}</span>
            </div>
            <div role="radiogroup" aria-label={`${label} font`} className="flex flex-wrap gap-2">
              {choices.map((f) => (
                <button
                  key={f.value}
                  type="button"
                  role="radio"
                  aria-checked={chosen === f.value}
                  aria-label={f.label}
                  title={f.help}
                  onClick={() => setDraft(setFont(draft, role, f.value))}
                  className={cn(
                    "rounded-md border px-3 py-1.5 text-left",
                    chosen === f.value ? "border-led" : "border-rule hover:border-rule-strong",
                  )}
                >
                  <span className="block text-base text-ink" style={{ fontFamily: `"${family(f.value)}"`, fontWeight: 700 }}>
                    {role === "mono" ? "0.21 1.48 12.40" : "Stage 3 Standards"}
                  </span>
                  <span className="block text-sm text-muted">{f.label}</span>
                </button>
              ))}
              {own ? (
                <label
                  htmlFor={`${inputId}-${role}`}
                  className={cn(
                    "flex cursor-pointer items-center rounded-md border border-dashed border-rule px-3 py-1.5 text-sm text-muted hover:border-rule-strong",
                    busy && "pointer-events-none opacity-60",
                  )}
                >
                  Add a font file
                  <input
                    id={`${inputId}-${role}`}
                    type="file"
                    accept=".ttf,.otf,font/ttf,font/otf"
                    aria-label={`Add a font file for ${label}`}
                    className="sr-only"
                    disabled={busy}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      e.target.value = "";
                      void add(role, file);
                    }}
                  />
                </label>
              ) : null}
            </div>
          </div>
        );
      })}
      {own ? (
        <p className="max-w-[60ch] text-sm text-muted">
          A TTF or OTF file of up to 2 MB, kept in this Look&apos;s folder. Use a font you are licensed to use
          in videos you publish.
        </p>
      ) : null}
      {refused ? <p className="text-sm text-destructive">{refused}</p> : null}
    </section>
  );
}
