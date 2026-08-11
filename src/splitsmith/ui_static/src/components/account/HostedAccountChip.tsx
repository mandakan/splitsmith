/**
 * HostedAccountChip - the hosted account this LOCAL install is linked to
 * (#719).
 *
 * Deliberately separate from AccountChip, which it resembles and does not
 * mean the same thing: AccountChip shows the session you are logged in
 * *as* (hosted only); this shows the hosted account this desktop install
 * is *linked to* (local only). Collapsing them would conflate a session
 * with a stored credential. They self-gate on opposite deployment modes,
 * so the two never render together.
 *
 * No "last sync" time here on purpose. Sync state is per-match and lives
 * on SyncCard; the only account-level equivalent is the hosted token
 * row's last_used_at, which a sync-scoped token cannot read back.
 *
 * Phone width (#733): this chip ran off the right edge of GlobalBar on
 * /pick, taking the email and the sign-out control with it. What it drops
 * to fit is the device name, and only that -- the email is the chip's
 * whole point (it says *which* hosted account this install is linked to)
 * and sign-out is its only affordance. The device name is the safe cut
 * because a phone is reading about one install, the one it is looking at.
 * The long sign-in label goes too: 155px of a 390px bar to say what "Sign
 * in" says next to a chip that is visibly about splitsmith.app.
 *
 * The truncation classes are belt-and-braces under that. Dropping content
 * buys a fixed number of pixels; `min-w-0` + `truncate` means a longer
 * email than the one measured shortens itself instead of overflowing
 * again, which is the failure mode this chip already had once.
 */

import { useCallback, useEffect, useState } from "react";
import { LogOut } from "lucide-react";

import { IconButton } from "@/components/ui/IconButton";
import { Button } from "@/components/ui/button";
import { DeviceLoginDialog } from "@/components/account/DeviceLoginDialog";
import { useDeploymentMode } from "@/lib/features";
import { useIsMobile } from "@/lib/useIsMobile";
import {
  api,
  HOSTED_ACCOUNT_CHANGED_EVENT,
  notifyHostedAccountChanged,
  type HostedAccountInfo,
} from "@/lib/api";

export function HostedAccountChip({ className }: { className?: string }) {
  const { mode, resolved } = useDeploymentMode();
  const isMobile = useIsMobile();
  const [account, setAccount] = useState<HostedAccountInfo | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [revokeWarning, setRevokeWarning] = useState(false);

  // Same shape as SyncCard's load(): the hosted-sync routes 404 outside
  // local mode, so the request only fires once the deployment mode has
  // genuinely resolved to "local" (the resolved flag closes the window
  // where the hook still reports its in-flight "local" default on a
  // hosted deployment). A stray fetch would 404
  // harmlessly here too and the catch below still resolves `loaded`.
  const load = useCallback(async () => {
    try {
      const settings = await api.getSyncSettings();
      setAccount(settings.account);
    } catch {
      // Sign-in button is the safe default on any load failure.
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (!resolved || mode !== "local") return;
    void load();
  }, [resolved, mode, load]);

  // Keeps this chip in agreement with its sibling copy (GlobalBar + the
  // mobile drawer render one each with independent state) and with
  // SyncSettingsDialog: any of them can change the linked account, and
  // this refetches on the resulting event instead of going stale until
  // a hard reload (#736).
  useEffect(() => {
    if (!resolved || mode !== "local") return;
    const onChanged = () => void load();
    window.addEventListener(HOSTED_ACCOUNT_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(HOSTED_ACCOUNT_CHANGED_EVENT, onChanged);
  }, [resolved, mode, load]);

  // Local-only, and only once the initial settings fetch has resolved --
  // avoids a flash of the sign-in button before we know whether an
  // account is already linked. This is the ONLY point that gates
  // rendering.
  if (!resolved || mode !== "local" || !loaded) return null;

  async function onSignOut() {
    setBusy(true);
    setRevokeWarning(false);
    try {
      const resp = await api.unlinkHostedAccount();
      setAccount(null);
      if (!resp.hosted_revoked) setRevokeWarning(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      data-testid="hosted-account-chip"
      className={`inline-flex min-w-0 flex-col items-end gap-1 ${className ?? ""}`}
    >
      {/* max-w-full, not just min-w-0: the wrapper is a *column* flex box
          with items-end, so a pill wider than the shrunk wrapper does not
          shrink to fit it -- it overflows leftwards, out over ModeSwitch,
          while the nav's own scrollWidth still reads a healthy 390. Only
          looking at the rendered bar catches that one. */}
      <div className="inline-flex min-w-0 max-w-full items-center gap-2 rounded-full border border-rule bg-surface-2 py-1 pl-3 pr-1">
        {account ? (
          <>
            <span
              className="min-w-0 max-w-[16rem] truncate text-[0.8125rem] text-ink-2"
              title={account.email}
            >
              {account.display_name ?? account.email}
            </span>
            {isMobile ? null : (
              <span className="shrink-0 truncate text-[0.6875rem] text-muted">
                - {account.device_name}
              </span>
            )}
            <IconButton
              className="shrink-0"
              variant="subtle"
              size="sm"
              label="Sign out"
              onClick={() => void onSignOut()}
              disabled={busy}
            >
              <LogOut className="size-3.5" />
            </IconButton>
          </>
        ) : (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setLoginOpen(true)}
          >
            {isMobile ? "Sign in" : "Sign in to splitsmith.app"}
          </Button>
        )}
      </div>
      {revokeWarning ? (
        <p className="max-w-[16rem] text-right text-[0.6875rem] text-muted">
          Could not confirm this device was revoked on splitsmith.app - check
          the account page.
        </p>
      ) : null}
      {loginOpen ? (
        <DeviceLoginDialog
          onClose={() => setLoginOpen(false)}
          onLinked={(linked) => {
            setAccount(linked);
            setRevokeWarning(false);
            setLoginOpen(false);
            notifyHostedAccountChanged();
          }}
        />
      ) : null}
    </div>
  );
}
