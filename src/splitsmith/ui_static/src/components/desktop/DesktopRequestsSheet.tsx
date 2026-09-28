/**
 * The match's desktop requests (#1100): what the phone asked the desktop
 * to run, each with its status line and a cancel while it can be. Opened
 * from the Overview header on a desktop-synced match.
 */
import { Label } from "@/components/ui/Label";
import { Sheet } from "@/components/ui/Sheet";
import type { DesktopCommand, DesktopPresence } from "@/lib/api";
import { commandTitle, presenceSummary } from "@/lib/desktopCommands";

import { DesktopCommandLine } from "./DesktopCommandLine";

export function DesktopRequestsSheet({
  open,
  onClose,
  commands,
  presence,
  onCancel,
}: {
  open: boolean;
  onClose: () => void;
  commands: DesktopCommand[];
  presence: DesktopPresence | null;
  onCancel: (id: string) => void;
}) {
  return (
    <Sheet open={open} onClose={onClose} label="Desktop requests">
      <div className="border-b border-rule px-4 py-3">
        <Label>Desktop requests</Label>
        {presence ? <p className="mt-1 text-sm text-muted">{presenceSummary(presence)}</p> : null}
      </div>
      {commands.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted">
          Nothing asked yet. Re-detect a stage on your desktop from its Audit menu.
        </p>
      ) : (
        <ul className="overflow-y-auto">
          {commands.map((c) => (
            <li key={c.id} className="border-b border-rule px-4 py-2.5">
              <p className="text-md text-ink">{commandTitle(c)}</p>
              <DesktopCommandLine command={c} presence={presence} onCancel={onCancel} className="mt-1" />
            </li>
          ))}
        </ul>
      )}
    </Sheet>
  );
}
