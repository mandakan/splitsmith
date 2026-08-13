/**
 * Compare route (/compare/:stage) - multi-shooter cockpit (#328, #700).
 *
 * Replaces the CLI-only ``splitsmith compare export`` for in-app
 * browsing. Viewport-locked cockpit layout:
 *
 *   - One merged header row (flex-none): stage nav + title + Audit /
 *     Compare / Coach tab strip, then the visibility chips, layout
 *     pills and export pushed to the right cluster
 *   - Visibility chips: one per shooter with avatar + initials; the
 *     audio-source chip carries the LED ring
 *   - Layout toggle: 2x2 / 1x4 / Stack
 *   - Fill-sizing video zone: each shooter's lossless trim, beep-
 *     aligned, sharing a bounded row with the leaderboard rail
 *   - Leaderboard rail: rank, stage time, delta, split microstats
 *   - Fused transport dock: play/scrub controls over the per-shooter
 *     track lanes behind one playhead
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
  ChevronDown,
  Loader2,
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
import {
  useLocation,
  useNavigate,
  useOutletContext,
  useParams,
  useSearchParams,
} from "react-router-dom";

import { Avatar } from "@/components/ui";
import { Button } from "@/components/ui/button";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { Snackbar, type SnackState } from "@/components/Snackbar";
import {
  ApiError,
  api,
  capabilityDenied,
  type CoachVideoEntry,
  type CompareShooterRecord,
  type CompareStageResponse,
  type MatchProject,
} from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";
import { momentHref, momentToSearch, parseMoment, resolveMomentView } from "@/lib/moment";
import { isShareView } from "@/lib/shareView";
import { useActiveShare } from "@/lib/useActiveShare";
import { cn } from "@/lib/utils";

import { initials } from "./compare/format";
import { LeaderboardRail } from "./compare/LeaderboardRail";
import { TransportDock } from "./compare/TransportDock";

type Layout = "grid" | "row" | "stack";

const SYNC_DRIFT_THRESHOLD_S = 0.15;
const SYNC_INTERVAL_MS = 120;

export function Compare() {
  const { stage: stageParam } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const urlMoment = useMemo(() => parseMoment(searchParams), [searchParams]);
  const href = useMatchHref();
  const shareView = isShareView(location.pathname);
  const stageNumber = stageParam ? Number(stageParam) : NaN;
  // Compare also mounts under ShareShell, which does supply a full
  // MatchShellOutletContext (capabilities: [] - see ShareShell.tsx). The
  // pre-existing `shareView` guard below excludes the rebuild-trim-cache
  // button entirely on that mount, before `editDenied` is ever consulted,
  // so the (always-empty) capabilities on the share mount never matter.
  const ctx = useOutletContext<MatchShellOutletContext | undefined>();
  // #756: rebuild-trim-caches POSTs a job - an edit-class write the
  // mirror guard 403s. Hidden (not disabled) here: it's one action among
  // several on a page whose primary value is reading.
  const editDenied = capabilityDenied(ctx?.capabilities, "edit");
  const { shareUrl } = useActiveShare();

  const [project, setProject] = useState<MatchProject | null>(null);
  const [bundle, setBundle] = useState<CompareStageResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [layout, setLayout] = useState<Layout>("grid");
  const [audioSlug, setAudioSlug] = useState<string | null>(null);
  const [visibleSlugs, setVisibleSlugs] = useState<Set<string>>(() => new Set());
  const [isPlaying, setIsPlaying] = useState(false);
  const [timeSinceBeep, setTimeSinceBeep] = useState(0);
  const [snack, setSnack] = useState<SnackState | null>(null);

  const videoRefs = useRef<Map<string, HTMLVideoElement>>(new Map());
  const rafRef = useRef<number | null>(null);
  const maxDriftRef = useRef(0);
  const startedAtRef = useRef(0);
  // Tracks the serialized form of the last APPLIED moment so a
  // query-only navigation to a *different* moment on an already-loaded
  // bundle re-arms and applies again, while a re-render with the same
  // moment (or no moment) does not keep re-scrubbing over the user's
  // own interaction. Reset to null on stage change alongside the bundle.
  const lastAppliedMomentRef = useRef<string | null>(null);

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
    lastAppliedMomentRef.current = null;
    api
      .getStageCompare(stageNumber)
      .then((b) => {
        if (!alive) return;
        setBundle(b);
        if (b.shooters.length > 0) {
          setAudioSlug(b.shooters[0].slug);
          setVisibleSlugs(
            new Set(b.shooters.filter((s) => s.video_ref).map((s) => s.slug)),
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

  // Camera alternatives per shooter, from each shooter's coach payload
  // (share-whitelisted; Compare's own bundle carries only the primary
  // trim). A failed fetch just means no switcher for that shooter.
  const [camsBySlug, setCamsBySlug] = useState<Record<string, CoachVideoEntry[]>>({});
  const [camIndexBySlug, setCamIndexBySlug] = useState<Record<string, number>>({});

  useEffect(() => {
    if (!bundle) return;
    let alive = true;
    setCamsBySlug({});
    setCamIndexBySlug({});
    (async () => {
      const results = await Promise.allSettled(
        bundle.shooters.map(async (s) => {
          const coach = await api.getStageCoach(s.slug, stageNumber);
          if (!coach) throw new Error(`no coach data for ${s.slug}`);
          return [s.slug, coach.videos] as const;
        }),
      );
      if (!alive) return;
      const map: Record<string, CoachVideoEntry[]> = {};
      for (const r of results) if (r.status === "fulfilled") map[r.value[0]] = r.value[1];
      setCamsBySlug(map);
    })();
    return () => {
      alive = false;
    };
  }, [bundle, stageNumber]);

  // Memoized: several effects depend on this list, and a fresh [] per
  // render would re-arm them all on every render while bundle is null.
  const orderedShooters = useMemo(() => bundle?.shooters ?? [], [bundle]);
  const playableShooters = orderedShooters.filter(
    (s) => s.video_ref && s.beep_offset_in_clip != null,
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

  // Index into camsBySlug[slug]; 0 = the bundle's own primary trim.
  // Invalid or unsyncable picks resolve to 0 - graceful drift, never an
  // error (moment links may name cameras that no longer exist).
  const camIndexFor = useCallback(
    (slug: string): number => {
      const idx = camIndexBySlug[slug] ?? 0;
      return idx > 0 && camsBySlug[slug]?.[idx]?.beep_in_clip != null ? idx : 0;
    },
    [camIndexBySlug, camsBySlug],
  );
  const effectiveBeep = useCallback(
    (s: CompareShooterRecord): number | null => {
      const idx = camIndexFor(s.slug);
      return idx > 0 ? camsBySlug[s.slug][idx].beep_in_clip : s.beep_offset_in_clip;
    },
    [camIndexFor, camsBySlug],
  );
  const tileSrc = useCallback(
    (s: CompareShooterRecord): string | null => {
      const idx = camIndexFor(s.slug);
      if (idx > 0) return api.videoStreamUrl(s.slug, camsBySlug[s.slug][idx].path);
      return s.video_ref ? api.shooterVideoStreamUrl(s.slug, s.video_ref) : null;
    },
    [camIndexFor, camsBySlug],
  );

  // Sync engine: read the master's currentTime, derive time-since-beep,
  // and pull the other videos into agreement when drift > threshold.
  useEffect(() => {
    if (!isPlaying || !audioShooter || audioShooter.beep_offset_in_clip == null)
      return;
    const masterEl = videoRefs.current.get(audioShooter.slug);
    if (!masterEl) return;

    startedAtRef.current = Date.now();
    maxDriftRef.current = 0;

    const interval = window.setInterval(() => {
      const masterBeep = effectiveBeep(audioShooter) ?? 0;
      const tsb = masterEl.currentTime - masterBeep;
      setTimeSinceBeep(tsb);
      // Resync slaves.
      videoRefs.current.forEach((el, slug) => {
        if (slug === audioShooter.slug) return;
        const shooter = orderedShooters.find((s) => s.slug === slug);
        if (!shooter) return;
        const beep = effectiveBeep(shooter);
        if (beep == null) return;
        const target = beep + tsb;
        const drift = Math.abs(el.currentTime - target);
        maxDriftRef.current = Math.max(maxDriftRef.current, drift);
        if (drift > SYNC_DRIFT_THRESHOLD_S) {
          el.currentTime = Math.max(0, target);
        }
      });
    }, SYNC_INTERVAL_MS);
    return () => {
      window.clearInterval(interval);
      if (maxDriftRef.current > 0) {
        const elapsedS = (Date.now() - startedAtRef.current) / 1000;
        console.info(
          "[compare-sync] stage %s max drift %sms over %ss",
          stageNumber,
          Math.round(maxDriftRef.current * 1000),
          Math.round(elapsedS),
        );
        maxDriftRef.current = 0;
      }
    };
  }, [isPlaying, audioShooter, orderedShooters, stageNumber, effectiveBeep]);

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
          if (!shooter) return;
          const beep = effectiveBeep(shooter);
          if (beep == null) return;
          el.currentTime = Math.max(0, beep + tsb);
        });
      });
    },
    [orderedShooters, effectiveBeep],
  );

  // Apply a shared moment (?t=&cam=&who=) on bundle load and whenever the
  // moment itself changes (query-only navigation, e.g. clicking another
  // shared link while Compare stays mounted): focus the requested
  // camera/shooters and scrub to the requested time. Guarded by
  // lastAppliedMomentRef (the last applied moment's serialized form) so
  // re-renders with the same moment don't keep re-applying and fighting
  // the user's own scrubbing, while a genuinely new moment re-arms.
  useEffect(() => {
    if (!bundle) return;
    const moment = urlMoment;
    if (!moment) return;
    const serialized = momentToSearch(moment).toString();
    if (lastAppliedMomentRef.current === serialized) return;
    lastAppliedMomentRef.current = serialized;
    const slugs = new Set(bundle.shooters.map((s) => s.slug));
    const view = resolveMomentView(moment, slugs);
    if (view.who) setVisibleSlugs(new Set(view.who));
    if (view.cam) setAudioSlug(view.cam);
    if (moment.v && typeof moment.v === "object") {
      const roster = new Set(bundle.shooters.map((s) => s.slug));
      const picks: Record<string, number> = {};
      for (const [slug, idx] of Object.entries(moment.v)) {
        if (roster.has(slug)) picks[slug] = idx;
      }
      // Validity against the camera lists is enforced lazily by camIndexFor
      // - the lists may still be loading when the moment applies.
      if (Object.keys(picks).length > 0) setCamIndexBySlug((prev) => ({ ...prev, ...picks }));
    }
    scrubTo(moment.t);
    // scrubTo writes currentTime immediately, but a video element that has
    // not reached HAVE_METADATA can drop that write - re-apply once per
    // element when its metadata arrives. Arrival is paused (isPlaying
    // defaults to false), so nothing else moves the clock in between.
    videoRefs.current.forEach((el, slug) => {
      if (el.readyState >= 1) return;
      const shooter = bundle.shooters.find((s) => s.slug === slug);
      if (!shooter) return;
      const offset = effectiveBeep(shooter);
      if (offset == null) return;
      el.addEventListener(
        "loadedmetadata",
        () => {
          el.currentTime = Math.max(0, offset + moment.t);
        },
        { once: true },
      );
    });
  }, [bundle, urlMoment, scrubTo, effectiveBeep]);

  // A tile whose src just swapped reloads at clip time 0; put it back on
  // the shared clock once its metadata is in. The drift guard keeps this
  // from fighting the sync engine or the user's scrubbing.
  useEffect(() => {
    videoRefs.current.forEach((el, slug) => {
      const shooter = orderedShooters.find((s) => s.slug === slug);
      if (!shooter) return;
      const beep = effectiveBeep(shooter);
      if (beep == null) return;
      const target = Math.max(0, beep + timeSinceBeep);
      if (Math.abs(el.currentTime - target) < 0.3) return;
      const apply = () => {
        el.currentTime = target;
        if (isPlaying) void el.play().catch(() => {});
      };
      if (el.readyState >= 1) apply();
      else el.addEventListener("loadedmetadata", apply, { once: true });
    });
  }, [camIndexBySlug, camsBySlug, orderedShooters, effectiveBeep, timeSinceBeep, isPlaying]);

  // Copies a shareable moment link: current time-since-beep, the audio
  // camera, and whichever shooters are currently visible - mirrors
  // ResultsStage's handleCopyMoment (single-shooter) but adds cam/who.
  // When the match has a live share, copy the share-scoped moment URL
  // instead of the operator one, so the link works for whoever the
  // owner actually shares it with. Share viewers never reach this
  // branch: useActiveShare returns null by construction on a share
  // mount, so they keep copying their own share-relative URL.
  const handleCopyMoment = useCallback(async () => {
    const t = Math.round(timeSinceBeep * 100) / 100;
    const who = playableShooters
      .filter((s) => visibleSlugs.has(s.slug))
      .map((s) => s.slug);
    const v: Record<string, number> = {};
    for (const s of playableShooters) {
      const idx = camIndexFor(s.slug);
      if (idx > 0) v[s.slug] = idx;
    }
    const moment = {
      t,
      cam: audioSlug ?? undefined,
      who,
      ...(Object.keys(v).length > 0 ? { v } : {}),
    };
    const link = shareUrl
      ? `${shareUrl}/compare/${stageNumber}?${momentToSearch(moment).toString()}`
      : `${window.location.origin}${momentHref(location.pathname, moment)}`;
    try {
      await navigator.clipboard.writeText(link);
      setSnack({
        message: shareUrl
          ? `Share link copied at ${t.toFixed(2)}s`
          : `Link copied at ${t.toFixed(2)}s`,
        tone: "status",
      });
    } catch {
      setSnack({ message: "Could not copy link", tone: "error" });
    }
  }, [
    timeSinceBeep,
    playableShooters,
    visibleSlugs,
    audioSlug,
    camIndexFor,
    location.pathname,
    shareUrl,
    stageNumber,
  ]);

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
    if (idx > 0) navigate(href("compare", String(all[idx - 1])));
  }
  function nextStage() {
    if (!project) return;
    const all = project.stages.map((s) => s.stage_number).sort((a, b) => a - b);
    const idx = all.indexOf(stageNumber);
    if (idx >= 0 && idx < all.length - 1)
      navigate(href("compare", String(all[idx + 1])));
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
    <div
      data-testid="compare-page"
      className={cn(
        "flex min-h-0 flex-col gap-3 overflow-hidden px-7 py-4",
        shareView
          ? "min-h-0 flex-1"
          : "h-[calc(100dvh-var(--shell-header-h,86px))] min-h-[560px]",
      )}
    >
      {/* Merged header row: stage nav + title + tab strip, then the
       *  chips, layout pills and export in the right cluster. */}
      <div className="flex flex-none flex-wrap items-center gap-x-4 gap-y-2 border-b border-rule pb-3">
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
        <h1 className="font-display text-2xl font-bold uppercase leading-none tracking-tight text-ink">
          <span className="text-led">STAGE {pad2(stageNumber)}</span>
          <span className="mx-2 text-whisper">·</span>
          <span>{bundle.stage_name}</span>
        </h1>
        <nav
          aria-label="Stage views"
          className="inline-flex overflow-hidden rounded-lg border border-rule bg-surface-2 p-0.5"
        >
          {/* Audit/Coach are operator-only surfaces (mutate state, need a
              session) - hidden on the anonymous share view (#700). */}
          {!shareView && (
            <button
              type="button"
              onClick={() => {
                // Compare is multi-shooter; pick the audio source (the
                // primary shown in this view) as the target shooter so
                // the user lands on the same camera they were watching.
                const target = audioSlug ?? orderedShooters[0]?.slug;
                if (target) navigate(href("audit", target, String(stageNumber)));
              }}
              className="inline-flex min-h-9 items-center rounded-md px-3.5 font-sans text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted hover:text-ink-2"
            >
              Audit
            </button>
          )}
          <span className="tab-pill-led-fill inline-flex min-h-9 items-center rounded-md px-3.5">
            Compare
          </span>
          {!shareView && (
            <button
              type="button"
              onClick={() => {
                const target = audioSlug ?? orderedShooters[0]?.slug;
                if (target) navigate(href("coach", target, String(stageNumber)));
              }}
              className="inline-flex min-h-9 items-center rounded-md px-3.5 font-sans text-[0.75rem] font-semibold uppercase tracking-[0.08em] text-muted hover:text-ink"
            >
              Coach
            </button>
          )}
        </nav>
        <div className="ml-auto flex flex-wrap items-center gap-3">
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
          <div className="inline-flex overflow-hidden rounded-lg border border-rule bg-surface-2 p-0.5">
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
          {/* Grid export ships with #328; disabled + badged like the
           *  Export page's compare ModeOption so the two surfaces agree.
           *  Operator-only affordance (mutates nothing yet, but the
           *  destination Export page needs a session) - hidden on the
           *  anonymous share view, same as Audit/Coach above (#700). */}
          {!shareView && (
            <Button
              type="button"
              variant="outline"
              disabled
              title="Multi-shooter grid export arrives with #328. Single-shooter export lives on the Export page."
            >
              <ArrowDownToLine className="size-3.5" />
              <span className="font-display uppercase tracking-[0.08em]">
                Export FCPXML
              </span>
              <span className="rounded border border-rule px-1.5 font-mono text-[0.625rem] font-semibold text-muted">
                #328
              </span>
            </Button>
          )}
        </div>
      </div>

      {/* Unfinished banner: when at least one shooter is playable, the
       *  grid renders the playable subset. Shooters without a cached
       *  trim are surfaced here so the user can rebuild the cache (when
       *  audit is done) or jump into audit (when nothing has run yet)
       *  without having to leave the page. */}
      {visibleShooters.length > 0 &&
      orderedShooters.some((s) => !s.video_ref) ? (
        <UnfinishedShootersBanner
          unfinished={orderedShooters.filter((s) => !s.video_ref)}
          onOpenInAudit={(slug) =>
            navigate(href("audit", slug, String(stageNumber)))
          }
          shareView={shareView}
          editDenied={editDenied}
        />
      ) : null}

      {/* Video zone + leaderboard rail share a bounded row */}
      <div className="flex min-h-0 flex-1 gap-4">
        {visibleShooters.length === 0 ? (
          <CompareEmptyState
            unfinished={orderedShooters.filter((s) => !s.video_ref)}
            onOpenInAudit={(slug) => {
              navigate(href("audit", slug, String(stageNumber)));
            }}
            shareView={shareView}
          />
        ) : (
          <>
            <div
              className={cn(
                "min-h-0 min-w-0 flex-1",
                layout === "stack"
                  ? "flex flex-col gap-3 overflow-y-auto"
                  : "grid gap-3",
              )}
              style={
                layout === "stack"
                  ? undefined
                  : layout === "grid"
                    ? {
                        gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
                        gridTemplateRows: `repeat(${Math.max(1, Math.ceil(visibleShooters.length / 2))}, minmax(0, 1fr))`,
                      }
                    : {
                        gridTemplateColumns: `repeat(${visibleShooters.length}, minmax(0, 1fr))`,
                        gridTemplateRows: "minmax(0, 1fr)",
                      }
              }
            >
              {visibleShooters.map((shooter) => (
                <VideoTile
                  key={shooter.slug}
                  shooter={shooter}
                  src={tileSrc(shooter)}
                  cams={camsBySlug[shooter.slug] ?? null}
                  camIndex={camIndexFor(shooter.slug)}
                  onPickCam={(index) =>
                    setCamIndexBySlug((prev) => ({ ...prev, [shooter.slug]: index }))
                  }
                  isAudio={audioSlug === shooter.slug}
                  fit={layout === "stack" ? "aspect" : "fill"}
                  onPickAudio={() => setAudioSlug(shooter.slug)}
                  onMount={(el) => setVideoRef(shooter.slug, el)}
                />
              ))}
            </div>
            <LeaderboardRail shooters={playableShooters} />
          </>
        )}
      </div>
      {visibleShooters.length > 0 ? (
        <TransportDock
          shooters={playableShooters}
          maxTime={maxStageTime}
          timeSinceBeep={timeSinceBeep}
          audioSlug={audioSlug}
          isPlaying={isPlaying}
          onTogglePlay={togglePlay}
          onScrub={scrubTo}
          onPickAudio={(slug) => setAudioSlug(slug)}
          momentT={urlMoment?.t ?? null}
          onCopyMoment={handleCopyMoment}
        />
      ) : null}
      <Snackbar snack={snack} onDismiss={() => setSnack(null)} />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Empty state -- no shooter has a usable trim for this stage yet             */
/* -------------------------------------------------------------------------- */

function UnfinishedShootersBanner({
  unfinished,
  onOpenInAudit,
  shareView,
  editDenied,
}: {
  unfinished: CompareShooterRecord[];
  onOpenInAudit: (slug: string) => void | Promise<void>;
  shareView: boolean;
  /** #756: rebuild-trim-caches is an edit-class write the mirror guard
   *  403s. Hidden alongside the share-view branch below - it's one
   *  action among several here, not the page's primary value. */
  editDenied: boolean;
}) {
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [doneSlugs, setDoneSlugs] = useState<Set<string>>(() => new Set());

  // A shooter with shots[] but no video_ref has finished audit; just
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
      // story is "reload the page"; the jobs rail surfaces progress).
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
        {shareView ? (
          <span>
            {unfinished.length === 1
              ? "shooter has"
              : "shooters have"}{" "}
            no comparison video for this stage yet.
          </span>
        ) : (
          <span>
            {unfinished.length === 1 ? "shooter has" : "shooters have"} no
            cached trim for this stage.
          </span>
        )}
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
              {/* Both CTAs are operator actions (rebuild POSTs, audit
                  needs a session) - a share viewer just sees the name
                  and the "missing footage" status above (#700). Rebuild
                  is additionally hidden (not disabled) on a read-only
                  mirror (#756): one action among several here, on a
                  page whose primary value is reading. */}
              {shareView ? null : auditedButUncached ? (
                queued ? (
                  <span className="text-[0.6875rem] uppercase tracking-[0.08em] text-done">
                    Build queued -- check Jobs
                  </span>
                ) : editDenied ? null : (
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
      {!shareView && errorMsg ? (
        <div className="mt-2 text-xs text-led">{errorMsg}</div>
      ) : null}
    </div>
  );
}

function CompareEmptyState({
  unfinished,
  onOpenInAudit,
  shareView,
}: {
  unfinished: CompareShooterRecord[];
  onOpenInAudit: (slug: string) => void | Promise<void>;
  shareView: boolean;
}) {
  // Compare uses the lossless export if present, otherwise the audit-mode
  // short-GOP cache; both come out of the per-shooter trim + audit pass.
  // So a missing video_ref means audit isn't finished for that shooter
  // (no primary, no beep, or the trim cache hasn't been built). Share
  // viewers get viewer-neutral copy instead of audit instructions - they
  // cannot act on any of this (#700).
  return (
    <div className="flex min-h-0 flex-1 flex-col justify-center rounded-2xl border border-rule-strong bg-surface px-6 py-10 text-sm text-muted">
      {shareView ? (
        <p className="text-center text-ink-2">
          The match owner hasn't prepared comparison video for this stage
          yet.
        </p>
      ) : (
        <p className="text-center text-ink-2">
          Compare needs an audited primary cam from each shooter.
        </p>
      )}
      {!shareView && unfinished.length > 0 && (
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
        title={`${shooter.name} - ${visible ? "hide" : "show"}`}
        aria-label={`${shooter.name} - ${visible ? "hide" : "show"}`}
        aria-pressed={visible}
      >
        {initials(shooter.name)}
      </button>
      <button
        type="button"
        onClick={onPickAudio}
        title={isAudio ? "Audio source" : "Use as audio source"}
        aria-label={`${shooter.name} - audio source`}
        aria-pressed={isAudio}
        className={cn(
          "inline-flex size-6 items-center justify-center rounded-full transition-colors",
          isAudio
            ? "bg-led-fill text-ink shadow-[0_0_10px_var(--color-led-glow)]"
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
  src,
  cams,
  camIndex,
  onPickCam,
  isAudio,
  fit,
  onPickAudio,
  onMount,
}: {
  shooter: CompareShooterRecord;
  src: string | null;
  cams: CoachVideoEntry[] | null;
  camIndex: number;
  onPickCam: (index: number) => void;
  isAudio: boolean;
  fit: "fill" | "aspect";
  onPickAudio: () => void;
  onMount: (el: HTMLVideoElement | null) => void;
}) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-xl border bg-bg-glow",
        fit === "fill" ? "flex min-h-0 flex-col" : "shrink-0",
        isAudio
          ? "border-led shadow-[0_0_0_1px_var(--color-led-deep),0_0_16px_var(--color-led-glow)]"
          : "border-rule-strong",
      )}
    >
      <div className="flex flex-none items-center gap-2 border-b border-rule bg-surface-2 px-3 py-1.5">
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
        <span className="ml-auto flex items-center gap-2">
          {cams && cams.length > 1 ? (
            <span className="relative inline-flex items-center">
              <select
                value={camIndex}
                onChange={(e) => onPickCam(Number(e.target.value))}
                aria-label={`${shooter.name} - camera`}
                className="cursor-pointer appearance-none bg-transparent pr-4 font-mono text-[0.625rem] font-bold uppercase tracking-[0.1em] text-muted transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led"
              >
                {cams.map((c, i) => (
                  <option key={c.path} value={i} disabled={c.beep_in_clip == null}>
                    {i === 0 ? "Primary" : `Cam ${i + 1}`}
                  </option>
                ))}
              </select>
              <ChevronDown
                aria-hidden
                className="pointer-events-none absolute right-0 size-3 text-subtle"
              />
            </span>
          ) : null}
          {isAudio && (
            <span className="inline-flex items-center gap-1 rounded border border-led-deep bg-led px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.14em] text-ink shadow-[0_0_8px_var(--color-led-glow)]">
              <Volume2 className="size-2.5" />
              Audio
            </span>
          )}
        </span>
      </div>
      <div className={cn("relative", fit === "fill" && "min-h-0 flex-1 bg-black")}>
        {src ? (
          <video
            ref={onMount}
            src={src}
            preload="metadata"
            playsInline
            controls={false}
            className={cn(
              fit === "fill"
                ? "h-full w-full object-contain"
                : "aspect-video w-full bg-black",
            )}
            onClick={(e) => {
              if (!isAudio) {
                onPickAudio();
                e.preventDefault();
              }
            }}
          />
        ) : (
          <div
            className={cn(
              "flex items-center justify-center bg-surface-3 text-sm text-muted",
              fit === "fill" ? "h-full" : "aspect-video",
            )}
          >
            No trim yet
          </div>
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
