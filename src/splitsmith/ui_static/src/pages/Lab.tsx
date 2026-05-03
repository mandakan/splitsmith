/**
 * Algorithm Lab: fixture catalog, batch eval, live tuning, per-fixture
 * diff overlay. Mirrors the ``splitsmith.lab`` Python module + the
 * ``splitsmith lab`` CLI; same backend endpoints back both.
 *
 * Slow path: POST /api/lab/eval runs CLAP + PANN + GBDT against every
 * fixture (or a slug subset) and returns the per-candidate universe.
 * Fast path: POST /api/lab/rescore takes the cached universe + a new
 * EnsembleConfig and returns updated metrics in <100 ms -- that's what
 * makes the consensus + threshold sliders feel live.
 *
 * Routing:
 *   /lab          -- catalog + global metrics + tuning
 *   /lab/:slug    -- detail drawer focused on one fixture (waveform diff)
 *
 * Zero impact on the production paths: the Lab nav entry is the only
 * surface change to AppShell, and no existing route was modified.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  AlertCircle,
  Beaker,
  CheckCircle2,
  ChevronRight,
  Loader2,
  Play,
  RotateCcw,
  Settings2,
} from "lucide-react";

import { Waveform } from "@/components/Waveform";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  api,
  type LabEvalConfig,
  type LabEvalFixture,
  type LabEvalRun,
  type LabFixtureRecord,
  type PeaksResult,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const DEFAULT_CONFIG: LabEvalConfig = {
  consensus: 3,
  apriori_boost: 1.0,
  tolerance_ms: 75.0,
  use_expected_rounds: true,
  voter_a_floor_override: null,
  voter_b_threshold_override: null,
  voter_c_threshold_override: null,
  voter_d_threshold_override: null,
};

export function Lab() {
  const navigate = useNavigate();
  const { slug } = useParams<{ slug?: string }>();
  const [catalog, setCatalog] = useState<LabFixtureRecord[]>([]);
  const [run, setRun] = useState<LabEvalRun | null>(null);
  const [config, setConfig] = useState<LabEvalConfig>(DEFAULT_CONFIG);
  const [evalLoading, setEvalLoading] = useState(false);
  const [rescoreLoading, setRescoreLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listLabFixtures()
      .then(setCatalog)
      .catch((err) => setError(String(err)));
  }, []);

  const runEval = useCallback(async () => {
    setEvalLoading(true);
    setError(null);
    try {
      const result = await api.runLabEval({ config, persist: true });
      setRun(result);
    } catch (err) {
      setError(String(err));
    } finally {
      setEvalLoading(false);
    }
  }, [config]);

  // Live rescore: when the user moves a slider, hit /api/lab/rescore. Skip
  // when we don't have a cached universe yet (the user must run eval at
  // least once first).
  useEffect(() => {
    if (!run) return;
    setRescoreLoading(true);
    const id = window.setTimeout(async () => {
      try {
        const updated = await api.rescoreLabUniverse(config);
        setRun(updated);
      } catch (err) {
        setError(String(err));
      } finally {
        setRescoreLoading(false);
      }
    }, 120);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config]);

  const focused = useMemo(() => {
    if (!run || !slug) return null;
    return run.universe.fixtures.find((f) => f.slug === slug) ?? null;
  }, [run, slug]);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <Beaker className="size-5 text-primary" />
            Algorithm Lab
          </h1>
          <p className="text-sm text-muted-foreground">
            Fixture catalog, ensemble eval, and live tuning. End-user paths are unaffected.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {run && (
            <Badge variant="outline" className="font-mono text-[10px]">
              cfg {run.config_hash}
            </Badge>
          )}
          <Button onClick={runEval} disabled={evalLoading}>
            {evalLoading ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Play className="size-4" />
            )}
            {run ? "Re-run eval" : "Run eval"}
          </Button>
        </div>
      </div>

      {error && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="flex items-start gap-2 py-3 text-sm text-destructive">
            <AlertCircle className="size-4 shrink-0" />
            {error}
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[2fr_1fr]">
        <SummaryCard run={run} rescoreLoading={rescoreLoading} />
        <TuningCard
          config={config}
          run={run}
          onChange={(next) => setConfig({ ...config, ...next })}
          onReset={() => setConfig(DEFAULT_CONFIG)}
        />
      </div>

      <FixtureTable
        catalog={catalog}
        run={run}
        activeSlug={slug ?? null}
        onSelect={(s) => navigate(s ? `/lab/${s}` : "/lab")}
      />

      {focused && <FixtureDetail fixture={focused} onClose={() => navigate("/lab")} />}
    </div>
  );
}

function SummaryCard({
  run,
  rescoreLoading,
}: {
  run: LabEvalRun | null;
  rescoreLoading: boolean;
}) {
  if (!run) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Run an eval</CardTitle>
          <CardDescription>
            Click "Run eval" to score the ensemble against every audited fixture.
            First run is slow (loads CLAP + PANN); the universe is then cached so
            slider tweaks rescore in &lt; 100 ms.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }
  const s = run.summary;
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2">
          Summary
          {rescoreLoading && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
        </CardTitle>
        <CardDescription>
          Across {s.n_fixtures} fixtures and {s.n_truth} ground-truth shots.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="Precision" value={fmtPct(s.precision)} />
        <Metric label="Recall" value={fmtPct(s.recall)} />
        <Metric label="F1" value={s.f1.toFixed(3)} />
        <Metric label="TP / FP / FN" value={`${s.true_positives} / ${s.false_positives} / ${s.false_negatives}`} />
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-0.5 font-mono text-lg">{value}</div>
    </div>
  );
}

function TuningCard({
  config,
  run,
  onChange,
  onReset,
}: {
  config: LabEvalConfig;
  run: LabEvalRun | null;
  onChange: (patch: Partial<LabEvalConfig>) => void;
  onReset: () => void;
}) {
  const cal = run?.universe;
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2">
          <Settings2 className="size-4" />
          Tuning
        </CardTitle>
        <CardDescription>
          Sliders rescore the cached universe live; "Run eval" refreshes the universe.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Slider
          label={`Consensus K (${config.consensus} of 4)`}
          value={config.consensus}
          min={1}
          max={4}
          step={1}
          onChange={(v) => onChange({ consensus: v })}
        />
        <Slider
          label={`Apriori boost (${config.apriori_boost.toFixed(2)})`}
          value={config.apriori_boost}
          min={0}
          max={2}
          step={0.05}
          onChange={(v) => onChange({ apriori_boost: v })}
        />
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={config.use_expected_rounds}
            onChange={(e) => onChange({ use_expected_rounds: e.target.checked })}
          />
          Use expected_rounds (adaptive voter C + apriori boost)
        </label>
        {cal && (
          <details className="rounded border border-border/60 bg-muted/30 px-3 py-2 text-xs">
            <summary className="cursor-pointer font-medium">Per-voter threshold overrides</summary>
            <div className="mt-3 space-y-2">
              <ThresholdRow
                label="Voter A floor"
                calibrated={cal.voter_a_floor}
                value={config.voter_a_floor_override}
                onChange={(v) => onChange({ voter_a_floor_override: v })}
                min={0}
                max={0.5}
                step={0.001}
              />
              <ThresholdRow
                label="Voter B threshold"
                calibrated={cal.voter_b_threshold}
                value={config.voter_b_threshold_override}
                onChange={(v) => onChange({ voter_b_threshold_override: v })}
                min={-0.05}
                max={0.2}
                step={0.001}
              />
              <ThresholdRow
                label="Voter C threshold"
                calibrated={cal.voter_c_threshold}
                value={config.voter_c_threshold_override}
                onChange={(v) => onChange({ voter_c_threshold_override: v })}
                min={0}
                max={1}
                step={0.005}
              />
              <ThresholdRow
                label="Voter D threshold"
                calibrated={cal.voter_d_threshold}
                value={config.voter_d_threshold_override}
                onChange={(v) => onChange({ voter_d_threshold_override: v })}
                min={0}
                max={0.5}
                step={0.001}
              />
            </div>
          </details>
        )}
        <div className="flex items-center gap-2 pt-1">
          <Button variant="ghost" size="sm" onClick={onReset}>
            <RotateCcw className="size-3.5" />
            Reset
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block text-xs">
      <div className="mb-1 font-medium text-foreground">{label}</div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
      />
    </label>
  );
}

function ThresholdRow({
  label,
  calibrated,
  value,
  onChange,
  min,
  max,
  step,
}: {
  label: string;
  calibrated: number;
  value: number | null;
  onChange: (v: number | null) => void;
  min: number;
  max: number;
  step: number;
}) {
  const active = value !== null;
  const display = active ? value : calibrated;
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{label}</span>
        <span className="font-mono text-[10px] text-muted-foreground">
          {active ? `override ${display.toFixed(4)}` : `calibrated ${calibrated.toFixed(4)}`}
        </span>
      </div>
      <div className="mt-1 flex items-center gap-2">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={display}
          onChange={(e) => onChange(Number(e.target.value))}
          className="flex-1"
        />
        {active && (
          <button
            type="button"
            className="text-[10px] text-muted-foreground hover:text-foreground"
            onClick={() => onChange(null)}
          >
            clear
          </button>
        )}
      </div>
    </div>
  );
}

function FixtureTable({
  catalog,
  run,
  activeSlug,
  onSelect,
}: {
  catalog: LabFixtureRecord[];
  run: LabEvalRun | null;
  activeSlug: string | null;
  onSelect: (slug: string | null) => void;
}) {
  const metricsBySlug = useMemo(() => {
    const map = new Map<string, LabEvalFixture>();
    run?.universe.fixtures.forEach((f) => map.set(f.slug, f));
    return map;
  }, [run]);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Fixtures</CardTitle>
        <CardDescription>
          {catalog.length} audited fixtures. Click a row for the per-candidate diff.
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/60 text-[11px] uppercase tracking-wide text-muted-foreground">
                <th className="px-4 py-2 text-left font-medium">Slug</th>
                <th className="px-3 py-2 text-right font-medium">Truth</th>
                <th className="px-3 py-2 text-right font-medium">Kept</th>
                <th className="px-3 py-2 text-right font-medium">P</th>
                <th className="px-3 py-2 text-right font-medium">R</th>
                <th className="px-3 py-2 text-right font-medium">F1</th>
                <th className="px-3 py-2 text-right font-medium">FP</th>
                <th className="px-3 py-2 text-right font-medium">FN</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {catalog.map((rec) => {
                const m = metricsBySlug.get(rec.slug);
                const active = rec.slug === activeSlug;
                return (
                  <tr
                    key={rec.slug}
                    className={cn(
                      "cursor-pointer border-b border-border/40 hover:bg-muted/40",
                      active && "bg-accent/40",
                    )}
                    onClick={() => onSelect(active ? null : rec.slug)}
                  >
                    <td className="px-4 py-2 font-mono text-xs">
                      {rec.slug}
                      {!rec.has_audio && (
                        <Badge variant="destructive" className="ml-2 text-[10px]">
                          no wav
                        </Badge>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs">{rec.n_shots}</td>
                    <td className="px-3 py-2 text-right font-mono text-xs">
                      {m ? m.metrics.n_kept : "--"}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs">
                      {m ? fmtPct(m.metrics.precision) : "--"}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs">
                      {m ? fmtPct(m.metrics.recall) : "--"}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs">
                      {m ? m.metrics.f1.toFixed(3) : "--"}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs">
                      {m ? (
                        <span className={m.metrics.false_positives ? "text-orange-500" : ""}>
                          {m.metrics.false_positives}
                        </span>
                      ) : (
                        "--"
                      )}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs">
                      {m ? (
                        <span className={m.metrics.false_negatives ? "text-red-500" : ""}>
                          {m.metrics.false_negatives}
                        </span>
                      ) : (
                        "--"
                      )}
                    </td>
                    <td className="px-2 py-2 text-muted-foreground">
                      <ChevronRight className="size-3.5" />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function FixtureDetail({
  fixture,
  onClose,
}: {
  fixture: LabEvalFixture;
  onClose: () => void;
}) {
  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [time, setTime] = useState(0);

  useEffect(() => {
    setPeaks(null);
    api
      .getFixturePeaks(fixture.audit_path)
      .then(setPeaks)
      .catch(() => setPeaks(null));
  }, [fixture.audit_path]);

  const fps = fixture.candidates.filter((c) => c.kept && c.truth === 0);
  const fns = fixture.truth_times.filter((t) => {
    const matched = fixture.candidates.some((c) => c.truth === 1 && c.matched_shot_number !== null && Math.abs(c.time - t) < 0.001);
    return !matched;
  });

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-3">
        <div>
          <CardTitle className="font-mono text-base">{fixture.slug}</CardTitle>
          <CardDescription>
            {fixture.metrics.n_truth} ground-truth shots, {fixture.metrics.n_kept} kept --
            P {fmtPct(fixture.metrics.precision)} / R {fmtPct(fixture.metrics.recall)} /
            F1 {fixture.metrics.f1.toFixed(3)}
          </CardDescription>
        </div>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {peaks ? (
          <Waveform
            peaks={peaks.peaks}
            duration={peaks.duration}
            currentTime={time}
            onScrub={setTime}
            beepTime={peaks.beep_time}
            height={140}
          >
            {/* Predicted shots (kept). Truth-positive (TP) green, false (FP) orange. */}
            {fixture.candidates
              .filter((c) => c.kept)
              .map((c) => (
                <Pin
                  key={`p-${c.candidate_number}`}
                  time={c.time}
                  duration={peaks.duration}
                  color={c.truth === 1 ? "var(--success, #22c55e)" : "#f97316"}
                  label={c.truth === 1 ? "TP" : "FP"}
                />
              ))}
            {/* Ground truth that no kept candidate matched (FN). */}
            {fns.map((t, i) => (
              <Pin
                key={`fn-${i}`}
                time={t}
                duration={peaks.duration}
                color="#ef4444"
                label="FN"
                top
              />
            ))}
          </Waveform>
        ) : (
          <div className="flex h-[140px] items-center justify-center rounded border border-border/40 bg-muted/30 text-xs text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" /> loading waveform...
          </div>
        )}

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <VoterRecallTable metrics={fixture.metrics} />
          <DiffList fps={fps} fns={fns} />
        </div>

        <CandidateTable candidates={fixture.candidates} />
      </CardContent>
    </Card>
  );
}

function Pin({
  time,
  duration,
  color,
  label,
  top,
}: {
  time: number;
  duration: number;
  color: string;
  label: string;
  top?: boolean;
}) {
  const left = duration > 0 ? `${(time / duration) * 100}%` : "0%";
  return (
    <div
      className="pointer-events-none absolute"
      style={{
        left,
        top: top ? 4 : undefined,
        bottom: top ? undefined : 4,
        width: 2,
        height: 32,
        background: color,
        transform: "translateX(-1px)",
      }}
      title={`${label} @ ${time.toFixed(3)}s`}
    />
  );
}

function VoterRecallTable({ metrics }: { metrics: LabEvalFixture["metrics"] }) {
  const order: Array<keyof typeof metrics.voter_recall> = ["vote_a", "vote_b", "vote_c", "vote_d"];
  return (
    <div className="rounded border border-border/60 p-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Per-voter recall on this fixture
      </div>
      <div className="grid grid-cols-4 gap-2 text-center">
        {order.map((k) => (
          <div key={String(k)}>
            <div className="text-[10px] uppercase text-muted-foreground">{String(k).slice(-1).toUpperCase()}</div>
            <div className="font-mono text-sm">{fmtPct(metrics.voter_recall[k as string] ?? 0)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function DiffList({
  fps,
  fns,
}: {
  fps: { time: number; ensemble_score: number; vote_total: number }[];
  fns: number[];
}) {
  return (
    <div className="rounded border border-border/60 p-3 text-xs">
      <div className="mb-2 font-semibold uppercase tracking-wide text-muted-foreground">
        Diffs
      </div>
      {fps.length === 0 && fns.length === 0 && (
        <div className="flex items-center gap-1 text-success">
          <CheckCircle2 className="size-3.5" /> no diffs
        </div>
      )}
      {fps.length > 0 && (
        <div className="mb-2">
          <div className="text-[10px] uppercase text-orange-500">false positives ({fps.length})</div>
          <ul className="mt-1 space-y-0.5 font-mono">
            {fps.slice(0, 8).map((c, i) => (
              <li key={`fp-${i}`}>{c.time.toFixed(3)}s -- vote {c.vote_total} (score {c.ensemble_score.toFixed(2)})</li>
            ))}
          </ul>
        </div>
      )}
      {fns.length > 0 && (
        <div>
          <div className="text-[10px] uppercase text-red-500">false negatives ({fns.length})</div>
          <ul className="mt-1 space-y-0.5 font-mono">
            {fns.slice(0, 8).map((t, i) => (
              <li key={`fn-${i}`}>{t.toFixed(3)}s</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function CandidateTable({
  candidates,
}: {
  candidates: LabEvalFixture["candidates"];
}) {
  return (
    <details className="rounded border border-border/60">
      <summary className="cursor-pointer px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Candidates ({candidates.length})
      </summary>
      <div className="max-h-72 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-card text-[10px] uppercase tracking-wide text-muted-foreground">
            <tr className="border-b border-border/40">
              <th className="px-2 py-1 text-left font-medium">#</th>
              <th className="px-2 py-1 text-right font-medium">t (s)</th>
              <th className="px-2 py-1 text-right font-medium">conf</th>
              <th className="px-2 py-1 text-right font-medium">A</th>
              <th className="px-2 py-1 text-right font-medium">B</th>
              <th className="px-2 py-1 text-right font-medium">C</th>
              <th className="px-2 py-1 text-right font-medium">D</th>
              <th className="px-2 py-1 text-right font-medium">score</th>
              <th className="px-2 py-1 text-center font-medium">kept</th>
              <th className="px-2 py-1 text-center font-medium">truth</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => {
              const isTP = c.kept && c.truth === 1;
              const isFP = c.kept && c.truth === 0;
              const isFN = !c.kept && c.truth === 1;
              return (
                <tr
                  key={c.candidate_number}
                  className={cn(
                    "border-b border-border/20 font-mono",
                    isTP && "bg-emerald-500/5",
                    isFP && "bg-orange-500/10",
                    isFN && "bg-red-500/10",
                  )}
                >
                  <td className="px-2 py-1">{c.candidate_number}</td>
                  <td className="px-2 py-1 text-right">{c.time.toFixed(3)}</td>
                  <td className="px-2 py-1 text-right">{c.confidence.toFixed(3)}</td>
                  <td className="px-2 py-1 text-right">{c.vote_a}</td>
                  <td className="px-2 py-1 text-right">{c.vote_b}</td>
                  <td className="px-2 py-1 text-right">{c.vote_c}</td>
                  <td className="px-2 py-1 text-right">{c.vote_d}</td>
                  <td className="px-2 py-1 text-right">{c.ensemble_score.toFixed(2)}</td>
                  <td className="px-2 py-1 text-center">{c.kept ? "Y" : ""}</td>
                  <td className="px-2 py-1 text-center">{c.truth ? "Y" : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function fmtPct(x: number): string {
  return `${(x * 100).toFixed(1)}%`;
}
