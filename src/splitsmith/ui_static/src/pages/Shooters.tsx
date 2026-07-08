/**
 * Shooters route (/shooters) -- multi-camera per shooter (#324).
 *
 * Per-shooter card per polished/16:
 *   - racing-color identity rail (MA red / JL amber / PE green / RJ blue
 *     ... picked deterministically from the shooter's slug)
 *   - shooter head (avatar, name, division, camera count, progress bar,
 *     status pill, row actions)
 *   - camera rows: role pill (primary/secondary), stage coverage chips
 *     (with overflow indicator), video count
 *
 * Add-shooter affordance at the bottom. Reference-shooters section is
 * UI scaffold only (future feature, gated by issue) so the redesign
 * doesn't leave broken affordances.
 *
 * Mounted under <MatchShell />.
 */

import {
  ArrowRight,
  Camera,
  CheckCircle2,
  Plus,
  RefreshCw,
  Search,
  Star,
  Trash2,
  UserPlus,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  useNavigate,
  useOutletContext,
  useSearchParams,
} from "react-router-dom";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { CompetitorRow } from "@/components/scoreboard/CompetitorRow";
import { Kicker } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/useConfirm";
import {
  ApiError,
  api,
  type ScoreboardMatchCompetitor,
  type ShooterCameraInfo,
  type ShooterListEntry,
  type ShooterListResponse,
} from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";
import { cn } from "@/lib/utils";

type Palette = "ma" | "jl" | "pe" | "rj" | "manual";

function pickPalette(slug: string, isYou: boolean): Palette {
  if (isYou) return "ma";
  let hash = 0;
  for (let i = 0; i < slug.length; i++) {
    hash = (hash * 31 + slug.charCodeAt(i)) | 0;
  }
  // Skip "ma" for non-you so MA always reads as you.
  const others = ["jl", "pe", "rj", "manual"] as const;
  return others[Math.abs(hash) % others.length];
}

const PALETTE_STYLE: Record<Palette, {
  rail: string;
  ring: string;
  avatarBg: string;
  glow: string;
}> = {
  ma: {
    rail: "bg-led shadow-[0_0_16px_var(--color-led-glow)]",
    ring: "border-led-deep",
    avatarBg:
      "bg-[linear-gradient(135deg,var(--color-led),var(--color-led-deep))] shadow-[0_0_0_1px_rgba(255,45,45,0.4),0_0_14px_var(--color-led-glow)]",
    glow: "shadow-[inset_0_0_0_1px_var(--color-led-deep)]",
  },
  jl: {
    rail: "bg-shooter-jl shadow-[0_0_16px_var(--color-shooter-jl-glow)]",
    ring: "border-shooter-jl-deep",
    avatarBg:
      "bg-[linear-gradient(135deg,var(--color-shooter-jl-soft),var(--color-shooter-jl-deep))] shadow-[0_0_0_1px_var(--color-shooter-jl-deep),0_0_14px_var(--color-shooter-jl-glow)]",
    glow: "shadow-[inset_0_0_0_1px_var(--color-shooter-jl-deep)]",
  },
  pe: {
    rail: "bg-shooter-pe shadow-[0_0_16px_var(--color-shooter-pe-glow)]",
    ring: "border-shooter-pe-deep",
    avatarBg:
      "bg-[linear-gradient(135deg,var(--color-shooter-pe-soft),var(--color-shooter-pe-deep))] shadow-[0_0_0_1px_var(--color-shooter-pe-deep),0_0_14px_var(--color-shooter-pe-glow)]",
    glow: "shadow-[inset_0_0_0_1px_var(--color-shooter-pe-deep)]",
  },
  rj: {
    rail: "bg-shooter-rj shadow-[0_0_16px_var(--color-shooter-rj-glow)]",
    ring: "border-shooter-rj-deep",
    avatarBg:
      "bg-[linear-gradient(135deg,var(--color-shooter-rj-soft),var(--color-shooter-rj-deep))] shadow-[0_0_0_1px_var(--color-shooter-rj-deep),0_0_14px_var(--color-shooter-rj-glow)]",
    glow: "shadow-[inset_0_0_0_1px_var(--color-shooter-rj-deep)]",
  },
  manual: {
    rail: "bg-manual shadow-[0_0_16px_var(--color-manual-glow)]",
    ring: "border-manual",
    avatarBg:
      "bg-[linear-gradient(135deg,var(--color-manual),#5B21B6)] shadow-[0_0_0_1px_var(--color-manual),0_0_14px_var(--color-manual-glow)]",
    glow: "shadow-[inset_0_0_0_1px_var(--color-manual)]",
  },
};

// Sections that are per-shooter: clicking them in the sidebar without a
// shooter in focus routes here with ``?pick=<section>`` so we can explain
// why, instead of silently dumping the user on the shooter list.
const PICK_SECTION_LABELS: Record<string, string> = {
  audit: "Audit",
  coach: "Coach",
  videos: "Videos",
  export: "Export",
};

export function Shooters() {
  const navigate = useNavigate();
  const confirm = useConfirm();
  const href = useMatchHref();
  const { project } = useOutletContext<MatchShellOutletContext>();
  const [searchParams] = useSearchParams();
  const pickSection = PICK_SECTION_LABELS[searchParams.get("pick") ?? ""] ?? null;
  const [data, setData] = useState<ShooterListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  // Roster picker (#598): when the match is scoreboard-linked, add-shooter
  // defaults to picking an unclaimed competitor instead of typing a name
  // from scratch. ``manualRoster`` lets the operator opt into the plain
  // name form anyway (imported reference shooters, walk-ons not yet on
  // the scoreboard, etc).
  const scoreboardMatchId = project?.scoreboard_match_id ?? null;
  const scoreboardContentType = project?.scoreboard_content_type ?? null;
  const isLinked = scoreboardMatchId != null && scoreboardContentType != null;
  const [manualRoster, setManualRoster] = useState(false);
  const [roster, setRoster] = useState<ScoreboardMatchCompetitor[] | null>(
    null,
  );
  const [rosterLoading, setRosterLoading] = useState(false);
  const [rosterError, setRosterError] = useState<string | null>(null);
  const [rosterFilter, setRosterFilter] = useState("");
  const [pickingId, setPickingId] = useState<number | null>(null);

  useEffect(() => {
    if (scoreboardMatchId == null || scoreboardContentType == null) {
      setRoster(null);
      return;
    }
    let alive = true;
    setRosterLoading(true);
    setRosterError(null);
    api
      .getScoreboardMatchDataUnbound(
        scoreboardContentType,
        Number(scoreboardMatchId),
      )
      .then((match) => {
        if (!alive) return;
        setRoster(match.competitors);
      })
      .catch((e) => {
        if (!alive) return;
        setRoster(null);
        setRosterError(e instanceof ApiError ? e.detail : String(e));
      })
      .finally(() => {
        if (alive) setRosterLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [scoreboardMatchId, scoreboardContentType]);

  // Competitors already claimed by a local shooter never show up as
  // pickable -- picking them again would just re-run the same bind.
  const claimedCompetitorIds = useMemo(() => {
    const ids = new Set<number>();
    for (const s of data?.shooters ?? []) {
      if (s.selected_competitor_id != null) ids.add(s.selected_competitor_id);
    }
    return ids;
  }, [data]);

  const availableRoster = useMemo(() => {
    const q = rosterFilter.trim().toLowerCase();
    return (roster ?? []).filter((c) => {
      if (claimedCompetitorIds.has(c.id)) return false;
      if (!q) return true;
      const hay = `${c.name} ${c.club ?? ""} ${c.division ?? ""}`.toLowerCase();
      return hay.includes(q);
    });
  }, [roster, claimedCompetitorIds, rosterFilter]);

  async function pickCompetitor(c: ScoreboardMatchCompetitor) {
    setPickingId(c.id);
    setError(null);
    try {
      const next = await api.addMatchShooter({
        name: c.name,
        division: c.division,
        selected_shooter_id: c.shooterId,
        selected_competitor_id: c.id,
      });
      setData(next);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setPickingId(null);
    }
  }

  const reload = useCallback(async () => {
    setError(null);
    try {
      const resp = await api.listMatchShooters();
      setData(resp);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function remove(slug: string, name: string) {
    const ok = await confirm({
      title: `Remove ${name}?`,
      body: "Their footage, audit, and exports inside the match folder will be deleted. This cannot be undone.",
      confirmLabel: "Remove shooter",
    });
    if (!ok.confirmed) return;
    setBusy(slug);
    try {
      const next = await api.removeMatchShooter(slug);
      setData(next);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function add() {
    if (!newName.trim()) return;
    setAdding(true);
    setError(null);
    try {
      const next = await api.addMatchShooter({ name: newName.trim() });
      setData(next);
      setNewName("");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setAdding(false);
    }
  }

  async function rebuildTrims(slug: string, name: string, count: number) {
    setBusy(slug);
    setError(null);
    try {
      const result = await api.buildShooterTrimCaches(slug);
      // The jobs rail already polls /api/jobs and surfaces the queued trim
      // jobs. We deliberately don't reload the shooter list here: the jobs run
      // asynchronously after this POST returns, so an immediate refetch would
      // report the same missing-trim count and just hold the per-shooter busy
      // lock across a second, pointless round-trip. The count settles the next
      // time the user loads this page.
      const submitted = result.jobs_submitted.length;
      if (submitted === 0) {
        setError(
          `No trim jobs to run for ${name} -- ${count} stages were eligible by count but every one was already cached, missing prerequisites, or already queued.`,
        );
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  const active = data?.shooters ?? [];
  const stagesTotal = active[0]?.stages_total ?? 0;

  return (
    <div className="px-7 py-5">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <Kicker className="mb-2">Match &middot; shooters</Kicker>
          <h1 className="mb-2 font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
            Shooters
          </h1>
          <p className="max-w-[40rem] text-sm text-muted">
            <b className="font-bold text-ink">{active.length} active</b>{" "}
            &middot; manage cameras, role assignments, and per-stage
            coverage.
          </p>
        </div>
      </div>

      {pickSection && (
        <div className="mb-4 rounded-md border border-rule-strong bg-surface-2 px-3 py-2 text-sm text-ink">
          <b className="font-bold">{pickSection}</b> is per-shooter — pick a
          shooter below to open it.
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
          {error}
        </div>
      )}

      <section className="mb-6">
        <SectionHead
          title="Active shooters"
          help="Footage attached and participating in this match"
        />
        <div className="flex flex-col gap-3">
          {active.map((shooter) => (
            <ShooterCard
              key={shooter.slug}
              shooter={shooter}
              stagesTotal={stagesTotal}
              busy={busy === shooter.slug}
              onRemove={() => void remove(shooter.slug, shooter.name)}
              onOpenAudit={() => navigate(href("audit", shooter.slug))}
              onOpenIngest={() => navigate(href("ingest", shooter.slug))}
              onRebuildTrims={() =>
                void rebuildTrims(
                  shooter.slug,
                  shooter.name,
                  shooter.stages_missing_trim,
                )
              }
            />
          ))}
        </div>
      </section>

      <section className="mb-6">
        <SectionHead
          title="Reference shooters"
          help="Imported runs from match winners or coach-approved shooters (future feature)"
        />
        <div
          className="overflow-hidden rounded-2xl border border-dashed border-rule-strong bg-surface-2/30 p-5"
          aria-label="Future feature placeholder"
        >
          <div className="mb-2 inline-flex items-center gap-2 font-display text-sm font-bold uppercase tracking-[0.06em] text-muted">
            <Star className="size-4" />
            Future feature
          </div>
          <p className="max-w-2xl text-[0.8125rem] leading-relaxed text-muted">
            Reference shooters provide hand-vetted shot timings (no local
            footage required) that show up in Compare timelines and Coach
            summaries, so you can benchmark against the winner or a
            coach-approved baseline. Sources will be scoreboard data, a
            hosted reference corpus, or a club-coach signed file.
          </p>
        </div>
      </section>

      <section>
        <SectionHead title="Add another shooter" />
        <div className="overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
          <div className="mb-3 inline-flex items-center gap-2.5 font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
            <UserPlus className="size-4 text-led" />
            New shooter
          </div>

          {isLinked && !manualRoster ? (
            <>
              <p className="mb-4 text-[0.8125rem] text-muted">
                This match is linked to the scoreboard -- pick a competitor
                below to add them with their scoreboard identity already
                bound, so their splits get an expected-rounds prior for
                free.
              </p>
              {rosterError && (
                <div className="mb-3 rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
                  {rosterError}
                </div>
              )}
              <label className="mb-3 flex min-h-10 items-center gap-2.5 rounded-lg border border-rule bg-surface-3 px-3.5 py-2 transition-colors focus-within:border-led focus-within:bg-bg-glow focus-within:shadow-[0_0_0_3px_var(--color-led-tint)]">
                <Search aria-hidden className="size-4 text-subtle" />
                <input
                  type="text"
                  value={rosterFilter}
                  onChange={(e) => setRosterFilter(e.target.value)}
                  placeholder="Filter by name, club, or division..."
                  className="flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-subtle"
                />
                {rosterFilter && (
                  <button
                    type="button"
                    onClick={() => setRosterFilter("")}
                    className="rounded p-0.5 text-subtle hover:bg-surface-2 hover:text-ink"
                    aria-label="Clear filter"
                  >
                    <X className="size-3.5" />
                  </button>
                )}
              </label>
              <div className="max-h-80 overflow-y-auto rounded-[10px] border border-rule bg-bg-glow">
                {rosterLoading ? (
                  <div className="px-4 py-8 text-center font-mono text-xs uppercase tracking-[0.08em] text-muted">
                    Loading roster from scoreboard...
                  </div>
                ) : availableRoster.length === 0 ? (
                  <div className="px-4 py-8 text-center font-mono text-xs uppercase tracking-[0.08em] text-muted">
                    {roster && roster.length > 0
                      ? "Every roster competitor is already linked to a shooter."
                      : "No competitors on this match yet."}
                  </div>
                ) : (
                  availableRoster.map((c) => (
                    <CompetitorRow
                      key={c.id}
                      competitor={c}
                      checked={false}
                      disabled={pickingId !== null}
                      onToggle={() => void pickCompetitor(c)}
                    />
                  ))
                )}
              </div>
              <button
                type="button"
                onClick={() => setManualRoster(true)}
                className="mt-3 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-led hover:text-led-soft"
              >
                Add manually instead
              </button>
            </>
          ) : (
            <>
              <p className="mb-4 text-[0.8125rem] text-muted">
                Add a squadmate by name; footage gets attached on the Ingest
                page once you pick their folder.
              </p>
              <div className="flex flex-wrap items-stretch gap-2.5">
                <input
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="Johan Larsson"
                  className="flex-1 min-w-[260px] rounded-md border border-rule bg-surface-3 px-3.5 py-2.5 text-sm text-ink outline-none focus:border-led focus:shadow-[0_0_0_3px_var(--color-led-tint)]"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void add();
                  }}
                />
                <Button
                  type="button"
                  onClick={() => void add()}
                  disabled={adding || !newName.trim()}
                  className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink"
                >
                  <Plus className="size-3.5" />
                  <span className="font-display uppercase tracking-[0.08em]">
                    {adding ? "Adding..." : "Add shooter"}
                  </span>
                </Button>
              </div>
              {isLinked && (
                <button
                  type="button"
                  onClick={() => setManualRoster(false)}
                  className="mt-3 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-led hover:text-led-soft"
                >
                  Pick from roster instead
                </button>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Shooter card                                                               */
/* -------------------------------------------------------------------------- */

function ShooterCard({
  shooter,
  stagesTotal,
  busy,
  onRemove,
  onOpenAudit,
  onOpenIngest,
  onRebuildTrims,
}: {
  shooter: ShooterListEntry;
  stagesTotal: number;
  busy: boolean;
  onRemove: () => void;
  onOpenAudit: () => void;
  onOpenIngest: () => void;
  onRebuildTrims: () => void;
}) {
  const palette = pickPalette(shooter.slug, false);
  const style = PALETTE_STYLE[palette];
  const progress =
    stagesTotal > 0 ? shooter.stages_audited / stagesTotal : 0;
  const status: "complete" | "in_progress" | "new" =
    progress >= 1
      ? "complete"
      : progress > 0
        ? "in_progress"
        : "new";

  // Group cameras into "Camera A / B / ..." labels in array order.
  const labeledCameras = shooter.cameras.map((cam, i) => ({
    ...cam,
    label: `Camera ${String.fromCharCode(65 + i)}`,
  }));

  return (
    <article
      className="relative overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]"
    >
      <span
        aria-hidden
        className={cn("absolute inset-y-0 left-0 w-[3px]", style.rail)}
      />
      <div className="flex flex-wrap items-center gap-4 border-b border-rule px-6 py-4">
        <span
          aria-hidden
          className={cn(
            "inline-flex size-11 items-center justify-center rounded-full font-mono text-sm font-bold text-ink",
            style.avatarBg,
          )}
        >
          {initials(shooter.name)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="inline-flex items-center gap-2.5 font-display text-base font-bold uppercase tracking-[0.04em] text-ink">
            {shooter.name}
          </div>
          <div className="mt-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
            {shooter.cameras.length} camera
            {shooter.cameras.length === 1 ? "" : "s"} &middot;{" "}
            {shooter.video_count} video
            {shooter.video_count === 1 ? "" : "s"}
          </div>
        </div>
        <div className="flex flex-col gap-1.5 min-w-[180px]">
          <div className="font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted tabular-nums">
            <b className="font-bold text-ink">
              {pad2(shooter.stages_audited)} / {pad2(stagesTotal)}
            </b>{" "}
            stages audited
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-surface-3">
            <span
              className={cn(
                "block h-full rounded-full transition-all duration-500",
                status === "complete"
                  ? "bg-done shadow-[0_0_6px_var(--color-done-glow)]"
                  : status === "in_progress"
                    ? "bg-live shadow-[0_0_6px_var(--color-live-glow)]"
                    : "bg-cold",
              )}
              style={{ width: `${Math.round(progress * 100)}%` }}
            />
          </div>
        </div>
        <StatusPill status={status} />
        <div className="flex items-center gap-1">
          {shooter.stages_missing_trim > 0 && (
            <button
              type="button"
              onClick={onRebuildTrims}
              disabled={busy}
              title={`Rebuild ${shooter.stages_missing_trim} missing trim cache${shooter.stages_missing_trim === 1 ? "" : "s"} for ${shooter.name}`}
              aria-label={`Rebuild ${shooter.stages_missing_trim} missing trim caches for ${shooter.name}`}
              className="inline-flex min-h-9 items-center gap-1.5 rounded-md border border-rule bg-surface-2 px-2.5 font-display text-[0.625rem] font-semibold uppercase tracking-[0.08em] text-ink-2 transition-colors hover:border-led hover:bg-led/10 hover:text-led disabled:opacity-50"
            >
              <RefreshCw className="size-3.5" />
              Rebuild ({shooter.stages_missing_trim})
            </button>
          )}
          <button
            type="button"
            onClick={onOpenAudit}
            disabled={shooter.video_count === 0}
            title={
              shooter.video_count === 0
                ? `Attach footage first - ${shooter.name} has no videos to audit`
                : `Open ${shooter.name}'s audit`
            }
            aria-label={`Open ${shooter.name}'s audit`}
            className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-ink-2 transition-colors hover:border-led hover:bg-led/10 hover:text-led disabled:opacity-30 disabled:hover:border-rule disabled:hover:bg-surface-2 disabled:hover:text-ink-2"
          >
            <ArrowRight className="size-4" />
          </button>
          <button
            type="button"
            onClick={onRemove}
            disabled={busy}
            title={`Remove ${shooter.name}`}
            aria-label="Remove shooter"
            className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-subtle transition-colors hover:border-led/40 hover:bg-led/10 hover:text-led disabled:opacity-30 disabled:hover:bg-surface-2 disabled:hover:text-subtle"
          >
            <Trash2 className="size-4" />
          </button>
        </div>
      </div>
      <div className="flex flex-col gap-0 p-4">
        {labeledCameras.length === 0 ? (
          <p className="px-2 py-3 text-center font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-muted">
            No cameras attached yet. Go to{" "}
            <button
              type="button"
              onClick={onOpenIngest}
              className="text-led hover:text-led-soft"
            >
              Ingest
            </button>{" "}
            to drop footage for this shooter.
          </p>
        ) : (
          labeledCameras.map((cam) => (
            <CameraRow
              key={cam.group_key}
              camera={cam}
              stagesTotal={stagesTotal}
            />
          ))
        )}
      </div>
    </article>
  );
}

function CameraRow({
  camera,
  stagesTotal,
}: {
  camera: ShooterCameraInfo & { label: string };
  stagesTotal: number;
}) {
  const missing = stagesTotal - camera.stage_numbers.length;
  return (
    <div className="grid grid-cols-[40px_1fr_140px_1fr_80px] items-center gap-4 border-t border-rule py-3 first:border-t-0">
      <span className="inline-flex size-10 items-center justify-center rounded-md border border-rule-strong bg-surface-3 text-ink-2">
        <Camera className="size-4" />
      </span>
      <div>
        <div className="font-display text-sm font-bold uppercase tracking-[0.04em] text-ink">
          {camera.label}
        </div>
        <div className="mt-0.5 font-mono text-[0.5625rem] uppercase tracking-[0.06em] text-muted">
          {[camera.model, camera.mount].filter(Boolean).join(" · ") ||
            "Unknown camera"}
        </div>
      </div>
      <div>
        <span
          className={cn(
            "inline-block rounded border px-2 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em]",
            camera.role === "primary"
              ? "border-led-deep bg-led/10 text-led"
              : "border-rule-strong bg-surface-3 text-ink-2",
          )}
        >
          {camera.role}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 font-mono text-[0.5625rem] tabular-nums">
        <span className="text-muted uppercase tracking-[0.06em]">Stages</span>
        {camera.stage_numbers.slice(0, 8).map((n) => (
          <span
            key={n}
            className="rounded border border-rule bg-surface-3 px-1.5 py-0.5 font-semibold text-ink-2"
          >
            {pad2(n)}
          </span>
        ))}
        {camera.stage_numbers.length > 8 && (
          <span className="rounded border border-rule bg-surface-2 px-1.5 py-0.5 text-subtle">
            +{camera.stage_numbers.length - 8}
          </span>
        )}
        {missing > 0 && (
          <span className="rounded border border-rule-strong bg-surface-2 px-1.5 py-0.5 text-subtle">
            -{missing} missing
          </span>
        )}
      </div>
      <span className="text-right font-mono text-[0.6875rem] font-semibold tabular-nums text-ink">
        {camera.video_count} {camera.video_count === 1 ? "video" : "videos"}
      </span>
    </div>
  );
}

function StatusPill({
  status,
}: {
  status: "complete" | "in_progress" | "new";
}) {
  const cfg = {
    complete: {
      label: "Complete",
      cls: "border-done/40 bg-done/10 text-done",
      icon: <CheckCircle2 className="size-3" strokeWidth={2.5} />,
    },
    in_progress: {
      label: "In progress",
      cls: "border-live/40 bg-live/10 text-live",
      icon: <span className="size-1.5 animate-pulse rounded-full bg-live" />,
    },
    new: {
      label: "Just imported",
      cls: "border-beep/40 bg-beep-tint text-beep",
      icon: <span className="size-1.5 rounded-full bg-beep" />,
    },
  }[status];
  return (
    <span
      className={cn(
        "inline-flex min-h-7 items-center gap-1.5 rounded-full border px-2.5 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em]",
        cfg.cls,
      )}
    >
      {cfg.icon}
      {cfg.label}
    </span>
  );
}

function SectionHead({
  title,
  help,
}: {
  title: string;
  help?: string;
}) {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-4">
      <div className="font-display text-base font-bold uppercase tracking-[0.08em] text-ink">
        {title}
      </div>
      {help && (
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          {help}
        </span>
      )}
    </div>
  );
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 0 || !parts[0]) return "??";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
