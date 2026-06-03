/**
 * Reusable accessible confirmation dialog.
 *
 * One styled, focus-trapping modal for every destructive confirm in the
 * app, replacing the mix of native ``window.confirm()`` calls and the
 * unconfirmed picker "forget". Drive it through the {@link useConfirm}
 * hook rather than rendering it directly.
 *
 * Accessibility (WCAG 2.2 AA): ``role="dialog"`` + ``aria-modal`` with
 * labelled/described regions; focus moves into the dialog on open, is
 * trapped on Tab/Shift-Tab, and is restored to the trigger on close; ESC
 * cancels. Colour is never the sole signal -- the destructive variant
 * also carries an icon and explicit button copy.
 */

import { useEffect, useRef, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export interface ConfirmCheckbox {
  key: string;
  label: string;
  help?: string;
}

interface ConfirmDialogProps {
  title: ReactNode;
  body?: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  destructive: boolean;
  busy?: boolean;
  checkboxes: ConfirmCheckbox[];
  checked: Record<string, boolean>;
  onToggle: (key: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  cancelLabel,
  destructive,
  busy = false,
  checkboxes,
  checked,
  onToggle,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cardRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const node = cardRef.current;
    const first = node?.querySelector<HTMLElement>(FOCUSABLE);
    // Focus the first control (Cancel, the least destructive) so a stray
    // Enter doesn't fire the delete.
    (first ?? node)?.focus();
    return () => {
      previouslyFocused.current?.focus?.();
    };
  }, []);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
      return;
    }
    if (e.key !== "Tab") return;
    const node = cardRef.current;
    if (!node) return;
    const focusables = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement as HTMLElement | null;
    if (e.shiftKey) {
      if (active === first || !node.contains(active)) {
        e.preventDefault();
        last.focus();
      }
    } else if (active === last || !node.contains(active)) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      aria-describedby={body ? "confirm-dialog-body" : undefined}
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/70 p-4"
      onClick={onCancel}
      onKeyDown={onKeyDown}
    >
      <Card
        ref={cardRef}
        tabIndex={-1}
        className="w-full max-w-md shadow-xl outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        <CardHeader>
          <CardTitle id="confirm-dialog-title" className="flex items-center gap-2">
            {destructive ? (
              <AlertTriangle className="size-5 text-destructive" aria-hidden="true" />
            ) : null}
            {title}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          {body ? (
            <div id="confirm-dialog-body" className="text-muted-foreground">
              {body}
            </div>
          ) : null}

          {checkboxes.length > 0 ? (
            <div className="space-y-2">
              {checkboxes.map((cb) => (
                <label
                  key={cb.key}
                  className="flex cursor-pointer items-start gap-2 rounded-md border border-border p-2 hover:bg-muted/40"
                >
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={Boolean(checked[cb.key])}
                    disabled={busy}
                    onChange={() => onToggle(cb.key)}
                  />
                  <div className="flex-1 space-y-0.5">
                    <span className="font-medium">{cb.label}</span>
                    {cb.help ? (
                      <p className="text-xs text-muted-foreground">{cb.help}</p>
                    ) : null}
                  </div>
                </label>
              ))}
            </div>
          ) : null}

          <div className="flex justify-end gap-2 border-t border-border pt-3">
            <Button variant="ghost" onClick={onCancel} disabled={busy}>
              {cancelLabel}
            </Button>
            <Button
              variant={destructive ? "destructive" : "default"}
              onClick={onConfirm}
              disabled={busy}
            >
              {confirmLabel}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
