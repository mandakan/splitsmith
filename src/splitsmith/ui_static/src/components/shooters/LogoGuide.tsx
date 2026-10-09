/**
 * LogoGuide -- where each logo goes on a title card (the logos guide): a
 * schematic card with its three spots, the brand top-left, the shooter
 * top-right and the event in the centre above the match name. A logo
 * that is set is drawn in its spot; an empty spot is a dashed, labelled
 * square. ``highlight`` dims the other two (the shooter sheet shows only
 * the spot it sets). Pure drawing: nothing is rendered by the server.
 */
import { cn } from "@/lib/utils";

export type LogoSpot = "brand" | "shooter" | "event";

const LABEL: Record<LogoSpot, string> = {
  brand: "Your brand",
  shooter: "Shooter logo",
  event: "Event logo",
};

function Spot({
  spot,
  url,
  dim,
  className,
}: {
  spot: LogoSpot;
  url: string | null;
  dim: boolean;
  className: string;
}) {
  return (
    <div
      data-spot={spot}
      className={cn(
        "absolute flex items-center justify-center rounded-md transition-opacity",
        url ? "" : "border-2 border-dashed border-ink-2/70 bg-ink/5",
        dim && "opacity-30",
        className,
      )}
    >
      {url ? (
        <img src={url} alt={LABEL[spot]} className="h-full w-full object-contain" />
      ) : (
        <span className="px-1 text-center text-xs font-semibold leading-tight text-ink-2">
          {LABEL[spot]}
        </span>
      )}
    </div>
  );
}

export function LogoGuide({
  brand = null,
  shooter = null,
  event = null,
  highlight = null,
  className,
}: {
  brand?: string | null;
  shooter?: string | null;
  event?: string | null;
  highlight?: LogoSpot | null;
  className?: string;
}) {
  const dim = (spot: LogoSpot) => highlight !== null && highlight !== spot;
  return (
    <div
      role="img"
      aria-label="Where the logos go on a title card: your brand top left, the shooter's logo top right, the event logo in the centre above the match name"
      className={cn("relative aspect-video w-full overflow-hidden rounded-md border border-rule bg-surface-3", className)}
    >
      <Spot spot="brand" url={brand} dim={dim("brand")} className="left-[4%] top-[6%] size-[18%]" />
      <Spot spot="shooter" url={shooter} dim={dim("shooter")} className="right-[4%] top-[6%] size-[18%]" />
      <Spot spot="event" url={event} dim={dim("event")} className="left-1/2 top-[22%] size-[24%] -translate-x-1/2" />
      <div className={cn("absolute inset-x-0 top-[56%] flex flex-col items-center gap-1.5", highlight && "opacity-30")}>
        <span className="text-md font-semibold text-ink">Match name</span>
        <span className="h-1.5 w-1/4 rounded-full bg-ink-2/40" />
        <span className="h-1.5 w-1/5 rounded-full bg-ink-2/40" />
      </div>
    </div>
  );
}
