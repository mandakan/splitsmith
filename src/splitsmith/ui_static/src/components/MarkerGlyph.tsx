/**
 * Audit-marker glyph -- color + shape, never color alone.
 *
 * Three states are distinguishable for color-blind users by shape, not just
 * by color, satisfying WCAG 1.4.1 (Use of Color):
 *
 *   detected     filled triangle ▼ (default state, "pinned" feel)
 *   rejected     outline triangle with strikethrough (visually "crossed out")
 *   manual       filled diamond ◆ with dashed border (different shape entirely)
 *
 * Used in:
 *   - /_design page (visual reference)
 *   - audit screen waveform overlay (#15)
 *   - badges and tables anywhere markers are referenced
 */

import { cn } from "@/lib/utils";

export type MarkerKind = "detected" | "rejected" | "manual";

interface MarkerGlyphProps {
  kind: MarkerKind;
  size?: number;
  className?: string;
  label?: string;
}

export function MarkerGlyph({ kind, size = 16, className, label }: MarkerGlyphProps) {
  const colorVar = `var(--marker-${kind})`;
  const aria = label ?? kind;
  const half = size / 2;

  // Stroke + dash pattern scale with size so the same component reads
  // crisp at the 14 px audit overlay and at the 24 px design-page swatch.
  // Constants tuned at size=24; smaller sizes shrink proportionally.
  const stroke = Math.max(0.75, size * 0.08);
  const dashOn = Math.max(1.5, size * 0.18);
  const dashOff = Math.max(0.75, size * 0.10);

  if (kind === "manual") {
    // Diamond rotated 45° with dashed border.
    const inset = size * 0.12;
    return (
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={aria}
        className={cn("inline-block shrink-0", className)}
      >
        <rect
          x={inset}
          y={inset}
          width={size - inset * 2}
          height={size - inset * 2}
          fill={colorVar}
          stroke={colorVar}
          strokeWidth={stroke}
          strokeDasharray={`${dashOn},${dashOff}`}
          strokeLinecap="round"
          transform={`rotate(45 ${half} ${half})`}
        />
      </svg>
    );
  }

  // Triangle (filled = detected, outline = rejected).
  const points = `${half},${size * 0.15} ${size * 0.92},${size * 0.85} ${size * 0.08},${size * 0.85}`;
  const transform = `rotate(180 ${half} ${half})`;
  const isOutline = kind === "rejected";
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={aria}
      className={cn("inline-block shrink-0", className)}
    >
      <polygon
        points={points}
        transform={transform}
        fill={isOutline ? "none" : colorVar}
        stroke={colorVar}
        strokeWidth={isOutline ? stroke : Math.max(0.4, size * 0.03)}
        strokeLinejoin="round"
      />
      {isOutline ? (
        <line
          x1={size * 0.15}
          y1={size * 0.5}
          x2={size * 0.85}
          y2={size * 0.5}
          stroke={colorVar}
          strokeWidth={stroke}
          strokeLinecap="round"
        />
      ) : null}
    </svg>
  );
}
