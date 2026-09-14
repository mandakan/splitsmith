/**
 * ShareShell - the public, token-authorized wrapper around the read-only
 * Results surface (#349). Mounts under /share/:token and provides the same
 * outlet context MatchShell gives Results/ResultsStage, fetched through the
 * anonymous /api/share/{token}/ path (see scopeRequestPath). No auth, no
 * mutations, no persistence - if a fetch 404s the link is gone (revoked,
 * expired, or never existed; the server keeps those indistinguishable).
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Outlet } from "react-router-dom";
import { Link2Off } from "lucide-react";

import {
  api,
  type MatchOrigin,
  type MatchProject,
  type ShooterListEntry,
} from "@/lib/api";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { Brand } from "@/components/ui/Brand";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { pickDefaultShooterSlug } from "@/lib/defaultShooter";

const MARKETING_URL = "https://splitsmith.app";

/** Page frame for every share render path (results, dead link, load
 *  error): one bar shaped like the app's GlobalBar (brand, the match
 *  name as the crumb, a call to the marketing site) and one footer
 *  line. At md+ the frame locks to the viewport (h-dvh) and the middle
 *  region owns scrolling, so the bar and footer stay pinned and a child
 *  that renders min-h-0 flex-1 (Compare's cockpit layout, desktop-gated
 *  at the same md breakpoint) is viewport-bounded without needing
 *  --shell-header-h. Below md the frame grows and the document scrolls
 *  as before - the mobile results viewer keeps its shipped scroll-away
 *  header/footer. */
function ShareFrame({ matchName, children }: { matchName: string | null; children: ReactNode }) {
  const linkClass =
    "rounded text-sm text-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led";
  return (
    <div className="flex min-h-dvh flex-col bg-bg md:h-dvh">
      <header className="relative flex-none border-b border-rule bg-surface">
        <nav aria-label="Global" className="flex items-center gap-4 px-4 py-3 md:px-7">
          <a
            href={MARKETING_URL}
            target="_blank"
            rel="noopener"
            aria-label="Splitsmith"
            className="shrink-0 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
          >
            <Brand variant="bar" />
          </a>
          {matchName ? (
            <span className="hidden min-w-0 items-center gap-2 text-md md:flex">
              <span className="truncate font-medium text-ink">{matchName}</span>
              <span className="text-muted">/</span>
              <span className="text-ink-2">Splits</span>
            </span>
          ) : null}
          <span className="flex-1" />
          <a
            href={MARKETING_URL}
            target="_blank"
            rel="noopener"
            className="shrink-0 rounded-full border border-rule-strong px-3 py-1 text-md text-ink-2 transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
          >
            Analyse your own matches &#8599;
          </a>
        </nav>
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 -bottom-px h-px opacity-55"
          style={{
            background:
              "linear-gradient(to right, transparent, var(--color-led) 18%, var(--color-led) 22%, var(--color-rule-strong) 30%, var(--color-rule-strong) 70%, var(--color-led) 78%, var(--color-led) 82%, transparent)",
          }}
        />
      </header>
      {/* flex column (not a plain block): the dead/error cards center
          themselves with flex-1 + place-items-center, which needs a
          flex parent - a percentage min-height would resolve to 0 here. */}
      <div className="flex flex-1 flex-col md:min-h-0 md:overflow-y-auto">{children}</div>
      <footer className="flex flex-none items-center justify-between gap-3 border-t border-rule px-4 py-3 md:px-7">
        <a href={MARKETING_URL} target="_blank" rel="noopener" className={linkClass}>
          Made with Splitsmith
        </a>
        <a href={MARKETING_URL} target="_blank" rel="noopener" className={linkClass}>
          splitsmith.app
        </a>
      </footer>
    </div>
  );
}

export function ShareShell() {
  const [shooters, setShooters] = useState<ShooterListEntry[]>([]);
  const [project, setProject] = useState<MatchProject | null>(null);
  // Carried through for the outlet context's shape only - the public
  // share surface never renders a write CTA regardless of origin, so
  // nothing here reads it back (#631 Task 10).
  const [origin, setOrigin] = useState<MatchOrigin | null>(null);
  const [dead, setDead] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  useEffect(() => {
    let alive = true;
    setLoadFailed(false);
    api
      .listMatchShooters()
      .then((r) => {
        if (!alive) return;
        setShooters(r.shooters);
        setOrigin(r.origin);
        const slug = pickDefaultShooterSlug(r.shooters);
        if (slug) {
          api
            .getProject(slug)
            .then((p) => {
              if (alive) setProject(p);
            })
            .catch(() => {
              // Roster loaded but the base project fetch failed (#540):
              // without a project the Results overview idles on its
              // standby state forever, so surface a retryable error
              // instead of a silent spinner. A dead token never lands
              // here - it already 404s on the roster fetch above.
              if (!alive) return;
              setProject(null);
              setLoadFailed(true);
            });
        }
      })
      .catch(() => {
        if (alive) setDead(true);
      });
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  if (dead)
    return (
      <ShareFrame matchName={null}>
        <ShareUnavailable />
      </ShareFrame>
    );
  if (loadFailed)
    return (
      <ShareFrame matchName={null}>
        <ShareLoadError onRetry={refresh} />
      </ShareFrame>
    );

  const context: MatchShellOutletContext = {
    project,
    health: null,
    shooters,
    refresh,
    origin,
    // The public share surface advertises no capabilities today - it
    // never renders a write CTA regardless of origin. When write-scoped
    // share tokens land, this reads the share payload's own field instead.
    capabilities: [],
  };
  return (
    <ShareFrame matchName={project?.name ?? null}>
      <Outlet context={context} />
    </ShareFrame>
  );
}

/** Full-page transient-failure state (#540): the roster loaded (token is
 *  live) but the base project fetch failed, so the overview cannot render.
 *  Distinct from ShareUnavailable - this one is retryable. */
function ShareLoadError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="grid flex-1 place-items-center px-6 py-10">
      <div className="flex max-w-sm flex-col items-center gap-3 text-center">
        <Label tone="subtle">Share link</Label>
        <h1 className="text-lg font-semibold text-ink">Could not load results</h1>
        <p className="text-md text-muted">
          The link is fine, but the results data did not load. This is usually temporary.
        </p>
        <Button type="button" onClick={onRetry}>
          Try again
        </Button>
      </div>
    </div>
  );
}

/** Full-page dead-link state. Shown when the share token 404s - revoked,
 *  expired, or never valid. No login CTA. */
function ShareUnavailable() {
  return (
    <div className="grid flex-1 place-items-center px-6 py-10">
      <div className="flex max-w-sm flex-col items-center gap-3 text-center">
        <Link2Off className="size-8 text-subtle" aria-hidden />
        <Label tone="subtle">Share link</Label>
        <h1 className="text-lg font-semibold text-ink">This link is no longer available</h1>
        <p className="text-md text-muted">Ask whoever shared it for a fresh link.</p>
      </div>
    </div>
  );
}
