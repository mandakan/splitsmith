/**
 * Merge wizard (#332).
 *
 * Three-step flow to consolidate N legacy single-shooter projects into
 * one redesign-era match folder via the SPA. Wraps /api/match/merge/plan
 * and /api/match/merge/execute -- the heavy lifting (validation, stage
 * reconciliation, slug assignment, conflict detection) all lives in
 * match_model.plan_merge so this surface is just orchestration.
 *
 * Routing: /pick/merge -- self-shelled like the rest of the picker
 * surfaces. On success: bind the new match and navigate to /.
 */

import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Folder,
  GitMerge,
  Loader2,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Brand } from "@/components/ui";
import {
  api,
  type ApiError,
  type MergePlanResponse,
  type RecentProjectDetail,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Step = "select" | "destination" | "review";

export function MergeMatches() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("select");
  const [recents, setRecents] = useState<RecentProjectDetail[]>([]);
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  const [destination, setDestination] = useState("");
  const [name, setName] = useState("");
  const [moveSource, setMoveSource] = useState(false);
  const [plan, setPlan] = useState<MergePlanResponse | null>(null);
  const [planning, setPlanning] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [executing, setExecuting] = useState(false);
  const [executeError, setExecuteError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getRecentProjectsDetail()
      .then((rs) => {
        if (alive) setRecents(rs);
      })
      .catch(() => {
        if (alive) setRecents([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  const legacy = useMemo(
    () => recents.filter((r) => r.kind === "legacy"),
    [recents],
  );
  const selectedLegacy = useMemo(
    () => legacy.filter((r) => selectedPaths.has(r.path)),
    [legacy, selectedPaths],
  );

  function toggleSelected(path: string) {
    setSelectedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
    // Any change invalidates the plan -- they must re-plan with the new set.
    setPlan(null);
    setPlanError(null);
  }

  // Default destination = parent dir of the first selected project + a
  // suggested folder name. The user can edit.
  useEffect(() => {
    if (selectedLegacy.length === 0 || destination) return;
    const first = selectedLegacy[0].path;
    const parent = first.substring(0, first.lastIndexOf("/"));
    setDestination(`${parent}/merged-match`);
    if (!name) setName(selectedLegacy[0].name || "Merged Match");
  }, [selectedLegacy, destination, name]);

  async function runPlan() {
    setPlanning(true);
    setPlanError(null);
    try {
      const p = await api.planMatchMerge({
        inputs: selectedLegacy.map((r) => r.path),
        output: destination || undefined,
        name: name || undefined,
      });
      setPlan(p);
      setStep("review");
    } catch (e) {
      const err = e as ApiError;
      setPlanError(err.detail ?? String(e));
    } finally {
      setPlanning(false);
    }
  }

  async function runExecute() {
    setExecuting(true);
    setExecuteError(null);
    try {
      await api.executeMatchMerge({
        inputs: selectedLegacy.map((r) => r.path),
        output: destination,
        name: name || undefined,
        move: moveSource,
      });
      navigate("/", { replace: true });
    } catch (e) {
      const err = e as ApiError;
      setExecuteError(err.detail ?? String(e));
      setExecuting(false);
    }
  }

  return (
    <div
      className="min-h-screen text-ink"
      style={{
        backgroundImage:
          "radial-gradient(1400px 600px at 50% -100px, rgba(255,45,45,0.05), transparent 60%), linear-gradient(to bottom, var(--color-bg-glow), var(--color-bg))",
        backgroundAttachment: "fixed",
      }}
    >
      <header className="sticky top-0 z-chrome border-b border-rule bg-gradient-to-b from-surface to-bg">
        <div className="mx-auto flex max-w-[1100px] items-center gap-6 px-8 py-3.5">
          <Brand variant="compact" />
          <div className="ml-auto" />
        </div>
        <div className="border-t border-rule bg-bg">
          <div className="mx-auto flex max-w-[1100px] items-center gap-3 px-8 py-2.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-subtle">
            <button
              type="button"
              onClick={() => navigate("/pick", { replace: true })}
              className="inline-flex items-center gap-1.5 text-subtle transition-colors hover:text-ink-2"
            >
              <ArrowLeft className="size-3" />
              Matches
            </button>
            <span className="text-whisper">/</span>
            <span className="font-semibold text-ink">Merge legacy projects</span>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1100px] px-8 pb-20 pt-10">
        <div className="mb-7">
          <div className="mb-2 inline-flex items-center gap-2.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.18em] text-led">
            <GitMerge className="size-3.5" />
            Match consolidation
          </div>
          <h1 className="mb-3 font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
            Merge legacy projects
          </h1>
          <p className="max-w-2xl text-[0.875rem] text-muted">
            Combine two or more single-shooter projects from the same match into one
            redesign-era match folder. The merge validates that stages and scoreboard
            data line up before writing anything; conflicts abort with a message so you
            can reconcile the sources first.
          </p>
        </div>

        <StepIndicator step={step} />

        {step === "select" && (
          <SelectStep
            legacy={legacy}
            selectedPaths={selectedPaths}
            onToggle={toggleSelected}
            onContinue={() => setStep("destination")}
          />
        )}

        {step === "destination" && (
          <DestinationStep
            destination={destination}
            name={name}
            moveSource={moveSource}
            selectedCount={selectedLegacy.length}
            planError={planError}
            planning={planning}
            onDestination={setDestination}
            onName={setName}
            onMoveSource={setMoveSource}
            onBack={() => setStep("select")}
            onContinue={() => void runPlan()}
          />
        )}

        {step === "review" && plan && (
          <ReviewStep
            plan={plan}
            destination={destination}
            moveSource={moveSource}
            executing={executing}
            executeError={executeError}
            onBack={() => {
              setPlan(null);
              setStep("destination");
            }}
            onExecute={() => void runExecute()}
          />
        )}
      </main>
    </div>
  );
}

function StepIndicator({ step }: { step: Step }) {
  const steps: { key: Step; label: string }[] = [
    { key: "select", label: "Select projects" },
    { key: "destination", label: "Destination" },
    { key: "review", label: "Review & merge" },
  ];
  const idx = steps.findIndex((s) => s.key === step);
  return (
    <ol className="mb-6 flex items-center gap-1.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em]">
      {steps.map((s, i) => {
        const done = i < idx;
        const active = i === idx;
        return (
          <li
            key={s.key}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5",
              active && "border-led-deep bg-[color:var(--color-led-tint)] text-led",
              done && "border-rule bg-surface-2 text-done",
              !active && !done && "border-rule bg-surface text-muted",
            )}
          >
            <span className="tabular-nums">{String(i + 1).padStart(2, "0")}</span>
            <span>{s.label}</span>
            {i < steps.length - 1 && <ArrowRight className="size-3 text-whisper" />}
          </li>
        );
      })}
    </ol>
  );
}

function SelectStep({
  legacy,
  selectedPaths,
  onToggle,
  onContinue,
}: {
  legacy: RecentProjectDetail[];
  selectedPaths: Set<string>;
  onToggle: (path: string) => void;
  onContinue: () => void;
}) {
  return (
    <div>
      <p className="mb-4 text-[0.875rem] text-ink-2">
        Pick the legacy projects to merge. Two or more required; one shooter per
        project. The list below is your recent-projects index filtered to
        single-shooter folders.
      </p>
      {legacy.length === 0 ? (
        <div className="rounded-md border border-rule bg-surface px-5 py-6 text-center text-[0.875rem] text-muted">
          No legacy projects found in your recents. Open them once from the picker so
          they show up here, then return.
        </div>
      ) : (
        <ul className="space-y-2">
          {legacy.map((r) => {
            const checked = selectedPaths.has(r.path);
            return (
              <li key={r.path}>
                <button
                  type="button"
                  onClick={() => onToggle(r.path)}
                  className={cn(
                    "grid w-full grid-cols-[28px_1fr_auto] items-center gap-3 rounded-md border px-4 py-3 text-left transition-colors",
                    checked
                      ? "border-led-deep bg-[color:var(--color-led-tint)]"
                      : "border-rule bg-surface hover:bg-surface-2",
                  )}
                >
                  <span
                    className={cn(
                      "inline-flex size-5 items-center justify-center rounded border",
                      checked
                        ? "border-led bg-led-fill text-ink"
                        : "border-rule-strong bg-surface-2",
                    )}
                  >
                    {checked && <CheckCircle2 className="size-3.5" />}
                  </span>
                  <div className="min-w-0">
                    <div className="truncate font-mono text-[0.8125rem] font-bold text-ink">
                      {r.name}
                    </div>
                    <div className="truncate font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                      {r.path}
                    </div>
                  </div>
                  <Folder className="size-4 text-muted" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-6 flex items-center justify-between">
        <span className="font-mono text-[0.75rem] tabular-nums text-muted">
          {selectedPaths.size} selected
        </span>
        <button
          type="button"
          onClick={onContinue}
          disabled={selectedPaths.size < 2}
          className={cn(
            "btn-led-fill inline-flex items-center gap-2 rounded-md px-4 py-2.5",
            selectedPaths.size < 2 && "opacity-40 hover:opacity-40",
          )}
        >
          Continue
          <ArrowRight className="size-3.5" />
        </button>
      </div>
    </div>
  );
}

function DestinationStep({
  destination,
  name,
  moveSource,
  selectedCount,
  planError,
  planning,
  onDestination,
  onName,
  onMoveSource,
  onBack,
  onContinue,
}: {
  destination: string;
  name: string;
  moveSource: boolean;
  selectedCount: number;
  planError: string | null;
  planning: boolean;
  onDestination: (v: string) => void;
  onName: (v: string) => void;
  onMoveSource: (v: boolean) => void;
  onBack: () => void;
  onContinue: () => void;
}) {
  return (
    <div className="grid gap-5">
      <p className="text-[0.875rem] text-ink-2">
        Pick where the merged match folder lives and what to call it. {selectedCount}{" "}
        legacy projects will be combined inside.
      </p>

      <Field
        label="Destination folder"
        hint="Absolute path. Created if missing; refused if it already contains match.json."
      >
        <input
          type="text"
          value={destination}
          onChange={(e) => onDestination(e.target.value)}
          placeholder="/Users/you/matches/bromma-2026"
          className="h-11 w-full rounded-md border border-rule bg-surface px-3 font-mono text-[0.875rem] text-ink placeholder:text-whisper focus-visible:border-led"
        />
      </Field>

      <Field label="Match name" hint="Optional. Defaults to the input project name; required when inputs disagree.">
        <input
          type="text"
          value={name}
          onChange={(e) => onName(e.target.value)}
          placeholder="e.g. Bromma Easter Shoot 2026"
          className="h-11 w-full rounded-md border border-rule bg-surface px-3 text-[0.875rem] text-ink placeholder:text-whisper focus-visible:border-led"
        />
      </Field>

      <div className="rounded-md border border-rule bg-surface px-4 py-3">
        <label className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={moveSource}
            onChange={(e) => onMoveSource(e.target.checked)}
            className="mt-0.5 size-4 accent-led"
          />
          <div>
            <div className="flex items-center gap-2 font-mono text-[0.75rem] font-bold uppercase tracking-[0.06em] text-ink-2">
              <Trash2 className="size-3" />
              Move sources (destructive)
            </div>
            <div className="mt-1 text-[0.8125rem] text-muted">
              By default the merge <b>copies</b> source projects into the new match.
              Tick this to <b>move</b> them instead -- the originals are removed once
              the merge succeeds. Faster, no double-disk-use, but irreversible.
            </div>
          </div>
        </label>
      </div>

      {planError && (
        <div className="flex items-start gap-2 rounded-md border border-led-deep bg-[color:var(--color-led-tint)] px-4 py-3 text-[0.8125rem] text-led">
          <AlertCircle className="mt-0.5 size-4 shrink-0" />
          <div>
            <div className="font-bold">Cannot plan merge</div>
            <div className="text-ink-2">{planError}</div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-2 rounded-md border border-rule px-3 py-2 font-mono text-[0.75rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:bg-surface-2"
        >
          <ArrowLeft className="size-3.5" />
          Back
        </button>
        <button
          type="button"
          onClick={onContinue}
          disabled={planning || !destination}
          className={cn(
            "btn-led-fill inline-flex items-center gap-2 rounded-md px-4 py-2.5",
            (planning || !destination) && "opacity-40 hover:opacity-40",
          )}
        >
          {planning ? (
            <>
              <Loader2 className="size-3.5 animate-spin" /> Planning...
            </>
          ) : (
            <>
              Preview merge
              <ArrowRight className="size-3.5" />
            </>
          )}
        </button>
      </div>
    </div>
  );
}

function ReviewStep({
  plan,
  destination,
  moveSource,
  executing,
  executeError,
  onBack,
  onExecute,
}: {
  plan: MergePlanResponse;
  destination: string;
  moveSource: boolean;
  executing: boolean;
  executeError: string | null;
  onBack: () => void;
  onExecute: () => void;
}) {
  return (
    <div className="grid gap-5">
      <p className="text-[0.875rem] text-ink-2">
        This is what the merge would write. Inspect the per-shooter assignments and
        reconciled stages before committing.
      </p>

      <section className="rounded-md border border-rule bg-surface">
        <header className="border-b border-rule px-5 py-3">
          <div className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-subtle">
            Match
          </div>
          <div className="mt-1 font-display text-[1.125rem] font-bold uppercase tracking-tight text-ink">
            {plan.name}
          </div>
          <div className="mt-1 grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-[0.6875rem] text-muted">
            <span>
              Date <b className="text-ink-2">{plan.match_date ?? "--"}</b>
            </span>
            <span>
              Stages <b className="text-ink-2">{plan.stages.length}</b>
            </span>
            <span>
              Scoreboard{" "}
              <b className="text-ink-2">{plan.scoreboard_match_id ?? "none"}</b>
            </span>
            <span>
              Shooters <b className="text-ink-2">{plan.shooter_moves.length}</b>
            </span>
          </div>
          <div className="mt-3 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            Destination &middot; <span className="text-ink-2">{destination}</span>
          </div>
        </header>
      </section>

      <section className="rounded-md border border-rule bg-surface">
        <header className="border-b border-rule px-5 py-3 font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
          Shooters ({plan.shooter_moves.length})
        </header>
        <ul className="divide-y divide-rule">
          {plan.shooter_moves.map((mv) => (
            <li
              key={mv.slug}
              className="grid grid-cols-[1fr_120px] items-center gap-3 px-5 py-3"
            >
              <div className="min-w-0">
                <div className="truncate font-mono text-[0.8125rem] font-bold text-ink">
                  {mv.competitor_name}
                </div>
                <div className="truncate font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                  {mv.source_root}
                </div>
              </div>
              <div className="text-right font-mono text-[0.6875rem]">
                <div className="text-muted">slug</div>
                <div className="font-bold text-led">{mv.slug}</div>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="rounded-md border border-rule bg-surface">
        <header className="border-b border-rule px-5 py-3 font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
          Stages ({plan.stages.length})
        </header>
        <ul className="grid grid-cols-2 gap-x-4 divide-y divide-rule sm:grid-cols-3">
          {plan.stages.map((s) => (
            <li
              key={s.stage_number}
              className="flex items-baseline gap-3 px-5 py-2.5"
            >
              <span className="font-mono text-[0.6875rem] tabular-nums text-muted">
                {String(s.stage_number).padStart(2, "0")}
              </span>
              <span className="truncate text-[0.8125rem] text-ink">{s.stage_name}</span>
              {s.expected_rounds != null && (
                <span className="ml-auto font-mono text-[0.625rem] tabular-nums text-muted">
                  {s.expected_rounds}r
                </span>
              )}
            </li>
          ))}
        </ul>
      </section>

      {executeError && (
        <div className="flex items-start gap-2 rounded-md border border-led-deep bg-[color:var(--color-led-tint)] px-4 py-3 text-[0.8125rem] text-led">
          <AlertCircle className="mt-0.5 size-4 shrink-0" />
          <div>
            <div className="font-bold">Merge failed</div>
            <div className="text-ink-2">{executeError}</div>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={onBack}
          disabled={executing}
          className="inline-flex items-center gap-2 rounded-md border border-rule px-3 py-2 font-mono text-[0.75rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:bg-surface-2 disabled:opacity-50"
        >
          <ArrowLeft className="size-3.5" />
          Edit destination
        </button>
        <button
          type="button"
          onClick={onExecute}
          disabled={executing}
          className={cn(
            "btn-led-fill inline-flex items-center gap-2 rounded-md px-5 py-2.5",
            executing && "opacity-60 hover:opacity-60",
          )}
        >
          {executing ? (
            <>
              <Loader2 className="size-3.5 animate-spin" />
              {moveSource ? "Moving + writing..." : "Copying + writing..."}
            </>
          ) : (
            <>
              <GitMerge className="size-3.5" />
              Merge {plan.shooter_moves.length} shooters
            </>
          )}
        </button>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="mb-1 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-ink-2">
        {label}
      </div>
      {children}
      {hint && (
        <div className="mt-1 text-[0.75rem] text-muted">{hint}</div>
      )}
    </label>
  );
}
