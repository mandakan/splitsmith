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
    <div role="group" aria-label="Role" className="inline-flex gap-0.5 rounded-md border border-rule-strong bg-surface-2 p-0.5">
      {opts.map((o) => {
        const on = value === o.v;
        return (
          <button
            key={o.v}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(o.v)}
            disabled={disabled}
            className={cn(
              "rounded px-2.5 py-1 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led disabled:opacity-50",
              on ? "bg-surface-3 text-ink" : "text-muted hover:text-ink",
              on && o.v === "ignored" && "line-through",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
