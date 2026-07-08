/**
 * ConnectMatchDialog -- attach an already-created match to the SSI
 * Scoreboard, then confirm a per-shooter name-to-competitor mapping
 * before anything is written (#598).
 *
 * Two steps in one modal:
 *   1. Search scoreboard.urdr.dev and pick the event -- reuses
 *      ``ResultRow`` and the raw ``fetch("/api/scoreboard/search?q=")``
 *      pattern from CreateMatch's from-scoreboard variant. Picking a
 *      result immediately calls ``connectScoreboardMatch``, which links
 *      the match + every current shooter server-side and returns
 *      name-based proposals.
 *   2. The proposals render as an editable local-shooter -> competitor
 *      mapping table (a plain ``<select>`` per row rather than
 *      CreateMatch's checkbox/accordion roster -- this step is "confirm
 *      one link per shooter", not "pick N of many"). Ambiguous
 *      proposals are flagged; unmatched rows default to "leave
 *      unlinked". Confirming calls ``reconcileScoreboardLinks`` with
 *      only the rows still linked, then ``onApplied`` so the caller can
 *      re-pull the roster (pass the outlet context's ``refresh``).
 *
 * Never auto-applies -- the operator always reviews the mapping first.
 */
import { ArrowLeft, Link2, Loader2, Search, X } from "lucide-react";
import { useRef, useState } from "react";

import { ResultRow } from "@/components/scoreboard/ResultRow";
import { Button } from "@/components/ui/button";
import { Portal } from "@/components/ui/Portal";
import {
  ApiError,
  api,
  type LinkProposal,
  type ScoreboardMatchCompetitor,
  type ScoreboardMatchData,
  type ScoreboardMatchRef,
  type ShooterListEntry,
} from "@/lib/api";
import { useDialogFocus } from "@/lib/dialogFocus";
import { cn } from "@/lib/utils";

interface ConnectMatchDialogProps {
  /** Local roster to map. Pass the caller's freshest shooter list (e.g.
   *  Shooters.tsx's own ``data.shooters``) rather than the outlet
   *  context's, which only refreshes on a shell reload. */
  shooters: ShooterListEntry[];
  onClose: () => void;
  /** Fired once ``reconcileScoreboardLinks`` has succeeded, so the caller
   *  can re-pull the roster (and the outlet context's project, so
   *  ``scoreboard_match_id`` gating elsewhere updates). */
  onApplied: () => void;
}

type Step = "search" | "map";

export function ConnectMatchDialog({
  shooters,
  onClose,
  onApplied,
}: ConnectMatchDialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  useDialogFocus(true, panelRef, onClose);

  const [step, setStep] = useState<Step>("search");
  const [error, setError] = useState<string | null>(null);

  // Step 1: search + connect.
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ScoreboardMatchRef[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [offline, setOffline] = useState(false);
  const [connecting, setConnecting] = useState(false);

  // Step 2: mapping, seeded from the connect response.
  const [matchName, setMatchName] = useState("");
  const [matchData, setMatchData] = useState<ScoreboardMatchData | null>(null);
  const [proposals, setProposals] = useState<LinkProposal[]>([]);
  const [stageMismatch, setStageMismatch] = useState(false);
  const [localStageCount, setLocalStageCount] = useState(0);
  const [scoreboardStageCount, setScoreboardStageCount] = useState(0);
  // slug -> picked competitor id, or null for "leave unlinked". Seeded
  // from the proposals but freely editable per row.
  const [rowPicks, setRowPicks] = useState<Record<string, number | null>>({});
  const [applying, setApplying] = useState(false);

  async function runSearch() {
    if (!query.trim()) {
      setResults([]);
      return;
    }
    setSearching(true);
    setOffline(false);
    setError(null);
    try {
      const resp = await fetch(
        `/api/scoreboard/search?q=${encodeURIComponent(query)}`,
        { headers: { Accept: "application/json" } },
      );
      if (resp.status === 401 || resp.status === 429 || resp.status === 502) {
        setOffline(true);
        setResults([]);
        return;
      }
      if (!resp.ok) {
        setError(`Search failed: ${resp.status}`);
        setResults([]);
        return;
      }
      const body = (await resp.json()) as ScoreboardMatchRef[];
      setResults(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSearching(false);
    }
  }

  async function connect(result: ScoreboardMatchRef) {
    setConnecting(true);
    setError(null);
    try {
      const connectResp = await api.connectScoreboardMatch(
        result.id,
        result.content_type,
      );
      const data = await api.getScoreboardMatchDataUnbound(
        result.content_type,
        result.id,
      );
      setMatchName(result.name);
      setMatchData(data);
      setProposals(connectResp.proposals);
      setStageMismatch(connectResp.stage_mismatch);
      setLocalStageCount(connectResp.local_stage_count);
      setScoreboardStageCount(connectResp.scoreboard_stage_count);
      const picks: Record<string, number | null> = {};
      for (const p of connectResp.proposals) {
        picks[p.slug] = p.competitor_id;
      }
      setRowPicks(picks);
      setStep("map");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setConnecting(false);
    }
  }

  async function apply() {
    if (!matchData) return;
    const links: { slug: string; shooter_id: number; competitor_id: number }[] =
      [];
    for (const s of shooters) {
      const competitorId = rowPicks[s.slug];
      if (competitorId == null) continue;
      const competitor = matchData.competitors.find(
        (c) => c.id === competitorId,
      );
      if (!competitor) continue;
      links.push({
        slug: s.slug,
        shooter_id: competitor.shooterId,
        competitor_id: competitor.id,
      });
    }
    setApplying(true);
    setError(null);
    try {
      await api.reconcileScoreboardLinks(links);
      onApplied();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setApplying(false);
    }
  }

  return (
    <Portal>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Connect to scoreboard"
        className="fixed inset-0 z-modal flex items-center justify-center bg-bg/70 p-4 backdrop-blur-sm"
        onClick={onClose}
      >
        <div
          ref={panelRef}
          tabIndex={-1}
          className="relative flex h-[min(720px,90vh)] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-rule-strong bg-surface text-ink shadow-[0_24px_48px_-12px_rgba(0,0,0,0.7)] outline-none"
          onClick={(e) => e.stopPropagation()}
        >
          <header className="flex items-center justify-between gap-4 border-b border-rule px-5 py-3.5">
            <div>
              <h2 className="font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
                Connect to scoreboard
              </h2>
              <p className="mt-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
                {step === "search"
                  ? "Step 1 -- find the event"
                  : "Step 2 -- confirm shooter links"}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded-md p-1.5 text-subtle hover:bg-surface-2 hover:text-ink"
            >
              <X className="size-4" />
            </button>
          </header>

          {error && (
            <div className="mx-5 mt-4 rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
              {error}
            </div>
          )}

          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-5 py-4">
            {step === "search" ? (
              <SearchStep
                query={query}
                setQuery={setQuery}
                results={results}
                searching={searching}
                offline={offline}
                connecting={connecting}
                onSearch={() => void runSearch()}
                onPick={(r) => void connect(r)}
              />
            ) : (
              <MapStep
                matchName={matchName}
                shooters={shooters}
                competitors={matchData?.competitors ?? []}
                proposals={proposals}
                rowPicks={rowPicks}
                onPick={(slug, competitorId) =>
                  setRowPicks((prev) => ({ ...prev, [slug]: competitorId }))
                }
                stageMismatch={stageMismatch}
                localStageCount={localStageCount}
                scoreboardStageCount={scoreboardStageCount}
                onBack={() => {
                  setStep("search");
                  setMatchData(null);
                  setProposals([]);
                }}
              />
            )}
          </div>

          <footer className="flex items-center justify-between border-t border-rule px-5 py-3.5">
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            {step === "map" && (
              <Button
                type="button"
                onClick={() => void apply()}
                disabled={applying}
                className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink"
              >
                <span className="font-display uppercase tracking-[0.08em]">
                  {applying ? "Linking..." : "Confirm links"}
                </span>
              </Button>
            )}
          </footer>
        </div>
      </div>
    </Portal>
  );
}

function SearchStep({
  query,
  setQuery,
  results,
  searching,
  offline,
  connecting,
  onSearch,
  onPick,
}: {
  query: string;
  setQuery: (v: string) => void;
  results: ScoreboardMatchRef[] | null;
  searching: boolean;
  offline: boolean;
  connecting: boolean;
  onSearch: () => void;
  onPick: (r: ScoreboardMatchRef) => void;
}) {
  return (
    <div>
      <p className="mb-4 text-[0.8125rem] text-muted">
        Search scoreboard.urdr.dev for this match. Picking an event links
        every current shooter and proposes name-based competitor matches
        you confirm next.
      </p>
      <label className="flex min-h-11 items-center gap-2.5 rounded-lg border border-rule bg-surface-3 px-3.5 py-2.5 transition-colors focus-within:border-led focus-within:bg-bg-glow focus-within:shadow-[0_0_0_3px_var(--color-led-tint)]">
        <Search aria-hidden className="size-4 text-subtle" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onSearch();
            }
          }}
          placeholder="Search matches... (Enter to search)"
          className="flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-subtle"
        />
      </label>

      {offline && (
        <div className="mt-4 rounded-lg border border-live/40 bg-live/10 px-4 py-3 text-sm text-ink-2">
          <div className="mb-1 font-display text-xs font-bold uppercase tracking-[0.1em] text-live">
            Scoreboard unavailable
          </div>
          <p className="text-[0.8125rem] text-muted">
            The scoreboard isn't reachable right now -- check the
            SPLITSMITH_SSI_TOKEN env var or your network, then try again.
          </p>
        </div>
      )}

      {results !== null && results.length > 0 && (
        <div className="mt-4 overflow-hidden rounded-[10px] border border-rule bg-bg-glow">
          {results.map((r) => (
            <ResultRow
              key={`${r.content_type}-${r.id}`}
              result={r}
              selected={false}
              onSelect={() => onPick(r)}
            />
          ))}
        </div>
      )}

      {results !== null && results.length === 0 && !offline && (
        <div className="mt-4 rounded-md border border-dashed border-rule px-4 py-6 text-center text-sm text-muted">
          {searching ? "Searching..." : "No matches for that search."}
        </div>
      )}

      {connecting && (
        <div className="mt-4 flex items-center gap-2 font-mono text-xs uppercase tracking-[0.08em] text-muted">
          <Loader2 className="size-3.5 animate-spin" />
          Linking match and proposing shooter links...
        </div>
      )}
    </div>
  );
}

function MapStep({
  matchName,
  shooters,
  competitors,
  proposals,
  rowPicks,
  onPick,
  stageMismatch,
  localStageCount,
  scoreboardStageCount,
  onBack,
}: {
  matchName: string;
  shooters: ShooterListEntry[];
  competitors: ScoreboardMatchCompetitor[];
  proposals: LinkProposal[];
  rowPicks: Record<string, number | null>;
  onPick: (slug: string, competitorId: number | null) => void;
  stageMismatch: boolean;
  localStageCount: number;
  scoreboardStageCount: number;
  onBack: () => void;
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onBack}
        className="mb-3 inline-flex items-center gap-1.5 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-subtle hover:text-ink-2"
      >
        <ArrowLeft className="size-3" />
        Pick a different event
      </button>
      <p className="mb-4 text-[0.8125rem] text-muted">
        Linked to <b className="font-bold text-ink">{matchName}</b>. Review
        the proposed shooter links below -- ambiguous or unmatched rows
        default to unlinked; pick a competitor or leave them alone.
      </p>

      {stageMismatch && (
        <div className="mb-4 rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
          Local stages ({localStageCount}) do not line up with the
          scoreboard ({scoreboardStageCount}); scores attach by stage
          number.
        </div>
      )}

      <div className="mb-1 grid gap-3 px-1 font-mono text-[0.625rem] font-bold uppercase tracking-[0.1em] text-subtle">
        <div
          className="grid gap-3"
          style={{ gridTemplateColumns: "1fr 1fr" }}
        >
          <span>Local shooter</span>
          <span>Scoreboard competitor</span>
        </div>
      </div>
      <div className="overflow-hidden rounded-[10px] border border-rule bg-bg-glow">
        {shooters.length === 0 ? (
          <div className="px-4 py-8 text-center font-mono text-xs uppercase tracking-[0.08em] text-muted">
            No shooters to link yet.
          </div>
        ) : (
          shooters.map((s) => {
            const picked = rowPicks[s.slug] ?? null;
            const proposal = proposals.find((p) => p.slug === s.slug) ?? null;
            return (
              <div
                key={s.slug}
                className="grid items-center gap-3 border-b border-rule px-4 py-2.5 last:border-b-0"
                style={{ gridTemplateColumns: "1fr 1fr" }}
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-ink">
                    {s.name}
                  </div>
                  {proposal?.ambiguous && picked != null && (
                    <div className="mt-0.5 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-beep">
                      Ambiguous match -- double check
                    </div>
                  )}
                </div>
                <select
                  value={picked ?? ""}
                  onChange={(e) =>
                    onPick(
                      s.slug,
                      e.target.value === "" ? null : Number(e.target.value),
                    )
                  }
                  className={cn(
                    "w-full rounded-md border bg-surface-3 px-2.5 py-1.5 text-sm text-ink outline-none focus:border-led focus:shadow-[0_0_0_3px_var(--color-led-tint)]",
                    proposal?.ambiguous && picked != null
                      ? "border-beep/60"
                      : "border-rule",
                  )}
                >
                  <option value="">Leave unlinked</option>
                  {competitors.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                      {c.division ? ` -- ${c.division}` : ""}
                    </option>
                  ))}
                </select>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

/** Small inline "connect" CTA -- rendered by the caller wherever the
 *  entry point makes sense (Shooters.tsx today) when
 *  ``project.scoreboard_match_id`` is null. Exported so the copy /
 *  styling stays in one place. */
export function ConnectMatchButton({ onClick }: { onClick: () => void }) {
  return (
    <Button
      type="button"
      onClick={onClick}
      className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink"
    >
      <Link2 className="size-3.5" />
      <span className="font-display uppercase tracking-[0.08em]">
        Connect to scoreboard
      </span>
    </Button>
  );
}
