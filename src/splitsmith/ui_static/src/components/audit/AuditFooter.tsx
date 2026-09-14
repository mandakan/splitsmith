/**
 * AuditFooter -- the shortcut line, always visible at the bottom of the
 * Audit page (UX PR 5, spec s4.4). Replaces the collapsible hint bar
 * and the stage-progress footer.
 */
import { Kbd } from "@/components/ui/Kbd";

export interface AuditFooterProps {
  onOpenHelp: () => void;
}

const MOD = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "⌃";

function Item({ keys, label }: { keys: string[]; label: string }) {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap">
      {keys.map((k) => (
        <Kbd key={k} size="sm">
          {k}
        </Kbd>
      ))}
      <span className="ml-0.5">{label}</span>
    </span>
  );
}

export function AuditFooter({ onOpenHelp }: AuditFooterProps) {
  return (
    <div className="sticky bottom-0 z-10 -mx-4 mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-rule bg-surface px-4 py-2 text-sm text-muted md:-mx-7 md:px-7">
      <Item keys={["Space"]} label="play" />
      <Item keys={["M", "⇧M"]} label="next / prev shot" />
      <Item keys={["F"]} label="next flag" />
      <Item keys={["K"]} label="toggle shot at playhead" />
      <Item keys={["R"]} label="reject" />
      <Item keys={["+", "0", "−"]} label="zoom" />
      <Item keys={[`${MOD}⏎`]} label="save & next" />
      <button type="button" onClick={onOpenHelp} className="ml-auto inline-flex items-center gap-1 rounded text-muted hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led">
        <Kbd size="sm">?</Kbd>
        <span className="ml-0.5">all shortcuts</span>
      </button>
    </div>
  );
}
