/**
 * Signed-in account chip + sign-out (auth-swap PR2c).
 *
 * Self-gating: renders nothing outside hosted mode or before the account
 * resolves, so it can be dropped into any shell header and stays invisible
 * on the desktop app. Shows the account email and a sign-out button that
 * revokes the session and drops to the login surface (the deployment-mode
 * gate redirects once the auth status flips to anonymous).
 *
 * Also links to /account (#867 Task 11), which owns display name and
 * desktop token management - an account-level concern, same tier as
 * sign-out, not match-scoped like ShareDialog. Desktop tokens used to
 * open in a dialog straight from this chip (#631 Task 10); that dialog
 * is now a section on the account page instead, and the chip just
 * links there. The control count this chip carries is unchanged - one
 * icon button was traded for one icon link - so the phone-width
 * reasoning below still holds.
 *
 * Phone width (#733): measured at 326 -> 632 on a 390px bar, i.e. further
 * past the edge than the chip that issue was filed about. What it drops is
 * the email, and the treatment is deliberately not HostedAccountChip's:
 * that chip keeps its email because the email is the fact it exists to
 * report, whereas here you *are* the account and the email is the only
 * thing on the chip that is not an affordance. All three controls stay
 * reachable -- with three icon buttons the admin variant already wants
 * 130px of the 158px a phone leaves, which no email would fit inside
 * legibly.
 */

import * as React from "react";
import { LogOut, Server, UserCog } from "lucide-react";
import { Link } from "react-router-dom";

import { IconButton, iconButtonVariants } from "@/components/ui/IconButton";
import { useDeploymentMode } from "@/lib/features";
import { useIsMobile } from "@/lib/useIsMobile";
import { useAuth } from "@/lib/auth";

export function AccountChip({ className }: { className?: string }) {
  const { mode } = useDeploymentMode();
  const isMobile = useIsMobile();
  const { status, user, logout } = useAuth();
  const [busy, setBusy] = React.useState(false);

  // Hosted-only, and only once a real account is resolved.
  if (mode !== "hosted" || status !== "authed" || !user) return null;

  async function onLogout() {
    setBusy(true);
    try {
      await logout();
    } catch {
      // logout() only flips to anon on a confirmed server revoke; if it
      // threw, the session is still live and the user stays signed in.
      // Swallow so it isn't an unhandled rejection -- the button re-enables
      // (finally) and they can retry.
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      data-testid="account-chip"
      className={`inline-flex min-w-0 items-center gap-2 rounded-full border border-rule bg-surface-2 py-1 pr-1 ${isMobile ? "pl-1" : "pl-3"} ${className ?? ""}`}
    >
      {isMobile ? null : (
        <span
          className="min-w-0 max-w-[16rem] truncate text-[0.8125rem] text-ink-2"
          title={user.email}
        >
          {user.display_name ?? user.email}
        </span>
      )}
      {user.is_admin ? (
        <Link
          to="/admin/workers"
          aria-label="Workers (admin)"
          title="Workers (admin)"
          className={iconButtonVariants({
            variant: "subtle",
            size: "sm",
            className: "shrink-0",
          })}
        >
          <Server className="size-3.5" />
        </Link>
      ) : null}
      <Link
        to="/account"
        aria-label="Account"
        title="Account"
        className={iconButtonVariants({
          variant: "subtle",
          size: "sm",
          className: "shrink-0",
        })}
      >
        <UserCog className="size-3.5" />
      </Link>
      <IconButton
        className="shrink-0"
        variant="subtle"
        size="sm"
        label="Sign out"
        onClick={onLogout}
        disabled={busy}
      >
        <LogOut className="size-3.5" />
      </IconButton>
    </div>
  );
}
