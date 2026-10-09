/**
 * Avatar -- a small colored circle with initials, identifying a shooter.
 *
 * The `you` variant always uses the LED-red identity. Other slots (1/2/3/4)
 * rotate through the amber/green/blue/manual palette per the polished
 * surfaces; pass `tone="auto"` and a `seed` for deterministic assignment.
 *
 * AvatarStack horizontally arranges multiple Avatars with overlap; an
 * `overflow` prop renders a "+N" chip at the end.
 */

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const PALETTE = ["jl", "pe", "rj", "manual"] as const;
type PaletteKey = (typeof PALETTE)[number];

function pickPalette(seed: string): PaletteKey {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

const TONE_CLASS: Record<"you" | PaletteKey, string> = {
  you: "bg-[radial-gradient(circle_at_30%_30%,var(--color-led-soft),var(--color-led-deep))] text-ink shadow-[0_0_0_1px_var(--color-led-deep),0_0_12px_var(--color-led-glow)]",
  jl: "bg-[radial-gradient(circle_at_30%_30%,var(--color-shooter-jl-soft),var(--color-shooter-jl-deep))] text-ink",
  pe: "bg-[radial-gradient(circle_at_30%_30%,var(--color-shooter-pe-soft),var(--color-shooter-pe-deep))] text-ink",
  rj: "bg-[radial-gradient(circle_at_30%_30%,var(--color-shooter-rj-soft),var(--color-shooter-rj-deep))] text-ink",
  manual:
    "bg-[radial-gradient(circle_at_30%_30%,var(--color-manual),#5B21B6)] text-ink",
};

const avatarVariants = cva(
  "inline-flex items-center justify-center rounded-full font-display font-bold uppercase tracking-tight ring-1 ring-bg/40 select-none",
  {
    variants: {
      size: {
        xs: "size-5 text-[0.5rem]",
        sm: "size-6 text-[0.625rem]",
        md: "size-8 text-xs",
        lg: "size-10 text-sm",
      },
    },
    defaultVariants: { size: "md" },
  },
);

export interface AvatarProps
  extends Omit<React.HTMLAttributes<HTMLSpanElement>, "children">,
    VariantProps<typeof avatarVariants> {
  initials: string;
  /** Explicit tone overrides the seed-derived one. */
  tone?: "you" | PaletteKey;
  /** Used to pick a deterministic palette when tone is not set. */
  seed?: string;
  /** Optional shooter name for the title tooltip. */
  name?: string;
  /** The shooter's own colour (#1249): a ring around the avatar. */
  accent?: string | null;
  /** The shooter's logo URL (#1249): drawn in place of the initials. */
  logo?: string | null;
}

export function Avatar({
  initials,
  tone,
  seed,
  size,
  name,
  className,
  accent,
  logo,
  style,
  ...props
}: AvatarProps) {
  const resolved = tone ?? pickPalette(seed ?? initials);
  // Only a shooter who set an accent gets a ring; everyone else renders
  // exactly as before (no style attribute at all).
  const ringed = accent ? { ...style, boxShadow: `0 0 0 1.5px ${accent}` } : style;
  return (
    <span
      title={name ?? initials}
      aria-label={name ?? initials}
      className={cn(avatarVariants({ size }), TONE_CLASS[resolved], logo && "overflow-hidden bg-surface-2", className)}
      style={ringed}
      {...props}
    >
      {logo ? (
        <img src={logo} alt="" className="h-full w-full object-contain" />
      ) : (
        initials.slice(0, 2).toUpperCase()
      )}
    </span>
  );
}

interface AvatarStackProps {
  avatars: AvatarProps[];
  size?: AvatarProps["size"];
  overflow?: number;
  className?: string;
}

export function AvatarStack({
  avatars,
  size = "sm",
  overflow,
  className,
}: AvatarStackProps) {
  return (
    <div className={cn("inline-flex items-center -space-x-1.5", className)}>
      {avatars.map((av, i) => (
        <Avatar key={i} size={size} {...av} />
      ))}
      {typeof overflow === "number" && overflow > 0 && (
        <span
          aria-label={`${overflow} more`}
          className={cn(
            avatarVariants({ size }),
            "bg-surface-3 text-muted ring-bg/40",
          )}
        >
          +{overflow}
        </span>
      )}
    </div>
  );
}
