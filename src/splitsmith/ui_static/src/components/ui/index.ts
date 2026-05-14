/**
 * Barrel for the foundational primitives. Surfaces import from
 * `@/components/ui` rather than reaching into individual files.
 *
 * The lowercase exports (badge, button, card, skeleton) are the legacy
 * shadcn primitives used by the current screens; the PascalCase exports
 * are the Shot Timer redesign primitives. Both coexist until each surface
 * migrates under issues #321-#331.
 */

export { Badge } from "./badge";
export { Button, buttonVariants } from "./button";
export {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "./card";
export { Skeleton } from "./skeleton";

export { Brand, BrandMark } from "./Brand";
export { Kicker } from "./Kicker";
export { DisplayHeading } from "./DisplayHeading";
export { ModeSwitch } from "./ModeSwitch";
export { Kbd } from "./Kbd";
export { IconButton, iconButtonVariants } from "./IconButton";
export { StatusPill, pillVariants } from "./StatusPill";
export { Tick, TickStrip, type TickState } from "./Tick";
export { Avatar, AvatarStack, type AvatarProps } from "./AvatarStack";
export { Readout, readoutValueVariants } from "./Readout";
export { ContextBar, Breadcrumb } from "./ContextBar";
export { ShotTimerShell } from "./ShotTimerShell";
