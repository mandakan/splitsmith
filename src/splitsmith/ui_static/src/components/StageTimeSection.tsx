/**
 * Stage duration for a stage without scoreboard data (Audit's trim gate).
 *
 * The trim / shot-detect pipeline gates on ``stage.time_seconds > 0``.
 * A shooter with no scoreboard times types the time here (spec
 * 2026-10-22, mockup C), the way a scoresheet shows it; picking the end
 * of the stage on the primary's waveform stays as the second way, for
 * when only the video knows. Either way the server stores the value with
 * ``time_seconds_manual=True`` so a later scoreboard sync keeps it. Every
 * stage's time, scores and order are also on the Stage details page,
 * which this links to.
 *
 * Visibility: a prompt when the stage has no time; an "Edit stage time"
 * button when the time was entered by hand; nothing for a scoreboard
 * time.
 */
import { useState } from "react";
import { Link } from "react-router-dom";

import { BeepWaveformPicker } from "@/components/BeepSection";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/Label";
import { api, type MatchProject, type StageEntry, type StageVideo } from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";
import { parseNumber } from "@/lib/stageDetails";

interface Props {
  slug: string;
  stageNumber: number;
  stage: StageEntry;
  primary: StageVideo;
  onProjectUpdate: (next: MatchProject) => void;
  setError: (msg: string | null) => void;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function StageTimeSection({ slug, stageNumber, stage, primary, onProjectUpdate, setError }: Props) {
  const href = useMatchHref();
  const hasNoValue = stage.time_seconds <= 0;
  const hasManualValue = stage.time_seconds_manual && !hasNoValue;
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(hasNoValue ? "" : String(stage.time_seconds));
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const beepTime = primary.beep_time;

  if (!hasNoValue && !hasManualValue) return null;
  if (hasManualValue && !open) {
    return (
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)} title="Edit the stage time you entered">
        Edit stage time
      </Button>
    );
  }

  const value = parseNumber(text);
  const send = async (seconds: number | null) => {
    setBusy(true);
    setError(null);
    try {
      onProjectUpdate(await api.setStageTime(slug, stageNumber, seconds));
      setOpen(false);
      setPicking(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 rounded-[10px] border border-rule bg-surface p-4">
      <Label>Stage time</Label>
      {hasNoValue ? (
        <p className="text-md text-ink-2">
          Stage {pad2(stageNumber)} has no time from the scoreboard. It is needed to trim the clip and find shots.
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <input
          aria-label={`Stage time for stage ${stageNumber}, in seconds`}
          inputMode="decimal"
          placeholder="12.65"
          className="numeral w-28 rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 text-base text-ink"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && value !== null && value > 0) void send(value);
          }}
        />
        <span className="text-md text-muted">seconds</span>
        <Button disabled={busy || value === null || value <= 0} onClick={() => void send(value)}>
          Save
        </Button>
        {hasManualValue ? (
          <>
            <Button variant="ghost" disabled={busy} onClick={() => void send(null)}>
              Clear
            </Button>
            <Button variant="ghost" disabled={busy} onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-3 text-md text-muted">
        {beepTime != null ? (
          <Button size="sm" onClick={() => setPicking((p) => !p)} aria-pressed={picking}>
            Pick the end on the waveform
          </Button>
        ) : null}
        <span>
          or fill in every stage on{" "}
          <Link className="text-ink underline decoration-rule-strong" to={href("stage-details", slug)}>
            Stage details
          </Link>
        </span>
      </div>
      {picking && beepTime != null ? (
        <BeepWaveformPicker
          slug={slug}
          stageNumber={stageNumber}
          videoId={primary.video_id}
          videoBeepTime={beepTime}
          draftSourceTime={value !== null ? beepTime + value : null}
          onPick={(sourceTime) => setText(Math.max(0, sourceTime - beepTime).toFixed(2))}
          setError={setError}
          snapEnabled={false}
          showFallbackBeepMarker={false}
          instructions="Scrub to the end of the stage (the RO's unload and show clear), then Save."
          ariaLabel={`Stage time picker for stage ${stageNumber}`}
        />
      ) : null}
    </div>
  );
}
