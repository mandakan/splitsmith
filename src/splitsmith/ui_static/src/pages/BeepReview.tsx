/**
 * Beep review queue (/beep-review) -- cross-shooter, grouped by stage (#326).
 *
 * Single home for all per-video beep work (#396). The right-pane detail owns
 * the real waveform picker + a small video preview around the candidate
 * beep, so the user never has to jump to Audit to adjust a beep. Audit's
 * anomaly banner deep-links here via ``?focus=slug::stage::video`` and
 * the queue opens with that item active.
 *
 * Two-pane:
 *
 *   Left -- review list:
 *     - Header card with progress bar (confirmed / pending) + legend
 *     - Stage groups: each lists one row per shooter primary that
 *       still needs confirmation (missing / low confidence / unreviewed)
 *
 *   Right -- detail:
 *     - Compact stage + shooter + camera summary
 *     - Stage-gating note: shot detection is blocked on this stage until
 *       every shooter's beep is confirmed
 *     - Real BeepWaveformPicker fed by per-video peaks + audio
 *     - Small video preview around the detected (or draft) beep
 *     - State-driven action card:
 *         idle    -> Confirm beep
 *         picking -> Apply & confirm (+ discard-shots warning)
 *         empty   -> Pick a beep to continue (disabled)
 *     - Side panels: alternative candidates + keyboard reference
 *
 * Mounted under MatchShell so the per-match sidebar carries over.
 */

import {
  Check,
  ChevronLeft,
  ChevronRight,
  Clock,
  Crosshair,
  Loader2,
  RefreshCw,
  Undo2,
  Volume2,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { BeepWaveformPicker } from "@/components/BeepSection";
import { Kicker } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/useConfirm";
import {
  ApiError,
  api,
  type BeepQueueItem,
  type BeepQueueResponse,
  type RawVideoManifestEntry,
} from "@/lib/api";
import { takeHref, useMatchHref } from "@/lib/matchHref";
import { findTakeForPath, takeFilename } from "@/lib/takes";
import { modKeyGlyph } from "@/lib/platform";
import { cn, useReleaseMediaOnUnmount } from "@/lib/utils";

export function BeepReview() {
  const navigate = useNavigate();
  const href = useMatchHref();
  const [searchParams, setSearchParams] = useSearchParams();
  const confirmDialog = useConfirm();
  const [data, setData] = useState<BeepQueueResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Re-detect runs an async job; surface its progress inline on the
  // Re-detect button. Null pct = running with no reported progress yet.
  const [redetecting, setRedetecting] = useState(false);
  const [redetectPct, setRedetectPct] = useState<number | null>(null);
  // Deep-link from Audit's anomaly banner: ?focus=slug::stage::video.
  // Honored once on first queue load; cleared from the URL afterwards so
  // the focus doesn't keep snapping back as the user works the queue.
  const focusParam = searchParams.get("focus");
  const focusConsumedRef = useRef(false);

  // The sidebar link we just clicked is still the focused element on
  // mount; the focus ring lingers and the next Space press wants to
  // activate it (and would, if our shortcut hook didn't preventDefault).
  // Blur it once so the ring goes away and Space reads like a global
  // shortcut. Audit happens to dodge this because the operator usually
  // clicks the waveform / video before hitting Space, moving focus
  // off the nav -- we just do it explicitly here.
  useEffect(() => {
    const active = document.activeElement;
    if (active instanceof HTMLElement && active !== document.body) {
      active.blur();
    }
  }, []);

  const reload = useCallback(async () => {
    try {
      // Include confirmed items so the operator can reopen an
      // already-confirmed beep to edit or re-detect it (not just work
      // the pending backlog). Confirmed items render collapsed under
      // each stage; the pending workflow below filters them back out.
      const q = await api.getBeepQueue(true);
      setData(q);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Every item, pending + confirmed, in stage/shooter order. ``active``
  // resolves against this so a reopened confirmed item can be selected.
  const flatItems: BeepQueueItem[] = useMemo(
    () => (data?.stages ?? []).flatMap((g) => g.items),
    [data],
  );
  // The pending backlog drives the confirm workflow: auto-select,
  // keyboard next/prev/skip, and "save & continue" advance. Confirmed
  // items are reachable only by clicking them in the collapsed section.
  const pendingItems: BeepQueueItem[] = useMemo(
    () => flatItems.filter((it) => it.status !== "confirmed"),
    [flatItems],
  );

  // Pick the first pending if nothing's selected -- or the deep-link
  // target if ?focus=slug::stage::video matches any item (now that
  // confirmed items are in the queue, a link may land on one).
  useEffect(() => {
    if (activeKey) return;
    if (focusParam && !focusConsumedRef.current) {
      focusConsumedRef.current = true;
      const match = flatItems.find((it) => keyOf(it) === focusParam);
      const next = new URLSearchParams(searchParams);
      next.delete("focus");
      setSearchParams(next, { replace: true });
      if (match) {
        setActiveKey(focusParam);
        return;
      }
      // Item not in the queue (missing or wrong slug). Fall through to
      // the first-pending default and surface a note so the user knows
      // their link didn't land where they aimed.
      setError(
        `Beep ${focusParam} isn't in the queue right now -- it may have been removed.`,
      );
    }
    // Default selection is the first *pending* item. When everything is
    // confirmed we leave nothing selected so the right pane shows the
    // "nothing pending" state instead of a confirmed item.
    if (pendingItems.length > 0) setActiveKey(keyOf(pendingItems[0]));
  }, [flatItems, pendingItems, activeKey, focusParam, searchParams, setSearchParams]);

  const active = activeKey
    ? flatItems.find((it) => keyOf(it) === activeKey) ?? null
    : null;

  // Single confirm path: when ``draftTime`` is provided we first push it
  // through the per-video override endpoint (sets source=manual, fires
  // the trim + shot-detect re-run chain, discarding stale processed
  // state), then mark the queue item reviewed. The detector candidate
  // path (no draft) just marks reviewed, no chain to re-run.
  const confirm = useCallback(
    async (item: BeepQueueItem, draftTime?: number) => {
      setBusy(true);
      try {
        if (draftTime != null) {
          await api.overrideBeepForVideo(
            item.slug,
            item.stage_number,
            item.video_id,
            draftTime,
          );
        }
        const next = await api.confirmBeepInQueue({
          slug: item.slug,
          stage_number: item.stage_number,
          video_id: item.video_id,
          time: draftTime ?? null,
          source: draftTime != null ? "manual" : "detected",
        });
        setData(next);
        // Advance to the next *pending* item after the one just
        // confirmed, in stage/shooter order -- not the global first
        // pending. The old code selected ``updatedFlat[0]`` every time,
        // which yanked the operator back to stage 1 on every save.
        setActiveKey(nextPendingKey(next, keyOf(item)));
      } catch (e) {
        setError(e instanceof ApiError ? e.detail : String(e));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  // Re-detect a beep from scratch. Destructive: it discards the current
  // (possibly confirmed) beep, this stage's trim cache, and any
  // shot-detection audit, then re-runs auto-detection. Gated behind a
  // confirm dialog because the operator may be reaching back into an
  // already-confirmed stage.
  const redetect = useCallback(
    async (item: BeepQueueItem) => {
      const res = await confirmDialog({
        title: "Re-detect this beep?",
        body: (
          <>
            Re-running detection on{" "}
            <b>
              {item.shooter_name} · stage {item.stage_number}
            </b>{" "}
            discards the current beep, this stage&apos;s trim cache, and
            any shot-detection audit, then searches the audio again. You
            will need to review and confirm the new beep.
          </>
        ),
        confirmLabel: "Re-detect",
      });
      if (!res.confirmed) return;
      setBusy(true);
      setRedetecting(true);
      setRedetectPct(null);
      setError(null);
      try {
        const job = await api.detectBeepForVideo(
          item.slug,
          item.stage_number,
          item.video_id,
          true,
        );
        await api.pollJob(job.id, (j) => {
          setRedetectPct(j.progress != null ? Math.round(j.progress * 100) : null);
        });
        const next = await api.getBeepQueue(true);
        setData(next);
        // Keep this item selected so the operator lands on the fresh
        // beep to review it.
        setActiveKey(keyOf(item));
      } catch (e) {
        setError(e instanceof ApiError ? e.detail : String(e));
      } finally {
        setBusy(false);
        setRedetecting(false);
        setRedetectPct(null);
      }
    },
    [confirmDialog],
  );

  const skip = useCallback(() => {
    if (!active || !data) return;
    // Skip = defer this one and move to the next pending in order (same
    // advance rule as save & continue), not the global first item.
    setActiveKey(nextPendingKey(data, keyOf(active)));
  }, [active, data]);

  const prevItem = useCallback(() => {
    if (!active) return;
    const idx = flatItems.findIndex((it) => keyOf(it) === keyOf(active));
    if (idx > 0) setActiveKey(keyOf(flatItems[idx - 1]));
  }, [active, flatItems]);

  const nextItem = useCallback(() => {
    if (!active) return;
    const idx = flatItems.findIndex((it) => keyOf(it) === keyOf(active));
    if (idx >= 0 && idx < flatItems.length - 1) {
      setActiveKey(keyOf(flatItems[idx + 1]));
    }
  }, [active, flatItems]);

  // Keyboard handlers.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (
        e.target instanceof HTMLElement &&
        (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")
      ) {
        return;
      }
      // Enter (confirm) is owned by ActiveDetail's own handler, which
      // knows about the picked draft + confirmed state. Handling it here
      // too would fire a second, draft-less confirm on the same press.
      if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        skip();
      } else if (e.key === "ArrowDown" || e.key === "j" || e.key === "J") {
        e.preventDefault();
        nextItem();
      } else if (e.key === "ArrowUp" || e.key === "k" || e.key === "K") {
        e.preventDefault();
        prevItem();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, busy, confirm, skip, prevItem, nextItem]);

  if (!data) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading beep queue...
      </div>
    );
  }

  return (
    <div className="grid h-[calc(100vh-86px)] grid-cols-[340px_1fr]">
      {/* Left: list */}
      <aside className="overflow-y-auto border-r border-rule bg-surface">
        <div className="border-b border-rule px-5 py-4">
          <Kicker className="mb-2">Beep review</Kicker>
          <div className="mb-1 font-display text-lg font-bold uppercase tracking-tight text-ink">
            Confirm
          </div>
          <p className="mb-3 text-[0.8125rem] text-muted">
            Verify each detected beep before shot detection runs.
          </p>
          <div className="mb-1.5 flex items-center justify-between font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted tabular-nums">
            <span>
              <b className="font-bold text-ink">{data.confirmed_count}</b>{" "}
              confirmed &middot;{" "}
              <b className="font-bold text-ink">{data.pending_count}</b>{" "}
              pending
            </span>
            <span className="text-subtle">
              {data.total_items} total
            </span>
          </div>
          <ProgressBar
            confirmed={data.confirmed_count}
            total={data.total_items}
          />
          <div className="mt-3 flex flex-wrap items-center gap-3 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            <LegendDot tone="confirmed" label="Done" />
            <LegendDot tone="pending" label="Pending" />
            <LegendDot tone="current" label="Current" />
          </div>
        </div>
        {data.stages.length === 0 ? (
          <div className="px-5 py-12 text-center text-sm text-muted">
            All beeps confirmed. Shot detection runs automatically on
            every confirmed stage.
          </div>
        ) : (
          data.stages.map((g) => (
            <StageGroup
              key={g.stage_number}
              group={g}
              activeKey={activeKey}
              onPick={(k) => setActiveKey(k)}
            />
          ))
        )}
      </aside>

      {/* Right: detail */}
      <main className="overflow-y-auto px-7 py-6">
        {active ? (
          <ActiveDetail
            item={active}
            busy={busy}
            onConfirm={(draftTime) =>
              void confirm(active, draftTime ?? undefined)
            }
            onRedetect={() => void redetect(active)}
            redetecting={redetecting}
            redetectPct={redetectPct}
            onSkip={skip}
            onError={setError}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-center text-sm text-muted">
            <div>
              <CheckCircle />
              <p className="mt-3">
                Nothing pending. Shot detection runs automatically on
                every confirmed stage.
              </p>
              <Button
                type="button"
                className="mt-4"
                onClick={() => navigate(href("audit"))}
              >
                <span className="font-display uppercase tracking-[0.08em]">
                  Continue to Audit
                </span>
                <ChevronRight className="size-3.5" />
              </Button>
            </div>
          </div>
        )}
        {error && (
          <div className="mt-4 rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
            {error}
          </div>
        )}
      </main>
    </div>
  );
}

function CheckCircle() {
  return (
    <span
      aria-hidden
      className="mx-auto inline-flex size-14 items-center justify-center rounded-full border border-done/40 bg-done/10 text-done shadow-[0_0_18px_var(--color-done-glow)]"
    >
      <Check className="size-7" strokeWidth={2.5} />
    </span>
  );
}

function keyOf(item: BeepQueueItem): string {
  return `${item.slug}::${item.stage_number}::${item.video_id}`;
}

/** Next item still needing review after ``afterKey``, in stage/shooter
 *  order. Prefers the first pending item *after* the current position;
 *  failing that, wraps to the first pending anywhere (to mop up items
 *  skipped earlier); returns null only when the whole queue is clean.
 *  This is the "save & continue" advance -- it must not snap back to the
 *  first stage while later stages are still pending. */
function nextPendingKey(
  resp: BeepQueueResponse,
  afterKey: string,
): string | null {
  const all = resp.stages.flatMap((g) => g.items);
  const isPending = (it: BeepQueueItem) => it.status !== "confirmed";
  const idx = all.findIndex((it) => keyOf(it) === afterKey);
  for (let i = idx + 1; i < all.length; i++) {
    if (isPending(all[i])) return keyOf(all[i]);
  }
  const firstPending = all.find(isPending);
  return firstPending ? keyOf(firstPending) : null;
}

/* -------------------------------------------------------------------------- */
/* Progress bar + legend                                                      */
/* -------------------------------------------------------------------------- */

function ProgressBar({
  confirmed,
  total,
}: {
  confirmed: number;
  total: number;
}) {
  const pct = total > 0 ? (confirmed / total) * 100 : 0;
  return (
    <div className="h-1 overflow-hidden rounded-full bg-surface-3">
      <span
        className="block h-full rounded-full bg-done shadow-[0_0_6px_var(--color-done-glow)] transition-all"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function LegendDot({
  tone,
  label,
}: {
  tone: "confirmed" | "pending" | "current";
  label: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden
        className={cn(
          "inline-block size-2 rounded-full",
          tone === "confirmed" && "bg-done shadow-[0_0_4px_var(--color-done-glow)]",
          tone === "pending" && "border border-rule-strong bg-transparent",
          tone === "current" && "bg-led shadow-[0_0_4px_var(--color-led-glow)]",
        )}
      />
      {label}
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* Stage group + queue item                                                   */
/* -------------------------------------------------------------------------- */

function StageGroup({
  group,
  activeKey,
  onPick,
}: {
  group: import("@/lib/api").BeepQueueStageGroup;
  activeKey: string | null;
  onPick: (k: string) => void;
}) {
  const pending = group.items.filter((it) => it.status !== "confirmed");
  const confirmed = group.items.filter((it) => it.status === "confirmed");
  // Keep the confirmed section open whenever the selected item lives in
  // it (e.g. the operator just clicked one to edit, or advanced onto it)
  // so it never collapses out from under them.
  const activeIsConfirmedHere = confirmed.some((it) => keyOf(it) === activeKey);
  const [expanded, setExpanded] = useState(false);
  const showConfirmed = expanded || activeIsConfirmedHere;

  // Edge case: a stage with no primary cameras yet -- nothing to review.
  if (group.items.length === 0) {
    return (
      <div className="border-t border-rule px-5 py-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
        Stage {pad2(group.stage_number)} &middot; {group.stage_name} -- no primary cameras
      </div>
    );
  }

  return (
    <div className="border-t border-rule">
      <div className="flex items-center gap-2 bg-surface-2 px-5 py-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
        <span className="inline-flex size-6 items-center justify-center rounded-md border border-rule-strong bg-surface-3 font-bold tabular-nums text-ink-2">
          {pad2(group.stage_number)}
        </span>
        <span className="font-bold text-ink-2">{group.stage_name}</span>
        <span className="ml-auto text-subtle">
          {group.confirmed} of {group.total_primaries} confirmed
        </span>
      </div>
      {pending.map((item) => (
        <ItemRow
          key={keyOf(item)}
          item={item}
          active={activeKey === keyOf(item)}
          onPick={onPick}
        />
      ))}
      {confirmed.length > 0 ? (
        <>
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            aria-expanded={showConfirmed}
            className="flex w-full items-center gap-1.5 border-b border-rule bg-surface px-5 py-1.5 text-left font-mono text-[0.5625rem] font-semibold uppercase tracking-[0.08em] text-subtle hover:bg-surface-2 hover:text-muted"
          >
            <ChevronRight
              className={cn(
                "size-3 transition-transform",
                showConfirmed && "rotate-90",
              )}
              aria-hidden
            />
            {showConfirmed ? "Hide" : "Show"} {confirmed.length} confirmed
          </button>
          {showConfirmed
            ? confirmed.map((item) => (
                <ItemRow
                  key={keyOf(item)}
                  item={item}
                  active={activeKey === keyOf(item)}
                  onPick={onPick}
                />
              ))
            : null}
        </>
      ) : null}
    </div>
  );
}

function ItemRow({
  item,
  active,
  onPick,
}: {
  item: BeepQueueItem;
  active: boolean;
  onPick: (k: string) => void;
}) {
  const k = keyOf(item);
  return (
    <button
      type="button"
      onClick={() => onPick(k)}
      className={cn(
        "grid w-full grid-cols-[28px_1fr_50px_24px] items-center gap-2.5 border-b border-rule px-5 py-2 text-left hover:bg-surface-2",
        active && "border-l-2 border-l-led bg-led/[0.06]",
        item.status === "confirmed" && !active && "opacity-80",
      )}
    >
      <ShooterDot initials={initials(item.shooter_name)} slug={item.slug} />
      <div className="min-w-0">
        <div className="truncate font-display text-[0.8125rem] font-bold uppercase tracking-[0.04em] text-ink">
          {item.shooter_name}
        </div>
        <div className="mt-0.5 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted tabular-nums">
          {item.beep_time != null ? `t ${item.beep_time.toFixed(3)}s` : "no beep"}
        </div>
      </div>
      <span
        className={cn(
          "text-right font-mono text-[0.6875rem] font-semibold tabular-nums",
          item.beep_confidence != null && item.beep_confidence < 0.6
            ? "text-live"
            : "text-ink-2",
        )}
      >
        {item.beep_confidence != null ? item.beep_confidence.toFixed(2) : "--"}
      </span>
      <StatusGlyph status={item.status} active={active} />
    </button>
  );
}

function ShooterDot({ initials, slug }: { initials: string; slug: string }) {
  // Pick a tone like the AvatarStack's palette.
  let hash = 0;
  for (let i = 0; i < slug.length; i++) {
    hash = (hash * 31 + slug.charCodeAt(i)) | 0;
  }
  const tones = [
    "bg-[linear-gradient(135deg,var(--color-led),var(--color-led-deep))]",
    "bg-[linear-gradient(135deg,var(--color-shooter-jl-soft),var(--color-shooter-jl-deep))]",
    "bg-[linear-gradient(135deg,var(--color-shooter-pe-soft),var(--color-shooter-pe-deep))]",
    "bg-[linear-gradient(135deg,var(--color-shooter-rj-soft),var(--color-shooter-rj-deep))]",
  ];
  const cls = tones[Math.abs(hash) % tones.length];
  return (
    <span
      className={cn(
        "inline-flex size-7 items-center justify-center rounded-full font-mono text-[0.6875rem] font-bold text-ink",
        cls,
      )}
    >
      {initials}
    </span>
  );
}

function StatusGlyph({
  status,
  active,
}: {
  status: BeepQueueItem["status"];
  active: boolean;
}) {
  if (active) {
    return (
      <span
        aria-label="Current"
        className="inline-block size-3 rounded-full bg-led shadow-[0_0_8px_var(--color-led-glow)]"
      />
    );
  }
  if (status === "confirmed") {
    return (
      <span
        aria-label="Confirmed"
        className="inline-flex size-3.5 items-center justify-center rounded-full bg-done text-bg"
      >
        <Check className="size-2.5" strokeWidth={3.5} aria-hidden />
      </span>
    );
  }
  if (status === "missing") {
    return (
      <span
        aria-label="Missing"
        className="inline-block size-3 rounded-full border border-led bg-led/10"
      />
    );
  }
  return (
    <span
      aria-label="Pending"
      className="inline-block size-3 rounded-full border border-rule-strong"
    />
  );
}

/* -------------------------------------------------------------------------- */
/* Active detail                                                              */
/* -------------------------------------------------------------------------- */

type DetailMode = "idle" | "picking" | "empty";

function ActiveDetail({
  item,
  busy,
  onConfirm,
  onRedetect,
  redetecting,
  redetectPct,
  onSkip,
  onError,
}: {
  item: BeepQueueItem;
  busy: boolean;
  /** ``draftTime`` is the operator's manually-picked beep time (null
   *  when confirming the detector's candidate as-is). When set, the
   *  parent fires the override + re-trim chain before marking reviewed. */
  onConfirm: (draftTime: number | null) => void;
  /** Re-run auto-detection from scratch (destructive; gated by a confirm
   *  dialog in the parent). Discards the current beep + trim + shots. */
  onRedetect: () => void;
  /** True while this item's re-detect job is in flight. */
  redetecting: boolean;
  /** Re-detect job progress percent, or null when unknown/idle. */
  redetectPct: number | null;
  onSkip: () => void;
  onError: (msg: string | null) => void;
}) {
  // Reset the draft whenever the active item changes. The key forces
  // BeepWaveformPicker to remount so its internal audio element + peaks
  // load against the new video instead of the previous one's state.
  const [draftTime, setDraftTime] = useState<number | null>(null);
  useEffect(() => {
    setDraftTime(null);
  }, [item.slug, item.stage_number, item.video_id]);

  // "Part of take <filename>" link when this primary belongs to a raw
  // recording covering 2+ stages. Resolved from the shooter's raw-video
  // manifest; errors are silent (the link just hides).
  const { matchId } = useParams<{ matchId?: string }>();
  const [takeEntry, setTakeEntry] = useState<RawVideoManifestEntry | null>(null);
  useEffect(() => {
    let alive = true;
    setTakeEntry(null);
    void api
      .getProject(item.slug)
      .then((p) => {
        if (alive) setTakeEntry(findTakeForPath(p.raw_videos, item.video_path));
      })
      .catch(() => {
        /* non-fatal: no take link */
      });
    return () => {
      alive = false;
    };
  }, [item.slug, item.video_path]);
  const takeName = takeEntry != null ? takeFilename(takeEntry) : null;

  // Single source of truth for playback: the right-pane <video>
  // element. The waveform picker reads from this same element instead
  // of creating its own audio (same pattern as the audit canvas
  // MultiCamColumn <video>). One Space press toggles both.
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const mode: DetailMode =
    draftTime != null ? "picking" : item.beep_time == null ? "empty" : "idle";
  // Already-confirmed item reopened for editing. In this state there's
  // nothing to "confirm" -- the operator either edits (pick a new point
  // -> mode flips to "picking") or re-detects. Drives the confirmed
  // heading/chip/note and hides the redundant "Confirm beep" button.
  const confirmedIdle = mode === "idle" && item.beep_reviewed;

  // The preview MP4 centres on whichever time is "live": the draft when
  // the operator has picked one, otherwise the detector's candidate.
  // When there's no beep at all (empty mode) we have nothing to centre
  // on, so the preview is suppressed and replaced with a hint.
  const previewTime = draftTime ?? item.beep_time;
  const detectedSeconds = item.beep_time;
  const delta =
    draftTime != null && detectedSeconds != null
      ? draftTime - detectedSeconds
      : null;

  const handleConfirm = useCallback(() => {
    if (mode === "empty") return;
    // A reopened confirmed beep with no fresh pick has nothing to
    // confirm -- editing means picking a new point (mode -> "picking"),
    // otherwise the operator re-detects or leaves it as-is.
    if (confirmedIdle) return;
    onConfirm(draftTime);
  }, [mode, confirmedIdle, draftTime, onConfirm]);

  // Keyboard:
  //   Enter -> confirm (with or without draft, gated by mode)
  //   U     -> revert draft
  //   S     -> skip
  //   J/K / ↓/↑ are handled at the page level for next/prev item
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (
        e.target instanceof HTMLElement &&
        (e.target.tagName === "INPUT" ||
          e.target.tagName === "TEXTAREA" ||
          e.target.isContentEditable)
      ) {
        return;
      }
      if (e.key === "Enter" && !busy && mode !== "empty" && !confirmedIdle) {
        e.preventDefault();
        handleConfirm();
      } else if ((e.key === "u" || e.key === "U") && draftTime != null) {
        e.preventDefault();
        setDraftTime(null);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, mode, confirmedIdle, draftTime, handleConfirm]);

  const pickerInstructions =
    mode === "empty"
      ? "Click the waveform where the beep should be. Trim + shot detection won't run on this video until a beep is set."
      : "Click the waveform to set a new beep, or confirm the detector's candidate as-is.";

  return (
    <div className="flex max-w-[1280px] flex-col gap-5">
      {/* Detail head */}
      <div className="flex flex-wrap items-center gap-3 border-b border-rule pb-3 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
        <span className="font-bold text-ink-2">
          Stage {pad2(item.stage_number)} &middot; {item.stage_name}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <ShooterDot initials={initials(item.shooter_name)} slug={item.slug} />
          <span className="text-ink-2">{item.shooter_name}</span>
        </span>
        <span className="text-subtle">Primary camera</span>
        {takeName != null && (
          <Link
            to={takeHref(matchId, item.slug, takeName)}
            className="inline-flex items-center gap-1 text-beep underline-offset-2 hover:text-ink-2 hover:underline"
          >
            Part of take {takeName}
          </Link>
        )}
        <span
          className={cn(
            "ml-auto inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[0.625rem] tracking-[0.12em]",
            mode === "picking"
              ? "border-led/60 bg-led/[0.08] text-led"
              : mode === "empty"
                ? "border-live/50 bg-live/[0.08] text-live"
                : confirmedIdle
                  ? "border-done/50 bg-done/[0.08] text-done"
                  : "border-rule-strong bg-surface-2 text-muted",
          )}
        >
          {mode === "picking"
            ? "• unsaved draft"
            : mode === "empty"
              ? "• awaiting input"
              : confirmedIdle
                ? "· confirmed"
                : "· clean"}
        </span>
      </div>

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Kicker className="mb-2">Beep review &middot; current</Kicker>
          <h1 className="mb-2 font-display text-3xl font-bold uppercase leading-none tracking-tight text-ink">
            {mode === "picking"
              ? "Pick the new beep"
              : mode === "empty"
                ? "No beep detected"
                : confirmedIdle
                  ? "Beep confirmed"
                  : "Confirm the beep"}
          </h1>
          <p className="max-w-2xl text-sm text-muted">
            {mode === "picking" && draftTime != null ? (
              <>
                Draft set to{" "}
                <b className="font-mono text-led tabular-nums">
                  {draftTime.toFixed(3)}s
                </b>
                . Applying will discard any kept shots on this stage and
                re-run trim + shot detection on the new beep.
              </>
            ) : mode === "empty" ? (
              <>
                The detector didn&apos;t find a beep on this video. Click
                the waveform where the beep should be -- trim and shot
                detection won&apos;t run on this video until a beep is
                set.
              </>
            ) : confirmedIdle ? (
              <>
                Confirmed beep at{" "}
                <b className="font-mono text-done tabular-nums">
                  {detectedSeconds!.toFixed(3)}s
                </b>
                . Click the waveform to move it, or re-detect to search
                the audio again -- either one discards this stage&apos;s
                trim + shot audit and re-runs the chain.
              </>
            ) : (
              <>
                Detector found{" "}
                {item.status === "low_confidence"
                  ? "a low-confidence"
                  : "a"}{" "}
                candidate at{" "}
                <b className="font-mono text-ink-2 tabular-nums">
                  {detectedSeconds!.toFixed(3)}s
                </b>
                . Verify the marker lands on the beep, or click the
                waveform to pick a different one.
              </>
            )}
          </p>
        </div>
      </div>

      <div className="flex items-start gap-3 rounded-xl border border-live/40 bg-live/[0.08] px-4 py-3 text-[0.8125rem] text-ink-2">
        <Crosshair className="mt-0.5 size-4 shrink-0 text-live" />
        <div>
          <b className="font-bold text-live">
            Stage {pad2(item.stage_number)} is gated on every shooter&apos;s
            beep being confirmed
          </b>{" "}
          before shot detection runs. Confirming the queue clears the
          gate.
        </div>
      </div>

      {/* Real waveform picker + small video preview. The picker remounts
          on item change via its key prop so the audio element resets and
          peaks reload against the new video. */}
      <div className="grid grid-cols-1 items-stretch gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
        <div className="flex flex-col overflow-hidden rounded-2xl border border-rule bg-surface-2 p-3">
          <BeepWaveformPicker
            key={`${item.slug}::${item.stage_number}::${item.video_id}`}
            slug={item.slug}
            stageNumber={item.stage_number}
            videoId={item.video_id}
            videoBeepTime={item.beep_time}
            draftSourceTime={draftTime}
            onPick={(t) => setDraftTime(t)}
            setError={onError}
            snapEnabled={false}
            showFallbackBeepMarker={item.beep_time != null}
            instructions={pickerInstructions}
            ariaLabel={`Beep picker for ${item.shooter_name}, stage ${item.stage_number}`}
            externalMediaRef={videoRef}
            fillHeight
          />
        </div>
        <BeepVideoMini
          key={`${item.slug}::${item.stage_number}::${item.video_id}`}
          slug={item.slug}
          videoPath={item.video_path}
          proxyReady={item.proxy_ready}
          initialTime={previewTime}
          videoRef={videoRef}
          mode={mode}
        />
      </div>

      {/* Action card + alt candidates */}
      <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-[minmax(0,1fr)_420px]">
        <div
          className={cn(
            "overflow-hidden rounded-2xl border bg-surface px-5 py-4",
            mode === "picking"
              ? "border-led-deep"
              : mode === "empty"
                ? "border-live/40"
                : "border-rule-strong",
          )}
        >
          <div className="mb-3 flex flex-wrap items-center gap-4">
            <span
              className={cn(
                "inline-flex size-10 items-center justify-center rounded-full",
                mode === "picking"
                  ? "bg-led/10 text-led"
                  : mode === "empty"
                    ? "bg-live/10 text-live"
                    : "bg-beep/10 text-beep",
              )}
            >
              <Volume2 className="size-4.5" />
            </span>
            <div className="flex flex-1 flex-wrap items-baseline gap-4">
              <div>
                <Kicker>{mode === "picking" ? "Detected" : mode === "empty" ? "Detected" : "Detected beep"}</Kicker>
                <div
                  className={cn(
                    "font-mono text-xl font-bold leading-none tabular-nums",
                    mode === "empty" ? "text-live" : "text-ink",
                  )}
                >
                  {detectedSeconds != null
                    ? `${detectedSeconds.toFixed(3)}s`
                    : "———"}
                </div>
              </div>
              {mode === "picking" && draftTime != null ? (
                <>
                  <span className="font-mono text-base text-subtle">→</span>
                  <div>
                    <Kicker className="text-led">Draft</Kicker>
                    <div className="font-mono text-xl font-bold leading-none text-led tabular-nums">
                      {draftTime.toFixed(3)}s
                    </div>
                  </div>
                </>
              ) : null}
              <div className="ml-auto text-right">
                <Kicker>
                  {mode === "picking" ? "Delta" : mode === "empty" ? "Status" : "Confidence"}
                </Kicker>
                <div
                  className={cn(
                    "font-mono text-[0.8125rem] tabular-nums",
                    mode === "empty" ? "text-live" : "text-ink-2",
                  )}
                >
                  {mode === "picking" && delta != null
                    ? `${delta >= 0 ? "+" : ""}${delta.toFixed(3)}s`
                    : mode === "empty"
                      ? "awaiting input"
                      : item.beep_confidence != null
                        ? `${item.beep_confidence.toFixed(2)} · ${
                            item.beep_confidence >= 0.85
                              ? "high"
                              : item.beep_confidence >= 0.6
                                ? "medium"
                                : "low"
                          }`
                        : "—"}
                </div>
              </div>
            </div>
          </div>

          {mode === "picking" ? (
            <div className="mb-3 flex items-start gap-2.5 rounded-md border border-live/40 bg-live/[0.08] px-3 py-2 text-[0.8125rem] text-ink-2">
              <span
                aria-hidden
                className="mt-px inline-flex size-4 shrink-0 items-center justify-center rounded-full border border-live/60 bg-live/10 font-mono text-[0.625rem] font-bold text-live"
              >
                !
              </span>
              <span>
                Applying will discard any kept shots on this stage and
                re-run trim + shot detection. The queue moves on once
                the chain completes.
              </span>
            </div>
          ) : confirmedIdle ? (
            <div className="mb-3 flex items-start gap-2.5 rounded-md border border-done/40 bg-done/[0.08] px-3 py-2 text-[0.8125rem] text-ink-2">
              <Check
                className="mt-px size-4 shrink-0 text-done"
                strokeWidth={3}
                aria-hidden
              />
              <span>
                This beep is confirmed. Editing (pick a new point) or
                re-detecting discards this stage&apos;s trim cache and any
                shot-detection audit, then re-runs the chain.
              </span>
            </div>
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            {mode === "empty" ? (
              <Button
                type="button"
                disabled
                variant="outline"
                title="Click the waveform to pick a beep"
              >
                <Check className="size-3.5" strokeWidth={3} />
                <span className="font-display uppercase tracking-[0.08em]">
                  Pick a beep to continue
                </span>
              </Button>
            ) : confirmedIdle ? null : (
              <Button
                type="button"
                onClick={handleConfirm}
                disabled={busy}
                className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink"
              >
                <Check className="size-3.5" strokeWidth={3} />
                <span className="font-display uppercase tracking-[0.08em]">
                  {mode === "picking" ? "Apply & confirm" : "Confirm beep"}
                </span>
                <kbd className="ml-1.5 rounded border border-current/40 px-1 font-mono text-[0.625rem]">
                  Enter
                </kbd>
              </Button>
            )}
            {draftTime != null ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => setDraftTime(null)}
                title="Discard the draft and revert to the detector's candidate"
              >
                <Undo2 className="size-3.5" />
                <span className="font-display uppercase tracking-[0.08em]">
                  Revert draft
                </span>
                <kbd className="ml-1.5 rounded border border-current/40 px-1 font-mono text-[0.625rem]">
                  U
                </kbd>
              </Button>
            ) : null}
            {item.beep_time != null ? (
              <Button
                type="button"
                variant="outline"
                onClick={onRedetect}
                disabled={busy}
                title="Discard this beep and re-run auto-detection from scratch"
              >
                {redetecting ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : (
                  <RefreshCw className="size-3.5" />
                )}
                <span className="font-display uppercase tracking-[0.08em] tabular-nums">
                  {redetecting
                    ? `Re-detecting${redetectPct != null ? ` ${redetectPct}%` : "..."}`
                    : "Re-detect"}
                </span>
              </Button>
            ) : null}
            <Button type="button" variant="ghost" onClick={onSkip}>
              Skip
              <kbd className="ml-1.5 rounded border border-current/40 px-1 font-mono text-[0.625rem]">
                S
              </kbd>
            </Button>
          </div>
          <p className="mt-3 text-[0.75rem] text-muted">
            Skipping leaves this beep pending. You can come back to it;
            shot detection won&apos;t start for this stage until all
            beeps are confirmed.
          </p>
        </div>

        {/* Side panels */}
        <div className="flex flex-col gap-3">
          <SidePanel title="Alternative beeps">
            {item.alt_candidates.length === 0 ? (
              <div className="px-3 py-3 text-xs text-subtle">
                No alternatives ranked.
              </div>
            ) : (
              item.alt_candidates.map((alt, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between gap-3 border-t border-rule px-3 py-2 first:border-t-0"
                >
                  <div>
                    <div className="font-mono text-[0.8125rem] font-semibold tabular-nums text-ink-2">
                      {alt.time.toFixed(3)}s
                    </div>
                    <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                      conf{" "}
                      {alt.confidence != null ? alt.confidence.toFixed(2) : "--"}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDraftTime(alt.time)}
                    className="rounded-md border border-rule bg-surface-3 px-3 py-1 font-display text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-ink-2 hover:border-led hover:bg-led/10 hover:text-led"
                  >
                    Use this
                  </button>
                </div>
              ))
            )}
          </SidePanel>
          <SidePanel title="Keyboard">
            <div className="flex flex-col gap-1 px-3 py-2 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
              <KbdRow
                what={mode === "picking" ? "Apply" : mode === "empty" ? "Pick beep" : "Confirm"}
                keys={mode === "empty" ? ["click waveform"] : ["Enter"]}
              />
              <KbdRow what="Revert draft" keys={["U"]} />
              <KbdRow what="Skip" keys={["S"]} />
              <KbdRow what="Next" keys={["↓", "J"]} />
              <KbdRow what="Prev" keys={["↑", "K"]} />
              <KbdRow
                what="Zoom in / fit / out"
                keys={[`${modKeyGlyph()}1`, `${modKeyGlyph()}2`, `${modKeyGlyph()}3`]}
              />
            </div>
          </SidePanel>
        </div>
      </div>
    </div>
  );
}

function BeepVideoMini({
  slug,
  videoPath,
  proxyReady,
  initialTime,
  videoRef,
  mode,
}: {
  slug: string;
  /** Per-stage video path. Serve the full source MP4 (same one the
   *  audit canvas streams) so the timeline matches the picker's
   *  waveform 1:1. */
  videoPath: string;
  /** False while the low-res proxy is still being generated. Drives the
   *  "preview generating" placeholder so we never mount a player that
   *  the server would answer with 425. */
  proxyReady: boolean;
  /** Time to park the playhead at when the clip first loads (detector
   *  beep, or the draft if the operator has picked one). */
  initialTime: number | null;
  /** Owned by ActiveDetail -- the single source of truth for
   *  playback. The picker reads scrub + time off this element. */
  videoRef: { current: HTMLVideoElement | null };
  mode: DetailMode;
}) {
  const localRef = useRef<HTMLVideoElement | null>(null);
  useReleaseMediaOnUnmount(localRef);
  const [videoError, setVideoError] = useState(false);
  // Reset error when the source video changes.
  useEffect(() => { setVideoError(false); }, [videoPath]);
  const label =
    mode === "picking"
      ? "ON DRAFT BEEP"
      : mode === "empty"
        ? "NO ANCHOR"
        : "ON DETECTED BEEP";

  // Park the video at ``initialTime`` once metadata lands.
  useEffect(() => {
    const v = localRef.current;
    if (!v || initialTime == null) return;
    const seek = () => {
      try {
        v.currentTime = initialTime;
      } catch {
        /* metadata not loaded yet -- loadedmetadata listener handles it */
      }
    };
    if (v.readyState >= 1 /* HAVE_METADATA */) seek();
    else v.addEventListener("loadedmetadata", seek, { once: true });
    return () => v.removeEventListener("loadedmetadata", seek);
  }, [initialTime]);

  return (
    <div className="relative overflow-hidden rounded-2xl border border-rule bg-surface-2">
      <div className="flex items-center justify-between border-b border-rule px-3 py-2">
        <Kicker>Preview &middot; primary cam</Kicker>
        <span
          className={cn(
            "font-mono text-[0.625rem] uppercase tracking-[0.12em]",
            mode === "picking"
              ? "text-led"
              : mode === "empty"
                ? "text-live"
                : "text-beep",
          )}
        >
          {label}
        </span>
      </div>
      {/* kind="proxy": beep review plays the low-res proxy, never the trim.
          The proxy is untrimmed and shares the source timeline origin, so
          the playhead stays in sync with the waveform (which is always the
          full-source WAV). kind="auto" would serve the cached audit trim
          once detect-beep has run, drifting the playhead.
          When the proxy is still being generated (proxyReady === false) the
          server would answer the stream with 425, so we render an explicit
          placeholder instead of mounting a player that cannot load. */}
      {!proxyReady ? (
        <div
          role="status"
          className="flex aspect-video w-full flex-col items-center justify-center gap-2 bg-black p-4 text-center text-sm text-white/70"
        >
          <Clock className="h-5 w-5 text-white/40" aria-hidden="true" />
          <span>Preview generating</span>
          <span className="text-xs text-white/40">Check back shortly</span>
        </div>
      ) : videoError ? (
        <div
          role="alert"
          className="flex aspect-video w-full flex-col items-center justify-center gap-2 bg-black p-4 text-center text-sm text-white/70"
        >
          <span>Preview unavailable</span>
          <button
            type="button"
            className="rounded border border-white/20 px-3 py-1 text-xs text-white/60 hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
            onClick={() => setVideoError(false)}
          >
            Retry
          </button>
        </div>
      ) : (
        <video
          ref={(el) => {
            localRef.current = el;
            videoRef.current = el;
          }}
          src={api.videoStreamUrl(slug, videoPath, "proxy")}
          playsInline
          controls
          preload="metadata"
          className="aspect-video w-full bg-black object-cover"
          aria-label="Primary cam, proxy - the playback master for both this pane and the waveform"
          title="Space toggles play/pause"
          onError={() => setVideoError(true)}
        />
      )}
    </div>
  );
}

function SidePanel({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-rule-strong bg-surface">
      <div className="border-b border-rule px-3 py-2 font-display text-[0.6875rem] font-bold uppercase tracking-[0.1em] text-ink">
        {title}
      </div>
      {children}
    </div>
  );
}

function KbdRow({ what, keys }: { what: string; keys: string[] }) {
  return (
    <div className="flex items-center justify-between">
      <span>{what}</span>
      <span className="inline-flex gap-1">
        {keys.map((k) => (
          <kbd
            key={k}
            className="rounded border border-rule-strong bg-surface-2 px-1.5 py-px font-mono text-[0.625rem] font-semibold text-ink-2"
          >
            {k}
          </kbd>
        ))}
      </span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 0 || !parts[0]) return "??";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

// Make ChevronLeft available for callers if exported in the future.
void ChevronLeft;
