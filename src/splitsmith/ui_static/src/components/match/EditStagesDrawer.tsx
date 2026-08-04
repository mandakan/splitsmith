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
 * shows what happened instead of closing. ``error`` alone does not mean
 * that shooter's list failed to save, though -- ``saved`` is the
 * discriminator: ``saved=false`` is a real per-shooter failure (their
 * project doc was never written), while ``saved=true`` with an ``error``
 * means the edit committed for that shooter and only a cleanup step
 * (video release, audit delete, or the artifact purge raising) came up
 * short. Individual files or storage objects the purge could not delete
 * are best-effort and appear in ``summary.errors`` only, so a shooter with
 * orphaned cache files can still report ``error: null``.
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
  type MatchStageDefinition,
  type StageEditRow,
  type StageEditSummary,
} from "@/lib/api";

export interface EditStagesDrawerProps {
  open: boolean;
  onClose: () => void;
  /** The MATCH's stage list (``GET /api/match/stages``), never a shooter's
   *  ``project.stages``. The server diffs the submission against
   *  ``Match.stages``, and the two documents diverge permanently once a
   *  scoreboard is linked -- submitting a shooter's copy reports every
   *  untouched stage as renamed and wipes ``stage_rounds`` off the match
   *  (which the ensemble's adaptive Voter C reads as ``expected``). */
  stages: MatchStageDefinition[];
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
  /** Whether the source ``StageEntry.stage_rounds`` was a non-null object
   *  (even one whose fields were all null). Preserved so an all-blank row
   *  round-trips to the SAME shape it came in as -- otherwise coercing an
   *  untouched ``{expected: null, paper_targets: null, steel_targets:
   *  null}`` down to a bare ``null`` on submit makes the server's
   *  ``stage_rounds`` comparison see a change that never happened, which
   *  it then counts as a no-op rename. */
  hadStageRounds: boolean;
}

let nextDraftId = -1;

function rowsFromStages(stages: MatchStageDefinition[]): EditRow[] {
  return stages.map((s) => ({
    key: `stage-${s.stage_number}`,
    stageNumber: s.stage_number,
    stageName: s.stage_name,
    expectedRounds: s.stage_rounds?.expected ?? null,
    paperTargets: s.stage_rounds?.paper_targets ?? null,
    steelTargets: s.stage_rounds?.steel_targets ?? null,
    removed: false,
    hadStageRounds: s.stage_rounds !== null,
  }));
}

/** Branch on the error body shapes this endpoint can return: a bare
 *  string ``detail`` (400 validation) sets ``ApiError.detail`` directly,
 *  while both 409 shapes carry ``detail: {code, message}`` which lands on
 *  ``ApiError.body`` as an object -- ``err.detail`` for those would just
 *  be a stringified blob, so the object case must be checked first.
 *
 *  FastAPI's 422 is a fourth shape and needs its own branch ahead of the
 *  object one: its ``detail`` is an ARRAY of pydantic errors, which passes
 *  ``typeof === "object"`` and fails ``"message" in body``, so it would
 *  otherwise fall through to ``err.detail`` -- a raw ``JSON.stringify`` of
 *  the pydantic blob, rendered verbatim into the alert. */
function stageEditErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.body;
    if (Array.isArray(body)) {
      const messages = body
        .map((entry) =>
          entry && typeof entry === "object" && "msg" in entry
            ? String((entry as { msg: unknown }).msg)
            : null,
        )
        .filter((m): m is string => m !== null);
      return messages.length > 0
        ? messages.join("; ")
        : "The stage list was rejected as invalid.";
    }
    if (body && typeof body === "object" && "message" in body) {
      return String((body as { message: unknown }).message);
    }
    return err.detail;
  }
  return err instanceof Error ? err.message : String(err);
}

function removalConfirmBody(toRemove: EditRow[], shooterCount: number): string {
  const numbers = toRemove
    .map((r) => r.stageNumber)
    .filter((n): n is number => n !== null);
  const label =
    numbers.length === 1 ? `stage ${numbers[0]}` : `stages ${numbers.join(", ")}`;
  const pronoun = numbers.length === 1 ? "its" : "their";
  const pronounCap = numbers.length === 1 ? "Its" : "Their";
  // "all N shooters" reads oddly at N=1 ("all 1 shooter") -- drop "all"
  // in that case rather than special-casing the whole sentence.
  const forShooters =
    shooterCount === 1 ? "for 1 shooter" : `for all ${shooterCount} shooters`;
  return (
    `Removing ${label} deletes ${pronoun} audit and trims ${forShooters}. ` +
    `${pronounCap} videos move to unassigned so you can re-attach them. ` +
    // The server allocates from a persisted counter the SPA never sees
    // (Match.next_stage_number) -- non-reuse is a real guarantee now, but
    // the specific next number is not something this client can predict.
    `Stage numbers are not reused, so the number you remove will not come back.`
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
        hadStageRounds: false,
      },
    ]);
  }

  async function handleSaveClick() {
    if (saveDisabled) return;
    if (toRemove.length > 0) {
      const confirmed = await confirm({
        title:
          toRemove.length === 1
            ? "Remove 1 stage?"
            : `Remove ${toRemove.length} stages?`,
        body: removalConfirmBody(toRemove, shooterCount),
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
          r.expectedRounds == null &&
          r.paperTargets == null &&
          r.steelTargets == null &&
          !r.hadStageRounds
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
                numbering, and a new stage gets the next number after the
                highest one ever used, not the gap.
              </p>

              {error ? (
                <div
                  role="alert"
                  className="mb-4 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive"
                >
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
  // Distinguish rows for screen-reader users -- with an identical label
  // on every row, "Remove stage" announced six times in a row gives no
  // way to tell which one is under the cursor.
  const rowIdentity = isNew ? "new stage" : `stage ${row.stageNumber}`;

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
        aria-label={row.removed ? `Undo remove for ${rowIdentity}` : `Remove ${rowIdentity}`}
        className="inline-flex size-7 items-center justify-center rounded-md text-subtle transition-colors hover:bg-led/10 hover:text-led disabled:opacity-50"
      >
        {row.removed ? <RotateCcw className="size-3.5" /> : <X className="size-3.5" />}
      </button>
    </div>
  );
}

/** The entries of ``summary.errors`` that no per-shooter row already shows.
 *
 *  The server's contract (documented on ``StageEditSummary.errors`` in
 *  ``stage_edit.py``): whenever a shooter row carries a non-null ``error``,
 *  the matching ``summary.errors`` entry is exactly `${slug}: ${error}` --
 *  for BOTH failure paths. The outer per-shooter failure sets
 *  ``error = str(exc)``; the per-stage cleanup failure sets
 *  ``error = "stage N: ..."``. Neither string equals its general-list entry
 *  on its own, which is why this rebuilds the prefixed form rather than
 *  comparing ``error`` verbatim -- doing that dedupped only the path whose
 *  ``error`` happened to already carry the slug, and rendered the other
 *  failed shooter twice.
 *
 *  What must survive the filter: best-effort purge failures (a cache file
 *  or storage object that would not delete). Those land in
 *  ``summary.errors`` as `${slug}: ${detail}` with NO shooter row behind
 *  them -- the shooter can report ``error: null`` -- so this list is the
 *  only place they are ever shown. That is also why filtering by slug
 *  instead of by the full string would be wrong: it would hide them.
 *
 *  Exported only so it can be unit-tested (there is no React testing
 *  library here, and adding one for this is not worth it), which costs the
 *  file its fast-refresh boundary -- same trade as
 *  ``StageReferenceDrawer.tsx``. */
// eslint-disable-next-line react-refresh/only-export-components
export function generalErrors(summary: StageEditSummary): string[] {
  const accountedFor = new Set(
    summary.shooters
      .filter((s) => s.error != null)
      .map((s) => `${s.slug}: ${s.error}`),
  );
  return summary.errors.filter((e) => !accountedFor.has(e));
}

/** Post-save panel for a summary that carries cleanup errors. The edit
 *  itself committed -- ``removed``/``added``/``renamed`` reflect what
 *  actually changed -- so this reads as "saved, with some issues" (amber,
 *  not red) rather than a failure banner.
 *
 *  A shooter's ``error`` alone does not mean that shooter's list wasn't
 *  saved -- ``saved`` is the discriminator (see the docstring on
 *  ``ShooterStageEditResult`` in ``stage_edit.py``, mirrored on the TS
 *  interface in ``api.ts``): ``saved=false`` means the project doc was
 *  never written and the shooter's stage list is unchanged, while
 *  ``saved=true`` with a non-null ``error`` means the list DID save and
 *  only a cleanup step (video release, audit delete, or the purge raising)
 *  failed afterward. The two get separate lists so a cleanup hiccup never
 *  reads as "your edit didn't take". */
function SaveResult({ result }: { result: StageEditSummary }) {
  const notSaved = result.shooters.filter((s) => !s.saved);
  const cleanupIssues = result.shooters.filter((s) => s.saved && s.error != null);
  // Keep the per-shooter lists (they carry the saved / not-saved
  // distinction) and show only what no shooter row already accounts for.
  const general = generalErrors(result);
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2 rounded-md border border-status-warning/40 bg-status-warning/10 p-3 text-sm text-ink">
        <AlertTriangle className="size-4 shrink-0 text-status-warning" />
        <div>
          <p className="font-semibold">Stages saved, with some issues</p>
          <p className="mt-1 text-xs text-muted">
            The stage list change committed ({result.removed.length} removed,{" "}
            {result.added.length} added, {result.renamed.length} renamed). The
            problems below are what didn't fully finish -- they don't all
            mean the edit itself failed.
          </p>
        </div>
      </div>

      {general.length > 0 ? (
        <ul className="list-disc space-y-1 pl-5 text-xs text-muted">
          {general.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      ) : null}

      {notSaved.length > 0 ? (
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wide text-subtle">
            Not saved for these shooters
          </p>
          <ul className="space-y-1 text-xs text-muted">
            {notSaved.map((s) => (
              <li key={s.slug}>
                <span className="font-mono text-ink">{s.slug}</span>: {s.error}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {cleanupIssues.length > 0 ? (
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wide text-subtle">
            Saved, but cleanup didn't fully finish
          </p>
          <ul className="space-y-1 text-xs text-muted">
            {cleanupIssues.map((s) => (
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
