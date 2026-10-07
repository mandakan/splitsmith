/**
 * FontPicker -- a Look's faces in the editor's Card styles tab (#1272): one
 * row per role (titles, figures), each face shown in itself. The faces are
 * the bundled catalog (``splitsmith.fonts``), served by
 * ``GET /api/looks/fonts/<id>`` for these samples; the draft preview draws
 * the choice on the real cards. Rules: ``lib/lookEditor`` (``setFont``).
 */
import { Label } from "@/components/ui/Label";
import type { FontInfo, StoredLookBody } from "@/lib/api";
import { FONT_DEFAULTS, FONT_ROLES, setFont } from "@/lib/lookEditor";
import { previewSrc } from "@/lib/looks";
import { cn } from "@/lib/utils";

const family = (id: string) => `splitsmith-sample-${id}`;

export function FontPicker({
  draft,
  setDraft,
  fonts,
}: {
  draft: StoredLookBody;
  setDraft: (d: StoredLookBody) => void;
  fonts: FontInfo[];
}) {
  if (fonts.length === 0) return null;
  const faces = fonts
    .map((f) => `@font-face { font-family: "${family(f.id)}"; src: url("${previewSrc(f.url)}"); font-weight: 100 900; }`)
    .join("\n");
  return (
    <section className="mt-4 flex flex-col gap-3 border-t border-rule pt-4">
      <style>{faces}</style>
      <Label>Fonts</Label>
      {FONT_ROLES.map(({ role, label, help }) => {
        const chosen = draft.fonts?.[role] ?? FONT_DEFAULTS[role];
        return (
          <div key={role} className="flex flex-col gap-1.5">
            <div className="flex items-baseline gap-2">
              <span className="text-md text-ink">{label}</span>
              <span className="text-sm text-muted">{help}</span>
            </div>
            <div role="radiogroup" aria-label={`${label} font`} className="flex flex-wrap gap-2">
              {fonts
                .filter((f) => f.role === role)
                .map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    role="radio"
                    aria-checked={chosen === f.id}
                    aria-label={f.label}
                    title={f.help}
                    onClick={() => setDraft(setFont(draft, role, f.id))}
                    className={cn(
                      "rounded-md border px-3 py-1.5 text-left",
                      chosen === f.id ? "border-led" : "border-rule hover:border-rule-strong",
                    )}
                  >
                    <span className="block text-base text-ink" style={{ fontFamily: `"${family(f.id)}"`, fontWeight: 700 }}>
                      {role === "mono" ? "0.21 1.48 12.40" : "Stage 3 Standards"}
                    </span>
                    <span className="block text-sm text-muted">{f.label}</span>
                  </button>
                ))}
            </div>
          </div>
        );
      })}
    </section>
  );
}
