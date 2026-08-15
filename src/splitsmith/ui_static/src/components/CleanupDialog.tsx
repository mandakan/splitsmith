/**
 * Reclaim-space dialog.
 *
 * The only caller of ``api.getCleanupPlan`` / ``api.applyCleanup`` -- both
 * routes have existed since the hosted-storage cleanup work landed, but
 * nothing in the SPA ever reached them until this dialog. The CLI's
 * ``splitsmith cleanup`` remains the only way in for a desktop user; a
 * hosted user has no shell, so without this the feature is unreachable.
 *
 * Category selection is what the server acts on -- ``cleanup_apply``
 * re-plans server-side from the categories it is sent, so the client
 * never sends paths, only categories, and the per-item checkboxes in the
 * "cannot be rebuilt" section cannot scope the request to individual
 * files. What they CAN do, and what they are wired to do, is gate the
 * Confirm button: an unrebuildable item's whole category is refused --
 * both in the UI (Confirm stays disabled) and again inside ``apply``
 * itself, so a stray Enter-key submit can't bypass the disabled state --
 * until every unrebuildable item currently in the plan has been
 * individually ticked. Opted-in state is pruned whenever the plan
 * refetches (category toggle), so a tick can never authorise a file the
 * user can no longer see.
 *
 * Unreconstructable items are shown, never hidden -- silently omitting a
 * multi-gigabyte trim from a list that promises what can be reclaimed
 * would leave the user no way to learn why it vanished. They are also
 * kept out of "select all", for the same reason audit-data is: both cost
 * data, not recompute time, to delete.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, HardDrive } from "lucide-react";

import {
  api,
  asJobsActiveError,
  type CleanupCategory,
  type CleanupPlan,
  type JobsActiveDetail,
} from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { Portal } from "@/components/ui/Portal";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useDialogFocus } from "@/lib/dialogFocus";

/** Every category except audit-data, which destroys the user's audit work
 *  rather than costing recompute time. Mirrors ``SAFE_CATEGORIES`` in
 *  ``splitsmith/cleanup.py`` -- the server re-plans from the categories it
 *  is sent, so this list is convenience, never enforcement. */
const SAFE: CleanupCategory[] = [
  "caches",
  "exports-light",
  "exports-overlays",
  "exports-trims",
  "audit-trims",
  "audio",
];

const ALL: CleanupCategory[] = [...SAFE, "audit-data"];

const LABELS: Record<CleanupCategory, string> = {
  caches: "Thumbnails, probes and waveform caches",
  "exports-light": "CSV, FCPXML and reports",
  "exports-overlays": "Rendered overlays",
  "exports-trims": "Lossless export trims",
  "audit-trims": "Audit scrub copies",
  audio: "Extracted audio",
  "audit-data": "Audit data (your shot edits)",
};

export function CleanupDialog({
  slug,
  open,
  onClose,
}: {
  slug: string;
  open: boolean;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<CleanupCategory[]>([]);
  const [plan, setPlan] = useState<CleanupPlan | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [applying, setApplying] = useState(false);
  const [blocked, setBlocked] = useState<JobsActiveDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** Per-item explicit consent, keyed by ``storage_key ?? path``. Consent
   *  only gates the Confirm button -- see the module doc comment for why
   *  it cannot scope the request itself. */
  const [optedIn, setOptedIn] = useState<Set<string>>(new Set());
  const panelRef = useRef<HTMLDivElement | null>(null);

  useDialogFocus(open, panelRef, onClose, { disableEscape: applying });

  // Debounced: the plan route returns an empty plan for unknown or partial
  // selections rather than a 400, specifically so this can fetch on every
  // toggle. That contract was written for a caller that never arrived
  // until now.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const t = setTimeout(() => {
      api
        .getCleanupPlan(slug, selected)
        .then((p) => {
          if (!cancelled) setPlan(p);
        })
        .catch(() => {
          if (!cancelled) setError("Could not read the cleanup plan.");
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [slug, selected, open]);

  /** Items the current plan cannot rebuild. Shown, never hidden -- see the
   *  module doc comment. */
  const unrebuildable = useMemo(
    () => (plan?.items ?? []).filter((i) => !i.reconstructable),
    [plan],
  );

  // A category toggle refetches the plan, which can drop an unrebuildable
  // item from view (or add a new one). Prune consent for anything no
  // longer present: a stale tick must not silently authorise a file the
  // user can no longer see.
  useEffect(() => {
    const visible = new Set(unrebuildable.map((i) => i.storage_key ?? i.path));
    setOptedIn((prev) => {
      const next = new Set([...prev].filter((k) => visible.has(k)));
      return next.size === prev.size ? prev : next;
    });
  }, [unrebuildable]);

  // ``plan === null`` (not yet fetched, or the request is still in flight)
  // must NOT read as "nothing unrebuildable" -- ``unrebuildable`` is
  // derived from ``plan?.items ?? []``, so before the first response lands
  // it is vacuously empty and ``.every()`` on it is vacuously true. Without
  // this check, a fast confirm before the debounced fetch resolves would
  // bypass consent entirely.
  const allUnrebuildableOptedIn = useMemo(
    () => plan !== null && unrebuildable.every((i) => optedIn.has(i.storage_key ?? i.path)),
    [plan, unrebuildable, optedIn],
  );

  const toggle = useCallback((c: CleanupCategory) => {
    setSelected((prev) =>
      prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c],
    );
  }, []);

  const toggleOptIn = useCallback((key: string) => {
    setOptedIn((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const checkAllUnrebuildable = useCallback(() => {
    setOptedIn(new Set(unrebuildable.map((i) => i.storage_key ?? i.path)));
  }, [unrebuildable]);

  const apply = useCallback(async () => {
    // Belt-and-suspenders alongside the disabled Confirm button: this is
    // the actual gate, so a stray Enter-key submit on the surface can't
    // reach applyCleanup without going through the disabled control.
    if (selected.length === 0 || !allUnrebuildableOptedIn) return;
    setError(null);
    setBlocked(null);
    setApplying(true);
    try {
      await api.applyCleanup(slug, selected);
      setConfirming(false);
      onClose();
    } catch (e) {
      const jobsActive = asJobsActiveError(e);
      if (jobsActive) {
        setBlocked(jobsActive);
      } else {
        setError("Cleanup failed.");
      }
    } finally {
      setApplying(false);
    }
  }, [slug, selected, allUnrebuildableOptedIn, onClose]);

  if (!open) return null;

  return (
    <Portal>
      <div
        className="fixed inset-0 z-modal flex items-center justify-center bg-bg/70 p-4"
        onClick={applying ? undefined : onClose}
      >
        <Card
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label="Reclaim space"
          className="flex max-h-[90vh] w-full max-w-lg flex-col shadow-xl"
          onClick={(e) => e.stopPropagation()}
        >
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <HardDrive className="size-5" />
              Reclaim space
            </CardTitle>
            <CardDescription>
              Delete regenerable local files and their object-storage copies.
              Nothing here is required for splitsmith to keep working -- it
              can all be rebuilt from source, except where noted below.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 overflow-y-auto text-sm">
            <div className="flex items-center justify-between gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setSelected(SAFE)}
                disabled={applying}
              >
                Select all
              </Button>
              {plan ? (
                <span className="text-xs font-medium text-muted">
                  Total: {formatBytes(plan.total_bytes)}
                </span>
              ) : null}
            </div>

            <ul className="space-y-1">
              {ALL.map((c) => {
                const t = plan?.totals_by_category?.[c];
                return (
                  <li
                    key={c}
                    className="flex items-center justify-between gap-2 rounded-md border border-rule p-2"
                  >
                    <label className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        className="size-4 accent-led disabled:cursor-not-allowed"
                        checked={selected.includes(c)}
                        onChange={() => toggle(c)}
                        aria-label={LABELS[c]}
                        disabled={applying}
                      />
                      <span>{LABELS[c]}</span>
                    </label>
                    {t ? (
                      <span className="whitespace-nowrap text-xs text-muted">
                        {t.file_count} file{t.file_count === 1 ? "" : "s"},{" "}
                        {formatBytes(t.bytes)}
                      </span>
                    ) : null}
                  </li>
                );
              })}
            </ul>

            {unrebuildable.length > 0 ? (
              <section
                aria-label="cannot be rebuilt"
                className="space-y-2 rounded-md border border-status-warning/40 bg-status-warning/10 p-2"
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="flex items-start gap-2 text-xs text-status-warning">
                    <AlertTriangle className="size-4 shrink-0" />
                    <span>
                      {unrebuildable.length} of these cannot be rebuilt -- their
                      source or audit data is already gone. Deleting them loses
                      the file for good. Confirm stays disabled until each one
                      below is individually checked.
                    </span>
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={checkAllUnrebuildable}
                    disabled={applying || allUnrebuildableOptedIn}
                  >
                    Check all
                  </Button>
                </div>
                <ul className="space-y-1">
                  {unrebuildable.map((i) => {
                    const key = i.storage_key ?? i.path;
                    return (
                      <li key={key}>
                        <label className="flex items-center gap-2 text-xs">
                          <input
                            type="checkbox"
                            className="size-4 accent-led disabled:cursor-not-allowed"
                            checked={optedIn.has(key)}
                            onChange={() => toggleOptIn(key)}
                            aria-label={i.path.split("/").pop() ?? i.path}
                            disabled={applying}
                          />
                          <span>
                            {i.path.split("/").pop()} ({formatBytes(i.size_bytes)})
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ) : null}

            {blocked ? (
              <p
                role="alert"
                className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive"
              >
                <AlertTriangle className="size-4 shrink-0" />
                <span>
                  {blocked.kind} is still running. Wait for it to finish, or
                  cancel it, before reclaiming space.
                </span>
              </p>
            ) : null}
            {error ? (
              <p
                role="alert"
                className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive"
              >
                <AlertTriangle className="size-4 shrink-0" />
                <span>{error}</span>
              </p>
            ) : null}
          </CardContent>
          <div className="flex items-center justify-end gap-2 border-t border-rule p-4">
            <Button type="button" variant="ghost" onClick={onClose} disabled={applying}>
              Cancel
            </Button>
            {confirming ? (
              <Button
                type="button"
                onClick={apply}
                disabled={applying || !allUnrebuildableOptedIn}
              >
                Confirm
              </Button>
            ) : (
              <Button
                type="button"
                disabled={selected.length === 0}
                onClick={() => setConfirming(true)}
              >
                Reclaim
              </Button>
            )}
          </div>
        </Card>
      </div>
    </Portal>
  );
}
