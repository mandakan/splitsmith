/**
 * CreateMatch route (/pick/new) -- two-variant create-match flow (#322).
 *
 * Renders both variants from polished/04-create-match.html in the
 * Shot Timer aesthetic:
 *
 *   A. From scoreboard -- search scoreboard.urdr.dev, pick the match,
 *      pick the primary competitor, set the project folder, fetch.
 *      Degrades gracefully when offline (the search endpoint already
 *      returns a structured error; we surface it inline). Per the
 *      "scoreboard is convenience, not foundation" rule we let the user
 *      fall back to manual without leaving the page.
 *
 *   B. Manual -- a stage editor + primary-shooter section. The scaffold
 *      is created via POST /api/match/create-manual and the SPA is
 *      navigated to the home page so the user lands on what will become
 *      the empty match-overview surface (#323) once that ships.
 *
 * Both variants share the page chrome and the segmented [Scoreboard | Manual]
 * toggle, mirroring the polished reference.
 */

import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  FolderOpen,
  Plus,
  Search,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { DirectoryPickerModal } from "@/components/DirectoryPickerModal";
import { CompetitorRow } from "@/components/scoreboard/CompetitorRow";
import { ResultRow } from "@/components/scoreboard/ResultRow";
import { Brand, Kicker } from "@/components/ui";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  api,
  type CreateMatchCompetitorPick,
  type CreateMatchStageDraft,
  type ScoreboardMatchCompetitor,
  type ScoreboardMatchData,
  type ScoreboardMatchRef,
} from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import { slugify } from "@/lib/slugify";
import { cn } from "@/lib/utils";

type Variant = "scoreboard" | "manual";

// Wire shape mirrors ``splitsmith.ui.scoreboard.models.MatchRef`` --
// reuse the type from api.ts instead of redefining (the previous local
// interface drifted -- ``match_id``/``match_date``/``club`` -- which
// made every row look "selected" because every field was undefined).
type ScoreboardSearchResult = ScoreboardMatchRef;

const DEFAULT_PARENT_DIR = "~/Splitsmith";

/** Resolve a parent dir + project name into the absolute project_folder
 *  string the backend accepts. Trailing/leading slashes on the parent
 *  are normalised. */
function projectFolderPath(parentDir: string, slug: string): string {
  const trimmed = parentDir.replace(/\/+$/, "");
  return `${trimmed || "."}/${slug}`;
}

const DIVISIONS = [
  "Production Optics",
  "Production",
  "Open",
  "Standard",
  "Classic",
  "Revolver",
];

export function CreateMatch() {
  const navigate = useNavigate();
  const [variant, setVariant] = useState<Variant>("manual");
  const [error, setError] = useState<string | null>(null);

  return (
    <div
      className="relative min-h-screen text-ink"
      style={{
        backgroundImage:
          "radial-gradient(1400px 600px at 50% -100px, rgba(255,45,45,0.04), transparent 60%), linear-gradient(to bottom, var(--color-bg-glow), var(--color-bg))",
        backgroundAttachment: "fixed",
      }}
    >
      <header className="sticky top-0 z-chrome border-b border-rule bg-gradient-to-b from-surface to-bg">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 -bottom-px h-px"
          style={{
            background:
              "linear-gradient(to right, transparent, var(--color-led) 18%, var(--color-led) 22%, var(--color-rule-strong) 30%, var(--color-rule-strong) 70%, var(--color-led) 78%, var(--color-led) 82%, transparent)",
            opacity: 0.55,
          }}
        />
        <div className="mx-auto flex max-w-[1100px] items-center gap-6 px-8 py-3.5">
          <Brand variant="compact" />
          <div className="ml-auto" />
        </div>
        <div className="border-t border-rule bg-bg">
          <div className="mx-auto flex max-w-[1100px] items-center gap-3 px-8 py-2.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-subtle">
            <button
              type="button"
              // Replace, not push: /pick/new is a transient form. If we
              // pushed /pick here the back stack ends up [..., /pick,
              // /pick/new, /pick] and a later open-match (which replaces
              // top with /) leaves /pick/new lurking under /, so the
              // browser back button lands on the form instead of the
              // picker.
              onClick={() => navigate("/pick", { replace: true })}
              className="inline-flex items-center gap-1.5 text-subtle transition-colors hover:text-ink-2"
            >
              <ArrowLeft className="size-3" />
              Matches
            </button>
            <span className="text-whisper">/</span>
            <span className="font-semibold text-ink">New match</span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1100px] px-8 pb-20 pt-10">
        <div className="mb-7">
          <Kicker className="mb-3">New match &middot; setup</Kicker>
          <h1 className="mb-3 font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
            Create a match
          </h1>
          <p className="max-w-[40rem] text-[0.9375rem] text-muted">
            Import from{" "}
            <span className="font-mono text-ink-2">scoreboard.urdr.dev</span>{" "}
            to auto-fill stages and shooters, or build it manually for
            matches without scoreboard data.
          </p>
        </div>

        <div
          className="mb-5 inline-flex rounded-[9px] border border-rule bg-surface-2 p-0.5"
          role="tablist"
        >
          <SourceTab
            active={variant === "scoreboard"}
            onClick={() => setVariant("scoreboard")}
          >
            <Search className="size-3.5" />
            From scoreboard
          </SourceTab>
          <SourceTab
            active={variant === "manual"}
            onClick={() => setVariant("manual")}
          >
            <Plus className="size-3.5" />
            Manual setup
          </SourceTab>
        </div>

        {error ? (
          <div className="mb-4 rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
            {error}
          </div>
        ) : null}

        {variant === "scoreboard" ? (
          <ScoreboardVariant
            onError={setError}
            onSwitchToManual={() => setVariant("manual")}
          />
        ) : (
          <ManualVariant onError={setError} />
        )}
      </main>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Source tab                                                                 */
/* -------------------------------------------------------------------------- */

function SourceTab({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "inline-flex min-h-10 items-center gap-2 rounded-md px-4 py-2 font-display text-xs font-semibold uppercase tracking-[0.1em] transition-all",
        active
          ? "bg-ink font-bold text-bg"
          : "text-muted hover:text-ink-2",
      )}
    >
      {children}
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Scoreboard variant                                                         */
/* -------------------------------------------------------------------------- */

function ScoreboardVariant({
  onError,
  onSwitchToManual,
}: {
  onError: (e: string | null) => void;
  onSwitchToManual: () => void;
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ScoreboardSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [offline, setOffline] = useState(false);
  const [selected, setSelected] = useState<ScoreboardSearchResult | null>(null);
  const [matchData, setMatchData] = useState<ScoreboardMatchData | null>(null);
  const [loadingRoster, setLoadingRoster] = useState(false);
  const [competitorFilter, setCompetitorFilter] = useState("");
  // competitor.id -> checked. No "me" / operator concept here -- the
  // operator running the app may be coaching and not a shooter at all
  // (see issue #350).
  const [checked, setChecked] = useState<Set<number>>(new Set());
  const [parentDir, setParentDir] = useState<string>(DEFAULT_PARENT_DIR);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  // In hosted mode the server picks the project folder
  // (``users/<user_id>/projects/<slug>/``); the picker UI is suppressed
  // because there's no useful host filesystem inside the container.
  const deploymentMode = useDeploymentMode();
  const hostedMode = deploymentMode === "hosted";
  // Division facet -- ``null`` means "any". Selected division narrows the
  // accordion list to a single group. Squad isn't on the wire today, so
  // division does double duty as both the facet chip row and the
  // accordion grouping axis.
  const [divisionFacet, setDivisionFacet] = useState<string | null>(null);
  // Open/closed state per division accordion. Defaults are decided after
  // roster load: all open when the roster is small (<=40), all collapsed
  // otherwise so the operator scans group headers first.
  const [openDivisions, setOpenDivisions] = useState<Record<string, boolean>>({});
  // Project folder slug. Defaults to slugify(matchName) when a match is
  // picked; editable via the inline input below the parent-dir picker.
  // Validated on blur (kebab-case, deduped against on-disk match dirs).
  const [slug, setSlug] = useState("");
  const [slugDirty, setSlugDirty] = useState(false);
  const [slugError, setSlugError] = useState<string | null>(null);
  // Leaf names of existing match dirs, harvested from /api/me/recent-projects
  // -- used so the slug validation can flag "name already taken" before
  // the operator hits Create. Best-effort: if recents is empty or the
  // fetch fails, we still let them submit and let the backend complain.
  const [existingLeaves, setExistingLeaves] = useState<Set<string>>(new Set());
  const step2Ref = useRef<HTMLDivElement | null>(null);

  // Scroll step 2 into view when the user picks a match (the detail
  // panel renders below the results list, which can land off-screen).
  useEffect(() => {
    if (selected && step2Ref.current) {
      step2Ref.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [selected?.id]);

  // Auto-load the match roster when the user picks a match. Reset
  // step-2 state so re-picking a different match doesn't carry stale
  // selections.
  useEffect(() => {
    if (!selected) {
      setMatchData(null);
      setChecked(new Set());
      return;
    }
    setMatchData(null);
    setChecked(new Set());
    setLoadingRoster(true);
    let cancelled = false;
    (async () => {
      try {
        const data = await api.getScoreboardMatchDataUnbound(
          selected.content_type,
          selected.id,
        );
        if (cancelled) return;
        setMatchData(data);
      } catch (e) {
        if (!cancelled) {
          onError(e instanceof ApiError ? e.detail : String(e));
        }
      } finally {
        if (!cancelled) setLoadingRoster(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected?.id, selected?.content_type, onError]);

  // Default the slug from the picked match name. Once the operator
  // edits it (``slugDirty``) we stop auto-overwriting on subsequent
  // match picks -- they're presumably keeping their custom name.
  useEffect(() => {
    if (!selected) {
      setSlug("");
      setSlugDirty(false);
      setSlugError(null);
      return;
    }
    if (!slugDirty) {
      setSlug(slugify(selected.name));
      setSlugError(null);
    }
  }, [selected?.id, selected?.content_type, slugDirty]);

  // Harvest the leaf name of every known match dir from recents so we
  // can flag duplicates inline. Fire once on mount; if the user creates
  // a match and bounces back to this page, the next mount picks up the
  // new entry. Quiet on failure -- this is a hint, not a gate.
  useEffect(() => {
    let cancelled = false;
    api
      .getRecentProjectsDetail()
      .then((projects) => {
        if (cancelled) return;
        const leaves = new Set<string>();
        for (const p of projects) {
          const leaf = p.path.split("/").filter(Boolean).pop();
          if (leaf) leaves.add(leaf);
        }
        setExistingLeaves(leaves);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const projectFolder = useMemo(
    () => (slug ? projectFolderPath(parentDir, slug) : ""),
    [parentDir, slug],
  );

  // Re-default accordion open state when a new roster arrives. Auto-
  // collapse for rosters > 40 so the operator scans group headers first;
  // small matches stay expanded so checkboxes are immediately reachable.
  useEffect(() => {
    if (!matchData) return;
    const divisions = new Set<string>();
    for (const c of matchData.competitors) {
      divisions.add(c.division ?? "Unknown division");
    }
    const expand = matchData.competitors.length <= 40;
    const next: Record<string, boolean> = {};
    for (const d of divisions) next[d] = expand;
    setOpenDivisions(next);
    setDivisionFacet(null);
  }, [matchData]);

  // ENTER triggers a search against the unbound /api/scoreboard/search
  // endpoint. Any backend failure (no SSI token, scoreboard down, rate
  // limited) routes through the same fallback CTA so the user can pivot
  // to manual setup without leaving the page.
  async function runSearch() {
    if (!query.trim()) {
      setResults([]);
      return;
    }
    setSearching(true);
    setOffline(false);
    onError(null);
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
        onError(`Search failed: ${resp.status}`);
        setResults([]);
        return;
      }
      const body = (await resp.json()) as ScoreboardSearchResult[];
      setResults(body);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSearching(false);
    }
  }

  function toggleChecked(id: number) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const competitors = matchData?.competitors ?? [];
  const filteredCompetitors = useMemo(() => {
    const q = competitorFilter.trim().toLowerCase();
    return competitors.filter((c) => {
      if (divisionFacet && (c.division ?? "Unknown division") !== divisionFacet) {
        return false;
      }
      if (!q) return true;
      const hay = `${c.name} ${c.club ?? ""} ${c.division ?? ""}`.toLowerCase();
      return hay.includes(q);
    });
  }, [competitors, competitorFilter, divisionFacet]);

  // Distinct divisions in roster order. Used by the facet chip row to
  // surface the meaningful categories without us having to predict them.
  // ``count`` is total roster size in that division (not the filtered
  // count) so the chip badge stays stable while the operator types.
  const divisionFacets = useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of competitors) {
      const d = c.division ?? "Unknown division";
      counts.set(d, (counts.get(d) ?? 0) + 1);
    }
    return Array.from(counts.entries()).map(([division, count]) => ({
      division,
      count,
    }));
  }, [competitors]);

  // Group the *filtered* roster by division so the accordion bodies
  // shrink as the operator types into the search box. When the active
  // facet narrows to a single division this is just one group.
  const groupedFiltered = useMemo(() => {
    const groups = new Map<string, ScoreboardMatchCompetitor[]>();
    for (const c of filteredCompetitors) {
      const d = c.division ?? "Unknown division";
      const arr = groups.get(d) ?? [];
      arr.push(c);
      groups.set(d, arr);
    }
    return Array.from(groups.entries());
  }, [filteredCompetitors]);

  // Slug validation. Kebab-cased pattern + dedup against on-disk match
  // dirs. Returns ``null`` when the value is acceptable, otherwise a
  // short message for the inline error string.
  function validateSlug(s: string): string | null {
    const trimmed = s.trim();
    if (!trimmed) return "Folder name can't be empty";
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(trimmed)) {
      return "Use kebab-case: lowercase letters, digits, hyphens";
    }
    if (existingLeaves.has(trimmed)) return "A match folder by that name already exists";
    return null;
  }

  const canCreate =
    !!selected &&
    !!matchData &&
    checked.size > 0 &&
    (hostedMode || parentDir.trim().length > 0) &&
    slug.trim().length > 0 &&
    slugError == null &&
    !creating;

  async function create() {
    if (!selected || !matchData) return;
    const picks: CreateMatchCompetitorPick[] = [];
    for (const c of matchData.competitors) {
      if (!checked.has(c.id)) continue;
      picks.push({
        name: c.name,
        division: c.division,
        selected_shooter_id: c.shooterId,
        selected_competitor_id: c.id,
      });
    }
    if (picks.length === 0) {
      onError("Pick at least one shooter to add.");
      return;
    }
    setCreating(true);
    onError(null);
    try {
      const health = await api.createMatchFromScoreboard({
        // Hosted mode: server picks the path; the SPA doesn't have a
        // useful host filesystem to point at. See #425.
        project_folder: hostedMode ? null : projectFolder,
        name: selected.name,
        match_id: selected.id,
        content_type: selected.content_type,
        competitors: picks,
      });
      // Use the match_id from the create response so we land on the
      // canonical /match/:matchId/ route. Navigating to "/" would
      // bounce via LegacyMatchRedirect through /api/health, which no
      // longer carries bound-state (doc 10 Tier 1 step 4) and so falls
      // through to /pick.
      navigate(health.match_id ? `/match/${health.match_id}/` : "/pick", {
        replace: true,
      });
    } catch (e) {
      setCreating(false);
      onError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_24px_48px_-24px_rgba(0,0,0,0.7)]">
      {/* Step 1: locate */}
      <Section eyebrow="01 Locate match" title="Find the match on scoreboard.urdr.dev">
        <p className="-mt-1 mb-4 max-w-xl text-[0.8125rem] text-muted">
          Search by name, club, or date. Level II+ matches shown by default.
        </p>

        <label
          className="flex min-h-11 items-center gap-2.5 rounded-lg border border-rule bg-surface-3 px-3.5 py-2.5 transition-colors focus-within:border-led focus-within:bg-bg-glow focus-within:shadow-[0_0_0_3px_var(--color-led-tint)]"
        >
          <Search aria-hidden className="size-4 text-subtle" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void runSearch();
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
              SPLITSMITH_SSI_TOKEN env var or your network. You can
              still create the match by{" "}
              <button
                type="button"
                onClick={onSwitchToManual}
                className="font-mono text-led hover:text-led-soft"
              >
                using manual setup
              </button>
              .
            </p>
          </div>
        )}

        {results !== null && results.length > 0 && (
          <div className="mt-4">
            <div className="mb-3 flex items-center justify-between font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-subtle">
              <span>
                <b className="font-bold text-ink-2">{results.length}</b>{" "}
                matches found
              </span>
              <span className="text-subtle">
                Level II and above &middot;{" "}
                <button type="button" className="text-led hover:text-led-soft">
                  include club matches
                </button>
              </span>
            </div>

            <div className="overflow-hidden rounded-[10px] border border-rule bg-bg-glow">
              {results.map((r) => (
                <ResultRow
                  key={`${r.content_type}-${r.id}`}
                  result={r}
                  selected={
                    selected?.id === r.id &&
                    selected?.content_type === r.content_type
                  }
                  onSelect={() => setSelected(r)}
                />
              ))}
            </div>
          </div>
        )}

        {results !== null && results.length === 0 && !offline && (
          <div className="mt-4 rounded-md border border-dashed border-rule px-4 py-6 text-center text-sm text-muted">
            {searching ? "Searching..." : "No matches for that search."}
          </div>
        )}

      </Section>

      {selected && (
        <div ref={step2Ref}>
          <Section
            eyebrow="02 Pick shooters"
            title={`Add shooters from ${selected.name}`}
          >
            <p className="-mt-1 mb-4 max-w-2xl text-[0.8125rem] text-muted">
              Pick every shooter to include in this match. Stage times are
              fetched for each selected shooter on create.
            </p>

            <label className="flex min-h-10 items-center gap-2.5 rounded-lg border border-rule bg-surface-3 px-3.5 py-2 transition-colors focus-within:border-led focus-within:bg-bg-glow focus-within:shadow-[0_0_0_3px_var(--color-led-tint)]">
              <Search aria-hidden className="size-4 text-subtle" />
              <input
                type="text"
                value={competitorFilter}
                onChange={(e) => setCompetitorFilter(e.target.value)}
                placeholder="Filter by name, club, or division..."
                className="flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-subtle"
              />
              {competitorFilter && (
                <button
                  type="button"
                  onClick={() => setCompetitorFilter("")}
                  className="rounded p-0.5 text-subtle hover:bg-surface-2 hover:text-ink"
                  aria-label="Clear filter"
                >
                  <X className="size-3.5" />
                </button>
              )}
            </label>

            {/* Division facets. A single ``All`` chip plus one chip per
                division. Clicking a division narrows the accordion list
                to that one group; clicking the active chip (or All)
                clears the facet. */}
            {divisionFacets.length > 1 && (
              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                <FacetChip
                  active={divisionFacet == null}
                  onClick={() => setDivisionFacet(null)}
                  count={competitors.length}
                >
                  All
                </FacetChip>
                {divisionFacets.map((f) => (
                  <FacetChip
                    key={f.division}
                    active={divisionFacet === f.division}
                    onClick={() =>
                      setDivisionFacet(
                        divisionFacet === f.division ? null : f.division,
                      )
                    }
                    count={f.count}
                  >
                    {f.division}
                  </FacetChip>
                ))}
              </div>
            )}

            <div className="mt-3 flex items-center justify-between font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-subtle">
              <span>
                {loadingRoster ? (
                  <span>Loading roster...</span>
                ) : (
                  <>
                    <b className="font-bold text-ink-2">{checked.size}</b> of{" "}
                    {competitors.length} selected
                  </>
                )}
              </span>
              {competitors.length > 0 && (
                <span>
                  {filteredCompetitors.length === competitors.length
                    ? `${competitors.length} shooters`
                    : `${filteredCompetitors.length} shown · ${competitors.length} total`}
                </span>
              )}
            </div>

            <div className="mt-2 max-h-96 overflow-y-auto rounded-[10px] border border-rule bg-bg-glow">
              {loadingRoster ? (
                <div className="px-4 py-8 text-center font-mono text-xs uppercase tracking-[0.08em] text-muted">
                  Loading roster from scoreboard...
                </div>
              ) : filteredCompetitors.length === 0 ? (
                <div className="px-4 py-8 text-center font-mono text-xs uppercase tracking-[0.08em] text-muted">
                  {competitors.length === 0
                    ? "No competitors on this match yet."
                    : "No shooters match that filter."}
                </div>
              ) : (
                groupedFiltered.map(([division, rows]) => {
                  const selectedInGroup = rows.filter((c) =>
                    checked.has(c.id),
                  ).length;
                  const open = openDivisions[division] ?? true;
                  return (
                    <DivisionAccordion
                      key={division}
                      division={division}
                      count={rows.length}
                      selected={selectedInGroup}
                      open={open}
                      onToggle={() =>
                        setOpenDivisions((prev) => ({
                          ...prev,
                          [division]: !open,
                        }))
                      }
                      onSelectAll={() => {
                        setChecked((prev) => {
                          const next = new Set(prev);
                          for (const c of rows) next.add(c.id);
                          return next;
                        });
                      }}
                      onClear={() => {
                        setChecked((prev) => {
                          const next = new Set(prev);
                          for (const c of rows) next.delete(c.id);
                          return next;
                        });
                      }}
                    >
                      {open
                        ? rows.map((c) => (
                            <CompetitorRow
                              key={c.id}
                              competitor={c}
                              checked={checked.has(c.id)}
                              onToggle={() => toggleChecked(c.id)}
                            />
                          ))
                        : null}
                    </DivisionAccordion>
                  );
                })
              )}
            </div>

            {/* Sticky selection footer. Lives in normal flow but
                ``position: sticky`` so it pins to the viewport bottom
                while the operator scrolls section 02. The Create
                button is rendered at the bottom of the form, but the
                running tally is more useful next to the list. */}
            <div className="sticky bottom-3 z-10 mt-3 flex items-center justify-between rounded-lg border border-rule-strong bg-surface/95 px-4 py-2.5 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-ink-2 backdrop-blur">
              <span>
                <b
                  className={cn(
                    "font-bold",
                    checked.size > 0 ? "text-led-text" : "text-muted",
                  )}
                >
                  {checked.size}
                </b>{" "}
                of {competitors.length} selected
              </span>
              {checked.size > 0 && (
                <button
                  type="button"
                  onClick={() => setChecked(new Set())}
                  className="text-subtle transition-colors hover:text-ink-2"
                >
                  Clear selection
                </button>
              )}
            </div>
          </Section>

          <Section eyebrow="03 Project folder" title="Where to save this match">
            <p className="-mt-1 mb-4 max-w-2xl text-[0.8125rem] text-muted">
              {hostedMode
                ? "The hosted server picks the storage location for you. The match name still drives the folder slug below; edit it if you'd like a different one."
                : "The match folder will be created as a new directory inside the parent you pick. The leaf defaults to the match name in kebab-case; edit it if you'd like a different folder name."}
            </p>

            <div className="flex flex-col gap-3 rounded-lg border border-rule bg-surface-3 px-4 py-3.5">
              {!hostedMode && (
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-mono text-[0.625rem] uppercase tracking-[0.1em] text-subtle">
                      Parent folder
                    </div>
                    <div className="mt-0.5 truncate font-mono text-[0.8125rem] tabular-nums text-ink-2">
                      {parentDir}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setPickerOpen(true)}
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-rule-strong bg-surface-2 px-3 py-1.5 font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:border-led-deep hover:bg-led-tint hover:text-led"
                  >
                    <FolderOpen className="size-3.5" />
                    Change...
                  </button>
                </div>
              )}
              <div className={cn(hostedMode ? "" : "border-t border-rule pt-3")}>
                <label className="block font-mono text-[0.625rem] uppercase tracking-[0.1em] text-subtle">
                  Folder name
                </label>
                <input
                  type="text"
                  value={slug}
                  onChange={(e) => {
                    setSlug(e.target.value);
                    setSlugDirty(true);
                    // Don't surface the error while the operator is
                    // still typing -- only on blur. Clear any stale
                    // message so they don't see "duplicate" linger
                    // after they edit it away.
                    if (slugError) setSlugError(null);
                  }}
                  onBlur={() => {
                    // Normalise to kebab-case on blur so a user who types
                    // "Bromma Classifier 2026" gets snapped to the canonical
                    // form (matches what the backend would have used) and
                    // can then accept or override.
                    const normalised = slugify(slug);
                    if (normalised !== slug) setSlug(normalised);
                    setSlugError(validateSlug(normalised));
                  }}
                  spellCheck={false}
                  aria-invalid={slugError != null}
                  aria-describedby={slugError ? "slug-error" : undefined}
                  className={cn(
                    "mt-1 w-full rounded-md border bg-surface-2 px-3 py-2 font-mono text-[0.8125rem] tabular-nums text-ink outline-none transition-colors",
                    slugError
                      ? "border-led shadow-[0_0_0_3px_var(--color-led-tint)]"
                      : "border-rule focus:border-led focus:shadow-[0_0_0_3px_var(--color-led-tint)]",
                  )}
                />
                {slugError && (
                  <p
                    id="slug-error"
                    className="mt-1.5 font-mono text-[0.6875rem] text-led-text"
                  >
                    {slugError}
                  </p>
                )}
              </div>
              {!hostedMode && (
                <div className="border-t border-rule pt-3">
                  <div className="font-mono text-[0.625rem] uppercase tracking-[0.1em] text-subtle">
                    Will create
                  </div>
                  <div className="mt-0.5 truncate font-mono text-[0.8125rem] tabular-nums text-ink">
                    {projectFolder}/
                  </div>
                </div>
              )}
            </div>
          </Section>

          <Section eyebrow="Preview">
            <div className="flex flex-wrap items-center gap-3.5 rounded-[10px] border border-done bg-gradient-to-r from-done/10 to-done/[0.02] px-4 py-3 font-mono text-xs uppercase tracking-[0.06em] text-ink-2">
              <span className="inline-flex size-6 items-center justify-center rounded-full bg-done text-bg shadow-[0_0_10px_var(--color-done-glow)]">
                <Check className="size-3.5" strokeWidth={3} />
              </span>
              <span>
                {canCreate ? (
                  <>
                    Ready to create &middot;{" "}
                    <b className="font-bold text-done">{checked.size}</b>{" "}
                    shooter{checked.size === 1 ? "" : "s"} &middot; stage times will
                    be fetched for each
                  </>
                ) : (
                  <span className="text-muted">
                    Pick at least one shooter above.
                  </span>
                )}
              </span>
            </div>
          </Section>

          <FooterActions
            onCancel={() => navigate("/pick", { replace: true })}
            primary={
              <Button
                type="button"
                onClick={() => void create()}
                disabled={!canCreate}
                className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_16px_var(--color-led-glow)] hover:bg-led hover:text-ink"
              >
                <span className="font-display uppercase tracking-[0.08em]">
                  {creating ? "Creating..." : "Create match"}
                </span>
                <ArrowRight className="size-3.5" />
              </Button>
            }
          />
        </div>
      )}

      {pickerOpen && !hostedMode && (
        <DirectoryPickerModal
          initialPath={parentDir.startsWith("~") ? null : parentDir}
          onSelect={(picked) => {
            setParentDir(picked);
            setPickerOpen(false);
          }}
          onCancel={() => setPickerOpen(false)}
        />
      )}
    </div>
  );
}

function FacetChip({
  active,
  count,
  onClick,
  children,
}: {
  active: boolean;
  count: number;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-display text-[0.6875rem] font-bold uppercase tracking-[0.06em] transition-colors",
        active
          ? "border-led bg-led-tint text-led-text"
          : "border-rule bg-surface-3 text-ink-2 hover:border-rule-strong hover:bg-surface-2",
      )}
    >
      <span>{children}</span>
      <span
        className={cn(
          "font-mono text-[0.625rem] tabular-nums",
          active ? "text-led-text" : "text-muted",
        )}
      >
        {count}
      </span>
    </button>
  );
}

function DivisionAccordion({
  division,
  count,
  selected,
  open,
  onToggle,
  onSelectAll,
  onClear,
  children,
}: {
  division: string;
  count: number;
  selected: number;
  open: boolean;
  onToggle: () => void;
  onSelectAll: () => void;
  onClear: () => void;
  children: React.ReactNode;
}) {
  const allSelected = selected > 0 && selected === count;
  return (
    <div className="border-b border-rule last:border-b-0">
      <div className="flex items-center gap-2 bg-surface-2 px-3 py-2">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="flex flex-1 items-center gap-2 text-left transition-colors hover:text-ink"
        >
          <ChevronDown
            aria-hidden
            className={cn(
              "size-3.5 shrink-0 text-subtle transition-transform",
              open ? "rotate-0" : "-rotate-90",
            )}
          />
          <span className="font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink">
            {division}
          </span>
          <span className="font-mono text-[0.625rem] tabular-nums text-muted">
            {selected > 0 ? `${selected}/${count}` : count}
          </span>
        </button>
        <button
          type="button"
          onClick={allSelected ? onClear : onSelectAll}
          className="rounded border border-rule bg-surface-3 px-2 py-0.5 font-mono text-[0.625rem] uppercase tracking-[0.08em] text-ink-2 transition-colors hover:border-rule-strong hover:text-ink"
        >
          {allSelected ? "Clear" : "All"}
        </button>
      </div>
      {open ? children : null}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Manual variant                                                             */
/* -------------------------------------------------------------------------- */

function ManualVariant({
  onError,
}: {
  onError: (e: string | null) => void;
}) {
  const navigate = useNavigate();
  const deploymentMode = useDeploymentMode();
  const hostedMode = deploymentMode === "hosted";
  const today = new Date().toISOString().slice(0, 10);
  const [name, setName] = useState("");
  const [matchDate, setMatchDate] = useState(today);
  const [club, setClub] = useState("");
  const [matchType, setMatchType] = useState("Club match");
  const [defaultDivision, setDefaultDivision] = useState(DIVISIONS[0]);
  const [folder, setFolder] = useState("");
  const [stages, setStages] = useState<CreateMatchStageDraft[]>([
    {
      stage_number: 1,
      stage_name: "Stage 1",
      expected_rounds: 12,
      target_type: "Pistol IPSC",
    },
  ]);
  const [shooterName, setShooterName] = useState("");
  const [shooterDivision, setShooterDivision] = useState(DIVISIONS[0]);
  const [creating, setCreating] = useState(false);

  // Auto-suggest a folder path from the match name once typed.
  // Hosted mode skips the suggestion -- the server picks the path and
  // the input isn't rendered.
  useEffect(() => {
    if (hostedMode) return;
    if (!folder && name.trim()) {
      const slug = name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
      if (slug) setFolder(`~/Splitsmith/${slug}/`);
    }
  }, [name, folder, hostedMode]);

  const totalExpected = useMemo(
    () =>
      stages.reduce((sum, s) => sum + (Number(s.expected_rounds) || 0), 0),
    [stages],
  );

  function updateStage(index: number, patch: Partial<CreateMatchStageDraft>) {
    setStages((prev) =>
      prev.map((s, i) => (i === index ? { ...s, ...patch } : s)),
    );
  }

  function addStage() {
    setStages((prev) => [
      ...prev,
      {
        stage_number: (prev[prev.length - 1]?.stage_number ?? 0) + 1,
        stage_name: `Stage ${prev.length + 1}`,
        expected_rounds: 12,
        target_type: "Pistol IPSC",
      },
    ]);
  }

  function removeStage(index: number) {
    setStages((prev) =>
      prev
        .filter((_, i) => i !== index)
        .map((s, i) => ({ ...s, stage_number: i + 1 })),
    );
  }

  async function submit() {
    if (!name.trim() || !shooterName.trim()) {
      onError("Match name and shooter name are required.");
      return;
    }
    if (!hostedMode && !folder.trim()) {
      onError("Project folder is required in local mode.");
      return;
    }
    if (stages.length === 0) {
      onError("At least one stage is required.");
      return;
    }
    setCreating(true);
    onError(null);
    try {
      const health = await api.createMatchManual({
        name: name.trim(),
        // Hosted mode: server picks the path; see #425.
        project_folder: hostedMode ? null : folder.trim(),
        match_date: matchDate || null,
        club: club.trim() || null,
        match_type: matchType,
        default_division: defaultDivision,
        stages: stages.map((s, i) => ({
          stage_number: i + 1,
          stage_name: s.stage_name.trim() || `Stage ${i + 1}`,
          expected_rounds: s.expected_rounds ?? null,
          target_type: s.target_type ?? null,
        })),
        primary_shooter: {
          name: shooterName.trim(),
          division: shooterDivision,
        },
      });
      navigate(health.match_id ? `/match/${health.match_id}/` : "/pick", {
        replace: true,
      });
    } catch (e) {
      setCreating(false);
      onError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_24px_48px_-24px_rgba(0,0,0,0.7)]">
      <Section eyebrow="01 Match details" title="Match details">
        <div className="mt-2 grid gap-x-5 gap-y-4 sm:grid-cols-2">
          <FormField label="Match name" required>
            <TextInput
              value={name}
              onChange={setName}
              placeholder="Bromma club practice - draw drills"
            />
          </FormField>
          <FormField label="Date" required>
            <TextInput value={matchDate} onChange={setMatchDate} type="date" mono />
          </FormField>
          <FormField label="Club">
            <TextInput value={club} onChange={setClub} placeholder="Bromma PK" />
          </FormField>
          <FormField label="Type">
            <Select
              value={matchType}
              onChange={setMatchType}
              options={[
                "Club match",
                "Practice",
                "Level I",
                "Level II",
                "Level III",
                "Other",
              ]}
            />
          </FormField>
          <FormField label="Default division">
            <Select
              value={defaultDivision}
              onChange={setDefaultDivision}
              options={DIVISIONS}
            />
          </FormField>
          {!hostedMode && (
            <FormField label="Project folder">
              <TextInput
                value={folder}
                onChange={setFolder}
                placeholder="~/Splitsmith/<slug>/"
                mono
              />
            </FormField>
          )}
        </div>
      </Section>

      <Section eyebrow="02 Stages" title="Stages">
        <p className="-mt-1 mb-4 text-[0.8125rem] text-muted">
          Add a row per stage. Expected shots feed the stage-aware
          detector; the name is what shows up everywhere downstream.
        </p>

        <div className="overflow-hidden rounded-xl border border-rule bg-bg-glow">
          <div
            className="grid items-center gap-3.5 border-b border-rule bg-surface-2 px-4 py-2.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle"
            style={{
              gridTemplateColumns: "48px 1fr 130px 180px 36px",
            }}
          >
            <span>#</span>
            <span>Name</span>
            <span className="text-right">Expected shots</span>
            <span>Target type</span>
            <span />
          </div>
          {stages.map((s, i) => (
            <div
              key={i}
              className="grid items-center gap-3.5 border-b border-rule px-4 py-2.5 last:border-b-0"
              style={{
                gridTemplateColumns: "48px 1fr 130px 180px 36px",
              }}
            >
              <span className="inline-flex h-8 w-9 items-center justify-center rounded-md border border-rule-strong bg-surface-3 font-mono text-xs font-bold tabular-nums text-ink">
                {String(i + 1).padStart(2, "0")}
              </span>
              <TextInput
                value={s.stage_name}
                onChange={(v) => updateStage(i, { stage_name: v })}
                placeholder={`Stage ${i + 1}`}
                compact
              />
              <TextInput
                value={String(s.expected_rounds ?? "")}
                onChange={(v) =>
                  updateStage(i, {
                    expected_rounds: v === "" ? null : Number(v),
                  })
                }
                placeholder="12"
                compact
                mono
                type="number"
                alignRight
              />
              <Select
                value={s.target_type ?? "Pistol IPSC"}
                onChange={(v) => updateStage(i, { target_type: v })}
                options={["Pistol IPSC", "Steel plate", "Mixed", "Classifier"]}
                compact
              />
              <button
                type="button"
                onClick={() => removeStage(i)}
                aria-label="Remove stage"
                className="inline-flex size-7 items-center justify-center rounded-md text-subtle transition-colors hover:bg-led/10 hover:text-led"
              >
                <X className="size-3.5" />
              </button>
            </div>
          ))}
          <div className="flex items-center justify-between bg-surface-2 px-4 py-2.5 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-muted">
            <span>
              <b className="font-bold text-ink">{stages.length} stages</b>{" "}
              &middot;{" "}
              <b className="font-bold text-ink">{totalExpected}</b> expected
              shots total
            </span>
            <button
              type="button"
              onClick={addStage}
              className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-rule-strong px-3 py-1.5 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-led transition-colors hover:border-led hover:bg-led/10"
            >
              <Plus className="size-3" />
              Add stage
            </button>
          </div>
        </div>
        <p className="mt-3 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-subtle">
          Stage times can be entered per stage during audit. If you already
          have official times, paste them in bulk after creating the match.
        </p>
      </Section>

      <Section eyebrow="03 First shooter" title="Add a shooter">
        <p className="-mt-1 mb-4 text-[0.8125rem] text-muted">
          Manual matches need at least one shooter to start. More can be
          added from the shooters page later.
        </p>
        <div className="grid gap-x-5 gap-y-4 sm:grid-cols-2">
          <FormField label="Shooter name" required>
            <TextInput
              value={shooterName}
              onChange={setShooterName}
              placeholder="Full name"
            />
          </FormField>
          <FormField label="Division">
            <Select
              value={shooterDivision}
              onChange={setShooterDivision}
              options={DIVISIONS}
            />
          </FormField>
        </div>
      </Section>

      <FooterActions
        onCancel={() => navigate("/pick", { replace: true })}
        primary={
          <Button
            type="button"
            onClick={() => void submit()}
            disabled={
              creating ||
              !name.trim() ||
              (!hostedMode && !folder.trim()) ||
              !shooterName.trim() ||
              stages.length === 0
            }
            className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_16px_var(--color-led-glow)] hover:bg-led hover:text-ink"
          >
            <span className="font-display uppercase tracking-[0.08em]">
              {creating ? "Creating..." : "Create match"}
            </span>
            <ArrowRight className="size-3.5" />
          </Button>
        }
      />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Shared bits                                                                */
/* -------------------------------------------------------------------------- */

function Section({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title?: string;
  children: React.ReactNode;
}) {
  // Eyebrow is structural ("01 Locate match"), not a stepper. The kicker
  // colour is muted subtle so it reads as "section number" rather than
  // "step indicator" -- the page used to render the LED-red "Step 1"
  // / "Step 2" / "Step 3" stepper which implied an enforced order the
  // form doesn't actually require.
  return (
    <div className="border-b border-rule px-7 py-6 last:border-b-0">
      <div className="mb-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.2em] text-subtle">
        {eyebrow}
      </div>
      {title && (
        <h2 className="mb-1.5 font-display text-[1.0625rem] font-bold uppercase tracking-tight text-ink">
          {title}
        </h2>
      )}
      {children}
    </div>
  );
}

function FormField({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <label className="font-mono text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-muted">
        {label}
        {required && <span className="ml-1 text-led">*</span>}
      </label>
      {children}
    </div>
  );
}

function TextInput({
  value,
  onChange,
  placeholder,
  type = "text",
  mono = false,
  compact = false,
  alignRight = false,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  mono?: boolean;
  compact?: boolean;
  alignRight?: boolean;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className={cn(
        "w-full rounded-lg border border-rule bg-surface-3 text-ink outline-none transition-all",
        "focus:border-led focus:bg-bg-glow focus:shadow-[0_0_0_3px_var(--color-led-tint)]",
        compact ? "px-3 py-2 text-[0.8125rem]" : "px-3.5 py-2.5 text-sm",
        mono && "font-mono tabular-nums",
        alignRight && "text-right",
      )}
    />
  );
}

function Select({
  value,
  onChange,
  options,
  compact = false,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  compact?: boolean;
}) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "w-full appearance-none rounded-lg border border-rule bg-surface-3 pr-8 text-ink outline-none transition-all",
          "focus:border-led focus:bg-bg-glow focus:shadow-[0_0_0_3px_var(--color-led-tint)]",
          compact ? "px-3 py-2 text-[0.8125rem]" : "px-3.5 py-2.5 text-sm",
        )}
      >
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute right-3 top-1/2 size-3.5 -translate-y-1/2 text-muted"
      />
    </div>
  );
}

function FooterActions({
  onCancel,
  primary,
}: {
  onCancel: () => void;
  primary: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between border-t border-rule bg-surface px-7 py-5">
      <Button type="button" variant="ghost" onClick={onCancel}>
        Cancel
      </Button>
      <div className="flex gap-2.5">{primary}</div>
    </div>
  );
}
