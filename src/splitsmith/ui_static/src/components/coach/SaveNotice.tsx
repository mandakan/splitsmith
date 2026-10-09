/**
 * The lane editor's one-line save notice: a region edit that did not reach
 * the server says so here instead of vanishing or replacing the page. A
 * conflict's discard is muted (nothing to do, the stage was reloaded); any
 * other failure is the destructive text with a Retry.
 */
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { SaveIssue } from "@/lib/useStageEvents";

export function SaveNotice({
  issue,
  onRetry,
  onDismiss,
}: {
  issue: SaveIssue;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const failed = issue.kind === "failed";
  return (
    <div
      role={failed ? "alert" : "status"}
      data-testid="region-save-notice"
      className="flex min-h-9 items-center gap-2 px-1 text-sm"
    >
      <p className={failed ? "min-w-0 flex-1 truncate text-destructive" : "min-w-0 flex-1 truncate text-muted"}>
        {failed
          ? `Your last region change was not saved: ${issue.message}`
          : "Your last region change was not saved. The stage changed elsewhere and was reloaded."}
      </p>
      {failed ? (
        <Button type="button" size="sm" onClick={onRetry}>
          Retry
        </Button>
      ) : null}
      <Button type="button" size="icon" variant="ghost" aria-label="Dismiss" onClick={onDismiss} className="size-8">
        <X aria-hidden />
      </Button>
    </div>
  );
}
