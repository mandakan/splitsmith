/**
 * Developer / Review / Fixture detail -- ``/dev/review/:slug`` (#331,
 * rehomed from /dev/corpus/:slug so labeling lights up step 02).
 *
 * The full-page replacement for legacy ``Lab.tsx``'s below-the-fold
 * fixture drawer. Same job (per-candidate diff + labeling), but as a
 * first-class route: the corpus table opens into it, prev/next walk the
 * corpus without a round trip through the list, and the labeling panel
 * gets a sticky column of its own instead of competing with a
 * thousands-of-pixels-tall table above it.
 *
 * Two things it does that the drawer could not:
 *
 *  - It self-heals a cold cache. The eval universe is per-server-session,
 *    so after a restart there is no run to focus. Rather than parking on
 *    a "run eval first" wall, the page fires a *slug-scoped* eval for the
 *    fixture it is showing (seconds, not the ~10 minutes a full-corpus
 *    eval costs). The backend merges scoped results into a same-config
 *    cached run, so this never destroys a full run.
 *  - It keeps the dev-mode ``?match=`` context on its own links, the way
 *    ``DeveloperShell`` does for the workflow stepper.
 *
 * The candidate/labeling components are shared with the legacy page --
 * this module owns page composition, navigation and the label-save flow,
 * not the widgets.
 */

import {
  AlertCircle,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Link2,
  Loader2,
  Pencil,
  Play,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { Waveform } from "@/components/Waveform";
import { useConfirm } from "@/components/useConfirm";
import { CandidateTable } from "@/components/lab/CandidateTable";
import { DiffList } from "@/components/lab/DiffList";
import { KeyboardLegend } from "@/components/lab/KeyboardLegend";
import { LabelBreakdown } from "@/components/lab/LabelBreakdown";
import { Pin } from "@/components/lab/Pin";
import { StepThroughPanel } from "@/components/lab/StepThroughPanel";
import { VoterRecallTable } from "@/components/lab/VoterRecallTable";
import {
  filterFixtures,
  isFilterKey,
  type FilterKey,
} from "@/components/lab/corpusFilter";
import { disposeLabAudio } from "@/components/lab/labAudio";
import { LAB_PALETTE, fmtPct } from "@/components/lab/labPalette";
import { REASON_SHORTCUTS, SUBCLASS_SHORTCUTS } from "@/components/lab/labels";
import { useLabRun } from "@/components/lab/useLabRun";
import {
  api,
  type LabEvalFixture,
  type LabEvalRun,
  type LabFixtureRecord,
  type PeaksResult,
  type StageAudit,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/** Build the /review URL for a fixture, threading the source video
 *  through when available so the review page boots with the video bound. */
function reviewUrl(auditPath: string, sourceVideo: string | null | undefined): string {
  let url = `/review?fixture=${encodeURIComponent(auditPath)}`;
  if (sourceVideo) url += `&video=${encodeURIComponent(sourceVideo)}`;
  return url;
}

/** Re-open the secondary diff-confirm review for a promoted fixture.
 *  Derived fixture and its anchor live side-by-side in the same dir. */
function promoteReviewUrl(derivedAuditPath: string, anchorSlug: string): string {
  const dir = derivedAuditPath.slice(0, derivedAuditPath.lastIndexOf("/"));
  const anchorPath = `${dir}/${anchorSlug}.json`;
  return `/promote-review?fixture=${encodeURIComponent(derivedAuditPath)}&anchor=${encodeURIComponent(anchorPath)}`;
}

export function DevFixtureDetail() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const confirm = useConfirm();
  const [searchParams] = useSearchParams();
  const matchContext = searchParams.get("match");
  // The corpus list threads its active search/filter onto the row link
  // (#898), so prev/next here walk the subset the operator saw -- and
  // the back link restores the list to that same state.
  const listQuery = searchParams.get("q") ?? "";
  const listFilterRaw = searchParams.get("filter");
  const listFilter: FilterKey = isFilterKey(listFilterRaw) ? listFilterRaw : "all";

  // Keep the dev-mode match context (same contract as DeveloperShell's
  // stepper, #884) plus the list's search/filter on our own links.
  const withContext = useCallback(
    (to: string) => {
      const params = new URLSearchParams();
      if (matchContext) params.set("match", matchContext);
      if (listQuery) params.set("q", listQuery);
      if (listFilter !== "all") params.set("filter", listFilter);
      const s = params.toString();
      return s ? { pathname: to, search: `?${s}` } : { pathname: to };
    },
    [matchContext, listQuery, listFilter],
  );

  // autoRescore off: this page has no tuning sliders, so the debounced
  // rescore would only ever fire on the hydrated config -- wasted work.
  const { run, setRun, runEval, evalLoading, error, hydrated } = useLabRun({
    autoRescore: false,
  });

  const [catalog, setCatalog] = useState<LabFixtureRecord[] | null>(null);
  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [peaksError, setPeaksError] = useState<string | null>(null);
  const [audit, setAudit] = useState<StageAudit | null>(null);
  const [time, setTime] = useState(0);
  const [savingLabel, setSavingLabel] = useState<number | null>(null);
  const [selectedCn, setSelectedCn] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .listLabFixtures()
      .then((c) => {
        if (alive) setCatalog(c);
      })
      .catch(() => {
        if (alive) setCatalog([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  // Tear down the shared AudioContext + decoded-buffer cache on leave;
  // otherwise hundreds of MB of decoded PCM survives navigation.
  useEffect(() => disposeLabAudio, []);

  const record = useMemo(
    () => catalog?.find((r) => r.slug === slug) ?? null,
    [catalog, slug],
  );
  const focused = useMemo(
    () => (slug ? (run?.universe.fixtures.find((f) => f.slug === slug) ?? null) : null),
    [run, slug],
  );

  // Prev/next walk the operator's visible subset (catalog order under
  // the q/filter params the corpus row link carried). A slug the subset
  // doesn't cover -- stale bookmark, hand-edited URL -- falls back to
  // the whole catalog rather than stranding navigation.
  const walkList = useMemo(() => {
    if (!catalog) return null;
    const visible = filterFixtures(catalog, listQuery, listFilter);
    return visible.some((r) => r.slug === slug) ? visible : catalog;
  }, [catalog, listQuery, listFilter, slug]);
  const index = useMemo(
    () => walkList?.findIndex((r) => r.slug === slug) ?? -1,
    [walkList, slug],
  );
  const prev = index > 0 ? (walkList?.[index - 1] ?? null) : null;
  const next =
    index >= 0 && walkList && index < walkList.length - 1 ? walkList[index + 1] : null;

  // Self-heal a cold cache: scope the eval to this one fixture. Guarded
  // per-slug so a run that still doesn't cover it (failed eval, deleted
  // audio) can't spin. Gated on ``hydrated`` so a slow last-run fetch
  // can't let the eval fire under the stale DEFAULT_CONFIG -- a scoped
  // eval whose config hash differs from the cached run's would replace
  // a tuned universe instead of merging into it (#900).
  const attemptedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!hydrated || !slug || !record || focused || evalLoading) return;
    if (attemptedRef.current === slug) return;
    attemptedRef.current = slug;
    void runEval([slug]);
  }, [hydrated, slug, record, focused, evalLoading, runEval]);

  const auditPath = record?.audit_path ?? focused?.audit_path ?? null;
  useEffect(() => {
    if (!auditPath) return;
    let alive = true;
    setPeaks(null);
    setPeaksError(null);
    setAudit(null);
    Promise.all([api.getFixturePeaks(auditPath), api.getFixtureAudit(auditPath)])
      .then(([p, a]) => {
        if (!alive) return;
        setPeaks(p);
        setAudit(a);
      })
      .catch((err) => {
        // The waveform is decoration here (the candidate table is the
        // tool), but a swallowed failure left a permanent "loading"
        // spinner (#898) -- say what happened instead.
        if (alive) setPeaksError(String(err));
      });
    return () => {
      alive = false;
    };
  }, [auditPath]);

  // A new fixture is a new labeling session.
  useEffect(() => {
    setSelectedCn(null);
    setTime(0);
  }, [slug]);

  const onLabelChanged = useCallback(
    (updated: LabEvalRun | null) => {
      // The server returns a freshly-relabeled run when a cached eval
      // exists; otherwise null, and we re-derive by re-evaluating just
      // this fixture rather than the whole corpus.
      if (updated) setRun(updated);
      else if (slug) void runEval([slug]);
    },
    [setRun, runEval, slug],
  );

  const handleLabel = useCallback(
    async (
      candidate_number: number,
      patch: { reason?: string | null; subclass?: string | null },
    ) => {
      if (!focused) return;
      // Time is the storage key, so look it up from the live candidate
      // list before sending.
      const cand = focused.candidates.find(
        (c) => c.candidate_number === candidate_number,
      );
      if (!cand) return;
      setSavingLabel(candidate_number);
      try {
        const resp = await api.applyLabLabels({
          audit_path: focused.audit_path,
          labels: [{ candidate_number, time: cand.time, ...patch }],
        });
        onLabelChanged(resp.run);
      } catch (err) {
        console.error("label save failed", err);
      } finally {
        setSavingLabel(null);
      }
    },
    [focused, onLabelChanged],
  );

  // Step-through registers a "next candidate in my filter+sort" resolver
  // so a label keypress auto-advances.
  const advanceRef = useRef<((cn: number) => number | null) | null>(null);
  const setAdvancer = useCallback((fn: ((cn: number) => number | null) | null) => {
    advanceRef.current = fn;
  }, []);

  const candidates = focused?.candidates;
  useEffect(() => {
    const cands = candidates;
    if (!cands || cands.length === 0) return;
    function isTypingTarget(t: EventTarget | null): boolean {
      if (!(t instanceof HTMLElement)) return false;
      if (t.isContentEditable) return true;
      const tag = t.tagName;
      return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    }
    // Arrow, not a function declaration: a hoisted declaration would
    // lose the ``cands`` narrowing above.
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTypingTarget(e.target)) return;
      const idx =
        selectedCn != null
          ? cands.findIndex((c) => c.candidate_number === selectedCn)
          : -1;

      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        const n = idx < 0 ? 0 : Math.min(cands.length - 1, idx + 1);
        setSelectedCn(cands[n].candidate_number);
        return;
      }
      if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        const n = idx < 0 ? cands.length - 1 : Math.max(0, idx - 1);
        setSelectedCn(cands[n].candidate_number);
        return;
      }
      if (e.key === "Escape") {
        setSelectedCn(null);
        return;
      }
      if (idx < 0) return;
      const c = cands[idx];

      const advance = () => {
        const n = advanceRef.current?.(c.candidate_number);
        if (n != null) setSelectedCn(n);
      };

      // Truth-positive candidates (kept or not) take a subclass; every
      // other candidate takes an FP reason. Treating FN candidates as
      // positives is what lets a missed truth shot be labeled paper /
      // steel / barrel -- the training signal for recovering them.
      const isPositive = c.truth === 1;

      if (e.key === "0" || e.key === "Backspace") {
        e.preventDefault();
        void handleLabel(
          c.candidate_number,
          isPositive ? { subclass: null } : { reason: null },
        );
        advance();
        return;
      }

      const key = e.key.toLowerCase();
      const label = isPositive ? SUBCLASS_SHORTCUTS[key] : REASON_SHORTCUTS[key];
      if (!label) return;
      e.preventDefault();
      void handleLabel(
        c.candidate_number,
        isPositive ? { subclass: label } : { reason: label },
      );
      advance();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [candidates, selectedCn, handleLabel]);

  const onDelete = useCallback(async () => {
    if (!record) return;
    const ok = await confirm({
      title: `Delete derived fixture "${record.slug}"?`,
      body: "This removes the JSON, WAV, peaks and promotion-report.",
    });
    if (!ok.confirmed) return;
    try {
      await api.deleteFixture(record.slug);
      navigate(withContext("/dev/corpus"), { replace: true });
    } catch (err) {
      window.alert(`Delete failed: ${err}`);
    }
  }, [record, confirm, navigate, withContext]);

  // Ground truth the consensus missed. When no run covers the fixture
  // yet, the audit's own shot list stands in so the waveform is still
  // readable pre-eval.
  const truthTimes = focused?.truth_times ?? audit?.shots?.map((s) => s.time) ?? [];
  const fns = focused
    ? focused.truth_times.filter(
        (t) =>
          !focused.candidates.some(
            (c) =>
              c.truth === 1 &&
              c.matched_shot_number !== null &&
              Math.abs(c.time - t) < 0.001,
          ),
      )
    : [];
  const fps = focused?.candidates.filter((c) => c.kept && c.truth === 0) ?? [];

  if (catalog !== null && !record && !focused) {
    return (
      <div className="mx-auto min-w-0 max-w-[1500px] px-7 py-5">
        <BackLink to={withContext("/dev/corpus")} />
        <div className="mt-6 rounded-md border border-rule bg-surface px-5 py-10 text-center">
          <div className="font-mono text-[0.8125rem] font-bold text-ink">{slug}</div>
          <p className="mt-2 text-[0.875rem] text-muted">
            No such fixture on disk. It may have been deleted or renamed.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto min-w-0 max-w-[1500px] space-y-4 px-7 py-5">
      <header>
        <div className="mb-3 flex items-center justify-between gap-6">
          <div className="flex items-center gap-4">
            <BackLink to={withContext("/dev/corpus")} />
            <span className="text-whisper">/</span>
            <div className="flex items-center gap-2.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.18em] text-beep">
              <span aria-hidden className="h-px w-6 bg-beep" />
              Fixture detail
            </div>
          </div>
          <nav className="flex items-center gap-1.5">
            <StepLink
              to={prev ? withContext(`/dev/review/${prev.slug}`) : null}
              label="Previous fixture"
              side="prev"
            />
            <span className="min-w-[74px] text-center font-mono text-[0.6875rem] tabular-nums text-muted">
              {index >= 0 && walkList
                ? `${String(index + 1).padStart(2, "0")} / ${String(walkList.length).padStart(2, "0")}`
                : "-- / --"}
            </span>
            <StepLink
              to={next ? withContext(`/dev/review/${next.slug}`) : null}
              label="Next fixture"
              side="next"
            />
          </nav>
        </div>

        <div className="flex flex-wrap items-end justify-between gap-x-7 gap-y-3 border-b border-rule pb-4">
          <div className="min-w-0">
            <h1 className="truncate font-mono text-[1.375rem] font-bold leading-tight tracking-tight text-ink">
              {slug}
            </h1>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
              <span>{record?.event_id ?? "no event id"}</span>
              <span className="text-whisper">/</span>
              <span>
                <b className="font-bold tabular-nums text-ink">
                  {record?.n_shots ?? focused?.metrics.n_truth ?? "--"}
                </b>{" "}
                ground truth
              </span>
              {record?.expected_rounds != null && (
                <>
                  <span className="text-whisper">/</span>
                  <span>
                    expected{" "}
                    <b className="font-bold tabular-nums text-ink">
                      {record.expected_rounds}
                    </b>
                  </span>
                </>
              )}
              {record?.beep_time != null && (
                <>
                  <span className="text-whisper">/</span>
                  <span>
                    beep{" "}
                    <b className="font-bold tabular-nums text-ink">
                      {record.beep_time.toFixed(3)}s
                    </b>
                  </span>
                </>
              )}
              {record && !record.has_audio && (
                <>
                  <span className="text-whisper">/</span>
                  <span className="rounded border border-[rgba(251,191,36,0.4)] bg-[color:var(--color-live-tint)] px-1.5 py-0.5 font-bold text-live">
                    no wav
                  </span>
                </>
              )}
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            {auditPath && (
              <ActionLink
                to={reviewUrl(auditPath, record?.source_video ?? focused?.source_video)}
                title="Edit ground-truth markers (add / move / delete shots, edit beep) in the review editor"
              >
                <Pencil className="size-3.5" />
                Edit markers
              </ActionLink>
            )}
            {record?.anchor_slug && (
              <ActionLink
                to={promoteReviewUrl(record.audit_path, record.anchor_slug)}
                title="Re-open the secondary diff-confirm review"
              >
                <Link2 className="size-3.5" />
                Re-review
              </ActionLink>
            )}
            {record?.anchor_slug && (
              <button
                type="button"
                onClick={onDelete}
                aria-label={`Delete derived fixture ${record.slug}`}
                title="Delete this derived fixture (anchor not affected)"
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-rule px-2.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.06em] text-muted transition-colors hover:border-destructive/50 hover:bg-destructive/10 hover:text-destructive"
              >
                <Trash2 className="size-3.5" />
                Delete
              </button>
            )}
          </div>
        </div>
      </header>

      <EvalStatusStrip
        focused={focused}
        run={run}
        evalLoading={evalLoading}
        error={error}
        onRun={() => slug && void runEval([slug])}
      />

      {/* Full-width diff: kept candidates coloured by outcome, plus the
          truth the consensus missed. */}
      <section className="overflow-hidden rounded-md border border-rule bg-surface">
        <div className="flex items-center justify-between border-b border-rule px-4 py-2.5">
          <h2 className="font-display text-[0.9375rem] font-bold uppercase tracking-tight text-ink">
            Waveform diff
          </h2>
          <div className="flex items-center gap-3 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em]">
            <Swatch color={LAB_PALETTE.tp} label="true positive" />
            <Swatch color={LAB_PALETTE.fp} label="false positive" />
            <Swatch color={LAB_PALETTE.fn} label="false negative" />
          </div>
        </div>
        <div className="p-3">
          {peaks ? (
            <Waveform
              peaks={peaks.peaks}
              duration={peaks.duration}
              currentTime={time}
              onScrub={setTime}
              beepTime={peaks.beep_time}
              height={150}
            >
              {focused
                ? focused.candidates
                    .filter((c) => c.kept)
                    .map((c) => (
                      <Pin
                        key={`p-${c.candidate_number}`}
                        time={c.time}
                        duration={peaks.duration}
                        color={c.truth === 1 ? LAB_PALETTE.tp : LAB_PALETTE.fp}
                        label={c.truth === 1 ? "TP" : "FP"}
                      />
                    ))
                : truthTimes.map((t, i) => (
                    <Pin
                      key={`gt-${i}`}
                      time={t}
                      duration={peaks.duration}
                      color={LAB_PALETTE.tp}
                      label={`shot ${i + 1}`}
                    />
                  ))}
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
          ) : peaksError ? (
            <div className="flex h-[150px] flex-col items-center justify-center gap-1.5 rounded border border-rule/60 bg-bg-glow px-6 text-center font-mono text-[0.6875rem] text-muted">
              <span className="inline-flex items-center gap-1.5 uppercase tracking-[0.08em]">
                <AlertCircle className="size-3.5" /> waveform unavailable
              </span>
              <span className="max-w-full truncate text-[0.625rem] text-subtle">
                {peaksError}
              </span>
            </div>
          ) : (
            <div className="flex h-[150px] items-center justify-center rounded border border-rule/60 bg-bg-glow font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-muted">
              <Loader2 className="mr-2 size-3.5 animate-spin" /> loading waveform
            </div>
          )}
        </div>
      </section>

      {/* The labeling workbench. One scroll surface per pane, both
          bounded to the viewport: the candidate table is the primary
          scroll region (its card is a viewport-height flex column, the
          table body fills it); the aside is a fixed-height flex column
          where filters, player and label buttons stay put and only the
          queue list flexes and scrolls. The old shape nested four
          scrollbars (page, a 384px table box, the whole aside, the
          queue inside the aside) and J/K walks fought all of them. */}
      {focused && (
        <div className="grid grid-cols-[minmax(0,1fr)_400px] items-start gap-4">
          <section className="flex max-h-[calc(100dvh-var(--shell-header-h,86px)-1.5rem)] min-w-0 flex-col overflow-hidden rounded-md border border-rule bg-surface">
            <div className="flex shrink-0 items-center justify-between border-b border-rule px-4 py-2.5">
              <div className="flex items-center gap-3">
                <h2 className="font-display text-[0.9375rem] font-bold uppercase tracking-tight text-ink">
                  Candidate universe
                </h2>
                <span className="font-mono text-[0.6875rem] tabular-nums text-muted">
                  {focused.candidates.length} candidates / {focused.metrics.n_kept} kept
                </span>
              </div>
              <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-subtle">
                J / K to walk
              </span>
            </div>
            <CandidateTable
              candidates={focused.candidates}
              onLabel={handleLabel}
              savingLabel={savingLabel}
              selectedCn={selectedCn}
              onSelect={setSelectedCn}
            />
          </section>

          {/* Rigid fixed-height flex column -- the aside itself NEVER
              scrolls. Every control (filters, player, label buttons,
              nav-key strip) has a permanent position; the queue list is
              the only element that flexes and scrolls. The per-label
              shortcut legend that used to force an aside scrollbar now
              lives as <kbd> hints on the label buttons themselves. */}
          <aside className="sticky top-[var(--shell-header-h,86px)] flex h-[calc(100dvh-var(--shell-header-h,86px)-1.5rem)] min-h-0 flex-col gap-3 self-start overflow-hidden">
            <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-[rgba(6,182,212,0.4)] bg-surface">
              <div className="flex shrink-0 items-center justify-between border-b border-rule px-4 py-2.5">
                <h2 className="font-display text-[0.9375rem] font-bold uppercase tracking-tight text-ink">
                  Label
                </h2>
                <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-beep">
                  step-through
                </span>
              </div>
              <div className="flex min-h-0 flex-1 flex-col p-3">
                <StepThroughPanel
                  fixture={focused}
                  selectedCn={selectedCn}
                  onSelect={setSelectedCn}
                  registerAdvancer={setAdvancer}
                  savingLabel={savingLabel}
                  onLabel={handleLabel}
                />
              </div>
            </section>
            <div className="shrink-0">
              <KeyboardLegend selectedCn={selectedCn} />
            </div>
          </aside>
        </div>
      )}

      {focused && (
        <footer className="space-y-3">
          <div className="grid grid-cols-6 gap-px overflow-hidden rounded-md border border-rule bg-rule">
            <Stat label="Precision" value={fmtPct(focused.metrics.precision)} />
            <Stat label="Recall" value={fmtPct(focused.metrics.recall)} />
            <Stat label="F1" value={focused.metrics.f1.toFixed(3)} />
            <Stat
              label="True pos"
              value={String(focused.metrics.true_positives)}
              tone="done"
            />
            <Stat
              label="False pos"
              value={String(focused.metrics.false_positives)}
              tone={focused.metrics.false_positives > 0 ? "live" : undefined}
            />
            <Stat
              label="False neg"
              value={String(focused.metrics.false_negatives)}
              tone={focused.metrics.false_negatives > 0 ? "bad" : undefined}
            />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <VoterRecallTable metrics={focused.metrics} />
            <DiffList fps={fps} fns={fns} />
            <div className="min-w-0">
              <LabelBreakdown
                fpByReason={focused.metrics.fp_by_reason}
                positivesBySubclass={focused.metrics.positives_by_subclass}
              />
            </div>
          </div>
        </footer>
      )}
    </div>
  );
}

function BackLink({ to }: { to: { pathname: string; search?: string } }) {
  return (
    <Link
      to={to}
      className="inline-flex items-center gap-1.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-muted transition-colors hover:text-ink"
    >
      <ArrowLeft className="size-3.5" />
      Corpus
    </Link>
  );
}

function StepLink({
  to,
  label,
  side,
}: {
  to: { pathname: string; search?: string } | null;
  label: string;
  side: "prev" | "next";
}) {
  const icon =
    side === "prev" ? <ChevronLeft className="size-3.5" /> : <ChevronRight className="size-3.5" />;
  const body = (
    <>
      {side === "prev" && icon}
      {side === "prev" ? "Prev" : "Next"}
      {side === "next" && icon}
    </>
  );
  const classes =
    "inline-flex h-8 items-center gap-1 rounded-md border px-2.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.06em] transition-colors";
  if (!to) {
    return (
      <span
        aria-label={label}
        aria-disabled="true"
        className={cn(classes, "cursor-not-allowed border-rule text-whisper")}
      >
        {body}
      </span>
    );
  }
  return (
    <Link
      to={to}
      aria-label={label}
      className={cn(classes, "border-rule text-ink-2 hover:border-beep hover:text-beep")}
    >
      {body}
    </Link>
  );
}

function ActionLink({
  to,
  title,
  children,
}: {
  to: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Link
      to={to}
      title={title}
      className="inline-flex h-8 items-center gap-1.5 rounded-md border border-rule px-2.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.06em] text-ink-2 transition-colors hover:border-beep hover:text-beep"
    >
      {children}
    </Link>
  );
}

function Swatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-muted">
      <span aria-hidden className="size-2 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "done" | "live" | "bad";
}) {
  return (
    <div className="bg-surface px-4 py-3">
      <div className="mb-1 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-muted">
        {label}
      </div>
      <div
        className={cn(
          "font-display text-[1.5rem] font-bold tabular-nums leading-none",
          tone === "done"
            ? "text-done"
            : tone === "live"
              ? "text-live"
              : tone === "bad"
                ? "text-destructive"
                : "text-ink",
        )}
      >
        {value}
      </div>
    </div>
  );
}

/**
 * Where the eval stands for *this* fixture. Four states, because the
 * operator's next action differs in each: labeling is impossible without
 * a run (candidates only exist after eval), so the pre-eval state has to
 * say so rather than looking like an empty table.
 */
function EvalStatusStrip({
  focused,
  run,
  evalLoading,
  error,
  onRun,
}: {
  focused: LabEvalFixture | null;
  run: LabEvalRun | null;
  evalLoading: boolean;
  error: string | null;
  onRun: () => void;
}) {
  if (evalLoading) {
    return (
      <Strip tone="beep">
        <Loader2 className="size-3.5 shrink-0 animate-spin text-beep" />
        <span>
          Scoring this fixture -- CLAP + PANN + GBDT. The first run of a server
          session loads the models, so it takes a moment.
        </span>
      </Strip>
    );
  }
  if (error) {
    return (
      <Strip tone="bad">
        <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
        <span className="min-w-0 flex-1 break-words">{error}</span>
        <RunButton onClick={onRun} label="Retry" />
      </Strip>
    );
  }
  if (focused) {
    return (
      <Strip tone="ok">
        <span className="rounded border border-[rgba(6,182,212,0.4)] bg-[color:var(--color-beep-tint)] px-1.5 py-0.5 font-bold text-beep">
          cached
        </span>
        <span>
          Scored under config <b className="font-bold text-ink">{run?.config_hash}</b>
          {run?.built_at && ` / built ${run.built_at}`}
        </span>
        <span className="flex-1" />
        <RunButton onClick={onRun} label="Re-eval" />
      </Strip>
    );
  }
  return (
    <Strip tone="warn">
      <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
      <span className="min-w-0 flex-1">
        No cached eval covers this fixture, so there are no candidates to label
        yet -- labels attach to detection candidates, not to the audit. A
        fixture-scoped eval is starting automatically; it takes seconds, unlike
        the full-corpus run. The eval cache is per-server-session, so a restart
        brings you back here.
      </span>
      <RunButton onClick={onRun} label="Eval now" />
    </Strip>
  );
}

function RunButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-rule bg-surface px-2.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-ink-2 transition-colors hover:border-beep hover:text-beep"
    >
      <Play className="size-3" />
      {label}
    </button>
  );
}

function Strip({
  tone,
  children,
}: {
  tone: "beep" | "ok" | "warn" | "bad";
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-2.5 rounded-md border px-4 py-2.5 font-mono text-[0.6875rem] leading-relaxed tracking-[0.02em]",
        tone === "beep" && "border-[rgba(6,182,212,0.4)] bg-[color:var(--color-beep-tint)] text-ink-2",
        tone === "ok" && "border-rule bg-surface text-muted",
        tone === "warn" &&
          "border-[rgba(251,191,36,0.4)] bg-[color:var(--color-live-tint)] text-live",
        tone === "bad" && "border-destructive/40 bg-destructive/5 text-destructive",
      )}
    >
      {children}
    </div>
  );
}
