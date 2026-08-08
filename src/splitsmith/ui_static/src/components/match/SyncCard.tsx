/**
 * SyncCard - local-only hosted-sync status card on MatchOverview
 * (desktop-to-hosted sync MVP, #631 Task 11).
 *
 * Renders only when useDeploymentMode() === "local" - the sync
 * endpoints it calls (GET/PUT /api/settings/hosted-sync, POST
 * /api/match/sync, GET /api/match/sync/status) 404 in hosted mode,
 * same guard idiom as the desktop-token management routes. A hosted
 * install has nothing to push to, so the card is simply absent there
 * rather than rendered disabled.
 *
 * States (text + icon, never color alone):
 *   not configured  - CTA opens SyncSettingsDialog.
 *   syncing         - a sync_match job for this install is
 *                       pending/running in the jobs list the shell
 *                       already polls; button disabled, progress
 *                       message shown. Takes priority over the other
 *                       states below since it reflects a push actually
 *                       in flight right now.
 *   errors          - the last computed push plan can't run (e.g. a
 *                       clip lives outside the match root). Listed in
 *                       full AND the button is disabled - Task 9's
 *                       review found stale=true always accompanies
 *                       errors, so an enabled button here would just
 *                       fail immediately. Checked before "stale".
 *   never synced    - last_synced_at is null.
 *   stale           - last_synced_at is set but pending_media > 0 (or
 *                       the plan is otherwise stale): "N files changed
 *                       since last sync".
 *   synced          - up to date: relative time + an "Open on
 *                       splitsmith.app" link built from the settings
 *                       GET's base_url.
 *
 * Status is this card's own fetch (GET .../sync/status doesn't belong
 * on the shell's per-poll project/beep-queue refetch); it refetches
 * once the sync_match job leaves the active set, same settlement-watch
 * idiom MatchShell uses for its own job-derived state (#663).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  CloudUpload,
  ExternalLink,
  Loader2,
  RefreshCw,
  Settings2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { SyncSettingsDialog } from "@/components/match/SyncSettingsDialog";
import {
  api,
  apiErrorText,
  type HostedSyncSettings,
  type Job,
  type SyncStatusResponse,
} from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import { isJobActive } from "@/lib/jobs";
import { cn } from "@/lib/utils";

export interface SyncCardProps {
  /** Full jobs list from the shell's single poller (MatchShell's
   *  useJobs()). SyncCard does not run its own poller - lib/jobs.ts's
   *  "one poller per shell" convention. */
  jobs: Job[];
  /** Current match id, used to build the "Open on splitsmith.app" link.
   *  Undefined only very early, before the route param resolves. */
  matchId?: string;
}

/** Compact "Xh ago" relative time, local to this card - mirrors
 *  AdminWorkers.tsx's ``relativeTime``. No shared helper exists yet
 *  (grepped before writing this); kept local like DesktopTokensDialog's
 *  ``formatTokenDate`` rather than inventing a shared one for a single
 *  caller. */
function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "just now";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export function SyncCard({ jobs, matchId }: SyncCardProps) {
  const { mode } = useDeploymentMode();
  const local = mode === "local";

  const [status, setStatus] = useState<SyncStatusResponse | null>(null);
  const [settings, setSettings] = useState<HostedSyncSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // Optimistic local echo of the job startSync() just created. The
  // jobs prop comes from MatchShell's useJobs(), which idle-polls
  // every 5s - without this, the button re-enables for up to 5s after
  // a click while the sync_match job is genuinely pending server-side
  // (double-submit window). Cleared once the poller's jobs list shows
  // this job id has left the active set.
  const [startedJob, setStartedJob] = useState<Job | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, cfg] = await Promise.all([
        api.getSyncStatus(),
        api.getSyncSettings(),
      ]);
      setStatus(s);
      setSettings(cfg);
      setLoadError(null);
    } catch (e) {
      setLoadError(apiErrorText(e, "Could not load sync status."));
    }
  }, []);

  useEffect(() => {
    if (!local) return;
    void load();
  }, [local, load]);

  const jobsSyncing = jobs.some((j) => j.kind === "sync_match" && isJobActive(j));
  const runningJob = jobs.find((j) => j.kind === "sync_match" && isJobActive(j)) ?? null;

  // startedJob has settled once the poller's jobs list carries its id
  // in a terminal (non-active) state. Until then - including while
  // the poller hasn't picked the job up at all yet - treat it as
  // still in flight.
  const startedJobSettled =
    startedJob != null && jobs.some((j) => j.id === startedJob.id && !isJobActive(j));
  const syncing = jobsSyncing || (startedJob != null && !startedJobSettled);

  // Drop the optimistic echo once the poller confirms settlement, so
  // a later click can set a fresh one.
  useEffect(() => {
    if (startedJobSettled) setStartedJob(null);
  }, [startedJobSettled]);

  // Refetch status once the sync job settles (succeeded / failed), same
  // active-set-departure idiom MatchShell uses for its own job-derived
  // refetches (#663) - polling status on every tick would be wasted
  // work; a settle is the only time the answer can have changed.
  const wasSyncingRef = useRef(false);
  useEffect(() => {
    if (wasSyncingRef.current && !syncing) {
      void load();
    }
    wasSyncingRef.current = syncing;
  }, [syncing, load]);

  if (!local) return null;

  async function handleSync() {
    setStartError(null);
    setStarting(true);
    try {
      const job = await api.startSync();
      setStartedJob(job);
    } catch (e) {
      setStartError(apiErrorText(e, "Could not start sync."));
    } finally {
      setStarting(false);
    }
  }

  function handleSettingsSaved(updated: HostedSyncSettings) {
    setSettings(updated);
    void load();
  }

  const hasErrors = (status?.errors.length ?? 0) > 0;
  const notConfigured = status != null && !status.configured;
  const buttonDisabled = starting || syncing || hasErrors;

  return (
    <section className="mb-6 rounded-xl border border-rule-strong bg-surface p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className="inline-flex size-9 shrink-0 items-center justify-center rounded-md border border-led-deep bg-surface-3 text-led shadow-[0_0_12px_var(--color-led-glow)]"
          >
            <CloudUpload className="size-4" />
          </span>
          <div>
            <h2 className="font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
              Hosted sync
            </h2>
            <SyncStatusLine
              status={status}
              loadError={loadError}
              syncing={syncing}
              runningJob={runningJob}
            />
          </div>
        </div>

        <div className="flex flex-col items-end gap-1.5">
          {notConfigured ? (
            <Button type="button" size="sm" onClick={() => setSettingsOpen(true)}>
              <Settings2 className="size-3.5" aria-hidden="true" />
              Set up hosted sync
            </Button>
          ) : (
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setSettingsOpen(true)}
                className="font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-subtle hover:text-ink-2"
              >
                Settings
              </button>
              <Button
                type="button"
                size="sm"
                onClick={() => void handleSync()}
                disabled={buttonDisabled}
              >
                {syncing || starting ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <CloudUpload className="size-3.5" aria-hidden="true" />
                )}
                {syncing || starting ? "Syncing..." : "Sync now"}
              </Button>
            </div>
          )}
          {status?.configured &&
          !hasErrors &&
          !syncing &&
          status.last_synced_at &&
          !status.stale &&
          settings?.base_url &&
          matchId ? (
            <a
              href={`${settings.base_url}/match/${matchId}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-led hover:text-led-soft"
            >
              Open on splitsmith.app
              <ExternalLink className="size-3" aria-hidden="true" />
            </a>
          ) : null}
        </div>
      </div>

      {hasErrors ? (
        <ul
          aria-live="polite"
          className="mt-3 space-y-1.5 rounded-md border border-destructive/40 bg-destructive/10 p-2.5"
        >
          {status?.errors.map((e, i) => (
            <li
              key={i}
              className="flex items-start gap-1.5 text-xs text-destructive"
            >
              <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
              <span>{e}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {startError ? (
        <p role="alert" className="mt-2.5 text-xs text-destructive">
          {startError}
        </p>
      ) : null}

      {settingsOpen ? (
        <SyncSettingsDialog
          settings={settings}
          onClose={() => setSettingsOpen(false)}
          onSaved={handleSettingsSaved}
        />
      ) : null}
    </section>
  );
}

function SyncStatusLine({
  status,
  loadError,
  syncing,
  runningJob,
}: {
  status: SyncStatusResponse | null;
  loadError: string | null;
  syncing: boolean;
  runningJob: Job | null;
}) {
  const lineClass = "mt-0.5 flex items-center gap-1.5 font-mono text-[0.75rem] text-muted";

  if (syncing) {
    return (
      <p className={lineClass} aria-live="polite">
        <Loader2 className="size-3.5 shrink-0 animate-spin text-led" aria-hidden="true" />
        {runningJob?.message ?? "Push in progress..."}
      </p>
    );
  }
  if (loadError && !status) {
    return (
      <p className={cn(lineClass, "text-destructive")}>
        <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
        {loadError}
      </p>
    );
  }
  if (!status) {
    return <p className={lineClass}>Checking sync status...</p>;
  }
  if (!status.configured) {
    return (
      <p className={lineClass}>
        <Settings2 className="size-3.5 shrink-0" aria-hidden="true" />
        Not set up yet - pushes this match to your splitsmith.app account.
      </p>
    );
  }
  if (status.errors.length > 0) {
    return (
      <p className={cn(lineClass, "text-destructive")} aria-live="polite">
        <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
        Sync can&apos;t run until these are fixed
      </p>
    );
  }
  if (!status.last_synced_at) {
    return (
      <p className={lineClass}>
        <Clock className="size-3.5 shrink-0" aria-hidden="true" />
        Never synced
      </p>
    );
  }
  if (status.stale) {
    return (
      <p className={lineClass}>
        <RefreshCw className="size-3.5 shrink-0" aria-hidden="true" />
        {status.pending_media} file{status.pending_media === 1 ? "" : "s"} changed
        since last sync
      </p>
    );
  }
  return (
    <p className={lineClass}>
      <CheckCircle2 className="size-3.5 shrink-0 text-beep" aria-hidden="true" />
      Synced {relativeTime(status.last_synced_at)}
    </p>
  );
}
