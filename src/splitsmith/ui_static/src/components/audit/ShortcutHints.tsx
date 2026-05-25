import { Keyboard, X } from "lucide-react";

import { Kbd } from "@/components/ui/Kbd";
import { modKeyGlyph } from "@/lib/platform";
import { cn } from "@/lib/utils";

interface ShortcutItem {
  keys: string[];
  label: string;
  strong?: boolean;
}

function defaultSet(): ShortcutItem[] {
  const mod = modKeyGlyph();
  return [
    { keys: ["Space"], label: "Play / pause" },
    { keys: ["M", "⇧M"], label: "Next / prev shot" },
    { keys: ["K"], label: "Toggle shot at playhead" },
    { keys: [`${mod}1`, `${mod}2`, `${mod}3`], label: "Zoom in / fit / out" },
    { keys: [`${mod}↵`], label: "Save & next stage", strong: true },
  ];
}

export interface ShortcutHintsProps {
  onDismiss: () => void;
  onOpenAll: () => void;
}

/**
 * Visible-by-default keyboard-shortcut strip that sits below the audit
 * toolbar. Surfaces the five keys that drive the audit loop so a new
 * shooter can pick them up at a glance, instead of having to open the
 * full sheet behind ``?``.
 *
 * Dismiss state lives at the parent (localStorage-backed); see
 * ``Audit.tsx``. When dismissed, render ``ShortcutHintsRestore`` instead.
 */
export function ShortcutHints({ onDismiss, onOpenAll }: ShortcutHintsProps) {
  const set = defaultSet();
  return (
    <div
      role="region"
      aria-label="Keyboard shortcuts"
      className={cn(
        "mt-2 flex items-center gap-4 rounded-md border border-rule px-4 py-2",
        "bg-[linear-gradient(to_right,color-mix(in_srgb,var(--surface-2)_80%,transparent)_0%,color-mix(in_srgb,var(--surface-2)_55%,transparent)_50%,color-mix(in_srgb,var(--surface-2)_80%,transparent)_100%)]",
        "shadow-[0_1px_0_rgba(255,255,255,0.02)_inset]",
      )}
    >
      <span className="inline-flex shrink-0 items-center gap-1.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.16em] text-subtle">
        <span
          aria-hidden
          className="inline-block size-1.5 rounded-full bg-led shadow-[0_0_6px_var(--led-glow)]"
        />
        Shortcuts
      </span>

      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-4 gap-y-1">
        {set.map((it, i) => (
          <span key={i} className="inline-flex items-center gap-2">
            {i > 0 ? (
              <span
                aria-hidden
                className="mr-2 inline-block h-3 w-px bg-rule-strong/70"
              />
            ) : null}
            <span className="inline-flex items-center gap-0.5">
              {it.keys.map((k, j) => (
                <span key={j} className="inline-flex items-center gap-0.5">
                  {j > 0 ? (
                    <span className="font-mono text-[0.625rem] text-subtle">
                      /
                    </span>
                  ) : null}
                  <Kbd
                    size="sm"
                    className={cn(
                      k === "Space" &&
                        "px-1.5 font-display font-bold tracking-[0.08em]",
                      it.strong &&
                        "border-led/50 bg-[color-mix(in_srgb,var(--led)_14%,var(--surface-3))] text-ink shadow-[0_0_0_1px_color-mix(in_srgb,var(--led)_30%,transparent),0_0_10px_color-mix(in_srgb,var(--led-glow)_40%,transparent)]",
                    )}
                  >
                    {k}
                  </Kbd>
                </span>
              ))}
            </span>
            <span
              className={cn(
                "whitespace-nowrap font-mono text-[0.625rem] font-medium uppercase tracking-wide",
                it.strong ? "text-ink-2" : "text-muted",
              )}
            >
              {it.label}
            </span>
          </span>
        ))}
      </div>

      <div className="inline-flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          onClick={onOpenAll}
          title="Open full shortcut sheet (?)"
          className="inline-flex items-center gap-1.5 rounded-sm border border-rule bg-transparent px-2 py-0.5 font-display text-[0.5625rem] font-bold uppercase tracking-[0.08em] text-ink-2 transition-colors hover:bg-surface-3"
        >
          More
          <Kbd size="sm">?</Kbd>
        </button>
        <button
          type="button"
          onClick={onDismiss}
          title="Hide shortcut hints"
          aria-label="Hide shortcut hints"
          className="inline-flex size-5 items-center justify-center rounded-sm border border-rule bg-transparent text-subtle transition-colors hover:bg-surface-3 hover:text-ink-2"
        >
          <X className="size-2.5" />
        </button>
      </div>
    </div>
  );
}

export interface ShortcutHintsRestoreProps {
  onShow: () => void;
}

/**
 * Compact pill that replaces the hints strip once the user has dismissed
 * it. Click reveals the strip again. Lives in the same slot below the
 * toolbar so the user's eye-line stays the same.
 */
export function ShortcutHintsRestore({ onShow }: ShortcutHintsRestoreProps) {
  return (
    <div className="mt-2 flex">
      <button
        type="button"
        onClick={onShow}
        title="Show shortcut hints"
        className="inline-flex items-center gap-1.5 rounded-sm border border-rule bg-surface-2 px-2.5 py-1 font-display text-[0.625rem] font-bold uppercase tracking-[0.08em] text-ink-2 transition-colors hover:bg-surface-3"
      >
        <Keyboard className="size-3" />
        Shortcuts
      </button>
    </div>
  );
}
