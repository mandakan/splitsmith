/**
 * IngestMoveBanner -- after an import on a multi-shooter match: "n videos
 * added to X, wrong shooter?" with one button per other shooter that
 * moves the whole batch (UX PR 6 restyle of the post-import banner).
 */
import { Button } from "@/components/ui/button";
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
    <div role="status" className="rounded-[10px] border border-rule bg-surface px-4 py-2.5 text-md text-ink-2">
      <div className="flex flex-wrap items-center gap-3">
        <span>
          <b className="numeral font-medium text-ink">{videoPaths.length}</b> {videoPaths.length === 1 ? "video" : "videos"} added to{" "}
          <b className="font-medium text-ink">{shooterName}</b>. <span className="text-muted">Wrong shooter? Move all to</span>
        </span>
        {shooters
          .filter((s) => s.slug !== excludeSlug)
          .map((s) => (
            <Button key={s.slug} size="sm" disabled={busy} onClick={() => void onMove(s.slug, videoPaths)}>
              {s.name}
            </Button>
          ))}
        <Button size="sm" variant="ghost" onClick={onDismiss} aria-label="Dismiss banner" className="ml-auto">
          Dismiss
        </Button>
      </div>
      {blocked.length > 0 ? (
        <p className="mt-1.5 text-sm text-live">
          {blocked.length} {blocked.length === 1 ? "stage" : "stages"} already had reviewed footage and stayed. Resolve manually.
        </p>
      ) : null}
    </div>
  );
}
