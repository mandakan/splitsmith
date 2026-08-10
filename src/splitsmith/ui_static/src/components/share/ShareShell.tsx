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
import { Link2Off, RotateCcw } from "lucide-react";

import {
  api,
  type MatchOrigin,
  type MatchProject,
  type ShooterListEntry,
} from "@/lib/api";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { BrandMark } from "@/components/ui/Brand";
import { pickDefaultShooterSlug } from "@/lib/defaultShooter";

const MARKETING_URL = "https://splitsmith.app";

/** Branded page frame for every share render path (results, dead link,
 *  load error): one thin header + one footer line, both linking to the
 *  marketing site. The frame locks to the viewport (h-dvh); the middle
 *  region owns scrolling, so the branded header/footer stay pinned and
 *  a child that renders min-h-0 flex-1 (Compare's cockpit layout) is
 *  viewport-bounded without needing --shell-header-h. */
function ShareFrame({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-dvh flex-col bg-bg">
      <header className="flex-none border-b border-rule bg-surface">
        <div className="mx-auto flex w-full max-w-[1100px] items-center justify-between gap-3 px-4 py-2.5 md:px-7">
          <a
            href={MARKETING_URL}
            target="_blank"
            rel="noopener"
            className="inline-flex items-center gap-2 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
          >
            <BrandMark className="size-5" />
            <span className="font-display text-sm font-bold uppercase tracking-tight text-ink">
              Splitsmith
            </span>
          </a>
          <a
            href={MARKETING_URL}
            target="_blank"
            rel="noopener"
            className="rounded font-mono text-[0.625rem] uppercase tracking-[0.14em] text-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
          >
            splitsmith.app
          </a>
        </div>
      </header>
      {/* flex column (not a plain block): the dead/error cards center
          themselves with flex-1 + place-items-center, which needs a
          flex parent - a percentage min-height would resolve to 0 here. */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</div>
      <footer className="flex-none border-t border-rule">
        <div className="mx-auto w-full max-w-[1100px] px-4 py-4 md:px-7">
          <a
            href={MARKETING_URL}
            target="_blank"
            rel="noopener"
            className="rounded font-mono text-[0.625rem] uppercase tracking-[0.14em] text-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
          >
            Made with Splitsmith - analyze your own matches
          </a>
        </div>
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
      <ShareFrame>
        <ShareUnavailable />
      </ShareFrame>
    );
  if (loadFailed)
    return (
      <ShareFrame>
        <ShareLoadError onRetry={refresh} />
      </ShareFrame>
    );

  const context: MatchShellOutletContext = {
    project,
    health: null,
    shooters,
    refresh,
    origin,
  };
  return (
    <ShareFrame>
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
      <div className="flex max-w-sm flex-col items-center gap-4 text-center">
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.14em] text-subtle">
          Share link
        </span>
        <h1 className="font-display text-xl font-bold uppercase tracking-tight text-ink">
          Could not load results
        </h1>
        <p className="text-sm text-muted">
          The link is fine, but the results data did not load. This is
          usually temporary.
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex min-h-11 items-center gap-2 rounded border border-edge bg-surface-2 px-4 font-mono text-xs uppercase tracking-[0.14em] text-ink hover:bg-surface-3"
        >
          <RotateCcw className="size-3.5" aria-hidden />
          Try again
        </button>
      </div>
    </div>
  );
}

/** Full-page dead-link state. Shown when the share token 404s - revoked,
 *  expired, or never valid. Instrument-panel aesthetic; no login CTA. */
function ShareUnavailable() {
  return (
    <div className="grid flex-1 place-items-center px-6 py-10">
      <div className="flex max-w-sm flex-col items-center gap-4 text-center">
        <Link2Off className="size-8 text-subtle" aria-hidden />
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.14em] text-subtle">
          Share link
        </span>
        <h1 className="font-display text-xl font-bold uppercase tracking-tight text-ink">
          This link is no longer available
        </h1>
        <p className="text-sm text-muted">
          Ask whoever shared it for a fresh link.
        </p>
      </div>
    </div>
  );
}
