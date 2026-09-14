/**
 * FootageCards -- the coverage matrix at phone width (UX PR 6): one card
 * per stage with the beep state on the first line and the file chips on
 * the second; a stage with no footage is one dim line.
 */
import type { StageVideo } from "@/lib/api";
import type { FootageRow } from "@/lib/footage";

import { BeepCell, type FootageHrefs } from "./CoverageMatrix";
import { FileChip } from "./FileChip";

export interface FootageCardsProps {
  rows: FootageRow[];
  hrefs: FootageHrefs;
  onOpen: (slug: string, stage: number, video: StageVideo) => void;
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function FootageCards({ rows, hrefs, onOpen }: FootageCardsProps) {
  return (
    <div className="flex flex-col gap-2">
      {rows.map((row) => {
        const cells = row.cells.filter((c) => c.videos.length > 0);
        const n = row.stageNumber;
        return (
          <section
            key={n}
            aria-label={`Stage ${n} ${row.stageName}`}
            className={`rounded-[10px] border border-rule bg-surface px-3.5 py-3 text-md ${cells.length ? "" : "text-subtle"}`}
          >
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm text-muted">{pad2(n)}</span>
              <span className={`min-w-0 flex-1 truncate ${cells.length ? "font-medium text-ink" : ""}`}>{row.stageName}</span>
              {cells.length === 0 ? (
                <span className="font-mono text-sm">no footage</span>
              ) : row.cells.length === 1 ? (
                <BeepCell cell={row.cells[0]} stage={n} hrefs={hrefs} className="text-sm" />
              ) : null}
            </div>
            {cells.map((cell) => (
              <div key={cell.slug} className="mt-2 flex flex-wrap items-center gap-1.5">
                {row.cells.length > 1 ? <span className="mr-1 text-sm text-muted">{cell.shooterName}</span> : null}
                {cell.videos.map((v) => (
                  <FileChip key={v.path} video={v} onOpen={(video) => onOpen(cell.slug, n, video)} />
                ))}
                {row.cells.length > 1 ? <BeepCell cell={cell} stage={n} hrefs={hrefs} className="ml-auto text-sm" /> : null}
              </div>
            ))}
          </section>
        );
      })}
    </div>
  );
}
