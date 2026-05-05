/**
 * Promote-from-anchor diff-confirm review (issue #125).
 *
 * URL: /promote-review?fixture=<derived-fixture-path>&anchor=<anchor-fixture-path>
 *
 * Shows two waveforms (anchor frozen, secondary editable) for each shot
 * in the pre-filled derived fixture. The user steps through shots and
 * confirms (Y), nudges (left/right), or escalates missed shots (N).
 *
 * After all shots are reviewed the fixture is saved with `source` set to
 * "confirmed" / "promoted-missed-escalated" / etc. on each shot. The
 * user then opens it in the regular Review page for full edit if needed.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Save,
  SkipForward,
  X,
} from "lucide-react";

import { MarkerLayer, type AuditMarker } from "@/components/MarkerLayer";
import { Waveform } from "@/components/Waveform";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  api,
  type AuditShot,
  type PeaksResult,
  type PromoteReport,
  type PromoteSnapResult,
  type StageAudit,
} from "@/lib/api";

const PEAK_BINS = 1500;
const NUDGE_MS = 5;
const ZOOM_WINDOW_S = 0.6; // half-window around current shot (seconds each side)

type ShotStatus =
  | "pending"
  | "confirmed"
  | "nudged"
  | "missed-detector"
  | "missed-anchor-wrong"
  | "missed-dropped";

interface ShotState {
  shotNumber: number;
  time: number | null; // current time in secondary clip
  anchorTime: number;
  status: ShotStatus;
  originalSource: string;
  displacement_ms: number | null;
  sanityFlag: string;
  subclass: string;
}

// ---------------------------------------------------------------------------
// Escalation overlay (native, no Dialog dep)
// ---------------------------------------------------------------------------

function EscalationModal({
  shotNumber,
  onClose,
  onSelect,
}: {
  shotNumber: number;
  onClose: () => void;
  onSelect: (action: "missed-detector" | "missed-anchor-wrong" | "missed-dropped") => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-popover border border-border rounded-lg shadow-xl p-5 w-80 flex flex-col gap-3">
        <div className="font-semibold text-sm">Shot {shotNumber} -- no candidate found</div>
        <div className="text-xs text-muted-foreground">
          Choose how to record this shot in the derived fixture.
        </div>
        {(
          [
            {
              action: "missed-detector" as const,
              title: "Detector missed it",
              desc: "I can hear/see the shot -- the secondary detector failed. Records as a labelled hard negative.",
            },
            {
              action: "missed-anchor-wrong" as const,
              title: "Anchor was wrong",
              desc: "The headcam anchor labelled a shot here incorrectly. Flags the anchor for re-audit.",
            },
            {
              action: "missed-dropped" as const,
              title: "Drop from this fixture",
              desc: "Camera was occluded, mic clipped, or shot wasn't acoustically present. Not penalised in eval.",
            },
          ] as const
        ).map(({ action, title, desc }) => (
          <button
            key={action}
            className="text-left rounded border border-border px-3 py-2.5 hover:bg-accent transition-colors"
            onClick={() => onSelect(action)}
          >
            <div className="text-sm font-medium">{title}</div>
            <div className="text-xs text-muted-foreground mt-0.5">{desc}</div>
          </button>
        ))}
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status badge helper
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: ShotStatus }) {
  const map: Record<ShotStatus, { label: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
    pending: { label: "pending", variant: "outline" },
    confirmed: { label: "confirmed", variant: "default" },
    nudged: { label: "nudged", variant: "default" },
    "missed-detector": { label: "detector missed", variant: "destructive" },
    "missed-anchor-wrong": { label: "anchor wrong", variant: "destructive" },
    "missed-dropped": { label: "dropped", variant: "secondary" },
  };
  const { label, variant } = map[status];
  return <Badge variant={variant}>{label}</Badge>;
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function PromoteReview() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const fixturePath = params.get("fixture");
  const anchorPath = params.get("anchor");

  // Fixture data
  const [fixture, setFixture] = useState<StageAudit | null>(null);
  const [anchorFixture, setAnchorFixture] = useState<StageAudit | null>(null);
  const [report, setReport] = useState<PromoteReport | null>(null);

  // Waveform peaks
  const [derivedPeaks, setDerivedPeaks] = useState<PeaksResult | null>(null);
  const [anchorPeaks, setAnchorPeaks] = useState<PeaksResult | null>(null);

  // Shot review state
  const [shots, setShots] = useState<ShotState[]>([]);
  const [currentIdx, setCurrentIdx] = useState(0);

  // UI state
  const [escalationShot, setEscalationShot] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);

  // Load fixture + anchor + report
  useEffect(() => {
    if (!fixturePath) return;
    Promise.all([
      api.getFixtureAudit(fixturePath),
      api.getFixturePeaks(fixturePath, PEAK_BINS),
      ...(anchorPath
        ? [api.getFixtureAudit(anchorPath), api.getFixturePeaks(anchorPath, PEAK_BINS)]
        : []),
    ])
      .then(([fix, peaks, ancFix, ancPeaks]) => {
        setFixture(fix as StageAudit);
        setDerivedPeaks(peaks as PeaksResult);
        if (ancFix) setAnchorFixture(ancFix as StageAudit);
        if (ancPeaks) setAnchorPeaks(ancPeaks as PeaksResult);

        // Initialise shot state from fixture shots
        const initShots: ShotState[] = (fix as StageAudit).shots.map((s: AuditShot) => ({
          shotNumber: s.shot_number,
          time: s.time ?? null,
          anchorTime: (s as any).anchor_time ?? s.time ?? 0,
          status: "pending",
          originalSource: (s as any).source ?? "promoted",
          displacement_ms: (s as any).snap_displacement_ms ?? null,
          sanityFlag: (s as any).sanity_flag ?? "",
          subclass: s.subclass ?? "unknown",
        }));
        setShots(initShots);
      })
      .catch((e) => setError(String(e)));
  }, [fixturePath, anchorPath]);

  // Load promotion report
  useEffect(() => {
    if (!fixturePath) return;
    const slug = fixturePath.split("/").pop()?.replace(".json", "") ?? "";
    api.getPromoteReport(slug).catch(() => null).then((r) => r && setReport(r));
  }, [fixturePath]);

  // Keyboard handler
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (escalationShot !== null) return;
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;

      const shot = shots[currentIdx];
      if (!shot) return;

      if (e.key === "y" || e.key === "Y") {
        e.preventDefault();
        confirmShot(currentIdx);
      } else if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        if (shot.time === null) {
          setEscalationShot(shot.shotNumber);
        } else {
          confirmShot(currentIdx);
        }
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        nudgeShot(currentIdx, -NUDGE_MS);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        nudgeShot(currentIdx, +NUDGE_MS);
      } else if (e.key === "ArrowDown" || e.key === "j") {
        e.preventDefault();
        setCurrentIdx((i) => Math.min(i + 1, shots.length - 1));
      } else if (e.key === "ArrowUp" || e.key === "k") {
        e.preventDefault();
        setCurrentIdx((i) => Math.max(i - 1, 0));
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [shots, currentIdx, escalationShot]);

  const confirmShot = useCallback(
    (idx: number) => {
      setShots((prev) => {
        const next = [...prev];
        const s = { ...next[idx] };
        s.status = s.status === "nudged" ? "nudged" : "confirmed";
        next[idx] = s;
        return next;
      });
      setCurrentIdx((i) => Math.min(i + 1, shots.length - 1));
    },
    [shots.length],
  );

  const nudgeShot = useCallback((idx: number, deltaMsArg: number) => {
    setShots((prev) => {
      const next = [...prev];
      const s = { ...next[idx] };
      if (s.time === null) return prev;
      s.time = Math.max(0, s.time + deltaMsArg / 1000);
      s.status = "nudged";
      next[idx] = s;
      return next;
    });
  }, []);

  const escalate = useCallback(
    (action: "missed-detector" | "missed-anchor-wrong" | "missed-dropped") => {
      if (escalationShot === null) return;
      const idx = shots.findIndex((s) => s.shotNumber === escalationShot);
      if (idx < 0) return;
      setShots((prev) => {
        const next = [...prev];
        const s = { ...next[idx], status: action };
        next[idx] = s;
        return next;
      });
      setEscalationShot(null);
      setCurrentIdx((i) => Math.min(i + 1, shots.length - 1));
    },
    [escalationShot, shots],
  );

  const save = useCallback(async () => {
    if (!fixturePath || !fixture) return;
    setSaving(true);
    try {
      const updated: StageAudit = {
        ...fixture,
        shots: shots.map((s) => ({
          shot_number: s.shotNumber,
          time: s.time ?? undefined,
          ms_after_beep: s.time != null && fixture.beep_time != null
            ? Math.round((s.time - fixture.beep_time) * 1000)
            : undefined,
          source: s.status,
          subclass: s.subclass,
          candidate_number: undefined,
        } as unknown as AuditShot)),
      };
      await api.saveFixtureAudit(fixturePath, updated);
      setSaved(true);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }, [fixturePath, fixture, shots]);

  // Waveform zoom: centre on current shot in secondary
  const currentShot = shots[currentIdx];
  const derivedZoomCenter = currentShot?.time ?? 0;
  const anchorZoomCenter = anchorFixture && currentShot
    ? currentShot.anchorTime
    : 0;

  // Build anchor markers (frozen)
  const anchorMarkers: AuditMarker[] = useMemo(() => {
    if (!anchorFixture) return [];
    return anchorFixture.shots.map((s, i) => ({
      id: `anchor-${i}`,
      time: s.time ?? 0,
      kind: "shot" as const,
      label: `${s.shot_number}`,
      color: "blue",
    }));
  }, [anchorFixture]);

  // Build derived markers
  const derivedMarkers: AuditMarker[] = useMemo(
    () =>
      shots
        .filter((s) => s.time !== null)
        .map((s, i) => ({
          id: `shot-${i}`,
          time: s.time!,
          kind: "shot" as const,
          label: `${s.shotNumber}`,
          color:
            s.status === "confirmed" || s.status === "nudged"
              ? "green"
              : s.status === "pending"
                ? "gray"
                : "red",
        })),
    [shots],
  );

  if (!fixturePath) {
    return (
      <div className="p-8 text-muted-foreground">
        Missing <code>fixture</code> query parameter.
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8 flex gap-2 items-start text-destructive">
        <AlertCircle className="mt-0.5 shrink-0" size={16} />
        <pre className="text-xs whitespace-pre-wrap">{error}</pre>
      </div>
    );
  }

  if (!fixture || shots.length === 0) {
    return (
      <div className="p-8 text-muted-foreground text-sm">Loading fixture...</div>
    );
  }

  const pending = shots.filter((s) => s.status === "pending").length;
  const slug = fixturePath.split("/").pop()?.replace(".json", "") ?? "";

  return (
    <div ref={containerRef} className="h-screen flex flex-col overflow-hidden bg-background">
      {/* Header */}
      <div className="border-b px-4 py-2 flex items-center gap-3 shrink-0">
        <Button variant="ghost" size="icon" onClick={() => navigate(-1)}>
          <ChevronLeft size={16} />
        </Button>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium truncate">{slug}</div>
          {fixture.anchor && (
            <div className="text-xs text-muted-foreground">
              derived from{" "}
              <span className="font-mono">{(fixture.anchor as any).fixture_slug}</span>
              {" "}
              <span className="text-[10px] font-mono opacity-60">
                {(fixture.anchor as any).revision_sha?.slice(0, 8)}
              </span>
            </div>
          )}
        </div>
        {report && (
          <div className="flex gap-2 text-xs text-muted-foreground">
            <span>
              {report.counts.snapped}/{report.counts.anchor_shots} snapped
            </span>
            {report.counts.missed > 0 && (
              <span className="text-destructive">{report.counts.missed} missed</span>
            )}
            {report.cross_align.confidence < 1.5 && (
              <Badge variant="destructive" className="text-[10px] px-1.5">
                low align conf {report.cross_align.confidence.toFixed(2)}
              </Badge>
            )}
          </div>
        )}
        <div className="flex items-center gap-1">
          <span className="text-xs text-muted-foreground">{pending} pending</span>
          <Button
            size="sm"
            variant="outline"
            onClick={save}
            disabled={saving}
            className="gap-1"
          >
            <Save size={12} />
            {saving ? "Saving..." : saved ? "Saved" : "Save"}
          </Button>
          {fixturePath && (
            <Button
              size="sm"
              variant="ghost"
              className="gap-1"
              onClick={() =>
                navigate(`/review?fixture=${encodeURIComponent(fixturePath)}`)
              }
            >
              <ExternalLink size={12} />
              Full review
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Waveforms -- left panel */}
        <div className="flex flex-col flex-1 overflow-hidden border-r">
          {/* Anchor waveform (frozen) */}
          {anchorPeaks && anchorFixture && (
            <div className="flex flex-col border-b" style={{ height: "45%" }}>
              <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground font-medium bg-muted/30 shrink-0">
                anchor (frozen) &mdash; {anchorPath?.split("/").pop()?.replace(".json", "")}
              </div>
              <div className="flex-1 relative overflow-hidden">
                <Waveform
                  peaks={anchorPeaks.peaks}
                  duration={anchorPeaks.duration}
                  pixelsPerSecond={300}
                  scrollToTime={anchorZoomCenter}
                />
                <MarkerLayer
                  markers={anchorMarkers}
                  duration={anchorPeaks.duration}
                  pixelsPerSecond={300}
                  currentTime={anchorZoomCenter}
                />
              </div>
            </div>
          )}

          {/* Secondary waveform (editable) */}
          {derivedPeaks && (
            <div className="flex flex-col" style={{ flex: 1 }}>
              <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground font-medium bg-muted/30 shrink-0">
                secondary &mdash; {slug}
              </div>
              <div className="flex-1 relative overflow-hidden">
                <Waveform
                  peaks={derivedPeaks.peaks}
                  duration={derivedPeaks.duration}
                  pixelsPerSecond={300}
                  scrollToTime={derivedZoomCenter}
                />
                <MarkerLayer
                  markers={derivedMarkers}
                  duration={derivedPeaks.duration}
                  pixelsPerSecond={300}
                  currentTime={derivedZoomCenter}
                />
              </div>
            </div>
          )}
        </div>

        {/* Shot list -- right panel */}
        <div className="w-72 flex flex-col overflow-hidden shrink-0">
          {/* Keyboard hint */}
          <div className="px-3 py-2 border-b text-[10px] text-muted-foreground space-y-0.5 shrink-0">
            <div><kbd className="font-mono bg-muted px-1 rounded">Y</kbd> confirm &nbsp; <kbd className="font-mono bg-muted px-1 rounded">N</kbd> escalate</div>
            <div><kbd className="font-mono bg-muted px-1 rounded">←</kbd><kbd className="font-mono bg-muted px-1 rounded">→</kbd> nudge ±5ms &nbsp; <kbd className="font-mono bg-muted px-1 rounded">J</kbd><kbd className="font-mono bg-muted px-1 rounded">K</kbd> navigate</div>
          </div>

          {/* Shot list */}
          <div className="flex-1 overflow-y-auto">
            {shots.map((s, idx) => {
              const isCurrent = idx === currentIdx;
              const isMissed = s.originalSource === "promoted-missed";
              return (
                <button
                  key={s.shotNumber}
                  className={`w-full text-left px-3 py-2 border-b flex flex-col gap-0.5 transition-colors ${
                    isCurrent ? "bg-accent" : "hover:bg-muted/50"
                  }`}
                  onClick={() => setCurrentIdx(idx)}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">
                      Shot {s.shotNumber}
                      {isMissed && (
                        <span className="ml-1 text-[10px] text-destructive">missed</span>
                      )}
                    </span>
                    <StatusBadge status={s.status} />
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                    {s.time !== null ? (
                      <span>{s.time.toFixed(3)}s</span>
                    ) : (
                      <span className="text-destructive">no candidate</span>
                    )}
                    {s.displacement_ms !== null && (
                      <span
                        className={
                          Math.abs(s.displacement_ms) > 30
                            ? "text-amber-500"
                            : "text-muted-foreground"
                        }
                      >
                        {s.displacement_ms > 0 ? "+" : ""}
                        {s.displacement_ms.toFixed(0)}ms
                      </span>
                    )}
                    <span className="ml-auto opacity-60">{s.subclass}</span>
                  </div>
                  {s.sanityFlag && s.sanityFlag !== "no-candidate" && (
                    <div className="text-[9px] text-amber-500">{s.sanityFlag}</div>
                  )}
                </button>
              );
            })}
          </div>

          {/* Action buttons for current shot */}
          <div className="border-t p-2 shrink-0 flex flex-col gap-1.5">
            {currentShot && (
              <>
                <div className="text-xs font-medium text-center text-muted-foreground">
                  Shot {currentShot.shotNumber} of {shots.length}
                </div>
                <div className="flex gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1 flex-1"
                    onClick={() => nudgeShot(currentIdx, -NUDGE_MS)}
                    disabled={currentShot.time === null}
                  >
                    <ArrowLeft size={12} />
                    {NUDGE_MS}ms
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1 flex-1"
                    onClick={() => nudgeShot(currentIdx, +NUDGE_MS)}
                    disabled={currentShot.time === null}
                  >
                    {NUDGE_MS}ms
                    <ArrowRight size={12} />
                  </Button>
                </div>
                <div className="flex gap-1">
                  <Button
                    size="sm"
                    className="gap-1 flex-1"
                    onClick={() => confirmShot(currentIdx)}
                    disabled={
                      currentShot.time === null && currentShot.status === "pending"
                    }
                  >
                    <Check size={12} />Y confirm
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    className="gap-1 flex-1"
                    onClick={() => {
                      if (currentShot.time === null) {
                        setEscalationShot(currentShot.shotNumber);
                      } else {
                        confirmShot(currentIdx);
                      }
                    }}
                  >
                    {currentShot.time === null ? (
                      <>
                        <X size={12} />N escalate
                      </>
                    ) : (
                      <>
                        <SkipForward size={12} />skip
                      </>
                    )}
                  </Button>
                </div>
                <div className="flex gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    className="gap-1"
                    onClick={() => setCurrentIdx((i) => Math.max(i - 1, 0))}
                    disabled={currentIdx === 0}
                  >
                    <ChevronLeft size={12} />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="gap-1 flex-1"
                    onClick={() =>
                      setCurrentIdx((i) => Math.min(i + 1, shots.length - 1))
                    }
                    disabled={currentIdx === shots.length - 1}
                  >
                    next <ChevronRight size={12} />
                  </Button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Escalation modal */}
      {escalationShot !== null && (
        <EscalationModal
          shotNumber={escalationShot}
          onClose={() => setEscalationShot(null)}
          onSelect={escalate}
        />
      )}
    </div>
  );
}
