/**
 * Compare route (/compare/:stage) -- multi-shooter sync timeline (#328).
 *
 * Replaces the CLI-only ``splitsmith compare export`` for in-app
 * browsing. Layout per polished/07:
 *
 *   - Compact header carried from /audit, with Audit / Compare / Coach
 *     tab strip (Compare is the active tab here)
 *   - Visibility chips: one per shooter with avatar + name; the audio-
 *     source chip carries the LED ring + "AUDIO" badge
 *   - Layout toggle: 2x2 / 1x4 / Stack
 *   - Multi-video grid: each shooter's lossless trim, beep-aligned
 *   - Shared transport with master scrub bar (time-since-beep)
 *   - F1-style sync timeline: per-shooter track with shot markers,
 *     beep tick at x=0, end-of-run marker at each track's total time,
 *     vertical playhead through all tracks
 *   - Ranking table: stage time, fastest split, avg split, rank pill
 *
 * Sync engine: the audio shooter is the master. Every 100ms we re-sync
 * the other shooters by setting their ``currentTime`` to
 * ``beep_offset_in_clip + (master.currentTime - master.beep_offset)``.
 * Cheap, eventually-consistent multi-cam sync that works in browsers
 * without WebCodecs / canvas-based playback.
 */

import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  Crosshair,
  Loader2,
  MoveLeft,
  MoveRight,
  Pause,
  Play,
  Volume2,
  VolumeX,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate, useParams } from "react-router-dom";

import { Avatar } from "@/components/ui";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  api,
  type CompareShooterRecord,
  type CompareStageResponse,
  type MatchProject,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Layout = "grid" | "row" | "stack";

const SYNC_DRIFT_THRESHOLD_S = 0.15;
const SYNC_INTERVAL_MS = 120;

export function Compare() {
  const { stage: stageParam } = useParams();
  const navigate = useNavigate();
  const stageNumber = stageParam ? Number(stageParam) : NaN;

  const [project, setProject] = useState<MatchProject | null>(null);
  const [bundle, setBundle] = useState<CompareStageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [layout, setLayout] = useState<Layout>("grid");
  const [audioSlug, setAudioSlug] = useState<string | null>(null);
  const [visibleSlugs, setVisibleSlugs] = useState<Set<string>>(() => new Set());
  const [isPlaying, setIsPlaying] = useState(false);
  const [timeSinceBeep, setTimeSinceBeep] = useState(0);

  const videoRefs = useRef<Map<string, HTMLVideoElement>>(new Map());
  const rafRef = useRef<number | null>(null);

  // Load project + compare data. Stage definitions are identical across
  // every shooter in a match, so we lift them from whichever shooter is
  // alphabetically first. Compare itself is slug-less (multi-shooter view).
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const shooters = await api.listMatchShooters();
        const first = shooters.shooters[0]?.slug;
        if (!first) return;
        const p = await api.getProject(first);
        if (alive) setProject(p);
      } catch {
        /* compare bundle below covers the no-shooter case */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!Number.isFinite(stageNumber)) return;
    let alive = true;
    setBundle(null);
    setError(null);
    api
      .getStageCompare(stageNumber)
      .then((b) => {
        if (!alive) return;
        setBundle(b);
        if (b.shooters.length > 0) {
          setAudioSlug(b.shooters[0].slug);
          setVisibleSlugs(
            new Set(b.shooters.filter((s) => s.video_path).map((s) => s.slug)),
          );
        }
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof ApiError ? e.detail : String(e));
      });
    return () => {
      alive = false;
    };
  }, [stageNumber]);

  const orderedShooters = bundle?.shooters ?? [];
  const playableShooters = orderedShooters.filter(
    (s) => s.video_path && s.beep_offset_in_clip != null,
  );
  const audioShooter = audioSlug
    ? orderedShooters.find((s) => s.slug === audioSlug) ?? null
    : null;
  const maxStageTime = useMemo(
    () =>
      Math.max(
        ...playableShooters.map((s) => s.stage_time_seconds ?? 0),
        ...playableShooters.flatMap((s) => s.shots.map((p) => p.time_after_beep)),
        1,
      ),
    [playableShooters],
  );

  // Sync engine: read the master's currentTime, derive time-since-beep,
  // and pull the other videos into agreement when drift > threshold.
  useEffect(() => {
    if (!isPlaying || !audioShooter || audioShooter.beep_offset_in_clip == null)
      return;
    const masterEl = videoRefs.current.get(audioShooter.slug);
    if (!masterEl) return;

    const interval = window.setInterval(() => {
      const masterBeep = audioShooter.beep_offset_in_clip ?? 0;
      const tsb = masterEl.currentTime - masterBeep;
      setTimeSinceBeep(tsb);
      // Resync slaves.
      videoRefs.current.forEach((el, slug) => {
        if (slug === audioShooter.slug) return;
        const shooter = orderedShooters.find((s) => s.slug === slug);
        if (!shooter || shooter.beep_offset_in_clip == null) return;
        const target = shooter.beep_offset_in_clip + tsb;
        if (Math.abs(el.currentTime - target) > SYNC_DRIFT_THRESHOLD_S) {
          el.currentTime = Math.max(0, target);
        }
      });
    }, SYNC_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [isPlaying, audioShooter, orderedShooters]);

  // When the master pauses naturally (end of clip), reflect into state.
  useEffect(() => {
    if (!audioShooter) return;
    const el = videoRefs.current.get(audioShooter.slug);
    if (!el) return;
    const onPause = () => setIsPlaying(false);
    const onPlay = () => setIsPlaying(true);
    el.addEventListener("pause", onPause);
    el.addEventListener("play", onPlay);
    return () => {
      el.removeEventListener("pause", onPause);
      el.removeEventListener("play", onPlay);
    };
  }, [audioShooter, bundle]);

  // Mute toggle: only the audio shooter plays sound; others muted.
  useEffect(() => {
    videoRefs.current.forEach((el, slug) => {
      el.muted = slug !== audioSlug;
    });
  }, [audioSlug]);

  const setVideoRef = useCallback(
    (slug: string, el: HTMLVideoElement | null) => {
      if (el) videoRefs.current.set(slug, el);
      else videoRefs.current.delete(slug);
    },
    [],
  );

  const togglePlay = useCallback(() => {
    if (!audioShooter) return;
    const master = videoRefs.current.get(audioShooter.slug);
    if (!master) return;
    if (master.paused) {
      void master.play().catch(() => {});
      videoRefs.current.forEach((el, slug) => {
        if (slug !== audioShooter.slug) void el.play().catch(() => {});
      });
    } else {
      master.pause();
      videoRefs.current.forEach((el, slug) => {
        if (slug !== audioShooter.slug) el.pause();
      });
    }
  }, [audioShooter]);

  const scrubTo = useCallback(
    (tsb: number) => {
      cancelAnimationFrame(rafRef.current ?? 0);
      rafRef.current = requestAnimationFrame(() => {
        setTimeSinceBeep(tsb);
        videoRefs.current.forEach((el, slug) => {
          const shooter = orderedShooters.find((s) => s.slug === slug);
          if (!shooter || shooter.beep_offset_in_clip == null) return;
          el.currentTime = Math.max(0, shooter.beep_offset_in_clip + tsb);
        });
      });
    },
    [orderedShooters],
  );

  function toggleVisibility(slug: string) {
    setVisibleSlugs((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  function prevStage() {
    if (!project) return;
    const all = project.stages.map((s) => s.stage_number).sort((a, b) => a - b);
    const idx = all.indexOf(stageNumber);
    if (idx > 0) navigate(`/compare/${all[idx - 1]}`);
  }
  function nextStage() {
    if (!project) return;
    const all = project.stages.map((s) => s.stage_number).sort((a, b) => a - b);
    const idx = all.indexOf(stageNumber);
    if (idx >= 0 && idx < all.length - 1) navigate(`/compare/${all[idx + 1]}`);
  }

  if (!stageNumber || Number.isNaN(stageNumber)) {
    return (
      <div className="px-7 py-8 text-sm text-muted">
        Select a stage from the sidebar to compare shooters.
      </div>
    );
  }

  if (error) {
    return (
      <div className="px-7 py-8">
        <div className="rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
          {error}
        </div>
      </div>
    );
  }

  if (!bundle) {
    return (
      <div className="flex h-64 items-center justify-center gap-2 text-sm text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading compare data...
      </div>
    );
  }

  const visibleShooters = playableShooters.filter((s) =>
    visibleSlugs.has(s.slug),
  );

  return (
    <div className="flex flex-col gap-4 px-7 py-5">
      {/* Compact header (mirrors Audit's pattern) */}
      <div className="flex flex-wrap items-center gap-4 border-b border-rule pb-4">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={prevStage}
            aria-label="Previous stage"
            className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink"
          >
            <ArrowLeft className="size-4" />
          </button>
          <button
            type="button"
            onClick={nextStage}
            aria-label="Next stage"
            className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink"
          >
            <ArrowRight className="size-4" />
          </button>
        </div>
        <h1 className="font-display text-3xl font-bold uppercase leading-none tracking-tight text-ink">
          <span className="text-led">STAGE {pad2(stageNumber)}</span>
          <span className="mx-2 text-whisper">·</span>
          <span>{bundle.stage_name}</span>
        </h1>
        <nav
          aria-label="Stage views"
          className="ml-auto inline-flex overflow-hidden rounded-lg border border-rule bg-surface-2 p-0.5"
        >
          <button
            type="button"
            onClick={() => {
              // Compare is multi-shooter; pick the audio source (the
              // primary shown in this view) as the target shooter so
              // the user lands on the same camera they were watching.
              const target = audioSlug ?? orderedShooters[0]?.slug;
              if (target) navigate(`/audit/${target}/${stageNumber}`);
            }}
            className="inline-flex min-h-9 items-center rounded-md px-3.5 font-sans text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted hover:text-ink-2"
          >
            Audit
          </button>
          <span className="tab-pill-led-fill inline-flex min-h-9 items-center rounded-md px-3.5">
            Compare
          </span>
          <button
            type="button"
            onClick={() => {
              const target = audioSlug ?? orderedShooters[0]?.slug;
              if (target) navigate(`/coach/${target}/${stageNumber}`);
            }}
            className="inline-flex min-h-9 items-center rounded-md px-3.5 font-sans text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted hover:text-ink"
          >
            Coach
          </button>
        </nav>
      </div>

      {/* Toolbar: visibility chips + layout toggle + export */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {orderedShooters.map((shooter) => (
            <ShooterChip
              key={shooter.slug}
              shooter={shooter}
              visible={visibleSlugs.has(shooter.slug)}
              isAudio={audioSlug === shooter.slug}
              onToggleVisibility={() => toggleVisibility(shooter.slug)}
              onPickAudio={() => setAudioSlug(shooter.slug)}
            />
          ))}
        </div>
        <div className="ml-auto inline-flex overflow-hidden rounded-lg border border-rule bg-surface-2 p-0.5">
          <LayoutPill
            label="2x2"
            active={layout === "grid"}
            onClick={() => setLayout("grid")}
          />
          <LayoutPill
            label="1x4"
            active={layout === "row"}
            onClick={() => setLayout("row")}
          />
          <LayoutPill
            label="Stack"
            active={layout === "stack"}
            onClick={() => setLayout("stack")}
          />
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() =>
            window.alert(
              "Export from the Compare view runs the same pipeline as 'splitsmith compare export <match>'. Kick it off from the Export page (#330) -- the compare grid mode arrives with the rest of the multi-shooter export plumbing.",
            )
          }
        >
          <ArrowDownToLine className="size-3.5" />
          <span className="font-display uppercase tracking-[0.08em]">
            Export FCPXML
          </span>
        </Button>
      </div>

      {/* Unfinished banner: when at least one shooter is playable, the
       *  grid renders the playable subset. Shooters without a cached
       *  trim are surfaced here so the user can rebuild the cache (when
       *  audit is done) or jump into audit (when nothing has run yet)
       *  without having to leave the page. */}
      {visibleShooters.length > 0 &&
      orderedShooters.some((s) => !s.video_path) ? (
        <UnfinishedShootersBanner
          unfinished={orderedShooters.filter((s) => !s.video_path)}
          onOpenInAudit={(slug) =>
            navigate(`/audit/${slug}/${stageNumber}`)
          }
        />
      ) : null}

      {/* Video grid */}
      <div className={layoutClass(layout)}>
        {visibleShooters.length === 0 ? (
          <CompareEmptyState
            unfinished={orderedShooters.filter((s) => !s.video_path)}
            onOpenInAudit={(slug) => {
              navigate(`/audit/${slug}/${stageNumber}`);
            }}
          />
        ) : (
          visibleShooters.map((shooter) => (
            <VideoTile
              key={shooter.slug}
              shooter={shooter}
              isAudio={audioSlug === shooter.slug}
              onPickAudio={() => setAudioSlug(shooter.slug)}
              onMount={(el) => setVideoRef(shooter.slug, el)}
            />
          ))
        )}
      </div>

      {/* Transport */}
      <Transport
        isPlaying={isPlaying}
        onTogglePlay={togglePlay}
        timeSinceBeep={timeSinceBeep}
        maxTime={maxStageTime}
        onScrub={scrubTo}
      />

      {/* Sync timeline */}
      <SyncTimeline
        shooters={playableShooters}
        maxTime={maxStageTime}
        timeSinceBeep={timeSinceBeep}
        audioSlug={audioSlug}
        onScrub={scrubTo}
      />

      {/* Ranking */}
      <RankingTable shooters={playableShooters} />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Empty state -- no shooter has a usable trim for this stage yet             */
/* -------------------------------------------------------------------------- */

function UnfinishedShootersBanner({
  unfinished,
  onOpenInAudit,
}: {
  unfinished: CompareShooterRecord[];
  onOpenInAudit: (slug: string) => void | Promise<void>;
}) {
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [doneSlugs, setDoneSlugs] = useState<Set<string>>(() => new Set());

  // A shooter with shots[] but no video_path has finished audit; just
  // the trim cache is missing. Offer to rebuild it in-place. A shooter
  // with neither needs to be audited first.
  const rebuild = async (slug: string) => {
    setErrorMsg(null);
    setBusySlug(slug);
    try {
      const res = await api.buildShooterTrimCaches(slug);
      // The server queues jobs but the bundle won't see the new trim
      // until the worker finishes. Tell the user to refresh once jobs
      // settle rather than polling the bundle here (Compare's polling
      // story is "reload the page"; the JobsPanel surfaces progress).
      if (res.jobs_submitted.length === 0) {
        setErrorMsg(
          `${slug}: nothing to rebuild -- either no stage qualifies or every cache is already on disk. Open the shooter in audit to see why.`,
        );
        return;
      }
      setDoneSlugs((prev) => new Set(prev).add(slug));
    } catch (e) {
      setErrorMsg(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusySlug(null);
    }
  };

  return (
    <div className="rounded-2xl border border-rule bg-surface px-5 py-3 text-sm text-muted">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink-2">
          Missing footage
        </span>
        <span className="text-ink-2">{unfinished.length}</span>
        <span>
          {unfinished.length === 1 ? "shooter has" : "shooters have"} no
          cached trim for this stage.
        </span>
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        {unfinished.map((s) => {
          const auditedButUncached = s.shots.length > 0;
          const queued = doneSlugs.has(s.slug);
          return (
            <div
              key={s.slug}
              className="inline-flex items-center gap-2 rounded-lg border border-rule-strong bg-surface-2 px-3 py-1.5 text-xs"
            >
              <span className="font-semibold text-ink-2">{s.name}</span>
              {auditedButUncached ? (
                queued ? (
                  <span className="text-[0.6875rem] uppercase tracking-[0.08em] text-done">
                    Build queued -- check Jobs
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => rebuild(s.slug)}
                    disabled={busySlug === s.slug}
                    className="inline-flex items-center gap-1.5 rounded-md border border-rule-strong bg-surface px-2 py-1 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-ink hover:border-led hover:text-led disabled:opacity-50"
                  >
                    {busySlug === s.slug ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : null}
                    Build trim cache
                  </button>
                )
              ) : (
                <button
                  type="button"
                  onClick={() => onOpenInAudit(s.slug)}
                  className="inline-flex items-center gap-1.5 rounded-md border border-rule-strong bg-surface px-2 py-1 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-ink hover:border-led hover:text-led"
                >
                  <ArrowRight className="size-3" />
                  Open in audit
                </button>
              )}
            </div>
          );
        })}
      </div>
      {errorMsg ? (
        <div className="mt-2 text-xs text-led">{errorMsg}</div>
      ) : null}
    </div>
  );
}

function CompareEmptyState({
  unfinished,
  onOpenInAudit,
}: {
  unfinished: CompareShooterRecord[];
  onOpenInAudit: (slug: string) => void | Promise<void>;
}) {
  // Compare uses the lossless export if present, otherwise the audit-mode
  // short-GOP cache; both come out of the per-shooter trim + audit pass.
  // So a missing video_path means audit isn't finished for that shooter
  // (no primary, no beep, or the trim cache hasn't been built).
  return (
    <div className="rounded-2xl border border-rule-strong bg-surface px-6 py-10 text-sm text-muted">
      <p className="text-center text-ink-2">
        Compare needs an audited primary cam from each shooter.
      </p>
      {unfinished.length > 0 && (
        <>
          <p className="mt-2 text-center">
            Not ready yet:{" "}
            <span className="font-semibold text-ink-2">
              {unfinished.map((s) => s.name).join(", ")}
            </span>
            .
          </p>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
            {unfinished.map((s) => (
              <button
                key={s.slug}
                type="button"
                onClick={() => onOpenInAudit(s.slug)}
                className="inline-flex items-center gap-2 rounded-lg border border-rule-strong bg-surface-2 px-3 py-2 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-ink hover:border-led hover:text-led"
              >
                <ArrowRight className="size-3.5" />
                Audit {s.name}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Shooter visibility chip                                                    */
/* -------------------------------------------------------------------------- */

function ShooterChip({
  shooter,
  visible,
  isAudio,
  onToggleVisibility,
  onPickAudio,
}: {
  shooter: CompareShooterRecord;
  visible: boolean;
  isAudio: boolean;
  onToggleVisibility: () => void;
  onPickAudio: () => void;
}) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-2 py-1 text-[0.8125rem] transition-colors",
        visible
          ? "border-rule-strong bg-surface-2"
          : "border-rule bg-surface-2/40 text-muted opacity-60",
        isAudio &&
          "border-led shadow-[0_0_0_1px_var(--color-led-deep),0_0_14px_var(--color-led-glow)]",
      )}
    >
      <Avatar
        size="xs"
        initials={initials(shooter.name)}
        tone={undefined}
        seed={shooter.slug}
        name={shooter.name}
      />
      <button
        type="button"
        onClick={onToggleVisibility}
        className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-ink-2 hover:text-ink"
        title={visible ? "Hide" : "Show"}
      >
        {shooter.name}
      </button>
      <button
        type="button"
        onClick={onPickAudio}
        title={isAudio ? "Audio source" : "Use as audio source"}
        aria-pressed={isAudio}
        className={cn(
          "inline-flex size-6 items-center justify-center rounded-full transition-colors",
          isAudio
            ? "bg-led text-bg shadow-[0_0_10px_var(--color-led-glow)]"
            : "bg-surface-3 text-subtle hover:text-ink",
        )}
      >
        {isAudio ? <Volume2 className="size-3" /> : <VolumeX className="size-3" />}
      </button>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Layout                                                                     */
/* -------------------------------------------------------------------------- */

function layoutClass(layout: Layout): string {
  if (layout === "grid") return "grid grid-cols-1 gap-3 sm:grid-cols-2";
  if (layout === "row") return "grid grid-cols-1 gap-3 md:grid-cols-4";
  return "flex flex-col gap-3";
}

function LayoutPill({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex min-h-9 items-center rounded-md px-3.5 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em] transition-colors",
        active
          ? "bg-ink text-bg"
          : "text-muted hover:text-ink",
      )}
    >
      {label}
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Video tile                                                                 */
/* -------------------------------------------------------------------------- */

function VideoTile({
  shooter,
  isAudio,
  onPickAudio,
  onMount,
}: {
  shooter: CompareShooterRecord;
  isAudio: boolean;
  onPickAudio: () => void;
  onMount: (el: HTMLVideoElement | null) => void;
}) {
  const url = shooter.video_path
    ? api.shooterVideoStreamUrl(shooter.slug, shooter.video_path)
    : null;
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border bg-bg-glow",
        isAudio
          ? "border-led shadow-[0_0_0_1px_var(--color-led-deep),0_0_16px_var(--color-led-glow)]"
          : "border-rule-strong",
      )}
    >
      <div className="flex items-center gap-2 border-b border-rule bg-surface-2 px-3 py-2">
        <Avatar
          size="xs"
          initials={initials(shooter.name)}
          tone={undefined}
          seed={shooter.slug}
          name={shooter.name}
        />
        <span className="font-display text-[0.75rem] font-bold uppercase tracking-[0.06em] text-ink">
          {shooter.name}
        </span>
        {isAudio && (
          <span className="ml-auto inline-flex items-center gap-1 rounded border border-led-deep bg-led px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-bg shadow-[0_0_8px_var(--color-led-glow)]">
            <Volume2 className="size-2.5" />
            Audio
          </span>
        )}
      </div>
      <div className="relative">
        {url ? (
          <video
            ref={onMount}
            src={url}
            preload="metadata"
            playsInline
            controls={false}
            className="aspect-video w-full bg-black"
            onClick={(e) => {
              if (!isAudio) {
                onPickAudio();
                e.preventDefault();
              }
            }}
          />
        ) : (
          <div className="flex aspect-video items-center justify-center bg-surface-3 text-sm text-muted">
            No trim yet
          </div>
        )}
      </div>
      <div className="flex items-center justify-between border-t border-rule bg-surface-2 px-3 py-1.5 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted tabular-nums">
        <span>
          {shooter.stage_time_seconds != null && (
            <>
              Stage{" "}
              <b className="font-bold text-ink">
                {shooter.stage_time_seconds.toFixed(2)}s
              </b>
            </>
          )}
        </span>
        <span>
          <b className="font-bold text-ink">{shooter.shots.length}</b> shots
        </span>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Transport                                                                  */
/* -------------------------------------------------------------------------- */

function Transport({
  isPlaying,
  onTogglePlay,
  timeSinceBeep,
  maxTime,
  onScrub,
}: {
  isPlaying: boolean;
  onTogglePlay: () => void;
  timeSinceBeep: number;
  maxTime: number;
  onScrub: (tsb: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-rule bg-surface-2 px-4 py-3">
      <button
        type="button"
        onClick={() => onScrub(0)}
        aria-label="Jump to beep"
        title="Jump to beep"
        className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-3 text-muted transition-colors hover:bg-surface-4 hover:text-ink"
      >
        <MoveLeft className="size-4" />
      </button>
      <button
        type="button"
        onClick={onTogglePlay}
        aria-label={isPlaying ? "Pause" : "Play"}
        className="inline-flex size-11 items-center justify-center rounded-full bg-led text-bg shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] transition-colors hover:bg-led-soft"
      >
        {isPlaying ? <Pause className="size-5" /> : <Play className="size-5" />}
      </button>
      <button
        type="button"
        onClick={() => onScrub(maxTime)}
        aria-label="Jump to end"
        title="Jump to end"
        className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-3 text-muted transition-colors hover:bg-surface-4 hover:text-ink"
      >
        <MoveRight className="size-4" />
      </button>
      <div className="ml-2 flex items-center gap-3 font-mono tabular-nums">
        <Readout label="t-beep" value={`${timeSinceBeep.toFixed(3)}s`} />
        <Readout label="span" value={`${maxTime.toFixed(2)}s`} />
      </div>
      <input
        type="range"
        className="flex-1 min-w-[160px] accent-led"
        min={0}
        max={maxTime}
        step={0.01}
        value={Math.max(0, Math.min(timeSinceBeep, maxTime))}
        onChange={(e) => onScrub(parseFloat(e.target.value))}
      />
    </div>
  );
}

function Readout({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col items-start gap-0.5">
      <span className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
        {label}
      </span>
      <span className="font-mono text-base font-bold leading-none text-ink">
        {value}
      </span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Sync timeline                                                              */
/* -------------------------------------------------------------------------- */

const TRACK_PALETTE: string[] = [
  "var(--color-led)",
  "var(--color-shooter-jl)",
  "var(--color-shooter-pe)",
  "var(--color-shooter-rj)",
  "var(--color-manual)",
];

function SyncTimeline({
  shooters,
  maxTime,
  timeSinceBeep,
  audioSlug,
  onScrub,
}: {
  shooters: CompareShooterRecord[];
  maxTime: number;
  timeSinceBeep: number;
  audioSlug: string | null;
  onScrub: (tsb: number) => void;
}) {
  const trackHeight = 56;
  const padLeft = 56;
  const padRight = 24;
  const padTop = 28;
  const padBottom = 22;
  const innerW = 1200;
  const innerH = padTop + shooters.length * trackHeight + padBottom;
  const xOf = (tsb: number) =>
    padLeft + ((tsb / maxTime) * (innerW - padLeft - padRight));

  function onClick(e: React.MouseEvent<SVGRectElement>) {
    const rect = (e.currentTarget as SVGRectElement).getBoundingClientRect();
    const px = e.clientX - rect.left;
    const ratio = (px - padLeft * (rect.width / innerW)) /
      ((innerW - padLeft - padRight) * (rect.width / innerW));
    onScrub(Math.max(0, Math.min(ratio * maxTime, maxTime)));
  }

  // Time ruler ticks every second.
  const ticks: number[] = [];
  for (let t = 0; t <= maxTime + 0.001; t += 1) ticks.push(t);

  return (
    <div className="overflow-hidden rounded-2xl border border-rule-strong bg-bg-glow shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-rule bg-gradient-to-b from-surface to-transparent px-5 py-3">
        <div className="inline-flex items-center gap-2.5 font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
          <Crosshair className="size-4 text-led" />
          Sync timeline
        </div>
        <span className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          all shooters aligned to beep at t=0
        </span>
      </div>
      <svg
        viewBox={`0 0 ${innerW} ${innerH}`}
        className="block w-full"
        preserveAspectRatio="none"
      >
        {/* Clickable backdrop */}
        <rect
          x={0}
          y={0}
          width={innerW}
          height={innerH}
          fill="transparent"
          onClick={onClick}
          style={{ cursor: "crosshair" }}
        />
        {/* Time ruler */}
        {ticks.map((t) => (
          <g key={`tick-${t}`}>
            <line
              x1={xOf(t)}
              x2={xOf(t)}
              y1={padTop - 4}
              y2={innerH - padBottom + 4}
              stroke="var(--color-rule)"
              strokeWidth={1}
            />
            <text
              x={xOf(t)}
              y={padTop - 8}
              textAnchor="middle"
              fill="var(--color-subtle)"
              fontFamily="JetBrains Mono, monospace"
              fontSize={10}
            >
              {t}s
            </text>
          </g>
        ))}
        {/* Beep marker */}
        <line
          x1={xOf(0)}
          x2={xOf(0)}
          y1={padTop}
          y2={innerH - padBottom}
          stroke="var(--color-beep)"
          strokeWidth={1.5}
          strokeDasharray="4 4"
        />
        {/* Playhead */}
        <line
          x1={xOf(Math.max(0, Math.min(timeSinceBeep, maxTime)))}
          x2={xOf(Math.max(0, Math.min(timeSinceBeep, maxTime)))}
          y1={padTop}
          y2={innerH - padBottom}
          stroke="var(--color-led)"
          strokeWidth={2}
          style={{
            filter: "drop-shadow(0 0 4px var(--color-led-glow))",
          }}
        />
        {/* Per-shooter tracks */}
        {shooters.map((shooter, i) => {
          const yMid = padTop + i * trackHeight + trackHeight / 2;
          const color =
            TRACK_PALETTE[i % TRACK_PALETTE.length] ?? "var(--color-ink-2)";
          const endX = xOf(shooter.stage_time_seconds ?? maxTime);
          return (
            <g key={shooter.slug}>
              {/* Track label */}
              <text
                x={padLeft - 10}
                y={yMid + 4}
                textAnchor="end"
                fill={audioSlug === shooter.slug ? "var(--color-led)" : "var(--color-ink-2)"}
                fontFamily="Antonio, sans-serif"
                fontSize={13}
                fontWeight={700}
              >
                {initials(shooter.name)}
              </text>
              {/* Track lane */}
              <line
                x1={xOf(0)}
                x2={endX}
                y1={yMid}
                y2={yMid}
                stroke={color}
                strokeWidth={3}
                strokeOpacity={0.4}
                strokeLinecap="round"
              />
              {/* End marker + total time */}
              <g>
                <line
                  x1={endX}
                  x2={endX}
                  y1={yMid - 8}
                  y2={yMid + 8}
                  stroke={color}
                  strokeWidth={2}
                />
                <text
                  x={endX + 6}
                  y={yMid + 4}
                  textAnchor="start"
                  fill={color}
                  fontFamily="JetBrains Mono, monospace"
                  fontSize={10}
                  fontWeight={700}
                >
                  {(shooter.stage_time_seconds ?? 0).toFixed(2)}s
                </text>
              </g>
              {/* Shot markers */}
              {shooter.shots.map((shot) => (
                <circle
                  key={`${shooter.slug}-${shot.shot_number}`}
                  cx={xOf(shot.time_after_beep)}
                  cy={yMid}
                  r={shot.source === "manual" ? 4 : 3.5}
                  fill={shot.source === "manual" ? "var(--color-manual)" : color}
                  stroke="var(--color-bg)"
                  strokeWidth={1}
                />
              ))}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Ranking                                                                    */
/* -------------------------------------------------------------------------- */

function RankingTable({ shooters }: { shooters: CompareShooterRecord[] }) {
  const rows = shooters
    .map((s) => {
      const splits = computeSplits(s.shots);
      return {
        shooter: s,
        time: s.stage_time_seconds ?? Infinity,
        avgSplit: splits.length === 0 ? null : avg(splits),
        fastestSplit: splits.length === 0 ? null : Math.min(...splits),
        shotCount: s.shots.length,
      };
    })
    .sort((a, b) => a.time - b.time)
    .map((row, i) => ({ ...row, rank: i + 1 }));

  return (
    <div className="overflow-hidden rounded-2xl border border-rule-strong bg-surface shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
      <div className="border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-5 py-3 font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
        Ranking
      </div>
      <div className="grid grid-cols-[48px_1fr_120px_120px_120px_80px] items-center gap-3 border-b border-rule bg-surface-2 px-5 py-2 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.18em] text-subtle">
        <span>#</span>
        <span>Shooter</span>
        <span className="text-right">Time</span>
        <span className="text-right">Fastest</span>
        <span className="text-right">Avg split</span>
        <span className="text-right">Shots</span>
      </div>
      {rows.map((row) => (
        <div
          key={row.shooter.slug}
          className="grid grid-cols-[48px_1fr_120px_120px_120px_80px] items-center gap-3 border-b border-rule px-5 py-3 last:border-b-0"
        >
          <RankPill rank={row.rank} />
          <div className="inline-flex items-center gap-2.5">
            <Avatar
              size="sm"
              initials={initials(row.shooter.name)}
              tone={undefined}
              seed={row.shooter.slug}
            />
            <span className="font-display text-sm font-bold uppercase tracking-[0.04em] text-ink">
              {row.shooter.name}
            </span>
          </div>
          <span
            className={cn(
              "text-right font-mono text-sm font-bold tabular-nums",
              row.rank === 1 ? "text-led drop-shadow-[0_0_8px_var(--color-led-glow)]" : "text-ink",
            )}
          >
            {Number.isFinite(row.time) ? `${row.time.toFixed(2)}s` : "--"}
          </span>
          <span className="text-right font-mono text-[0.8125rem] tabular-nums text-ink-2">
            {row.fastestSplit != null ? `${row.fastestSplit.toFixed(3)}s` : "--"}
          </span>
          <span className="text-right font-mono text-[0.8125rem] tabular-nums text-muted">
            {row.avgSplit != null ? `${row.avgSplit.toFixed(3)}s` : "--"}
          </span>
          <span className="text-right font-mono text-[0.8125rem] tabular-nums text-muted">
            {row.shotCount}
          </span>
        </div>
      ))}
    </div>
  );
}

function RankPill({ rank }: { rank: number }) {
  const tone =
    rank === 1
      ? "border-led bg-led text-bg shadow-[0_0_10px_var(--color-led-glow)]"
      : rank === 2
        ? "border-ink-2 bg-surface-3 text-ink"
        : "border-rule bg-surface-3 text-muted";
  return (
    <span
      className={cn(
        "inline-flex size-8 items-center justify-center rounded-md border font-display text-sm font-bold tabular-nums",
        tone,
      )}
    >
      {rank}
    </span>
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function computeSplits(
  shots: { time_after_beep: number }[],
): number[] {
  if (shots.length < 2) return [];
  const sorted = [...shots]
    .map((s) => s.time_after_beep)
    .sort((a, b) => a - b);
  const splits: number[] = [];
  for (let i = 1; i < sorted.length; i++) {
    splits.push(sorted[i] - sorted[i - 1]);
  }
  return splits;
}

function avg(arr: number[]): number {
  return arr.reduce((a, b) => a + b, 0) / arr.length;
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 0 || !parts[0]) return "??";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
