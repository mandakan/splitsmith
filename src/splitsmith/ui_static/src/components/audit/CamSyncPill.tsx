import { cn } from "@/lib/utils";

/**
 * Per-cam sync state. Mirrors the design's ARSyncPill states:
 *   synced         -- detected with high confidence (or manually
 *                     re-picked and accepted)
 *   low_confidence -- auto-detected, low confidence; needs review
 *   manual         -- operator-overridden buzzer time
 *   no_beep        -- detector hasn't run or returned nothing
 */
export type CamSyncState = "synced" | "low_confidence" | "manual" | "no_beep";

export interface CamSyncPillProps {
  state: CamSyncState;
  /** Beep time for this cam, in source seconds. Null when no_beep. */
  beepTime: number | null;
  /** Detector confidence (0..1) for low_confidence + synced. */
  beepConfidence: number | null;
  /** When set, the pill renders the offset (signed seconds) instead of
   *  the absolute time. Secondaries pass `(beep_time - primary.beep_time)`
   *  so the operator sees the cam's drift vs the primary directly on the
   *  tile rather than as a separate footer. */
  offsetSeconds?: number | null;
  size?: "xs" | "sm";
  onClick?: () => void;
}

interface ToneSpec {
  text: string;
  bg: string;
  border: string;
  glow: string;
  badge: string;
  glyph: string;
  label: string;
}

const TONES: Record<CamSyncState, ToneSpec> = {
  synced: {
    text: "text-beep",
    bg: "bg-beep/10",
    border: "border-beep/40",
    glow: "",
    badge: "bg-beep text-bg",
    glyph: "✓",
    label: "synced",
  },
  low_confidence: {
    text: "text-live",
    bg: "bg-live/10",
    border: "border-live/40",
    glow: "shadow-[0_0_8px_var(--color-live-glow)]",
    badge: "bg-live text-bg",
    glyph: "!",
    label: "needs sync",
  },
  manual: {
    text: "text-manual",
    bg: "bg-manual/10",
    border: "border-manual/40",
    glow: "",
    badge: "bg-manual text-bg",
    glyph: "✎",
    label: "manual",
  },
  no_beep: {
    text: "text-led",
    bg: "bg-led-tint",
    border: "border-led/40",
    glow: "",
    badge: "bg-led text-bg",
    glyph: "×",
    label: "no beep",
  },
};

function formatSeconds(s: number, digits = 3): string {
  return `${s.toFixed(digits)}s`;
}

function formatOffset(s: number): string {
  const sign = s >= 0 ? "+" : "";
  return `${sign}${s.toFixed(3)}s`;
}

function formatBody(state: CamSyncState, props: CamSyncPillProps): string {
  if (state === "no_beep") return "no beep";
  if (props.offsetSeconds != null) {
    return state === "manual"
      ? `${formatOffset(props.offsetSeconds)} · manual`
      : formatOffset(props.offsetSeconds);
  }
  if (props.beepTime == null) return "no beep";
  if (state === "low_confidence" && props.beepConfidence != null) {
    return `${formatSeconds(props.beepTime)} · ${props.beepConfidence.toFixed(2)}`;
  }
  if (state === "manual") return `${formatSeconds(props.beepTime)} · manual`;
  return formatSeconds(props.beepTime);
}

export function CamSyncPill(props: CamSyncPillProps) {
  const { state, size = "sm", onClick } = props;
  const tone = TONES[state];
  const body = formatBody(state, props);
  const tiny = size === "xs";
  return (
    <button
      type="button"
      onClick={onClick}
      title={`${tone.label} -- click to ${
        state === "synced" ? "verify or re-pick" : "fix sync"
      }`}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border font-mono font-bold uppercase tabular-nums tracking-[0.04em]",
        tiny ? "px-1.5 py-0.5 text-[0.5625rem]" : "px-2 py-0.5 text-[0.625rem]",
        tone.border,
        tone.bg,
        tone.text,
        tone.glow,
        "transition-colors hover:brightness-110",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "inline-flex items-center justify-center rounded-full text-[0.5625rem] font-extrabold leading-none",
          tiny ? "size-3" : "size-3.5",
          tone.badge,
        )}
      >
        {tone.glyph}
      </span>
      <span>{body}</span>
    </button>
  );
}
