import { useMemo } from "react";

import { type LabEvalFixture } from "@/lib/api";

import { LAB_PALETTE, otherCandidateColor } from "./labPalette";

export function ZoomedWaveform({
  buffer,
  windowStart,
  windowEnd,
  playStart,
  playEnd,
  candidateTime,
  candidateColor,
  playhead,
  otherCandidates,
  truths,
  height = 120,
}: {
  buffer: AudioBuffer;
  windowStart: number;
  windowEnd: number;
  playStart: number;
  playEnd: number;
  candidateTime: number;
  candidateColor: string;
  playhead: number | null;
  otherCandidates: LabEvalFixture["candidates"];
  truths: { time: number; matched: boolean }[];
  height?: number;
}) {
  // Bin into 600 vertical strips; one peak per strip. Cheap to recompute
  // on slider drag because the windowed sample range is tiny (~50k samples
  // for a 1.5s window at 48kHz).
  const BINS = 600;
  const peaks = useMemo(() => {
    const sr = buffer.sampleRate;
    const startIdx = Math.max(0, Math.floor(windowStart * sr));
    const endIdx = Math.min(buffer.length, Math.ceil(windowEnd * sr));
    const ch = buffer.getChannelData(0);
    const span = Math.max(1, endIdx - startIdx);
    const out = new Float32Array(BINS);
    for (let i = 0; i < BINS; i++) {
      const s = startIdx + Math.floor((i * span) / BINS);
      const e = Math.max(s + 1, startIdx + Math.floor(((i + 1) * span) / BINS));
      let max = 0;
      for (let j = s; j < Math.min(endIdx, e); j++) {
        const v = Math.abs(ch[j]);
        if (v > max) max = v;
      }
      out[i] = max;
    }
    return out;
  }, [buffer, windowStart, windowEnd]);

  const VIEW_W = 1000; // SVG view-box width; CSS scales to container
  const span = windowEnd - windowStart;
  const xFor = (t: number) => ((t - windowStart) / span) * VIEW_W;
  const playX1 = xFor(playStart);
  const playX2 = xFor(playEnd);

  return (
    <div className="rounded border border-rule/60 bg-bg">
      <svg
        viewBox={`0 0 ${VIEW_W} ${height}`}
        preserveAspectRatio="none"
        className="block w-full"
        style={{ height }}
      >
        {/* Play-window highlight + edges (theme primary). */}
        <rect
          x={playX1}
          y={0}
          width={Math.max(1, playX2 - playX1)}
          height={height}
          fill={LAB_PALETTE.playWindow}
          fillOpacity={0.1}
        />
        <line
          x1={playX1}
          x2={playX1}
          y1={0}
          y2={height}
          stroke={LAB_PALETTE.playWindow}
          strokeWidth={1}
          strokeDasharray="3 3"
          strokeOpacity={0.7}
        />
        <line
          x1={playX2}
          x2={playX2}
          y1={0}
          y2={height}
          stroke={LAB_PALETTE.playWindow}
          strokeWidth={1}
          strokeDasharray="3 3"
          strokeOpacity={0.7}
        />

        {/* Peaks: vertical bars centered on midline */}
        <g fill="currentColor" opacity={0.55}>
          {Array.from(peaks).map((p, i) => {
            const h = Math.max(0.5, p * (height * 0.85));
            const cx = (i + 0.5) * (VIEW_W / BINS);
            const w = Math.max(0.6, VIEW_W / BINS - 0.3);
            return (
              <rect
                key={i}
                x={cx - w / 2}
                y={(height - h) / 2}
                width={w}
                height={h}
              />
            );
          })}
        </g>

        {/* Truth (audit) reference lines, always dashed so they read
            as "audit point" rather than "the candidate". Colour
            encodes whether a kept TP candidate matched this truth:
            green = matched, red = unmatched (FN). */}
        {truths.map(({ time: tt, matched }, i) => (
          <line
            key={`tr-${i}`}
            x1={xFor(tt)}
            x2={xFor(tt)}
            y1={0}
            y2={height}
            stroke={matched ? LAB_PALETTE.tp : LAB_PALETTE.fn}
            strokeOpacity={matched ? 0.55 : 0.75}
            strokeWidth={1}
            strokeDasharray="4 3"
          />
        ))}

        {/* Other candidates in window. Outcome-coloured dots so the
            visual encoding is consistent with the candidate line. */}
        {otherCandidates.map((c) => {
          const cx = xFor(c.time);
          const color = otherCandidateColor(c);
          return (
            <g key={`oc-${c.candidate_number}`}>
              <circle cx={cx} cy={height - 4} r={2.5} fill={color} fillOpacity={0.85} />
              <line
                x1={cx}
                x2={cx}
                y1={height - 12}
                y2={height}
                stroke={color}
                strokeOpacity={0.45}
                strokeWidth={1}
              />
            </g>
          );
        })}

        {/* Candidate center -- coloured by outcome */}
        <line
          x1={xFor(candidateTime)}
          x2={xFor(candidateTime)}
          y1={0}
          y2={height}
          stroke={candidateColor}
          strokeWidth={2}
        />

        {/* Playhead -- only shown while playing. Positioned in the
            visible window if the loop falls inside it (almost always
            true since the play window is enclosed by the visible
            window). */}
        {playhead != null && playhead >= windowStart && playhead <= windowEnd && (
          <line
            x1={xFor(playhead)}
            x2={xFor(playhead)}
            y1={0}
            y2={height}
            stroke={LAB_PALETTE.playhead}
            strokeWidth={1.5}
            strokeOpacity={0.85}
          />
        )}

        {/* Time-axis labels at the visible-window edges */}
        <text
          x={4}
          y={11}
          fontSize={9}
          fill="currentColor"
          opacity={0.55}
          fontFamily="ui-monospace, monospace"
        >
          {windowStart.toFixed(2)}s
        </text>
        <text
          x={VIEW_W - 4}
          y={11}
          fontSize={9}
          fill="currentColor"
          opacity={0.55}
          fontFamily="ui-monospace, monospace"
          textAnchor="end"
        >
          {windowEnd.toFixed(2)}s
        </text>
      </svg>
    </div>
  );
}
