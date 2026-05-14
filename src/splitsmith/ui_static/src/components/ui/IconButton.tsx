/**
 * Square icon-only button. Variants:
 *  - default: surface-2 fill, ink icon on hover, mode-accent border on focus
 *  - subtle: transparent fill, muted icon, hover surface-3
 *  - led: LED outline always visible (used for primary affordances)
 *
 * Minimum target 36px; size="lg" is 44px (per a11y guide).
 */

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const iconButtonVariants = cva(
  "inline-flex items-center justify-center rounded-md border transition-colors disabled:opacity-50 disabled:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "border-rule bg-surface-2 text-ink-2 hover:border-rule-strong hover:bg-surface-3 hover:text-ink",
        subtle:
          "border-transparent bg-transparent text-muted hover:text-ink hover:bg-surface-3",
        led: "border-led/60 bg-surface-2 text-led hover:bg-[color:var(--color-led-tint)] hover:border-led",
      },
      size: {
        sm: "size-8 [&_svg]:size-3.5",
        md: "size-9 [&_svg]:size-4",
        lg: "size-11 [&_svg]:size-5",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "md",
    },
  },
);

export interface IconButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof iconButtonVariants> {
  label: string;
}

export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ variant, size, label, className, ...props }, ref) => (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      title={label}
      className={cn(iconButtonVariants({ variant, size }), className)}
      {...props}
    />
  ),
);
IconButton.displayName = "IconButton";

export { iconButtonVariants };
