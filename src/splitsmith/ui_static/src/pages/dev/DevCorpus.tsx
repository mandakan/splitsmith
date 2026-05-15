/**
 * Developer / Corpus (#331).
 *
 * Lists every fixture currently on disk plus a workflow-status banner
 * across the 4 dev-mode steps. Reuses /api/lab/fixtures (always
 * available; just reads the audit-fixtures dir) plus /api/dev/model.
 *
 * Replaces the corpus-browsing slice of legacy Lab.tsx.
 */

import { ArrowRight, Inbox, Search, Slash } from "lucide-react";
import { Fragment, useEffect, useMemo, useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";

import { api, type DevReviewQueueItem, type LabFixtureRecord } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { DeveloperShellOutletContext } from "@/components/developer/DeveloperShell";

const FILTER_DEFS = [
  { key: "all", label: "all" },
  { key: "pending", label: "needs review" },
  { key: "promoted", label: "promoted" },
  { key: "audio-missing", label: "no audio" },
] as const;

type FilterKey = (typeof FILTER_DEFS)[number]["key"];

export function DevCorpus() {
  const { model } = useOutletContext<DeveloperShellOutletContext>();
  const navigate = useNavigate();

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

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return fixtures.filter((fx) => {
      if (filter === "pending" && !fx.anchor_slug) return false;
      if (filter === "promoted" && !fx.anchor_slug) return false;
      if (filter === "audio-missing" && fx.has_audio) return false;
      if (!q) return true;
      return (
        fx.slug.toLowerCase().includes(q) ||
        (fx.source ?? "").toLowerCase().includes(q) ||
        (fx.event_id ?? "").toLowerCase().includes(q)
      );
    });
  }, [fixtures, query, filter]);

  const pendingCount = queue.length;

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
      </div>

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
                onOpen={() => navigate(`/dev/legacy/lab/${fx.slug}`)}
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

function FixtureRow({ fx, onOpen }: { fx: LabFixtureRecord; onOpen: () => void }) {
  const tags: string[] = [];
  if (fx.anchor_slug) tags.push("promoted");
  if (!fx.has_audio) tags.push("no-audio");
  if (fx.expected_rounds && fx.n_shots && fx.expected_rounds !== fx.n_shots) tags.push("mismatch");
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
        {tags.length === 0 ? (
          <span className="font-mono text-[0.625rem] text-whisper">--</span>
        ) : (
          tags.map((t) => (
            <span
              key={t}
              className={cn(
                "rounded border px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.06em]",
                t === "promoted"
                  ? "border-[rgba(6,182,212,0.4)] bg-[color:var(--color-beep-tint)] text-beep"
                  : t === "mismatch"
                    ? "border-[rgba(251,191,36,0.4)] bg-[color:var(--color-live-tint)] text-live"
                    : "border-rule bg-surface-2 text-muted",
              )}
            >
              {t}
            </span>
          ))
        )}
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
