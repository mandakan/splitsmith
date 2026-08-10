/**
 * GlobalBar - row one of the app's single header (#550).
 *
 * Owns only what is true on every surface: the brand, the workspace mode
 * switch, and the account menu. Anything that depends on which shell is
 * mounted (breadcrumbs, shooter chips, dev steps, switch project) belongs
 * in that shell's context row instead -- see ``useShellContextSlot``.
 *
 * Mostly not rendered on mobile: RootLayout gates it on ``useIsMobile``
 * because MatchShell's mobile header and nav drawer already carry the
 * account menu, and a second stacked row costs too much vertical space on
 * a phone. But that gate is conditional on a shell *owning* the mobile
 * account menu, and the shell-less routes (/pick and friends) own nothing
 * -- so this bar is what a phone gets there, and it has to fit one.
 *
 * At 390px it did not (#733). The bar was 656px wide signed in, and the
 * account chip -- last item, so first off the edge -- took the linked
 * email and the sign-out control with it. The pixels came from a fixed
 * desktop budget: 56 of padding, 58 of wordmark, 152 of ModeSwitch and 64
 * of gaps left 36px for a chip that needs 50 before it draws any text.
 * The wordmark is the cheapest 58 to give back, because the brand glyph
 * sits immediately to its left saying the same word; tightening padding
 * and gaps to phone-appropriate values returns another 56. The chips
 * spend the rest -- see their own notes for what each drops.
 */

import { AccountChip } from "@/components/AccountChip";
import { HostedAccountChip } from "@/components/account/HostedAccountChip";
import { Brand, ModeSwitch } from "@/components/ui";
import { useIsMobile } from "@/lib/useIsMobile";
import { cn } from "@/lib/utils";

export function GlobalBar() {
  // One definition of "mobile" for the whole bar rather than a JS gate for
  // the wordmark and a CSS `sm:` for the spacing -- those two disagree
  // between 640 and 767px, which is exactly the range a small tablet sits
  // in. The chips gate on the same hook for the same reason.
  const isMobile = useIsMobile();
  return (
    <nav
      aria-label="Global"
      className={cn("flex items-center py-3", isMobile ? "gap-2 px-4" : "gap-4 px-7")}
    >
      <Brand variant="compact" className="shrink-0" />
      {isMobile ? null : (
        <span className="font-display text-base font-bold uppercase tracking-tight text-ink">
          Splitsmith
        </span>
      )}
      <div className="flex-1" />
      <ModeSwitch size="sm" />
      <HostedAccountChip />
      <AccountChip />
    </nav>
  );
}
