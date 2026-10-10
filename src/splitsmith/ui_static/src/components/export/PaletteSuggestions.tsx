/**
 * PaletteSuggestions -- the top of the Look editor's Palette tab (#1273): a
 * full palette in one click, from a colour by a colour-theory scheme, from
 * what stands out on this match's footage, from the club logo, or from a
 * ready-made range palette, and a warning when the current accent blends
 * into the footage. Choosing one replaces the draft's colours and accent
 * series; nothing is saved until Save. Rules live in ``lib/palette``.
 */
import { Check } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { Segmented } from "@/components/ui/Segmented";
import { api, type Rgb, type StoredLookBody } from "@/lib/api";
import { hexToRgb, rgbToHex, sourceHints } from "@/lib/lookEditor";
import { cn } from "@/lib/utils";
import {
  READY_MADE,
  isApplied,
  SCHEMES,
  footageAccents,
  paletteFrom,
  weakAccent,
  type Suggestion,
  type Swatch,
} from "@/lib/palette";

type Source = "colour" | "footage" | "logo" | "ready";

export interface PaletteSuggestionsProps {
  draft: StoredLookBody;
  setDraft: (draft: StoredLookBody) => void;
  slug: string;
  stageNumber: number;
  /** Footage is sampled from trims on this disk, which hosted has none of. */
  hosted?: boolean;
  /** The shooter's Identity sheet on the Footage page, for "Add a logo". */
  identityHref?: string;
}

function Strip({ colors, series }: { colors: Rgb[]; series: string[] }) {
  return (
    <span className="flex items-center gap-1">
      {colors.map((c, i) => (
        <span key={i} aria-hidden className="h-5 w-5 rounded-sm border border-rule" style={{ background: rgbToHex(c) }} />
      ))}
      <span className="ml-1 flex gap-0.5">
        {series.map((hex, i) => (
          <span key={i} aria-hidden className="size-2.5 rounded-full" style={{ background: hex }} />
        ))}
      </span>
    </span>
  );
}

function Card({
  s,
  help,
  inUse,
  onUse,
}: {
  s: Suggestion;
  help?: string;
  inUse: boolean;
  onUse: (s: Suggestion) => void;
}) {
  const c = s.colors;
  return (
    <button
      type="button"
      onClick={() => onUse(s)}
      aria-label={`Use the ${s.label} palette`}
      aria-pressed={inUse}
      className={cn(
        "group flex cursor-pointer flex-col items-start gap-1.5 rounded-md border bg-surface p-2 text-left transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-led",
        inUse ? "border-led" : "border-rule hover:border-rule-strong hover:bg-surface-2",
      )}
    >
      <span className="flex w-full items-baseline justify-between gap-2">
        <span className="text-md text-ink">{s.label}</span>
        {inUse ? (
          <span className="flex items-center gap-1 text-sm text-ink">
            <Check className="size-3.5" aria-hidden /> In use
          </span>
        ) : (
          <span className="text-sm text-muted group-hover:text-ink group-focus-visible:text-ink">Apply</span>
        )}
      </span>
      {help ? <span className="text-sm text-muted">{help}</span> : null}
      <Strip colors={[c.accent, c.accent_fill, c.split, c.split_good]} series={s.series} />
    </button>
  );
}

export function PaletteSuggestions({
  draft,
  setDraft,
  slug,
  stageNumber,
  hosted = false,
  identityHref,
}: PaletteSuggestionsProps) {
  const [source, setSource] = useState<Source>("colour");
  const [seed, setSeed] = useState<string>(rgbToHex(draft.colors.accent ?? [255, 45, 45]));
  const [footage, setFootage] = useState<Swatch[]>([]);
  const [average, setAverage] = useState<Rgb | null>(null);
  const [logo, setLogo] = useState<Swatch[]>([]);

  useEffect(() => {
    let alive = true;
    api
      .paletteSources(slug, stageNumber > 0 ? [stageNumber] : [])
      .then((r) => {
        if (!alive) return;
        setFootage(r.footage);
        setAverage(r.average);
        setLogo(r.logo);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [slug, stageNumber]);

  // What the last click did, said at once: the preview takes a moment to
  // catch up, and a click with no answer reads as a click that missed.
  const [applied, setApplied] = useState<string | null>(null);
  const clear = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(clear.current), []);
  const use = (s: Suggestion) => {
    setDraft({ ...draft, colors: s.colors, accent_series: s.series });
    setApplied(s.label);
    window.clearTimeout(clear.current);
    clear.current = window.setTimeout(() => setApplied(null), 5000);
  };
  const hints = sourceHints({ footage: footage.length, logo: logo.length, hosted });
  const weak = useMemo(() => weakAccent(draft.colors, footage, average), [draft.colors, footage, average]);

  const suggestions: { s: Suggestion; help?: string }[] = useMemo(() => {
    const base = draft.colors;
    if (source === "colour") {
      const accent = hexToRgb(seed);
      return accent ? SCHEMES.map((x) => ({ s: paletteFrom(accent, x.id, base, footage) })) : [];
    }
    if (source === "footage") {
      return footageAccents(footage, average, 4).map((accent, i) => ({
        s: { ...paletteFrom(accent, "complementary", base, footage), label: i === 0 ? "Stands out most" : `Stands out ${rgbToHex(accent)}` },
      }));
    }
    if (source === "logo") {
      return logo
        .filter((sw) => Math.max(...sw.rgb) - Math.min(...sw.rgb) > 40)
        .slice(0, 3)
        .map((sw) => ({ s: { ...paletteFrom(sw.rgb, "complementary", base, footage), label: `Logo ${rgbToHex(sw.rgb)}` } }));
    }
    return READY_MADE.map((r) => ({ s: { ...paletteFrom(r.accent, r.scheme, base, footage), label: r.label }, help: r.help }));
  }, [source, seed, draft.colors, footage, average, logo]);

  return (
    <section className="flex flex-col gap-2 border-b border-rule pb-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Label>Suggestions</Label>
        <Segmented<Source>
          label="Suggest from"
          value={source}
          onChange={setSource}
          options={[
            { value: "colour", label: "A colour" },
            {
              value: "footage",
              label: "This footage",
              disabled: footage.length === 0,
            },
            { value: "logo", label: "Club logo", disabled: logo.length === 0 },
            { value: "ready", label: "Ready-made" },
          ]}
        />
      </div>
      {hints.length > 0 ? (
        <ul className="text-sm text-subtle">
          {hints.map((h) => (
            <li key={h}>{h}</li>
          ))}
          {logo.length === 0 && identityHref ? (
            <li>
              <a className="text-led-text underline-offset-4 hover:underline" href={identityHref}>
                Add a logo
              </a>
            </li>
          ) : null}
        </ul>
      ) : null}
      {applied ? (
        <p role="status" className="text-sm text-ink">
          Applied {applied}. The preview is updating; Save keeps it.
        </p>
      ) : null}
      {weak ? (
        <div role="status" className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <span>{weak.message}</span>
          <Button variant="default" size="sm" onClick={() => use(paletteFrom(weak.better, "complementary", draft.colors, footage))}>
            Use {rgbToHex(weak.better)}
          </Button>
        </div>
      ) : null}
      {source === "colour" ? (
        <label className="flex items-center gap-2 text-sm text-muted">
          <input
            type="color"
            aria-label="Start from colour"
            value={seed}
            onChange={(e) => setSeed(e.target.value)}
            className="h-7 w-9 cursor-pointer rounded border border-rule-strong bg-transparent"
          />
          Start from this colour
        </label>
      ) : null}
      {source === "footage" && footage.length > 0 ? (
        <div className="flex items-center gap-2 text-sm text-muted">
          <span>This footage:</span>
          <Strip colors={footage.map((f) => f.rgb)} series={[]} />
        </div>
      ) : null}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {suggestions.map(({ s, help }) => (
          <Card key={s.id + s.label} s={s} help={help} inUse={isApplied(s, draft)} onUse={use} />
        ))}
      </div>
    </section>
  );
}
