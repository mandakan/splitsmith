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
import { Link2Off } from "lucide-react";

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
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  useEffect(() => {
    let alive = true;
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
              if (alive) setProject(null);
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
