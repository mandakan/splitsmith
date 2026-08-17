import { cn } from "@/lib/utils";

/**
 * One-line nav-key strip. The per-label shortcut keys used to be
 * listed here as a ~200px card, which forced the labeling aside into
 * its own scrollbar; they now live as <kbd> hints on the label buttons
 * themselves (StepThroughPanel), so this strip only carries the keys
 * that have no button.
 */
export function KeyboardLegend({ selectedCn }: { selectedCn: number | null }) {
  return (
    <div
      className={cn(
        "flex items-center gap-4 overflow-hidden whitespace-nowrap rounded border border-rule/60 px-3 py-1.5 font-mono text-[10px] text-muted",
        selectedCn != null && "border-led/60 bg-led/5 text-ink",
      )}
    >
      <span className="font-sans text-[10px] font-semibold uppercase tracking-wide">
        {selectedCn != null ? `row #${selectedCn}` : "keys"}
      </span>
      <span>
        <kbd>J</kbd>/<kbd>K</kbd> walk
      </span>
      <span>
        <kbd>Space</kbd> play
      </span>
      <span>
        <kbd>Esc</kbd> deselect
      </span>
      <span>
        <kbd>0</kbd> clear
      </span>
    </div>
  );
}
