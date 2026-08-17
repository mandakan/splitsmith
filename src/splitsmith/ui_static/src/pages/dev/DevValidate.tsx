/**
 * Developer / Validate (#331).
 *
 * Run-config bar at top, then headline metrics, per-shooter holdout
 * panel as the centerpiece (per the per-shooter-holdout memory:
 * the corpus is small enough that 5-fold CV splits per-row, not
 * per-shooter, and misses overfit a leave-one-shooter-out catches),
 * per-venue breakdown, confusion matrix + voter agreement.
 *
 * Wires to /api/lab/eval + /api/lab/last-run -- treats the lab eval
 * runner as the validation engine. When --lab isn't enabled, the run
 * button explains how to enable it and the panels render with the
 * last-built calibration's recall as a baseline only.
 */

import { Play, Save, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";

import { SweepsCard } from "@/components/SweepsCard";
import { TuningPanel } from "@/components/lab/TuningPanel";
import { useLabRun } from "@/components/lab/useLabRun";
import { api, type LabEvalRun, type LabEvalFixture } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { DeveloperShellOutletContext } from "@/components/developer/DeveloperShell";

type SplitStrategy = "5fold" | "per-shooter" | "per-venue";

const SPLITS: { key: SplitStrategy; label: string }[] = [
  { key: "5fold", label: "Training CV" },
  { key: "per-shooter", label: "Per shooter" },
  { key: "per-venue", label: "Per venue" },
];

export function DevValidate() {
  const { model, refresh } = useOutletContext<DeveloperShellOutletContext>();
  const [split, setSplit] = useState<SplitStrategy>("per-shooter");
  const {
    run: lastRun,
    config,
    setConfig,
    resetConfig,
    runEval,
    evalLoading: running,
    rescoreLoading,
    error: hookError,
  } = useLabRun({ autoRescore: true });
  const [error, setError] = useState<string | null>(null);
  const [labMissing, setLabMissing] = useState(false);
  const useApriori = config.apriori_boost > 0;

  // The hook's own mount hydration covers the happy "adopt the cached
  // run" path; this probe exists only to tell "no run yet" (404, fine)
  // apart from "the /api/lab/* surface itself is missing" (any other
  // failure -- e.g. --lab not enabled), which the hook's hydrate effect
  // deliberately treats identically (it just swallows every error).
  useEffect(() => {
    let alive = true;
    api
      .getLastLabRun()
      .catch((e: unknown) => {
        if (e && typeof e === "object" && "status" in e && (e as { status: number }).status === 404) {
          return;
        }
        if (alive) setLabMissing(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!hookError) return;
    if (hookError.includes("404") || hookError.includes("lab")) {
      setLabMissing(true);
      setError(
        "Validation runs require --lab to be enabled. Launch the server with `splitsmith ui --lab` to enable evaluations.",
      );
    } else {
      setError(hookError);
    }
  }, [hookError]);

  async function runValidation() {
    setError(null);
    await runEval();
    refresh();
  }

  return (
    <div className="min-w-0 px-7 py-7">
      <header className="mb-6 flex items-end gap-7">
        <div className="flex-1">
          <div className="mb-2 flex items-center gap-2.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.18em] text-beep">
            <span aria-hidden className="h-px w-6 bg-beep" />
            Step 03 / Validate
          </div>
          <h1 className="font-display text-[2rem] font-bold uppercase leading-none tracking-tight text-ink">
            Per-shooter holdout
          </h1>
          <p className="mt-2 max-w-xl text-[0.875rem] text-muted">
            Validate the shipped ensemble against the corpus. Per-shooter is the right split
            here -- the corpus is small enough that 5-fold CV mixes a shooter's audited
            stages into both train and test and misses overfit you'd see in the field.
          </p>
        </div>
      </header>

      {/* Run config bar */}
      <section
        className="mb-6 overflow-hidden rounded-md border border-rule bg-surface"
        style={{
          boxShadow: "inset 0 1px 0 rgba(6,182,212,0.15)",
        }}
      >
        <div className="grid grid-cols-[1.4fr_1fr_1fr_0.9fr] divide-x divide-rule">
          <ConfigCell label="Split strategy">
            <div className="flex gap-1">
              {SPLITS.map((s) => (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => setSplit(s.key)}
                  className={cn(
                    "rounded-md border px-2.5 py-1.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.06em] transition-colors",
                    split === s.key
                      ? "border-[rgba(6,182,212,0.4)] bg-[color:var(--color-beep-tint)] text-beep"
                      : "border-rule bg-surface-2 text-muted hover:text-ink",
                  )}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </ConfigCell>
          {/* Read-only: Run always scores the full corpus; subset
              evals live on the Corpus page's filter bar (#941). The old
              cell drew a chevron with no menu behind it. */}
          <ConfigCell label="Corpus">
            <div className="flex items-center gap-2 rounded-md border border-rule bg-surface-2 px-3 py-2 font-mono text-[0.75rem] tabular-nums text-ink">
              <span className="font-bold">{model?.fixture_count ?? "--"}</span>
              <span className="text-muted">fixtures</span>
              <span className="flex-1" />
              <span className="text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
                subset via corpus
              </span>
            </div>
          </ConfigCell>
          {/* Read-only: the Tuning panel below owns the consensus knob
              (with the rest of the ensemble parameters). Two controls
              writing config.consensus disagreed on the voter count and
              could show different ranges for the same value (#899). */}
          <ConfigCell label="Consensus">
            <div
              data-testid="consensus-readout"
              className="flex items-center gap-2 rounded-md border border-rule bg-surface-2 px-3 py-2 font-mono text-[0.75rem] tabular-nums text-ink"
            >
              <span className="font-bold text-beep">{config.consensus}</span>
              <span className="text-muted">of 3</span>
              <span className="flex-1" />
              <span className="text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
                tune below
              </span>
            </div>
          </ConfigCell>
          <ConfigCell label="Apriori boost">
            <button
              type="button"
              onClick={() => setConfig({ apriori_boost: useApriori ? 0 : 1.0 })}
              className={cn(
                "relative inline-flex h-6 w-10 items-center rounded-full border transition-colors",
                useApriori
                  ? "border-[rgba(6,182,212,0.4)] bg-[color:var(--color-beep-tint)]"
                  : "border-rule bg-surface-3",
              )}
              role="switch"
              aria-checked={useApriori}
            >
              <span
                className={cn(
                  "size-4 rounded-full transition-transform",
                  useApriori ? "translate-x-5 bg-beep" : "translate-x-1 bg-muted",
                )}
              />
            </button>
            <button
              type="button"
              onClick={runValidation}
              disabled={running}
              className="ml-3 inline-flex h-9 items-center gap-2 rounded-md bg-beep px-3 font-mono text-[0.75rem] font-bold uppercase tracking-[0.08em] text-bg shadow-[0_0_12px_var(--color-beep-glow)] transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              <Play className="size-3.5" />
              {running ? "Running..." : "Run"}
            </button>
          </ConfigCell>
        </div>
        <div className="flex items-center justify-between border-t border-rule bg-bg-glow px-4 py-2 font-mono text-[0.6875rem] tabular-nums text-muted">
          <span>
            Last run{" "}
            <b className="text-ink">
              {lastRun ? formatLocalTime(lastRun.built_at) : labMissing ? "n/a" : "--"}
            </b>{" "}
            &middot; consensus {lastRun?.config.consensus ?? "--"} &middot; apriori{" "}
            {lastRun?.config.apriori_boost ?? "--"}
          </span>
          {(running || rescoreLoading) && (
            <span className="flex items-center gap-2">
              <span className="size-1.5 rounded-full bg-live shadow-[0_0_6px_var(--color-live-glow)]" />
              {running ? "running eval..." : "rescoring..."}
            </span>
          )}
        </div>
      </section>

      {error && (
        <div className="mb-6 rounded-md border border-[rgba(255,45,45,0.4)] bg-[color:var(--color-led-tint)] px-4 py-3 text-[0.8125rem] text-led">
          {error}
        </div>
      )}

      {/* Metrics grid */}
      <MetricsGrid run={lastRun} target={model?.recall ?? 0.95} />

      {/* Tuning + sweeps, side by side */}
      <div className="mb-6 grid grid-cols-1 gap-5 lg:grid-cols-2">
        <TuningPanel
          config={config}
          onChange={setConfig}
          onReset={resetConfig}
          run={lastRun}
          rescoreLoading={rescoreLoading}
        />
        <SweepsCard />
      </div>

      {/* The split chips select which breakdown renders -- they used
          to be decorative (both cards always showed, "5-fold CV" showed
          nothing at all). "Training CV" is the build's own per-class
          5-fold numbers from the calibration artifact. */}
      {split === "5fold" ? (
        <TrainingCvCard model={model} />
      ) : split === "per-shooter" ? (
        <ShooterHoldoutCard run={lastRun} split={split} />
      ) : (
        <VenueBreakdown run={lastRun} />
      )}

      <div className="grid grid-cols-1 gap-5">
        <ConfusionPanel run={lastRun} />
      </div>

      {/* Footer actions */}
      <footer className="sticky bottom-0 mt-8 flex items-center gap-3 border-t border-rule bg-surface px-7 py-3 -mx-7 -mb-7">
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-md border border-[rgba(255,45,45,0.4)] px-3 py-2 font-mono text-[0.75rem] font-bold uppercase tracking-[0.08em] text-led hover:bg-[color:var(--color-led-tint)]"
          disabled
        >
          <Undo2 className="size-3.5" /> Roll back
        </button>
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-md border border-rule px-3 py-2 font-mono text-[0.75rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:bg-surface-2"
          disabled
        >
          <Save className="size-3.5" /> Save report
        </button>
        <div className="flex-1 text-right font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
          Signed off by you &middot; promotes go through Retrain
        </div>
      </footer>
    </div>
  );
}

function ConfigCell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="px-4 py-3">
      <div className="mb-1.5 flex items-center gap-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-muted">
        <span className="size-1 rounded-full bg-beep" />
        {label}
      </div>
      {children}
    </div>
  );
}

function MetricsGrid({ run, target }: { run: LabEvalRun | null; target: number }) {
  const r = run?.summary;
  const cells: {
    label: string;
    value: string;
    delta?: string;
    state: "good" | "beep" | "warn" | "bad";
    target?: string;
  }[] = [
    {
      label: "Recall",
      value: r ? r.recall.toFixed(3) : "--",
      target: `target ${target.toFixed(2)}`,
      state: r ? (r.recall >= target ? "good" : "warn") : "beep",
    },
    {
      label: "Precision",
      value: r ? r.precision.toFixed(3) : "--",
      state: r ? (r.precision >= 0.9 ? "good" : "warn") : "beep",
    },
    {
      label: "F1",
      value: r ? r.f1.toFixed(3) : "--",
      state: r ? (r.f1 >= 0.92 ? "good" : "warn") : "beep",
    },
    {
      label: "False positives",
      value: r ? r.false_positives.toString() : "--",
      state: r ? (r.false_positives === 0 ? "good" : r.false_positives < 5 ? "warn" : "bad") : "beep",
    },
  ];

  return (
    <div className="mb-6 grid grid-cols-4 gap-4">
      {cells.map((c) => (
        <div
          key={c.label}
          className="relative overflow-hidden rounded-md border border-rule bg-surface px-4 py-3"
        >
          <span
            aria-hidden
            className={cn(
              "absolute left-0 top-0 h-full w-0.5",
              c.state === "good" && "bg-done",
              c.state === "beep" && "bg-beep",
              c.state === "warn" && "bg-live",
              c.state === "bad" && "bg-led",
            )}
          />
          <div className="flex items-center justify-between font-mono text-[0.625rem] font-bold uppercase tracking-[0.12em] text-muted">
            <span>{c.label}</span>
            {c.target && <span className="text-subtle">{c.target}</span>}
          </div>
          <div
            className={cn(
              "mt-1 font-display text-[2.25rem] font-bold tabular-nums",
              c.state === "good" && "text-done",
              c.state === "beep" && "text-beep",
              c.state === "warn" && "text-live",
              c.state === "bad" && "text-led",
            )}
            style={{
              textShadow:
                c.state === "good"
                  ? "0 0 12px rgba(74,222,128,0.3)"
                  : c.state === "warn"
                    ? "0 0 12px rgba(251,191,36,0.3)"
                    : c.state === "bad"
                      ? "0 0 12px rgba(255,45,45,0.3)"
                      : "0 0 12px rgba(6,182,212,0.3)",
            }}
          >
            {c.value}
          </div>
          {c.delta && (
            <div className="mt-2 border-t border-dashed border-rule pt-2 font-mono text-[0.6875rem] tabular-nums text-muted">
              {c.delta}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

interface ShooterRow {
  key: string;
  initials: string;
  name: string;
  recall: number;
  fixtures: number;
  flagged: boolean;
}

function ShooterHoldoutCard({ run, split }: { run: LabEvalRun | null; split: SplitStrategy }) {
  const rows = run ? groupByShooter(run.universe.fixtures) : [];
  const meanRecall =
    rows.length > 0 ? rows.reduce((acc, r) => acc + r.recall, 0) / rows.length : 0;
  const spread =
    rows.length > 0
      ? Math.max(...rows.map((r) => r.recall)) - Math.min(...rows.map((r) => r.recall))
      : 0;
  const flagged = rows.filter((r) => r.flagged).length;

  return (
    <section
      className="mb-6 overflow-hidden rounded-md border bg-surface"
      style={{
        borderColor: flagged > 0 ? "rgba(251,191,36,0.4)" : "var(--color-rule)",
        background:
          flagged > 0
            ? "linear-gradient(180deg, rgba(251,191,36,0.05), transparent)"
            : "var(--color-surface)",
      }}
    >
      <header className="flex items-center justify-between border-b border-rule px-5 py-4">
        <div>
          <div className="mb-1 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.18em] text-live">
            Centerpiece
          </div>
          <h2 className="font-display text-[1.25rem] font-bold uppercase tracking-tight text-ink">
            Per-shooter holdout
          </h2>
        </div>
        <span
          className={cn(
            "inline-flex items-center gap-2 rounded-full px-3 py-1.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.06em]",
            flagged > 0
              ? "bg-[color:var(--color-live-tint)] text-live"
              : "bg-[color:var(--color-done-tint)] text-done",
          )}
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              flagged > 0
                ? "bg-live shadow-[0_0_6px_var(--color-live-glow)]"
                : "bg-done shadow-[0_0_6px_var(--color-done-glow)]",
            )}
          />
          {flagged > 0 ? `${flagged} below target` : "All above target"}
        </span>
      </header>
      <div className="grid grid-cols-3 divide-x divide-dashed divide-rule border-b border-rule">
        <SummaryStat label="Mean recall" value={meanRecall ? meanRecall.toFixed(3) : "--"} />
        <SummaryStat label="Spread" value={spread ? `${(spread * 100).toFixed(1)}pp` : "--"} />
        <SummaryStat label="Target" value="0.85" />
      </div>
      <ul>
        {rows.length === 0 && (
          <li className="px-5 py-10 text-center text-[0.875rem] text-muted">
            Run a validation to populate the per-shooter table. {split === "per-shooter" ? "" : "Switch to per-shooter for the strongest signal."}
          </li>
        )}
        {rows.map((row) => (
          <li
            key={row.key}
            className={cn(
              "grid grid-cols-[38px_1fr_90px_1fr_60px_90px] items-center gap-4 border-b border-rule px-5 py-3",
              row.flagged && "bg-[color:var(--color-live-tint)]",
            )}
          >
            <span
              className="inline-flex size-9 items-center justify-center rounded-md font-mono text-[0.75rem] font-bold text-ink"
              style={{
                background: shooterGradient(row.key),
                boxShadow: "0 0 8px rgba(255,255,255,0.05)",
              }}
            >
              {row.initials}
            </span>
            <div>
              <div className="font-mono text-[0.8125rem] font-bold text-ink">{row.name}</div>
              <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                {row.fixtures} fixtures
              </div>
            </div>
            <div
              className={cn(
                "text-right font-display text-[1.5rem] font-bold tabular-nums",
                row.flagged ? "text-live" : "text-ink",
              )}
            >
              {row.recall.toFixed(3)}
            </div>
            <RecallMeter recall={row.recall} target={0.85} />
            <span className="text-right font-mono text-[0.6875rem] tabular-nums text-muted">
              {row.fixtures}
            </span>
            <span
              className={cn(
                "text-right font-mono text-[0.6875rem] font-bold uppercase tracking-[0.06em]",
                row.flagged ? "text-live" : "text-done",
              )}
            >
              {row.flagged ? "below" : "ok"}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RecallMeter({ recall, target }: { recall: number; target: number }) {
  const pct = Math.max(0, Math.min(1, recall)) * 100;
  const fill = recall >= target ? "bg-done" : recall >= target - 0.1 ? "bg-live" : "bg-led";
  const targetPct = target * 100;
  return (
    <div className="relative h-2 w-full overflow-visible rounded-full bg-surface-3">
      <div className={cn("h-full rounded-full", fill)} style={{ width: `${pct}%` }} />
      <div
        className="absolute -top-1 h-4 w-px bg-ink-2"
        style={{ left: `${targetPct}%` }}
        aria-label={`Target ${target}`}
      />
    </div>
  );
}

function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-5 py-3">
      <div className="font-mono text-[0.625rem] font-bold uppercase tracking-[0.12em] text-muted">
        {label}
      </div>
      <div className="mt-0.5 font-display text-[1.5rem] font-bold tabular-nums text-ink">
        {value}
      </div>
    </div>
  );
}

/** The GBDT's own 5-fold cross-validation, per camera class, as
 *  recorded in the active calibration artifact at build time. These are
 *  training-corpus numbers (optimistic vs a true holdout) but they are
 *  available the moment a build finishes, with no eval run. */
function TrainingCvCard({ model }: { model: DeveloperShellOutletContext["model"] }) {
  const metrics = model?.metrics_by_class ?? null;
  return (
    <section className="overflow-hidden rounded-md border border-rule bg-surface">
      <header className="border-b border-rule px-5 py-4">
        <h2 className="font-display text-[1rem] font-bold uppercase tracking-tight text-ink">
          Training CV ({model?.active_version ?? "--"})
        </h2>
        <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          Voter C 5-fold cross-validation at the shipped threshold, per camera class --
          recorded by the build itself
        </div>
      </header>
      {metrics ? (
        <div className="divide-y divide-rule">
          <div className="grid grid-cols-[1fr_repeat(6,90px)] gap-3 bg-surface-2 px-5 py-2 font-mono text-[0.625rem] font-bold uppercase tracking-[0.12em] text-subtle">
            <span>Camera class</span>
            <span className="text-right">Precision</span>
            <span className="text-right">Recall</span>
            <span className="text-right">F1</span>
            <span className="text-right">TP</span>
            <span className="text-right">FP</span>
            <span className="text-right">FN</span>
          </div>
          {Object.entries(metrics)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([cls, m]) => (
              <div
                key={cls}
                className="grid grid-cols-[1fr_repeat(6,90px)] items-center gap-3 px-5 py-2.5 font-mono text-[0.8125rem] tabular-nums"
              >
                <span className="font-bold uppercase tracking-[0.06em] text-ink">{cls}</span>
                <span className="text-right text-ink">
                  {m.voter_c_precision_cv?.toFixed(3) ?? "--"}
                </span>
                <span className="text-right text-ink">
                  {m.voter_c_recall_cv?.toFixed(3) ?? "--"}
                </span>
                <span className="text-right font-bold text-done">
                  {m.voter_c_f1_cv?.toFixed(3) ?? "--"}
                </span>
                <span className="text-right text-muted">{m.voter_c_tp ?? "--"}</span>
                <span className="text-right text-muted">{m.voter_c_fp ?? "--"}</span>
                <span className="text-right text-muted">{m.voter_c_fn ?? "--"}</span>
              </div>
            ))}
        </div>
      ) : (
        <div className="px-5 py-8 text-center font-mono text-[0.75rem] text-muted">
          The active calibration predates per-class CV metrics -- rebuild to record them.
        </div>
      )}
    </section>
  );
}

function VenueBreakdown({ run }: { run: LabEvalRun | null }) {
  const rows = run ? groupByVenue(run.universe.fixtures) : [];
  return (
    <section className="overflow-hidden rounded-md border border-rule bg-surface">
      <header className="border-b border-rule px-5 py-4">
        <h2 className="font-display text-[1rem] font-bold uppercase tracking-tight text-ink">
          Per-venue breakdown
        </h2>
        <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          Recall &middot; precision &middot; F1 by shooting venue
        </div>
      </header>
      <ul>
        {rows.length === 0 && (
          <li className="px-5 py-10 text-center text-[0.875rem] text-muted">
            Run validation to populate.
          </li>
        )}
        {rows.map((row) => (
          <li
            key={row.key}
            className="grid grid-cols-[1.5fr_60px_1fr_80px_80px_50px] items-center gap-3 border-b border-rule px-5 py-2.5"
          >
            <div>
              <div className="font-mono text-[0.8125rem] font-bold text-ink">{row.key}</div>
              <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                {row.fixtures} fixtures
              </div>
            </div>
            <span className="font-mono text-[0.75rem] tabular-nums text-ink-2">
              {row.fixtures}
            </span>
            <RecallMeter recall={row.recall} target={0.85} />
            <span className="text-right font-mono text-[0.75rem] tabular-nums text-ink-2">
              {row.precision.toFixed(3)}
            </span>
            <span className="text-right font-mono text-[0.75rem] tabular-nums text-ink-2">
              {row.f1.toFixed(3)}
            </span>
            <span
              className={cn(
                "text-right font-mono text-[0.6875rem] font-bold uppercase",
                row.recall >= 0.85 ? "text-done" : "text-live",
              )}
            >
              {row.recall >= 0.85 ? "ok" : "low"}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ConfusionPanel({ run }: { run: LabEvalRun | null }) {
  const r = run?.summary;
  return (
    <section className="overflow-hidden rounded-md border border-rule bg-surface">
      <header className="border-b border-rule px-5 py-4">
        <h2 className="font-display text-[1rem] font-bold uppercase tracking-tight text-ink">
          Confusion matrix
        </h2>
      </header>
      <div className="grid grid-cols-2 gap-3 bg-bg-glow p-5">
        <ConfusionCell
          label="True positives"
          value={r?.true_positives}
          state="good"
        />
        <ConfusionCell
          label="False negatives"
          value={r?.false_negatives}
          state="warn"
        />
        <ConfusionCell
          label="False positives"
          value={r?.false_positives}
          state="bad"
        />
        <ConfusionCell label="True negatives" value={null} state="muted" />
      </div>
    </section>
  );
}

function ConfusionCell({
  label,
  value,
  state,
}: {
  label: string;
  value: number | null | undefined;
  state: "good" | "warn" | "bad" | "muted";
}) {
  return (
    <div
      className={cn(
        "rounded-md border px-4 py-3",
        state === "good" && "border-[rgba(74,222,128,0.4)] bg-[color:var(--color-done-tint)]",
        state === "warn" && "border-[rgba(251,191,36,0.4)] bg-[color:var(--color-live-tint)]",
        state === "bad" && "border-[rgba(255,45,45,0.4)] bg-[color:var(--color-led-tint)]",
        state === "muted" && "border-rule bg-surface",
      )}
    >
      <div className="font-mono text-[0.625rem] font-bold uppercase tracking-[0.12em] text-muted">
        {label}
      </div>
      <div
        className={cn(
          "font-display text-[2rem] font-bold tabular-nums",
          state === "good" && "text-done",
          state === "warn" && "text-live",
          state === "bad" && "text-led",
          state === "muted" && "text-whisper",
        )}
      >
        {value == null ? "--" : value}
      </div>
    </div>
  );
}

function groupByShooter(fixtures: LabEvalFixture[]): ShooterRow[] {
  const buckets = new Map<string, { fixtures: LabEvalFixture[] }>();
  for (const f of fixtures) {
    const sh = shooterFromSlug(f.slug) ?? "unknown";
    if (!buckets.has(sh)) buckets.set(sh, { fixtures: [] });
    buckets.get(sh)!.fixtures.push(f);
  }
  return Array.from(buckets.entries())
    .map(([key, { fixtures: fxs }]) => {
      const totalTruth = fxs.reduce((a, x) => a + x.metrics.n_truth, 0);
      const totalTp = fxs.reduce((a, x) => a + x.metrics.true_positives, 0);
      const recall = totalTruth > 0 ? totalTp / totalTruth : 0;
      return {
        key,
        initials: key.slice(1, 3).toUpperCase(),
        name: key,
        recall,
        fixtures: fxs.length,
        flagged: recall < 0.85,
      };
    })
    .sort((a, b) => a.recall - b.recall);
}

interface VenueRow {
  key: string;
  fixtures: number;
  recall: number;
  precision: number;
  f1: number;
}

function groupByVenue(fixtures: LabEvalFixture[]): VenueRow[] {
  const buckets = new Map<string, LabEvalFixture[]>();
  for (const f of fixtures) {
    const v = venueFromSlug(f.slug) ?? "unknown";
    if (!buckets.has(v)) buckets.set(v, []);
    buckets.get(v)!.push(f);
  }
  return Array.from(buckets.entries())
    .map(([key, fxs]) => {
      const truth = fxs.reduce((a, x) => a + x.metrics.n_truth, 0);
      const tp = fxs.reduce((a, x) => a + x.metrics.true_positives, 0);
      const fp = fxs.reduce((a, x) => a + x.metrics.false_positives, 0);
      const recall = truth > 0 ? tp / truth : 0;
      const precision = tp + fp > 0 ? tp / (tp + fp) : 0;
      const f1 = recall + precision > 0 ? (2 * recall * precision) / (recall + precision) : 0;
      return { key, fixtures: fxs.length, recall, precision, f1 };
    })
    .sort((a, b) => a.recall - b.recall);
}

function venueFromSlug(slug: string): string | null {
  const parts = slug.split("-");
  if (parts[0] !== "stage" || parts[1] !== "shots") return null;
  const out: string[] = [];
  for (const p of parts.slice(2)) {
    if (p.length === 4 && /^\d+$/.test(p)) break;
    out.push(p);
  }
  return out.length > 0 ? out.join("-") : null;
}

function shooterFromSlug(slug: string): string | null {
  for (const p of slug.split("-")) {
    if (p.length === 9 && p.startsWith("s") && /^[0-9a-f]+$/.test(p.slice(1))) return p;
  }
  return null;
}

function shooterGradient(key: string): string {
  // Stable per-key gradient so the same shooter keeps the same chip color
  // across reloads. Hash slug -> hue.
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  const hue = hash % 360;
  return `linear-gradient(135deg, hsl(${hue} 70% 55%), hsl(${(hue + 30) % 360} 60% 35%))`;
}

function formatLocalTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
