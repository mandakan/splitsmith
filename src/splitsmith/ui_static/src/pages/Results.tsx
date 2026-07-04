/**
 * Results - read-only match results overview. One card per stage; each
 * row inside is one shooter's run: time + status, tap -> stage playback.
 * Desktop (lg+) renders the same rows as a stages-x-shooters matrix.
 * Read-only by contract: this surface (and everything under
 * components/results/) is the future share-view; no mutations, no
 * operator-only assumptions. See the 2026-07-04 spec.
 */

import { useEffect, useMemo, useState } from "react";
import { Link, useOutletContext } from "react-router-dom";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { Kicker } from "@/components/ui";
import { api } from "@/lib/api";
import { buildStageMatrix, matchTotals } from "@/lib/stageMatrix";
import { statusLabel } from "@/lib/stageStatus";
import { useMatchHref } from "@/lib/matchHref";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/* Status chip                                                                 */
/* -------------------------------------------------------------------------- */

/** Per-tone Tailwind class string for the status chip. Color is always a
 *  redundant cue - the chip carries a text label (accessibility requirement). */
const CHIP_TONE: Record<string, string> = {
  done: "border-led-deep bg-led/15 text-led",
  in_progress: "border-live/50 bg-live/10 text-live",
  ready: "border-rule-strong bg-surface-3 text-ink-2",
  partial: "border-beep/40 bg-beep-tint text-beep",
  todo: "border-rule bg-surface-2 text-whisper",
  skipped: "border-rule bg-surface-2 text-muted",
};

function StatusChip({ tone, status }: { tone: string; status: string }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center rounded border px-2 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.1em]",
        CHIP_TONE[tone] ?? CHIP_TONE.todo,
      )}
    >
      {statusLabel(status as Parameters<typeof statusLabel>[0])}
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                     */
/* -------------------------------------------------------------------------- */

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function formatDate(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return iso;
  const day = String(d.getUTCDate()).padStart(2, "0");
  const months = [
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
  ];
  return `${day} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/** Format a stage duration as "MM:SS.ss" (omit minutes if zero). */
function formatTime(seconds: number): string {
  if (!seconds || seconds <= 0) return "-";
  const mins = Math.floor(seconds / 60);
  const secs = seconds - mins * 60;
  const secsStr = secs.toFixed(2).padStart(5, "0");
  return mins > 0 ? `${pad2(mins)}:${secsStr}` : secsStr;
}

/* -------------------------------------------------------------------------- */
/* Page                                                                        */
/* -------------------------------------------------------------------------- */

export function Results() {
  const { project, shooters } = useOutletContext<MatchShellOutletContext>();
  const href = useMatchHref();

  const rows = useMemo(
    () => (project ? buildStageMatrix(project.stages, shooters) : []),
    [project, shooters],
  );
  const totals = useMemo(() => matchTotals(rows, shooters), [rows, shooters]);

  const isSingleShooter = shooters.length <= 1;

  // Per-shooter stage times. The outlet-context project belongs to ONE
  // shooter (the URL/default one), so multi-shooter matches fetch every
  // shooter's project (read-only GET) and pivot to slug -> stage -> time.
  // Null while in flight; a shooter whose fetch failed is simply absent
  // from the map - its cells render the status chip without a time. A
  // wrong time is worse than no time on a results surface.
  const [shooterStageTimes, setShooterStageTimes] = useState<Record<
    string,
    Record<number, number>
  > | null>(null);
  useEffect(() => {
    if (shooters.length <= 1) {
      setShooterStageTimes(null);
      return;
    }
    let alive = true;
    setShooterStageTimes(null);
    Promise.all(
      shooters.map((s) =>
        api
          .getProject(s.slug)
          .then((p) => [s.slug, p] as const)
          .catch(() => [s.slug, null] as const),
      ),
    ).then((entries) => {
      if (!alive) return;
      const map: Record<string, Record<number, number>> = {};
      for (const [slug, p] of entries) {
        if (!p) continue;
        map[slug] = Object.fromEntries(
          p.stages.map((st) => [st.stage_number, st.time_seconds]),
        );
      }
      setShooterStageTimes(map);
    });
    return () => {
      alive = false;
    };
  }, [shooters]);

  // Stage time for one cell, or null when unknown (multi-shooter fetch
  // still in flight / failed for that shooter). Single-shooter reads the
  // outlet-context project directly - it IS that shooter's project.
  const cellTime = (slug: string, stageNumber: number): number | null => {
    if (isSingleShooter) {
      return (
        project?.stages.find((s) => s.stage_number === stageNumber)
          ?.time_seconds ?? null
      );
    }
    const t = shooterStageTimes?.[slug]?.[stageNumber];
    return typeof t === "number" ? t : null;
  };

  // Total match time: single-shooter only. In multi-shooter mode there is
  // no single "match total" to show in the header (each shooter has their
  // own), so the header omits it rather than showing one shooter's sum.
  const totalTimeSecs = useMemo(
    () =>
      isSingleShooter && project
        ? project.stages.reduce((sum, s) => sum + (s.time_seconds ?? 0), 0)
        : 0,
    [isSingleShooter, project],
  );

  if (!project) {
    return (
      <div className="px-4 py-16 text-center text-muted">
        <Kicker>Loading</Kicker>
        <p className="mt-4 font-mono text-xs uppercase tracking-[0.14em]">
          Standby...
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-[1100px] mx-auto px-4 md:px-7 pb-20 pt-6">
      {/* Match header */}
      <header className="mb-6">
        <Kicker className="mb-2">Results</Kicker>
        <h1 className="font-display text-3xl font-bold uppercase leading-none tracking-tight text-ink mb-2">
          {project.name}
        </h1>
        <div className="flex flex-wrap items-center gap-3 font-mono text-xs uppercase tracking-[0.06em] text-muted">
          {project.match_date ? (
            <time
              dateTime={project.match_date}
              className="border-r border-rule pr-3 font-bold text-ink-2"
            >
              {formatDate(project.match_date)}
            </time>
          ) : null}
          {totalTimeSecs > 0 ? (
            <span className="border-r border-rule pr-3">
              <span className="font-mono tabular-nums text-ink-2">
                {formatTime(totalTimeSecs)}
              </span>
              {" "}match total
            </span>
          ) : null}
          <span>
            <span className="font-bold text-ink-2">{totals.auditedShooterStages}</span>
            {" / "}
            <span>{totals.totalShooterStages}</span>
            {" audited"}
          </span>
        </div>
      </header>

      {/* Mobile: one card per stage */}
      <div className="lg:hidden space-y-3">
        {rows.map((row) => {
          return (
            <section
              key={row.stageNumber}
              className="rounded-xl border border-rule-strong bg-surface-2 overflow-hidden"
            >
              {/* Stage kicker */}
              <div className="flex items-center gap-2.5 px-4 py-2.5 border-b border-rule">
                <span className="font-mono text-[0.625rem] font-bold uppercase tracking-[0.14em] text-muted">
                  Stage {pad2(row.stageNumber)}
                </span>
                {row.stageName ? (
                  <span className="truncate font-display text-xs font-bold uppercase tracking-[0.06em] text-ink-2">
                    {row.stageName}
                  </span>
                ) : null}
              </div>
              {/* Shooter rows */}
              <div className="divide-y divide-rule">
                {row.cells.map((cell) => {
                  const audited = cell.status === "audited";
                  if (audited) {
                    const time = cellTime(cell.shooter.slug, row.stageNumber);
                    return (
                      <Link
                        key={cell.shooter.slug}
                        to={href("results", cell.shooter.slug, String(row.stageNumber))}
                        className="flex min-h-11 items-center gap-3 px-4 py-2 hover:bg-surface-3 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led focus-visible:ring-inset"
                      >
                        {!isSingleShooter && (
                          <span className="flex-1 truncate font-display text-sm font-semibold uppercase tracking-tight text-ink">
                            {cell.shooter.name}
                          </span>
                        )}
                        <span
                          className={cn(
                            "font-mono text-sm tabular-nums text-ink-2",
                            isSingleShooter && "flex-1",
                          )}
                        >
                          {time != null ? formatTime(time) : "-"}
                        </span>
                        <StatusChip tone={cell.tone} status={cell.status} />
                      </Link>
                    );
                  }
                  // Skipped rows carry their state in the chip alone - a
                  // "Not audited" label next to a "Skipped" chip contradicts
                  // itself (skipping was a decision, not missing work).
                  const skipped = cell.status === "skipped";
                  return (
                    <div
                      key={cell.shooter.slug}
                      className="flex min-h-11 items-center gap-3 px-4 py-2"
                    >
                      {!isSingleShooter && (
                        <span className="flex-1 truncate font-display text-sm font-semibold uppercase tracking-tight text-subtle">
                          {cell.shooter.name}
                        </span>
                      )}
                      {skipped ? (
                        isSingleShooter && <span aria-hidden className="flex-1" />
                      ) : (
                        <span
                          className={cn(
                            "font-mono text-xs uppercase tracking-[0.08em] text-subtle",
                            isSingleShooter && "flex-1",
                          )}
                        >
                          Not audited
                        </span>
                      )}
                      <StatusChip tone={cell.tone} status={cell.status} />
                    </div>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>

      {/* Desktop (lg+): stages x shooters matrix */}
      <div className="hidden lg:block">
        {/* Header row */}
        <div
          className="mb-1 grid gap-px"
          style={{
            gridTemplateColumns: `200px repeat(${Math.max(shooters.length, 1)}, 1fr)`,
          }}
        >
          <div className="px-3 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-subtle">
            Stage
          </div>
          {isSingleShooter ? (
            <div className="px-3 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-subtle">
              Result
            </div>
          ) : (
            shooters.map((s) => (
              <div
                key={s.slug}
                className="px-3 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-subtle truncate"
              >
                {s.name}
              </div>
            ))
          )}
        </div>
        {/* Stage rows */}
        <div className="space-y-px">
          {rows.map((row) => {
            return (
              <div
                key={row.stageNumber}
                className="grid gap-px rounded-lg overflow-hidden border border-rule-strong bg-surface-2"
                style={{
                  gridTemplateColumns: `200px repeat(${Math.max(shooters.length, 1)}, 1fr)`,
                }}
              >
                {/* Stage label cell */}
                <div className="flex items-center gap-2 bg-surface px-3 py-3">
                  <span className="font-mono text-xs font-bold tabular-nums text-muted">
                    {pad2(row.stageNumber)}
                  </span>
                  <span className="truncate font-display text-xs font-semibold uppercase tracking-[0.04em] text-ink">
                    {row.stageName || `Stage ${row.stageNumber}`}
                  </span>
                </div>
                {/* Shooter cells */}
                {row.cells.map((cell) => {
                  const audited = cell.status === "audited";
                  if (audited) {
                    const time = cellTime(cell.shooter.slug, row.stageNumber);
                    return (
                      <Link
                        key={cell.shooter.slug}
                        to={href("results", cell.shooter.slug, String(row.stageNumber))}
                        className="flex min-h-11 items-center justify-between gap-2 bg-surface-2 px-3 py-2 hover:bg-surface-3 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led focus-visible:ring-inset"
                      >
                        <span className="font-mono text-sm tabular-nums text-ink-2">
                          {time != null ? formatTime(time) : "-"}
                        </span>
                        <StatusChip tone={cell.tone} status={cell.status} />
                      </Link>
                    );
                  }
                  // Skipped cells: chip only (see the mobile rows note).
                  return (
                    <div
                      key={cell.shooter.slug}
                      className={cn(
                        "flex min-h-11 items-center gap-2 bg-surface-2 px-3 py-2",
                        cell.status === "skipped"
                          ? "justify-end"
                          : "justify-between",
                      )}
                    >
                      {cell.status !== "skipped" && (
                        <span className="font-mono text-xs uppercase tracking-[0.08em] text-subtle">
                          -
                        </span>
                      )}
                      <StatusChip tone={cell.tone} status={cell.status} />
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
