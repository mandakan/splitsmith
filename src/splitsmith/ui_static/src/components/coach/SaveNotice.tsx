/**
 * The one-line save notice: an edit (a region on Breakdown, a note on Coach)
 * that did not reach the server says so here instead of vanishing or
 * replacing the page. A conflict's discard is muted (nothing to do, the
 * stage was reloaded); any other failure is the destructive text with a
 * Retry, and the server's own message sits in the line's tooltip for
 * diagnosis, not in the copy.
 */
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { SaveIssue } from "@/lib/useStageEvents";

export function SaveNotice({
  issue,
  busy,
  onRetry,
  onDismiss,
  subject = "region change",
  testId = "region-save-notice",
}: {
  issue: SaveIssue;
  /** An edit is outstanding: Retry would do nothing until it settles. */
  busy: boolean;
  onRetry: () => void;
  onDismiss: () => void;
  /** What was not saved, as the line names it ("region change", "note change"). */
  subject?: string;
  testId?: string;
}) {
  const failed = issue.kind === "failed";
  return (
    <div role={failed ? "alert" : "status"} data-testid={testId} className="flex min-h-9 items-center gap-2 px-1 text-sm">
      <p
        title={failed ? issue.message : undefined}
        className={failed ? "min-w-0 flex-1 truncate text-destructive" : "min-w-0 flex-1 truncate text-muted"}
      >
        {failed
          ? `Your last ${subject} was not saved.`
          : `Your last ${subject} was not saved. The stage changed elsewhere and was reloaded.`}
      </p>
      {failed ? (
        <Button
          type="button"
          size="sm"
          onClick={onRetry}
          disabled={busy}
          title={busy ? `Wait for the current ${subject} to save` : undefined}
        >
          Retry
        </Button>
      ) : null}
      <Button type="button" size="icon" variant="ghost" aria-label="Dismiss" onClick={onDismiss} className="size-8">
        <X aria-hidden />
      </Button>
    </div>
  );
}
