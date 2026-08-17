import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";

import { LAB_REASONS, LAB_SUBCLASSES, type LabEvalFixture } from "@/lib/api";
import { cn } from "@/lib/utils";

import { REASON_SHORTCUTS, SUBCLASS_SHORTCUTS } from "./labels";
import { SnippetPlayer } from "./SnippetPlayer";

// label -> shortcut key, so each button can carry its own <kbd> hint
// (the big per-key legend card is gone; see KeyboardLegend).
const KEY_FOR_REASON: Record<string, string> = Object.fromEntries(
  Object.entries(REASON_SHORTCUTS).map(([k, v]) => [v, k]),
);
const KEY_FOR_SUBCLASS: Record<string, string> = Object.fromEntries(
  Object.entries(SUBCLASS_SHORTCUTS).map(([k, v]) => [v, k]),
);

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

/**
 * The always-on-screen labeling panel for /dev/corpus/:slug (its only
 * consumer since the legacy Lab page died -- the ``autoPlay`` /
 * ``preserveSelection`` compat props went with it, #901).
 *
 * Playback starts silent: opening a fixture is a user gesture, so the
 * AudioContext is resumable and auto-play would loop a gunshot at
 * whoever arrived. Once the operator starts playback (play button or
 * space) the panel arms itself and later candidate changes auto-play,
 * which is what makes stepping feel continuous; navigating to another
 * fixture disarms it again -- a new fixture is a new labeling session
 * and must not open with sound.
 *
 * Selections made outside the panel's own filter (the candidate table
 * beside it, J/K over the full universe) are treated as intentional
 * and followed rather than re-snapped to the head of the list -- a
 * label save rebuilds the list, and re-snapping would move the
 * operator off the row they just labeled. The panel falls back to the
 * head only when the candidate is gone from the fixture entirely.
 */
export function StepThroughPanel({
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
  const [playing, setPlaying] = useState(false);

  // Armed once the operator has asked for audio at least once.
  const armedRef = useRef(false);
  const togglePlay = useCallback(() => {
    armedRef.current = true;
    setPlaying((p) => !p);
  }, []);

  // Disarm on fixture change (keyed on slug -- label saves hand back
  // fresh fixture objects for the same slug and must not disarm): the
  // panel stays mounted across prev/next, and a fixture the operator
  // just navigated to must not start playing until asked.
  const slug = fixture.slug;
  useEffect(() => {
    armedRef.current = false;
    setPlaying(false);
  }, [slug]);

  // Auto-play whenever the candidate changes (resumes if user paused).
  useEffect(() => {
    if (!armedRef.current) return;
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
      togglePlay();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePlay]);

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

  // Default selection: first item in the active list. ``ordered`` is
  // rebuilt on every label save (the run comes back with fresh candidate
  // objects), so "the selection is gone" means gone from the *fixture*,
  // not merely from this panel's filter -- an off-list selection is an
  // intentional pick from the candidate table beside the panel.
  useEffect(() => {
    if (ordered.length === 0) return;
    if (selectedCn == null) {
      onSelect(ordered[0].candidate_number);
      return;
    }
    const alive = fixture.candidates.some((c) => c.candidate_number === selectedCn);
    if (!alive) onSelect(ordered[0].candidate_number);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ordered]);

  // Follow a selection made outside the panel's own filter rather than
  // showing "nothing selected" -- otherwise clicking a non-borderline
  // row in the side-by-side candidate table would blank the player and
  // the label buttons.
  const current = useMemo(() => {
    const inList = ordered.find((c) => c.candidate_number === selectedCn) ?? null;
    if (inList) return inList;
    return fixture.candidates.find((c) => c.candidate_number === selectedCn) ?? null;
  }, [ordered, selectedCn, fixture.candidates]);

  const idxInList = current
    ? ordered.findIndex((c) => c.candidate_number === current.candidate_number)
    : -1;

  // Keep the selected row visible in the queue as the operator walks.
  // Container-scoped on purpose: scrollIntoView adjusts every scrollable
  // ancestor, which made the whole aside (and the page) lurch on each
  // J/K press. Only the queue's own scrollTop may move.
  useEffect(() => {
    if (selectedCn == null) return;
    const el = document.querySelector<HTMLElement>(`[data-step-cn="${selectedCn}"]`);
    const box = el?.closest<HTMLElement>("[data-step-queue]");
    if (!el || !box) return;
    const er = el.getBoundingClientRect();
    const br = box.getBoundingClientRect();
    if (er.top < br.top) {
      box.scrollTop += er.top - br.top;
    } else if (er.bottom > br.bottom) {
      box.scrollTop += er.bottom - br.bottom;
    }
  }, [selectedCn]);

  // Move to the next candidate in the active filter+sort. Used by the
  // label buttons so clicking a button advances like a keypress does.
  const advanceFromCurrent = useCallback(() => {
    if (idxInList < 0 || idxInList >= ordered.length - 1) return;
    onSelect(ordered[idxInList + 1].candidate_number);
  }, [idxInList, ordered, onSelect]);

  // No frame of its own: the hosting card carries the accent border
  // (nesting a green frame inside the detail page's cyan card was the
  // #898 clash). Green stays for selection semantics in the queue below.
  //
  // Fill-height flex column: the hosting card is viewport-bounded, so
  // controls / player / label buttons hold their place and the queue
  // (flex-1 below) is the only part that grows and scrolls.
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex shrink-0 flex-wrap items-end gap-3 text-[11px]">
        <label className="flex flex-col gap-1">
          <span className="font-medium text-muted">Filter</span>
          <select
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value as StepFilter);
              e.currentTarget.blur();
            }}
            className="rounded border border-rule bg-bg px-1 py-0.5"
          >
            <option value="borderline">Borderline (1-3 votes, recommended)</option>
            <option value="rejected_only">Rejected only</option>
            <option value="fps_only">FPs only (kept negatives)</option>
            <option value="unlabeled_only">Unlabeled only</option>
            <option value="all">All candidates</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium text-muted">Class</span>
          <select
            value={classFilter}
            onChange={(e) => {
              setClassFilter(e.target.value);
              e.currentTarget.blur();
            }}
            className="rounded border border-rule bg-bg px-1 py-0.5"
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
          <span className="font-medium text-muted">Sort</span>
          <select
            value={sort}
            onChange={(e) => {
              setSort(e.target.value as StepSort);
              e.currentTarget.blur();
            }}
            className="rounded border border-rule bg-bg px-1 py-0.5"
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
          <span className="font-medium text-muted">Pre ms ({preMs})</span>
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
          <span className="font-medium text-muted">Post ms ({postMs})</span>
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
        <span className="ml-auto text-muted">
          {/* "--" rather than "0": the selection has no queue position
              when it sits outside the panel filter. */}
          {idxInList >= 0 ? idxInList + 1 : "--"} / {ordered.length}
          {ordered.length === 0 && " (no candidates match filter)"}
          {/* The operator picked a row that this filter excludes. */}
          {idxInList < 0 && current && " (selection is outside this filter)"}
        </span>
      </div>

      <div className="shrink-0">
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
          <div className="rounded border border-dashed border-rule/60 px-4 py-6 text-center text-xs text-muted">
            {ordered.length === 0
              ? "Adjust the filter or run eval to populate the candidate list."
              : "No candidate selected -- press J or click a row to start."}
          </div>
        )}
      </div>

      {current && (
        <div className="mt-3 flex shrink-0 flex-wrap gap-1 text-[10px]">
          {(current.truth === 1 ? LAB_SUBCLASSES : LAB_REASONS).map((label) => {
            const key = (current.truth === 1 ? KEY_FOR_SUBCLASS : KEY_FOR_REASON)[label];
            return (
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
                className="inline-flex items-center gap-1 rounded border border-rule/60 bg-bg px-1.5 py-0.5 hover:bg-surface-3"
              >
                {key && (
                  <kbd className="rounded-sm border border-rule/60 bg-surface-2 px-0.5 font-mono text-[9px] uppercase text-muted">
                    {key}
                  </kbd>
                )}
                {label}
              </button>
            );
          })}
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
            className="inline-flex items-center gap-1 rounded border border-rule/60 bg-bg px-1.5 py-0.5 text-muted hover:bg-surface-3"
          >
            <kbd className="rounded-sm border border-rule/60 bg-surface-2 px-0.5 font-mono text-[9px] uppercase text-muted">
              0
            </kbd>
            clear
          </button>
        </div>
      )}

      {/* Compact list -- shows position in the queue + assigned labels.
          The one flexing region of the panel: grows to whatever height
          the fixed sections leave, scrolls internally past its floor. */}
      <div
        data-step-queue
        className="mt-3 min-h-[4rem] flex-1 overflow-y-auto rounded border border-rule/60 bg-bg/50"
      >
        <table className="w-full text-[11px]">
          <tbody>
            {ordered.map((c) => {
              const sel = c.candidate_number === selectedCn;
              const saving = savingLabel === c.candidate_number;
              const label = c.truth === 1 ? c.subclass : c.reason;
              return (
                <tr
                  key={c.candidate_number}
                  data-step-cn={c.candidate_number}
                  className={cn(
                    "cursor-pointer border-b border-rule/30 font-mono",
                    sel && "bg-led/15 outline outline-1 outline-led/60",
                    !sel && c.kept && c.truth === 1 && "bg-emerald-500/5",
                    !sel && c.kept && c.truth === 0 && "bg-orange-500/10",
                  )}
                  onClick={() => onSelect(c.candidate_number)}
                >
                  <td className="px-2 py-0.5">#{c.candidate_number}</td>
                  <td className="px-2 py-0.5 text-right text-muted">
                    {c.time.toFixed(3)}s
                  </td>
                  <td className="px-2 py-0.5 text-right text-muted">
                    score {c.ensemble_score.toFixed(2)}
                  </td>
                  <td className="px-2 py-0.5 text-right">
                    {label ? (
                      <span className="rounded bg-muted px-1">{label}</span>
                    ) : (
                      <span className="text-muted">--</span>
                    )}
                  </td>
                  <td className="w-4">
                    {saving && <Loader2 className="size-3 animate-spin text-muted" />}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
