/**
 * Match export route (/export) -- compare-grid MP4 configurator (phase 0).
 *
 * The existing `Export.tsx` is scoped to one shooter (`/export/:slug`):
 * it loads `api.getProject(slug)` and its "Multi-shooter compare grid"
 * mode has sat hard-disabled with a `#328` badge ever since, because a
 * 4-shooter grid can't be configured from inside one shooter's page.
 * This page is the match-scoped surface `/export` needed -- it renders
 * every shooter on the match, lets the operator pick which one supplies
 * the audio track, which stages to render, and at what canvas size, then
 * queues the same `POST /api/match/compare-export` job the CLI's
 * `compare export --format mp4` submits and polls it to completion.
 *
 * `/export/:slug` and `/export/:slug/:stage` are untouched -- they keep
 * resolving to the single-shooter `Export` page. A later phase folds the
 * two together; this page deliberately reuses `Export.tsx`'s visual
 * primitives (lifted into `components/export/primitives.tsx`) so that
 * fold has one copy of each to work from, not two to reconcile.
 *
 * Local mode only, per the endpoint: no hosted download list, no export
 * history. Rendering runs for minutes on a full 4K grid, so the page
 * queues a job and polls it exactly like `Export.tsx`'s match-bundle
 * flow. A job can finish having rendered only some of the selected
 * stages -- the renderer isolates per-stage failures -- so a partial
 * result is shown as a success with a named list of what failed, never
 * as an outright failure.
 */

import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ExternalLink,
  Volume2,
  VolumeX,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  LedCtaButton,
  Section,
  SelectField,
  StageChip,
} from "@/components/export/primitives";
import {
  ApiError,
  api,
  type CompareGridResult,
  type Job,
  type MatchProject,
  type ShooterListEntry,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  CANVAS_CHOICES,
  buildCompareGridPayload,
  summarizeGridResult,
  type CanvasChoice,
} from "@/pages/matchExportModel";

export function MatchExport() {
  const [shooters, setShooters] = useState<ShooterListEntry[] | null>(null);
  const [matchName, setMatchName] = useState<string>("");
  const [project, setProject] = useState<MatchProject | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [audioFrom, setAudioFrom] = useState<string>("");
  const [selectedStages, setSelectedStages] = useState<Set<number>>(() => new Set());
  const [canvas, setCanvas] = useState<CanvasChoice>(CANVAS_CHOICES[0]);

  const [job, setJob] = useState<Job | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<CompareGridResult | null>(null);

  // Load every shooter on the match, then the stage list off whichever
  // shooter is alphabetically first -- stage numbers/names are shared
  // across a match's shooters, so any one of them names the full set
  // (mirrors Compare.tsx's load).
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await api.listMatchShooters();
        if (!alive) return;
        setShooters(list.shooters);
        setMatchName(list.match_name);
        const first = list.shooters[0]?.slug;
        if (!first) return;
        setAudioFrom(first);
        const p = await api.getProject(first);
        if (alive) setProject(p);
      } catch (e) {
        if (alive) setLoadError(e instanceof ApiError ? e.detail : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const stages = project?.stages ?? [];
  // Server-side validation catches the real eligibility question (does
  // *any* shooter have a trim for the stage, does the chosen audio
  // source specifically). Skipped stages are the one thing this page
  // can rule out up front without a round-trip.
  const eligibleNumbers = useMemo(
    () => stages.filter((s) => !s.skipped).map((s) => s.stage_number),
    [stages],
  );
  const eligibleSet = useMemo(() => new Set(eligibleNumbers), [eligibleNumbers]);

  // Pre-select every eligible stage on first load.
  useEffect(() => {
    if (eligibleNumbers.length > 0 && selectedStages.size === 0) {
      setSelectedStages(new Set(eligibleNumbers));
    }
  }, [eligibleNumbers, selectedStages.size]);

  // Drop any stage that became ineligible (e.g. the stage list changed
  // under us) rather than silently rendering a black-tile stage.
  useEffect(() => {
    setSelectedStages((prev) => {
      const next = new Set<number>();
      for (const n of prev) if (eligibleSet.has(n)) next.add(n);
      return next.size === prev.size ? prev : next;
    });
  }, [eligibleSet]);

  const toggleStage = useCallback(
    (n: number) => {
      if (!eligibleSet.has(n)) return;
      setSelectedStages((prev) => {
        const next = new Set(prev);
        if (next.has(n)) next.delete(n);
        else next.add(n);
        return next;
      });
    },
    [eligibleSet],
  );

  const orderedSelection = useMemo(
    () => stages.map((s) => s.stage_number).filter((n) => selectedStages.has(n)),
    [stages, selectedStages],
  );

  const busy =
    submitting || job?.status === "pending" || job?.status === "running";
  const canSubmit = !busy && audioFrom !== "" && orderedSelection.length > 0;

  async function submit() {
    if (!canSubmit) return;
    setSubmitError(null);
    setResult(null);
    setSubmitting(true);
    try {
      const payload = buildCompareGridPayload({
        stageNumbers: orderedSelection,
        audioFrom,
        canvas,
        outputName: "compare-grid",
      });
      const submitted = await api.exportCompareGrid(payload);
      setJob(submitted);
      const final = await api.pollJob(submitted.id, setJob);
      if (final.status === "succeeded" && final.result) {
        setResult(final.result as unknown as CompareGridResult);
      } else if (final.status === "failed") {
        setSubmitError(final.error ?? "Render failed.");
      }
    } catch (e) {
      setSubmitError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function reveal(path: string) {
    try {
      await api.revealFile(path);
    } catch {
      // Reveal is non-critical.
    }
  }

  if (!shooters && !loadError) {
    return (
      <div className="px-7 py-6 text-sm text-muted">Loading match...</div>
    );
  }

  const summary = result ? summarizeGridResult(result) : null;

  return (
    <div className="px-7 py-5">
      <div className="mb-5">
        <h1 className="mb-2 font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
          Match export
        </h1>
        <p className="max-w-[40rem] text-sm text-muted">
          Render every shooter on {matchName || "this match"} into one grid
          MP4 for the selected stages, beep-aligned, with the chosen
          shooter's audio.
        </p>
      </div>

      {loadError && (
        <div className="mb-4 rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
          {loadError}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_340px]">
        {/* Left column: sections */}
        <div className="flex min-w-0 flex-col gap-5">
          <Section
            number={1}
            title="Audio source"
            help="Which shooter's microphone feeds the grid's sound track."
          >
            {(shooters ?? []).length === 0 ? (
              <p className="text-sm text-muted">No shooters on this match yet.</p>
            ) : (
              <div className="flex flex-col gap-2">
                {(shooters ?? []).map((s) => {
                  const active = audioFrom === s.slug;
                  return (
                    <label
                      key={s.slug}
                      className={cn(
                        "flex cursor-pointer items-center gap-3 rounded-md border px-3 py-2 transition-colors",
                        active
                          ? "border-led bg-led/10 shadow-[0_0_0_1px_var(--color-led-deep)]"
                          : "border-rule-strong bg-surface-2 hover:bg-surface-3",
                      )}
                    >
                      <input
                        type="radio"
                        name="audio-from"
                        value={s.slug}
                        checked={active}
                        onChange={() => setAudioFrom(s.slug)}
                        className="accent-led"
                      />
                      <span className="font-display text-sm font-bold uppercase tracking-[0.04em] text-ink">
                        {s.name}
                      </span>
                      {active ? (
                        <Volume2 className="ml-auto size-3.5 text-led" />
                      ) : (
                        <VolumeX className="ml-auto size-3.5 text-subtle" />
                      )}
                    </label>
                  );
                })}
              </div>
            )}
          </Section>

          <Section
            number={2}
            title="Stages"
            help={`${eligibleNumbers.length} of ${stages.length} stages selectable.`}
          >
            {stages.length === 0 ? (
              <p className="text-sm text-muted">No stages on this match yet.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {stages.map((s) => {
                  const eligible = eligibleSet.has(s.stage_number);
                  return (
                    <StageChip
                      key={s.stage_number}
                      stageNumber={s.stage_number}
                      stageName={s.stage_name}
                      selected={selectedStages.has(s.stage_number)}
                      eligible={eligible}
                      title={
                        eligible
                          ? `Stage ${s.stage_number} -- ${s.stage_name}`
                          : "Stage skipped."
                      }
                      onToggle={() => toggleStage(s.stage_number)}
                    />
                  );
                })}
              </div>
            )}
          </Section>

          <Section
            number={3}
            title="Canvas size"
            help="4K UHD is the default; 1080p renders faster."
          >
            <div className="max-w-xs">
              <SelectField
                label="Canvas"
                value={canvas.id}
                onChange={(v) => {
                  const next = CANVAS_CHOICES.find((c) => c.id === v);
                  if (next) setCanvas(next);
                }}
                options={CANVAS_CHOICES.map((c) => ({ value: c.id, label: c.label }))}
              />
            </div>
          </Section>
        </div>

        {/* Right column: render rail */}
        <aside className="lg:sticky lg:top-[6.5rem] lg:self-start">
          <div className="overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
            <div className="border-b border-rule px-5 py-3.5">
              <div className="font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
                Render
              </div>
              <div className="mt-1 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                Pre-flight check
              </div>
            </div>
            <div className="flex flex-col gap-2.5 px-5 py-4 font-mono text-[0.75rem] uppercase tracking-[0.04em] text-muted tabular-nums">
              <div className="flex items-baseline justify-between gap-3">
                <span>Stages</span>
                <span className="font-bold text-ink">
                  {orderedSelection.length} / {eligibleNumbers.length}
                </span>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <span>Audio</span>
                <span className="font-bold text-ink">
                  {shooters?.find((s) => s.slug === audioFrom)?.name ?? "--"}
                </span>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <span>Canvas</span>
                <span className="font-bold text-ink">{canvas.label}</span>
              </div>
            </div>
            <div className="border-t border-rule px-5 py-4">
              <LedCtaButton
                busy={busy}
                icon={<Check className="size-3.5" strokeWidth={3} />}
                label="Render grid"
                busyLabel="Rendering..."
                onClick={() => void submit()}
                disabled={!canSubmit}
              />
              {busy && job?.message && (
                <div className="mt-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                  {job.message}
                  {job.progress != null ? ` (${Math.round(job.progress * 100)}%)` : ""}
                </div>
              )}
              {submitError && (
                <div className="mt-3 rounded-lg border border-led/40 bg-led/10 px-3 py-2 text-[0.8125rem] text-led">
                  {submitError}
                </div>
              )}
              {result && summary && (
                <div
                  className={cn(
                    "mt-3 rounded-lg border px-3 py-2.5 text-[0.8125rem] text-ink-2",
                    summary.partial
                      ? "border-live/40 bg-live/10"
                      : "border-done/40 bg-done/10",
                  )}
                >
                  <div
                    className={cn(
                      "mb-1.5 inline-flex items-center gap-1.5 font-display font-bold uppercase tracking-[0.08em]",
                      summary.partial ? "text-live" : "text-done",
                    )}
                  >
                    {summary.partial ? (
                      <AlertTriangle className="size-3.5" strokeWidth={2.5} />
                    ) : (
                      <CheckCircle2 className="size-3.5" strokeWidth={2.5} />
                    )}
                    {summary.headline}
                  </div>
                  {summary.partial && (
                    <ul className="ml-4 list-disc text-muted">
                      {summary.failedStages.map((name) => (
                        <li key={name}>{name} did not render</li>
                      ))}
                    </ul>
                  )}
                  <button
                    type="button"
                    onClick={() => void reveal(result.output_path)}
                    className="mt-2 inline-flex items-center gap-1.5 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-led hover:text-led-soft"
                  >
                    Reveal file <ExternalLink className="size-3" />
                  </button>
                </div>
              )}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
