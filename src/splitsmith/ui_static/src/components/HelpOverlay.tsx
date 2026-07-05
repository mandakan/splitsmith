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
import { Portal } from "@/components/ui/Portal";
import { useDialogFocus } from "@/lib/dialogFocus";
import { modKeyGlyph } from "@/lib/platform";
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
  const mod = modKeyGlyph();
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
    { keys: ["K"], desc: "Toggle keep / reject (manual: delete); auto-advances if enabled" },
    { keys: ["Delete"], desc: "Delete focused manual marker" },
    { keys: ["Alt", "←", "→"], desc: "Nudge selected marker (10.7 ms)" },
    { keys: ["Alt", "Shift", "←", "→"], desc: "Nudge selected marker (1 ms)" },
    { keys: ["Double-click"], desc: "Add a manual marker at click time" },
  ];
  const view: ShortcutRow[] = [
    { keys: ["L"], desc: "Toggle the marker list drawer" },
    { keys: [mod, "1"], desc: "Zoom in" },
    { keys: [mod, "2"], desc: "Fit waveform to view" },
    { keys: [mod, "3"], desc: "Zoom out" },
  ];
  const edit: ShortcutRow[] = [
    { keys: [mod, "Z"], desc: "Undo last marker change" },
    {
      keys: [mod, "S"],
      desc:
        mode === "audit"
          ? "Save audit JSON and jump to the next stage"
          : "Save fixture JSON",
    },
  ];
  const sectionsList: Section[] = [
    { title: "Playback", rows: playback },
    { title: "Markers", rows: markers },
    { title: "View", rows: view },
    { title: "Edit", rows: edit },
  ];
  if (mode === "audit") {
    sectionsList.splice(3, 0, {
      title: "Stages",
      rows: [
        { keys: ["["], desc: "Previous stage (auto-saves first)" },
        { keys: ["]"], desc: "Next stage (auto-saves first)" },
      ],
    });
  }
  return sectionsList;
}

export function HelpOverlay({ open, onClose, mode }: HelpOverlayProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);

  // Escape + focus entry / Tab trap / restore come from the shared hook
  // (aria-modal without a real trap let Tab wander behind the overlay).
  // ``?`` as a toggle is this dialog's own affordance.
  useDialogFocus(open, panelRef, onClose);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "?") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const grouped = sections(mode);

  return (
    <Portal>
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
      className="fixed inset-0 z-modal flex items-center justify-center"
    >
      <div
        aria-hidden
        className="absolute inset-0 bg-ink/40 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        className={cn(
          "relative max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-rule bg-surface text-ink shadow-xl",
          "p-6",
        )}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Keyboard shortcuts</h2>
            <p className="text-xs text-muted">
              Press <Kbd>?</Kbd> any time to toggle this help.
            </p>
          </div>
          <Button
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
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
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
                    <dd className="text-right text-sm text-muted">
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
    </Portal>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex min-w-[1.5rem] items-center justify-center rounded border border-rule bg-muted px-1.5 py-0.5 font-mono text-[0.7rem] font-medium leading-none">
      {children}
    </kbd>
  );
}
