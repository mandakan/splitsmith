/**
 * CorpusMap -- every fixture as a tile: one row per match, a block per
 * stage, a tile per recording (shooter x camera). The tile's colour is
 * the chosen lens (what the shipped build did with it, its leave-one-
 * match-out fold, or the beep ranker's held-out verdict); hovering a band
 * of the flow diagram lights up the tiles it carries. Rules live in
 * ``lib/modelMap``.
 */
import { useMemo } from "react";

import { Label, Segmented } from "@/components/ui";
import {
  HELD_OUT_COLOR,
  HELD_OUT_TONE_COLOR,
  MEMBERSHIP_COLOR,
  STATUS_COLOR,
  STATUS_LABEL,
  STATUS_ORDER,
  angleOf,
  corpusRows,
  fixtureFlags,
  fixtureStatus,
  heldOutTone,
  matchKey,
  type Membership,
  type ModelMapFixture,
} from "@/lib/modelMap";
import { cn } from "@/lib/utils";

export type MapLens = "status" | "fold" | "heldout" | "compare" | "ranker";

function tileColor(
  f: ModelMapFixture,
  lens: MapLens,
  folds: Record<string, string>,
  membership: Record<string, Membership> | null,
): string {
  if (lens === "fold") return f.voter_c ? (folds[matchKey(f)] ?? "var(--color-muted)") : "transparent";
  if (lens === "ranker") return HELD_OUT_COLOR[f.beep_ranker?.held_out ?? "none"];
  if (lens === "heldout") {
    const tone = heldOutTone(f);
    return tone ? HELD_OUT_TONE_COLOR[tone] : "var(--color-whisper)";
  }
  if (lens === "compare") return MEMBERSHIP_COLOR[membership?.[f.stem] ?? "neither"];
  return STATUS_COLOR[fixtureStatus(f)];
}

/** A tile is hollow when the lens has nothing to say about the fixture. */
function hollow(f: ModelMapFixture, lens: MapLens, membership: Record<string, Membership> | null): boolean {
  if (lens === "status") return fixtureStatus(f) === "unused" || fixtureStatus(f) === "superseded";
  if (lens === "fold") return f.voter_c === null;
  if (lens === "heldout") return heldOutTone(f) === null;
  if (lens === "compare") return (membership?.[f.stem] ?? "neither") === "neither";
  return !f.beep_ranker?.held_out;
}

export function CorpusMap({
  fixtures,
  lens,
  onLens,
  folds,
  highlight,
  selected,
  onSelect,
  membership = null,
  names = { a: "A", b: "B" },
}: {
  fixtures: ModelMapFixture[];
  lens: MapLens;
  onLens: (lens: MapLens) => void;
  folds: Record<string, string>;
  highlight: Set<string> | null;
  selected: string | null;
  onSelect: (stem: string) => void;
  /** Which of two compared models each fixture calibrated; the Compare lens needs it. */
  membership?: Record<string, Membership> | null;
  names?: { a: string; b: string };
}) {
  const rows = useMemo(() => corpusRows(fixtures), [fixtures]);
  const lenses = [
    { value: "status" as const, label: "Build" },
    { value: "fold" as const, label: "Match fold" },
    { value: "heldout" as const, label: "Held out" },
    ...(membership ? [{ value: "compare" as const, label: "Compare" }] : []),
    { value: "ranker" as const, label: "Beep ranker" },
  ];
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Label>Corpus map</Label>
        <Segmented label="Colour tiles by" value={lens} options={lenses} onChange={onLens} />
      </div>
      <Legend lens={lens} folds={folds} names={names} />
      {rows.length === 0 ? (
        <p className="text-sm text-muted">No fixture matches the filter.</p>
      ) : (
        <div className="flex flex-col divide-y divide-rule rounded-[10px] border border-rule bg-surface">
          {rows.map((row) => (
            <div key={row.match} className="grid grid-cols-1 gap-2 px-3 py-2.5 md:grid-cols-[200px_1fr]">
              <div className="flex min-w-0 items-baseline gap-2">
                {lens === "fold" ? (
                  <i aria-hidden className="size-2 shrink-0 rounded-full" style={{ background: folds[row.match] }} />
                ) : null}
                <span className="truncate text-sm text-ink-2" title={row.match}>
                  {row.match}
                </span>
                <span className="numeral text-sm text-subtle">{row.count}</span>
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-2">
                {row.stages.map((block) => (
                  <div key={String(block.stage)} className="flex items-center gap-1">
                    <span className="w-5 text-right font-mono text-sm text-subtle">{block.stage ?? "?"}</span>
                    <div className="flex gap-[3px]">
                      {block.fixtures.map((f) => {
                        const dim = highlight !== null && !highlight.has(f.stem);
                        const color = tileColor(f, lens, folds, membership);
                        return (
                          <button
                            key={f.stem}
                            type="button"
                            onClick={() => onSelect(f.stem)}
                            title={`${f.stem}\n${STATUS_LABEL[fixtureStatus(f)]} / ${f.camera ?? "camera?"} / ${f.n_shots} shots`}
                            aria-label={f.stem}
                            className={cn(
                              "relative size-3.5 overflow-hidden rounded-[3px] border transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
                              dim && "opacity-15",
                              selected === f.stem && "ring-2 ring-ink",
                            )}
                            style={
                              hollow(f, lens, membership)
                                ? { borderColor: color === "transparent" ? "var(--color-whisper)" : color }
                                : { background: color, borderColor: color }
                            }
                          >
                            {angleOf(f) !== "primary" ? (
                              <i aria-hidden className="absolute bottom-0 right-0 size-1 rounded-tl-sm bg-bg/70" />
                            ) : null}
                            {lens === "status" ? <TileMarks f={f} /> : null}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/** A slash across a tile the next retrain drops; an amber dot on one still unreviewed. */
function TileMarks({ f }: { f: ModelMapFixture }) {
  const { leaving, unreviewed } = fixtureFlags(f);
  return (
    <>
      {leaving ? (
        <i
          aria-hidden
          className="absolute inset-0"
          style={{
            background:
              "linear-gradient(to top right, transparent calc(50% - 1px), var(--color-bg) calc(50% - 1px), var(--color-bg) calc(50% + 1px), transparent calc(50% + 1px))",
          }}
        />
      ) : null}
      {unreviewed ? (
        <i aria-hidden className="absolute left-0 top-0 size-1.5 rounded-br-sm border-b border-r border-bg bg-live" />
      ) : null}
    </>
  );
}

function Swatch({ color, hollow = false, children }: { color: string; hollow?: boolean; children: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm text-muted">
      <i
        aria-hidden
        className="size-2.5 rounded-[2px] border"
        style={hollow ? { borderColor: color } : { background: color, borderColor: color }}
      />
      {children}
    </span>
  );
}

function Legend({
  lens,
  folds,
  names,
}: {
  lens: MapLens;
  folds: Record<string, string>;
  names: { a: string; b: string };
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      {lens === "status"
        ? STATUS_ORDER.map((s) => (
            <Swatch key={s} color={STATUS_COLOR[s]} hollow={s === "unused" || s === "superseded"}>
              {STATUS_LABEL[s]}
            </Swatch>
          ))
        : null}
      {lens === "status" ? (
        <>
          <span className="inline-flex items-center gap-1.5 text-sm text-subtle">
            <i aria-hidden className="relative size-2.5 overflow-hidden rounded-[2px] bg-muted">
              <TileMarks
                f={{ voter_c: "headcam", superseded_by: "x", review_status: "reviewed" } as ModelMapFixture}
              />
            </i>
            Leaves at retrain
          </span>
          <span className="inline-flex items-center gap-1.5 text-sm text-subtle">
            <i aria-hidden className="relative size-2.5 overflow-hidden rounded-[2px] bg-muted">
              <TileMarks
                f={{ voter_c: "headcam", superseded_by: null, review_status: "needs_review" } as ModelMapFixture}
              />
            </i>
            Trained, unreviewed
          </span>
        </>
      ) : null}
      {lens === "fold" ? (
        <>
          {Object.entries(folds).map(([m, c]) => (
            <Swatch key={m} color={c}>
              {m}
            </Swatch>
          ))}
          <Swatch color="var(--color-whisper)" hollow>
            Not calibrating
          </Swatch>
        </>
      ) : null}
      {lens === "heldout" ? (
        <>
          <Swatch color={HELD_OUT_TONE_COLOR.clean}>Every shot, nothing else</Swatch>
          <Swatch color={HELD_OUT_TONE_COLOR.missed}>Missed a shot</Swatch>
          <Swatch color={HELD_OUT_TONE_COLOR.extra}>Extra detections only</Swatch>
          <Swatch color="var(--color-whisper)" hollow>
            Not scored by this build
          </Swatch>
        </>
      ) : null}
      {lens === "compare" ? (
        <>
          <Swatch color={MEMBERSHIP_COLOR.both}>Both models</Swatch>
          <Swatch color={MEMBERSHIP_COLOR.a}>{`Only ${names.a}`}</Swatch>
          <Swatch color={MEMBERSHIP_COLOR.b}>{`Only ${names.b}`}</Swatch>
          <Swatch color={MEMBERSHIP_COLOR.neither} hollow>
            Neither
          </Swatch>
        </>
      ) : null}
      {lens === "ranker" ? (
        <>
          <Swatch color={HELD_OUT_COLOR.right}>Beep picked</Swatch>
          <Swatch color={HELD_OUT_COLOR.wrong}>Wrong pick</Swatch>
          <Swatch color={HELD_OUT_COLOR.unreachable}>Beep not a candidate</Swatch>
          <Swatch color={HELD_OUT_COLOR.none} hollow>
            Not in ranker set
          </Swatch>
        </>
      ) : null}
      <span className="inline-flex items-center gap-1.5 text-sm text-subtle">
        <i aria-hidden className="relative size-2.5 rounded-[2px] bg-muted">
          <i className="absolute bottom-0 right-0 size-1 bg-bg/70" />
        </i>
        Second camera
      </span>
    </div>
  );
}
