/**
 * Splits -- the match read as numbers (spec 2026-09-13 s4.5, UX PR 4).
 * Route stays ``/results``; also mounted anonymously under
 * ``/share/:token/results``, where it renders the same table minus every
 * owner affordance (Share, refresh, Audit links, audit wording).
 *
 * Five stats over the audited stages, then one row per stage with the
 * splits the audit produced before the scorecard the scoreboard
 * imported. Everything comes from one project GET per shooter, derived
 * through lib/splitsTable.ts; the page maps rows to primitives and owns
 * only the fetches, the share / refresh chrome and the shooter filter.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useOutletContext, useParams } from "react-router-dom";
import { Loader2, RefreshCw } from "lucide-react";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { ShareDialog } from "@/components/results/ShareDialog";
import { SplitsCards } from "@/components/results/SplitsCards";
import { SplitsTable, type SplitsHrefs } from "@/components/results/SplitsTable";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Stat, StatStrip } from "@/components/ui/Stat";
import { ApiError, api, type MatchProject, type SyncStatusResponse } from "@/lib/api";
import { pickDefaultShooterSlug } from "@/lib/defaultShooter";
import { useDeploymentMode } from "@/lib/features";
import { useMatchHref } from "@/lib/matchHref";
import { formatClock } from "@/lib/overview";
import {
  buildSplitsRows,
  firstPlayable,
  scoreboardTotals,
  scorecardSyncedAt,
  splitsStats,
} from "@/lib/splitsTable";
import { useIsMobile } from "@/lib/useIsMobile";

function formatDate(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  if (Number.isNaN(d.getTime())) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d.getUTCDate()} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function fmt(v: number | null, digits: number): string {
  return v == null ? "—" : v.toFixed(digits);
}

export function Results() {
  const { project, shooters, refresh } = useOutletContext<MatchShellOutletContext>();
  const href = useMatchHref();
  const isMobile = useIsMobile();

  // Share button: hosted mode only, and only on the owner route. The same
  // component renders for anonymous share viewers under /share/:token -
  // the button must not appear there. useDeploymentMode() reports "local"
  // until the features fetch resolves, so the button pops in after it.
  const { mode: deploymentMode, resolved: modeResolved } = useDeploymentMode();
  const { token: shareToken, matchId } = useParams<{ token?: string; matchId?: string }>();
  const isShare = Boolean(shareToken);
  const canShare = deploymentMode === "hosted" && !shareToken;
  const [showShare, setShowShare] = useState(false);

  // Local mode cannot mint share links (the store and the public share
  // surface are hosted-only), but a match that has been pushed at least
  // once has a hosted twin whose Splits page carries the real Share
  // button - so deep-link there instead of showing nothing. Gated on
  // ``resolved`` because the sync endpoints 404 in hosted mode and the
  // hook reports "local" until the features fetch settles. The link
  // stays through stale states (same call as SyncCard's own link) with
  // an "unsynced changes" hint so a fresh audit isn't shared before it
  // has been pushed.
  const probeHostedLink = modeResolved && deploymentMode === "local" && !shareToken && Boolean(matchId);
  const [hosted, setHosted] = useState<{ status: SyncStatusResponse; baseUrl: string } | null>(null);
  useEffect(() => {
    if (!probeHostedLink) {
      setHosted(null);
      return;
    }
    let cancelled = false;
    void Promise.all([api.getSyncStatus(), api.getSyncSettings()])
      .then(([status, settings]) => {
        if (cancelled) return;
        setHosted(settings.base_url ? { status, baseUrl: settings.base_url } : null);
      })
      // Offline or unconfigured is a normal desktop condition - no link, no error.
      .catch(() => {
        if (!cancelled) setHosted(null);
      });
    return () => {
      cancelled = true;
    };
  }, [probeHostedLink, matchId]);
  const hostedResultsHref =
    hosted && hosted.status.configured && hosted.status.last_synced_at && matchId
      ? `${hosted.baseUrl}/match/${matchId}/results`
      : null;
  const hostedStale = Boolean(hosted?.status.stale);

  // Refresh-from-scoreboard: owner-only (share viewers cannot fetch
  // upstream), and only worth showing once the match is scoreboard-linked.
  // Works in local mode too - it re-pulls each linked shooter's scorecard.
  const canRefresh = !shareToken && project?.scoreboard_match_id != null;
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refreshFromScoreboard() {
    setRefreshing(true);
    setError(null);
    try {
      const linked = shooters.filter((s) => s.selected_competitor_id != null);
      const results = await Promise.allSettled(linked.map((s) => api.refreshScoreboardTimes(s.slug)));
      // Always refresh, even on partial failure - shooters whose refresh
      // DID succeed must still become visible instead of being hidden
      // behind a sibling's error.
      refresh();
      const failedSlugs = results
        .map((r, i) => (r.status === "rejected" ? linked[i].slug : null))
        .filter((slug): slug is string => slug !== null);
      setError(failedSlugs.length > 0 ? `Refresh failed for: ${failedSlugs.join(", ")}` : null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setRefreshing(false);
    }
  }

  // One project per shooter. The outlet project belongs to the default
  // shooter and seeds the map synchronously so the first paint has rows;
  // the other shooters' projects (read-only GET) arrive after. A shooter
  // whose fetch failed is absent from the map and renders no cell: a
  // wrong figure is worse than no figure on a results surface.
  const defaultSlug = pickDefaultShooterSlug(shooters) ?? null;
  const [fetched, setFetched] = useState<Record<string, MatchProject | null>>({});
  useEffect(() => {
    const others = shooters.filter((s) => s.slug !== defaultSlug);
    if (others.length === 0) {
      setFetched({});
      return;
    }
    let alive = true;
    setFetched({});
    Promise.all(
      others.map((s) =>
        api
          .getProject(s.slug)
          .then((p) => [s.slug, p] as const)
          .catch(() => [s.slug, null] as const),
      ),
    ).then((entries) => {
      if (alive) setFetched(Object.fromEntries(entries));
    });
    return () => {
      alive = false;
    };
  }, [shooters, defaultSlug]);
  const projects = useMemo<Record<string, MatchProject | null>>(
    () => (defaultSlug ? { ...fetched, [defaultSlug]: project } : fetched),
    [fetched, defaultSlug, project],
  );

  const [filterSlug, setFilterSlug] = useState<string | null>(null);
  const rows = useMemo(
    () => buildSplitsRows({ projects, shooters, leadSlug: defaultSlug, filterSlug, share: isShare }),
    [projects, shooters, defaultSlug, filterSlug, isShare],
  );
  const stats = useMemo(() => splitsStats(rows), [rows]);
  const totals = useMemo(() => scoreboardTotals(rows), [rows]);
  const playable = useMemo(() => firstPlayable(rows), [rows]);
  const syncedAt = scorecardSyncedAt(project);

  const hrefs = useMemo<SplitsHrefs>(
    () => ({
      stage: (slug, n) => href("results", slug, String(n)),
      audit: (slug, n) => href("audit", slug, String(n)),
    }),
    [href],
  );

  if (!project) {
    return <p className="px-7 py-10 text-md text-muted">Reading match state...</p>;
  }

  const shooterLine =
    shooters.length > 1 ? `${shooters.length} shooters` : (shooters[0]?.name ?? project.competitor_name ?? null);
  const stageTakes = shooters.length > 1 ? "stage takes" : "stages";

  const sub = isShare ? (
    <>
      {shooterLine}
      {project.match_date ? (
        <>
          {" · "}
          <time dateTime={project.match_date}>{formatDate(project.match_date)}</time>
        </>
      ) : null}
      {" · "}
      {stats.audited} {stats.audited === 1 ? "stage" : "stages"} on video
    </>
  ) : (
    <>
      {shooterLine ? <>{shooterLine} &middot; </> : null}
      {stats.audited} of {stats.total} {stageTakes} audited
      {syncedAt ? (
        <>
          {" · "}Scorecard synced {formatDateTime(syncedAt)}
          {canRefresh ? (
            <button
              type="button"
              onClick={() => void refreshFromScoreboard()}
              disabled={refreshing}
              aria-label={refreshing ? "Refreshing from scoreboard" : "Refresh from scoreboard"}
              className="ml-1.5 inline-flex align-middle text-ink-2 transition-colors hover:text-ink disabled:opacity-60"
            >
              {refreshing ? (
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="size-3.5" aria-hidden />
              )}
            </button>
          ) : null}
        </>
      ) : null}
    </>
  );

  const shareButton = canShare ? (
    <Button type="button" onClick={() => setShowShare(true)} aria-label="Manage share links for these results">
      Share
    </Button>
  ) : hostedResultsHref ? (
    <span className="inline-flex flex-wrap items-center gap-2">
      {hostedStale ? <span className="text-sm text-ink-2">Unsynced changes - sync first to share them</span> : null}
      <Button asChild>
        <a
          href={hostedResultsHref}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Share on splitsmith.app - opens the hosted results page"
        >
          Share on splitsmith.app
        </a>
      </Button>
    </span>
  ) : null;

  // The one primary action on the page.
  const playAll = playable ? (
    <Button variant="primary" asChild>
      <Link to={`${hrefs.stage(playable.slug, playable.stageNumber)}?play=all`}>Play all</Link>
    </Button>
  ) : null;

  return (
    <div className="mx-auto w-full max-w-[1100px] px-4 py-4 md:px-7 md:py-5">
      <PageHeader
        title="Splits"
        sub={sub}
        actions={
          <>
            {shareButton}
            {playAll}
          </>
        }
      >
        {shooters.length > 1 ? (
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by shooter">
            <button type="button" onClick={() => setFilterSlug(null)} aria-pressed={filterSlug == null}>
              <Chip tone={filterSlug == null ? "ok" : "neutral"}>All</Chip>
            </button>
            {shooters.map((s) => (
              <button
                key={s.slug}
                type="button"
                onClick={() => setFilterSlug(s.slug === filterSlug ? null : s.slug)}
                aria-pressed={filterSlug === s.slug}
              >
                <Chip tone={filterSlug === s.slug ? "ok" : "neutral"} tick={s.slug === defaultSlug ? "draw" : "muted"}>
                  {s.name}
                </Chip>
              </button>
            ))}
          </div>
        ) : null}
      </PageHeader>

      {error ? (
        <p role="alert" className="mb-3 text-sm text-led-text">
          {error}
        </p>
      ) : null}

      <StatStrip lead className="mb-4">
        <Stat label="Avg draw" value={fmt(stats.avgDraw, 2)} unit={stats.avgDraw != null ? "s" : undefined} tone={stats.avgDraw == null ? "dim" : "ink"} />
        <Stat label="Avg split" value={fmt(stats.avgSplit, 3)} unit={stats.avgSplit != null ? "s" : undefined} tone={stats.avgSplit == null ? "dim" : "ink"} />
        <Stat label="Fastest split" value={fmt(stats.fastestSplit, 3)} unit={stats.fastestSplit != null ? "s" : undefined} tone={stats.fastestSplit == null ? "dim" : "ink"} />
        <Stat label="Shots" value={String(stats.shots)} tone={stats.shots === 0 ? "dim" : "ink"} />
        <Stat label="Scored time" value={stats.scoredTime != null ? formatClock(stats.scoredTime) : "—"} tone={stats.scoredTime == null ? "dim" : "ink"} />
      </StatStrip>

      {isMobile ? (
        <SplitsCards rows={rows} share={isShare} hrefs={hrefs} />
      ) : (
        <SplitsTable
          rows={rows}
          multi={shooters.length > 1}
          filterSlug={filterSlug}
          share={isShare}
          totals={totals}
          hrefs={hrefs}
        />
      )}

      {canShare && showShare ? <ShareDialog onClose={() => setShowShare(false)} /> : null}
    </div>
  );
}
