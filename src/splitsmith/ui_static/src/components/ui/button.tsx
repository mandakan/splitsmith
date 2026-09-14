import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/* Variants follow the visual budget (spec 2026-09-13 s5): `primary` is the
 * page's single primary action and the only Antonio button (the
 * .btn-led-fill recipe from index.css); everything else is Geist 13
 * medium on a neutral surface. `destructive` is an outline with led-text,
 * never a red fill. `outline` and `secondary` are kept as aliases of
 * `default` so existing call sites compile unchanged. */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-[13px] font-medium leading-[1.2] transition-colors duration-150 ease-[var(--ease-default)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "btn-primary rounded-lg",
        default: "border border-rule-strong bg-surface-2 text-ink hover:bg-surface-3",
        secondary: "border border-rule-strong bg-surface-2 text-ink hover:bg-surface-3",
        outline: "border border-rule-strong bg-surface-2 text-ink hover:bg-surface-3",
        ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
        destructive: "border border-led/45 bg-transparent text-led-text hover:bg-led-tint",
        link: "text-led-text underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-3.5",
        sm: "h-8 rounded-md px-3 text-[12px]",
        lg: "h-10 px-5",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
