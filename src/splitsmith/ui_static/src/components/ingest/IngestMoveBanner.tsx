import { X } from "lucide-react";

import { ShooterPickerPopover } from "@/components/ingest/ShooterPickerPopover";
import type { MoveShooterBlocked, ShooterListEntry } from "@/lib/api";

export function IngestMoveBanner({
  shooterName,
  videoPaths,
  shooters,
  excludeSlug,
  blocked,
  busy,
  onMove,
  onDismiss,
}: {
  shooterName: string;
  videoPaths: string[];
  shooters: ShooterListEntry[];
  excludeSlug: string;
  blocked: MoveShooterBlocked[];
  busy: boolean;
  onMove: (targetSlug: string, paths: string[]) => Promise<void>;
  onDismiss: () => void;
}) {
  return (
    <div className="mb-4 overflow-hidden rounded-xl border border-beep/40 bg-beep-tint">
      <div className="relative flex flex-wrap items-center gap-3 px-4 py-3">
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-0.5 bg-beep shadow-[0_0_8px_var(--color-beep-glow)]"
        />
        <span className="font-mono text-[0.75rem] text-ink-2">
          <b className="font-bold text-beep">{videoPaths.length}</b>{" "}
          video{videoPaths.length === 1 ? "" : "s"} added to{" "}
          <b className="text-ink">{shooterName}</b>.{" "}
          <span className="text-muted">Wrong shooter?</span>
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted">
            Move all to
          </span>
          <ShooterPickerPopover
            shooters={shooters}
            excludeSlug={excludeSlug}
            busy={busy}
            onPick={(targetSlug) => void onMove(targetSlug, videoPaths)}
          />
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss banner"
          className="ml-auto rounded p-0.5 text-subtle hover:text-ink"
        >
          <X className="size-4" />
        </button>
      </div>
      {blocked.length > 0 && (
        <div className="border-t border-beep/20 bg-live/10 px-4 py-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-live">
          {blocked.length} stage{blocked.length === 1 ? "" : "s"} already had reviewed footage
          -- not moved. Resolve manually.
        </div>
      )}
    </div>
  );
}
