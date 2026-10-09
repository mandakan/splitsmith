/**
 * AccountMenu -- the account pill at the top right (spec 2026-10-09): you,
 * as your avatar (your logo from the shooter book once set, else your
 * initials), and everything about you behind it: You, Shooters, Branding,
 * and on splitsmith.app the Account page. Both modes, every page. The
 * splitsmith.app connection keeps its own chip beside it (HostedAccountChip
 * locally, AccountChip hosted): the local one opens its sign-in dialog from
 * itself, which a popover would unmount.
 */
import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Link } from "react-router-dom";

import { Avatar } from "@/components/ui/AvatarStack";
import { Menu, menuItemClass } from "@/components/ui/Menu";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useDeploymentMode } from "@/lib/features";
import { initials } from "@/lib/shooters";

interface Me {
  name: string | null;
  logo: string | null;
}

export function AccountMenu({ className }: { className?: string }) {
  const { mode, resolved } = useDeploymentMode();
  const { status } = useAuth();
  const [open, setOpen] = useState(false);
  const [me, setMe] = useState<Me>({ name: null, logo: null });
  const visible = resolved && (mode === "local" || status === "authed");

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    void (async () => {
      try {
        const pin = await api.getScoreboardIdentity();
        if (!alive || !pin) return;
        const book = await api.getShooterBook().catch(() => ({ entries: [] }));
        const mine = book.entries.find((e) => e.shooter_id === pin.shooter_id);
        const logo = mine?.identity.logo
          ? `/api/me/shooter-book/${pin.shooter_id}/logo?v=${encodeURIComponent(mine.identity.logo)}`
          : null;
        if (alive) setMe({ name: pin.display_name ?? null, logo });
      } catch {
        /* not pinned yet, or offline: the pill shows a generic avatar */
      }
    })();
    return () => {
      alive = false;
    };
  }, [visible]);

  if (!visible) return null;
  const close = () => setOpen(false);
  const items: { to: string; label: string }[] = [
    { to: "/you", label: "You" },
    { to: "/shooters", label: "Shooters" },
    { to: "/you#brand", label: "Branding" },
    ...(mode === "hosted" ? [{ to: "/account", label: "Account" }] : []),
  ];
  return (
    <div className={`relative ${className ?? ""}`}>
      <button
        type="button"
        aria-label="Your account"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 rounded-full border border-rule bg-surface-2 p-0.5 pr-1.5 text-muted transition-colors hover:text-ink"
      >
        <Avatar initials={me.name ? initials(me.name) : "?"} tone="you" size="sm" logo={me.logo} name={me.name ?? "You"} />
        <ChevronDown className="size-3.5" aria-hidden />
      </button>
      <Menu open={open} onClose={close} align="right" className="min-w-44">
        {items.map((item) => (
          <Link key={item.to} to={item.to} role="menuitem" className={menuItemClass} onClick={close}>
            {item.label}
          </Link>
        ))}
      </Menu>
    </div>
  );
}
