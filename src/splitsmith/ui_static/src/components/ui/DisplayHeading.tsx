/**
 * Antonio uppercase heading. Variants govern size; the element defaults to
 * `<h2>` but can be overridden via `as`.
 */

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const headingVariants = cva(
  "font-display font-bold uppercase tracking-tight text-ink",
  {
    variants: {
      variant: {
        hero: "text-5xl leading-[0.95]",
        page: "text-3xl leading-tight",
        card: "text-2xl leading-tight",
        section: "text-xl leading-tight",
      },
    },
    defaultVariants: {
      variant: "page",
    },
  },
);

type AsProp = "h1" | "h2" | "h3" | "h4" | "h5" | "h6" | "div" | "span";

interface DisplayHeadingProps
  extends React.HTMLAttributes<HTMLHeadingElement>,
    VariantProps<typeof headingVariants> {
  as?: AsProp;
}

export function DisplayHeading({
  as: As = "h2",
  variant,
  className,
  ...props
}: DisplayHeadingProps) {
  return (
    <As className={cn(headingVariants({ variant }), className)} {...props} />
  );
}
