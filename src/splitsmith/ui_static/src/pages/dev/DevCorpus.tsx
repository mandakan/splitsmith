/**
 * Developer / Corpus (#331).
 *
 * Lists every fixture currently on disk plus a workflow-status banner
 * across the 4 dev-mode steps. Reuses /api/lab/fixtures (always
 * available; just reads the audit-fixtures dir) plus /api/dev/model.
 *
 * Replaces the corpus-browsing slice of legacy Lab.tsx.
 */

import { ArrowRight, FlaskConical, Inbox, Loader2, Search, Slash } from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useOutletContext } from "react-router-dom";

import { PromoteFromAnchorPanel } from "@/components/lab/PromoteFromAnchorPanel";
import { PromoteStagesPanel } from "@/components/lab/PromoteStagesPanel";
import { useLabRun } from "@/components/lab/useLabRun";
import {
  FILTER_DEFS,
  filterFixtures,
  isPromoted,
  needsReview,
  type FilterKey,
} from "@/components/lab/corpusFilter";
import { api, type DevReviewQueueItem, type LabFixtureRecord } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { DeveloperShellOutletContext } from "@/components/developer/DeveloperShell";

export function DevCorpus() {
  const { model } = useOutletContext<DeveloperShellOutletContext>();
  const navigate = useNavigate();
  // Carry the dev-mode match context (?match=) into the fixture detail
  // page, the same way DeveloperShell carries it across the stepper.
  const { search } = useLocation();

  const [fixtures, setFixtures] = useState<LabFixtureRecord[]>([]);
  const [queue, setQueue] = useState<DevReviewQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");

  useEffect(() => {
    let alive = true;
    Promise.all([api.listLabFixtures().catch(() => []), api.getDevReviewQueue().catch(() => null)])
      .then(([fx, q]) => {
        if (!alive) return;
        setFixtures(fx);
        setQueue([...(q?.pending ?? []), ...(q?.flagged ?? [])]);
        setLoading(false);
      })
      .catch(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const filtered = useMemo(
    () => filterFixtures(fixtures, query, filter),
    [fixtures, query, filter],
  );

  // Subset eval from the filter bar (#941): run /api/lab/eval over
  // exactly what the operator is looking at. autoRescore off -- this
  // page has no tuning sliders; the cached-run adoption + hydration
  // gate is what we want (submitting DEFAULT_CONFIG before hydration
  // settles would replace a tuned universe under a different hash).
  const {
    run: evalRun,
    runEval,
    evalLoading,
    error: evalError,
    hydrated: evalHydrated,
  } = useLabRun({ autoRescore: false });
  // Slugs of the last eval launched FROM THIS PAGE -- the summary
  // strip aggregates over these, not over whatever else the merged
  // run happens to cover.
  const [evaledSlugs, setEvaledSlugs] = useState<string[] | null>(null);
  const evalTargets = useMemo(
    () => filtered.filter((f) => f.has_audio).map((f) => f.slug),
    [filtered],
  );
  // Unscoped run when nothing narrows the view: that is the canonical
  // full-corpus eval, same as Validate's Run button submits.
  const wholeCorpus = filter === "all" && !query.trim();

  async function onEval() {
    setEvaledSlugs(evalTargets);
    await runEval(wholeCorpus ? undefined : evalTargets);
  }

  const evalSummary = useMemo(() => {
    if (!evalRun || !evaledSlugs || evalLoading) return null;
    const wanted = new Set(evaledSlugs);
    const covered = evalRun.universe.fixtures.filter((f) => wanted.has(f.slug));
    if (covered.length === 0) return null;
    const tp = covered.reduce((a, f) => a + f.metrics.true_positives, 0);
    const kept = covered.reduce((a, f) => a + f.metrics.n_kept, 0);
    const truth = covered.reduce((a, f) => a + f.metrics.n_truth, 0);
    const precision = kept ? tp / kept : 0;
    const recall = truth ? tp / truth : 0;
    const f1 = precision + recall ? (2 * precision * recall) / (precision + recall) : 0;
    return { n: covered.length, precision, recall, f1 };
  }, [evalRun, evaledSlugs, evalLoading]);

  const pendingCount = queue.length;

  const matchContext = new URLSearchParams(search).get("match");
  // Row links carry the active search/filter alongside the match
  // context, so the detail page's prev/next walk the subset the
  // operator is looking at, not the whole corpus (#898).
  const detailSearch = useMemo(() => {
    const params = new URLSearchParams();
    if (matchContext) params.set("match", matchContext);
    if (query.trim()) params.set("q", query.trim());
    if (filter !== "all") params.set("filter", filter);
    const s = params.toString();
    return s ? `?${s}` : "";
  }, [matchContext, query, filter]);

  return (
    <div className="min-w-0 px-7 py-7">
      {/* Page head */}
      <header className="mb-6 flex items-end gap-7">
        <div className="flex-1">
          <div className="mb-2 flex items-center gap-2.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.18em] text-beep">
            <span aria-hidden className="h-px w-6 bg-beep" />
            Step 01 / Corpus
          </div>
          <h1 className="font-display text-[2rem] font-bold uppercase leading-none tracking-tight text-ink">
            Audited fixtures
          </h1>
          <p className="mt-2 max-w-xl text-[0.875rem] text-muted">
            The corpus that calibrates and trains the ensemble. Every fixture here is JSON +
            sibling WAV; the calibration script reads this directory directly.
          </p>
        </div>
        <div className="flex items-center gap-3 font-mono text-[0.75rem] tabular-nums">
          <span className="text-muted">Active model</span>
          <b className="font-bold text-ink">{model?.active_version ?? "--"}</b>
          <span className="text-whisper">/</span>
          <span className="text-muted">recall</span>
          <b className="font-bold text-done">{model ? model.recall.toFixed(2) : "--"}</b>
        </div>
      </header>

      {/* Promote entry points -- full-width expandable sections: this
          page has the room and the corpus table below is the thing
          they populate. */}
      <div className="mb-6 flex flex-wrap items-start gap-2.5">
        <span className="mr-1 flex h-8 items-center font-mono text-[0.625rem] font-bold uppercase tracking-[0.18em] text-muted">
          Promote
        </span>
        <PromoteStagesPanel catalog={fixtures} onCatalogChanged={setFixtures} />
        <PromoteFromAnchorPanel fixtures={fixtures} />
      </div>

      {/* Workflow status banner */}
      <WorkflowBanner pendingReview={pendingCount} corpusSize={fixtures.length} model={model} />

      {/* Inbox card */}
      {pendingCount > 0 && (
        <InboxCard
          items={queue.slice(0, 4)}
          remaining={Math.max(0, queue.length - 4)}
          onOpen={() => navigate("/dev/review")}
        />
      )}

      {/* Toolbar */}
      <div className="mb-3 flex items-center gap-3">
        <div className="relative flex flex-1 items-center">
          <Search className="absolute left-3 size-4 text-muted" />
          <input
            type="search"
            placeholder="Search fixtures..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="h-10 w-full rounded-md border border-rule bg-surface pl-10 pr-12 text-[0.875rem] text-ink placeholder:text-muted focus-visible:border-beep"
          />
          <kbd className="absolute right-3 inline-flex items-center gap-0.5 rounded border border-rule-strong bg-surface-2 px-1.5 py-0.5 font-mono text-[0.6875rem] text-ink-2">
            <Slash className="size-2.5" />
          </kbd>
        </div>
        <div className="flex items-center gap-1.5">
          {FILTER_DEFS.map((f) => {
            const active = filter === f.key;
            return (
              <button
                key={f.key}
                type="button"
                onClick={() => setFilter(f.key)}
                className={cn(
                  "h-8 rounded-full border px-3 font-mono text-[0.6875rem] font-medium uppercase tracking-[0.06em] transition-colors",
                  active
                    ? "border-[rgba(6,182,212,0.4)] bg-[color:var(--color-beep-tint)] text-beep"
                    : "border-rule bg-surface text-muted hover:text-ink",
                )}
              >
                <span className="text-whisper">filter:</span> {f.label}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          onClick={() => void onEval()}
          disabled={!evalHydrated || evalLoading || evalTargets.length === 0}
          title={
            wholeCorpus
              ? "Run the ensemble eval over the whole corpus (same as Validate's Run)"
              : "Run the ensemble eval over the fixtures the current filter shows"
          }
          className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-[rgba(6,182,212,0.4)] bg-[color:var(--color-beep-tint)] px-3 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.06em] text-beep transition-colors hover:bg-[rgba(6,182,212,0.18)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {evalLoading ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <FlaskConical className="size-3.5" />
          )}
          {evalLoading
            ? "Running..."
            : wholeCorpus
              ? `Eval corpus (${evalTargets.length})`
              : `Eval these ${evalTargets.length}`}
        </button>
      </div>

      {/* Subset-eval result strip: aggregates over the fixtures the
          last eval launched here actually covered. */}
      {evalError && (
        <div className="mb-3 rounded-md border border-[rgba(255,45,45,0.4)] bg-destructive/10 px-4 py-2 font-mono text-[0.75rem] text-destructive">
          {evalError}
        </div>
      )}
      {evalSummary && (
        <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-1 rounded-md border border-rule bg-surface px-4 py-2 font-mono text-[0.75rem] tabular-nums">
          <span className="font-bold uppercase tracking-[0.08em] text-muted">
            Eval &middot; {evalSummary.n} fixture{evalSummary.n === 1 ? "" : "s"}
          </span>
          <span>
            <span className="text-muted">precision</span>{" "}
            <b className="text-ink">{(evalSummary.precision * 100).toFixed(1)}%</b>
          </span>
          <span>
            <span className="text-muted">recall</span>{" "}
            <b className="text-ink">{(evalSummary.recall * 100).toFixed(1)}%</b>
          </span>
          <span>
            <span className="text-muted">F1</span>{" "}
            <b className="text-ink">{evalSummary.f1.toFixed(3)}</b>
          </span>
          <button
            type="button"
            onClick={() =>
              navigate(`/dev/validate${matchContext ? `?match=${encodeURIComponent(matchContext)}` : ""}`)
            }
            className="ml-auto inline-flex items-center gap-1 text-beep hover:underline"
          >
            Open in Validate <ArrowRight className="size-3" />
          </button>
        </div>
      )}

      {/* Fixtures table */}
      <section className="overflow-hidden rounded-md border border-rule bg-surface">
        <div className="flex items-center justify-between border-b border-rule px-4 py-3">
          <div className="flex items-center gap-3">
            <h2 className="font-display text-[0.9375rem] font-bold uppercase tracking-tight text-ink">
              Fixtures
            </h2>
            <span className="font-mono text-[0.6875rem] tabular-nums text-muted">
              {filtered.length} / {fixtures.length}
            </span>
          </div>
        </div>
        <div className="grid grid-cols-[1fr_140px_70px_70px_120px_28px] items-center gap-3 border-b border-rule bg-surface-2 px-4 py-2 font-mono text-[0.625rem] font-bold uppercase tracking-[0.12em] text-subtle">
          <span>Fixture</span>
          <span>Source</span>
          <span className="text-right">Shots</span>
          <span className="text-right">Audio</span>
          <span>Tags</span>
          <span />
        </div>
        {loading ? (
          <div className="px-4 py-10 text-center text-[0.875rem] text-muted">Loading...</div>
        ) : filtered.length === 0 ? (
          <div className="px-4 py-10 text-center text-[0.875rem] text-muted">
            No fixtures match these filters.
          </div>
        ) : (
          <ul>
            {filtered.map((fx) => (
              <FixtureRow
                key={fx.slug}
                fx={fx}
                onOpen={() => navigate(`/dev/corpus/${fx.slug}${detailSearch}`)}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function WorkflowBanner({
  pendingReview,
  corpusSize,
  model,
}: {
  pendingReview: number;
  corpusSize: number;
  model: DeveloperShellOutletContext["model"];
}) {
  const steps = [
    {
      label: "Corpus",
      value: corpusSize.toString().padStart(2, "0"),
      hint: "fixtures audited",
      state: "ok" as const,
    },
    {
      label: "Review queue",
      value: pendingReview.toString().padStart(2, "0"),
      hint: pendingReview > 0 ? "awaiting confirm" : "all caught up",
      state: pendingReview > 0 ? ("alert" as const) : ("ok" as const),
    },
    {
      label: "Validate",
      value: "--",
      hint: "no run since build",
      state: "idle" as const,
    },
    {
      label: "Retrain",
      value: model?.active_version ?? "--",
      hint: "shipped",
      state: "ok" as const,
    },
  ];

  return (
    <div
      className="mb-6 rounded-md border border-rule bg-surface p-4"
      style={{ boxShadow: "inset 0 1px 0 rgba(6,182,212,0.1)" }}
    >
      <div className="grid grid-cols-[1fr_28px_1fr_28px_1fr_28px_1fr] items-center gap-3">
        {steps.map((step, i) => (
          <Fragment key={step.label}>
            <div
              className={cn(
                "rounded-md border px-4 py-3 transition-colors",
                step.state === "alert"
                  ? "border-[rgba(251,191,36,0.4)] bg-[color:var(--color-live-tint)]"
                  : step.state === "idle"
                    ? "border-rule bg-bg-glow"
                    : "border-rule bg-bg-glow",
              )}
            >
              <div
                className={cn(
                  "mb-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em]",
                  step.state === "alert" ? "text-live" : "text-muted",
                )}
              >
                {`0${i + 1} / ${step.label}`}
              </div>
              <div
                className={cn(
                  "mb-0.5 font-display text-[1.75rem] font-bold tabular-nums",
                  step.state === "alert" ? "text-live" : "text-ink",
                )}
                style={
                  step.state === "alert"
                    ? { textShadow: "0 0 12px rgba(251,191,36,0.4)" }
                    : undefined
                }
              >
                {step.value}
              </div>
              <div className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted">
                {step.hint}
              </div>
            </div>
            {i < steps.length - 1 && (
              <div aria-hidden className="flex items-center justify-center text-muted">
                <ArrowRight className="size-3.5" />
              </div>
            )}
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function InboxCard({
  items,
  remaining,
  onOpen,
}: {
  items: DevReviewQueueItem[];
  remaining: number;
  onOpen: () => void;
}) {
  return (
    <section
      className="mb-6 overflow-hidden rounded-md border border-[rgba(6,182,212,0.3)] bg-surface"
      style={{
        background: "linear-gradient(180deg, rgba(6,182,212,0.06), transparent)",
      }}
    >
      <header className="flex items-center justify-between border-b border-rule px-4 py-3">
        <div className="flex items-center gap-3">
          <Inbox className="size-4 text-beep" />
          <h2 className="font-display text-[0.9375rem] font-bold uppercase tracking-tight text-ink">
            Inbox
          </h2>
          <span className="rounded bg-[color:var(--color-beep-tint)] px-2 py-0.5 font-mono text-[0.625rem] font-bold tabular-nums text-beep">
            {items.length + remaining}
          </span>
        </div>
        <button
          type="button"
          onClick={onOpen}
          className="font-mono text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-beep hover:text-ink"
        >
          Review all
        </button>
      </header>
      <ul className="divide-y divide-rule">
        {items.map((it) => (
          <li
            key={it.slug}
            className="grid grid-cols-[28px_1fr_120px_90px_80px] items-center gap-3 px-4 py-2.5"
          >
            <span className="inline-flex size-7 items-center justify-center rounded bg-[color:var(--color-led-tint)] text-led">
              <Inbox className="size-3.5" />
            </span>
            <div className="min-w-0">
              <div className="truncate font-mono text-[0.8125rem] font-bold text-ink">
                {it.slug}
              </div>
              <div className="truncate font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                {it.venue ?? "--"} &middot; stage {it.stage_number ?? "?"} &middot;{" "}
                {it.shooter ?? "?"}
              </div>
            </div>
            <span className="inline-flex items-center gap-1.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  it.source === "match" ? "bg-led" : "bg-manual",
                )}
              />
              {it.source_label}
            </span>
            <span className="font-mono text-[0.6875rem] tabular-nums text-subtle">
              {formatAge(it.age_seconds)}
            </span>
            <button
              type="button"
              onClick={onOpen}
              className="rounded-md border border-rule px-2.5 py-1 font-mono text-[0.625rem] font-bold uppercase tracking-[0.06em] text-ink-2 transition-colors hover:bg-surface-2"
            >
              Open
            </button>
          </li>
        ))}
        {remaining > 0 && (
          <li className="px-4 py-2 text-center font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            ... {remaining} more in queue
          </li>
        )}
      </ul>
    </section>
  );
}

type TagTone = "beep" | "live" | "done" | "muted";

/** Row badges. ``short``: fewer audited shots than the scorecard's
 *  minimum round count -- possible missed labels. Shooting *more* than
 *  the minimum is normal IPSC (makeup shots) and is deliberately not
 *  flagged; the old strict-equality "mismatch" tag lit up 91% of the
 *  corpus for exactly that reason. */
export function fixtureTags(fx: LabFixtureRecord): { key: string; tone: TagTone }[] {
  const tags: { key: string; tone: TagTone }[] = [];
  if (isPromoted(fx)) tags.push({ key: "promoted", tone: "beep" });
  if (needsReview(fx)) tags.push({ key: "needs review", tone: "live" });
  if (!fx.has_audio) tags.push({ key: "no-audio", tone: "muted" });
  if (fx.expected_rounds && fx.n_shots && fx.n_shots < fx.expected_rounds) {
    tags.push({ key: `short ${fx.expected_rounds - fx.n_shots}`, tone: "live" });
  }
  tags.push(
    fx.in_calibration
      ? { key: "in model", tone: "done" }
      : { key: "not in model", tone: "muted" },
  );
  return tags;
}

const TAG_TONE_CLASSES: Record<TagTone, string> = {
  beep: "border-[rgba(6,182,212,0.4)] bg-[color:var(--color-beep-tint)] text-beep",
  live: "border-[rgba(251,191,36,0.4)] bg-[color:var(--color-live-tint)] text-live",
  done: "border-[rgba(74,222,128,0.4)] bg-[color:var(--color-done-tint)] text-done",
  muted: "border-rule bg-surface-2 text-muted",
};

function FixtureRow({ fx, onOpen }: { fx: LabFixtureRecord; onOpen: () => void }) {
  const tags = fixtureTags(fx);
  return (
    <li className="grid grid-cols-[1fr_140px_70px_70px_120px_28px] items-center gap-3 border-b border-rule px-4 py-2.5 transition-colors hover:bg-surface-2">
      <div className="min-w-0">
        <button
          type="button"
          onClick={onOpen}
          className="block truncate text-left font-mono text-[0.8125rem] font-bold text-ink hover:text-beep"
        >
          {fx.slug}
        </button>
        <div className="truncate font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          {fx.event_id ?? "no event id"}
        </div>
      </div>
      <span className="truncate font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
        {fx.source ?? "--"}
      </span>
      <span className="text-right font-mono text-[0.875rem] font-bold tabular-nums text-ink">
        {fx.n_shots}
      </span>
      <span
        className={cn(
          "text-right font-mono text-[0.75rem] tabular-nums",
          fx.has_audio ? "text-done" : "text-led",
        )}
      >
        {fx.has_audio ? "yes" : "no"}
      </span>
      <div className="flex flex-wrap gap-1">
        {tags.map((t) => (
          <span
            key={t.key}
            className={cn(
              "rounded border px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.06em]",
              TAG_TONE_CLASSES[t.tone],
            )}
          >
            {t.key}
          </span>
        ))}
      </div>
      <button
        type="button"
        onClick={onOpen}
        className="text-muted transition-colors hover:text-ink"
        aria-label="Open fixture"
      >
        <ArrowRight className="size-4" />
      </button>
    </li>
  );
}

function formatAge(seconds: number | null): string {
  if (seconds == null) return "--";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}
