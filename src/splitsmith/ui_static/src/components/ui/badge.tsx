import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        destructive:
          "border-transparent bg-destructive text-destructive-foreground",
        outline: "text-foreground",
        good: "border-transparent bg-split-good text-split-good-foreground",
        ok: "border-transparent bg-split-ok text-split-ok-foreground",
        slow: "border-transparent bg-split-slow text-split-slow-foreground",
        transition:
          "border-transparent bg-split-transition text-split-transition-foreground",
        statusNotStarted:
          "border-transparent bg-status-not-started/20 text-status-not-started",
        statusInProgress:
          "border-transparent bg-status-in-progress/20 text-status-in-progress",
        statusComplete:
          "border-transparent bg-status-complete/20 text-status-complete",
        statusWarning:
          "border-transparent bg-status-warning/20 text-status-warning",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
