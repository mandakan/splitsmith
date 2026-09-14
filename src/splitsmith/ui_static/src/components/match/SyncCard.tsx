/**
 * SyncCard - local-only hosted-sync status card on MatchOverview
 * (desktop-to-hosted sync MVP, #631 Task 11).
 *
 * Renders only when useDeploymentMode().mode === "local" - the sync
 * endpoints it calls (GET/PUT /api/settings/hosted-sync, POST
 * /api/match/sync, GET /api/match/sync/status) 404 in hosted mode,
 * same guard idiom as the desktop-token management routes. A hosted
 * install has nothing to push to, so the card is simply absent there
 * rather than rendered disabled.
 *
 * States (text + icon, never color alone):
 *   not configured  - CTA opens SyncSettingsDialog.
 *   syncing         - a sync_match job for THIS match is
 *                       pending/running in the jobs list the shell
 *                       already polls (matched on Job.match_id - the
 *                       jobs list is cross-match); button disabled,
 *                       progress message shown. Takes priority over the
 *                       other states below since it reflects a push
 *                       actually in flight right now.
 *   other syncing   - a sync_match job for a DIFFERENT match is
 *                       active: button parked (one push at a time) with
 *                       an honest "another match is syncing" line
 *                       instead of mirroring that match's progress.
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
 *   synced          - up to date: relative time.
 *
 * An "Open on splitsmith.app" link (built from the settings GET's
 * base_url) renders in every state after the first push, i.e. whenever
 * last_synced_at is set - stale or not, the hosted match is there.
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
 *  (grepped before writing this); kept local like DesktopTokensSection's
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

  // Refetch keyed on matchId: the card is NOT remounted on match
  // navigation (react-router reuses the instance above the :matchId
  // param), so without this a never-synced match B kept showing match
  // A's cached status after switching. Reset the per-match state first
  // so B renders "Checking sync status..." instead of A's answer while
  // the fetch is in flight.
  useEffect(() => {
    if (!local) return;
    setStatus(null);
    setLoadError(null);
    setStartError(null);
    setStartedJob(null);
    void load();
  }, [local, load, matchId]);

  // The jobs list is global (cross-match), so match on match_id too -
  // by kind alone, match B's card mirrors match A's in-flight push
  // (progress line + disabled "Syncing..." button). A foreign active
  // sync still parks the button (one push at a time), but is reported
  // as another match's sync, not this one's.
  const isActiveSync = (j: Job) => j.kind === "sync_match" && isJobActive(j);
  const runningJob =
    jobs.find((j) => isActiveSync(j) && j.match_id === matchId) ?? null;
  const jobsSyncing = runningJob != null;
  const otherMatchSyncing =
    !jobsSyncing && jobs.some((j) => isActiveSync(j) && j.match_id !== matchId);

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
  const buttonDisabled = starting || syncing || hasErrors || otherMatchSyncing;

  return (
    // One-line row (spec 2026-09-13 s4.2; UX PR 3): icon, name, status
    // sentence, then the actions on the right. Errors and the start
    // failure render as extra lines inside the same row. No card padding,
    // no glow; the page's primary button is the next-step action, so the
    // sync button stays `default`.
    <section
      aria-label="Hosted sync"
      className="mb-4 rounded-[10px] border border-rule bg-surface px-3.5 py-2"
    >
      <div className="flex flex-wrap items-center gap-3">
        <span
          aria-hidden="true"
          className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-md border border-rule-strong text-muted"
        >
          <CloudUpload className="size-3" />
        </span>
        <h2 className="text-md font-medium text-ink">Hosted sync</h2>
        <SyncStatusLine
          status={status}
          loadError={loadError}
          syncing={syncing}
          runningJob={runningJob}
          otherMatchSyncing={otherMatchSyncing}
        />
        <div className="ml-auto flex items-center gap-2">
          {/* The hosted match exists from the first push onward, so the
              link stays through stale / syncing / plan-error states -
              the status line beside it already says what is unpushed. */}
          {status?.configured && status.last_synced_at && settings?.base_url && matchId ? (
            <a
              href={`${settings.base_url}/match/${matchId}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink"
            >
              Open on splitsmith.app
              <ExternalLink className="size-3" aria-hidden="true" />
            </a>
          ) : null}
          {notConfigured ? (
            <Button type="button" size="sm" onClick={() => setSettingsOpen(true)}>
              <Settings2 className="size-3.5" aria-hidden="true" />
              Set up hosted sync
            </Button>
          ) : (
            <>
              <Button type="button" size="sm" variant="ghost" onClick={() => setSettingsOpen(true)}>
                Settings
              </Button>
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
            </>
          )}
        </div>
      </div>

      {hasErrors ? (
        <ul aria-live="polite" className="mt-2 space-y-1 pl-[34px]">
          {status?.errors.map((e, i) => (
            <li key={i} className="flex items-start gap-1.5 text-sm text-led-text">
              <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
              <span>{e}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {startError ? (
        <p role="alert" className="mt-1.5 pl-[34px] text-sm text-led-text">
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
  otherMatchSyncing,
}: {
  status: SyncStatusResponse | null;
  loadError: string | null;
  syncing: boolean;
  runningJob: Job | null;
  otherMatchSyncing: boolean;
}) {
  const lineClass = "flex items-center gap-1.5 text-sm text-muted";

  if (syncing) {
    return (
      <p className={lineClass} aria-live="polite">
        <Loader2 className="size-3.5 shrink-0 animate-spin text-live" aria-hidden="true" />
        {runningJob?.message ?? "Push in progress..."}
      </p>
    );
  }
  if (otherMatchSyncing) {
    return (
      <p className={lineClass} aria-live="polite">
        <Clock className="size-3.5 shrink-0" aria-hidden="true" />
        Another match is syncing - this one can start when it finishes
      </p>
    );
  }
  if (loadError && !status) {
    return (
      <p className={cn(lineClass, "text-led-text")}>
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
      <p className={cn(lineClass, "text-led-text")} aria-live="polite">
        <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
        Sync can&apos;t run until these are fixed
      </p>
    );
  }
  if ((status.remote_changes ?? 0) > 0) {
    return (
      <p className={lineClass}>
        <RefreshCw className="size-3.5 shrink-0 text-live" aria-hidden="true" />
        Hosted has newer changes - sync now
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
      <CheckCircle2 className="size-3.5 shrink-0 text-done" aria-hidden="true" />
      Synced {relativeTime(status.last_synced_at)}
    </p>
  );
}
