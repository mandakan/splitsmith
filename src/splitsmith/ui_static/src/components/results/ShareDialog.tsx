/**
 * ShareDialog - owner-facing share-link management for a match.
 *
 * Mounted only on the owner's match route (hosted mode, no :token param).
 * Lists live and revoked links, lets the owner create new ones, copy URLs,
 * and revoke live links with a two-click confirm.
 *
 * Overlay architecture: body Portal + z-modal token + useDialogFocus (modal
 * trap). Same skeleton as ConfirmDialog and RelinkDialog.
 *
 * Accessibility (WCAG 2.2 AA): role="dialog" + aria-modal; focus moves in on
 * open, is trapped, and restores to the trigger on close; Escape closes via
 * useDialogFocus stack; copy feedback is text-only (label swap to "Copied"),
 * not color-only; revoked entries carry an explicit "Revoked" text label and
 * muted styling, never color alone.
 */

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Link2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Portal } from "@/components/ui/Portal";
import { useDialogFocus } from "@/lib/dialogFocus";
import { api, type ShareInfo } from "@/lib/api";
import { cn } from "@/lib/utils";

interface ShareDialogProps {
  onClose: () => void;
}

/** Format an ISO timestamp as "DD Mon YYYY". */
function formatShareDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const day = String(d.getUTCDate()).padStart(2, "0");
  const months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  return `${day} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

export function ShareDialog({ onClose }: ShareDialogProps) {
  const [shares, setShares] = useState<ShareInfo[] | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [armedRevoke, setArmedRevoke] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const panelRef = useRef<HTMLDivElement | null>(null);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useDialogFocus(true, panelRef, onClose);

  const loadShares = async () => {
    try {
      const resp = await api.listShares();
      // Newest first - sort descending by created_at.
      const sorted = resp.shares
        .slice()
        .sort(
          (a, b) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
        );
      setShares(sorted);
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void loadShares();
    // Run once on mount - loadShares captures setShares/setFetchError from
    // the same render; refs are stable, so the empty dep array is correct.
  }, []);

  useEffect(() => () => {
    if (copyTimerRef.current != null) clearTimeout(copyTimerRef.current);
  }, []);

  const handleCreate = async () => {
    setCreating(true);
    setFetchError(null);
    try {
      await api.createShare();
      await loadShares();
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (shareId: string) => {
    setRevoking(shareId);
    setArmedRevoke(null);
    setFetchError(null);
    try {
      await api.revokeShare(shareId);
      await loadShares();
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : String(e));
    } finally {
      setRevoking(null);
    }
  };

  const handleCopy = async (shareId: string, url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setCopiedId(shareId);
      // Clear any existing timer before setting a new one.
      if (copyTimerRef.current != null) clearTimeout(copyTimerRef.current);
      // Reset label after 2 s.
      copyTimerRef.current = setTimeout(
        () => setCopiedId((id) => (id === shareId ? null : id)),
        2000,
      );
    } catch {
      // Clipboard access denied - silently ignore.
    }
  };

  const busy = creating || revoking !== null;

  return (
    <Portal>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="share-dialog-title"
        aria-describedby="share-dialog-desc"
        className="fixed inset-0 z-modal flex items-center justify-center bg-bg/70 p-4"
        onClick={onClose}
      >
        <Card
          ref={panelRef}
          tabIndex={-1}
          className="flex max-h-[90vh] w-full max-w-lg flex-col shadow-xl outline-none"
          onClick={(e) => e.stopPropagation()}
        >
          <CardHeader>
            <CardTitle
              id="share-dialog-title"
              className="flex items-center gap-2"
            >
              <Link2 className="size-5" aria-hidden="true" />
              Share results
            </CardTitle>
            <CardDescription id="share-dialog-desc">
              Anyone with a link sees the read-only results - splits, stats,
              and video. Revoke a link to cut off access.
            </CardDescription>
          </CardHeader>

          <CardContent className="flex-1 space-y-4 overflow-y-auto text-sm">
            {/* Error banner */}
            {fetchError ? (
              <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-xs text-destructive">
                <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
                <span>{fetchError}</span>
              </div>
            ) : null}

            {/* Create link */}
            <div>
              <Button
                type="button"
                size="sm"
                onClick={() => void handleCreate()}
                disabled={busy}
              >
                <Link2 className="size-4" aria-hidden="true" />
                Create link
              </Button>
            </div>

            {/* Links list */}
            {shares === null && !fetchError ? (
              <div className="text-xs text-muted">Loading...</div>
            ) : shares !== null && shares.length === 0 ? (
              <div className="rounded-md border border-dashed border-rule p-3 text-xs text-muted">
                No links yet.
              </div>
            ) : shares !== null ? (
              <div className="space-y-2">
                {shares.map((share) => {
                  const live = share.revoked_at === null;
                  return (
                    <div
                      key={share.id}
                      className={cn(
                        "rounded-md border border-rule p-3 space-y-2 text-sm",
                        !live && "opacity-50",
                      )}
                    >
                      {/* URL row - live links only */}
                      {live ? (
                        <div className="flex items-center gap-2">
                          <input
                            type="text"
                            readOnly
                            value={share.url}
                            aria-label="Share link URL"
                            className="min-w-0 flex-1 rounded border border-rule bg-bg px-2 py-1 font-mono text-xs"
                            onFocus={(e) => e.currentTarget.select()}
                          />
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            aria-label={
                              copiedId === share.id
                                ? "URL copied to clipboard"
                                : "Copy share link to clipboard"
                            }
                            onClick={() => void handleCopy(share.id, share.url)}
                          >
                            {copiedId === share.id ? "Copied" : "Copy"}
                          </Button>
                        </div>
                      ) : null}

                      {/* Meta row: date + revoke control */}
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs text-muted">
                          {live
                            ? `Created ${formatShareDate(share.created_at)}`
                            : `Revoked ${formatShareDate(share.revoked_at!)}`}
                        </span>

                        {live ? (
                          armedRevoke === share.id ? (
                            <div className="flex items-center gap-1">
                              <Button
                                type="button"
                                variant="destructive"
                                size="sm"
                                aria-label="Confirm: revoke this share link"
                                onClick={() => void handleRevoke(share.id)}
                                disabled={revoking === share.id}
                              >
                                Confirm revoke
                              </Button>
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={() => setArmedRevoke(null)}
                                disabled={revoking === share.id}
                              >
                                Cancel
                              </Button>
                            </div>
                          ) : (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              aria-label="Revoke this share link"
                              onClick={() => setArmedRevoke(share.id)}
                              disabled={busy}
                            >
                              Revoke
                            </Button>
                          )
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : null}
          </CardContent>

          <div className="flex justify-end border-t border-rule p-4">
            <Button type="button" variant="ghost" onClick={onClose}>
              Close
            </Button>
          </div>
        </Card>
      </div>
    </Portal>
  );
}
