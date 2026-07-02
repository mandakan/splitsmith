import type { VideoRole } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Primary / Secondary / Ignore segmented control for a video's role. */
export function RoleToggles({
  value,
  onChange,
  disabled,
}: {
  value: VideoRole;
  onChange: (r: VideoRole) => void;
  disabled?: boolean;
}) {
  const opts: { v: VideoRole; label: string }[] = [
    { v: "primary", label: "Primary" },
    { v: "secondary", label: "Secondary" },
    { v: "ignored", label: "Ignore" },
  ];
  return (
    <div className="inline-flex gap-0.5 rounded-md border border-rule bg-surface-2 p-0.5">
      {opts.map((o) => {
        const on = value === o.v;
        return (
          <button
            key={o.v}
            type="button"
            onClick={() => onChange(o.v)}
            disabled={disabled}
            className={cn(
              "rounded px-2.5 py-1 font-display text-[0.625rem] font-semibold uppercase tracking-[0.06em] transition-all",
              on && o.v === "primary" && "border border-led-deep bg-led/10 text-led",
              on && o.v === "secondary" && "bg-surface-4 text-ink",
              on && o.v === "ignored" && "bg-surface-4 text-muted line-through",
              !on && "text-muted hover:text-ink",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
