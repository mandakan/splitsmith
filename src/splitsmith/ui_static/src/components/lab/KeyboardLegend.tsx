import { cn } from "@/lib/utils";

export function KeyboardLegend({ selectedCn }: { selectedCn: number | null }) {
  return (
    <div
      className={cn(
        "rounded border border-rule/60 px-3 py-2 text-[11px] text-muted",
        selectedCn != null && "border-led/60 bg-led/5 text-ink",
      )}
    >
      <div className="mb-1 font-semibold uppercase tracking-wide">
        Keyboard {selectedCn != null ? `(row #${selectedCn} selected)` : "(click or J/K to select a row)"}
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-0.5 font-mono text-[10px] sm:grid-cols-4">
        <span><kbd>J</kbd> / <kbd>↓</kbd> next</span>
        <span><kbd>K</kbd> / <kbd>↑</kbd> prev</span>
        <span><kbd>Esc</kbd> deselect</span>
        <span><kbd>Space</kbd> play / pause</span>
        <span><kbd>0</kbd> / <kbd>Bksp</kbd> clear</span>
        <span><kbd>X</kbd> cross_bay</span>
        <span><kbd>E</kbd> echo</span>
        <span><kbd>B</kbd> barrel_echo / barrel</span>
        <span><kbd>W</kbd> wind</span>
        <span><kbd>M</kbd> movement</span>
        <span><kbd>S</kbd> steel_ring / steel</span>
        <span><kbd>H</kbd> handling</span>
        <span><kbd>A</kbd> agc_artifact</span>
        <span><kbd>V</kbd> speech (Voice)</span>
        <span><kbd>O</kbd> other</span>
        <span><kbd>U</kbd> unknown</span>
        <span><kbd>P</kbd> paper (TP only)</span>
      </div>
    </div>
  );
}
