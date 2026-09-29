/**
 * One desktop request's status line (#1100): what the desktop is doing
 * with it, in the SyncCard's line style, with Cancel while it can be. On a
 * narrow column the link and Cancel wrap under the line rather than
 * squeezing it, and a long link truncates.
 */
import { AlertTriangle, CheckCircle2, Clock, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { DesktopCommand, DesktopPresence } from "@/lib/api";
import { commandLine } from "@/lib/desktopCommands";
import { cn } from "@/lib/utils";

const OFFLINE: DesktopPresence = { linked: true, last_seen_at: null, around: false };

export function DesktopCommandLine({
  command,
  presence,
  onCancel,
  className,
}: {
  command: DesktopCommand;
  presence: DesktopPresence | null;
  onCancel?: (id: string) => void;
  className?: string;
}) {
  const line = commandLine(command, presence ?? OFFLINE);
  const Icon =
    line.tone === "ok" ? CheckCircle2 : line.tone === "error" ? AlertTriangle : line.tone === "live" ? Loader2 : Clock;
  return (
    <div className={cn("flex flex-wrap items-center gap-x-2 gap-y-1 text-sm", className)} role="status" aria-live="polite">
      <Icon
        className={cn(
          "size-3.5 shrink-0",
          line.tone === "ok" && "text-done",
          line.tone === "live" && "animate-spin text-live",
          line.tone === "muted" && "text-muted",
          line.tone === "error" && "text-led-text",
        )}
        aria-hidden="true"
      />
      <span className={cn("min-w-0 flex-1 basis-48", line.tone === "error" ? "text-led-text" : "text-muted")}>
        {line.text}
      </span>
      {line.link ? (
        <a className="min-w-0 max-w-full truncate text-sm text-ink underline" href={line.link.href} target="_blank" rel="noreferrer">
          {line.link.label}
        </a>
      ) : null}
      {line.cancellable && onCancel ? (
        <Button type="button" size="sm" variant="ghost" onClick={() => onCancel(command.id)}>
          Cancel
        </Button>
      ) : null}
    </div>
  );
}
