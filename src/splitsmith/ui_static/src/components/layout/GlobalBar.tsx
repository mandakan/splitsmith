/**
 * GlobalBar - row one of the app's single header (#550).
 *
 * Owns only what is true on every surface: the brand, the workspace mode
 * switch, and the account menu. Anything that depends on which shell is
 * mounted (breadcrumbs, shooter chips, dev steps, switch project) belongs
 * in that shell's context row instead -- see ``useShellContextSlot``.
 *
 * Not rendered on mobile. RootLayout gates it on ``useIsMobile`` because
 * MatchShell's mobile header and nav drawer already carry the account
 * menu, and a second stacked row costs too much vertical space on a
 * phone.
 */

import { AccountChip } from "@/components/AccountChip";
import { HostedAccountChip } from "@/components/account/HostedAccountChip";
import { Brand, ModeSwitch } from "@/components/ui";

export function GlobalBar() {
  return (
    <nav
      aria-label="Global"
      className="flex items-center gap-4 px-7 py-3"
    >
      <Brand variant="compact" />
      <span className="font-display text-base font-bold uppercase tracking-tight text-ink">
        Splitsmith
      </span>
      <div className="flex-1" />
      <ModeSwitch size="sm" />
      <HostedAccountChip />
      <AccountChip />
    </nav>
  );
}
