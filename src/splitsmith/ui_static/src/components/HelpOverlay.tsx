/**
 * Keyboard-shortcut help overlay (#15 step 7).
 *
 * Triggered by ``?`` on the audit + review screens. Lists every hotkey
 * we ship grouped by purpose so the user doesn't have to dig through
 * tooltips. Closes on Esc, ``?``, or backdrop click.
 *
 * Shared between Audit + Review with a ``mode`` prop that swaps the
 * one section that differs (the audit screen has Cmd+S; the review
 * screen does too but no project-mode-only items).
 */

import { useEffect, useRef } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type HelpMode = "audit" | "review";

interface HelpOverlayProps {
  open: boolean;
  onClose: () => void;
  mode: HelpMode;
}

interface ShortcutRow {
  keys: string[];
  desc: string;
}

interface Section {
  title: string;
  rows: ShortcutRow[];
}

function sections(mode: HelpMode): Section[] {
  const playback: ShortcutRow[] = [
    { keys: ["Space"], desc: "Play / pause" },
    { keys: ["←", "→"], desc: "Nudge playhead 250 ms" },
    { keys: ["Shift", "←", "→"], desc: "Nudge playhead 25 ms" },
    { keys: ["R"], desc: "Toggle loop (snaps back to anchor)" },
  ];
  const markers: ShortcutRow[] = [
    { keys: ["M"], desc: "Next kept shot" },
    { keys: ["Shift", "M"], desc: "Previous kept shot" },
    { keys: ["N"], desc: "Next marker (any kind)" },
    { keys: ["Shift", "N"], desc: "Previous marker (any kind)" },
    { keys: ["K"], desc: "Toggle current shot keep / reject (manual: delete)" },
    { keys: ["Delete"], desc: "Delete focused manual marker" },
    { keys: ["Alt", "←", "→"], desc: "Nudge selected marker (10.7 ms)" },
    { keys: ["Alt", "Shift", "←", "→"], desc: "Nudge selected marker (1 ms)" },
    { keys: ["Double-click"], desc: "Add a manual marker at click time" },
  ];
  const view: ShortcutRow[] = [
    { keys: ["L"], desc: "Toggle the marker list drawer" },
    { keys: ["⌘", "1"], desc: "Zoom in" },
    { keys: ["⌘", "2"], desc: "Fit waveform to view" },
    { keys: ["⌘", "3"], desc: "Zoom out" },
  ];
  const edit: ShortcutRow[] = [
    { keys: ["⌘", "Z"], desc: "Undo last marker change" },
    { keys: ["⌘", "S"], desc: mode === "audit" ? "Save audit JSON" : "Save fixture JSON" },
  ];
  return [
    { title: "Playback", rows: playback },
    { title: "Markers", rows: markers },
    { title: "View", rows: view },
    { title: "Edit", rows: edit },
  ];
}

export function HelpOverlay({ open, onClose, mode }: HelpOverlayProps) {
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" || e.key === "?") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Move focus into the dialog so screen readers + Tab nav land here
  // immediately after open (#21 a11y).
  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  if (!open) return null;

  const grouped = sections(mode);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
      className="fixed inset-0 z-50 flex items-center justify-center"
    >
      <div
        aria-hidden
        className="absolute inset-0 bg-foreground/40 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        className={cn(
          "relative max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-border bg-card text-card-foreground shadow-xl",
          "p-6",
        )}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Keyboard shortcuts</h2>
            <p className="text-xs text-muted-foreground">
              Press <Kbd>?</Kbd> any time to toggle this help.
            </p>
          </div>
          <Button
            ref={closeRef}
            size="sm"
            variant="ghost"
            onClick={onClose}
            aria-label="Close help (Esc)"
          >
            <X className="size-4" />
          </Button>
        </div>
        <div className="grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2">
          {grouped.map((section) => (
            <section key={section.title}>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {section.title}
              </h3>
              <dl className="space-y-1.5">
                {section.rows.map((row, i) => (
                  <div key={i} className="flex items-baseline justify-between gap-3">
                    <dt className="flex flex-wrap items-center gap-1">
                      {row.keys.map((k, j) => (
                        <Kbd key={j}>{k}</Kbd>
                      ))}
                    </dt>
                    <dd className="text-right text-sm text-muted-foreground">
                      {row.desc}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex min-w-[1.5rem] items-center justify-center rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[0.7rem] font-medium leading-none">
      {children}
    </kbd>
  );
}
