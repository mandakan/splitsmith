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
  ChevronRight,
  Hammer,
  Headphones,
  Link2,
  Loader2,
  Pencil,
  Play,
  RotateCcw,
  Save,
  Settings2,
  Trash2,
} from "lucide-react";

import { SweepsCard } from "@/components/SweepsCard";
import { useConfirm } from "@/components/useConfirm";
import { Waveform } from "@/components/Waveform";
import { disposeLabAudio } from "@/components/lab/labAudio";
import { LAB_PALETTE, fmtPct } from "@/components/lab/labPalette";
import { CandidateTable } from "@/components/lab/CandidateTable";
import { DiffList } from "@/components/lab/DiffList";
import { KeyboardLegend } from "@/components/lab/KeyboardLegend";
import { LabelBreakdown } from "@/components/lab/LabelBreakdown";
import { LabelProgress } from "@/components/lab/LabelProgress";
import { REASON_SHORTCUTS, SUBCLASS_SHORTCUTS } from "@/components/lab/labels";
import { Pin } from "@/components/lab/Pin";
import { PromoteFromAnchorPanel } from "@/components/lab/PromoteFromAnchorPanel";
import { PromoteStagesPanel } from "@/components/lab/PromoteStagesPanel";
import { StepThroughPanel } from "@/components/lab/StepThroughPanel";
import { DEFAULT_CONFIG } from "@/components/lab/useLabRun";
import { VoterRecallTable } from "@/components/lab/VoterRecallTable";
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
  type Job,
  type LabEvalConfig,
  type LabEvalFixture,
  type LabEvalRun,
  type LabFixtureRecord,
  type PeaksResult,
  type StageAudit,
} from "@/lib/api";
import { cn } from "@/lib/utils";

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
  // Optional ``slugs`` scopes the eval to a fixture subset -- the fast
  // path the pre-eval drawer uses so labeling one fixture doesn't cost
  // a full-corpus run. Guarded with Array.isArray because two call
  // sites pass this directly as an onClick handler (the arg is then a
  // MouseEvent, which must not leak into the request body).
  const runEval = useCallback(async (slugs?: unknown): Promise<void> => {
    if (inFlightEvalRef.current) return inFlightEvalRef.current;
    const wanted = Array.isArray(slugs) ? (slugs as string[]) : undefined;
    const p = (async () => {
      setEvalLoading(true);
      setError(null);
      try {
        const job = await api.runLabEval({ slugs: wanted, config, persist: true });
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

  // The detail drawer renders below the fixture table, which at corpus
  // size is thousands of pixels tall -- without an explicit scroll a
  // row click looks like a no-op. Scroll when the selected slug
  // changes; not when the drawer merely swaps Lite -> full after an
  // eval, since the operator is already looking at it then.
  const detailRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!slug) return;
    detailRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [slug]);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <Beaker className="size-5 text-led" />
            Algorithm Lab
          </h1>
          <p className="text-sm text-muted">
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
          <PromoteStagesPanel
            catalog={catalog}
            onCatalogChanged={(next) => setCatalog(next)}
          />
          <PromoteFromAnchorPanel fixtures={catalog} />
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

      {slug ? (
        <div ref={detailRef} className="scroll-mt-24">
          {focused ? (
            <FixtureDetail
              fixture={focused}
              onClose={() => navigate("/dev/legacy/lab", { replace: true })}
              onLabelChanged={(updated) => {
                if (updated) setRun(updated);
                else runEval();
              }}
            />
          ) : (
            <FixtureDetailLite
              record={catalog.find((r) => r.slug === slug) ?? null}
              onClose={() => navigate("/dev/legacy/lab", { replace: true })}
              onRunEvalScoped={(slugs) => void runEval(slugs)}
              evalLoading={evalLoading}
            />
          )}
        </div>
      ) : null}

      <SweepsCard />
    </div>
  );
}

function FixtureDetailLite({
  record,
  onClose,
  onRunEvalScoped,
  evalLoading,
}: {
  record: LabFixtureRecord | null;
  onClose: () => void;
  onRunEvalScoped: (slugs: string[]) => void;
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
        <CardContent className="py-4 text-sm text-muted">
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
              title="Edit ground-truth markers (add / move / delete shots) in the review editor"
            >
              <Pencil className="size-3.5" />
              Edit markers
            </Link>
          </Button>
          <Button
            size="sm"
            onClick={() => onRunEvalScoped([record.slug])}
            disabled={evalLoading}
          >
            {evalLoading ? <Loader2 className="size-3.5 animate-spin" /> : <Play className="size-3.5" />}
            Eval this fixture
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
        <div className="flex gap-1.5 rounded border border-[rgba(251,191,36,0.4)] bg-[color:var(--color-live-tint)] px-3 py-2 text-xs text-live">
          <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
          <span>
            Candidate labeling needs an eval run first -- labels (paper /
            steel / cross_bay / echo ...) attach to detection candidates,
            which only exist after eval. <b>Eval this fixture</b> above runs
            it for just this fixture in seconds; the header&apos;s Run eval
            covers the whole corpus and takes minutes. The eval cache is
            per-server-session, so a restart brings you back here.
          </span>
        </div>
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
          <div className="flex h-[140px] items-center justify-center rounded border border-rule/40 bg-muted/30 text-xs text-muted">
            <Loader2 className="mr-2 size-4 animate-spin" /> loading waveform...
          </div>
        )}
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
          {rescoreLoading && <Loader2 className="size-4 animate-spin text-muted" />}
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
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
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
          <details className="rounded border border-rule/60 bg-muted/30 px-3 py-2 text-xs">
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
      <div className="mb-1 font-medium text-ink">{label}</div>
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
        <span className="font-mono text-[10px] text-muted">
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
            className="text-[10px] text-muted hover:text-ink"
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
              <tr className="border-b border-rule/60 text-[11px] uppercase tracking-wide text-muted">
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
                      className="bg-muted/30 text-[10px] uppercase tracking-wide text-muted"
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
        "cursor-pointer border-b border-rule/40 hover:bg-muted/40",
        active && "bg-surface-3/40",
      )}
      onClick={() => onSelect(active ? null : rec.slug)}
    >
      <td
        className={cn(
          "px-4 py-2 font-mono text-xs",
          isSibling && "pl-8 text-muted",
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
      <td className="px-2 py-2 text-muted">
        <div className="flex items-center justify-end gap-1">
          {rec.anchor_slug && (
            <Link
              to={promoteReviewUrl(rec.audit_path, rec.anchor_slug)}
              onClick={(e) => e.stopPropagation()}
              className="rounded p-1 text-muted hover:bg-surface-3 hover:text-ink"
              title="Re-open the secondary diff-confirm review"
              aria-label={`Re-review promotion ${rec.slug}`}
            >
              <Link2 className="size-3.5" />
            </Link>
          )}
          <Link
            to={reviewUrl(rec.audit_path, rec.source_video)}
            onClick={(e) => e.stopPropagation()}
            className="rounded p-1 text-muted hover:bg-surface-3 hover:text-ink"
            title="Edit ground-truth markers in the review editor (candidate labeling lives in the row's detail drawer, after an eval)"
            aria-label={`Edit markers for ${rec.slug}`}
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
              className="rounded p-1 text-muted hover:bg-destructive/10 hover:text-destructive"
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
              title="Edit ground-truth markers (add / move / delete shots, edit beep) in the review editor"
            >
              <Pencil className="size-3.5" />
              Edit markers
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
          <div className="flex h-[140px] items-center justify-center rounded border border-rule/40 bg-muted/30 text-xs text-muted">
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
            <span className="text-[11px] text-muted">
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
        <div className="absolute right-0 top-full z-20 mt-1 w-80 rounded-md border border-rule bg-surface-2 p-3 shadow-md">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted">
            Save tuning
          </div>
          <p className="mt-1 text-[11px] text-muted">
            Writes <span className="font-mono">configs/ensemble.&lt;name&gt;.yaml</span> with the active
            config + summary + provenance. Replayable via <span className="font-mono">splitsmith lab load-config</span>.
          </p>
          <label className="mt-2 block text-[11px]">
            <span className="text-muted">Name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="mt-1 w-full rounded border border-rule bg-bg px-2 py-1 font-mono text-xs"
              placeholder="tighter-d"
            />
          </label>
          <label className="mt-2 block text-[11px]">
            <span className="text-muted">Note (optional)</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              className="mt-1 w-full rounded border border-rule bg-bg px-2 py-1 text-xs"
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
        <div className="absolute right-0 top-full z-20 mt-1 w-80 rounded-md border border-rule bg-surface-2 p-3 shadow-md">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted">
            Rebuild calibration
          </div>
          <p className="mt-1 text-[11px] text-muted">
            Refits voter thresholds + the GBDT against every audited fixture and overwrites
            <span className="font-mono"> src/splitsmith/data/</span>. Slow (model-bound). After it
            completes, the next eval picks up the new thresholds.
          </p>
          <p className="mt-1 text-[11px] text-muted">
            Requires the CLAP / PANN feature caches under
            <span className="font-mono"> tests/fixtures/.cache/</span> -- build them first via the
            extract scripts if a new fixture's cache is missing.
          </p>
          <label className="mt-2 block text-[11px]">
            <span className="text-muted">Target recall ({targetRecall.toFixed(2)})</span>
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
            <span className="text-muted">Tolerance ms ({toleranceMs.toFixed(0)})</span>
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
                {job.message && <span className="text-muted">{job.message}</span>}
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

