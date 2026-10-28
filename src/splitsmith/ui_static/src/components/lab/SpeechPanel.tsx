/**
 * The fixture page's speech review (#1778): the masker's proposal (or the
 * fixture's stored masks once reviewed) as bands on the waveform and rows to
 * play, trim or drop, then one decision. Rules live in ``lib/speechReview``;
 * the server re-checks every one before it writes.
 */
import { Loader2, Play, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Waveform } from "@/components/Waveform";
import { Button, Chip, Table, Td, Th, Tr, inputClass } from "@/components/ui";
import { api, type PeaksResult } from "@/lib/api";
import {
  DECISION_LABEL,
  canSave,
  initialRegions,
  isDirty,
  maskedSeconds,
  problems,
  type SpeechDecision,
  type SpeechReviewState,
  type SpeechSpan,
} from "@/lib/speechReview";
import { cn } from "@/lib/utils";

function Band({ span, duration, className }: { span: SpeechSpan; duration: number; className: string }) {
  if (duration <= 0) return null;
  return (
    <div
      className={cn("pointer-events-none absolute inset-y-0", className)}
      style={{
        left: `${(span.start / duration) * 100}%`,
        width: `${(Math.max(0, span.end - span.start) / duration) * 100}%`,
      }}
    />
  );
}

export function SpeechPanel({
  slug,
  peaks,
  time,
  onScrub,
  playFrom,
}: {
  slug: string;
  peaks: PeaksResult | null;
  time: number;
  onScrub: (t: number) => void;
  playFrom: (t: number) => void;
}) {
  const [state, setState] = useState<SpeechReviewState | null>(null);
  const [regions, setRegions] = useState<SpeechSpan[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<SpeechDecision | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .getLabSpeech(slug)
      .then((s) => {
        if (!alive) return;
        setState(s);
        setRegions(initialRegions(s));
      })
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [slug]);

  const duration = state?.duration || peaks?.duration || 0;
  const guards = useMemo(() => state?.protected ?? [], [state]);
  const found = useMemo(() => problems(regions, guards, duration), [regions, guards, duration]);

  const save = useCallback(
    async (decision: SpeechDecision) => {
      setSaving(decision);
      setError(null);
      try {
        const saved = await api.saveLabSpeech(slug, {
          decision,
          regions: decision === "no_speech" ? [] : regions,
        });
        setState(saved);
        setRegions(initialRegions(saved));
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setSaving(null);
      }
    },
    [regions, slug],
  );

  const edit = (i: number, key: "start" | "end", value: string) => {
    const v = Number(value);
    if (!Number.isFinite(v)) return;
    setRegions((rs) => rs.map((r, j) => (j === i ? { ...r, [key]: v } : r)));
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-4 py-3 text-sm text-muted">
        <Loader2 className="size-3.5 animate-spin" /> finding speech
      </div>
    );
  }

  const overlaps = state?.proposal?.protected_overlaps ?? [];
  const dirty = state ? isDirty(state, regions) : false;

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        {state?.review ? (
          <Chip tone="ok">
            {DECISION_LABEL[state.review.decision]}
            {dirty ? ", edited" : ""}
          </Chip>
        ) : (
          <Chip tone="warn">Not reviewed</Chip>
        )}
        {overlaps.length > 0 && <Chip tone="warn">Speech at a shot or the beep</Chip>}
        <span className="numeral text-sm text-ink-2">
          {regions.length} {regions.length === 1 ? "region" : "regions"}, {maskedSeconds(regions).toFixed(2)} s
        </span>
        {!state?.proposal && <span className="text-sm text-muted">detector unavailable</span>}
      </div>

      {peaks && (
        <Waveform
          peaks={peaks.peaks}
          duration={peaks.duration}
          currentTime={time}
          onScrub={onScrub}
          beepTime={peaks.beep_time}
          height={72}
          ariaLabel="Speech masks"
        >
          {guards.map((g, i) => (
            <Band key={`g-${i}`} span={g} duration={peaks.duration} className="bg-done/35" />
          ))}
          {overlaps.map((o, i) => (
            <Band key={`o-${i}`} span={o} duration={peaks.duration} className="bg-live/70" />
          ))}
          {regions.map((r, i) => (
            <Band
              key={`r-${i}`}
              span={r}
              duration={peaks.duration}
              className={found.has(i) ? "border border-destructive bg-destructive/20" : "bg-live/25"}
            />
          ))}
        </Waveform>
      )}

      <Table>
        <thead>
          <tr>
            <Th>Start s</Th>
            <Th>End s</Th>
            <Th>Length</Th>
            <Th>Check</Th>
            <Th />
          </tr>
        </thead>
        <tbody>
          {regions.map((r, i) => (
            <Tr key={i}>
              <Td>
                <input
                  aria-label={`Region ${i + 1} start`}
                  type="number"
                  step="0.01"
                  value={r.start}
                  onChange={(e) => edit(i, "start", e.target.value)}
                  className={cn(inputClass, "numeral w-24")}
                />
              </Td>
              <Td>
                <input
                  aria-label={`Region ${i + 1} end`}
                  type="number"
                  step="0.01"
                  value={r.end}
                  onChange={(e) => edit(i, "end", e.target.value)}
                  className={cn(inputClass, "numeral w-24")}
                />
              </Td>
              <Td className="numeral">{(r.end - r.start).toFixed(2)} s</Td>
              <Td className={found.has(i) ? "text-destructive" : "text-muted"}>{found.get(i) ?? "ok"}</Td>
              <Td>
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" aria-label={`Play region ${i + 1}`} onClick={() => playFrom(r.start)}>
                    <Play />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove region ${i + 1}`}
                    onClick={() => setRegions((rs) => rs.filter((_, j) => j !== i))}
                  >
                    <Trash2 />
                  </Button>
                </div>
              </Td>
            </Tr>
          ))}
        </tbody>
      </Table>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="ghost"
          onClick={() =>
            setRegions((rs) => [...rs, { start: Number(time.toFixed(2)), end: Number((time + 0.5).toFixed(2)) }])
          }
        >
          <Plus /> Region at playhead
        </Button>
        {state?.proposal && (
          <Button size="sm" variant="ghost" onClick={() => setRegions(initialRegions({ ...state, review: null }))}>
            Back to proposal
          </Button>
        )}
        <span className="flex-1" />
        <Button
          size="sm"
          variant="primary"
          disabled={saving !== null || !canSave("masked", regions, guards, duration)}
          onClick={() => void save("masked")}
        >
          {saving === "masked" && <Loader2 className="animate-spin" />} Accept masks
        </Button>
        <Button size="sm" disabled={saving !== null} onClick={() => void save("no_speech")}>
          No speech
        </Button>
        <Button
          size="sm"
          variant="destructive"
          disabled={saving !== null || !canSave("excluded", regions, guards, duration)}
          onClick={() => void save("excluded")}
        >
          Exclude stage
        </Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <p className="text-sm text-muted">Green marks the shots and the beep. A mask never covers them.</p>
    </div>
  );
}
