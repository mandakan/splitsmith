/**
 * ShareShell - the public, token-authorized wrapper around the read-only
 * Results surface (#349). Mounts under /share/:token and provides the same
 * outlet context MatchShell gives Results/ResultsStage, fetched through the
 * anonymous /api/share/{token}/ path (see scopeRequestPath). No auth, no
 * mutations, no persistence - if a fetch 404s the link is gone (revoked,
 * expired, or never existed; the server keeps those indistinguishable).
 */
import { useCallback, useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import { Link2Off, RotateCcw } from "lucide-react";

import {
  api,
  type MatchProject,
  type ShooterListEntry,
} from "@/lib/api";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { pickDefaultShooterSlug } from "@/lib/defaultShooter";

export function ShareShell() {
  const [shooters, setShooters] = useState<ShooterListEntry[]>([]);
  const [project, setProject] = useState<MatchProject | null>(null);
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

  if (dead) return <ShareUnavailable />;
  if (loadFailed) return <ShareLoadError onRetry={refresh} />;

  const context: MatchShellOutletContext = {
    project,
    health: null,
    shooters,
    refresh,
  };
  return (
    <div className="min-h-dvh bg-bg">
      <Outlet context={context} />
    </div>
  );
}

/** Full-page transient-failure state (#540): the roster loaded (token is
 *  live) but the base project fetch failed, so the overview cannot render.
 *  Distinct from ShareUnavailable - this one is retryable. */
function ShareLoadError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="grid min-h-dvh place-items-center bg-bg px-6 py-10">
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
    <div className="grid min-h-dvh place-items-center bg-bg px-6 py-10">
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
