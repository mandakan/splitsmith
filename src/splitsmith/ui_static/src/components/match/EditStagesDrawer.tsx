/**
 * EditStagesDrawer -- add, remove, and rename stages on the bound match (#521).
 *
 * Modal dialog contract copied from ``MobileNav`` (same directory): Portal
 * to body, ``z-drawer``, focus trap + Escape + focus restore via
 * ``useDialogFocus``, backdrop click closes. Row markup (the numbered
 * badge / name field / expected-shots field / remove button grid) is
 * copied from the manual-create stage editor in ``CreateMatch.tsx`` so
 * this looks like it already belongs -- but the badge here shows the
 * real, possibly-gapped ``stage_number`` as muted secondary identity, and
 * is never the row's list position. A stage's number is stable for its
 * lifetime, not reused once freed, and a freshly added row has none yet
 * (the server allocates it), so new rows render "New" instead of a number.
 *
 * Save posts the FULL desired list to ``PUT /api/match/stages``; the
 * server diffs it against what's stored. Rows marked for removal stay
 * visible (struck through) until Save so the user can change their mind,
 * but are simply omitted from the submitted list -- there is no
 * "removed: true" wire flag. A never-saved draft row has no server-side
 * existence to mark for removal, so its remove button deletes it from the
 * list outright instead of toggling a strikethrough.
 *
 * The edit endpoint can return three different error body shapes (see
 * Task 8's report): a plain string ``detail`` (400 validation), or an
 * object ``detail: {code, message}`` for both 409s (lost optimistic lock,
 * or no project bound). ``stageEditErrorMessage`` below branches on
 * ``typeof body`` first, exactly as the report says it must.
 *
 * A 200 response is still not necessarily a clean success: on success we
 * call ``onSaved(summary)`` unconditionally (the caller's stage list did
 * change and needs a refresh), but if ``summary.errors`` is non-empty or
 * any ``ShooterStageEditResult.error`` is set, the drawer stays open and
 * shows what didn't fully clean up instead of closing -- that failure is
 * real and per-shooter, even though the edit itself committed.
 */

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, Plus, RotateCcw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Portal } from "@/components/ui/Portal";
import { useConfirm } from "@/components/useConfirm";
import { useDialogFocus } from "@/lib/dialogFocus";
import { cn } from "@/lib/utils";
import {
  ApiError,
  api,
  type StageEditRow,
  type StageEditSummary,
  type StageEntry,
} from "@/lib/api";

export interface EditStagesDrawerProps {
  open: boolean;
  onClose: () => void;
  stages: StageEntry[];
  shooterCount: number;
  onSaved: (summary: StageEditSummary) => void;
}

/** One row's local edit state. ``key`` is a stable client id for React's
 *  reconciliation -- existing stages key off their ``stage_number``, but
 *  a freshly added row can't: every new row's ``stageNumber`` is null
 *  until Save allocates one, so two new rows would collide on that. */
interface EditRow {
  key: string;
  stageNumber: number | null;
  stageName: string;
  expectedRounds: number | null;
  paperTargets: number | null;
  steelTargets: number | null;
  removed: boolean;
}

let nextDraftId = -1;

function rowsFromStages(stages: StageEntry[]): EditRow[] {
  return stages.map((s) => ({
    key: `stage-${s.stage_number}`,
    stageNumber: s.stage_number,
    stageName: s.stage_name,
    expectedRounds: s.stage_rounds?.expected ?? null,
    paperTargets: s.stage_rounds?.paper_targets ?? null,
    steelTargets: s.stage_rounds?.steel_targets ?? null,
    removed: false,
  }));
}

/** Branch on the three error body shapes from Task 8's report: a bare
 *  string ``detail`` (400 validation) sets ``ApiError.detail`` directly,
 *  while both 409 shapes carry ``detail: {code, message}`` which lands on
 *  ``ApiError.body`` as an object -- ``err.detail`` for those would just
 *  be a stringified blob, so the object case must be checked first. */
function stageEditErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body;
    if (body && typeof body === "object" && "message" in body) {
      return String((body as { message: unknown }).message);
    }
    return err.detail;
  }
  return err instanceof Error ? err.message : String(err);
}

function removalConfirmBody(
  toRemove: EditRow[],
  shooterCount: number,
  nextStageNumber: number,
): string {
  const numbers = toRemove
    .map((r) => r.stageNumber)
    .filter((n): n is number => n !== null);
  const label =
    numbers.length === 1 ? `stage ${numbers[0]}` : `stages ${numbers.join(", ")}`;
  const pronoun = numbers.length === 1 ? "its" : "their";
  const pronounCap = numbers.length === 1 ? "Its" : "Their";
  const shooterWord = shooterCount === 1 ? "shooter" : "shooters";
  return (
    `Removing ${label} deletes ${pronoun} audit and trims for all ${shooterCount} ${shooterWord}. ` +
    `${pronounCap} videos move to unassigned so you can re-attach them. ` +
    `Stage numbers are not reused -- the next stage you add will be ${nextStageNumber}.`
  );
}

export function EditStagesDrawer({
  open,
  onClose,
  stages,
  shooterCount,
  onSaved,
}: EditStagesDrawerProps) {
  const [rows, setRows] = useState<EditRow[]>(() => rowsFromStages(stages));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<StageEditSummary | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const confirm = useConfirm();

  // Reset local edit state whenever the drawer opens -- otherwise a
  // second open would carry over edits (or a stale save result) from a
  // previous session against what may now be a different stage list.
  useEffect(() => {
    if (open) {
      setRows(rowsFromStages(stages));
      setError(null);
      setResult(null);
    }
    // `stages` is a fresh array identity on most parent renders regardless
    // of content; `open` is what actually gates the reset.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useDialogFocus(open, panelRef, onClose, { disableEscape: saving });

  if (!open) return null;

  const remaining = rows.filter((r) => !r.removed);
  const toRemove = rows.filter((r) => r.removed);
  const hasBlankName = remaining.some((r) => r.stageName.trim() === "");
  const saveDisabled = saving || remaining.length === 0 || hasBlankName;

  function updateRow(key: string, patch: Partial<EditRow>) {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  function toggleOrDiscard(row: EditRow) {
    if (row.stageNumber === null) {
      // Never saved server-side -- nothing to mark for removal, just
      // drop the draft row.
      setRows((prev) => prev.filter((r) => r.key !== row.key));
    } else {
      setRows((prev) =>
        prev.map((r) => (r.key === row.key ? { ...r, removed: !r.removed } : r)),
      );
    }
  }

  function addRow() {
    setRows((prev) => [
      ...prev,
      {
        key: `draft-${nextDraftId--}`,
        stageNumber: null,
        stageName: "",
        expectedRounds: null,
        paperTargets: null,
        steelTargets: null,
        removed: false,
      },
    ]);
  }

  async function handleSaveClick() {
    if (saveDisabled) return;
    if (toRemove.length > 0) {
      const existingMax = stages.reduce((m, s) => Math.max(m, s.stage_number), 0);
      const draftsInThisSave = remaining.filter((r) => r.stageNumber === null).length;
      const nextStageNumber = existingMax + draftsInThisSave + 1;
      const confirmed = await confirm({
        title:
          toRemove.length === 1
            ? "Remove 1 stage?"
            : `Remove ${toRemove.length} stages?`,
        body: removalConfirmBody(toRemove, shooterCount, nextStageNumber),
        confirmLabel: "Remove and save",
      });
      if (!confirmed.confirmed) return;
    }
    await save();
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const payload: StageEditRow[] = remaining.map((r) => ({
        stage_number: r.stageNumber,
        stage_name: r.stageName,
        stage_rounds:
          r.expectedRounds == null && r.paperTargets == null && r.steelTargets == null
            ? null
            : {
                expected: r.expectedRounds,
                paper_targets: r.paperTargets,
                steel_targets: r.steelTargets,
              },
      }));
      const summary = await api.editMatchStages(payload);
      onSaved(summary);
      const hasIssues =
        summary.errors.length > 0 || summary.shooters.some((s) => s.error != null);
      if (hasIssues) {
        setResult(summary);
      } else {
        onClose();
      }
    } catch (e) {
      setError(stageEditErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Portal>
      <div
        aria-hidden
        className="fixed inset-0 z-drawer bg-bg/70"
        onClick={saving ? undefined : onClose}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-stages-title"
        className="fixed inset-y-0 right-0 z-drawer flex w-full max-w-2xl flex-col border-l border-rule bg-surface shadow-xl"
      >
        <div className="flex items-center justify-between border-b border-rule px-5 py-4">
          <h2
            id="edit-stages-title"
            className="font-display text-base font-bold uppercase tracking-tight text-ink"
          >
            Edit stages
          </h2>
          <Button
            size="sm"
            variant="ghost"
            onClick={onClose}
            disabled={saving}
            aria-label="Close"
          >
            <X className="size-4" />
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {result ? (
            <SaveResult result={result} />
          ) : (
            <>
              <p className="mb-4 text-[0.8125rem] text-muted">
                Stage numbers are stable for a stage's lifetime and are
                never reused -- removing a stage leaves a gap in the
                numbering, and a new stage always gets the next free one.
              </p>

              {error ? (
                <div className="mb-4 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                  <AlertTriangle className="size-4 shrink-0" />
                  <span>{error}</span>
                </div>
              ) : null}

              <div className="overflow-hidden rounded-xl border border-rule bg-bg-glow">
                <div
                  className="grid items-center gap-3.5 border-b border-rule bg-surface-2 px-4 py-2.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle"
                  style={{ gridTemplateColumns: "56px 1fr 130px 36px" }}
                >
                  <span>#</span>
                  <span>Name</span>
                  <span className="text-right">Expected shots</span>
                  <span />
                </div>
                {rows.map((row) => (
                  <EditStageRow
                    key={row.key}
                    row={row}
                    disabled={saving}
                    onChange={(patch) => updateRow(row.key, patch)}
                    onToggleRemove={() => toggleOrDiscard(row)}
                  />
                ))}
                <div className="flex items-center justify-between bg-surface-2 px-4 py-2.5 font-mono text-[0.6875rem] uppercase tracking-[0.08em] text-muted">
                  <span>
                    <b className="font-bold text-ink">{remaining.length}</b>{" "}
                    stage{remaining.length === 1 ? "" : "s"} after save
                  </span>
                  <button
                    type="button"
                    onClick={addRow}
                    disabled={saving}
                    className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-rule-strong px-3 py-1.5 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-led transition-colors hover:border-led hover:bg-led/10 disabled:opacity-50"
                  >
                    <Plus className="size-3" />
                    Add stage
                  </button>
                </div>
              </div>
            </>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-rule px-5 py-4">
          {result ? (
            <Button type="button" className="ml-auto" onClick={onClose}>
              Done
            </Button>
          ) : (
            <>
              <Button type="button" variant="ghost" onClick={onClose} disabled={saving}>
                Cancel
              </Button>
              <Button type="button" onClick={handleSaveClick} disabled={saveDisabled}>
                {saving ? <Loader2 className="size-4 animate-spin" /> : null}
                Save
              </Button>
            </>
          )}
        </div>
      </div>
    </Portal>
  );
}

function EditStageRow({
  row,
  disabled,
  onChange,
  onToggleRemove,
}: {
  row: EditRow;
  disabled: boolean;
  onChange: (patch: Partial<EditRow>) => void;
  onToggleRemove: () => void;
}) {
  const isNew = row.stageNumber === null;
  const rowDisabled = disabled || row.removed;

  return (
    <div
      className={cn(
        "grid items-center gap-3.5 border-b border-rule px-4 py-2.5 last:border-b-0",
        row.removed && "opacity-60",
      )}
      style={{ gridTemplateColumns: "56px 1fr 130px 36px" }}
    >
      {/* Muted secondary identity, never the row's ordinal -- a gapped
          list (1, 2, 4, 5) must show its real numbers, and a row the
          user just added shows no number at all since the server
          allocates it on save. */}
      {isNew ? (
        <span className="inline-flex h-8 items-center justify-center rounded-md border border-dashed border-rule px-1 font-mono text-[0.625rem] font-semibold uppercase tracking-wide text-subtle">
          New
        </span>
      ) : (
        <span className="inline-flex h-8 items-center justify-center rounded-md border border-rule px-1 font-mono text-xs tabular-nums text-subtle">
          #{row.stageNumber}
        </span>
      )}
      <input
        type="text"
        value={row.stageName}
        onChange={(e) => onChange({ stageName: e.target.value })}
        placeholder="Stage name"
        disabled={rowDisabled}
        className={cn(
          "w-full rounded-lg border border-rule bg-surface-3 px-3 py-2 text-[0.8125rem] text-ink outline-none transition-all",
          "focus:border-led focus:bg-bg-glow focus:shadow-[0_0_0_3px_var(--color-led-tint)]",
          "disabled:cursor-not-allowed disabled:opacity-60",
          row.removed && "line-through",
        )}
      />
      <input
        type="number"
        value={row.expectedRounds ?? ""}
        onChange={(e) =>
          onChange({
            expectedRounds: e.target.value === "" ? null : Number(e.target.value),
          })
        }
        placeholder="12"
        disabled={rowDisabled}
        className={cn(
          "w-full rounded-lg border border-rule bg-surface-3 px-3 py-2 text-right font-mono text-[0.8125rem] tabular-nums text-ink outline-none transition-all",
          "focus:border-led focus:bg-bg-glow focus:shadow-[0_0_0_3px_var(--color-led-tint)]",
          "disabled:cursor-not-allowed disabled:opacity-60",
          row.removed && "line-through",
        )}
      />
      <button
        type="button"
        onClick={onToggleRemove}
        disabled={disabled}
        aria-label={row.removed ? "Undo remove" : "Remove stage"}
        className="inline-flex size-7 items-center justify-center rounded-md text-subtle transition-colors hover:bg-led/10 hover:text-led disabled:opacity-50"
      >
        {row.removed ? <RotateCcw className="size-3.5" /> : <X className="size-3.5" />}
      </button>
    </div>
  );
}

/** Post-save panel for a summary that carries cleanup errors. The edit
 *  itself committed -- ``removed``/``added``/``renamed`` reflect what
 *  actually changed -- so this reads as "saved, with some issues" (amber,
 *  not red) rather than a failure banner. Per-shooter errors are called
 *  out individually: a non-null ``error`` on a shooter means that
 *  shooter's stage list specifically was NOT saved. */
function SaveResult({ result }: { result: StageEditSummary }) {
  const failedShooters = result.shooters.filter((s) => s.error != null);
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2 rounded-md border border-status-warning/40 bg-status-warning/10 p-3 text-sm text-ink">
        <AlertTriangle className="size-4 shrink-0 text-status-warning" />
        <div>
          <p className="font-semibold">Stages saved, with some issues</p>
          <p className="mt-1 text-xs text-muted">
            The stage list change committed ({result.removed.length} removed,{" "}
            {result.added.length} added, {result.renamed.length} renamed). The
            problems below are cleanup that didn't fully finish -- they
            don't mean the edit itself failed.
          </p>
        </div>
      </div>

      {result.errors.length > 0 ? (
        <ul className="list-disc space-y-1 pl-5 text-xs text-muted">
          {result.errors.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      ) : null}

      {failedShooters.length > 0 ? (
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wide text-subtle">
            Not saved for these shooters
          </p>
          <ul className="space-y-1 text-xs text-muted">
            {failedShooters.map((s) => (
              <li key={s.slug}>
                <span className="font-mono text-ink">{s.slug}</span>: {s.error}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
