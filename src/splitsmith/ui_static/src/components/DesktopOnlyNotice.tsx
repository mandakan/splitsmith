/**
 * DesktopGate - phones get a signpost instead of a broken desktop
 * layout. Pass-through above md; below md the wrapped page never
 * mounts. Rotating a tablet re-renders the real screen (matchMedia
 * listener), so no redirect and no URL change.
 */
import type { ReactNode } from "react";
import { MonitorSmartphone } from "lucide-react";
import { Link } from "react-router-dom";
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
      <div className="flex max-w-sm flex-col items-center gap-4 text-center">
        <MonitorSmartphone className="size-8 text-subtle" aria-hidden />
        <div className="font-display text-xl font-bold uppercase tracking-tight text-ink">
          This screen needs a desktop
        </div>
        <p className="text-sm text-muted">
          {screen} works with waveforms and dense controls that do not fit a
          phone. Results and the match overview work great here.
        </p>
        {links && (
          <div className="flex flex-wrap items-center justify-center gap-3">
            <Link
              to={href("results")}
              className="btn-led-fill inline-flex items-center justify-center rounded-md min-h-11 px-5"
            >
              Results
            </Link>
            <Link
              to={href("")}
              className="btn-led-outline inline-flex items-center justify-center rounded-md min-h-11 px-5"
            >
              Match overview
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
