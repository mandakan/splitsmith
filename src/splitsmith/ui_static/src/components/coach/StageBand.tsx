/**
 * The stage's timeline band, shared by Coach and Breakdown (#1371): the
 * shared ``Timeline`` with the Audio track (``WaveformTrack`` over the
 * stage's peaks) and the region lanes (``LaneEditor``), the Full-resolution
 * video entry in its More menu, and the lane hints under it. Every lane rule
 * lives in the lane editor and ``useStageEvents``; this file only wires them
 * to the workspace.
 */
import { useState } from "react";

import { LANE_ROWS, LaneEditor, LaneHints } from "@/components/coach/LaneEditor";
import { Timeline } from "@/components/timeline/Timeline";
import { WaveformTrack } from "@/components/timeline/WaveformTrack";
import { menuItemClass } from "@/components/ui/Menu";
import { type Zoom } from "@/lib/timelineView";
import type { StageView, StageWorkspace } from "@/lib/useStageWorkspace";

/** The Audio row's height in the band. */
export const AUDIO_ROW_HEIGHT = 56;

export function StageBand({ ws, view }: { ws: StageWorkspace; view: StageView }) {
  const [zoom, setZoom] = useState<Zoom>(null);
  const { coach, peaks, peaksLoading, scrub, regions } = ws;
  const { stageTime, tFromBeep, audioBeep, eventsReadOnly } = view;
  if (!coach) return null;
  const seekFromBeep = (t: number) => {
    if (ws.videoRef.current) ws.videoRef.current.currentTime = coach.beep_time + t;
  };
  return (
    <div>
      <Timeline
        duration={stageTime}
        origin={0}
        fps={30}
        currentTime={tFromBeep}
        playing={ws.isPlaying}
        onSeek={seekFromBeep}
        zoom={zoom}
        onZoomChange={setZoom}
        menuExtra={
          scrub.available ? (
            <button
              type="button"
              role="menuitemcheckbox"
              aria-checked={scrub.fullRes}
              className={menuItemClass}
              onClick={() => scrub.setFullRes(!scrub.fullRes)}
            >
              Full-resolution video
              <span className="ml-auto text-sm text-muted">{scrub.fullRes ? "on" : "off"}</span>
            </button>
          ) : undefined
        }
        tracks={[
          {
            id: "audio",
            rows: [{ label: "Audio", height: AUDIO_ROW_HEIGHT }],
            seekable: true,
            render: (geom) =>
              peaksLoading ? (
                // Nothing drawn yet: "No audio" would otherwise flash on
                // every stage load before a normal-latency request has had
                // a chance to resolve.
                <div style={{ height: AUDIO_ROW_HEIGHT }} />
              ) : (
                <WaveformTrack
                  peaks={peaks?.peaks ?? null}
                  clipDuration={peaks?.duration ?? 0}
                  from={audioBeep}
                  to={audioBeep + stageTime}
                  geom={geom}
                  height={AUDIO_ROW_HEIGHT}
                />
              ),
          },
          {
            id: "lanes",
            rows: LANE_ROWS,
            render: () => (
              <LaneEditor
                shots={coach.shots}
                events={regions.events}
                stageTime={stageTime}
                currentTime={tFromBeep}
                selectedId={regions.selectedId}
                readOnly={eventsReadOnly}
                onSelect={regions.select}
                onSeek={seekFromBeep}
                onChange={regions.change}
                onCancel={regions.cancel}
              />
            ),
          },
        ]}
      />
      <LaneHints readOnly={eventsReadOnly} />
    </div>
  );
}
