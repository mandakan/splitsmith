/**
 * FileChip -- one video on the Footage page (UX PR 6): a mono chip with
 * the role as its tick (red primary, grey secondary, hollow ignored) and
 * the file's short name. A button that opens the clip sheet.
 */
import type { StageVideo } from "@/lib/api";
import { shortName } from "@/lib/footage";
import { cn } from "@/lib/utils";

export interface FileChipProps {
  video: StageVideo;
  current?: boolean;
  onOpen: (video: StageVideo) => void;
}

const TICK: Record<StageVideo["role"], string> = {
  primary: "bg-led",
  secondary: "bg-muted",
  ignored: "border border-rule-strong bg-transparent",
};

export function FileChip({ video, current = false, onOpen }: FileChipProps) {
  const base = video.path.split("/").pop() ?? video.path;
  return (
    <button
      type="button"
      onClick={() => onOpen(video)}
      title={`${base} · ${video.role}`}
      aria-label={`${base}, ${video.role}`}
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-sm transition-colors hover:border-ink-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
        video.role === "ignored" ? "border-rule text-subtle" : "border-rule-strong text-ink-2",
        current && "border-ink-2 text-ink",
      )}
    >
      <i aria-hidden className={cn("size-1.5 shrink-0 rounded-full", TICK[video.role])} />
      <span className="truncate">{shortName(video.path)}</span>
    </button>
  );
}
