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
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertCircle,
  Beaker,
  CheckCircle2,
  ChevronRight,
  Hammer,
  Loader2,
  Pencil,
  Play,
  RotateCcw,
  Save,
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
  LAB_REASONS,
  LAB_SUBCLASSES,
  type Job,
  type LabEvalConfig,
  type LabEvalFixture,
  type LabEvalRun,
  type LabFixtureRecord,
  type PeaksResult,
  type StageAudit,
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
          <SaveYamlButton run={run} />
          <RebuildCalibrationButton onCompleted={() => setRun(null)} />
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

      {focused ? (
        <FixtureDetail
          fixture={focused}
          onClose={() => navigate("/lab")}
          onLabelChanged={runEval}
        />
      ) : slug ? (
        <FixtureDetailLite
          record={catalog.find((r) => r.slug === slug) ?? null}
          onClose={() => navigate("/lab")}
          onRunEval={runEval}
          evalLoading={evalLoading}
        />
      ) : null}
    </div>
  );
}

function FixtureDetailLite({
  record,
  onClose,
  onRunEval,
  evalLoading,
}: {
  record: LabFixtureRecord | null;
  onClose: () => void;
  onRunEval: () => void;
  evalLoading: boolean;
}) {
  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [audit, setAudit] = useState<StageAudit | null>(null);
  const [time, setTime] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!record) return;
    setPeaks(null);
    setAudit(null);
    setError(null);
    Promise.all([
      api.getFixturePeaks(record.audit_path),
      api.getFixtureAudit(record.audit_path),
    ])
      .then(([p, a]) => {
        setPeaks(p);
        setAudit(a);
      })
      .catch((err) => setError(String(err)));
  }, [record]);

  if (!record) {
    return (
      <Card>
        <CardContent className="py-4 text-sm text-muted-foreground">
          Fixture not found in the catalog.
        </CardContent>
      </Card>
    );
  }

  const shotTimes = audit?.shots?.map((s) => s.time) ?? [];

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-3">
        <div>
          <CardTitle className="font-mono text-base">{record.slug}</CardTitle>
          <CardDescription>
            {record.n_shots} ground-truth shots
            {record.expected_rounds != null && ` · expected ${record.expected_rounds}`}
            {record.beep_time != null && ` · beep ${record.beep_time.toFixed(3)}s`}
            {" · pre-eval view (waveform + ground truth only)"}
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link
              to={`/review?fixture=${encodeURIComponent(record.audit_path)}`}
              title="Open in the review editor"
            >
              <Pencil className="size-3.5" />
              Re-label
            </Link>
          </Button>
          <Button size="sm" onClick={onRunEval} disabled={evalLoading}>
            {evalLoading ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
            Run eval
          </Button>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && (
          <div className="rounded bg-destructive/10 px-3 py-2 text-xs text-destructive">{error}</div>
        )}
        {peaks ? (
          <Waveform
            peaks={peaks.peaks}
            duration={peaks.duration}
            currentTime={time}
            onScrub={setTime}
            beepTime={peaks.beep_time}
            height={140}
          >
            {shotTimes.map((t, i) => (
              <Pin
                key={`gt-${i}`}
                time={t}
                duration={peaks.duration}
                color="var(--success, #22c55e)"
                label={`shot ${i + 1}`}
              />
            ))}
          </Waveform>
        ) : (
          <div className="flex h-[140px] items-center justify-center rounded border border-border/40 bg-muted/30 text-xs text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" /> loading waveform...
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          Diffs (TP/FP/FN), the per-voter breakdown, the candidate table, and label
          shortcuts only render after Run eval -- they need the per-candidate feature
          universe (CLAP / PANN / GBDT) which is built by eval.
        </p>
      </CardContent>
    </Card>
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
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Metric label="Precision" value={fmtPct(s.precision)} />
          <Metric label="Recall" value={fmtPct(s.recall)} />
          <Metric label="F1" value={s.f1.toFixed(3)} />
          <Metric
            label="TP / FP / FN"
            value={`${s.true_positives} / ${s.false_positives} / ${s.false_negatives}`}
          />
        </div>
        <LabelBreakdown
          fpByReason={s.fp_by_reason}
          positivesBySubclass={s.positives_by_subclass}
        />
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
                      <div className="flex items-center justify-end gap-1">
                        <Link
                          to={`/review?fixture=${encodeURIComponent(rec.audit_path)}`}
                          onClick={(e) => e.stopPropagation()}
                          className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                          title="Re-label this fixture in the review editor"
                          aria-label={`Re-label ${rec.slug}`}
                        >
                          <Pencil className="size-3.5" />
                        </Link>
                        <ChevronRight className="size-3.5" />
                      </div>
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

// Single-key shortcuts when a candidate row is selected.
// For rejected (FP / not-kept) candidates: set ``reason``.
// For kept positives (TP): set ``subclass`` (paper/steel/unknown).
const REASON_SHORTCUTS: Record<string, string> = {
  x: "cross_bay",
  e: "echo",
  w: "wind",
  m: "movement",
  s: "steel_ring",
  h: "handling",
  a: "agc_artifact",
  y: "speech", // mnemonic: spYech (s is steel_ring)
  o: "other",
  u: "unknown",
};
const SUBCLASS_SHORTCUTS: Record<string, string> = {
  p: "paper",
  s: "steel",
  u: "unknown",
};

function FixtureDetail({
  fixture,
  onClose,
  onLabelChanged,
}: {
  fixture: LabEvalFixture;
  onClose: () => void;
  onLabelChanged: () => void;
}) {
  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [time, setTime] = useState(0);
  const [savingLabel, setSavingLabel] = useState<number | null>(null);
  const [selectedCn, setSelectedCn] = useState<number | null>(null);

  useEffect(() => {
    setPeaks(null);
    setSelectedCn(null);
    api
      .getFixturePeaks(fixture.audit_path)
      .then(setPeaks)
      .catch(() => setPeaks(null));
  }, [fixture.audit_path]);

  const handleLabel = useCallback(
    async (
      candidate_number: number,
      patch: { reason?: string | null; subclass?: string | null },
    ) => {
      setSavingLabel(candidate_number);
      try {
        await api.applyLabLabels({
          audit_path: fixture.audit_path,
          labels: [{ candidate_number, ...patch }],
        });
        onLabelChanged();
      } catch (err) {
        console.error("label save failed", err);
      } finally {
        setSavingLabel(null);
      }
    },
    [fixture.audit_path, onLabelChanged],
  );

  // Keyboard shortcuts: row selection + label assignment.
  useEffect(() => {
    function isTypingTarget(t: EventTarget | null): boolean {
      if (!(t instanceof HTMLElement)) return false;
      if (t.isContentEditable) return true;
      const tag = t.tagName;
      return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    }
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTypingTarget(e.target)) return;
      const cands = fixture.candidates;
      if (cands.length === 0) return;
      const idx = selectedCn != null ? cands.findIndex((c) => c.candidate_number === selectedCn) : -1;

      // Navigation: j/k or ArrowDown/Up. Wraps at edges.
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        const next = idx < 0 ? 0 : Math.min(cands.length - 1, idx + 1);
        setSelectedCn(cands[next].candidate_number);
        return;
      }
      if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = idx < 0 ? cands.length - 1 : Math.max(0, idx - 1);
        setSelectedCn(cands[next].candidate_number);
        return;
      }
      if (e.key === "Escape") {
        setSelectedCn(null);
        return;
      }
      if (idx < 0) return;
      const c = cands[idx];

      // Clear: 0 or Backspace.
      if (e.key === "0" || e.key === "Backspace") {
        e.preventDefault();
        if (c.kept && c.truth === 1) {
          handleLabel(c.candidate_number, { subclass: null });
        } else {
          handleLabel(c.candidate_number, { reason: null });
        }
        return;
      }

      const key = e.key.toLowerCase();
      if (c.kept && c.truth === 1) {
        const sub = SUBCLASS_SHORTCUTS[key];
        if (sub) {
          e.preventDefault();
          handleLabel(c.candidate_number, { subclass: sub });
        }
      } else {
        const reason = REASON_SHORTCUTS[key];
        if (reason) {
          e.preventDefault();
          handleLabel(c.candidate_number, { reason });
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fixture.candidates, selectedCn, handleLabel]);

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
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link
              to={`/review?fixture=${encodeURIComponent(fixture.audit_path)}`}
              title="Open this fixture in the review editor (re-label shots, edit beep, save in place)"
            >
              <Pencil className="size-3.5" />
              Re-label
            </Link>
          </Button>
          <Button variant="ghost" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
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

        <LabelBreakdown
          fpByReason={fixture.metrics.fp_by_reason}
          positivesBySubclass={fixture.metrics.positives_by_subclass}
        />

        <CandidateTable
          candidates={fixture.candidates}
          onLabel={handleLabel}
          savingLabel={savingLabel}
          selectedCn={selectedCn}
          onSelect={setSelectedCn}
        />
        <KeyboardLegend selectedCn={selectedCn} />
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
  onLabel,
  savingLabel,
  selectedCn,
  onSelect,
}: {
  candidates: LabEvalFixture["candidates"];
  onLabel: (
    candidate_number: number,
    patch: { reason?: string | null; subclass?: string | null },
  ) => void;
  savingLabel: number | null;
  selectedCn: number | null;
  onSelect: (cn: number | null) => void;
}) {
  // Auto-scroll the selected row into view when it changes via keyboard nav.
  useEffect(() => {
    if (selectedCn == null) return;
    const el = document.querySelector(`[data-cn="${selectedCn}"]`);
    if (el && "scrollIntoView" in el) {
      (el as HTMLElement).scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [selectedCn]);

  return (
    <details className="rounded border border-border/60" open>
      <summary className="cursor-pointer px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Candidates ({candidates.length}) -- click a row + use shortcut keys (or the dropdown)
      </summary>
      <div className="max-h-96 overflow-y-auto">
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
              <th className="px-2 py-1 text-left font-medium">label</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => {
              const isTP = c.kept && c.truth === 1;
              const isFP = c.kept && c.truth === 0;
              const isFN = !c.kept && c.truth === 1;
              const saving = savingLabel === c.candidate_number;
              const selected = selectedCn === c.candidate_number;
              return (
                <tr
                  key={c.candidate_number}
                  data-cn={c.candidate_number}
                  className={cn(
                    "cursor-pointer border-b border-border/20 font-mono",
                    isTP && "bg-emerald-500/5",
                    isFP && "bg-orange-500/10",
                    isFN && "bg-red-500/10",
                    selected && "outline outline-2 outline-primary/70",
                  )}
                  onClick={() => onSelect(selected ? null : c.candidate_number)}
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
                  <td className="px-2 py-1">
                    <LabelDropdown
                      candidate={c}
                      onChange={(patch) => onLabel(c.candidate_number, patch)}
                      saving={saving}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function LabelDropdown({
  candidate,
  onChange,
  saving,
}: {
  candidate: LabEvalFixture["candidates"][number];
  onChange: (patch: { reason?: string | null; subclass?: string | null }) => void;
  saving: boolean;
}) {
  // Kept positive (TP): edit subclass. Kept FP: edit reason. Rejected
  // candidates aren't worth labelling -- they don't survive consensus
  // so they don't pollute precision -- but we still let the user tag a
  // reason for rejected ones if they want a record (e.g. for #87
  // mining cross-references).
  const isKept = candidate.kept;
  const isPositive = candidate.truth === 1;
  if (isKept && isPositive) {
    return (
      <div className="flex items-center gap-1">
        <select
          value={candidate.subclass ?? ""}
          onChange={(e) => onChange({ subclass: e.target.value || null })}
          className="rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]"
          disabled={saving}
        >
          <option value="">--</option>
          {LAB_SUBCLASSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {saving && <Loader2 className="size-3 animate-spin text-muted-foreground" />}
      </div>
    );
  }
  return (
    <div className="flex items-center gap-1">
      <select
        value={candidate.reason ?? ""}
        onChange={(e) => onChange({ reason: e.target.value || null })}
        className={cn(
          "rounded border border-border/60 bg-background px-1 py-0.5 text-[11px]",
          isKept && !isPositive && "border-orange-400/60",
        )}
        disabled={saving}
      >
        <option value="">--</option>
        {LAB_REASONS.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>
      {saving && <Loader2 className="size-3 animate-spin text-muted-foreground" />}
    </div>
  );
}

function LabelBreakdown({
  fpByReason,
  positivesBySubclass,
}: {
  fpByReason: Record<string, number>;
  positivesBySubclass: Record<string, number>;
}) {
  const fpEntries = Object.entries(fpByReason).filter(([, n]) => n > 0);
  const subEntries = Object.entries(positivesBySubclass).filter(([, n]) => n > 0);
  if (fpEntries.length === 0 && subEntries.length === 0) {
    return null;
  }
  return (
    <div className="rounded border border-border/60 p-3 text-xs">
      <div className="mb-2 font-semibold uppercase tracking-wide text-muted-foreground">
        Label breakdown
      </div>
      {fpEntries.length > 0 && (
        <div className="mb-2">
          <div className="text-[10px] uppercase text-orange-500">false positives by class</div>
          <ul className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono">
            {fpEntries
              .sort((a, b) => b[1] - a[1])
              .map(([k, n]) => (
                <li key={k} className="flex justify-between">
                  <span>{k}</span>
                  <span className="text-muted-foreground">{n}</span>
                </li>
              ))}
          </ul>
        </div>
      )}
      {subEntries.length > 0 && (
        <div>
          <div className="text-[10px] uppercase text-emerald-600">positives by subclass</div>
          <ul className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono">
            {subEntries
              .sort((a, b) => b[1] - a[1])
              .map(([k, n]) => (
                <li key={k} className="flex justify-between">
                  <span>{k}</span>
                  <span className="text-muted-foreground">{n}</span>
                </li>
              ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function SaveYamlButton({ run }: { run: LabEvalRun | null }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Suggest a slug derived from the active config hash so accidental
  // double-clicks don't all collide on "ensemble.tuning.yaml".
  useEffect(() => {
    if (open && !name && run) {
      setName(`tuning-${run.config_hash}`);
    }
  }, [open, name, run]);

  const submit = useCallback(async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.saveLabConfig({ name: name.trim(), note: note.trim() || undefined, overwrite });
      setResult(res.path);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }, [name, note, overwrite]);

  return (
    <div className="relative">
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen((v) => !v)}
        disabled={!run}
        title={run ? "Save current tuning as configs/ensemble.<name>.yaml" : "Run eval first"}
      >
        <Save className="size-4" />
        Save as YAML
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-80 rounded-md border border-border bg-popover p-3 shadow-md">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Save tuning
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Writes <span className="font-mono">configs/ensemble.&lt;name&gt;.yaml</span> with the active
            config + summary + provenance. Replayable via <span className="font-mono">splitsmith lab load-config</span>.
          </p>
          <label className="mt-2 block text-[11px]">
            <span className="text-muted-foreground">Name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="mt-1 w-full rounded border border-border bg-background px-2 py-1 font-mono text-xs"
              placeholder="tighter-d"
            />
          </label>
          <label className="mt-2 block text-[11px]">
            <span className="text-muted-foreground">Note (optional)</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              className="mt-1 w-full rounded border border-border bg-background px-2 py-1 text-xs"
              placeholder="Why this tuning is interesting..."
            />
          </label>
          <label className="mt-2 flex items-center gap-2 text-[11px]">
            <input
              type="checkbox"
              checked={overwrite}
              onChange={(e) => setOverwrite(e.target.checked)}
            />
            Overwrite if exists
          </label>
          {error && (
            <div className="mt-2 rounded bg-destructive/10 px-2 py-1 text-[11px] text-destructive">
              {error}
            </div>
          )}
          {result && (
            <div className="mt-2 rounded bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-700 dark:text-emerald-300">
              Saved: <span className="font-mono">{result}</span>
            </div>
          )}
          <div className="mt-3 flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
              Close
            </Button>
            <Button size="sm" onClick={submit} disabled={busy || !name.trim()}>
              {busy ? <Loader2 className="size-3.5 animate-spin" /> : "Save"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function RebuildCalibrationButton({ onCompleted }: { onCompleted: () => void }) {
  const [open, setOpen] = useState(false);
  const [targetRecall, setTargetRecall] = useState(0.95);
  const [toleranceMs, setToleranceMs] = useState(75);
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  // Poll the active job until it leaves the running state so the user
  // sees progress without leaving the Lab.
  useEffect(() => {
    if (!job || job.status === "succeeded" || job.status === "failed" || job.status === "cancelled") {
      return;
    }
    let stopped = false;
    const tick = async () => {
      try {
        const next = await api.getJob(job.id);
        if (stopped) return;
        setJob(next);
        if (next.status === "succeeded") {
          onCompleted();
        }
      } catch (err) {
        if (!stopped) setError(String(err));
      }
    };
    const id = window.setInterval(tick, 1000);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [job, onCompleted]);

  const submit = useCallback(async () => {
    setSubmitting(true);
    setError(null);
    try {
      const j = await api.rebuildLabCalibration({
        target_recall: targetRecall,
        tolerance_ms: toleranceMs,
      });
      setJob(j);
      setConfirmed(false);
    } catch (err) {
      setError(String(err));
    } finally {
      setSubmitting(false);
    }
  }, [targetRecall, toleranceMs]);

  const running = job && (job.status === "pending" || job.status === "running");
  return (
    <div className="relative">
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen((v) => !v)}
        disabled={!!running}
        title="Re-run scripts/build_ensemble_artifacts.py and refresh shipped thresholds"
      >
        {running ? <Loader2 className="size-4 animate-spin" /> : <Hammer className="size-4" />}
        Rebuild calibration
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-80 rounded-md border border-border bg-popover p-3 shadow-md">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Rebuild calibration
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Refits voter thresholds + the GBDT against every audited fixture and overwrites
            <span className="font-mono"> src/splitsmith/data/</span>. Slow (model-bound). After it
            completes, the next eval picks up the new thresholds.
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Requires the CLAP / PANN feature caches under
            <span className="font-mono"> tests/fixtures/.cache/</span> -- build them first via the
            extract scripts if a new fixture's cache is missing.
          </p>
          <label className="mt-2 block text-[11px]">
            <span className="text-muted-foreground">Target recall ({targetRecall.toFixed(2)})</span>
            <input
              type="range"
              min={0.8}
              max={1.0}
              step={0.01}
              value={targetRecall}
              onChange={(e) => setTargetRecall(Number(e.target.value))}
              className="mt-1 w-full"
            />
          </label>
          <label className="mt-2 block text-[11px]">
            <span className="text-muted-foreground">Tolerance ms ({toleranceMs.toFixed(0)})</span>
            <input
              type="range"
              min={15}
              max={150}
              step={5}
              value={toleranceMs}
              onChange={(e) => setToleranceMs(Number(e.target.value))}
              className="mt-1 w-full"
            />
          </label>
          <label className="mt-2 flex items-center gap-2 text-[11px]">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            I understand this overwrites the shipped calibration
          </label>
          {error && (
            <div className="mt-2 rounded bg-destructive/10 px-2 py-1 text-[11px] text-destructive">
              {error}
            </div>
          )}
          {job && (
            <div className="mt-2 rounded bg-muted/50 px-2 py-1 text-[11px]">
              <div className="flex items-center justify-between">
                <span className="font-mono">{job.status}</span>
                {job.message && <span className="text-muted-foreground">{job.message}</span>}
              </div>
            </div>
          )}
          <div className="mt-3 flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
              Close
            </Button>
            <Button size="sm" onClick={submit} disabled={submitting || !!running || !confirmed}>
              {submitting ? <Loader2 className="size-3.5 animate-spin" /> : "Run rebuild"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function KeyboardLegend({ selectedCn }: { selectedCn: number | null }) {
  return (
    <div
      className={cn(
        "rounded border border-border/60 px-3 py-2 text-[11px] text-muted-foreground",
        selectedCn != null && "border-primary/60 bg-primary/5 text-foreground",
      )}
    >
      <div className="mb-1 font-semibold uppercase tracking-wide">
        Keyboard {selectedCn != null ? `(row #${selectedCn} selected)` : "(click or J/K to select a row)"}
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-0.5 font-mono text-[10px] sm:grid-cols-4">
        <span><kbd>J</kbd> / <kbd>↓</kbd> next</span>
        <span><kbd>K</kbd> / <kbd>↑</kbd> prev</span>
        <span><kbd>Esc</kbd> deselect</span>
        <span><kbd>0</kbd> / <kbd>Bksp</kbd> clear</span>
        <span><kbd>X</kbd> cross_bay</span>
        <span><kbd>E</kbd> echo</span>
        <span><kbd>W</kbd> wind</span>
        <span><kbd>M</kbd> movement</span>
        <span><kbd>S</kbd> steel_ring / steel</span>
        <span><kbd>H</kbd> handling</span>
        <span><kbd>A</kbd> agc_artifact</span>
        <span><kbd>Y</kbd> speech</span>
        <span><kbd>O</kbd> other</span>
        <span><kbd>U</kbd> unknown</span>
        <span><kbd>P</kbd> paper (TP only)</span>
      </div>
    </div>
  );
}

function fmtPct(x: number): string {
  return `${(x * 100).toFixed(1)}%`;
}
