/**
 * Triage - the responsive stage worklist (slice 4, mobile audit triage
 * program, #700 follow-up). One card per shooter-stage cell that still
 * needs attention: accept it, flag it for a closer look on desktop, or
 * jump to its results. Cards group by stage; anything already closed
 * out (audited or skipped, and not flagged) collapses into a "Done"
 * section at the bottom so the page always leads with the work that's
 * left. Responsive by design - it doubles as the desktop worklist, so
 * this page is not behind DesktopGate.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Loader2 } from "lucide-react";

import { AnomalyChips } from "@/components/audit/AnomalyChips";
import { MobileConfirmSheet } from "@/components/MobileConfirmSheet";
import { Kicker, Skeleton, StatusPill } from "@/components/ui";
import { StageDot } from "@/components/ui/StageDot";
import { ApiError, api, type TriageCell, type TriageResponse } from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";
import { statusLabel } from "@/lib/stageStatus";

type PendingAction =
  | { kind: "accept"; cell: TriageCell }
  | { kind: "flag"; cell: TriageCell }
  | { kind: "unflag"; cell: TriageCell };

function cellKey(cell: TriageCell): string {
  return `${cell.slug}::${cell.stage_number}`;
}

function isDone(cell: TriageCell): boolean {
  return (cell.status === "audited" || cell.status === "skipped") && !cell.needs_attention?.flagged;
}

/** Map a 409 accept conflict to operator-readable copy. Unknown details
 *  fall back to the raw detail string rather than swallowing it. */
function acceptErrorMessage(detail: string): string {
  switch (detail) {
    case "nothing_to_accept":
      return "Nothing to accept yet - no kept shots on this stage.";
    case "not_fully_classified":
      return "Some shots could not be classified - finish this stage on desktop.";
    case "version_conflict":
      return "This stage changed elsewhere - reload to see the latest.";
    default:
      return detail;
  }
}

export function Triage() {
  const href = useMatchHref();
  const [data, setData] = useState<TriageResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [confirming, setConfirming] = useState<PendingAction | null>(null);
  const [note, setNote] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [cardErrors, setCardErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    let active = true;
    setLoadError(null);
    api
      .getTriage()
      .then((res) => {
        if (active) setData(res);
      })
      .catch((e) => {
        if (active) setLoadError(e instanceof ApiError ? e.detail : String(e));
      });
    return () => {
      active = false;
    };
  }, [reloadToken]);

  const openAccept = (cell: TriageCell) => {
    setCardErrors((prev) => ({ ...prev, [cellKey(cell)]: "" }));
    setConfirming({ kind: "accept", cell });
  };
  const openFlag = (cell: TriageCell) => {
    setNote("");
    setConfirming({ kind: "flag", cell });
  };
  const openUnflag = (cell: TriageCell) => {
    setConfirming({ kind: "unflag", cell });
  };
  const closeSheet = () => setConfirming(null);

  const runAccept = async (cell: TriageCell) => {
    const key = cellKey(cell);
    setConfirming(null);
    setBusyKey(key);
    setCardErrors((prev) => ({ ...prev, [key]: "" }));
    try {
      const res = await api.acceptStage(cell.slug, cell.stage_number);
      setData(res);
    } catch (e) {
      const message =
        e instanceof ApiError && e.status === 409
          ? acceptErrorMessage(e.detail)
          : e instanceof ApiError
            ? e.detail
            : String(e);
      setCardErrors((prev) => ({ ...prev, [key]: message }));
    } finally {
      setBusyKey(null);
    }
  };

  const runAttention = async (cell: TriageCell, body: { flagged: boolean; note?: string | null }) => {
    const key = cellKey(cell);
    setConfirming(null);
    setBusyKey(key);
    setCardErrors((prev) => ({ ...prev, [key]: "" }));
    try {
      const res = await api.setStageAttention(cell.slug, cell.stage_number, body);
      setData(res);
    } catch (e) {
      const message = e instanceof ApiError ? e.detail : String(e);
      setCardErrors((prev) => ({ ...prev, [key]: message }));
    } finally {
      setBusyKey(null);
    }
  };

  if (loadError) {
    return (
      <div className="mx-auto max-w-md px-4 pb-24 pt-4 md:max-w-3xl">
        <Kicker className="mb-3">Triage</Kicker>
        <div role="alert" className="rounded-md border border-led/40 bg-led/10 px-3 py-3 text-sm text-led">
          {loadError}
        </div>
        <button
          type="button"
          onClick={() => setReloadToken((t) => t + 1)}
          className="mt-3 min-h-11 rounded border border-rule px-4 text-sm text-ink"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="mx-auto max-w-md px-4 pb-24 pt-4 md:max-w-3xl" role="status" aria-label="Loading triage">
        <Kicker className="mb-3">Triage</Kicker>
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="rounded-lg border border-rule bg-surface p-4">
              <Skeleton className="mb-2 h-4 w-32" />
              <Skeleton className="h-3 w-full" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  const activeCells = data.cells.filter((c) => !isDone(c));
  const doneCells = data.cells.filter(isDone);

  const stages = Array.from(new Set(activeCells.map((c) => c.stage_number))).sort((a, b) => a - b);
  const cellsByStage = new Map<number, TriageCell[]>();
  for (const c of activeCells) {
    const list = cellsByStage.get(c.stage_number) ?? [];
    list.push(c);
    cellsByStage.set(c.stage_number, list);
  }

  return (
    <div className="mx-auto max-w-md px-4 pb-24 pt-4 md:max-w-3xl">
      <header className="mb-4 flex items-center justify-between">
        <Kicker>Triage</Kicker>
        {data.flagged_count > 0 ? (
          <StatusPill tone="led">
            {data.flagged_count} flagged
          </StatusPill>
        ) : null}
      </header>

      {activeCells.length === 0 && doneCells.length === 0 ? (
        <p className="px-1 py-10 text-center text-sm text-muted" role="status">
          Nothing in triage - no stages yet.
        </p>
      ) : null}

      {activeCells.length === 0 && doneCells.length > 0 ? (
        <p className="mb-4 px-1 text-sm text-muted" role="status">
          All clear - every stage is done.
        </p>
      ) : null}

      <div className="space-y-6">
        {stages.map((stageNumber) => {
          const cells = cellsByStage.get(stageNumber) ?? [];
          return (
            <section key={stageNumber}>
              <h2 className="mb-2 font-display text-sm font-bold uppercase tracking-[0.1em] text-ink-2">
                Stage {stageNumber} - {cells[0]?.stage_name}
              </h2>
              <div className="space-y-3">
                {cells.map((cell) => (
                  <TriageCard
                    key={cellKey(cell)}
                    cell={cell}
                    href={href}
                    busy={busyKey === cellKey(cell)}
                    error={cardErrors[cellKey(cell)]}
                    lowConfidenceThreshold={data.beep_low_confidence_threshold}
                    onAccept={() => openAccept(cell)}
                    onFlag={() => openFlag(cell)}
                    onUnflag={() => openUnflag(cell)}
                  />
                ))}
              </div>
            </section>
          );
        })}
      </div>

      {doneCells.length > 0 ? (
        <details className="mt-6">
          <summary className="cursor-pointer font-display text-sm font-bold uppercase tracking-[0.1em] text-muted">
            Done ({doneCells.length})
          </summary>
          <div className="mt-3 space-y-2">
            {doneCells.map((cell) => (
              <div
                key={cellKey(cell)}
                className="flex items-center justify-between gap-3 rounded-lg border border-rule bg-surface px-4 py-3"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <StageDot status={cell.status} />
                  <span className="truncate text-sm text-ink">
                    Stage {cell.stage_number} - {cell.stage_name} - {cell.shooter_name}
                  </span>
                  <span className="shrink-0 text-xs text-muted">{statusLabel(cell.status)}</span>
                </div>
                <Link
                  to={href("results", cell.slug, String(cell.stage_number))}
                  className="min-h-11 shrink-0 rounded border border-rule px-3 text-sm text-ink no-underline flex items-center"
                >
                  Results
                </Link>
              </div>
            ))}
          </div>
        </details>
      ) : null}

      <MobileConfirmSheet
        open={confirming?.kind === "accept"}
        title="Accept stage?"
        body={
          confirming?.kind === "accept"
            ? `Mark ${confirming.cell.shooter_name}'s stage ${confirming.cell.stage_number} as audited.`
            : ""
        }
        confirmLabel="Accept"
        onConfirm={() => {
          if (confirming?.kind === "accept") void runAccept(confirming.cell);
        }}
        onCancel={closeSheet}
      />
      <MobileConfirmSheet
        open={confirming?.kind === "flag"}
        title="Flag for desktop?"
        body={
          <label className="block">
            <span className="mb-1 block text-sm text-ink">Note (optional)</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={280}
              rows={3}
              className="w-full rounded border border-rule bg-bg px-3 py-2 text-sm text-ink"
            />
          </label>
        }
        confirmLabel="Flag for desktop"
        onConfirm={() => {
          if (confirming?.kind === "flag") {
            void runAttention(confirming.cell, { flagged: true, note: note || undefined });
          }
        }}
        onCancel={closeSheet}
      />
      <MobileConfirmSheet
        open={confirming?.kind === "unflag"}
        title="Unflag this stage?"
        body={
          confirming?.kind === "unflag"
            ? `Clear the flag on ${confirming.cell.shooter_name}'s stage ${confirming.cell.stage_number}.`
            : ""
        }
        confirmLabel="Unflag"
        onConfirm={() => {
          if (confirming?.kind === "unflag") void runAttention(confirming.cell, { flagged: false });
        }}
        onCancel={closeSheet}
      />
    </div>
  );
}

function TriageCard({
  cell,
  href,
  busy,
  error,
  lowConfidenceThreshold,
  onAccept,
  onFlag,
  onUnflag,
}: {
  cell: TriageCell;
  href: (...segments: string[]) => string;
  busy: boolean;
  error: string | undefined;
  lowConfidenceThreshold: number;
  onAccept: () => void;
  onFlag: () => void;
  onUnflag: () => void;
}) {
  const terminal = cell.status === "audited" || cell.status === "skipped";
  const flagged = cell.needs_attention?.flagged ?? false;
  const lowConfidence = cell.beep_confidence != null && cell.beep_confidence < lowConfidenceThreshold;

  return (
    <div className="rounded-lg border border-rule bg-surface p-4">
      <div className="mb-2 flex items-center gap-2">
        <StageDot status={cell.status} />
        <span className="font-bold text-ink">{cell.shooter_name}</span>
        <span className="text-xs text-muted">{statusLabel(cell.status)}</span>
      </div>

      {lowConfidence ? (
        <div className="mb-2">
          <StatusPill tone="awaiting" icon={<AlertTriangle aria-hidden className="size-3" />}>
            Beep {Math.round((cell.beep_confidence ?? 0) * 100)}%
          </StatusPill>
        </div>
      ) : null}

      {cell.anomalies.length > 0 ? (
        <div className="mb-2">
          <AnomalyChips anomalies={cell.anomalies} />
        </div>
      ) : null}

      {flagged ? (
        <div className="mb-2">
          <StatusPill tone="in-progress">Flagged for desktop</StatusPill>
          {cell.needs_attention?.note ? (
            <p className="mt-1 text-sm text-muted">{cell.needs_attention.note}</p>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        {!terminal ? (
          <button
            type="button"
            disabled={busy}
            onClick={onAccept}
            className="btn-led-fill inline-flex min-h-11 items-center justify-center rounded-md px-4 text-sm disabled:opacity-40"
          >
            {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : "Accept"}
          </button>
        ) : null}
        {flagged ? (
          <button
            type="button"
            disabled={busy}
            onClick={onUnflag}
            className="min-h-11 rounded border border-rule px-4 text-sm text-ink disabled:opacity-40"
          >
            Unflag
          </button>
        ) : (
          <button
            type="button"
            disabled={busy}
            onClick={onFlag}
            className="min-h-11 rounded border border-rule px-4 text-sm text-ink disabled:opacity-40"
          >
            Flag
          </button>
        )}
        <Link
          to={href("results", cell.slug, String(cell.stage_number))}
          className="min-h-11 inline-flex items-center rounded border border-rule px-4 text-sm text-ink no-underline"
        >
          Results
        </Link>
      </div>

      {error ? (
        <p role="alert" className="mt-3 text-sm text-led">
          {error}
        </p>
      ) : null}
    </div>
  );
}
