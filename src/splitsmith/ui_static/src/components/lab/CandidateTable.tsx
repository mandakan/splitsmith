import { useEffect } from "react";

import { type LabEvalFixture } from "@/lib/api";
import { cn } from "@/lib/utils";

import { LabelDropdown } from "./LabelDropdown";

export function CandidateTable({
  candidates,
  onLabel,
  savingLabel,
  selectedCn,
  onSelect,
}: {
  candidates: LabEvalFixture["candidates"];
  onLabel: (
    candidate_number: number,
    patch: { reason?: string | null; subclass?: string | null },
  ) => void;
  savingLabel: number | null;
  selectedCn: number | null;
  onSelect: (cn: number | null) => void;
}) {
  // Auto-scroll the selected row into view when it changes via keyboard nav.
  useEffect(() => {
    if (selectedCn == null) return;
    const el = document.querySelector(`[data-cn="${selectedCn}"]`);
    if (el && "scrollIntoView" in el) {
      (el as HTMLElement).scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [selectedCn]);

  // The single scroll surface of the card: the parent section is a
  // bounded flex column (DevFixtureDetail), so this region takes all
  // remaining height instead of the old arbitrary max-h-96 box that
  // showed ~9 of 65 rows and nested a third scrollbar into the page.
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <table className="w-full text-xs">
        {/* z-10: row selects and selection rings paint over a
            z-less sticky header. Hairline via shadow -- borders on
            sticky table rows shear off while scrolling. */}
        <thead className="sticky top-0 z-10 bg-surface text-[10px] uppercase tracking-wide text-muted shadow-[0_1px_0_0_var(--color-rule)]">
          <tr>
              <th className="px-2 py-1 text-left font-medium">#</th>
              <th className="px-2 py-1 text-right font-medium">t (s)</th>
              <th className="px-2 py-1 text-right font-medium">conf</th>
              <th className="px-2 py-1 text-right font-medium">A</th>
              <th className="px-2 py-1 text-right font-medium">B</th>
              <th className="px-2 py-1 text-right font-medium">C</th>
              <th className="px-2 py-1 text-right font-medium">score</th>
              <th className="px-2 py-1 text-center font-medium">kept</th>
              <th className="px-2 py-1 text-center font-medium">truth</th>
              <th className="px-2 py-1 text-left font-medium">label</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => {
              const isTP = c.kept && c.truth === 1;
              const isFP = c.kept && c.truth === 0;
              const isFN = !c.kept && c.truth === 1;
              const saving = savingLabel === c.candidate_number;
              const selected = selectedCn === c.candidate_number;
              return (
                <tr
                  key={c.candidate_number}
                  data-cn={c.candidate_number}
                  className={cn(
                    // scroll-mt clears the sticky header when keyboard
                    // nav scrolls a row to the top edge. The selection
                    // ring is an inset shadow, not an outline: outlines
                    // paint outside the row box, over the sticky header
                    // and the neighbouring rows.
                    "cursor-pointer scroll-mt-7 border-b border-rule/20 font-mono",
                    isTP && "bg-emerald-500/5",
                    isFP && "bg-orange-500/10",
                    isFN && "bg-red-500/10",
                    selected && "bg-led/10 shadow-[inset_0_0_0_1.5px_rgba(255,45,45,0.65)]",
                  )}
                  onClick={() => onSelect(selected ? null : c.candidate_number)}
                >
                  <td className="px-2 py-1">{c.candidate_number}</td>
                  <td className="px-2 py-1 text-right">{c.time.toFixed(3)}</td>
                  <td className="px-2 py-1 text-right">{c.confidence.toFixed(3)}</td>
                  <td className="px-2 py-1 text-right">{c.vote_a}</td>
                  <td className="px-2 py-1 text-right">{c.vote_b}</td>
                  <td className="px-2 py-1 text-right">{c.vote_c}</td>
                  <td className="px-2 py-1 text-right">{c.ensemble_score.toFixed(2)}</td>
                  <td className="px-2 py-1 text-center">{c.kept ? "Y" : ""}</td>
                  <td className="px-2 py-1 text-center">{c.truth ? "Y" : ""}</td>
                  <td className="px-2 py-1">
                    <LabelDropdown
                      candidate={c}
                      onChange={(patch) => onLabel(c.candidate_number, patch)}
                      saving={saving}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
    </div>
  );
}
