/**
 * PaletteSuggestions -- the top of the Look editor's Palette tab (#1273): a
 * full palette in one click, from a colour by a colour-theory scheme, from
 * what stands out on this match's footage, from the club logo, or from a
 * ready-made range palette, and a warning when the current accent blends
 * into the footage. Choosing one replaces the draft's colours and accent
 * series; nothing is saved until Save. Rules live in ``lib/palette``.
 */
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { Segmented } from "@/components/ui/Segmented";
import { api, type Rgb, type StoredLookBody } from "@/lib/api";
import { hexToRgb, rgbToHex } from "@/lib/lookEditor";
import {
  READY_MADE,
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

function Card({ s, help, onUse }: { s: Suggestion; help?: string; onUse: (s: Suggestion) => void }) {
  const c = s.colors;
  return (
    <button
      type="button"
      onClick={() => onUse(s)}
      aria-label={`Use the ${s.label} palette`}
      className="flex flex-col items-start gap-1.5 rounded-md border border-rule p-2 text-left hover:border-rule-strong"
    >
      <span className="text-md text-ink">{s.label}</span>
      {help ? <span className="text-sm text-muted">{help}</span> : null}
      <Strip colors={[c.accent, c.accent_fill, c.split, c.split_good, c.split_slow]} series={s.series} />
    </button>
  );
}

export function PaletteSuggestions({ draft, setDraft, slug, stageNumber }: PaletteSuggestionsProps) {
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

  const use = (s: Suggestion) => setDraft({ ...draft, colors: s.colors, accent_series: s.series });
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
              title: "No trimmed footage of this stage on this machine",
            },
            { value: "logo", label: "Club logo", disabled: logo.length === 0, title: "This shooter has no logo" },
            { value: "ready", label: "Ready-made" },
          ]}
        />
      </div>
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
          <Card key={s.id + s.label} s={s} help={help} onUse={use} />
        ))}
      </div>
    </section>
  );
}
