/**
 * The stage's timeline band, shared by Coach and Breakdown (#1371): the
 * shared ``Timeline`` with the Audio track (``WaveformTrack`` over the
 * stage's peaks) and the region lanes (``LaneEditor``), the Full-resolution
 * video entry in its More menu, and the lane hints under it. Every lane rule
 * lives in the lane editor and ``useStageEvents``; this file only wires them
 * to the workspace.
 */
import { useState, type ReactNode } from "react";

import { LANE_ROWS, LaneEditor, LaneHints } from "@/components/coach/LaneEditor";
import { Timeline } from "@/components/timeline/Timeline";
import { WaveformTrack } from "@/components/timeline/WaveformTrack";
import { menuItemClass } from "@/components/ui/Menu";
import { type Zoom } from "@/lib/timelineView";
import type { StageView, StageWorkspace } from "@/lib/useStageWorkspace";

/** The Audio row's height in the band. */
export const AUDIO_ROW_HEIGHT = 56;

export interface StageBandProps {
  ws: StageWorkspace;
  view: StageView;
  /** A short window: a tighter header row, and the lane hints hidden until
   *  "Lane keys" in the band's menu turns them on. */
  compact?: boolean;
  /** Controls drawn in the header row (Breakdown's transport when compact). */
  toolbar?: ReactNode;
  /** The Audio row's height: taller where Breakdown's splitter gives the band room (#1373). */
  audioHeight?: number;
  /** The rows' height when the splitter makes the band shorter than its rows: they scroll under the fixed ruler (Timeline `rowsHeight`). */
  rowsHeight?: number;
}

export function StageBand({ ws, view, compact = false, toolbar, audioHeight = AUDIO_ROW_HEIGHT, rowsHeight }: StageBandProps) {
  const [zoom, setZoom] = useState<Zoom>(null);
  const [keysOn, setKeysOn] = useState(false);
  const { coach, peaks, peaksLoading, scrub, regions } = ws;
  const { stageTime, tFromBeep, audioBeep, eventsReadOnly } = view;
  if (!coach) return null;
  const showHints = !compact || keysOn;
  return (
    <div>
      <Timeline
        duration={stageTime}
        origin={0}
        fps={30}
        currentTime={tFromBeep}
        playing={ws.isPlaying}
        onSeek={ws.seekFromBeep}
        zoom={zoom}
        onZoomChange={setZoom}
        dense={compact}
        rowsHeight={rowsHeight}
        toolbar={toolbar}
        menuExtra={
          scrub.available || (compact && !eventsReadOnly) ? (
            <>
              {scrub.available ? (
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
              ) : null}
              {compact && !eventsReadOnly ? (
                <button
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={keysOn}
                  className={menuItemClass}
                  onClick={() => setKeysOn(!keysOn)}
                >
                  Lane keys
                  <span className="ml-auto text-sm text-muted">{keysOn ? "on" : "off"}</span>
                </button>
              ) : null}
            </>
          ) : undefined
        }
        tracks={[
          {
            id: "audio",
            rows: [{ label: "Audio", height: audioHeight }],
            seekable: true,
            render: (geom) =>
              peaksLoading ? (
                // Nothing drawn yet: "No audio" would otherwise flash on
                // every stage load before a normal-latency request has had
                // a chance to resolve.
                <div style={{ height: audioHeight }} />
              ) : (
                <WaveformTrack
                  peaks={peaks?.peaks ?? null}
                  clipDuration={peaks?.duration ?? 0}
                  from={audioBeep}
                  to={audioBeep + stageTime}
                  geom={geom}
                  height={audioHeight}
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
                onSeek={ws.seekFromBeep}
                onChange={regions.change}
                onCancel={regions.cancel}
              />
            ),
          },
        ]}
      />
      {showHints ? <LaneHints readOnly={eventsReadOnly} /> : null}
    </div>
  );
}
