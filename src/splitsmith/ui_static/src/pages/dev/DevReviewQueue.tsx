/**
 * Developer / Review queue (#331).
 *
 * Three-column layout: dev sidebar (rendered by DeveloperShell), a
 * persistent queue list, and a focused detail panel. The detail panel
 * routes the user into /dev/legacy/review to do the actual edit -- the
 * fixture-edit primitive lives there and we don't fork it during the
 * redesign. The queue list itself is fully redesigned per polished/10.
 */

import {
  CheckCircle2,
  Circle,
  ExternalLink,
  Inbox,
  Keyboard,
  ListChecks,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { api, type DevReviewQueueItem, type DevReviewQueueResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

export function DevReviewQueue() {
  const [queue, setQueue] = useState<DevReviewQueueResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchParams, setSearchParams] = useSearchParams();
  const activeSlug = searchParams.get("slug");

  useEffect(() => {
    let alive = true;
    api
      .getDevReviewQueue()
      .then((q) => {
        if (!alive) return;
        setQueue(q);
        setLoading(false);
      })
      .catch(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const allItems = useMemo(() => {
    if (!queue) return [] as DevReviewQueueItem[];
    return [...queue.pending, ...queue.flagged, ...queue.done];
  }, [queue]);

  const activeItem = useMemo(() => {
    if (!queue) return null;
    if (activeSlug) {
      return allItems.find((it) => it.slug === activeSlug) ?? queue.pending[0] ?? null;
    }
    return queue.pending[0] ?? null;
  }, [queue, activeSlug, allItems]);

  function selectItem(slug: string) {
    setSearchParams({ slug }, { replace: true });
  }

  const totalDone = queue?.done.length ?? 0;
  const totalPending = queue?.pending.length ?? 0;
  const totalFlagged = queue?.flagged.length ?? 0;
  const total = totalDone + totalPending + totalFlagged;

  return (
    <div className="grid h-[calc(100vh-86px)] grid-cols-[320px_1fr]">
      <QueueList
        loading={loading}
        queue={queue}
        activeSlug={activeItem?.slug ?? null}
        totalDone={totalDone}
        total={total}
        onSelect={selectItem}
      />
      <DetailPane item={activeItem} loading={loading} />
    </div>
  );
}

function QueueList({
  loading,
  queue,
  activeSlug,
  totalDone,
  total,
  onSelect,
}: {
  loading: boolean;
  queue: DevReviewQueueResponse | null;
  activeSlug: string | null;
  totalDone: number;
  total: number;
  onSelect: (slug: string) => void;
}) {
  const donePct = total === 0 ? 0 : (totalDone / total) * 100;
  return (
    <aside className="sticky top-0 flex h-[calc(100vh-86px)] flex-col overflow-y-auto border-r border-rule bg-surface">
      <header className="border-b border-rule p-4">
        <div className="mb-1 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.18em] text-beep">
          Step 02 / Review queue
        </div>
        <div className="font-display text-[1.25rem] font-bold uppercase leading-tight tracking-tight text-ink">
          Confirm promotions
        </div>
        <div className="mt-3 flex items-center justify-between font-mono text-[0.6875rem] tabular-nums text-muted">
          <span>
            <b className="text-ink">{totalDone}</b> / {total} cleared
          </span>
          <span>
            {total - totalDone > 0
              ? `${total - totalDone} awaiting`
              : "queue empty"}
          </span>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3">
          <div className="h-full bg-done" style={{ width: `${donePct}%` }} />
        </div>
      </header>

      {loading ? (
        <div className="px-4 py-10 text-center text-[0.875rem] text-muted">Loading queue...</div>
      ) : !queue ? (
        <div className="px-4 py-10 text-center text-[0.875rem] text-led">
          Failed to load queue.
        </div>
      ) : (
        <>
          <QueueSection
            label="Pending"
            count={queue.pending.length}
            tone="live"
            items={queue.pending}
            activeSlug={activeSlug}
            onSelect={onSelect}
          />
          {queue.flagged.length > 0 && (
            <QueueSection
              label="Flagged"
              count={queue.flagged.length}
              tone="led"
              items={queue.flagged}
              activeSlug={activeSlug}
              onSelect={onSelect}
            />
          )}
          <QueueSection
            label="Cleared"
            count={queue.done.length}
            tone="done"
            items={queue.done.slice(0, 30)}
            activeSlug={activeSlug}
            onSelect={onSelect}
            muted
          />
        </>
      )}
    </aside>
  );
}

function QueueSection({
  label,
  count,
  tone,
  items,
  activeSlug,
  onSelect,
  muted,
}: {
  label: string;
  count: number;
  tone: "live" | "led" | "done";
  items: DevReviewQueueItem[];
  activeSlug: string | null;
  onSelect: (slug: string) => void;
  muted?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <section>
      <header className="flex items-center justify-between border-y border-rule bg-surface-2 px-4 py-2 font-mono text-[0.625rem] font-bold uppercase tracking-[0.18em] text-subtle">
        <span className="flex items-center gap-2">
          <span
            className={cn(
              "size-1.5 rounded-full",
              tone === "live" && "bg-live",
              tone === "led" && "bg-led",
              tone === "done" && "bg-done",
            )}
          />
          {label}
        </span>
        <span className="rounded bg-surface-3 px-1.5 py-0.5 tabular-nums text-ink-2">{count}</span>
      </header>
      <ul className={cn("divide-y divide-rule", muted && "opacity-70")}>
        {items.map((it) => (
          <QueueItem
            key={it.slug}
            item={it}
            active={it.slug === activeSlug}
            onSelect={() => onSelect(it.slug)}
          />
        ))}
      </ul>
    </section>
  );
}

function QueueItem({
  item,
  active,
  onSelect,
}: {
  item: DevReviewQueueItem;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        className={cn(
          "grid w-full grid-cols-[28px_1fr_28px] items-center gap-3 px-4 py-2.5 text-left transition-colors",
          active
            ? "bg-[color:var(--color-beep-tint)] shadow-[inset_2px_0_0_var(--color-beep)]"
            : "hover:bg-surface-2",
        )}
      >
        <span
          className={cn(
            "inline-flex size-7 items-center justify-center rounded",
            item.source === "match" && "bg-[color:var(--color-led-tint)] text-led",
            item.source === "github" && "bg-[color:var(--color-manual-tint)] text-manual",
            item.source === "ad-hoc" && "bg-surface-3 text-muted",
          )}
        >
          <Inbox className="size-3.5" />
        </span>
        <div className="min-w-0">
          <div className="truncate font-mono text-[0.8125rem] font-bold text-ink">
            {item.slug}
          </div>
          <div className="flex items-center gap-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
            <span>{item.source_label}</span>
            <span className="text-whisper">/</span>
            <span>{item.n_shots} shots</span>
          </div>
        </div>
        {item.status === "done" ? (
          <CheckCircle2 className="size-4 text-done" />
        ) : item.status === "flagged" ? (
          <span className="inline-flex size-5 items-center justify-center rounded-full bg-led text-bg font-mono text-[0.625rem] font-bold">
            !
          </span>
        ) : (
          <Circle className="size-4 text-live" />
        )}
      </button>
    </li>
  );
}

function DetailPane({ item, loading }: { item: DevReviewQueueItem | null; loading: boolean }) {
  if (loading) {
    return (
      <main className="flex h-full items-center justify-center text-[0.875rem] text-muted">
        Loading...
      </main>
    );
  }
  if (!item) {
    return (
      <main className="flex h-full flex-col items-center justify-center gap-3 px-7 py-7 text-center">
        <ListChecks className="size-12 text-subtle" />
        <h2 className="font-display text-[1.25rem] font-bold uppercase tracking-tight text-ink">
          Inbox zero
        </h2>
        <p className="max-w-md text-[0.875rem] text-muted">
          Nothing to confirm right now. Promotions land here automatically when you keep a
          shot inside Audit, and GitHub-submitted fixtures arrive after CI passes.
        </p>
      </main>
    );
  }

  const reviewUrl = `/review?fixture=${encodeURIComponent(item.audit_path)}`;

  return (
    <main className="flex h-full flex-col overflow-y-auto">
      <header className="flex items-start justify-between gap-4 border-b border-rule px-7 py-6">
        <div>
          <div className="mb-2 flex items-center gap-2 font-mono text-[0.6875rem] uppercase tracking-[0.06em]">
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 font-bold",
                item.source === "match"
                  ? "bg-[color:var(--color-led-tint)] text-led"
                  : item.source === "github"
                    ? "bg-[color:var(--color-manual-tint)] text-manual"
                    : "bg-surface-3 text-muted",
              )}
            >
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  item.source === "match" && "bg-led",
                  item.source === "github" && "bg-manual",
                  item.source === "ad-hoc" && "bg-muted",
                )}
              />
              {item.source_label}
            </span>
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-2 py-0.5",
                item.status === "pending" && "bg-[color:var(--color-live-tint)] text-live",
                item.status === "flagged" && "bg-[color:var(--color-led-tint)] text-led",
                item.status === "done" && "bg-[color:var(--color-done-tint)] text-done",
              )}
            >
              {item.status === "pending" ? "Pending confirm" : item.status}
            </span>
          </div>
          <h1 className="font-mono text-[1.25rem] font-bold tracking-tight text-ink">
            {item.slug}
          </h1>
          <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-1 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
            <span>
              Venue <b className="text-ink-2">{item.venue ?? "--"}</b>
            </span>
            <span>
              Stage <b className="text-ink-2">{item.stage_number ?? "?"}</b>
            </span>
            <span>
              Shooter <b className="text-ink-2">{item.shooter ?? "?"}</b>
            </span>
            <span>
              Shots <b className="text-ink-2">{item.n_shots}</b>
            </span>
          </div>
        </div>
      </header>

      {/* Action toolbar */}
      <div className="flex items-center gap-3 border-b border-rule bg-bg-glow px-7 py-3">
        <a
          href={reviewUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-2 rounded-md bg-done px-4 py-2 font-mono text-[0.75rem] font-bold uppercase tracking-[0.08em] text-bg shadow-[0_0_12px_var(--color-done-glow)] transition-colors hover:bg-[color:var(--color-done)]"
        >
          Open in editor
          <ExternalLink className="size-3.5" />
          <kbd className="ml-1 rounded border border-bg/30 bg-bg/20 px-1 py-0.5 font-mono text-[0.625rem]">
            O
          </kbd>
        </a>
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-md border border-rule bg-surface px-3 py-2 font-mono text-[0.75rem] font-bold uppercase tracking-[0.08em] text-ink-2 transition-colors hover:bg-surface-2"
          disabled
          title="Not yet wired"
        >
          Approve to corpus
          <kbd className="ml-1 rounded border border-rule bg-bg/40 px-1 py-0.5 font-mono text-[0.625rem]">
            A
          </kbd>
        </button>
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-md border border-[rgba(255,45,45,0.4)] bg-[color:var(--color-led-tint)] px-3 py-2 font-mono text-[0.75rem] font-bold uppercase tracking-[0.08em] text-led transition-colors hover:bg-[rgba(255,45,45,0.2)]"
          disabled
          title="Not yet wired"
        >
          Reject &middot; send back
          <kbd className="ml-1 rounded border border-led/40 bg-led/20 px-1 py-0.5 font-mono text-[0.625rem]">
            R
          </kbd>
        </button>
        <div className="flex-1" />
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-md px-2 py-1 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted transition-colors hover:text-ink"
        >
          Skip
          <kbd className="rounded border border-rule bg-surface-2 px-1 py-0.5 font-mono text-[0.625rem]">
            J
          </kbd>
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 px-7 py-6">
        <div className="grid grid-cols-[1.5fr_1fr] gap-5">
          <section className="rounded-md border border-rule bg-surface p-5">
            <header className="mb-3 flex items-center justify-between">
              <h2 className="font-display text-[0.9375rem] font-bold uppercase tracking-tight text-ink">
                Diff snapshot
              </h2>
              <span className="font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
                Open in editor for full waveform
              </span>
            </header>
            <p className="text-[0.8125rem] leading-relaxed text-muted">
              This queue is intentionally light. Confirming a promotion needs the full
              waveform + per-shot diff which the editor at{" "}
              <code className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[0.75rem] text-ink">
                /review
              </code>{" "}
              already provides. The "Approve to corpus" / "Reject" buttons above are
              placeholders for a future inline-confirm flow once the queue persists state on
              the backend.
            </p>
            <div className="mt-4 rounded border border-rule bg-bg-glow px-4 py-3">
              <div className="font-mono text-[0.625rem] uppercase tracking-[0.18em] text-subtle">
                Fixture path
              </div>
              <code className="font-mono text-[0.75rem] text-ink-2">{item.audit_path}</code>
            </div>
          </section>

          <section className="rounded-md border border-rule bg-surface p-5">
            <header className="mb-3 flex items-center gap-2">
              <Keyboard className="size-4 text-muted" />
              <h2 className="font-display text-[0.875rem] font-bold uppercase tracking-tight text-ink">
                Keyboard
              </h2>
            </header>
            <dl className="grid grid-cols-[28px_1fr] gap-y-2 font-mono text-[0.75rem]">
              <dt>
                <kbd className="rounded border border-rule bg-surface-2 px-1.5 py-0.5 text-[0.625rem]">
                  O
                </kbd>
              </dt>
              <dd className="text-ink-2">Open in editor</dd>
              <dt>
                <kbd className="rounded border border-rule bg-surface-2 px-1.5 py-0.5 text-[0.625rem]">
                  J
                </kbd>
              </dt>
              <dd className="text-ink-2">Next item</dd>
              <dt>
                <kbd className="rounded border border-rule bg-surface-2 px-1.5 py-0.5 text-[0.625rem]">
                  K
                </kbd>
              </dt>
              <dd className="text-ink-2">Previous item</dd>
            </dl>
          </section>
        </div>
      </div>
    </main>
  );
}
