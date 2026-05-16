/**
 * Beep review queue (/beep-review) -- cross-shooter, grouped by stage (#326).
 *
 * Two-pane per polished/06:
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
 *     - Mini oscilloscope waveform around the detected beep (SVG)
 *     - Detected beep card with Confirm / Adjust / Skip actions
 *     - Side panels: alternative candidates + keyboard reference
 *
 * Mounted under MatchShell so the per-match sidebar carries over.
 */

import {
  Check,
  ChevronLeft,
  ChevronRight,
  Crosshair,
  Loader2,
  Volume2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Kicker } from "@/components/ui";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  api,
  type BeepQueueItem,
  type BeepQueueResponse,
} from "@/lib/api";
import { cn } from "@/lib/utils";

export function BeepReview() {
  const navigate = useNavigate();
  const [data, setData] = useState<BeepQueueResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const q = await api.getBeepQueue();
      setData(q);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const flatItems: BeepQueueItem[] = useMemo(
    () => (data?.stages ?? []).flatMap((g) => g.items),
    [data],
  );

  // Pick the first pending if nothing's selected.
  useEffect(() => {
    if (!activeKey && flatItems.length > 0) {
      setActiveKey(keyOf(flatItems[0]));
    }
  }, [flatItems, activeKey]);

  const active = activeKey
    ? flatItems.find((it) => keyOf(it) === activeKey) ?? null
    : null;

  const confirm = useCallback(
    async (item: BeepQueueItem, alt?: number) => {
      setBusy(true);
      try {
        const next = await api.confirmBeepInQueue({
          slug: item.slug,
          stage_number: item.stage_number,
          video_id: item.video_id,
          time: alt ?? null,
          source: alt != null ? "manual" : "detected",
        });
        setData(next);
        // Move to next pending item in the same stage if any, else next overall.
        const updatedFlat = next.stages.flatMap((g) => g.items);
        const nextItem = updatedFlat[0];
        setActiveKey(nextItem ? keyOf(nextItem) : null);
      } catch (e) {
        setError(e instanceof ApiError ? e.detail : String(e));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const skip = useCallback(() => {
    if (!active) return;
    const idx = flatItems.findIndex((it) => keyOf(it) === keyOf(active));
    const next = flatItems[idx + 1] ?? flatItems[0] ?? null;
    setActiveKey(next ? keyOf(next) : null);
  }, [active, flatItems]);

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
      if (e.key === "Enter" && active && !busy) {
        e.preventDefault();
        void confirm(active);
      } else if (e.key === "s" || e.key === "S") {
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
            All beeps confirmed. Shot detection can run on every stage.
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
            onConfirm={() => void confirm(active)}
            onConfirmAlt={(t) => void confirm(active, t)}
            onSkip={skip}
            onOpenAudit={() =>
              navigate(`/audit/${active.slug}/${active.stage_number}`)
            }
          />
        ) : (
          <div className="flex h-full items-center justify-center text-center text-sm text-muted">
            <div>
              <CheckCircle />
              <p className="mt-3">
                Nothing pending. Shot detection can run on every stage.
              </p>
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
  if (group.items.length === 0 && group.total_primaries === group.confirmed) {
    return (
      <div className="border-t border-rule px-5 py-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
        Stage {pad2(group.stage_number)} &middot; {group.stage_name} -- {group.confirmed} of {group.total_primaries} confirmed
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
      {group.items.map((item) => {
        const k = keyOf(item);
        return (
          <button
            key={k}
            type="button"
            onClick={() => onPick(k)}
            className={cn(
              "grid w-full grid-cols-[28px_1fr_50px_24px] items-center gap-2.5 border-b border-rule px-5 py-2 text-left hover:bg-surface-2",
              activeKey === k && "border-l-2 border-l-led bg-led/[0.06]",
            )}
          >
            <ShooterDot initials={initials(item.shooter_name)} slug={item.slug} />
            <div className="min-w-0">
              <div className="truncate font-display text-[0.8125rem] font-bold uppercase tracking-[0.04em] text-ink">
                {item.shooter_name}
              </div>
              <div className="mt-0.5 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted tabular-nums">
                {item.beep_time != null
                  ? `t ${item.beep_time.toFixed(3)}s`
                  : "no beep"}
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
              {item.beep_confidence != null
                ? item.beep_confidence.toFixed(2)
                : "--"}
            </span>
            <StatusGlyph
              status={item.status}
              active={activeKey === k}
            />
          </button>
        );
      })}
    </div>
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

function ActiveDetail({
  item,
  busy,
  onConfirm,
  onConfirmAlt,
  onSkip,
  onOpenAudit,
}: {
  item: BeepQueueItem;
  busy: boolean;
  onConfirm: () => void;
  onConfirmAlt: (t: number) => void;
  onSkip: () => void;
  onOpenAudit: () => void;
}) {
  return (
    <div className="flex max-w-[1100px] flex-col gap-5">
      {/* Detail head */}
      <div className="flex flex-wrap items-center gap-3 border-b border-rule pb-3 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
        <span className="font-bold text-ink-2">
          Stage {pad2(item.stage_number)} &middot; {item.stage_name}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <ShooterDot initials={initials(item.shooter_name)} slug={item.slug} />
          <span className="text-ink-2">{item.shooter_name}</span>
        </span>
        <span className="text-subtle">Camera A &middot; primary</span>
        <button
          type="button"
          onClick={onOpenAudit}
          className="ml-auto inline-flex items-center gap-1.5 font-display text-[0.625rem] font-semibold uppercase tracking-[0.1em] text-led hover:text-led-soft"
        >
          Open in audit <ChevronRight className="size-3" />
        </button>
      </div>

      <div>
        <Kicker className="mb-2">Beep review &middot; current</Kicker>
        <h1 className="mb-2 font-display text-3xl font-bold uppercase leading-none tracking-tight text-ink">
          Confirm the beep
        </h1>
        <p className="max-w-xl text-sm text-muted">
          {item.beep_time != null
            ? `Detector found ${item.status === "low_confidence" ? "a low-confidence" : "a"} candidate at ${item.beep_time.toFixed(3)}s. Verify the marker lands on the beep before shot detection runs.`
            : "No beep was detected on this video. Open the audit page to set it manually."}
        </p>
      </div>

      <div className="flex items-start gap-3 rounded-xl border border-live/40 bg-live/[0.08] px-4 py-3 text-[0.8125rem] text-ink-2">
        <Crosshair className="mt-0.5 size-4 shrink-0 text-live" />
        <div>
          <b className="font-bold text-live">
            Stage {pad2(item.stage_number)} is gated on every shooter's
            beep being confirmed
          </b>{" "}
          before shot detection runs. Confirming the queue clears the
          gate.
        </div>
      </div>

      {/* Mini oscilloscope */}
      {item.beep_time != null && <MiniScope item={item} />}

      {/* Two-col bottom */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_320px]">
        {/* Primary card */}
        <div className="overflow-hidden rounded-2xl border border-rule-strong bg-surface px-5 py-4">
          <div className="mb-3 flex items-center gap-3">
            <span className="inline-flex size-11 items-center justify-center rounded-full bg-beep/10 text-beep">
              <Volume2 className="size-5" />
            </span>
            <div>
              <div className="font-mono text-[0.625rem] uppercase tracking-[0.08em] text-muted">
                Detected beep
              </div>
              <div className="font-mono text-2xl font-bold tabular-nums text-ink">
                {item.beep_time != null ? `${item.beep_time.toFixed(3)}s` : "--"}
              </div>
              <div className="mt-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted tabular-nums">
                {item.beep_confidence != null
                  ? `${item.beep_confidence.toFixed(2)} confidence · ${
                      item.beep_confidence >= 0.85
                        ? "high"
                        : item.beep_confidence >= 0.6
                          ? "medium"
                          : "low"
                    }`
                  : item.status === "missing"
                    ? "missing"
                    : "no detector confidence"}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              onClick={onConfirm}
              disabled={busy || item.beep_time == null}
              className="bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink"
            >
              <Check className="size-3.5" strokeWidth={3} />
              <span className="font-display uppercase tracking-[0.08em]">
                Confirm beep
              </span>
              <kbd className="ml-1.5 rounded border border-current/40 px-1 font-mono text-[0.625rem]">
                Enter
              </kbd>
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={onOpenAudit}
              title="Open this stage in audit to fine-tune"
            >
              <span className="font-display uppercase tracking-[0.08em]">
                Adjust in audit
              </span>
              <kbd className="ml-1.5 rounded border border-current/40 px-1 font-mono text-[0.625rem]">
                A
              </kbd>
            </Button>
            <Button type="button" variant="ghost" onClick={onSkip}>
              Skip
              <kbd className="ml-1.5 rounded border border-current/40 px-1 font-mono text-[0.625rem]">
                S
              </kbd>
            </Button>
          </div>
          <p className="mt-3 text-[0.75rem] text-muted">
            Skipping leaves this beep pending. You can come back to it;
            shot detection will not start for this stage until all beeps
            are confirmed.
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
                    onClick={() => onConfirmAlt(alt.time)}
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
              <KbdRow what="Confirm" keys={["Enter"]} />
              <KbdRow what="Skip" keys={["S"]} />
              <KbdRow what="Next" keys={["↓", "J"]} />
              <KbdRow what="Prev" keys={["↑", "K"]} />
            </div>
          </SidePanel>
        </div>
      </div>
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
/* Mini oscilloscope                                                          */
/* -------------------------------------------------------------------------- */

function MiniScope({ item: _item }: { item: BeepQueueItem }) {
  // We don't have audio peaks here; render the polished design's
  // schematic envelope around the beep. The mini scope is informational
  // -- "Adjust in audit" jumps the user to the full waveform.
  const W = 1000;
  const H = 110;
  const beepX = W * 0.475;
  return (
    <div className="overflow-hidden rounded-xl border border-rule bg-bg-glow">
      <div className="flex items-center justify-between border-b border-rule px-4 py-2 text-xs">
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          Waveform · near detected beep
        </span>
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
          schematic preview
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="block w-full" preserveAspectRatio="none">
        <defs>
          <linearGradient id="env-beep" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-beep)" stopOpacity="0.35" />
            <stop offset="100%" stopColor="var(--color-beep)" stopOpacity="0.05" />
          </linearGradient>
        </defs>
        <line
          x1="0"
          y1={H / 2}
          x2={W}
          y2={H / 2}
          stroke="var(--color-rule-strong)"
          strokeWidth={0.5}
          strokeDasharray="2 3"
        />
        <path
          d={`M0 ${H / 2} L${beepX - 30} ${H / 2 - 1} L${beepX - 10} 30 L${beepX} 70 L${beepX + 5} 22 L${beepX + 10} 78 L${beepX + 15} 28 L${beepX + 25} ${H / 2 + 1} L${W} ${H / 2}`}
          fill="url(#env-beep)"
          stroke="var(--color-beep)"
          strokeWidth={0.7}
        />
        <line
          x1={beepX}
          y1={0}
          x2={beepX}
          y2={H}
          stroke="var(--color-beep)"
          strokeWidth={2.5}
          strokeDasharray="6 3"
        />
        <rect x={beepX - 30} y={2} width={60} height={16} rx={4} fill="var(--color-beep)" />
        <text
          x={beepX}
          y={14}
          textAnchor="middle"
          fill="var(--color-bg)"
          fontFamily="JetBrains Mono"
          fontSize={9}
          fontWeight={700}
        >
          BEEP
        </text>
      </svg>
      <div className="grid grid-cols-7 gap-0 px-4 py-1.5 font-mono text-[0.5625rem] uppercase tracking-[0.06em] text-subtle">
        {["-1.5s", "-1.0s", "-0.5s", "BEEP", "+0.5s", "+1.0s", "+1.5s"].map(
          (l, i) => (
            <span
              key={l}
              className={cn(
                "text-center",
                i === 3 && "font-bold text-beep",
              )}
            >
              {l}
            </span>
          ),
        )}
      </div>
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
