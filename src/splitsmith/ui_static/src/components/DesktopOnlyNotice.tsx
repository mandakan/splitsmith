/**
 * DesktopGate - phones get a signpost instead of a broken desktop
 * layout. Pass-through above md; below md the wrapped page never
 * mounts. Rotating a tablet re-renders the real screen (matchMedia
 * listener), so no redirect and no URL change.
 */
import type { ReactNode } from "react";
import { MonitorSmartphone } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { useMatchHref } from "@/lib/matchHref";
import { useIsMobile } from "@/lib/useIsMobile";

export function DesktopGate({
  screen,
  children,
  links = true,
}: {
  screen: string;
  children: ReactNode;
  links?: boolean;
}) {
  const isMobile = useIsMobile();
  if (!isMobile) return <>{children}</>;
  return <DesktopOnlyNotice screen={screen} links={links} />;
}

export function DesktopOnlyNotice({
  screen,
  links = true,
}: {
  screen: string;
  links?: boolean;
}) {
  const href = useMatchHref();
  return (
    <div className="grid min-h-[60dvh] place-items-center px-6 py-10">
      <div className="flex max-w-sm flex-col items-center gap-3 text-center">
        <MonitorSmartphone className="size-8 text-subtle" aria-hidden />
        <h1 className="text-lg font-semibold text-ink">This screen needs a desktop</h1>
        <p className="text-md text-muted">
          {screen} is a desktop workflow; its layout and controls do not fit a phone.
          {links ? " Splits and the Overview work here." : ""}
        </p>
        {links ? (
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Button variant="primary" asChild>
              <Link to={href("results")}>Splits</Link>
            </Button>
            <Button asChild>
              <Link to={href("")}>Overview</Link>
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
