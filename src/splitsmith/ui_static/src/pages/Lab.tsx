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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertCircle,
  Beaker,
  CheckCircle2,
  ChevronRight,
  FlaskConical,
  Hammer,
  Headphones,
  Link2,
  Loader2,
  Pause,
  Pencil,
  Play,
  RotateCcw,
  Save,
  Settings2,
  Trash2,
  XCircle,
} from "lucide-react";

import { SweepsCard } from "@/components/SweepsCard";
import { useConfirm } from "@/components/useConfirm";
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
  type MatchProject,
  type PeaksResult,
  type StageAudit,
  type StageExportStatus,
} from "@/lib/api";
import { slugify } from "@/lib/slugify";
import { cn } from "@/lib/utils";

const DEFAULT_CONFIG: LabEvalConfig = {
  consensus: 2,
  apriori_boost: 1.0,
  tolerance_ms: 75.0,
  use_expected_rounds: true,
  voter_a_floor_override: null,
  voter_b_threshold_override: null,
  voter_c_threshold_override: null,
};

/** Build the /review URL for a fixture, threading the source video
 *  through when available so the review page boots with the video
 *  bound (no separate ``splitsmith review --video ...`` invocation). */
function reviewUrl(auditPath: string, sourceVideo: string | null | undefined): string {
  let url = `/review?fixture=${encodeURIComponent(auditPath)}`;
  if (sourceVideo) url += `&video=${encodeURIComponent(sourceVideo)}`;
  return url;
}

function promoteReviewUrl(derivedAuditPath: string, anchorSlug: string): string {
  // Derived fixture and its anchor live side-by-side in the same dir.
  const dir = derivedAuditPath.slice(0, derivedAuditPath.lastIndexOf("/"));
  const anchorPath = `${dir}/${anchorSlug}.json`;
  return `/promote-review?fixture=${encodeURIComponent(derivedAuditPath)}&anchor=${encodeURIComponent(anchorPath)}`;
}

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
    // Hydrate from the server's most-recent run cache so navigating
    // away from /lab and back doesn't wipe the eval state.
    api
      .getLastLabRun()
      .then((r) => {
        setRun(r);
        setConfig(r.config);
      })
      .catch(() => {
        // 404 = no eval has run yet; that's the normal first-load case.
      });
  }, []);

  // Tear down the shared AudioContext + decoded-buffer cache when the
  // user leaves /lab; otherwise hundreds of MB of decoded PCM survives
  // navigation for the lifetime of the tab.
  useEffect(() => {
    return () => {
      disposeLabAudio();
    };
  }, []);

  // Coalesce concurrent runEval calls. Without this, each label-save
  // fallback (when the server cache is cold) submits its own job ->
  // 12-15 labels -> 12-15 eval jobs.
  const inFlightEvalRef = useRef<Promise<void> | null>(null);
  const runEval = useCallback(async (): Promise<void> => {
    if (inFlightEvalRef.current) return inFlightEvalRef.current;
    const p = (async () => {
      setEvalLoading(true);
      setError(null);
      try {
        const job = await api.runLabEval({ config, persist: true });
        const finished = await api.pollJob(job.id, () => {
          /* jobs rail polls /api/jobs on its own interval and renders the
             progress; we just need to await terminal status here. */
        });
        if (finished.status !== "succeeded") {
          throw new Error(finished.error ?? `eval ${finished.status}`);
        }
        const result = await api.getLastLabRun();
        setRun(result);
      } catch (err) {
        setError(String(err));
      } finally {
        setEvalLoading(false);
        inFlightEvalRef.current = null;
      }
    })();
    inFlightEvalRef.current = p;
    return p;
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
          <PromoteAllStagesButton
            catalog={catalog}
            onCatalogChanged={(next) => setCatalog(next)}
          />
          <PromoteFromAnchorButton fixtures={catalog} />
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
        onSelect={(s) =>
          navigate(s ? `/dev/legacy/lab/${s}` : "/dev/legacy/lab")
        }
        onDeleted={(deletedSlug) =>
          setCatalog((prev) => prev.filter((r) => r.slug !== deletedSlug))
        }
      />

      {focused ? (
        <FixtureDetail
          fixture={focused}
          onClose={() => navigate("/dev/legacy/lab", { replace: true })}
          onLabelChanged={(updated) => {
            if (updated) setRun(updated);
            else runEval();
          }}
        />
      ) : slug ? (
        <FixtureDetailLite
          record={catalog.find((r) => r.slug === slug) ?? null}
          onClose={() => navigate("/dev/legacy/lab", { replace: true })}
          onRunEval={runEval}
          evalLoading={evalLoading}
        />
      ) : null}

      <SweepsCard />
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
              to={reviewUrl(record.audit_path, record.source_video)}
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
                color={LAB_PALETTE.tp}
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
  onDeleted,
}: {
  catalog: LabFixtureRecord[];
  run: LabEvalRun | null;
  activeSlug: string | null;
  onSelect: (slug: string | null) => void;
  onDeleted: (slug: string) => void;
}) {
  const metricsBySlug = useMemo(() => {
    const map = new Map<string, LabEvalFixture>();
    run?.universe.fixtures.forEach((f) => map.set(f.slug, f));
    return map;
  }, [run]);

  // Group catalog rows by ``event_id`` so multi-camera siblings render
  // together. Sort: events with siblings first (multi-cam coverage is
  // the more interesting case), then by event_id, then ungrouped rows
  // alphabetically. Within an event, anchor-style rows (no anchor_slug)
  // come before derived siblings so the headcam baseline reads first.
  const groupedCatalog = useMemo(() => {
    const groups = new Map<string, LabFixtureRecord[]>();
    const ungrouped: LabFixtureRecord[] = [];
    for (const rec of catalog) {
      if (rec.event_id) {
        const list = groups.get(rec.event_id) ?? [];
        list.push(rec);
        groups.set(rec.event_id, list);
      } else {
        ungrouped.push(rec);
      }
    }
    for (const list of groups.values()) {
      list.sort((a, b) => {
        const ad = a.anchor_slug ? 1 : 0;
        const bd = b.anchor_slug ? 1 : 0;
        if (ad !== bd) return ad - bd;
        return a.slug.localeCompare(b.slug);
      });
    }
    const ordered: { eventId: string | null; rows: LabFixtureRecord[] }[] = [];
    const sortedEventIds = Array.from(groups.keys()).sort((a, b) => {
      const aSize = groups.get(a)!.length;
      const bSize = groups.get(b)!.length;
      if (aSize !== bSize) return bSize - aSize;
      return a.localeCompare(b);
    });
    for (const eventId of sortedEventIds) {
      ordered.push({ eventId, rows: groups.get(eventId)! });
    }
    if (ungrouped.length > 0) {
      ungrouped.sort((a, b) => a.slug.localeCompare(b.slug));
      ordered.push({ eventId: null, rows: ungrouped });
    }
    return ordered;
  }, [catalog]);

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
                <th
                  className="px-3 py-2 text-right font-medium"
                  title="Labeled candidates / total -- rough labeling progress"
                >
                  Labels
                </th>
                <th className="px-3 py-2 text-right font-medium">P</th>
                <th className="px-3 py-2 text-right font-medium">R</th>
                <th className="px-3 py-2 text-right font-medium">F1</th>
                <th className="px-3 py-2 text-right font-medium">FP</th>
                <th className="px-3 py-2 text-right font-medium">FN</th>
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {groupedCatalog.flatMap((group) => {
                const rows: React.ReactNode[] = [];
                if (group.eventId !== null && group.rows.length > 1) {
                  // Always show the full event_id including the shooter
                  // suffix -- per user direction, shooter identity is
                  // load-bearing for training data and shouldn't be
                  // implicit. ``self`` rows surface as such so they can
                  // be re-tagged later when SSI ids become available.
                  rows.push(
                    <tr
                      key={`event:${group.eventId}`}
                      className="bg-muted/30 text-[10px] uppercase tracking-wide text-muted-foreground"
                    >
                      <td colSpan={10} className="px-4 py-1 font-medium">
                        Event {group.eventId} -- {group.rows.length} cameras
                      </td>
                    </tr>,
                  );
                }
                for (const rec of group.rows) {
                  const m = metricsBySlug.get(rec.slug);
                  const active = rec.slug === activeSlug;
                  const isSibling =
                    group.eventId !== null && group.rows.length > 1;
                  rows.push(<FixtureRow
                    key={rec.slug}
                    rec={rec}
                    m={m}
                    active={active}
                    onSelect={onSelect}
                    onDeleted={onDeleted}
                    isSibling={isSibling}
                  />);
                }
                return rows;
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function FixtureRow({
  rec,
  m,
  active,
  isSibling,
  onSelect,
  onDeleted,
}: {
  rec: LabFixtureRecord;
  m: LabEvalFixture | undefined;
  active: boolean;
  isSibling: boolean;
  onSelect: (slug: string | null) => void;
  onDeleted: (slug: string) => void;
}) {
  const confirm = useConfirm();
  return (
    <tr
      className={cn(
        "cursor-pointer border-b border-border/40 hover:bg-muted/40",
        active && "bg-accent/40",
      )}
      onClick={() => onSelect(active ? null : rec.slug)}
    >
      <td
        className={cn(
          "px-4 py-2 font-mono text-xs",
          isSibling && "pl-8 text-muted-foreground",
        )}
      >
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
        {m ? <LabelProgress fixture={m} /> : "--"}
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
          {rec.anchor_slug && (
            <Link
              to={promoteReviewUrl(rec.audit_path, rec.anchor_slug)}
              onClick={(e) => e.stopPropagation()}
              className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
              title="Re-open the secondary diff-confirm review"
              aria-label={`Re-review promotion ${rec.slug}`}
            >
              <Link2 className="size-3.5" />
            </Link>
          )}
          <Link
            to={reviewUrl(rec.audit_path, rec.source_video)}
            onClick={(e) => e.stopPropagation()}
            className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
            title="Re-label this fixture in the review editor"
            aria-label={`Re-label ${rec.slug}`}
          >
            <Pencil className="size-3.5" />
          </Link>
          {rec.anchor_slug && (
            <button
              type="button"
              onClick={async (e) => {
                e.stopPropagation();
                const ok = await confirm({
                  title: `Delete derived fixture "${rec.slug}"?`,
                  body: "This removes the JSON, WAV, peaks and promotion-report.",
                });
                if (!ok.confirmed) return;
                try {
                  await api.deleteFixture(rec.slug);
                  onDeleted(rec.slug);
                } catch (err) {
                  window.alert(`Delete failed: ${err}`);
                }
              }}
              className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              title="Delete this derived fixture (anchor not affected)"
              aria-label={`Delete derived fixture ${rec.slug}`}
            >
              <Trash2 className="size-3.5" />
            </button>
          )}
          <ChevronRight className="size-3.5" />
        </div>
      </td>
    </tr>
  );
}

/** Per-fixture labeling progress: count of candidates carrying any
 *  ``reason`` or ``subclass`` over the total candidate universe.
 *  Rough by design -- not every candidate is worth labeling, but the
 *  ratio still ranks fixtures by how much labeling effort has gone in. */
function LabelProgress({ fixture }: { fixture: LabEvalFixture }) {
  const total = fixture.candidates.length;
  const labeled = fixture.candidates.filter((c) => c.reason || c.subclass).length;
  if (total === 0) return <>--</>;
  const pct = Math.round((labeled / total) * 100);
  return (
    <span title={`${labeled} of ${total} candidates carry a label`}>
      {labeled}/{total} ({pct}%)
    </span>
  );
}

// Single-key shortcuts when a candidate row is selected.
// For rejected (FP / not-kept) candidates: set ``reason``.
// For kept positives (TP): set ``subclass`` (paper/steel/unknown).
const REASON_SHORTCUTS: Record<string, string> = {
  x: "cross_bay",
  e: "echo",
  b: "barrel_echo",
  w: "wind",
  m: "movement",
  s: "steel_ring",
  h: "handling",
  a: "agc_artifact",
  v: "speech", // mnemonic: Voice. Was Y, but Y/S confusion (steel_ring next
  // to speech on QWERTY) caused a lot of mis-clicks in practice.
  o: "other",
  u: "unknown",
};
const SUBCLASS_SHORTCUTS: Record<string, string> = {
  p: "paper",
  s: "steel",
  b: "barrel",
  u: "unknown",
};

function FixtureDetail({
  fixture,
  onClose,
  onLabelChanged,
}: {
  fixture: LabEvalFixture;
  onClose: () => void;
  onLabelChanged: (updated: LabEvalRun | null) => void;
}) {
  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [time, setTime] = useState(0);
  const [savingLabel, setSavingLabel] = useState<number | null>(null);
  const [selectedCn, setSelectedCn] = useState<number | null>(null);
  const [stepThrough, setStepThrough] = useState(false);

  useEffect(() => {
    setPeaks(null);
    setSelectedCn(null);
    setStepThrough(false);
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
      // Time is the storage key, so we look it up from the live
      // candidate list before sending. Falls back to candidate_number-
      // only if the candidate has been removed mid-flight (rare).
      const cand = fixture.candidates.find((c) => c.candidate_number === candidate_number);
      if (!cand) {
        console.warn("label save: candidate", candidate_number, "not found");
        return;
      }
      setSavingLabel(candidate_number);
      try {
        const resp = await api.applyLabLabels({
          audit_path: fixture.audit_path,
          labels: [{ candidate_number, time: cand.time, ...patch }],
        });
        // Server returns a freshly-relabeled run when a cached eval
        // exists; otherwise it returns null and the parent triggers a
        // full eval as a fallback.
        onLabelChanged(resp.run);
      } catch (err) {
        console.error("label save failed", err);
      } finally {
        setSavingLabel(null);
      }
    },
    [fixture.audit_path, fixture.candidates, onLabelChanged],
  );

  // Step-through can register a "what's the next candidate after this
  // one in my filter+sort?" resolver. Used by the keyboard handler to
  // auto-advance after a label key. The candidate table doesn't set
  // this, so labels in that mode just stay on the current row.
  const advanceRef = useRef<((cn: number) => number | null) | null>(null);
  const setAdvancer = useCallback((fn: ((cn: number) => number | null) | null) => {
    advanceRef.current = fn;
  }, []);

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

      const advance = () => {
        const next = advanceRef.current?.(c.candidate_number);
        if (next != null) setSelectedCn(next);
      };

      // Truth-positive candidates (whether the consensus kept them or
      // not) take a subclass label; non-truth candidates take a reason.
      // Treating FN candidates as positives lets you mark a rejected
      // truth shot with paper / steel / barrel directly, which is the
      // training signal for recovering missed shots.
      const isPositive = c.truth === 1;

      // Clear: 0 or Backspace.
      if (e.key === "0" || e.key === "Backspace") {
        e.preventDefault();
        if (isPositive) {
          handleLabel(c.candidate_number, { subclass: null });
        } else {
          handleLabel(c.candidate_number, { reason: null });
        }
        advance();
        return;
      }

      const key = e.key.toLowerCase();
      if (isPositive) {
        const sub = SUBCLASS_SHORTCUTS[key];
        if (sub) {
          e.preventDefault();
          handleLabel(c.candidate_number, { subclass: sub });
          advance();
        }
      } else {
        const reason = REASON_SHORTCUTS[key];
        if (reason) {
          e.preventDefault();
          handleLabel(c.candidate_number, { reason });
          advance();
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
              to={reviewUrl(fixture.audit_path, fixture.source_video)}
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
                  color={c.truth === 1 ? LAB_PALETTE.tp : LAB_PALETTE.fp}
                  label={c.truth === 1 ? "TP" : "FP"}
                />
              ))}
            {/* Ground truth that no kept candidate matched (FN). */}
            {fns.map((t, i) => (
              <Pin
                key={`fn-${i}`}
                time={t}
                duration={peaks.duration}
                color={LAB_PALETTE.fn}
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

        <div className="flex items-center gap-2">
          <Button
            variant={stepThrough ? "default" : "outline"}
            size="sm"
            onClick={() => setStepThrough((v) => !v)}
            title="Toggle step-through labeling mode (audio snippets + autoplay)"
          >
            <Headphones className="size-3.5" />
            {stepThrough ? "Exit step-through" : "Step through"}
          </Button>
          {stepThrough && (
            <span className="text-[11px] text-muted-foreground">
              Click a candidate or use J/K. Audio auto-plays on each candidate; space toggles play/pause. Press a label key to save and advance.
            </span>
          )}
        </div>

        {stepThrough ? (
          <StepThroughPanel
            fixture={fixture}
            selectedCn={selectedCn}
            onSelect={setSelectedCn}
            registerAdvancer={setAdvancer}
            savingLabel={savingLabel}
            onLabel={handleLabel}
          />
        ) : (
          <CandidateTable
            candidates={fixture.candidates}
            onLabel={handleLabel}
            savingLabel={savingLabel}
            selectedCn={selectedCn}
            onSelect={setSelectedCn}
          />
        )}
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
  const order: Array<keyof typeof metrics.voter_recall> = ["vote_a", "vote_b", "vote_c"];
  return (
    <div className="rounded border border-border/60 p-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Per-voter recall on this fixture
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
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

// ---------------------------------------------------------------------------
// Promote-from-anchor trigger (issue #125)
// ---------------------------------------------------------------------------

interface BatchRow {
  stageNumber: number;
  stageName: string;
  slug: string;
  exists: boolean;
  blockers: string[];
  selected: boolean;
  status: "idle" | "running" | "ok" | "error";
  message: string | null;
}

function buildBatchRows(
  project: MatchProject,
  overview: StageExportStatus[],
  catalog: LabFixtureRecord[],
): BatchRow[] {
  const existing = new Set(catalog.map((f) => f.slug));
  const token = project.shooter_token;
  const projectSlug = slugify(project.name);
  const overviewByStage = new Map(overview.map((s) => [s.stage_number, s]));
  const rows: BatchRow[] = [];
  for (const stage of project.stages) {
    if (stage.placeholder || stage.skipped) continue;
    const ov = overviewByStage.get(stage.stage_number);
    const primary = stage.videos[0] ?? null;
    const blockers: string[] = [];
    if (!ov || !ov.audit_path) blockers.push("no audit JSON (run shot-detect)");
    if (!primary) blockers.push("no primary video");
    else {
      if (primary.beep_time == null) blockers.push("primary has no beep_time");
      if (!primary.camera_mount) blockers.push("primary has no camera_mount");
    }
    const slug = token
      ? `stage-shots-${projectSlug}-stage${stage.stage_number}-${token}`
      : `stage-shots-${projectSlug}-stage${stage.stage_number}`;
    rows.push({
      stageNumber: stage.stage_number,
      stageName: stage.stage_name,
      slug,
      exists: existing.has(slug),
      blockers,
      selected: blockers.length === 0,
      status: "idle",
      message: null,
    });
  }
  return rows;
}

function PromoteAllStagesButton({
  catalog,
  onCatalogChanged,
}: {
  catalog: LabFixtureRecord[];
  onCatalogChanged: (next: LabFixtureRecord[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [project, setProject] = useState<MatchProject | null>(null);
  const [rows, setRows] = useState<BatchRow[]>([]);
  const [overwrite, setOverwrite] = useState(false);
  const [running, setRunning] = useState(false);

  // Re-derive rows when the catalog changes (so "exists" badges update after
  // a successful batch run without re-fetching the project).
  useEffect(() => {
    if (!project) return;
    setRows((prev) => {
      const existing = new Set(catalog.map((f) => f.slug));
      return prev.map((r) => ({ ...r, exists: existing.has(r.slug) }));
    });
  }, [catalog, project]);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      // Lab is process-scoped (legacy dev tool). Pull stage definitions
      // off whichever shooter is alphabetically first; in match mode they
      // share the same stage list, and in single-shooter mode there's
      // only one shooter to pick.
      const shooters = await api.listMatchShooters();
      const first = shooters.shooters[0]?.slug;
      if (!first) {
        setRows([]);
        return;
      }
      const [proj, ov] = await Promise.all([
        api.getProject(first),
        api.getExportOverview(first),
      ]);
      setProject(proj);
      setRows(buildBatchRows(proj, ov.stages, catalog));
    } catch (err) {
      setLoadError(String(err));
    } finally {
      setLoading(false);
    }
  }, [catalog]);

  const toggleOpen = useCallback(() => {
    setOpen((v) => {
      const next = !v;
      if (next) void load();
      return next;
    });
  }, [load]);

  const toggleRow = useCallback((stageNumber: number) => {
    setRows((prev) =>
      prev.map((r) =>
        r.stageNumber === stageNumber && r.blockers.length === 0
          ? { ...r, selected: !r.selected }
          : r,
      ),
    );
  }, []);

  const setAllSelected = useCallback((selected: boolean) => {
    setRows((prev) =>
      prev.map((r) => (r.blockers.length === 0 ? { ...r, selected } : r)),
    );
  }, []);

  const submit = useCallback(async () => {
    setRunning(true);
    const queue = rows.filter((r) => r.selected && r.blockers.length === 0);
    setRows((prev) =>
      prev.map((r) =>
        queue.find((q) => q.stageNumber === r.stageNumber)
          ? { ...r, status: "running", message: null }
          : r,
      ),
    );
    for (const row of queue) {
      try {
        const rec = await api.promoteFixture({
          stage_number: row.stageNumber,
          slug: row.slug,
          overwrite,
        });
        setRows((prev) =>
          prev.map((r) =>
            r.stageNumber === row.stageNumber
              ? { ...r, status: "ok", message: rec.audit_path }
              : r,
          ),
        );
      } catch (err) {
        setRows((prev) =>
          prev.map((r) =>
            r.stageNumber === row.stageNumber
              ? { ...r, status: "error", message: String(err) }
              : r,
          ),
        );
      }
    }
    try {
      const next = await api.listLabFixtures();
      onCatalogChanged(next);
    } catch {
      // Catalog refresh is best-effort; row-level "ok" status already
      // confirms the server accepted each promote.
    }
    setRunning(false);
  }, [rows, overwrite, onCatalogChanged]);

  const eligibleCount = rows.filter((r) => r.blockers.length === 0).length;
  const selectedCount = rows.filter(
    (r) => r.selected && r.blockers.length === 0,
  ).length;
  const shooterPinned = project?.selected_shooter_id != null;
  const allEligibleSelected = eligibleCount > 0 && selectedCount === eligibleCount;

  return (
    <div className="relative">
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5"
        onClick={toggleOpen}
        title="Promote every eligible stage in this project as a primary fixture"
      >
        <FlaskConical className="size-3.5" />
        Promote all stages
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-[640px] rounded-md border border-border bg-popover p-4 shadow-md">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Promote all stages
          </div>
          <p className="text-[11px] text-muted-foreground mb-3">
            Batch-runs the per-stage primary-fixture promote against every
            stage that has an audit JSON, a primary beep, and a camera mount.
            Same write path as the single-stage button on the Audit page.
          </p>
          {loading && (
            <div className="flex items-center gap-2 py-6 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              Loading project + export overview...
            </div>
          )}
          {!loading && loadError && (
            <div className="flex gap-1.5 rounded bg-destructive/10 px-2 py-1.5 text-[11px] text-destructive">
              <AlertCircle className="size-3.5 mt-0.5 shrink-0" />
              {loadError}
            </div>
          )}
          {!loading && !loadError && project && !shooterPinned && (
            <div className="flex gap-1.5 rounded bg-destructive/10 px-2 py-1.5 text-[11px] text-destructive">
              <AlertCircle className="size-3.5 mt-0.5 shrink-0" />
              This project has no SSI shooter pinned. Pin one via the Ingest
              page before promoting -- the server refuses to land fixtures
              with an unknown shooter.
            </div>
          )}
          {!loading && !loadError && project && shooterPinned && rows.length === 0 && (
            <div className="rounded bg-muted px-2 py-1.5 text-[11px] text-muted-foreground">
              No non-placeholder stages in this project.
            </div>
          )}
          {!loading && !loadError && project && shooterPinned && rows.length > 0 && (
            <>
              <div className="mb-2 flex items-center justify-between text-[11px] text-muted-foreground">
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={allEligibleSelected}
                    onChange={(e) => setAllSelected(e.target.checked)}
                    disabled={running || eligibleCount === 0}
                  />
                  Select all eligible ({eligibleCount})
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={overwrite}
                    onChange={(e) => setOverwrite(e.target.checked)}
                    disabled={running}
                  />
                  Overwrite if exists
                </label>
              </div>
              <div className="max-h-80 overflow-y-auto rounded border border-border">
                <table className="w-full text-[11px]">
                  <thead className="sticky top-0 bg-muted/60 text-left text-muted-foreground">
                    <tr>
                      <th className="px-2 py-1.5 w-6"></th>
                      <th className="px-2 py-1.5 w-10">#</th>
                      <th className="px-2 py-1.5">Stage / slug</th>
                      <th className="px-2 py-1.5 w-32">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const eligible = row.blockers.length === 0;
                      const willOverwrite = row.exists && overwrite;
                      const blocked = !eligible;
                      return (
                        <tr
                          key={row.stageNumber}
                          className={cn(
                            "border-t border-border align-top",
                            blocked && "opacity-60",
                          )}
                        >
                          <td className="px-2 py-1.5">
                            <input
                              type="checkbox"
                              checked={row.selected && eligible}
                              disabled={!eligible || running}
                              onChange={() => toggleRow(row.stageNumber)}
                            />
                          </td>
                          <td className="px-2 py-1.5 font-mono">
                            {row.stageNumber}
                          </td>
                          <td className="px-2 py-1.5">
                            <div className="font-medium">{row.stageName}</div>
                            <div className="mt-0.5 font-mono text-[10px] text-muted-foreground break-all">
                              {row.slug}
                            </div>
                            {row.blockers.length > 0 && (
                              <div className="mt-0.5 text-[10px] text-amber-700 dark:text-amber-300">
                                blocked: {row.blockers.join("; ")}
                              </div>
                            )}
                            {row.status === "error" && row.message && (
                              <div className="mt-0.5 text-[10px] text-destructive break-words">
                                {row.message}
                              </div>
                            )}
                            {row.status === "ok" && row.message && (
                              <div className="mt-0.5 font-mono text-[10px] text-emerald-700 dark:text-emerald-300 break-all">
                                {row.message}
                              </div>
                            )}
                          </td>
                          <td className="px-2 py-1.5">
                            {row.status === "running" && (
                              <span className="inline-flex items-center gap-1 text-muted-foreground">
                                <Loader2 className="size-3 animate-spin" />
                                promoting
                              </span>
                            )}
                            {row.status === "ok" && (
                              <span className="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-300">
                                <CheckCircle2 className="size-3" />
                                promoted
                              </span>
                            )}
                            {row.status === "error" && (
                              <span className="inline-flex items-center gap-1 text-destructive">
                                <XCircle className="size-3" />
                                failed
                              </span>
                            )}
                            {row.status === "idle" && eligible && row.exists && (
                              <span
                                className={cn(
                                  "inline-flex items-center gap-1",
                                  willOverwrite
                                    ? "text-amber-700 dark:text-amber-300"
                                    : "text-muted-foreground",
                                )}
                                title={
                                  willOverwrite
                                    ? "A fixture with this slug exists; overwrite is on so it will be replaced."
                                    : "A fixture with this slug exists; toggle 'Overwrite if exists' to replace it. Otherwise the server will reject this row."
                                }
                              >
                                exists
                                {willOverwrite ? " (overwrite)" : ""}
                              </span>
                            )}
                            {row.status === "idle" && eligible && !row.exists && (
                              <span className="text-muted-foreground">
                                ready
                              </span>
                            )}
                            {row.status === "idle" && !eligible && (
                              <span className="text-muted-foreground">
                                blocked
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <div className="flex items-center gap-2 pt-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setOpen(false)}
              disabled={running}
            >
              Close
            </Button>
            <span className="ml-auto text-[11px] text-muted-foreground">
              {selectedCount} selected
            </span>
            <Button
              size="sm"
              onClick={submit}
              disabled={running || selectedCount === 0 || !shooterPinned}
            >
              {running ? (
                <Loader2 className="size-3.5 animate-spin mr-1" />
              ) : null}
              Promote selected
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function PromoteFromAnchorButton({ fixtures }: { fixtures: LabFixtureRecord[] }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [anchorSlug, setAnchorSlug] = useState("");
  const [secondaryWav, setSecondaryWav] = useState("");
  const [slug, setSlug] = useState("");
  const [cameraId, setCameraId] = useState("");
  const [mount, setMount] = useState("tripod");
  const [position, setPosition] = useState("bay-fixed");
  const [audioSource, setAudioSource] = useState("internal");
  const [snapWindowMs, setSnapWindowMs] = useState(60);
  const [overwrite, setOverwrite] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [resolvedPaths, setResolvedPaths] = useState<{
    fixture_path: string;
    anchor_path: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (
      !job ||
      job.status === "succeeded" ||
      job.status === "failed" ||
      job.status === "cancelled"
    )
      return;
    let stopped = false;
    const tick = async () => {
      try {
        const j = await api.getJob(job.id);
        if (stopped) return;
        setJob(j);
        if (j.status === "succeeded" && resolvedPaths) {
          setOpen(false);
          navigate(
            `/promote-review?fixture=${encodeURIComponent(resolvedPaths.fixture_path)}&anchor=${encodeURIComponent(resolvedPaths.anchor_path)}`,
          );
        }
      } catch (err) {
        if (!stopped) setError(String(err));
      }
    };
    const id = window.setInterval(tick, 1500);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [job, resolvedPaths, navigate]);

  const submit = useCallback(async () => {
    const anchor = fixtures.find((f) => f.slug === anchorSlug);
    if (!anchor) return;
    setSubmitting(true);
    setError(null);
    try {
      const resp = await api.promoteFromAnchor({
        anchor_path: anchor.audit_path,
        secondary_wav_path: secondaryWav,
        slug,
        camera_id: cameraId,
        mount,
        position,
        audio_source: audioSource,
        snap_window_ms: snapWindowMs,
        overwrite,
      });
      setJob(resp.job);
      setResolvedPaths({
        fixture_path: resp.fixture_path,
        anchor_path: resp.anchor_path,
      });
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  }, [
    anchorSlug,
    audioSource,
    cameraId,
    fixtures,
    mount,
    overwrite,
    position,
    secondaryWav,
    slug,
    snapWindowMs,
  ]);

  const running = job && (job.status === "pending" || job.status === "running");

  const fieldCls = "w-full rounded border border-input bg-background px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-ring";

  return (
    <div className="relative">
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5"
        onClick={() => setOpen((v) => !v)}
        disabled={!!running}
      >
        {running ? <Loader2 className="size-3.5 animate-spin" /> : <Link2 className="size-3.5" />}
        Promote from anchor
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-96 rounded-md border border-border bg-popover p-4 shadow-md">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-3">
            Promote from anchor
          </div>
          <div className="flex flex-col gap-2.5">
            <div>
              <div className="text-xs text-muted-foreground mb-1">Anchor fixture</div>
              <select
                className={fieldCls}
                value={anchorSlug}
                onChange={(e) => setAnchorSlug(e.target.value)}
              >
                <option value="">Pick anchor...</option>
                {fixtures.map((f) => (
                  <option key={f.slug} value={f.slug}>
                    {f.slug} ({f.n_shots} shots)
                  </option>
                ))}
              </select>
            </div>
            <div>
              <div className="text-xs text-muted-foreground mb-1">Secondary WAV path (absolute)</div>
              <input
                className={fieldCls}
                placeholder="/path/to/secondary.wav"
                value={secondaryWav}
                onChange={(e) => setSecondaryWav(e.target.value)}
              />
            </div>
            <div>
              <div className="text-xs text-muted-foreground mb-1">Target fixture slug</div>
              <input
                className={fieldCls}
                placeholder="tallmilan-2026-stage5-phone"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-xs text-muted-foreground mb-1">Camera ID</div>
                <input
                  className={fieldCls}
                  placeholder="apple-iphone17pro"
                  value={cameraId}
                  onChange={(e) => setCameraId(e.target.value)}
                />
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-1">Snap window (ms)</div>
                <input
                  className={fieldCls}
                  type="number"
                  min={10}
                  max={500}
                  value={snapWindowMs}
                  onChange={(e) => setSnapWindowMs(Number(e.target.value))}
                />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <div className="text-xs text-muted-foreground mb-1">Mount</div>
                <select className={fieldCls} value={mount} onChange={(e) => setMount(e.target.value)}>
                  {["head","chest","belt","helmet","hand","tripod","monopod","gimbal"].map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-1">Position</div>
                <select className={fieldCls} value={position} onChange={(e) => setPosition(e.target.value)}>
                  {["shooter","ro","squadmate","bay-fixed"].map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-1">Audio source</div>
                <select className={fieldCls} value={audioSource} onChange={(e) => setAudioSource(e.target.value)}>
                  {["internal","lav-wired","lav-wireless","shotgun-hotshoe"].map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
            </div>
            <label className="flex items-center gap-2 text-xs cursor-pointer text-muted-foreground">
              <input
                type="checkbox"
                checked={overwrite}
                onChange={(e) => setOverwrite(e.target.checked)}
              />
              Overwrite if slug exists
            </label>
            {running && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" />
                {job?.message ?? "running..."}
                {job?.progress != null && (
                  <span className="ml-auto font-mono">{Math.round(job.progress * 100)}%</span>
                )}
              </div>
            )}
            {error && (
              <div className="text-xs text-destructive flex gap-1.5">
                <AlertCircle className="size-3.5 mt-0.5 shrink-0" />
                {error}
              </div>
            )}
            <div className="flex gap-2 pt-1">
              <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={submit}
                disabled={submitting || !!running || !anchorSlug || !secondaryWav || !slug || !cameraId}
              >
                {submitting ? <Loader2 className="size-3.5 animate-spin mr-1" /> : null}
                Promote
              </Button>
            </div>
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

type StepFilter =
  | "borderline"
  | "rejected_only"
  | "fps_only"
  | "unlabeled_only"
  | "all";
type StepSort =
  | "ensemble_score_desc"
  | "ensemble_score_asc"
  | "vote_total_desc"
  | "confidence_desc"
  | "confidence_asc"
  | "chronological";

function StepThroughPanel({
  fixture,
  selectedCn,
  onSelect,
  registerAdvancer,
  savingLabel,
  onLabel,
}: {
  fixture: LabEvalFixture;
  selectedCn: number | null;
  onSelect: (cn: number | null) => void;
  registerAdvancer: (fn: ((cn: number) => number | null) | null) => void;
  savingLabel: number | null;
  onLabel: (
    cn: number,
    patch: { reason?: string | null; subclass?: string | null },
  ) => void;
}) {
  const [filter, setFilter] = useState<StepFilter>("borderline");
  const [classFilter, setClassFilter] = useState<string>("");
  const [sort, setSort] = useState<StepSort>("ensemble_score_desc");
  const [preMs, setPreMs] = useState(100);
  const [postMs, setPostMs] = useState(300);
  const [playing, setPlaying] = useState(true);

  const togglePlay = useCallback(() => setPlaying((p) => !p), []);

  // Auto-play whenever the candidate changes (resumes if user paused).
  useEffect(() => {
    setPlaying(true);
  }, [selectedCn]);

  // Spacebar toggles play/pause when not typing in an input.
  useEffect(() => {
    function isTyping(t: EventTarget | null): boolean {
      if (!(t instanceof HTMLElement)) return false;
      if (t.isContentEditable) return true;
      return ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key !== " ") return;
      if (isTyping(e.target)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      e.preventDefault();
      setPlaying((p) => !p);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const ordered = useMemo(() => {
    let list = [...fixture.candidates];
    if (filter === "borderline") {
      // Disagreement set: at least one voter disagrees with the consensus.
      // vote_total in {1, 2, 3} -- excludes 0 (all-reject) and 4 (all-accept).
      // These are the highest-value candidates to label for voter C training.
      list = list.filter((c) => c.vote_total >= 1 && c.vote_total <= 3);
    } else if (filter === "rejected_only") {
      list = list.filter((c) => !c.kept);
    } else if (filter === "fps_only") {
      list = list.filter((c) => c.kept && c.truth === 0);
    } else if (filter === "unlabeled_only") {
      list = list.filter((c) => {
        if (c.truth === 1) return c.subclass == null;
        return c.reason == null;
      });
    }
    // Class filter (issue: review-and-relabel by current label class).
    // Matches against either ``reason`` (for FP-style candidates) or
    // ``subclass`` (for TP/FN positives), since the user picks the
    // class they want to audit and the same string can appear on both
    // axes (e.g., a wrongly-labeled S "steel_ring" reason).
    if (classFilter) {
      list = list.filter((c) => c.reason === classFilter || c.subclass === classFilter);
    }
    list.sort((a, b) => {
      if (sort === "ensemble_score_desc") return b.ensemble_score - a.ensemble_score;
      if (sort === "ensemble_score_asc") return a.ensemble_score - b.ensemble_score;
      if (sort === "vote_total_desc") {
        if (b.vote_total !== a.vote_total) return b.vote_total - a.vote_total;
        return b.ensemble_score - a.ensemble_score;
      }
      if (sort === "confidence_desc") return b.confidence - a.confidence;
      if (sort === "confidence_asc") return a.confidence - b.confidence;
      return a.time - b.time;
    });
    return list;
  }, [fixture.candidates, filter, classFilter, sort]);

  // Register the auto-advance resolver: given the current cn, return
  // the next cn in the active filter+sort or null at the end.
  useEffect(() => {
    registerAdvancer((cn) => {
      const idx = ordered.findIndex((c) => c.candidate_number === cn);
      if (idx < 0 || idx >= ordered.length - 1) return null;
      return ordered[idx + 1].candidate_number;
    });
    return () => registerAdvancer(null);
  }, [ordered, registerAdvancer]);

  // Default selection: first item in the active list.
  useEffect(() => {
    if (ordered.length === 0) return;
    if (selectedCn == null || !ordered.some((c) => c.candidate_number === selectedCn)) {
      onSelect(ordered[0].candidate_number);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ordered]);

  const current = useMemo(
    () => ordered.find((c) => c.candidate_number === selectedCn) ?? null,
    [ordered, selectedCn],
  );

  const idxInList = current
    ? ordered.findIndex((c) => c.candidate_number === current.candidate_number)
    : -1;

  // Move to the next candidate in the active filter+sort. Used by the
  // label buttons so clicking a button advances like a keypress does.
  const advanceFromCurrent = useCallback(() => {
    if (idxInList < 0 || idxInList >= ordered.length - 1) return;
    onSelect(ordered[idxInList + 1].candidate_number);
  }, [idxInList, ordered, onSelect]);

  return (
    <div className="rounded border border-primary/40 bg-primary/5 p-3">
      <div className="mb-3 flex flex-wrap items-end gap-3 text-[11px]">
        <label className="flex flex-col gap-1">
          <span className="font-medium text-muted-foreground">Filter</span>
          <select
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value as StepFilter);
              e.currentTarget.blur();
            }}
            className="rounded border border-border bg-background px-1 py-0.5"
          >
            <option value="borderline">Borderline (1-3 votes, recommended)</option>
            <option value="rejected_only">Rejected only</option>
            <option value="fps_only">FPs only (kept negatives)</option>
            <option value="unlabeled_only">Unlabeled only</option>
            <option value="all">All candidates</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium text-muted-foreground">Class</span>
          <select
            value={classFilter}
            onChange={(e) => {
              setClassFilter(e.target.value);
              e.currentTarget.blur();
            }}
            className="rounded border border-border bg-background px-1 py-0.5"
            title="Show only candidates currently labeled with this class -- useful for reviewing a known-bad batch (e.g. mis-typed S vs Y)."
          >
            <option value="">(any)</option>
            <optgroup label="FP reason">
              {LAB_REASONS.map((r) => (
                <option key={`r-${r}`} value={r}>
                  {r}
                </option>
              ))}
            </optgroup>
            <optgroup label="TP subclass">
              {LAB_SUBCLASSES.map((s) => (
                <option key={`s-${s}`} value={s}>
                  {s}
                </option>
              ))}
            </optgroup>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium text-muted-foreground">Sort</span>
          <select
            value={sort}
            onChange={(e) => {
              setSort(e.target.value as StepSort);
              e.currentTarget.blur();
            }}
            className="rounded border border-border bg-background px-1 py-0.5"
          >
            <option value="ensemble_score_desc">
              Ensemble score desc (near-consensus first)
            </option>
            <option value="ensemble_score_asc">Ensemble score asc (least-voted first)</option>
            <option value="vote_total_desc">Vote total desc (most voters agree first)</option>
            <option value="confidence_desc">Confidence desc (loudest first)</option>
            <option value="confidence_asc">Confidence asc (quietest first)</option>
            <option value="chronological">Chronological</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium text-muted-foreground">Pre ms ({preMs})</span>
          <input
            type="range"
            min={0}
            max={2000}
            step={10}
            value={preMs}
            onChange={(e) => setPreMs(Number(e.target.value))}
            onPointerUp={(e) => e.currentTarget.blur()}
            onKeyUp={(e) => e.currentTarget.blur()}
            // Mirror the slider so dragging left grows the pre-window
            // (matches the play-window bracket that extends leftwards
            // on the waveform below).
            style={{ direction: "rtl" }}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium text-muted-foreground">Post ms ({postMs})</span>
          <input
            type="range"
            min={50}
            max={2000}
            step={10}
            value={postMs}
            onChange={(e) => setPostMs(Number(e.target.value))}
            onPointerUp={(e) => e.currentTarget.blur()}
            onKeyUp={(e) => e.currentTarget.blur()}
          />
        </label>
        <span className="ml-auto text-muted-foreground">
          {idxInList + 1} / {ordered.length}
          {ordered.length === 0 && " (no candidates match filter)"}
        </span>
      </div>

      {current ? (
        <SnippetPlayer
          fixture={fixture}
          candidate={current}
          playing={playing}
          onTogglePlay={togglePlay}
          preMs={preMs}
          postMs={postMs}
          allCandidates={fixture.candidates}
          truthTimes={fixture.truth_times}
        />
      ) : (
        <div className="rounded border border-dashed border-border/60 px-4 py-6 text-center text-xs text-muted-foreground">
          Adjust the filter or run eval to populate the candidate list.
        </div>
      )}

      {/* Compact list -- shows position in the queue + assigned labels. */}
      <div className="mt-3 max-h-60 overflow-y-auto rounded border border-border/60 bg-background/50">
        <table className="w-full text-[11px]">
          <tbody>
            {ordered.map((c) => {
              const sel = c.candidate_number === selectedCn;
              const saving = savingLabel === c.candidate_number;
              const label = c.truth === 1 ? c.subclass : c.reason;
              return (
                <tr
                  key={c.candidate_number}
                  className={cn(
                    "cursor-pointer border-b border-border/30 font-mono",
                    sel && "bg-primary/15 outline outline-1 outline-primary/60",
                    !sel && c.kept && c.truth === 1 && "bg-emerald-500/5",
                    !sel && c.kept && c.truth === 0 && "bg-orange-500/10",
                  )}
                  onClick={() => onSelect(c.candidate_number)}
                >
                  <td className="px-2 py-0.5">#{c.candidate_number}</td>
                  <td className="px-2 py-0.5 text-right text-muted-foreground">
                    {c.time.toFixed(3)}s
                  </td>
                  <td className="px-2 py-0.5 text-right text-muted-foreground">
                    score {c.ensemble_score.toFixed(2)}
                  </td>
                  <td className="px-2 py-0.5 text-right">
                    {label ? (
                      <span className="rounded bg-muted px-1">{label}</span>
                    ) : (
                      <span className="text-muted-foreground">--</span>
                    )}
                  </td>
                  <td className="w-4">
                    {saving && <Loader2 className="size-3 animate-spin text-muted-foreground" />}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {current && (
        <div className="mt-3 flex flex-wrap gap-1 text-[10px]">
          {(current.truth === 1 ? LAB_SUBCLASSES : LAB_REASONS).map((label) => (
            <button
              key={label}
              type="button"
              onClick={(e) => {
                if (current.truth === 1) {
                  onLabel(current.candidate_number, { subclass: label });
                } else {
                  onLabel(current.candidate_number, { reason: label });
                }
                advanceFromCurrent();
                e.currentTarget.blur();
              }}
              className="rounded border border-border/60 bg-background px-2 py-0.5 hover:bg-accent"
            >
              {label}
            </button>
          ))}
          <button
            type="button"
            onClick={(e) => {
              if (current.truth === 1) {
                onLabel(current.candidate_number, { subclass: null });
              } else {
                onLabel(current.candidate_number, { reason: null });
              }
              e.currentTarget.blur();
            }}
            className="rounded border border-border/60 bg-background px-2 py-0.5 text-muted-foreground hover:bg-accent"
          >
            clear
          </button>
        </div>
      )}
    </div>
  );
}

// Visible context window in the zoomed waveform: candidate is centered;
// the play window (pre/post) is highlighted within this view. If the
// play window exceeds the context, the view widens to fit.
const CONTEXT_HALF_MS = 750;

let _sharedAudioCtx: AudioContext | null = null;
function getAudioCtx(): AudioContext {
  if (!_sharedAudioCtx) {
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    _sharedAudioCtx = new Ctor();
  }
  return _sharedAudioCtx;
}

// Decoded AudioBuffers are big (~12 MB/min mono float32 at 48 kHz). The
// cache uses Map insertion-order as LRU and is capped so a long Lab
// session can't keep every fixture resident.
const AUDIO_CACHE_MAX = 3;
const _audioBufferCache = new Map<string, Promise<AudioBuffer>>();
function loadAudioBuffer(url: string): Promise<AudioBuffer> {
  const existing = _audioBufferCache.get(url);
  if (existing) {
    _audioBufferCache.delete(url);
    _audioBufferCache.set(url, existing);
    return existing;
  }
  const ctx = getAudioCtx();
  const p = fetch(url)
    .then((r) => {
      if (!r.ok) throw new Error(`audio fetch failed: ${r.status}`);
      return r.arrayBuffer();
    })
    .then((buf) => ctx.decodeAudioData(buf));
  _audioBufferCache.set(url, p);
  p.catch(() => _audioBufferCache.delete(url));
  while (_audioBufferCache.size > AUDIO_CACHE_MAX) {
    const oldest = _audioBufferCache.keys().next().value;
    if (oldest === undefined) break;
    _audioBufferCache.delete(oldest);
  }
  return p;
}

// Clear cached buffers and close the shared AudioContext so its audio
// thread + scheduling state can be reclaimed. Called on Lab unmount.
function disposeLabAudio(): void {
  _audioBufferCache.clear();
  if (_sharedAudioCtx) {
    _sharedAudioCtx.close().catch(() => {
      /* already closed */
    });
    _sharedAudioCtx = null;
  }
}

function useAudioBuffer(url: string): {
  buffer: AudioBuffer | null;
  loading: boolean;
  error: string | null;
} {
  const [state, setState] = useState<{
    buffer: AudioBuffer | null;
    loading: boolean;
    error: string | null;
  }>({ buffer: null, loading: true, error: null });
  useEffect(() => {
    let alive = true;
    setState({ buffer: null, loading: true, error: null });
    loadAudioBuffer(url)
      .then((buf) => {
        if (alive) setState({ buffer: buf, loading: false, error: null });
      })
      .catch((err) => {
        if (alive)
          setState({
            buffer: null,
            loading: false,
            error: err instanceof Error ? err.message : String(err),
          });
      });
    return () => {
      alive = false;
    };
  }, [url]);
  return state;
}

function SnippetPlayer({
  fixture,
  candidate,
  playing,
  onTogglePlay,
  preMs,
  postMs,
  allCandidates,
  truthTimes,
}: {
  fixture: LabEvalFixture;
  candidate: LabEvalFixture["candidates"][number];
  playing: boolean;
  onTogglePlay: () => void;
  preMs: number;
  postMs: number;
  allCandidates: LabEvalFixture["candidates"];
  truthTimes: number[];
}) {
  const url = api.fixtureAudioUrl(fixture.audit_path);
  const { buffer, loading, error } = useAudioBuffer(url);

  const sourceRef = useRef<AudioBufferSourceNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  // Reference points for the playhead approximation: the AudioContext
  // time at which the source last started, and the buffer offset it
  // started at. Used to compute "where in the loop are we now?" without
  // querying the source (WebAudio doesn't expose that).
  const startedAtRef = useRef<number>(0);
  const startOffsetRef = useRef<number>(0);
  const [playhead, setPlayhead] = useState<number>(candidate.time);

  const t = candidate.time;
  const safePreMs = Math.max(0, preMs);
  const safePostMs = Math.max(10, postMs);
  const loopStart = Math.max(0, t - safePreMs / 1000);
  const loopEnd = Math.min(
    buffer ? buffer.duration : t + safePostMs / 1000,
    t + safePostMs / 1000,
  );

  // Visible window: at least ±CONTEXT_HALF_MS around the candidate, but
  // expand to enclose the play window if pre/post exceed the default.
  const ctxStart = Math.max(
    0,
    Math.min(loopStart, t - CONTEXT_HALF_MS / 1000),
  );
  const ctxEnd = buffer
    ? Math.min(buffer.duration, Math.max(loopEnd, t + CONTEXT_HALF_MS / 1000))
    : Math.max(loopEnd, t + CONTEXT_HALF_MS / 1000);

  // Recreate the source on candidate change. WebAudio nodes are
  // single-use after stop(), so we always tear down + rebuild here.
  // The source always loops continuously; pause is implemented by
  // ramping gain to 0 (avoids start/stop latency on toggle).
  useEffect(() => {
    if (!buffer) return;
    const ctx = getAudioCtx();
    if (ctx.state === "suspended") {
      ctx.resume().catch(() => {
        /* requires a user gesture -- the play button click counts. */
      });
    }
    const gain = ctx.createGain();
    gain.gain.value = playing ? 1.0 : 0.0;
    gain.connect(ctx.destination);
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.loop = true;
    src.loopStart = loopStart;
    src.loopEnd = loopEnd;
    src.connect(gain);
    src.start(0, loopStart);
    sourceRef.current = src;
    gainRef.current = gain;
    startedAtRef.current = ctx.currentTime;
    startOffsetRef.current = loopStart;

    return () => {
      try {
        src.stop();
      } catch {
        /* already stopped */
      }
      src.disconnect();
      gain.disconnect();
      if (sourceRef.current === src) sourceRef.current = null;
      if (gainRef.current === gain) gainRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buffer, candidate.candidate_number]);

  // Live-update the loop window when sliders move; no source restart.
  useEffect(() => {
    const src = sourceRef.current;
    if (!src) return;
    src.loopStart = loopStart;
    src.loopEnd = loopEnd;
  }, [loopStart, loopEnd]);

  // Mute / unmute via gain ramp (no clicks on toggle).
  useEffect(() => {
    const gain = gainRef.current;
    if (!gain) return;
    const ctx = getAudioCtx();
    const target = playing ? 1.0 : 0.0;
    gain.gain.cancelScheduledValues(ctx.currentTime);
    gain.gain.setValueAtTime(gain.gain.value, ctx.currentTime);
    gain.gain.linearRampToValueAtTime(target, ctx.currentTime + 0.015);
  }, [playing]);

  // Playhead. WebAudio's AudioBufferSourceNode doesn't expose its
  // internal position, so we approximate it from elapsed AudioContext
  // time. After a slider drag the bracket moves but the underlying
  // source phase stays continuous, so the line may briefly fall out
  // of sync for one cycle -- it re-aligns on the next loop wrap.
  useEffect(() => {
    if (!buffer || !playing) return;
    let raf = 0;
    const tick = () => {
      const ctx = getAudioCtx();
      const span = Math.max(0.001, loopEnd - loopStart);
      const elapsed = ctx.currentTime - startedAtRef.current;
      const phase = ((elapsed % span) + span) % span;
      setPlayhead(loopStart + phase);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [buffer, loopStart, loopEnd, playing]);

  // Markers for the zoomed view. We show every audited truth in the
  // window so the user can see where their audit landed relative to
  // the detected candidate -- the two often differ by a few ms (the
  // detector marks the rise foot, which can land in a small precursor
  // bump just before the loud transient). Matched truths render dashed
  // and translucent so they don't fight the candidate line; unmatched
  // truths (FNs) stay solid red.
  const otherCandidates = useMemo(
    () =>
      allCandidates.filter(
        (c) =>
          c.candidate_number !== candidate.candidate_number &&
          c.time >= ctxStart &&
          c.time <= ctxEnd,
      ),
    [allCandidates, candidate.candidate_number, ctxStart, ctxEnd],
  );
  const truthsInWindow = useMemo(() => {
    const tolMs = 75; // matches lab _label_truth tolerance
    return truthTimes
      .filter((tt) => tt >= ctxStart && tt <= ctxEnd)
      .map((tt) => ({
        time: tt,
        matched: allCandidates.some(
          (c) => c.kept && c.truth === 1 && Math.abs(c.time - tt) * 1000 <= tolMs,
        ),
      }));
  }, [truthTimes, allCandidates, ctxStart, ctxEnd]);

  const labelText =
    candidate.truth === 1 ? candidate.subclass : candidate.reason;

  // For TP / FN candidates, surface the nearest audit shot time and
  // the offset from the candidate -- helps decide "is this candidate
  // really the audited shot, or did we match the wrong onset?"
  const nearestTruth = useMemo(() => {
    if (truthTimes.length === 0) return null;
    let best: { time: number; deltaMs: number } | null = null;
    for (const tt of truthTimes) {
      const deltaMs = (tt - t) * 1000;
      if (Math.abs(deltaMs) > 200) continue; // off-screen / unrelated
      if (best === null || Math.abs(deltaMs) < Math.abs(best.deltaMs)) {
        best = { time: tt, deltaMs };
      }
    }
    return best;
  }, [truthTimes, t]);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={(e) => {
              onTogglePlay();
              e.currentTarget.blur();
            }}
            className="flex size-7 items-center justify-center rounded-full bg-primary text-primary-foreground hover:bg-primary/90"
            title={playing ? "Pause (space)" : "Play (space)"}
            aria-label={playing ? "Pause" : "Play"}
          >
            {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
          </button>
          <span className="font-mono">#{candidate.candidate_number}</span>
          <span className="text-muted-foreground">
            t={candidate.time.toFixed(3)}s · conf {candidate.confidence.toFixed(3)} · score{" "}
            {candidate.ensemble_score.toFixed(2)}
          </span>
          <VoterChips candidate={candidate} />
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "rounded px-2 py-0.5 font-mono text-[10px]",
              outcomeColor(candidate),
            )}
          >
            {outcomeLabel(candidate)}
          </span>
          {labelText && (
            <span className="rounded bg-muted px-2 py-0.5 font-mono text-[10px]">{labelText}</span>
          )}
        </div>
      </div>
      {error ? (
        <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          Failed to load audio: {error}
        </div>
      ) : loading || !buffer ? (
        <div className="flex h-[120px] items-center justify-center rounded border border-border/40 bg-muted/30 text-xs text-muted-foreground">
          <Loader2 className="mr-2 size-4 animate-spin" /> loading audio buffer...
        </div>
      ) : (
        <ZoomedWaveform
          buffer={buffer}
          windowStart={ctxStart}
          windowEnd={ctxEnd}
          playStart={loopStart}
          playEnd={loopEnd}
          candidateTime={t}
          candidateColor={candidateLineColor(candidate)}
          playhead={playing ? playhead : null}
          otherCandidates={otherCandidates}
          truths={truthsInWindow}
        />
      )}
      <div className="text-[10px] text-muted-foreground">
        Visible window: {((ctxEnd - ctxStart) * 1000).toFixed(0)} ms · play window:{" "}
        {(safePreMs + safePostMs).toFixed(0)} ms ({safePreMs.toFixed(0)} pre /{" "}
        {safePostMs.toFixed(0)} post){playing ? " · looping" : " · paused"}
        {nearestTruth != null && (
          <>
            {" · "}
            <span className="font-mono">
              audit at {nearestTruth.time.toFixed(3)}s ({nearestTruth.deltaMs >= 0 ? "+" : ""}
              {nearestTruth.deltaMs.toFixed(0)} ms from candidate)
            </span>
          </>
        )}
      </div>
    </div>
  );
}

function VoterChips({
  candidate,
}: {
  candidate: LabEvalFixture["candidates"][number];
}) {
  const items: { key: string; label: string; on: boolean }[] = [
    { key: "a", label: "A", on: candidate.vote_a === 1 },
    { key: "b", label: "B", on: candidate.vote_b === 1 },
    { key: "c", label: "C", on: candidate.vote_c === 1 },
  ];
  return (
    <div className="flex gap-0.5">
      {items.map((it) => (
        <span
          key={it.key}
          className={cn(
            "rounded px-1 font-mono text-[9px]",
            it.on
              ? "bg-emerald-500/20 text-emerald-700 dark:text-emerald-300"
              : "bg-muted text-muted-foreground",
          )}
          title={`Voter ${it.label}: ${it.on ? "yes" : "no"}`}
        >
          {it.label}
        </span>
      ))}
    </div>
  );
}

// Centralized lab outcome palette. Uses the project's Okabe-Ito-derived
// design tokens (defined in styles/index.css) so the same TP/FP/FN
// colours are used everywhere and the palette stays color-blind safe.
const LAB_PALETTE = {
  tp: "var(--color-split-good)", // Okabe-Ito bluish green
  fp: "var(--color-split-slow)", // Okabe-Ito vermillion
  fn: "var(--color-destructive)", // shadcn destructive
  rejected: "var(--color-marker-rejected)", // neutral gray
  candidatePrimary: "var(--color-marker-detected)", // Okabe-Ito blue
  playhead: "var(--color-waveform-playhead)", // Okabe-Ito vermillion
  playWindow: "var(--color-primary)", // shadcn primary (theme-tracking)
} as const;

function candidateLineColor(c: LabEvalFixture["candidates"][number]): string {
  if (c.kept && c.truth === 1) return LAB_PALETTE.tp;
  if (c.kept && c.truth === 0) return LAB_PALETTE.fp;
  if (!c.kept && c.truth === 1) return LAB_PALETTE.fn;
  return LAB_PALETTE.candidatePrimary;
}

function otherCandidateColor(c: LabEvalFixture["candidates"][number]): string {
  if (c.kept && c.truth === 1) return LAB_PALETTE.tp;
  if (c.kept && c.truth === 0) return LAB_PALETTE.fp;
  return LAB_PALETTE.rejected;
}

function ZoomedWaveform({
  buffer,
  windowStart,
  windowEnd,
  playStart,
  playEnd,
  candidateTime,
  candidateColor,
  playhead,
  otherCandidates,
  truths,
  height = 120,
}: {
  buffer: AudioBuffer;
  windowStart: number;
  windowEnd: number;
  playStart: number;
  playEnd: number;
  candidateTime: number;
  candidateColor: string;
  playhead: number | null;
  otherCandidates: LabEvalFixture["candidates"];
  truths: { time: number; matched: boolean }[];
  height?: number;
}) {
  // Bin into 600 vertical strips; one peak per strip. Cheap to recompute
  // on slider drag because the windowed sample range is tiny (~50k samples
  // for a 1.5s window at 48kHz).
  const BINS = 600;
  const peaks = useMemo(() => {
    const sr = buffer.sampleRate;
    const startIdx = Math.max(0, Math.floor(windowStart * sr));
    const endIdx = Math.min(buffer.length, Math.ceil(windowEnd * sr));
    const ch = buffer.getChannelData(0);
    const span = Math.max(1, endIdx - startIdx);
    const out = new Float32Array(BINS);
    for (let i = 0; i < BINS; i++) {
      const s = startIdx + Math.floor((i * span) / BINS);
      const e = Math.max(s + 1, startIdx + Math.floor(((i + 1) * span) / BINS));
      let max = 0;
      for (let j = s; j < Math.min(endIdx, e); j++) {
        const v = Math.abs(ch[j]);
        if (v > max) max = v;
      }
      out[i] = max;
    }
    return out;
  }, [buffer, windowStart, windowEnd]);

  const VIEW_W = 1000; // SVG view-box width; CSS scales to container
  const span = windowEnd - windowStart;
  const xFor = (t: number) => ((t - windowStart) / span) * VIEW_W;
  const playX1 = xFor(playStart);
  const playX2 = xFor(playEnd);

  return (
    <div className="rounded border border-border/60 bg-background">
      <svg
        viewBox={`0 0 ${VIEW_W} ${height}`}
        preserveAspectRatio="none"
        className="block w-full"
        style={{ height }}
      >
        {/* Play-window highlight + edges (theme primary). */}
        <rect
          x={playX1}
          y={0}
          width={Math.max(1, playX2 - playX1)}
          height={height}
          fill={LAB_PALETTE.playWindow}
          fillOpacity={0.1}
        />
        <line
          x1={playX1}
          x2={playX1}
          y1={0}
          y2={height}
          stroke={LAB_PALETTE.playWindow}
          strokeWidth={1}
          strokeDasharray="3 3"
          strokeOpacity={0.7}
        />
        <line
          x1={playX2}
          x2={playX2}
          y1={0}
          y2={height}
          stroke={LAB_PALETTE.playWindow}
          strokeWidth={1}
          strokeDasharray="3 3"
          strokeOpacity={0.7}
        />

        {/* Peaks: vertical bars centered on midline */}
        <g fill="currentColor" opacity={0.55}>
          {Array.from(peaks).map((p, i) => {
            const h = Math.max(0.5, p * (height * 0.85));
            const cx = (i + 0.5) * (VIEW_W / BINS);
            const w = Math.max(0.6, VIEW_W / BINS - 0.3);
            return (
              <rect
                key={i}
                x={cx - w / 2}
                y={(height - h) / 2}
                width={w}
                height={h}
              />
            );
          })}
        </g>

        {/* Truth (audit) reference lines, always dashed so they read
            as "audit point" rather than "the candidate". Colour
            encodes whether a kept TP candidate matched this truth:
            green = matched, red = unmatched (FN). */}
        {truths.map(({ time: tt, matched }, i) => (
          <line
            key={`tr-${i}`}
            x1={xFor(tt)}
            x2={xFor(tt)}
            y1={0}
            y2={height}
            stroke={matched ? LAB_PALETTE.tp : LAB_PALETTE.fn}
            strokeOpacity={matched ? 0.55 : 0.75}
            strokeWidth={1}
            strokeDasharray="4 3"
          />
        ))}

        {/* Other candidates in window. Outcome-coloured dots so the
            visual encoding is consistent with the candidate line. */}
        {otherCandidates.map((c) => {
          const cx = xFor(c.time);
          const color = otherCandidateColor(c);
          return (
            <g key={`oc-${c.candidate_number}`}>
              <circle cx={cx} cy={height - 4} r={2.5} fill={color} fillOpacity={0.85} />
              <line
                x1={cx}
                x2={cx}
                y1={height - 12}
                y2={height}
                stroke={color}
                strokeOpacity={0.45}
                strokeWidth={1}
              />
            </g>
          );
        })}

        {/* Candidate center -- coloured by outcome */}
        <line
          x1={xFor(candidateTime)}
          x2={xFor(candidateTime)}
          y1={0}
          y2={height}
          stroke={candidateColor}
          strokeWidth={2}
        />

        {/* Playhead -- only shown while playing. Positioned in the
            visible window if the loop falls inside it (almost always
            true since the play window is enclosed by the visible
            window). */}
        {playhead != null && playhead >= windowStart && playhead <= windowEnd && (
          <line
            x1={xFor(playhead)}
            x2={xFor(playhead)}
            y1={0}
            y2={height}
            stroke={LAB_PALETTE.playhead}
            strokeWidth={1.5}
            strokeOpacity={0.85}
          />
        )}

        {/* Time-axis labels at the visible-window edges */}
        <text
          x={4}
          y={11}
          fontSize={9}
          fill="currentColor"
          opacity={0.55}
          fontFamily="ui-monospace, monospace"
        >
          {windowStart.toFixed(2)}s
        </text>
        <text
          x={VIEW_W - 4}
          y={11}
          fontSize={9}
          fill="currentColor"
          opacity={0.55}
          fontFamily="ui-monospace, monospace"
          textAnchor="end"
        >
          {windowEnd.toFixed(2)}s
        </text>
      </svg>
    </div>
  );
}

function outcomeLabel(c: LabEvalFixture["candidates"][number]): string {
  if (c.kept && c.truth === 1) return "TP";
  if (c.kept && c.truth === 0) return "FP";
  if (!c.kept && c.truth === 1) return "FN";
  return "TN";
}
function outcomeColor(c: LabEvalFixture["candidates"][number]): string {
  if (c.kept && c.truth === 1) return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
  if (c.kept && c.truth === 0) return "bg-orange-500/20 text-orange-700 dark:text-orange-300";
  if (!c.kept && c.truth === 1) return "bg-red-500/20 text-red-700 dark:text-red-300";
  return "bg-muted text-muted-foreground";
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
        <span><kbd>Space</kbd> play / pause</span>
        <span><kbd>0</kbd> / <kbd>Bksp</kbd> clear</span>
        <span><kbd>X</kbd> cross_bay</span>
        <span><kbd>E</kbd> echo</span>
        <span><kbd>B</kbd> barrel_echo / barrel</span>
        <span><kbd>W</kbd> wind</span>
        <span><kbd>M</kbd> movement</span>
        <span><kbd>S</kbd> steel_ring / steel</span>
        <span><kbd>H</kbd> handling</span>
        <span><kbd>A</kbd> agc_artifact</span>
        <span><kbd>V</kbd> speech (Voice)</span>
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
