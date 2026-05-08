/**
 * Relink Sources dialog.
 *
 * The user picks a search root; the server walks it recursively and
 * matches files by basename against the project's registered ``raw/``
 * symlinks. Repeatable -- after one apply, the list of unresolved
 * rows shrinks and the user can pick another root for the rest.
 *
 * Statuses (LinkStatus): ok / broken / missing_link / not_a_symlink.
 * Only ok / broken / missing_link rows are eligible for relinking;
 * not_a_symlink rows are surfaced read-only with an explanation.
 */

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, FolderSearch, Link2, Link2Off } from "lucide-react";

import { FolderPicker } from "@/components/FolderPicker";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ApiError,
  api,
  type LinkStatus,
  type LinkStatusEntry,
  type RelinkEntry,
} from "@/lib/api";

interface RelinkDialogProps {
  onClose: () => void;
  onApplied?: () => void;
}

interface RowState {
  /** From /api/videos/link-status -- always present, even before any
   *  scan has run. */
  link: LinkStatusEntry;
  /** Most recent scan result for this row, if any. */
  scan: RelinkEntry | null;
  /** User's current pick (path). Defaults to ``scan.chosen_path`` when
   *  the scan auto-resolved, but the user can override. */
  picked: string | null;
}

function statusLabel(status: LinkStatus): string {
  switch (status) {
    case "ok":
      return "OK";
    case "broken":
      return "Broken target";
    case "missing_link":
      return "Symlink missing";
    case "not_a_symlink":
      return "Plain file";
  }
}

function statusBadgeClass(status: LinkStatus): string {
  switch (status) {
    case "ok":
      return "border-status-complete/40 bg-status-complete/10 text-status-complete";
    case "broken":
    case "missing_link":
      return "border-status-warning/40 bg-status-warning/10 text-status-warning";
    case "not_a_symlink":
      return "border-border bg-muted text-muted-foreground";
  }
}

export function RelinkDialog({ onClose, onApplied }: RelinkDialogProps) {
  const [rows, setRows] = useState<RowState[]>([]);
  const [searchRoot, setSearchRoot] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scannedRoots, setScannedRoots] = useState<string[]>([]);
  const [appliedCount, setAppliedCount] = useState(0);

  // Initial load: just the link status, no scan yet.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const resp = await api.getLinkStatus();
        if (cancelled) return;
        setRows(
          resp.entries.map((link) => ({ link, scan: null, picked: null })),
        );
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const counts = useMemo(() => {
    const out = { ok: 0, broken: 0, missing: 0, plain: 0 };
    for (const row of rows) {
      if (row.link.status === "ok") out.ok += 1;
      else if (row.link.status === "broken") out.broken += 1;
      else if (row.link.status === "missing_link") out.missing += 1;
      else out.plain += 1;
    }
    return out;
  }, [rows]);

  const eligibleForApply = useMemo(
    () => rows.filter((r) => r.picked && r.link.status !== "not_a_symlink"),
    [rows],
  );

  const runScan = async (root: string) => {
    setBusy(true);
    setError(null);
    try {
      const resp = await api.relinkScan(root);
      setRows((prev) =>
        prev.map((row) => {
          const found = resp.entries.find((e) => e.video_id === row.link.video_id);
          if (!found) return row;
          // Only auto-fill ``picked`` when the row hasn't been resolved
          // yet by a previous scan. This way iterating across multiple
          // search roots accumulates resolutions without clobbering them.
          const picked =
            row.picked ?? (found.chosen_path && !found.ambiguous ? found.chosen_path : null);
          return { ...row, scan: found, picked };
        }),
      );
      setSearchRoot(resp.search_root);
      setScannedRoots((prev) =>
        prev.includes(resp.search_root) ? prev : [...prev, resp.search_root],
      );
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const applyAll = async () => {
    if (eligibleForApply.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const decisions: Record<string, string> = {};
      for (const row of eligibleForApply) {
        if (row.picked) decisions[row.link.video_id] = row.picked;
      }
      const resp = await api.relinkApply(decisions);
      setAppliedCount((c) => c + resp.applied.length);
      // Refresh statuses so applied rows flip to ``ok`` and the user
      // sees them turn green.
      const fresh = await api.getLinkStatus();
      setRows((prev) =>
        fresh.entries.map((link) => {
          const old = prev.find((r) => r.link.video_id === link.video_id);
          return {
            link,
            scan: old?.scan ?? null,
            // Once relinked, drop the pick so the row no longer counts
            // as "to apply". A subsequent scan can repopulate it.
            picked: link.status === "ok" ? null : old?.picked ?? null,
          };
        }),
      );
      onApplied?.();
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="relink-dialog-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-4"
      onClick={onClose}
    >
      <Card
        className="flex max-h-[90vh] w-full max-w-3xl flex-col shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <CardHeader>
          <CardTitle id="relink-dialog-title" className="flex items-center gap-2">
            <Link2 className="size-5" />
            Relink sources
          </CardTitle>
          <CardDescription>
            Repoint the <code>raw/</code> symlinks at new locations after the
            originals moved (e.g. onto a network share). <code>project.json</code>{" "}
            is never modified -- only the symlinks under <code>raw/</code> are
            rewritten.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 overflow-y-auto text-sm">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full border border-status-complete/40 bg-status-complete/10 px-2 py-0.5 text-status-complete">
              {counts.ok} OK
            </span>
            {counts.broken > 0 ? (
              <span className="rounded-full border border-status-warning/40 bg-status-warning/10 px-2 py-0.5 text-status-warning">
                {counts.broken} broken
              </span>
            ) : null}
            {counts.missing > 0 ? (
              <span className="rounded-full border border-status-warning/40 bg-status-warning/10 px-2 py-0.5 text-status-warning">
                {counts.missing} missing
              </span>
            ) : null}
            {counts.plain > 0 ? (
              <span className="rounded-full border border-border bg-muted px-2 py-0.5 text-muted-foreground">
                {counts.plain} plain
              </span>
            ) : null}
            {appliedCount > 0 ? (
              <span className="ml-auto text-xs text-muted-foreground">
                Repointed {appliedCount} link{appliedCount === 1 ? "" : "s"} this session.
              </span>
            ) : null}
          </div>

          {pickerOpen ? (
            <div className="rounded-md border border-border p-2">
              <FolderPicker
                onSelect={async (path) => {
                  setPickerOpen(false);
                  await runScan(path);
                }}
                onCancel={() => setPickerOpen(false)}
                mode="inline"
                allowEmptyFolder
                selectLabel="Scan this folder"
              />
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setPickerOpen(true)}
                disabled={busy}
              >
                <FolderSearch className="size-4" />
                {scannedRoots.length === 0 ? "Pick search folder..." : "Add another folder..."}
              </Button>
              {scannedRoots.length > 0 ? (
                <span className="text-xs text-muted-foreground">
                  Scanned: {scannedRoots.join(" · ")}
                </span>
              ) : null}
            </div>
          )}

          {error ? (
            <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
              <AlertTriangle className="size-4 shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}

          <div className="space-y-1">
            {rows.length === 0 ? (
              <div className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                No registered videos in this project.
              </div>
            ) : (
              rows.map((row) => (
                <RelinkRow
                  key={row.link.video_id}
                  row={row}
                  onPick={(path) =>
                    setRows((prev) =>
                      prev.map((r) =>
                        r.link.video_id === row.link.video_id ? { ...r, picked: path } : r,
                      ),
                    )
                  }
                  disabled={busy}
                />
              ))
            )}
          </div>
        </CardContent>
        <div className="flex items-center justify-between gap-2 border-t border-border p-4">
          <div className="text-xs text-muted-foreground">
            {eligibleForApply.length === 0
              ? searchRoot
                ? "Pick a candidate per row to enable apply."
                : "Pick a search folder to find candidates."
              : `${eligibleForApply.length} row${eligibleForApply.length === 1 ? "" : "s"} ready to relink.`}
          </div>
          <div className="flex items-center gap-2">
            <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
              Close
            </Button>
            <Button
              type="button"
              onClick={applyAll}
              disabled={busy || eligibleForApply.length === 0}
            >
              <Link2 className="size-4" />
              Apply ({eligibleForApply.length})
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

interface RelinkRowProps {
  row: RowState;
  onPick: (path: string | null) => void;
  disabled: boolean;
}

function RelinkRow({ row, onPick, disabled }: RelinkRowProps) {
  const { link, scan, picked } = row;
  const candidates = scan?.candidates ?? [];
  const isOk = link.status === "ok";
  const isPlain = link.status === "not_a_symlink";

  return (
    <div className="rounded-md border border-border p-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs">{link.name}</span>
        <span
          className={`rounded-full border px-2 py-0.5 text-xs ${statusBadgeClass(link.status)}`}
        >
          {statusLabel(link.status)}
        </span>
        {picked ? (
          <span className="ml-auto inline-flex items-center gap-1 text-xs text-status-complete">
            <CheckCircle2 className="size-3.5" />
            picked
          </span>
        ) : null}
      </div>
      <div className="mt-1 break-all text-xs text-muted-foreground">
        {link.current_target ? (
          <>
            <Link2 className="mr-1 inline size-3" />
            {link.current_target}
          </>
        ) : (
          <>
            <Link2Off className="mr-1 inline size-3" />
            no target
          </>
        )}
      </div>
      {isPlain ? (
        <div className="mt-2 text-xs text-muted-foreground">
          Regular file -- not a symlink. Relink can't help here. (See the
          ingest copy-mode work in #245.)
        </div>
      ) : candidates.length === 0 ? (
        scan ? (
          <div className="mt-2 text-xs text-muted-foreground">
            Not found under the last search folder. Try another root.
          </div>
        ) : null
      ) : candidates.length === 1 ? (
        <div className="mt-2 flex items-start gap-2">
          <input
            type="checkbox"
            className="mt-1"
            checked={picked === candidates[0]}
            disabled={disabled || isOk}
            onChange={(e) => onPick(e.target.checked ? candidates[0] : null)}
          />
          <div className="flex-1 break-all text-xs">
            <span className="text-muted-foreground">-&gt;</span> {candidates[0]}
          </div>
        </div>
      ) : (
        <div className="mt-2">
          <div className="text-xs text-status-warning">
            {candidates.length} candidates -- pick one:
          </div>
          <div className="mt-1 space-y-1">
            {candidates.map((path) => (
              <label
                key={path}
                className="flex cursor-pointer items-start gap-2 rounded-md border border-border p-1 hover:bg-muted/40"
              >
                <input
                  type="radio"
                  name={`pick-${link.video_id}`}
                  className="mt-1"
                  checked={picked === path}
                  disabled={disabled}
                  onChange={() => onPick(path)}
                />
                <span className="flex-1 break-all text-xs">{path}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
