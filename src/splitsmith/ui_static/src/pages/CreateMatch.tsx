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
  Plus,
  Search,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Brand, Kicker } from "@/components/ui";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  api,
  type CreateMatchStageDraft,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Variant = "scoreboard" | "manual";

interface ScoreboardSearchResult {
  match_id: number;
  content_type: number;
  name: string;
  club?: string | null;
  match_date?: string | null;
  stage_count?: number | null;
  competitor_count?: number | null;
  level?: string | null;
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
      <header className="sticky top-0 z-40 border-b border-rule bg-gradient-to-b from-surface to-bg">
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
  const [projectFolder, setProjectFolder] = useState("");
  const [shooterName, setShooterName] = useState("");
  const [creating, setCreating] = useState(false);

  // Debounced search. The /api/scoreboard/search endpoint requires a bound
  // project today, so a real client-only path needs a backend hook (#323+).
  // For now: ENTER triggers a search; the offline-graceful path returns a
  // structured error which we render as a fallback CTA.
  async function runSearch() {
    if (!query.trim()) {
      setResults([]);
      return;
    }
    setSearching(true);
    setOffline(false);
    onError(null);
    try {
      // Until the unbound-search endpoint lands we ask the user to use
      // manual; this surface stays in the UI so the redesign mockup
      // is honored even when the backing API isn't ready.
      // (Calling /api/scoreboard/search unbound returns 409 no_project.)
      const resp = await fetch(
        `/api/scoreboard/search?q=${encodeURIComponent(query)}`,
        { headers: { Accept: "application/json" } },
      );
      if (resp.status === 409) {
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

  async function create() {
    if (!selected) return;
    if (!projectFolder.trim() || !shooterName.trim()) {
      onError("Project folder and your name are required.");
      return;
    }
    setCreating(true);
    onError(null);
    try {
      await api.createMatchFromScoreboard({
        project_folder: projectFolder.trim(),
        name: selected.name,
        match_id: selected.match_id,
        content_type: selected.content_type,
        primary_shooter_name: shooterName.trim(),
      });
      navigate("/", { replace: true });
    } catch (e) {
      setCreating(false);
      onError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_24px_48px_-24px_rgba(0,0,0,0.7)]">
      {/* Step 1: locate */}
      <Section eyebrow="Step 1 · locate match" title="Find the match on scoreboard.urdr.dev">
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
              The scoreboard search needs a bound project (or comes back
              when the unbound search lands in a follow-up). You can
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
                  key={`${r.content_type}-${r.match_id}`}
                  result={r}
                  selected={selected?.match_id === r.match_id}
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

        {selected && (
          <div className="relative mt-5 overflow-hidden rounded-xl border border-rule-strong bg-surface-2 px-5 py-5">
            <span
              aria-hidden
              className="absolute inset-y-0 left-0 w-0.5 bg-led shadow-[0_0_12px_var(--color-led-glow)]"
            />
            <div className="mb-4 flex flex-wrap items-start justify-between gap-4 border-b border-rule pb-3">
              <div>
                <div className="font-display text-base font-bold uppercase tracking-tight text-ink">
                  {selected.name}
                  {selected.level && (
                    <span className="ml-2 font-mono text-[0.625rem] font-bold uppercase tracking-[0.14em] text-led">
                      &middot; {selected.level}
                    </span>
                  )}
                </div>
                <div className="mt-1 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
                  {[
                    selected.match_date,
                    selected.club,
                    selected.stage_count
                      ? `${selected.stage_count} stages`
                      : null,
                    selected.competitor_count
                      ? `${selected.competitor_count} competitors`
                      : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
              </div>
            </div>

            <FormField label="Your name" required>
              <input
                type="text"
                value={shooterName}
                onChange={(e) => setShooterName(e.target.value)}
                placeholder="Mathias Axell"
                className="w-full rounded-lg border border-rule bg-surface-3 px-3.5 py-2.5 text-sm text-ink outline-none focus:border-led focus:bg-bg-glow focus:shadow-[0_0_0_3px_var(--color-led-tint)]"
              />
            </FormField>
            <FormField label="Project folder" required>
              <input
                type="text"
                value={projectFolder}
                onChange={(e) => setProjectFolder(e.target.value)}
                placeholder="~/Splitsmith/vads-easter-shoot-2026/"
                className="w-full rounded-lg border border-rule bg-surface-3 px-3.5 py-2.5 font-mono text-[0.8125rem] tabular-nums text-ink outline-none focus:border-led focus:bg-bg-glow focus:shadow-[0_0_0_3px_var(--color-led-tint)]"
              />
            </FormField>
          </div>
        )}
      </Section>

      {selected && (
        <>
          <Section eyebrow="Preview">
            <div className="flex items-center gap-3.5 rounded-[10px] border border-done bg-gradient-to-r from-done/10 to-done/[0.02] px-4 py-3 font-mono text-xs uppercase tracking-[0.06em] text-ink-2">
              <span className="inline-flex size-6 items-center justify-center rounded-full bg-done text-bg shadow-[0_0_10px_var(--color-done-glow)]">
                <Check className="size-3.5" strokeWidth={3} />
              </span>
              <span>
                Ready to create &middot; primary shooter{" "}
                <b className="font-bold text-done">{shooterName || "(set above)"}</b>{" "}
                &middot; stages will be fetched after binding
              </span>
            </div>
          </Section>

          <FooterActions
            onCancel={() => navigate("/pick", { replace: true })}
            primary={
              <Button
                type="button"
                onClick={() => void create()}
                disabled={
                  creating || !shooterName.trim() || !projectFolder.trim()
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
        </>
      )}
    </div>
  );
}

function ResultRow({
  result,
  selected,
  onSelect,
}: {
  result: ScoreboardSearchResult;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "relative grid w-full items-center gap-4 border-b border-rule px-4 py-3.5 text-left transition-colors last:border-b-0 hover:bg-surface-2",
        selected && "bg-led/10",
      )}
      style={{ gridTemplateColumns: "32px 1fr 110px 90px 24px" }}
    >
      {selected && (
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-0.5 bg-led shadow-[0_0_8px_var(--color-led-glow)]"
        />
      )}
      <span
        className={cn(
          "inline-flex size-7 items-center justify-center rounded-md",
          selected
            ? "bg-led-fill text-ink shadow-[0_0_10px_var(--color-led-glow)]"
            : "bg-surface-3 text-muted",
        )}
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="M3 21l9-15 9 15H3z" />
        </svg>
      </span>
      <div>
        <div
          className={cn(
            "font-display text-[0.9375rem] font-bold uppercase tracking-tight",
            selected ? "text-led" : "text-ink",
          )}
        >
          {result.name}
        </div>
        <div className="mt-1 flex flex-wrap gap-x-2 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
          {[
            result.club,
            result.stage_count ? `${result.stage_count} stages` : null,
            result.competitor_count
              ? `${result.competitor_count} competitors`
              : null,
          ]
            .filter(Boolean)
            .map((s, i, arr) => (
              <span key={i}>
                {s}
                {i < arr.length - 1 && <span className="ml-2 text-whisper">·</span>}
              </span>
            ))}
        </div>
      </div>
      <div className="text-right font-mono text-xs tabular-nums text-ink-2">
        {result.match_date ?? ""}
      </div>
      <div>
        {result.level && (
          <span
            className={cn(
              "inline-block rounded px-2 py-0.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em]",
              result.level === "Club"
                ? "border border-rule-strong bg-surface-3 text-ink-2"
                : "border border-led-deep bg-led/10 text-led",
            )}
          >
            {result.level}
          </span>
        )}
      </div>
      {selected ? (
        <span className="inline-flex size-5 items-center justify-center rounded-full bg-led-fill text-ink shadow-[0_0_10px_var(--color-led-glow)]">
          <Check className="size-3" strokeWidth={3} />
        </span>
      ) : (
        <span />
      )}
    </button>
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
  useEffect(() => {
    if (!folder && name.trim()) {
      const slug = name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
      if (slug) setFolder(`~/Splitsmith/${slug}/`);
    }
  }, [name, folder]);

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
    if (!name.trim() || !folder.trim() || !shooterName.trim()) {
      onError("Match name, project folder, and your name are required.");
      return;
    }
    if (stages.length === 0) {
      onError("At least one stage is required.");
      return;
    }
    setCreating(true);
    onError(null);
    try {
      await api.createMatchManual({
        name: name.trim(),
        project_folder: folder.trim(),
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
      navigate("/", { replace: true });
    } catch (e) {
      setCreating(false);
      onError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_24px_48px_-24px_rgba(0,0,0,0.7)]">
      <Section eyebrow="Step 1 · match details" title="Match details">
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
          <FormField label="Project folder">
            <TextInput
              value={folder}
              onChange={setFolder}
              placeholder="~/Splitsmith/<slug>/"
              mono
            />
          </FormField>
        </div>
      </Section>

      <Section eyebrow="Step 2 · stages" title="Stages">
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

      <Section eyebrow="Step 3 · primary shooter" title="Shooter (you)">
        <p className="-mt-1 mb-4 text-[0.8125rem] text-muted">
          You can add more shooters after the match is created.
        </p>
        <div className="grid gap-x-5 gap-y-4 sm:grid-cols-2">
          <FormField label="Your name" required>
            <TextInput
              value={shooterName}
              onChange={setShooterName}
              placeholder="Mathias Axell"
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
              !folder.trim() ||
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
  return (
    <div className="border-b border-rule px-7 py-6 last:border-b-0">
      <div className="mb-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.2em] text-led">
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
