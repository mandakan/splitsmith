/**
 * FileChip -- one video on the Footage page (UX PR 6): a mono chip with
 * the role as its tick (red primary, grey secondary, hollow ignored), the
 * file's short name, and its beep state as a mark at the end when there is
 * one to show (``chipBeepMark``: a primary always, a secondary only when
 * its beep is off). A button that opens the clip sheet.
 */
import type { StageVideo } from "@/lib/api";
import { BEEP_WORDS, shortName, type VideoBeep } from "@/lib/footage";
import { cn } from "@/lib/utils";

export interface FileChipProps {
  video: StageVideo;
  /** The beep mark to show, or null for none. */
  beep?: VideoBeep | null;
  current?: boolean;
  onOpen: (video: StageVideo) => void;
}

const TICK: Record<StageVideo["role"], string> = {
  primary: "bg-led",
  secondary: "bg-muted",
  ignored: "border border-rule-strong bg-transparent",
};

/** Stage-state colours: green done, amber for the user to act, muted while
 *  a job runs. */
const MARK: Record<VideoBeep, { glyph: string; className: string }> = {
  confirmed: { glyph: "✓", className: "text-done" },
  detected: { glyph: "?", className: "text-live" },
  low: { glyph: "?", className: "text-live" },
  missing: { glyph: "!", className: "text-live" },
  detecting: { glyph: "…", className: "text-subtle" },
};

export function FileChip({ video, beep = null, current = false, onOpen }: FileChipProps) {
  const base = video.path.split("/").pop() ?? video.path;
  const said = beep ? `, ${BEEP_WORDS[beep]}` : "";
  return (
    <button
      type="button"
      onClick={() => onOpen(video)}
      title={`${base} · ${video.role}${beep ? ` · ${BEEP_WORDS[beep]}` : ""}`}
      aria-label={`${base}, ${video.role}${said}`}
      className={cn(
        "inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-sm transition-colors hover:border-ink-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led",
        video.role === "ignored" ? "border-rule text-subtle" : "border-rule-strong text-ink-2",
        current && "border-ink-2 text-ink",
      )}
    >
      <i aria-hidden className={cn("size-1.5 shrink-0 rounded-full", TICK[video.role])} />
      <span className="truncate">{shortName(video.path)}</span>
      {beep ? (
        <span aria-hidden className={cn("shrink-0 font-sans", MARK[beep].className)}>
          {MARK[beep].glyph}
        </span>
      ) : null}
    </button>
  );
}
