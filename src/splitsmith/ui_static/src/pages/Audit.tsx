/**
 * Audit screen v2 (#15).
 *
 * Through Step 5 -- save flow + audit_events log persisted.
 *
 * Contract:
 *   - Audit truth = primary's audio. The waveform is always the primary's.
 *   - The active <video> drives playback time.
 *   - The Audit page exposes "primary timeline" times to children.
 *   - Markers live on the primary's timeline. Switching tabs offsets only
 *     the video element; markers don't move.
 *
 * Marker model:
 *   - Each candidate from `_candidates_pending_audit.candidates` becomes a
 *     marker. If the same candidate_number is in `shots[]`, kind="detected"
 *     (kept); otherwise kind="rejected".
 *   - Each shot with no candidate_number (or source="manual") becomes a
 *     standalone manual marker.
 *
 * Step 4 adds the bottom stepper (◀ shot N/M ▶) and the right-side list
 * drawer (toggled with `L`). Step 5 wires save: Cmd+S writes the audit
 * JSON to ``<project>/audit/stage<N>.json`` (atomic, with a .bak), and a
 * silent auto-save fires on stage switch so the user never loses work.
 * Edits accrete into ``audit_events[]`` -- an append-only history that
 * makes the saved JSON self-explaining months later.
 *
 * Right-click context menu is still deferred to Step 7 polish.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  useNavigate,
  useOutletContext,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  Crosshair,
  Eye,
  HelpCircle,
  ListChecks,
  Loader2,
  MoreHorizontal,
  Pause,
  Play,
  Repeat,
} from "lucide-react";

import { AnomalyChips } from "@/components/audit/AnomalyChips";
import { AnomalyPins } from "@/components/audit/AnomalyPins";
import { BeepStatusChip } from "@/components/audit/BeepStatusChip";
import { PrereqGate } from "@/components/audit/PrereqGate";
import {
  CamSyncPill,
  type CamSyncState,
} from "@/components/audit/CamSyncPill";
import { SessionSummary } from "@/components/audit/SessionSummary";
import { SyncBanner } from "@/components/audit/SyncBanner";
import {
  PipBay,
  pipFootprintWidth,
  type PipCorner,
  type PipSize,
} from "@/components/audit/PipBay";
import { StageActionBar } from "@/components/audit/StageActionBar";
import { StageChipRail } from "@/components/audit/StageChipRail";
import {
  DEFAULT_FILTERS,
  FilterBar,
  ZoomControls,
  type MarkerFilters,
  visibleKindsFromFilters,
  zoomToPixelsPerSecond,
} from "@/components/AuditControls";
import { HelpOverlay } from "@/components/HelpOverlay";
import { ListDrawer } from "@/components/ListDrawer";
import { MarkerLayer, type AuditMarker } from "@/components/MarkerLayer";
import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { ShotStepper } from "@/components/ShotStepper";
import { VideoPanel } from "@/components/VideoPanel";
import { Waveform } from "@/components/Waveform";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/Kbd";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ApiError,
  api,
  type AuditEvent,
  type AuditShot,
  type Job,
  type MatchProject,
  type PeaksResult,
  type StageAudit,
  type StageVideo,
} from "@/lib/api";
import { detectAnomalies, keptShotsFromMarkers } from "@/lib/anomalies";
import { isTypingTextTarget, useBlurOnPointerClick } from "@/lib/audit-input";
import { computeAuditNextStep } from "@/lib/audit-next-step";
import { cn } from "@/lib/utils";

const PEAK_BINS = 1500;
const MAX_UNDO = 50;
const K_AUTO_PROGRESS_KEY = "splitsmith.audit.k_auto_progress";
// v2: sizes rebalanced 2026-05-17 to match design (smaller, less intrusive).
// Bumping the key forces every operator back onto the new defaults exactly
// once; subsequent tweaks of corner/size still persist normally.
const PIP_LAYOUT_KEY = "splitsmith.audit.pip.v2";

interface PipLayoutState {
  corner: PipCorner;
  size: PipSize;
  hidden: boolean;
}

const PIP_LAYOUT_DEFAULT: PipLayoutState = { corner: "br", size: "S", hidden: false };

function loadPipLayout(): PipLayoutState {
  if (typeof window === "undefined") return PIP_LAYOUT_DEFAULT;
  try {
    const raw = window.localStorage.getItem(PIP_LAYOUT_KEY);
    if (!raw) return PIP_LAYOUT_DEFAULT;
    const parsed = JSON.parse(raw) as Partial<PipLayoutState>;
    return {
      corner:
        parsed.corner === "tl" || parsed.corner === "tr" || parsed.corner === "bl" || parsed.corner === "br"
          ? parsed.corner
          : PIP_LAYOUT_DEFAULT.corner,
      size:
        parsed.size === "S" || parsed.size === "M" || parsed.size === "L"
          ? parsed.size
          : PIP_LAYOUT_DEFAULT.size,
      hidden: parsed.hidden === true,
    };
  } catch {
    return PIP_LAYOUT_DEFAULT;
  }
}

const NEXT_CORNER: Record<PipCorner, PipCorner> = { tl: "tr", tr: "br", br: "bl", bl: "tl" };
const NEXT_SIZE: Record<PipSize, PipSize> = { S: "M", M: "L", L: "S" };

export function Audit() {
  // ShooterScopedRoute canonicalises every Audit entry to /audit/:slug/:stage
  // (or /audit/:slug when no stage yet), so slug is always populated by the
  // time we render. The slug also keys the component remount on switch.
  const { slug: slugParam, stage: stageParam } = useParams<{
    slug?: string;
    stage?: string;
  }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const doneParam = searchParams.get("done");
  const nextShooterParam = searchParams.get("next");

  // Drop button / chip focus after a mouse click so the next Space press
  // toggles playback instead of re-clicking the last-touched control.
  useBlurOnPointerClick();

  const [project, setProject] = useState<MatchProject | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);
  // Shooters come from the MatchShell outlet context now -- one fetch
  // per match, shared with the breadcrumb chip strip. No own fetch.
  const outletCtx = useOutletContext<MatchShellOutletContext | undefined>();
  const shooters = outletCtx?.shooters ?? [];
  // ShooterScopedRoute remounts this whole component on slug change so we
  // no longer need explicit switching state -- the URL change is the
  // single source of truth.

  const [peaks, setPeaks] = useState<PeaksResult | null>(null);
  const [peaksLoading, setPeaksLoading] = useState(false);
  const [peaksError, setPeaksError] = useState<string | null>(null);

  const [audit, setAudit] = useState<StageAudit | null>(null);

  const [markers, setMarkers] = useState<AuditMarker[]>([]);
  const undoStackRef = useRef<AuditMarker[][]>([]);
  const [focusedMarkerId, setFocusedMarkerId] = useState<string | null>(null);

  // Stepper navigates kept shots (detected + manual) in time order. The
  // index is decoupled from the playhead -- scrubbing doesn't reset it.
  const [currentShotIndex, setCurrentShotIndex] = useState(0);
  const [showDrawer, setShowDrawer] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  // Save flow (Step 5).
  // sessionEventsRef accumulates audit_events for this session; appended
  // to the saved JSON's audit_events[] on save and cleared. isDirtyRef
  // controls whether stage-switch / Cmd+S actually fires a write.
  const sessionEventsRef = useRef<AuditEvent[]>([]);
  const isDirtyRef = useRef(false);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>({ kind: "idle" });

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [activeVideoIndex, setActiveVideoIndex] = useState(0);
  const [loopMode, setLoopMode] = useState(false);
  const [gridMode, setGridMode] = useState(true);
  // Stable refs used by grid-mode callbacks to avoid stale closures without
  // adding them to useCallback / useEffect dep arrays.
  const isPlayingRef = useRef(false);
  const currentTimeRef = useRef(0);
  // Map of secondary video path -> element, populated by SecondarySlot mounts.
  const secondaryRefsMap = useRef<Map<string, HTMLVideoElement>>(new Map());
  // Map of secondary video path -> beep offset (secondary.beep_time - auditBeep).
  // Rebuilt whenever videos or auditBeep change so the rAF loop reads stable values.
  const secondaryOffsetsRef = useRef<Map<string, number>>(new Map());
  // Auto-advance to the next visible marker after K toggles a candidate.
  // Default on (FCP-style "mark and move"); persisted across sessions
  // because the user audits in long flow blocks and shouldn't have to
  // re-enable it every reload.
  const [kAutoProgress, setKAutoProgress] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    const v = window.localStorage.getItem(K_AUTO_PROGRESS_KEY);
    return v == null ? true : v === "1";
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(K_AUTO_PROGRESS_KEY, kAutoProgress ? "1" : "0");
  }, [kAutoProgress]);

  // Floating PiP layout: corner, size step, hidden flag. Persisted per
  // localStorage so the operator's preferred bay position survives
  // reloads and stage switches. Hotkeys V / Alt+V / D mutate this from
  // the global keyboard handler.
  const [pipLayout, setPipLayout] = useState<PipLayoutState>(loadPipLayout);
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(PIP_LAYOUT_KEY, JSON.stringify(pipLayout));
    } catch {
      /* quota / private mode: best-effort persistence */
    }
  }, [pipLayout]);
  const togglePipHidden = useCallback(
    () => setPipLayout((p) => ({ ...p, hidden: !p.hidden })),
    [],
  );
  const cyclePipSize = useCallback(
    () => setPipLayout((p) => ({ ...p, size: NEXT_SIZE[p.size] })),
    [],
  );
  const cyclePipCorner = useCallback(
    () => setPipLayout((p) => ({ ...p, corner: NEXT_CORNER[p.corner] })),
    [],
  );
  const showPipBay = useCallback(
    () => setPipLayout((p) => ({ ...p, hidden: false })),
    [],
  );

  // Sync mode state lives up here so callbacks below can close over it;
  // the handlers that actually call overrideBeepForVideo are declared
  // further down (after slug + stageNumber are in scope).
  const [syncMode, setSyncMode] = useState<StageVideo | null>(null);
  const [syncCandidate, setSyncCandidate] = useState<number | null>(null);
  const [syncBusy, setSyncBusy] = useState(false);
  const startSync = useCallback((cam: StageVideo) => {
    setSyncMode(cam);
    setSyncCandidate(null);
  }, []);
  const cancelSync = useCallback(() => {
    setSyncMode(null);
    setSyncCandidate(null);
    setSyncBusy(false);
  }, []);
  // Anchor for loop-to-start semantics: the audit-timeline position
  // playback last started from (or where the user last scrubbed). On
  // pause / end-of-clip while loopMode is on, the playhead snaps back
  // here. Matches the old review SPA's "Loop: pause snaps the playhead
  // back to where playback started" behavior.
  const loopAnchorRef = useRef<number | null>(null);

  // Keep stable refs in sync so callbacks that can't take deps use them.
  useEffect(() => { isPlayingRef.current = isPlaying; }, [isPlaying]);
  useEffect(() => { currentTimeRef.current = currentTime; }, [currentTime]);

  const [filters, setFilters] = useState<MarkerFilters>(DEFAULT_FILTERS);
  // ``null`` = fit-to-width; numeric multiplier scales pixels-per-second
  // relative to fit. Reset on stage change.
  const [zoom, setZoom] = useState<number | null>(null);
  // Callback ref so we attach the ResizeObserver exactly when the
  // wrapper mounts. A useEffect-based ref + ``[]`` deps wouldn't fire
  // again once peaks load and the conditional render finally inserts
  // the div, so viewportWidth would stay at 0 and zoom would be a
  // no-op (zoomToPixelsPerSecond returns null when viewport is 0).
  const [waveformViewport, setWaveformViewport] = useState(0);
  const waveformObserverRef = useRef<ResizeObserver | null>(null);
  const waveformWrapperRef = useCallback((el: HTMLDivElement | null) => {
    waveformObserverRef.current?.disconnect();
    if (!el) {
      waveformObserverRef.current = null;
      return;
    }
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.floor(entry.contentRect.width);
        if (w > 0) setWaveformViewport(w);
      }
    });
    observer.observe(el);
    setWaveformViewport(Math.floor(el.getBoundingClientRect().width));
    waveformObserverRef.current = observer;
  }, []);
  const rafRef = useRef<number | null>(null);

  // ShooterScopedRoute redirects to /shooters when slug is missing, so by
  // the time this renders ``slugParam`` is always a non-empty string.
  const slug = slugParam!;

  const stageNumber = useMemo(() => {
    if (stageParam == null) return null;
    const n = Number.parseInt(stageParam, 10);
    return Number.isFinite(n) ? n : null;
  }, [stageParam]);

  // Load project once.
  useEffect(() => {
    let alive = true;
    api
      .getProject(slug)
      .then((p) => {
        if (alive) setProject(p);
      })
      .catch((err) => {
        if (alive) setProjectError(err instanceof ApiError ? err.detail : String(err));
      });
    return () => {
      alive = false;
    };
  }, []);

  // Resolved automation: feeds the CamSyncPill's "needs sync" gate so it
  // reads from the same threshold the HITL queue uses. Server-resolved
  // (CLI > project > global > default); we only consume the result.
  // Falls back to the in-code AutomationSettings default (0.95) until
  // the request lands.
  const [beepLowConfThreshold, setBeepLowConfThreshold] = useState(0.95);
  useEffect(() => {
    let alive = true;
    api
      .getAutomation(slug)
      .then((r) => {
        if (alive) {
          setBeepLowConfThreshold(r.settings.beep_low_confidence_threshold);
        }
      })
      .catch(() => {
        /* keep default -- automation endpoint failures aren't fatal */
      });
    return () => {
      alive = false;
    };
  }, []);

  // Switching shooter is a route change now (#353 phase 1). The chip
  // strip uses <Link to=/audit/:newSlug/:stage>; ShooterScopedRoute
  // canonicalises the URL and remounts this component with key={slug},
  // which resets every piece of local state (peaks, audit JSON, markers,
  // video refs, undo stack) without us having to thread reset logic
  // through every effect.

  const stagesWithPrimary = useMemo(() => {
    if (!project) return [];
    return project.stages.filter((s) => s.videos.some((v) => v.role === "primary"));
  }, [project]);

  // Stable identity for memoisation: a fresh array each render churns
  // any consumer that keys on the items.
  const stageSelectorOptions = useMemo(
    () =>
      stagesWithPrimary.map((s) => ({
        stageNumber: s.stage_number,
        stageName: s.stage_name,
      })),
    [stagesWithPrimary],
  );

  // Stage rail items carry the active flag; per-stage done state needs a
  // server endpoint we don't have yet (stages_audited is a count, not a
  // set), so completed stages render as "todo" for now. Tracked as a
  // follow-up to phase 2 of the audit redesign.
  const stageRailItems = useMemo(
    () =>
      stageSelectorOptions.map((s) => ({
        stageNumber: s.stageNumber,
        stageName: s.stageName,
        status: (s.stageNumber === stageNumber ? "active" : "todo") as
          | "done"
          | "active"
          | "todo",
      })),
    [stageSelectorOptions, stageNumber],
  );

  // Neighbour stage numbers for prev/next nav. `null` at the boundaries
  // so the header buttons disable instead of wrapping -- accidental wrap
  // is worse than a dead key when the user is moving fast. Cross-shooter
  // chaining is handled by computeAuditNextStep; these are strictly
  // within-shooter jumps for `[` / `]` and the header chevrons.
  const { prevStageNumber, nextStageNumber } = useMemo(() => {
    if (stageNumber == null || stageSelectorOptions.length === 0) {
      return { prevStageNumber: null, nextStageNumber: null };
    }
    const idx = stageSelectorOptions.findIndex((s) => s.stageNumber === stageNumber);
    if (idx === -1) return { prevStageNumber: null, nextStageNumber: null };
    return {
      prevStageNumber: idx > 0 ? stageSelectorOptions[idx - 1].stageNumber : null,
      nextStageNumber:
        idx < stageSelectorOptions.length - 1
          ? stageSelectorOptions[idx + 1].stageNumber
          : null,
    };
  }, [stageSelectorOptions, stageNumber]);

  useEffect(() => {
    if (stageNumber != null) return;
    if (stagesWithPrimary.length === 0) return;
    const target = slugParam
      ? `/audit/${slugParam}/${stagesWithPrimary[0].stage_number}`
      : `/audit/${stagesWithPrimary[0].stage_number}`;
    navigate(target, { replace: true });
  }, [stageNumber, stagesWithPrimary, navigate, slugParam]);

  const stage = useMemo(() => {
    if (!project || stageNumber == null) return null;
    return project.stages.find((s) => s.stage_number === stageNumber) ?? null;
  }, [project, stageNumber]);

  const videos = useMemo<StageVideo[]>(() => {
    if (!stage) return [];
    const primary = stage.videos.find((v) => v.role === "primary");
    const secondaries = stage.videos
      .filter((v) => v.role === "secondary")
      .slice()
      .sort((a, b) => a.added_at.localeCompare(b.added_at));
    return primary ? [primary, ...secondaries] : [...secondaries];
  }, [stage]);

  const primary = videos[0] ?? null;
  const activeVideo = videos[activeVideoIndex] ?? primary;
  const primaryBeep = primary?.beep_time ?? null;

  // Per-cam buzzer sync state, surfaced as the CamSyncPill on each tile
  // inside the PipBay.
  //
  //   no_beep          -- never detected anything
  //   manual           -- operator overrode the buzzer time
  //   low_confidence   -- auto-detected, confidence below the
  //                       beep_low_confidence_threshold automation
  //                       setting (same gate the HITL queue uses),
  //                       AND the operator hasn't acked it yet
  //                       (beep_reviewed === false). Reviewed
  //                       low-confidence beeps are treated as
  //                       synced -- the operator has eyeballed and
  //                       confirmed.
  //   synced           -- everything else.
  const camSyncStates = useMemo<CamSyncState[]>(() => {
    return videos.map((v) => {
      if (v.beep_time == null) return "no_beep";
      if (v.beep_source === "manual") return "manual";
      if (
        v.beep_confidence != null &&
        v.beep_confidence < beepLowConfThreshold &&
        !v.beep_reviewed
      ) {
        return "low_confidence";
      }
      return "synced";
    });
  }, [videos, beepLowConfThreshold]);
  const camsNeedingSync = camSyncStates.filter(
    (s) => s === "low_confidence" || s === "no_beep",
  ).length;
  const activeBeep = activeVideo?.beep_time ?? null;
  // Beep position **on the audit timeline** -- this is the X where the
  // waveform draws the dashed beep line and where audit-time = beep-time.
  // When the server is serving trimmed audio, peaks.beep_time is the
  // clip-local beep (typically near the trim buffer of 5 s). When the
  // server falls back to full-source audio, peaks.beep_time mirrors
  // primary.beep_time. Either way, this value is the correct anchor.
  const auditBeep = peaks?.beep_time ?? primaryBeep;

  // Rebuild the secondary offset table whenever videos or auditBeep change.
  // The rAF tick loop reads this map without needing it in its dep array.
  //
  // Also catch up any already-mounted secondaries: the SecondarySlot ref
  // callback fires during the commit phase (before this effect), so on the
  // initial render after data loads -- and on stage change -- the new
  // <video> elements land in secondaryRefsMap *before* this map is
  // populated. Without a re-seek here they stay parked at source time 0
  // and play out of sync once the user hits play.
  useEffect(() => {
    const map = new Map<string, number>();
    if (auditBeep != null) {
      for (const v of videos.slice(1)) {
        if (v.beep_time != null) {
          map.set(v.path, v.beep_time - auditBeep);
        }
      }
    }
    secondaryOffsetsRef.current = map;
    for (const [path, sv] of secondaryRefsMap.current) {
      const off = map.get(path);
      if (off == null) continue;
      const target = currentTimeRef.current + off;
      if (sv.readyState >= 1) {
        sv.currentTime = target;
      } else {
        sv.addEventListener(
          "loadedmetadata",
          () => {
            sv.currentTime = target;
          },
          { once: true },
        );
      }
    }
  }, [videos, auditBeep]);

  const beepOffset = useMemo(() => {
    // Primary tab: served clip *is* the audit timeline (trimmed primary
    // serves trimmed; untrimmed primary serves source). Offset is zero.
    if (activeVideoIndex === 0) return 0;
    // Secondary tab: served clip is always the source for now (per-video
    // trimming isn't wired through the production UI yet). Map audit time
    // T to secondary source time via beep alignment.
    if (activeBeep == null || auditBeep == null) return 0;
    return activeBeep - auditBeep;
  }, [activeBeep, auditBeep, activeVideoIndex]);

  // Reset state on stage change. Anything dirty has already been
  // auto-saved in the StageSelector handler before navigating.
  useEffect(() => {
    setCurrentTime(0);
    setIsPlaying(false);
    setActiveVideoIndex(0);
    setFocusedMarkerId(null);
    setCurrentShotIndex(0);
    setShowDrawer(false);
    setZoom(null);
    setFilters(DEFAULT_FILTERS);
    undoStackRef.current = [];
    sessionEventsRef.current = [];
    isDirtyRef.current = false;
    setSaveStatus({ kind: "idle" });
    setGridMode(true);
    // Don't clear secondaryRefsMap here. Refs attach during commit before
    // this effect runs, so a clear() would wipe the freshly-mounted new
    // stage's <video> elements. SecondarySlot's unmount path calls
    // setRef(null) -> handleSecondaryRef deletes stale entries, which is
    // sufficient to keep the map clean across stage transitions.
    const v = videoRef.current;
    if (v) {
      v.pause();
      v.currentTime = 0;
    }
  }, [stageNumber]);

  // Load peaks.
  useEffect(() => {
    if (stageNumber == null || !primary) {
      setPeaks(null);
      return;
    }
    let alive = true;
    setPeaksLoading(true);
    setPeaksError(null);
    api
      .getStagePeaks(slug, stageNumber, PEAK_BINS)
      .then((p) => {
        if (alive) setPeaks(p);
      })
      .catch((err) => {
        if (alive) {
          setPeaksError(err instanceof ApiError ? err.detail : String(err));
          setPeaks(null);
        }
      })
      .finally(() => {
        if (alive) setPeaksLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [slug, stageNumber, primary]);

  // Load audit JSON. 404 means "no audit yet" -- start with empty markers.
  useEffect(() => {
    if (stageNumber == null) {
      setAudit(null);
      return;
    }
    let alive = true;
    api
      .getStageAudit(slug, stageNumber)
      .then((a) => {
        if (!alive) return;
        setAudit(a);
        setMarkers(deriveMarkers(a));
      })
      .catch(() => {
        if (!alive) return;
        setAudit(null);
        setMarkers([]);
      });
    return () => {
      alive = false;
    };
  }, [slug, stageNumber]);

  // Tab change: re-seek the new <video> to the audit-timeline position.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const targetVideoTime = currentTime + beepOffset;
    const seekWhenReady = () => {
      if (Number.isFinite(targetVideoTime) && targetVideoTime >= 0) {
        v.currentTime = targetVideoTime;
      }
      if (isPlaying) void v.play();
    };
    if (v.readyState >= 1) {
      seekWhenReady();
    } else {
      v.addEventListener("loadedmetadata", seekWhenReady, { once: true });
      return () => v.removeEventListener("loadedmetadata", seekWhenReady);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeVideoIndex]);

  useEffect(() => {
    if (!isPlaying) return;
    const tick = () => {
      const v = videoRef.current;
      if (v) {
        const auditT = v.currentTime - beepOffset;
        const dur = peaks?.duration ?? null;
        // Loop wrap at end-of-clip -- snap to the anchor (where play
        // started) so the user can hear a section repeatedly without
        // re-clicking. Falls back to 0 on the rare case the anchor is
        // unset (loop toggled mid-playback before any anchor recorded).
        if (loopMode && dur != null && auditT >= dur - 0.05) {
          const target = loopAnchorRef.current ?? 0;
          v.currentTime = target + beepOffset;
          setCurrentTime(target);
          // Snap secondaries to the loop anchor too.
          for (const [path, sv] of secondaryRefsMap.current) {
            const off = secondaryOffsetsRef.current.get(path);
            if (off != null) sv.currentTime = target + off;
          }
        } else {
          setCurrentTime(auditT);
          // Drift-correct secondaries: nudge if >50 ms off. Auto-resume is
          // handled event-driven via the primary's "timeupdate" listener -- not
          // here -- to avoid seek-thrashing at 60 fps.
          for (const [path, sv] of secondaryRefsMap.current) {
            const off = secondaryOffsetsRef.current.get(path);
            if (off == null) continue;
            const expected = auditT + off;
            if (!sv.paused && Math.abs(sv.currentTime - expected) > 0.05) {
              sv.currentTime = expected;
            }
          }
        }
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    };
  }, [isPlaying, beepOffset, loopMode, peaks]);

  // Master/slave sync: invoked from the primary's <video onTimeUpdate>.
  // Browser-throttled to ~4 Hz, which is the right rate for reconciliation.
  // For each secondary: if the primary is inside the secondary's content
  // range and it's paused, seek+play it; if out of range and playing, pause.
  // Standard multi-video sync recipe -- rAF would seek-thrash.
  const handlePrimaryTimeUpdate = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    const auditT = v.currentTime - beepOffset;
    for (const [path, sv] of secondaryRefsMap.current) {
      const off = secondaryOffsetsRef.current.get(path);
      if (off == null) continue;
      const expected = auditT + off;
      const dur = Number.isFinite(sv.duration) ? sv.duration : null;
      const inRange = expected >= 0 && (dur == null || expected <= dur);
      if (inRange && sv.paused && isPlayingRef.current) {
        sv.currentTime = expected;
        void sv.play().catch(() => {});
      } else if (!inRange && !sv.paused) {
        sv.pause();
      }
    }
  }, [beepOffset]);

  const handleScrub = useCallback(
    (primaryTime: number) => {
      const v = videoRef.current;
      if (v) v.currentTime = primaryTime + beepOffset;
      setCurrentTime(primaryTime);
      // Manual scrub re-anchors the loop. Without this, hitting R, then
      // dragging to a candidate, then play-pausing would yank the
      // playhead back to the OLD anchor instead of the new one.
      loopAnchorRef.current = primaryTime;
      // Seek all secondaries to the equivalent position. The primary's
      // timeupdate listener will resume any that are in their content range.
      for (const [path, sv] of secondaryRefsMap.current) {
        const off = secondaryOffsetsRef.current.get(path);
        if (off == null) continue;
        const expected = primaryTime + off;
        const dur = Number.isFinite(sv.duration) ? sv.duration : null;
        const inRange = expected >= 0 && (dur == null || expected <= dur);
        if (inRange) {
          sv.currentTime = expected;
          if (isPlayingRef.current && sv.paused) void sv.play().catch(() => {});
        }
      }
    },
    [beepOffset],
  );

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) {
      // Starting playback -- record the anchor in audit-timeline coords.
      loopAnchorRef.current = v.currentTime - beepOffset;
      void v.play();
      for (const sv of secondaryRefsMap.current.values()) {
        void sv.play();
      }
      setIsPlaying(true);
    } else {
      v.pause();
      for (const sv of secondaryRefsMap.current.values()) {
        sv.pause();
      }
      setIsPlaying(false);
      // Loop semantics: pause snaps back to where play started.
      if (loopMode && loopAnchorRef.current != null) {
        const target = loopAnchorRef.current;
        v.currentTime = target + beepOffset;
        setCurrentTime(target);
        for (const [path, sv] of secondaryRefsMap.current) {
          const off = secondaryOffsetsRef.current.get(path);
          if (off != null) sv.currentTime = target + off;
        }
      }
    }
  }, [beepOffset, loopMode]);

  // ---- Marker mutators (push prev state to undo stack) -------------------

  const recordEvent = useCallback((kind: string, payload: Record<string, unknown>) => {
    sessionEventsRef.current.push({
      ts: new Date().toISOString(),
      kind,
      payload,
    });
    isDirtyRef.current = true;
  }, []);

  // ---- Grid mode callbacks (#128) -----------------------------------------

  // Called by VideoPanel's SecondarySlot when a secondary video mounts/unmounts.
  const handleSecondaryRef = useCallback((path: string, el: HTMLVideoElement | null) => {
    if (el) {
      secondaryRefsMap.current.set(path, el);
      const off = secondaryOffsetsRef.current.get(path);
      if (off != null) {
        const target = currentTimeRef.current + off;
        if (el.readyState >= 1) {
          el.currentTime = target;
        } else {
          el.addEventListener("loadedmetadata", () => { el.currentTime = target; }, { once: true });
        }
        if (isPlayingRef.current) void el.play();
      }
    } else {
      secondaryRefsMap.current.delete(path);
    }
  }, []);

  // Buffering events are no-ops at the page level: each secondary plays
  // independently. The primary's timeupdate listener reconciles state on the
  // fly, so a secondary that stalls or runs off the end of its source just
  // pauses naturally and the primary keeps going. SecondarySlot still shows
  // its own buffering overlay locally.
  const handleSecondaryBuffering = useCallback(
    (_path: string, _isBuffering: boolean) => {},
    [],
  );

  const handleGridModeToggle = useCallback(() => {
    setGridMode((prev) => {
      if (!prev) {
        // Entering grid mode: lock to primary so beepOffset stays at 0.
        setActiveVideoIndex(0);
      } else {
        // Leaving grid mode: clean up secondary refs.
        secondaryRefsMap.current.clear();
      }
      return !prev;
    });
  }, []);

  // ---- Marker mutators (push prev state to undo stack) -------------------

  const mutate = useCallback((next: AuditMarker[]) => {
    setMarkers((prev) => {
      undoStackRef.current.push(prev);
      if (undoStackRef.current.length > MAX_UNDO) undoStackRef.current.shift();
      return next;
    });
    isDirtyRef.current = true;
  }, []);

  const undo = useCallback(() => {
    setMarkers((curr) => {
      const prev = undoStackRef.current.pop();
      return prev ?? curr;
    });
  }, []);

  const handleMarkerClick = useCallback(
    (m: AuditMarker) => {
      if (m.kind === "manual") return; // toggle is meaningless for manual markers
      const next = m.kind === "detected" ? "rejected" : "detected";
      recordEvent(next === "detected" ? "marker_kept" : "marker_rejected", {
        id: m.id,
        time: m.time,
        candidate_number: m.candidateNumber,
      });
      mutate(markers.map((x) => (x.id === m.id ? { ...x, kind: next } : x)));
    },
    [markers, mutate, recordEvent],
  );

  const handleMarkerDelete = useCallback(
    (m: AuditMarker) => {
      if (m.kind === "manual") {
        recordEvent("marker_deleted", { id: m.id, time: m.time, kind: m.kind });
        mutate(markers.filter((x) => x.id !== m.id));
      } else if (m.kind === "detected") {
        recordEvent("marker_rejected", {
          id: m.id,
          time: m.time,
          candidate_number: m.candidateNumber,
        });
        mutate(markers.map((x) => (x.id === m.id ? { ...x, kind: "rejected" } : x)));
      }
    },
    [markers, mutate, recordEvent],
  );

  // Drag / nudge gestures are bracketed by the MarkerLayer:
  //   onTimeChangeBegin -> snapshot the pre-edit markers list
  //   onTimeChange      -> live-mutate state for visual feedback (no undo push)
  //   onTimeChangeCommit-> push exactly one undo entry + one audit event
  //
  // editingSnapshotRef holds the markers snapshot taken at Begin so the
  // Commit can recover the pre-edit time without depending on the React
  // state at the moment of the closure.
  const editingSnapshotRef = useRef<{
    id: string;
    fromMarkers: AuditMarker[];
    fromTime: number;
  } | null>(null);

  const handleMarkerTimeChange = useCallback((id: string, time: number) => {
    // Live update for visual feedback only. Undo push happens at Commit.
    setMarkers((prev) => prev.map((x) => (x.id === id ? { ...x, time } : x)));
    isDirtyRef.current = true;
  }, []);

  const handleMarkerTimeChangeBegin = useCallback((id: string) => {
    setMarkers((prev) => {
      const target = prev.find((x) => x.id === id);
      editingSnapshotRef.current = {
        id,
        fromMarkers: prev,
        fromTime: target?.time ?? 0,
      };
      return prev;
    });
  }, []);

  const handleMarkerTimeChangeCommit = useCallback((id: string, time: number) => {
    const snap = editingSnapshotRef.current;
    editingSnapshotRef.current = null;
    if (!snap || snap.id !== id) return;
    if (snap.fromTime === time) return; // no-op gesture; don't dirty undo
    undoStackRef.current.push(snap.fromMarkers);
    if (undoStackRef.current.length > MAX_UNDO) undoStackRef.current.shift();
    sessionEventsRef.current.push({
      ts: new Date().toISOString(),
      kind: "marker_time_changed",
      payload: { id, from_time: snap.fromTime, to_time: time },
    });
    isDirtyRef.current = true;
  }, []);

  // Alt+Arrow nudge burst (page-level handler -- fires whether or not a
  // marker has DOM focus). Uses the same begin/commit bracketing so a
  // run of nudges produces one undo entry. Mirror of MarkerLayer's
  // keyboard burst tracking; lives here because the page-level handler
  // can't see MarkerLayer's internal state.
  const altNudgeRef = useRef<{ id: string; lastTime: number; timer: number } | null>(
    null,
  );
  const flushAltNudge = useCallback(() => {
    const n = altNudgeRef.current;
    if (!n) return;
    window.clearTimeout(n.timer);
    altNudgeRef.current = null;
    handleMarkerTimeChangeCommit(n.id, n.lastTime);
  }, [handleMarkerTimeChangeCommit]);

  const handleAddManual = useCallback(
    (time: number) => {
      const id = `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      recordEvent("marker_added_manual", { id, time });
      mutate([
        ...markers,
        {
          id,
          kind: "manual",
          time,
          candidateNumber: null,
          confidence: null,
          peakAmplitude: null,
          note: "",
        },
      ]);
      setFocusedMarkerId(id);
    },
    [markers, mutate, recordEvent],
  );

  const handleNoteChange = useCallback(
    (id: string, note: string) => {
      // Notes don't go on the undo stack -- a stray keystroke shouldn't
      // bury the last marker drag. We do log the change to audit_events
      // (debounce-on-save would be nicer; one event per keystroke is
      // fine for v1 since notes are short).
      sessionEventsRef.current.push({
        ts: new Date().toISOString(),
        kind: "note_changed",
        payload: { id, note },
      });
      isDirtyRef.current = true;
      setMarkers((prev) => prev.map((m) => (m.id === id ? { ...m, note } : m)));
    },
    [],
  );

  // Kept shots = the sequence the stepper walks. Detected (kept) + manual
  // markers, sorted by time. Rejected markers don't appear here -- the
  // user reaches them via the list drawer.
  const keptShots = useMemo(
    () =>
      markers
        .filter((m) => m.kind === "detected" || m.kind === "manual")
        .slice()
        .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id)),
    [markers],
  );

  // Live anomaly list -- mirrors what the saved report.txt will surface.
  // Recomputed on every marker / beep / stage-time change so chips + pins
  // stay in sync as the user keeps / rejects candidates. The list also
  // absorbs the "beep looks wrong" heuristic below as a synthetic warn
  // chip -- the dedicated banner is reserved for sync-mode (per design),
  // so passive warnings ride the anomaly row alongside everything else.
  const anomalies = useMemo(() => {
    if (!stage) return [];
    const shots = keptShotsFromMarkers(markers, auditBeep);
    return detectAnomalies(shots, stage.time_seconds);
  }, [markers, auditBeep, stage]);

  // "Beep looks wrong" heuristic. Fires when the post-detection state
  // has signals that strongly suggest the beep was placed on the wrong
  // sound rather than e.g. the user missing shots. Surfacing this as a
  // banner is the proactive counterpart to the always-visible 'Re-pick
  // beep' button: catches the mistake even before the user manually
  // compares shot count vs expected.
  //
  // Heuristic, conservative to avoid false alarms:
  //   - "draw too long": first detected shot lands > 2.5s after beep
  //     (typical IPSC draw is < 2s; > 2.5s means the beep is probably
  //     before the actual buzzer).
  //   - "stage time overshoot": the last detected shot lands more than
  //     1s AFTER the official stage time. Stage time is the call from
  //     beep to last shot; if our beep is too early, last shot's time-
  //     from-beep exceeds stage_time.
  //
  // Only fires when there are enough shots to draw a conclusion (>= 3).
  const beepDiagnostic = useMemo<{ reason: string } | null>(() => {
    if (!stage || stage.time_seconds <= 0 || auditBeep == null) return null;
    if (keptShots.length < 3) return null;
    const sorted = keptShots.slice().sort((a, b) => a.time - b.time);
    const first = sorted[0];
    const last = sorted[sorted.length - 1];
    const firstFromBeep = first.time - auditBeep;
    const lastFromBeep = last.time - auditBeep;
    const overshoot = lastFromBeep - stage.time_seconds;
    if (firstFromBeep > 2.5) {
      return {
        reason: `First shot lands ${firstFromBeep.toFixed(2)}s after the beep -- typical draws are well under 2 s, so the beep is likely placed before the actual buzzer.`,
      };
    }
    if (overshoot > 1.0) {
      return {
        reason: `Last shot lands ${overshoot.toFixed(2)}s after the official stage time (${stage.time_seconds.toFixed(2)}s) -- the beep may have been picked up too early.`,
      };
    }
    return null;
  }, [keptShots, auditBeep, stage]);

  // Anomaly chips shown above the waveform. The "beep looks wrong"
  // diagnostic used to ride along as a synthetic warn chip here; it now
  // lives on BeepStatusChip directly (tooltip + amber tone), so the
  // anomaly row reflects only the structured shot-level anomalies. One
  // signal, one home, attached to its trigger.
  const anomalyChips = anomalies;

  // Whenever the kept-shot list shrinks (reject / delete), keep the index
  // in range. Don't change otherwise -- the user's position is sticky.
  useEffect(() => {
    if (keptShots.length === 0) {
      if (currentShotIndex !== 0) setCurrentShotIndex(0);
      return;
    }
    if (currentShotIndex >= keptShots.length) {
      setCurrentShotIndex(keptShots.length - 1);
    }
  }, [keptShots, currentShotIndex]);

  const stepShot = useCallback(
    (delta: number) => {
      if (keptShots.length === 0) return;
      // Anchor on the focused marker (#39): if focus is on a kept shot,
      // step from there. Otherwise use currentShotIndex as a fallback.
      // This way, after K rejects the focused marker, M / Shift+M land
      // on the correct neighbour in the post-mutation list instead of
      // skipping one because the integer index now addresses i+2.
      let anchor = currentShotIndex;
      if (focusedMarkerId) {
        const idx = keptShots.findIndex((k) => k.id === focusedMarkerId);
        if (idx >= 0) anchor = idx;
        // Focus is on a non-kept marker (e.g., just-rejected). Walk to
        // the kept shot at-or-just-before the playhead so +1 lands on
        // the next kept after currentTime.
        else {
          let preceding = -1;
          for (let i = 0; i < keptShots.length; i++) {
            if (keptShots[i].time <= currentTime) preceding = i;
            else break;
          }
          // delta>0: start one step *before* the next kept marker so
          // anchor+1 lands on it. delta<0: start at the next kept marker
          // so anchor-1 lands on the preceding one.
          anchor = delta > 0 ? preceding : Math.max(0, preceding + 1);
        }
      }
      const next = Math.min(Math.max(anchor + delta, 0), keptShots.length - 1);
      setCurrentShotIndex(next);
      setFocusedMarkerId(keptShots[next].id);
      handleScrub(keptShots[next].time);
    },
    [keptShots, currentShotIndex, focusedMarkerId, currentTime, handleScrub],
  );

  const visibleKinds = useMemo(() => visibleKindsFromFilters(filters), [filters]);

  // Markers in time order, filtered to currently-visible kinds. N and
  // K-auto-progress walk this list -- a marker that's filtered out of
  // view shouldn't be a navigation target either, otherwise the
  // playhead jumps to a marker the user can't see.
  const visibleMarkersSorted = useMemo(
    () =>
      markers
        .filter((m) => visibleKinds.has(m.kind))
        .slice()
        .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id)),
    [markers, visibleKinds],
  );

  const stepAnyMarker = useCallback(
    (delta: number) => {
      if (visibleMarkersSorted.length === 0) return;
      // Anchor: focused marker -> use its index; otherwise pick the
      // marker at-or-just-before the playhead so forward stepping lands
      // on the next one and back-stepping lands on the previous.
      let curIdx = -1;
      if (focusedMarkerId) {
        curIdx = visibleMarkersSorted.findIndex((m) => m.id === focusedMarkerId);
      }
      if (curIdx < 0) {
        for (let i = 0; i < visibleMarkersSorted.length; i++) {
          if (visibleMarkersSorted[i].time <= currentTime) curIdx = i;
          else break;
        }
        if (curIdx < 0) curIdx = delta > 0 ? -1 : 0;
      }
      const nextIdx = Math.min(
        Math.max(curIdx + delta, 0),
        visibleMarkersSorted.length - 1,
      );
      const target = visibleMarkersSorted[nextIdx];
      setFocusedMarkerId(target.id);
      handleScrub(target.time);
      const keptIdx = keptShots.findIndex((k) => k.id === target.id);
      if (keptIdx >= 0) setCurrentShotIndex(keptIdx);
    },
    [visibleMarkersSorted, focusedMarkerId, currentTime, handleScrub, keptShots],
  );

  const jumpToMarker = useCallback(
    (m: AuditMarker) => {
      setFocusedMarkerId(m.id);
      handleScrub(m.time);
      // If the marker is a kept shot, line the stepper up with it too.
      const idx = keptShots.findIndex((k) => k.id === m.id);
      if (idx >= 0) setCurrentShotIndex(idx);
    },
    [handleScrub, keptShots],
  );

  const pixelsPerSecond = useMemo(
    () => zoomToPixelsPerSecond(zoom, waveformViewport, peaks?.duration ?? 0),
    [zoom, waveformViewport, peaks],
  );

  // ---- Save flow (Step 5) ------------------------------------------------

  const performSave = useCallback(
    async (opts: { silent?: boolean; advance?: boolean } = {}): Promise<boolean> => {
      if (stageNumber == null || !stage) return false;
      if (!isDirtyRef.current && opts.silent) return true; // nothing to save
      const beepInClip = peaks?.beep_time ?? primary?.beep_time ?? null;
      const appendEvents = sessionEventsRef.current;
      const payload = buildAuditJson({
        base: audit,
        stage: {
          stage_number: stage.stage_number,
          stage_name: stage.stage_name,
          time_seconds: stage.time_seconds,
        },
        primaryBeepInClip: beepInClip,
        markers,
        appendEvents: [
          ...appendEvents,
          {
            ts: new Date().toISOString(),
            kind: "save",
            payload: { shots_count: 0 /* filled after build */ },
          },
        ],
      });
      // Attach the actual shots count to the synthetic save event.
      const lastEv = payload.audit_events?.[payload.audit_events.length - 1];
      if (lastEv && lastEv.kind === "save") {
        lastEv.payload = { shots_count: payload.shots.length };
      }
      setSaveStatus({ kind: "saving" });
      try {
        const saved = await api.saveStageAudit(slug, stageNumber, payload);
        setAudit(saved);
        sessionEventsRef.current = [];
        isDirtyRef.current = false;
        setSaveStatus({ kind: "saved", at: Date.now() });
        // Auto-advance on explicit Save (Cmd+S or the Save button): the
        // common audit loop is "run detect -> Save -> next stage", so we
        // jump immediately after the write returns. Silent saves (the
        // dirty-flush during stage switch) never advance.
        //
        // The conveyor chains across shooters: at the last stage of the
        // current shooter, land in a shooter-complete interstitial that
        // names the next shooter and offers an explicit CTA (Variant D
        // in the design bundle); at the last stage of the last shooter,
        // land in the match-complete finish state. ?done=1 swaps the
        // StageActionBar for SessionSummary either way; ?next=<slug>
        // distinguishes shooter-complete from match-complete and names
        // the next shooter's pickup target.
        if (opts.advance) {
          const step = computeAuditNextStep({
            shooters,
            activeSlug: slugParam,
            stages: stageSelectorOptions,
            activeStage: stageNumber,
          });
          if (step.kind === "stage") {
            navigate(`/audit/${step.nextSlug}/${step.nextStage}`);
          } else if (step.kind === "shooter" && slugParam != null && stageNumber != null) {
            navigate(
              `/audit/${slugParam}/${stageNumber}?done=1&next=${encodeURIComponent(step.nextSlug)}`,
              { replace: true },
            );
          } else if (slugParam != null && stageNumber != null) {
            navigate(`/audit/${slugParam}/${stageNumber}?done=1`, { replace: true });
          }
        }
        return true;
      } catch (err) {
        const message = err instanceof ApiError ? err.detail : String(err);
        setSaveStatus({ kind: "error", message });
        return false;
      }
    },
    [
      stageNumber,
      stage,
      peaks,
      primary,
      audit,
      markers,
      navigate,
      slugParam,
      shooters,
      stageSelectorOptions,
    ],
  );

  // Auto-clear "saved" toast after a short hold so it stops nagging.
  useEffect(() => {
    if (saveStatus.kind !== "saved") return;
    const timer = window.setTimeout(() => setSaveStatus({ kind: "idle" }), 2500);
    return () => window.clearTimeout(timer);
  }, [saveStatus]);

  // Auto-save on stage switch: if the user picks a different stage in
  // the selector, wait for the save to complete before navigating so a
  // crash mid-flight doesn't lose the last stage's edits.
  const navigateToStage = useCallback(
    async (n: number) => {
      if (stageNumber === n) return;
      if (isDirtyRef.current) {
        await performSave({ silent: true });
      }
      const target = slugParam ? `/audit/${slugParam}/${n}` : `/audit/${n}`;
      navigate(target);
    },
    [navigate, performSave, stageNumber, slugParam],
  );

  // Sync mode commit handler. Lives here so it can close over the
  // slug/stageNumber + setters; declared below those without churning
  // every existing useCallback's dep array. See `startSync` / `cancelSync`
  // up top for the state plumbing.
  //
  // Caveat (TODO): the candidate is picked on the primary's waveform
  // regardless of which cam is selected. For the primary that's right;
  // for secondaries it's a visual approximation -- swapping the
  // waveform to the secondary's own audio needs getVideoPeaks plumbing
  // + a peaks/duration swap on the Waveform component.
  const applySync = useCallback(async () => {
    if (syncMode == null || syncCandidate == null || stageNumber == null) return;
    setSyncBusy(true);
    try {
      const updated = await api.overrideBeepForVideo(
        slug,
        stageNumber,
        syncMode.video_id,
        syncCandidate,
      );
      setProject(updated);
      setSyncMode(null);
      setSyncCandidate(null);
    } catch (e) {
      setProjectError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSyncBusy(false);
    }
  }, [syncMode, syncCandidate, slug, stageNumber]);

  // "Looks right" -- keeps the current buzzer time but flips
  // beep_reviewed so the pill drops the "needs sync" flag. No
  // detection or trim chain is queued. Mirrors the BeepReview page's
  // ack action but lives next to the cam it applies to.
  const markSyncReviewed = useCallback(async () => {
    if (syncMode == null || stageNumber == null || syncMode.beep_time == null) {
      return;
    }
    setSyncBusy(true);
    try {
      const updated = await api.setBeepReviewed(
        slug,
        stageNumber,
        syncMode.video_id,
        true,
      );
      setProject(updated);
      setSyncMode(null);
      setSyncCandidate(null);
    } catch (e) {
      setProjectError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setSyncBusy(false);
    }
  }, [syncMode, slug, stageNumber]);

  // ---- Global keyboard shortcuts -----------------------------------------

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      // ``inField`` distinguishes typing text from anything else focusable.
      // Hidden checkboxes (filter chips), buttons, etc. are NOT "in a field"
      // -- the audit screen reserves Space / arrow keys / etc. for playback
      // control regardless of which control was last clicked.
      const inField = isTypingTextTarget(target);

      if (e.code === "Space" && !inField) {
        e.preventDefault();
        togglePlay();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        undo();
        return;
      }
      if (!inField && e.key === "?") {
        e.preventDefault();
        setShowHelp((v) => !v);
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void performSave({ advance: true });
        return;
      }
      // PiP bay shortcuts: V toggle, Alt+V cycle size, D snap next corner.
      // Suppressed in text fields so users typing notes don't trip them.
      if (!inField && !e.metaKey && !e.ctrlKey) {
        if (e.key.toLowerCase() === "v") {
          e.preventDefault();
          if (e.altKey) cyclePipSize();
          else togglePipHidden();
          return;
        }
        if (e.key.toLowerCase() === "d" && !e.altKey) {
          e.preventDefault();
          cyclePipCorner();
          return;
        }
      }
      // `[` / `]` walk between stages without leaving the keyboard.
      // navigateToStage auto-saves a dirty stage before navigating, so
      // these can be tapped freely while auditing.
      if (!inField && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (e.key === "[" && prevStageNumber != null) {
          e.preventDefault();
          void navigateToStage(prevStageNumber);
          return;
        }
        if (e.key === "]" && nextStageNumber != null) {
          e.preventDefault();
          void navigateToStage(nextStageNumber);
          return;
        }
      }
      if ((e.metaKey || e.ctrlKey) && (e.key === "1" || e.key === "2" || e.key === "3")) {
        // Cmd+1 zoom in / Cmd+2 fit / Cmd+3 zoom out -- matches the old
        // review SPA's bindings so muscle memory carries over.
        e.preventDefault();
        if (e.key === "2") setZoom(null);
        else if (e.key === "1")
          setZoom((z) => Math.min(16, (z ?? 1) * 1.5));
        else setZoom((z) => {
          const next = (z ?? 1) / 1.5;
          return next <= 0.25 ? null : next;
        });
        return;
      }
      // Alt+Arrow nudges the focused marker (or the current shot) by
      // detector resolution; Alt+Shift+Arrow is sample-precise (~1 ms).
      // We also scrub the playhead to the new position so the user
      // immediately hears what the marker is aligned to.
      if (
        !inField &&
        e.altKey &&
        !e.metaKey &&
        !e.ctrlKey &&
        (e.key === "ArrowLeft" || e.key === "ArrowRight")
      ) {
        e.preventDefault();
        let target: AuditMarker | null = null;
        if (focusedMarkerId) {
          target = markers.find((x) => x.id === focusedMarkerId) ?? null;
        }
        if (!target && keptShots.length > 0) {
          const idx = Math.min(currentShotIndex, keptShots.length - 1);
          target = keptShots[idx];
        }
        if (!target) return;
        const dir = e.key === "ArrowRight" ? 1 : -1;
        const step = e.shiftKey ? 0.001 : 0.0107;
        const dur = peaks?.duration ?? target.time + step;
        const next = Math.min(dur, Math.max(0, target.time + dir * step));
        // Burst-coalesce so Cmd+Z reverses the entire burst, not each tap.
        if (altNudgeRef.current?.id !== target.id) {
          flushAltNudge();
          handleMarkerTimeChangeBegin(target.id);
          altNudgeRef.current = {
            id: target.id,
            lastTime: next,
            timer: window.setTimeout(flushAltNudge, 350),
          };
        } else {
          window.clearTimeout(altNudgeRef.current.timer);
          altNudgeRef.current.lastTime = next;
          altNudgeRef.current.timer = window.setTimeout(flushAltNudge, 350);
        }
        handleMarkerTimeChange(target.id, next);
        handleScrub(next);
        return;
      }
      if (!inField && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (e.key === "m" || e.key === "M") {
          e.preventDefault();
          stepShot(e.shiftKey ? -1 : 1);
          return;
        }
        if (e.key === "n" || e.key === "N") {
          // N steps through *every* marker (detected / rejected / manual)
          // so the user can find a rejected one and K-toggle it back to
          // kept without leaving the keyboard.
          e.preventDefault();
          stepAnyMarker(e.shiftKey ? -1 : 1);
          return;
        }
        if (e.key === "l" || e.key === "L") {
          e.preventDefault();
          setShowDrawer((v) => !v);
          return;
        }
        if (e.key === "r" || e.key === "R") {
          // Old review SPA used L for loop; L is now the drawer here, so
          // loop moves to R ("repeat"). Visible button next to play/pause
          // matches the same icon.
          e.preventDefault();
          setLoopMode((v) => !v);
          return;
        }
        if ((e.key === "Delete" || e.key === "Backspace") && focusedMarkerId) {
          const target = markers.find((x) => x.id === focusedMarkerId) ?? null;
          if (target?.kind === "manual") {
            e.preventDefault();
            handleMarkerDelete(target);
          }
          return;
        }
        if (e.key === "k" || e.key === "K") {
          // Keep / reject toggle for the current shot. Lets the user step
          // through shots with M and decide each one without reaching for
          // the mouse. Prefers the focused marker (could be a rejected
          // one the user is reconsidering) and falls back to the kept
          // shot at the stepper's current position.
          e.preventDefault();
          let target: AuditMarker | null = null;
          if (focusedMarkerId) {
            target = markers.find((x) => x.id === focusedMarkerId) ?? null;
          }
          if (!target && keptShots.length > 0) {
            const idx = Math.min(currentShotIndex, keptShots.length - 1);
            target = keptShots[idx];
          }
          if (target) {
            // Toggle is meaningless for manual markers; route to delete so
            // K on a manual marker actually does something useful.
            if (target.kind === "manual") handleMarkerDelete(target);
            else handleMarkerClick(target);
            // Auto-progress: walk the visible marker list (filters
            // respected) so the user can rip through K-K-K without
            // moving the mouse. stepAnyMarker anchors on the focused
            // marker, which is the one we just toggled -- so +1 lands
            // on the next visible marker even though kept/rejected
            // membership shifted underneath us.
            if (kAutoProgress) stepAnyMarker(1);
          }
          return;
        }
        if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
          // Fine-grained playhead step. Shift = ~1 frame at 30 fps;
          // unmodified = 250 ms (matches the old review SPA).
          e.preventDefault();
          const v = videoRef.current;
          if (!v) return;
          const dir = e.key === "ArrowRight" ? 1 : -1;
          const step = e.shiftKey ? 0.025 : 0.25;
          const t = v.currentTime - beepOffset;
          const dur = peaks?.duration ?? t + step;
          handleScrub(Math.min(dur, Math.max(0, t + dir * step)));
          return;
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    togglePlay,
    undo,
    stepShot,
    stepAnyMarker,
    performSave,
    beepOffset,
    peaks,
    handleScrub,
    handleMarkerClick,
    handleMarkerDelete,
    handleMarkerTimeChange,
    handleMarkerTimeChangeBegin,
    flushAltNudge,
    focusedMarkerId,
    markers,
    keptShots,
    currentShotIndex,
    kAutoProgress,
    navigateToStage,
    prevStageNumber,
    nextStageNumber,
    togglePipHidden,
    cyclePipSize,
    cyclePipCorner,
  ]);

  // Stage switch / unmount: flush any pending nudge bracket so the
  // commit doesn't fire after the markers list has been swapped out.
  useEffect(() => {
    return () => flushAltNudge();
  }, [stageNumber, flushAltNudge]);

  // Pin the served file to either trim or source for the lifetime of
  // this <video> element. Without this, a background trim job that
  // completes mid-playback would flip the server's auto-pick from
  // source to trim and the browser's next Range request would fall
  // past the (shorter) trim's EOF -- the player errors out with
  // "source not found" and only a full reload recovers. Wait for
  // peaks to load so we know which kind to pin to; when peaks fails
  // (no beep yet, etc.) the server's ``auto`` still does the right
  // thing because the trim can't exist without a beep.
  const videoSrc = activeVideo
    ? peaks
      ? api.videoStreamUrl(slug, activeVideo.path, peaks.trimmed ? "trim" : "source")
      : peaksError != null
        ? api.videoStreamUrl(slug, activeVideo.path)
        : ""
    : "";

  // ---- Render ------------------------------------------------------------

  if (projectError) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
        <Card>
          <CardHeader>
            <CardTitle>Failed to load project</CardTitle>
            <CardDescription>{projectError}</CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
        <Card>
          <CardContent className="flex items-center gap-2 py-6 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading project...
          </CardContent>
        </Card>
      </div>
    );
  }

  if (stagesWithPrimary.length === 0) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold tracking-tight">Audit</h1>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Crosshair className="size-5" /> Nothing to audit yet
            </CardTitle>
            <CardDescription>
              Assign a primary video to at least one stage on the Ingest screen.
              Audit always operates on a stage's primary audio.
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  const detectedCount = markers.filter((m) => m.kind === "detected").length;
  const rejectedCount = markers.filter((m) => m.kind === "rejected").length;
  const manualCount = markers.filter((m) => m.kind === "manual").length;

  // ?done=1 is set by performSave when the conveyor lands on the
  // finish state (no more stages, no more shooters). The StageActionBar
  // swaps for the SessionSummary card so the operator gets a clear
  // finish line instead of a stranded CTA.
  //
  // Plain const (not useMemo) on purpose -- this lives *after* the
  // early returns (projectError / !project / no stages with primary),
  // and a conditionally-called hook trips React error #310.
  const sessionDone = doneParam === "1";
  const activeShooter = shooters.find((s) => s.slug === slugParam) ?? null;
  const summaryStats: { label: string; value: string; sub?: string }[] = [];
  if (activeShooter) {
    summaryStats.push({
      label: "Stages audited",
      value: String(activeShooter.stages_audited),
      sub: `of ${activeShooter.stages_total}`,
    });
  }
  summaryStats.push({
    label: "Shots on this stage",
    value: String(detectedCount + manualCount),
    sub: `${rejectedCount} rejected · ${manualCount} manual`,
  });
  summaryStats.push({
    label: "Anomalies",
    value: String(anomalies.length),
    sub: anomalies.length === 0 ? "clean" : "open",
  });

  // Blocking pre-audit state. When a stage hasn't met the prerequisites
  // for audit -- the trim isn't built yet, or detection hasn't run --
  // the audit canvas is replaced by PrereqGate. The toolbar's
  // TrimNowBadge / DetectShotsBadge are suppressed in that case so the
  // affordance lives in exactly one place (the gate).
  //
  // Only fires once peaks have loaded so we don't flash the gate while
  // we're still figuring out whether the trim exists.
  const prereqKind: "trim" | "detect" | null = peaks
    ? !peaks.trimmed
      ? "trim"
      : markers.length === 0
        ? "detect"
        : null
    : null;
  const prereqActive = prereqKind != null && stage != null && primary != null;

  return (
    <div className="flex min-h-full flex-col gap-4 px-7 pb-24 pt-5 text-ink">
      {stage && primary ? (
        <>
          {/* Stage chip rail. The Audit / Compare / Coach view switcher
              isn't on this page per design -- those views are reached
              from the sidebar (cross-view nav is shell-level). */}
          <div className="border-b border-rule pb-3">
            <StageChipRail
              stages={stageRailItems}
              activeStage={stageNumber ?? null}
              onPick={(n) => void navigateToStage(n)}
            />
          </div>

          {/* Toolbar: beep status + re-pick + trim/detect + filter chips
              + zoom + drawer toggle. Save & next + Undo live in the
              sticky bottom action bar; shooter switcher lives in the
              MatchShell breadcrumb. */}
          <div className="flex flex-wrap items-center gap-2.5">
            {/* Confidence-aware beep status -- absorbs the old
                "BeepDiagnostic" banner into a chip tooltip + tone shift
                so the diagnostic lives next to its trigger. Re-pick beep
                opens sync mode on the primary cam; it also remains
                available on the per-cam CamSyncPill inside the PipBay
                for secondaries. */}
            <BeepStatusChip
              beepTime={primary.beep_time}
              confidence={primary.beep_confidence ?? null}
              diagnostic={beepDiagnostic?.reason ?? null}
              onRePick={() => startSync(primary)}
            />
            {/* Re-pick beep affordance is now the primary cam's
                CamSyncPill inside the PipBay. Click the pill to enter
                sync mode for that cam (or any secondary). */}
            {peaks && !peaks.trimmed && !prereqActive ? (
              <TrimNowBadge
                slug={slug}
                stageNumber={stage.stage_number}
                hasBeep={primary.beep_time != null}
                hasStageTime={stage.time_seconds > 0}
                onProjectUpdate={(p) => {
                  setProject(p);
                  if (stageNumber != null) {
                    api
                      .getStagePeaks(slug, stageNumber, PEAK_BINS)
                      .then((np) => setPeaks(np))
                      .catch(() => {});
                  }
                }}
              />
            ) : null}
            {peaks ? (
              <FilterBar
                filters={filters}
                counts={{
                  detected: detectedCount,
                  rejected: rejectedCount,
                  manual: manualCount,
                  // The audit waveform anchors on the primary; the beep
                  // marker reflects ``auditBeep`` (= peaks.beep_time
                  // falling back to primary.beep_time). 0 when the
                  // primary has no beep yet.
                  beep: auditBeep != null ? 1 : 0,
                }}
                onChange={setFilters}
              />
            ) : null}
            <div className="ml-auto inline-flex items-center gap-2">
              {/* K-auto-step toggle. The transport bar is gone but this
                  is a behaviour preference, not a transport control --
                  keep it visible so the operator who relies on "mark
                  and advance" doesn't lose it. Quiet pill per design
                  spec: a *preference toggle* shouldn't read as a
                  primary action. */}
              <button
                type="button"
                onClick={() => setKAutoProgress((v) => !v)}
                aria-pressed={kAutoProgress}
                title={
                  kAutoProgress
                    ? "Auto-step to next shot on accept (K to toggle)"
                    : "Stay on shot after accept (K to toggle)"
                }
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-display text-[0.625rem] font-bold uppercase tracking-[0.06em] transition-colors",
                  kAutoProgress
                    ? "border-led/35 bg-led-tint text-ink"
                    : "border-rule bg-surface-2 text-muted hover:border-rule-strong hover:bg-surface-3 hover:text-ink",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "inline-block size-1.5 rounded-full",
                    kAutoProgress
                      ? "bg-led shadow-[0_0_6px_var(--color-led-glow)]"
                      : "bg-rule-strong",
                  )}
                />
                <Kbd size="sm">K</Kbd>
                <span>Auto-step</span>
              </button>
              {pipLayout.hidden ? (
                <button
                  type="button"
                  onClick={showPipBay}
                  aria-label="Show camera bay (V)"
                  title="Show camera bay (V)"
                  className="inline-flex items-center gap-1.5 rounded-md border border-led-deep bg-led-tint px-2.5 py-2 font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-led-soft shadow-[0_0_12px_var(--color-led-glow)] transition-colors hover:bg-led/20"
                >
                  <Eye className="size-3.5" aria-hidden />
                  Show cam
                  <span className="ml-1 inline-flex h-4 items-center rounded-sm border border-led-deep bg-bg/40 px-1 font-mono text-[0.5625rem] font-bold text-led-soft">
                    V
                  </span>
                </button>
              ) : null}
              {peaks ? <ZoomControls zoom={zoom} onZoomChange={setZoom} /> : null}
              <button
                type="button"
                onClick={() => setShowDrawer((v) => !v)}
                aria-label="Toggle marker drawer (L)"
                aria-pressed={showDrawer}
                title="Marker list (L)"
                className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-muted transition-colors hover:bg-surface-3 hover:text-ink"
              >
                <ListChecks className="size-4" />
              </button>
              <button
                type="button"
                onClick={() => setShowHelp(true)}
                aria-label="Keyboard shortcuts (?)"
                title="Keyboard shortcuts (?)"
                className="inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-muted transition-colors hover:bg-surface-3 hover:text-ink"
              >
                <HelpCircle className="size-4" />
              </button>
              {peaks && peaks.trimmed && !prereqActive ? (
                <DetectShotsBadge
                  slug={slug}
                  stageNumber={stage.stage_number}
                  hasBeep={primary.beep_time != null}
                  hasStageTime={stage.time_seconds > 0}
                  hasCandidates={markers.length > 0}
                  onComplete={async () => {
                    if (stageNumber == null) return;
                    const a = await api.getStageAudit(slug, stageNumber);
                    setAudit(a);
                    setMarkers(deriveMarkers(a));
                  }}
                />
              ) : null}
            </div>
          </div>

          {/* When the stage isn't ready to audit (trim missing, or no
              candidates yet), the entire canvas is replaced by
              PrereqGate. The toolbar's beep / filter chips stay
              visible (those are status, not actions), but PiP bay,
              waveform, shot stepper, and bottom action bar all
              suspend until prerequisites pass. */}
          {prereqActive && stage && primary ? (
            <PrereqGate
              kind={prereqKind!}
              slug={slug}
              stageNumber={stage.stage_number}
              stage={stage}
              blocked={primary.beep_time == null || stage.time_seconds <= 0}
              blockedReason={
                primary.beep_time == null
                  ? "Detect or set the beep first."
                  : stage.time_seconds <= 0
                    ? "Import a scoreboard so the stage time is known."
                    : null
              }
              hasSource
              hasStageTime={stage.time_seconds > 0}
              hasBeep={primary.beep_time != null}
              hasTrim={!!peaks?.trimmed}
              onProjectUpdate={(p) => {
                setProject(p);
                if (stageNumber != null) {
                  api
                    .getStagePeaks(slug, stageNumber, PEAK_BINS)
                    .then((np) => setPeaks(np))
                    .catch(() => {});
                }
              }}
              onAuditRefresh={async () => {
                if (stageNumber == null) return;
                const a = await api.getStageAudit(slug, stageNumber);
                setAudit(a);
                setMarkers(deriveMarkers(a));
              }}
            />
          ) : null}

          {/* Video lives in a floating PiP bay (replaces the legacy
              inline instrument frame). The bay anchors to a viewport
              corner so the waveform owns full page width, and the page
              flow no longer reserves vertical room for the video. */}
          {!prereqActive && !pipLayout.hidden ? (
            <PipBay
              corner={pipLayout.corner}
              size={pipLayout.size}
              camCount={videos.length}
              needsSyncCount={camsNeedingSync}
              onNeedsSyncClick={() => {
                const idx = camSyncStates.findIndex(
                  (s) => s === "low_confidence" || s === "no_beep",
                );
                if (idx >= 0 && videos[idx]) startSync(videos[idx]);
              }}
              onHide={togglePipHidden}
              onCycleSize={cyclePipSize}
              onCycleCorner={cyclePipCorner}
              transport={
                peaks ? (
                  <BayTransport
                    isPlaying={isPlaying}
                    loopMode={loopMode}
                    currentTime={currentTime}
                    duration={peaks.duration}
                    onTogglePlay={togglePlay}
                    onToggleLoop={() => setLoopMode((v) => !v)}
                    onStepFrame={(dir) => {
                      const v = videoRef.current;
                      if (!v) return;
                      const t = v.currentTime - beepOffset;
                      const dur = peaks.duration;
                      handleScrub(Math.min(dur, Math.max(0, t + dir * 0.025)));
                    }}
                  />
                ) : null
              }
            >
              <VideoPanel
                ref={videoRef}
                videos={videos}
                primaryBeepTime={primaryBeep}
                activeIndex={activeVideoIndex}
                onActiveIndexChange={setActiveVideoIndex}
                videoSrc={videoSrc}
                gridMode={gridMode}
                onGridModeToggle={handleGridModeToggle}
                onSecondaryRef={handleSecondaryRef}
                onSecondaryBuffering={handleSecondaryBuffering}
                onPrimaryTimeUpdate={handlePrimaryTimeUpdate}
                showHeader={false}
                renderCamOverlay={(video, index) => {
                  const state = camSyncStates[index] ?? "no_beep";
                  // Secondaries show the offset from primary on the pill
                  // so the cam's drift is visible right next to the cam.
                  const offsetSeconds =
                    index === 0 || video.beep_time == null || primaryBeep == null
                      ? null
                      : video.beep_time - primaryBeep;
                  return (
                    <CamSyncPill
                      state={state}
                      beepTime={video.beep_time}
                      beepConfidence={video.beep_confidence}
                      offsetSeconds={offsetSeconds}
                      size={pipLayout.size === "S" ? "xs" : "sm"}
                      onClick={() => startSync(video)}
                    />
                  );
                }}
                className="size-full [&_video]:!max-h-full [&_video]:!w-full"
              />
            </PipBay>
          ) : null}

          {peaks && !prereqActive ? (
            <>
              {/* Transport bar removed -- playback (play/pause/loop/step
                  frame) lives in the PipBay's shared transport row per
                  design. Position/clip readouts also live in the bay;
                  stage time is in the bottom action bar's kicker.
                  Kept/rejected/manual counts are covered by the filter
                  chips in the toolbar. */}

              {/* Anomaly chips OR sync banner. When the operator is
                  re-picking a buzzer for a cam, the chips swap out for
                  the SyncBanner per design. */}
              {syncMode != null ? (
                <SyncBanner
                  camLabel={
                    videos.findIndex((v) => v.video_id === syncMode.video_id) === 0
                      ? "Primary"
                      : `Cam ${videos.findIndex((v) => v.video_id === syncMode.video_id) + 1} · Secondary`
                  }
                  oldBeepTime={syncMode.beep_time}
                  candidateTime={syncCandidate}
                  onCancel={cancelSync}
                  onApply={() => void applySync()}
                  onMarkReviewed={
                    syncMode.beep_time != null && !syncMode.beep_reviewed
                      ? () => void markSyncReviewed()
                      : undefined
                  }
                  busy={syncBusy}
                />
              ) : (
                <AnomalyChips
                  anomalies={anomalyChips}
                  onJump={(a) => {
                    if (a.time != null) handleScrub(a.time);
                  }}
                />
              )}

              {/* Scope waveform with framed chrome. Header is the
                  design's mono `● PRIMARY · {n} peaks · {d}s` line on
                  the left and a one-line action hint on the right --
                  no Antonio brand header, no legend dot row (the
                  filter chips in the toolbar own those tones).
                  During sync mode the frame is tinted live-amber so
                  the operator can't miss they're in a re-pick flow. */}
              <div
                className={cn(
                  "overflow-hidden rounded-2xl border bg-bg-glow shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)] transition-colors",
                  syncMode
                    ? "border-live shadow-[0_0_0_1px_var(--color-live-glow)_inset,0_0_24px_rgba(251,191,36,0.22)]"
                    : "border-rule-strong",
                )}
              >
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-rule bg-surface-2 px-4 py-2.5 font-mono text-[0.625rem] uppercase tracking-[0.14em] text-subtle">
                  <span className="inline-flex items-center gap-1.5 tabular-nums">
                    <span
                      aria-hidden
                      className="inline-block size-1.5 rounded-full bg-led shadow-[0_0_6px_var(--color-led-glow)]"
                    />
                    <b className="font-bold text-led-soft">Primary</b>
                    <span aria-hidden className="text-rule-strong">
                      ·
                    </span>
                    <span>{peaks.peaks.length} peaks</span>
                    <span aria-hidden className="text-rule-strong">
                      ·
                    </span>
                    <span>{peaks.duration.toFixed(2)}s</span>
                  </span>
                  <span className="inline-flex items-center gap-2">
                    {peaksLoading ? (
                      <span
                        className="inline-flex items-center gap-1.5 text-led"
                        aria-live="polite"
                      >
                        <Loader2 className="size-3 animate-spin" aria-hidden />
                        Loading
                      </span>
                    ) : null}
                    <span className="text-whisper">
                      scrub or double-click to add marker
                    </span>
                  </span>
                </div>
                <div className="relative px-4 py-3" ref={waveformWrapperRef}>
                  <Waveform
                    peaks={peaks.peaks}
                    duration={peaks.duration}
                    currentTime={
                      syncMode != null && syncCandidate != null
                        ? syncCandidate
                        : currentTime
                    }
                    beepTime={
                      syncMode != null
                        ? (syncCandidate ?? syncMode.beep_time)
                        : filters.beep
                          ? auditBeep
                          : null
                    }
                    pixelsPerSecond={pixelsPerSecond}
                    onScrub={
                      syncMode != null ? (t) => setSyncCandidate(t) : handleScrub
                    }
                    onDoubleClick={syncMode != null ? undefined : handleAddManual}
                    height={180}
                  >
                    {syncMode == null ? (
                      <MarkerLayer
                        markers={markers}
                        duration={peaks.duration}
                        focusedId={focusedMarkerId}
                        onFocusChange={setFocusedMarkerId}
                        onClick={handleMarkerClick}
                        onDelete={handleMarkerDelete}
                        onTimeChange={handleMarkerTimeChange}
                        onTimeChangeBegin={handleMarkerTimeChangeBegin}
                        onTimeChangeCommit={handleMarkerTimeChangeCommit}
                        visibleKinds={visibleKinds}
                      />
                    ) : null}
                  </Waveform>
                  {/* Anomaly pins sit on the seam between the legend
                      header and the waveform bars. `inset-x-4` matches
                      the wrapper's px-4 so the % positions align with
                      the Waveform's content box (at fit zoom). The h-0
                      overlay + per-pin `-translate-y-1/2` puts each pin
                      visually centred on the seam. */}
                  <div
                    aria-hidden
                    className="pointer-events-none absolute inset-x-4 top-0 z-10 h-0"
                  >
                    <AnomalyPins
                      anomalies={anomalies}
                      duration={peaks.duration}
                      onJump={(a) => {
                        if (a.time != null) handleScrub(a.time);
                      }}
                    />
                  </div>
                </div>
                {/* Time ruler -- six evenly-spaced ticks across the
                    fit-zoom duration. Mono, whisper-toned. */}
                <div className="flex justify-between border-t border-rule bg-surface-2 px-4 py-1.5 font-mono text-[0.625rem] tabular-nums text-whisper">
                  {Array.from({ length: 6 }, (_, i) => (
                    <span key={i}>
                      {((i / 5) * peaks.duration).toFixed(2)}
                    </span>
                  ))}
                </div>
                {/* Cam offset readout moved onto each secondary's
                    CamSyncPill -- the offset surfaces next to the cam
                    it applies to instead of as a separate footer. */}
              </div>

              {/* Shot stepper. When the PiP bay is anchored to a
                  bottom corner it overlays the page near the stepper's
                  notes input -- reserve horizontal room on that side so
                  the input never ends up under the bay. */}
              <div
                style={{
                  marginRight:
                    !pipLayout.hidden && pipLayout.corner === "br"
                      ? pipFootprintWidth(pipLayout.size, videos.length) + 20
                      : undefined,
                  marginLeft:
                    !pipLayout.hidden && pipLayout.corner === "bl"
                      ? pipFootprintWidth(pipLayout.size, videos.length) + 20
                      : undefined,
                  transition: "margin 180ms var(--ease-default)",
                }}
              >
                <ShotStepper
                  shots={keptShots}
                  currentIndex={currentShotIndex}
                  onStep={stepShot}
                  onNoteChange={handleNoteChange}
                />
              </div>
            </>
          ) : peaksLoading ? (
            <div className="flex h-32 items-center justify-center gap-2 text-sm text-muted">
              <Loader2 className="size-4 animate-spin" /> Computing waveform...
            </div>
          ) : peaksError ? (
            <div className="rounded-md border border-led/40 bg-led/10 p-4 text-sm text-led">
              Couldn't load peaks: {peaksError}
            </div>
          ) : null}
        </>
      ) : null}
      <ListDrawer
        open={showDrawer}
        onClose={() => setShowDrawer(false)}
        markers={markers}
        currentMarkerId={focusedMarkerId}
        onJumpTo={jumpToMarker}
        onDelete={handleMarkerDelete}
      />
      <HelpOverlay
        open={showHelp}
        onClose={() => setShowHelp(false)}
        mode="audit"
      />
      {stagesWithPrimary.length > 0 && !prereqActive ? (
        sessionDone ? (
          <div className="fixed inset-x-0 bottom-0 z-30 border-t border-rule-strong bg-bg/95 px-5 py-3 backdrop-blur">
            <SessionSummary
              shooterName={
                shooters.find((s) => s.slug === slugParam)?.name ?? null
              }
              stats={summaryStats}
              onJumpToOverview={() => navigate("/shooters")}
              nextShooterLabel={
                nextShooterParam
                  ? (shooters.find((s) => s.slug === nextShooterParam)?.name ??
                    null)
                  : null
              }
              onAuditNextShooter={
                nextShooterParam
                  ? () => navigate(`/audit/${nextShooterParam}`)
                  : undefined
              }
            />
          </div>
        ) : (
          <StageActionBar
            shooters={shooters}
            activeSlug={slugParam}
            activeStage={stageNumber}
            stages={stageRailItems.map((s) => ({
              stageNumber: s.stageNumber,
              stageName: s.stageName,
              status: s.status,
            }))}
            step={computeAuditNextStep({
              shooters,
              activeSlug: slugParam,
              stages: stageSelectorOptions,
              activeStage: stageNumber,
            })}
            dirty={isDirtyRef.current && saveStatus.kind !== "saving"}
            saving={saveStatus.kind === "saving"}
            justSaved={saveStatus.kind === "saved"}
            canUndo={undoStackRef.current.length > 0}
            onSave={() => void performSave({ advance: true })}
            onUndo={undo}
          />
        )
      ) : null}
      {saveStatus.kind === "error" ? <SaveToast status={saveStatus} /> : null}
    </div>
  );
}

/** Shared transport row inside the PipBay: play/pause, time readout,
 *  loop toggle, frame-step buttons. Per design, the page no longer
 *  ships its own transport bar; this is the single source of playback
 *  truth for the operator. */
function BayTransport({
  isPlaying,
  loopMode,
  currentTime,
  duration,
  onTogglePlay,
  onToggleLoop,
  onStepFrame,
}: {
  isPlaying: boolean;
  loopMode: boolean;
  currentTime: number;
  duration: number;
  onTogglePlay: () => void;
  onToggleLoop: () => void;
  onStepFrame: (dir: -1 | 1) => void;
}) {
  return (
    <>
      <button
        type="button"
        onClick={onTogglePlay}
        title={isPlaying ? "Pause (Space)" : "Play (Space)"}
        aria-label={isPlaying ? "Pause (Space)" : "Play (Space)"}
        className="inline-flex size-6 items-center justify-center rounded-full border-0 bg-led-fill text-ink shadow-[0_0_10px_var(--color-led-glow)] transition-colors hover:bg-led-soft"
      >
        {isPlaying ? <Pause className="size-3" /> : <Play className="size-3" />}
      </button>
      <span className="font-mono text-[0.6875rem] tabular-nums text-ink-2">
        {currentTime.toFixed(3)}
        <span className="text-subtle">/{duration.toFixed(2)}s</span>
      </span>
      <span
        aria-hidden
        className="font-mono text-[0.5625rem] font-bold uppercase tracking-[0.12em] text-subtle"
      >
        shared transport
      </span>
      <div className="ml-auto inline-flex items-center gap-1">
        <button
          type="button"
          onClick={onToggleLoop}
          aria-pressed={loopMode}
          title="Loop (R)"
          aria-label={loopMode ? "Loop on (R)" : "Loop off (R)"}
          className={cn(
            "inline-flex size-[22px] items-center justify-center rounded-sm border transition-colors",
            loopMode
              ? "border-led bg-led/10 text-led shadow-[0_0_8px_var(--color-led-glow)]"
              : "border-rule bg-transparent text-muted hover:border-rule-strong hover:text-ink-2",
          )}
        >
          <Repeat className="size-3" aria-hidden />
        </button>
        <button
          type="button"
          onClick={() => onStepFrame(-1)}
          title="Step frame back (Shift+←)"
          aria-label="Step frame back"
          className="inline-flex size-[22px] items-center justify-center rounded-sm border border-rule font-mono text-[0.625rem] font-bold text-muted transition-colors hover:border-rule-strong hover:text-ink-2"
        >
          ‹
        </button>
        <button
          type="button"
          onClick={() => onStepFrame(1)}
          title="Step frame forward (Shift+→)"
          aria-label="Step frame forward"
          className="inline-flex size-[22px] items-center justify-center rounded-sm border border-rule font-mono text-[0.625rem] font-bold text-muted transition-colors hover:border-rule-strong hover:text-ink-2"
        >
          ›
        </button>
      </div>
    </>
  );
}

function SaveToast({ status }: { status: SaveStatus }) {
  // Single aria-live region for save status. We render the container
  // unconditionally so screen readers can pick up status changes; only
  // the inner pill is conditional.
  let label = "";
  let tone = "";
  if (status.kind === "saving") {
    label = "Saving audit...";
    tone = "bg-card text-foreground";
  } else if (status.kind === "saved") {
    label = "Audit saved";
    tone = "bg-status-complete/10 text-foreground border-status-complete/40";
  } else if (status.kind === "error") {
    label = `Save failed: ${status.message}`;
    tone = "bg-destructive/10 text-destructive border-destructive/40";
  }
  return (
    <div
      role="status"
      aria-live={status.kind === "error" ? "assertive" : "polite"}
      className="pointer-events-none fixed bottom-4 right-4 z-50"
    >
      {label ? (
        <div
          className={`pointer-events-auto rounded-md border px-3 py-2 text-sm shadow-md ${tone}`}
        >
          {label}
        </div>
      ) : null}
    </div>
  );
}

type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  | { kind: "error"; message: string };

function buildAuditJson(opts: {
  base: StageAudit | null;
  stage: { stage_number: number; stage_name: string; time_seconds: number };
  primaryBeepInClip: number | null;
  markers: AuditMarker[];
  appendEvents: AuditEvent[];
}): StageAudit {
  const { base, stage, primaryBeepInClip, markers, appendEvents } = opts;

  // Kept = detected + manual, sorted by time. Each gets a sequential
  // shot_number; we preserve the candidate_number when the marker came
  // from a detected candidate so the SSI cross-reference stays intact.
  const kept = markers
    .filter((m) => m.kind === "detected" || m.kind === "manual")
    .slice()
    .sort((a, b) => a.time - b.time || a.id.localeCompare(b.id));

  const shots: AuditShot[] = kept.map((m, i) => {
    const ms_after_beep =
      primaryBeepInClip != null ? Math.round((m.time - primaryBeepInClip) * 1000) : 0;
    return {
      shot_number: i + 1,
      candidate_number: m.candidateNumber,
      time: round3(m.time),
      ms_after_beep,
      source: m.kind === "manual" ? "manual" : "detected",
      ...(m.note ? { note: m.note } : {}),
    } as AuditShot & { note?: string };
  });

  const previousEvents = base?.audit_events ?? [];
  const audit_events = [...previousEvents, ...appendEvents];

  return {
    ...(base ?? {}),
    stage_number: stage.stage_number,
    stage_name: stage.stage_name,
    stage_time_seconds: stage.time_seconds,
    beep_time: primaryBeepInClip ?? base?.beep_time,
    shots,
    _candidates_pending_audit: base?._candidates_pending_audit,
    audit_events,
  };
}

function round3(n: number): number {
  return Math.round(n * 1000) / 1000;
}

interface DetectShotsBadgeProps {
  slug: string;
  stageNumber: number;
  hasBeep: boolean;
  hasStageTime: boolean;
  hasCandidates: boolean;
  onComplete: () => Promise<void> | void;
}

function DetectShotsBadge({
  slug,
  stageNumber,
  hasBeep,
  hasStageTime,
  hasCandidates,
  onComplete,
}: DetectShotsBadgeProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const blocked = !hasBeep || !hasStageTime;
  const running = job != null && (job.status === "pending" || job.status === "running");
  const reason = !hasBeep
    ? "Detect or set the beep first."
    : !hasStageTime
      ? "Import a scoreboard so the stage time is known."
      : null;

  // Auto-adopt an in-flight shot-detect job after reload. Auto-trim
  // chains shot detection; the user often lands on Audit while it's
  // still mid-flight.
  //
  // Reset local state on stage change before the async lookup -- same
  // reasoning as the trim badge: navigating from a stage with a running
  // job to one without otherwise leaves the stale ``job`` in place.
  useEffect(() => {
    let cancelled = false;
    setJob(null);
    setError(null);
    api
      .listJobs()
      .then(async (jobs) => {
        if (cancelled) return;
        const active = jobs.find(
          (j) =>
            j.kind === "shot_detect" &&
            j.stage_number === stageNumber &&
            (j.status === "pending" || j.status === "running"),
        );
        if (!active) return;
        setJob(active);
        try {
          const final = await api.pollJob(active.id, setJob);
          if (cancelled) return;
          if (final.status === "succeeded") await onComplete();
          else if (final.status === "failed")
            setError(final.error ?? "Shot detection failed");
        } finally {
          if (!cancelled) setJob(null);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [stageNumber, onComplete]);

  const runDetect = useCallback(
    async (reset: boolean) => {
      setError(null);
      try {
        const initial = await api.detectShots(slug, stageNumber, { reset });
        setJob(initial);
        const final = await api.pollJob(initial.id, setJob);
        if (final.status === "failed") {
          setError(final.error ?? "Shot detection failed");
          return;
        }
        await onComplete();
      } catch (err) {
        setError(err instanceof ApiError ? err.detail : String(err));
      } finally {
        setJob(null);
      }
    },
    [slug, stageNumber, onComplete],
  );

  const onClick = useCallback(() => void runDetect(false), [runDetect]);
  const onResetClick = useCallback(() => {
    if (
      !window.confirm(
        "Reset & re-detect shots for this stage?\n\n" +
          "This wipes your kept / rejected decisions and runs detection from " +
          "scratch. Use this when the previous detection went badly (bad beep, " +
          "wrong stage time, etc.) and you want to start over.",
      )
    )
      return;
    void runDetect(true);
  }, [runDetect]);

  // CSV-only re-run. Reuses the existing exportStage job (the server
  // supports write_csv with everything else off, see ui/exports.py). The
  // JobsPanel surfaces progress + the result path; we just kick it off
  // and clear errors here.
  const onExportShotsClick = useCallback(() => {
    setError(null);
    void (async () => {
      try {
        await api.exportStage(slug, stageNumber, {
          write_trim: false,
          write_csv: true,
          write_fcpxml: false,
          write_report: false,
          write_overlay: false,
        });
      } catch (err) {
        setError(err instanceof ApiError ? err.detail : String(err));
      }
    })();
  }, [slug, stageNumber]);

  const pct = job?.progress != null ? Math.round(job.progress * 100) : null;

  // While a job is running we always surface progress inline so the
  // operator sees what's happening. When idle, behaviour depends on
  // whether candidates exist: no candidates = inline action-required
  // button; candidates = collapsed "..." overflow menu (the design's
  // intended utility placement).
  if (running) {
    return (
      <span
        role="status"
        title={`Shot detection ${pct != null ? `(${pct}%)` : "running"}`}
        className="inline-flex items-center gap-1.5 rounded-md border border-led-deep bg-led-tint px-2.5 py-2 font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-led-soft"
      >
        <Loader2 className="size-3 animate-spin" aria-hidden />
        <span className="tabular-nums">
          Detecting
          {pct != null ? ` ${pct.toString().padStart(2, " ")}%` : "..."}
        </span>
      </span>
    );
  }

  if (!hasCandidates) {
    return (
      <span className="flex items-center gap-2">
        <Badge variant="secondary" title="No candidates yet -- run shot detection">
          no candidates
        </Badge>
        <Button
          size="sm"
          variant="outline"
          onClick={onClick}
          disabled={blocked}
          title={reason ?? "Run splitsmith.shot_detect on the audit clip"}
        >
          Detect shots
        </Button>
        {error ? <span className="text-xs text-destructive">{error}</span> : null}
      </span>
    );
  }

  return (
    <DetectShotsMenu
      blocked={blocked}
      reason={reason}
      onRerun={onClick}
      onReset={onResetClick}
      onExportShots={onExportShotsClick}
      error={error}
    />
  );
}

interface DetectShotsMenuProps {
  blocked: boolean;
  reason: string | null;
  onRerun: () => void;
  onReset: () => void;
  onExportShots: () => void;
  error: string | null;
}

function DetectShotsMenu({
  blocked,
  reason,
  onRerun,
  onReset,
  onExportShots,
  error,
}: DetectShotsMenuProps) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (!wrapperRef.current) return;
      if (!wrapperRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div ref={wrapperRef} className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Detection utilities"
        title={reason ?? "Detection utilities"}
        className={cn(
          "inline-flex size-9 items-center justify-center rounded-md border border-rule bg-surface-2 text-muted transition-colors hover:bg-surface-3 hover:text-ink",
          open && "border-rule-strong bg-surface-3 text-ink",
        )}
      >
        <MoreHorizontal className="size-4" aria-hidden />
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+6px)] z-40 w-64 overflow-hidden rounded-md border border-rule-strong bg-surface-1 shadow-[0_12px_32px_-12px_rgba(0,0,0,0.7)]"
        >
          <button
            role="menuitem"
            type="button"
            onClick={() => {
              setOpen(false);
              onRerun();
            }}
            disabled={blocked}
            className="flex w-full items-start gap-2 px-3 py-2.5 text-left text-[0.8125rem] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            <div className="min-w-0">
              <div className="font-display text-[0.75rem] font-bold uppercase tracking-[0.06em]">
                Re-run detection
              </div>
              <div className="mt-0.5 text-[0.6875rem] text-muted">
                Refresh candidates; kept shots are preserved.
              </div>
            </div>
          </button>
          <button
            role="menuitem"
            type="button"
            onClick={() => {
              setOpen(false);
              onExportShots();
            }}
            className="flex w-full items-start gap-2 px-3 py-2.5 text-left text-[0.8125rem] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
          >
            <div className="min-w-0">
              <div className="font-display text-[0.75rem] font-bold uppercase tracking-[0.06em]">
                Export shot table
              </div>
              <div className="mt-0.5 text-[0.6875rem] text-muted">
                CSV · all confirmed shots in this stage.
              </div>
            </div>
          </button>
          <button
            role="menuitem"
            type="button"
            onClick={() => {
              setOpen(false);
              onReset();
            }}
            disabled={blocked}
            className="flex w-full items-start gap-2 border-t border-rule px-3 py-2.5 text-left text-[0.8125rem] text-destructive transition-colors hover:bg-destructive/10 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <div className="min-w-0">
              <div className="font-display text-[0.75rem] font-bold uppercase tracking-[0.06em]">
                Reset & re-detect
              </div>
              <div className="mt-0.5 text-[0.6875rem] text-destructive/80">
                Wipes kept / rejected decisions and starts over.
              </div>
            </div>
          </button>
          {reason ? (
            <div className="border-t border-rule px-3 py-2 text-[0.6875rem] text-muted">
              {reason}
            </div>
          ) : null}
          {error ? (
            <div className="border-t border-rule px-3 py-2 text-[0.6875rem] text-destructive">
              {error}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

interface TrimNowBadgeProps {
  slug: string;
  stageNumber: number;
  hasBeep: boolean;
  hasStageTime: boolean;
  onProjectUpdate: (p: MatchProject) => void;
}

function TrimNowBadge({
  slug,
  stageNumber,
  hasBeep,
  hasStageTime,
  onProjectUpdate,
}: TrimNowBadgeProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const blocked = !hasBeep || !hasStageTime;
  const reason = !hasBeep
    ? "Detect or set the beep first."
    : !hasStageTime
      ? "Import a scoreboard so the stage time is known."
      : null;
  const running = job != null && (job.status === "pending" || job.status === "running");

  // Auto-adopt an in-flight trim on mount / stage change. After a page
  // reload the server still has the running job; we reattach to it
  // instead of leaving the user a "Trim now" button that double-submits.
  //
  // Reset local state synchronously on stage change before the async
  // lookup -- otherwise navigating from a stage with a running trim to
  // one without leaves the stale ``job`` in place (the effect's no-match
  // branch never cleared it), making every other stage look like it's
  // trimming too.
  useEffect(() => {
    let cancelled = false;
    setJob(null);
    setError(null);
    api
      .listJobs()
      .then(async (jobs) => {
        if (cancelled) return;
        const active = jobs.find(
          (j) =>
            j.kind === "trim" &&
            j.stage_number === stageNumber &&
            (j.status === "pending" || j.status === "running"),
        );
        if (!active) return;
        setJob(active);
        try {
          const final = await api.pollJob(active.id, setJob);
          if (cancelled) return;
          if (final.status === "succeeded") onProjectUpdate(await api.getProject(slug));
          else if (final.status === "failed") setError(final.error ?? "Trim failed");
        } finally {
          if (!cancelled) setJob(null);
        }
      })
      .catch(() => {
        /* swallow -- the user can still click Trim now to retry */
      });
    return () => {
      cancelled = true;
    };
  }, [stageNumber, onProjectUpdate]);

  const onClick = useCallback(async () => {
    setError(null);
    try {
      // The server returns the existing active job if one is in flight,
      // so two clicks (or a click after reload) don't spawn parallels.
      const initial = await api.trimStage(slug, stageNumber);
      setJob(initial);
      const final = await api.pollJob(initial.id, setJob);
      if (final.status === "failed") {
        setError(final.error ?? "Trim failed");
        return;
      }
      const fresh = await api.getProject(slug);
      onProjectUpdate(fresh);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setJob(null);
    }
  }, [stageNumber, onProjectUpdate]);

  const pct = job?.progress != null ? Math.round(job.progress * 100) : null;

  return (
    <span className="flex items-center gap-2">
      <Badge
        variant="destructive"
        title="The audit screen is reading the full source clip. Trim makes scrubbing frame-accurate."
      >
        untrimmed -- scrubbing will be slow
      </Badge>
      <Button
        size="sm"
        variant="outline"
        onClick={onClick}
        disabled={running || blocked}
        // Fixed width so progress text changes (e.g. "Trimming... (12%)" ->
        // "(45%)") don't reflow the row. Reflow on Chromium closes any open
        // native <select> dropdown elsewhere on the page.
        className="min-w-[12rem] justify-center"
        title={reason ?? "Re-encode with short GOP for scrub-friendly playback"}
      >
        {running ? <Loader2 className="mr-1 size-3 animate-spin" /> : null}
        <span className="tabular-nums">
          {running ? "Trimming..." : "Trim now"}
          {running && pct != null ? ` (${pct.toString().padStart(2, " ")}%)` : null}
        </span>
      </Button>
      {error ? <span className="text-xs text-destructive">{error}</span> : null}
    </span>
  );
}

function deriveMarkers(audit: StageAudit | null): AuditMarker[] {
  if (!audit) return [];
  const candidates = audit._candidates_pending_audit?.candidates ?? [];
  const shotsByCandidateNumber = new Map<number, true>();
  for (const s of audit.shots ?? []) {
    if (s.candidate_number != null) shotsByCandidateNumber.set(s.candidate_number, true);
  }
  const markers: AuditMarker[] = candidates.map((c) => ({
    id: `cand-${c.candidate_number}`,
    kind: shotsByCandidateNumber.has(c.candidate_number) ? "detected" : "rejected",
    time: c.time,
    candidateNumber: c.candidate_number,
    confidence: c.confidence ?? null,
    peakAmplitude: c.peak_amplitude ?? null,
    note: "",
  }));
  // Manual shots: those without a matching candidate_number.
  // Derived (promoted) fixtures may include shots with ``time: null`` for
  // anchor shots that the secondary couldn't snap; skip those here so the
  // marker drawer doesn't crash on ``time.toFixed(...)``.
  for (const s of audit.shots ?? []) {
    if (s.time == null) continue;
    if (s.candidate_number == null || s.source === "manual") {
      markers.push({
        id: `manual-shot-${s.shot_number}`,
        kind: "manual",
        time: s.time,
        candidateNumber: s.candidate_number ?? null,
        confidence: null,
        peakAmplitude: null,
        note: "",
      });
    }
  }
  return markers;
}


