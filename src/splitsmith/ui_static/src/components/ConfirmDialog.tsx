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

import { useRef, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Portal } from "@/components/ui/Portal";
import { useDialogFocus } from "@/lib/dialogFocus";

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
  useDialogFocus(true, cardRef, onCancel);

  return (
    <Portal>
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      aria-describedby={body ? "confirm-dialog-body" : undefined}
      className="fixed inset-0 z-modal flex items-center justify-center bg-bg/70 p-4"
      onClick={onCancel}
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
            <div id="confirm-dialog-body" className="text-muted">
              {body}
            </div>
          ) : null}

          {checkboxes.length > 0 ? (
            <div className="space-y-2">
              {checkboxes.map((cb) => (
                <label
                  key={cb.key}
                  className="flex cursor-pointer items-start gap-2 rounded-md border border-rule p-2 hover:bg-muted/40"
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
                      <p className="text-xs text-muted">{cb.help}</p>
                    ) : null}
                  </div>
                </label>
              ))}
            </div>
          ) : null}

          <div className="flex justify-end gap-2 border-t border-rule pt-3">
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
    </Portal>
  );
}
