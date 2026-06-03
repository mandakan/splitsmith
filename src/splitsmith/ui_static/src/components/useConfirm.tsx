/**
 * Promise-based confirmation hook backed by one {@link ConfirmDialog}.
 *
 * Mount {@link ConfirmProvider} once near the app root, then call
 * ``const confirm = useConfirm()`` anywhere below it. ``confirm(opts)``
 * opens the dialog and resolves when the user acts:
 *
 *   if (!(await confirm({ title: "Remove shooter?" })).confirmed) return;
 *
 * That shape makes the native ``window.confirm()`` callsites a near
 * drop-in. ``opts.checkboxes`` adds opt-in toggles (e.g. the project
 * delete's "also delete files on disk"); their state comes back on
 * ``result.checked`` keyed by checkbox ``key``.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ConfirmDialog, type ConfirmCheckbox } from "./ConfirmDialog";

export interface ConfirmOptions {
  title: ReactNode;
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Defaults to ``true`` -- nearly every caller is a destructive action. */
  destructive?: boolean;
  checkboxes?: ConfirmCheckbox[];
}

export interface ConfirmResult {
  confirmed: boolean;
  checked: Record<string, boolean>;
}

type ConfirmFn = (opts: ConfirmOptions) => Promise<ConfirmResult>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

interface PendingState {
  opts: ConfirmOptions;
  resolve: (result: ConfirmResult) => void;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingState | null>(null);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  // Hold the active resolver so a backdrop/ESC cancel can still settle the
  // promise even though it isn't passed through the dialog's own handlers.
  const resolverRef = useRef<((result: ConfirmResult) => void) | null>(null);

  const confirm = useCallback<ConfirmFn>((opts) => {
    return new Promise<ConfirmResult>((resolve) => {
      resolverRef.current = resolve;
      // Checkboxes start unchecked -- opt-in by design.
      setChecked({});
      setPending({ opts, resolve });
    });
  }, []);

  const settle = useCallback(
    (confirmed: boolean) => {
      const resolve = resolverRef.current;
      resolverRef.current = null;
      setPending(null);
      resolve?.({ confirmed, checked });
    },
    [checked],
  );

  const toggle = useCallback((key: string) => {
    setChecked((prev) => ({ ...prev, [key]: !prev[key] }));
  }, []);

  const value = useMemo(() => confirm, [confirm]);

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      {pending ? (
        <ConfirmDialog
          title={pending.opts.title}
          body={pending.opts.body}
          confirmLabel={pending.opts.confirmLabel ?? "Delete"}
          cancelLabel={pending.opts.cancelLabel ?? "Cancel"}
          destructive={pending.opts.destructive ?? true}
          checkboxes={pending.opts.checkboxes ?? []}
          checked={checked}
          onToggle={toggle}
          onConfirm={() => settle(true)}
          onCancel={() => settle(false)}
        />
      ) : null}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (ctx === null) {
    throw new Error("useConfirm must be used within a <ConfirmProvider>");
  }
  return ctx;
}
