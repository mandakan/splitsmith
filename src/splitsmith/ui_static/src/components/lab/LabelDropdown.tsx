import { Loader2 } from "lucide-react";

import { LAB_REASONS, LAB_SUBCLASSES, type LabEvalFixture } from "@/lib/api";
import { cn } from "@/lib/utils";

export function LabelDropdown({
  candidate,
  onChange,
  saving,
}: {
  candidate: LabEvalFixture["candidates"][number];
  onChange: (patch: { reason?: string | null; subclass?: string | null }) => void;
  saving: boolean;
}) {
  // Kept positive (TP): edit subclass. Kept FP: edit reason. Rejected
  // candidates aren't worth labelling -- they don't survive consensus
  // so they don't pollute precision -- but we still let the user tag a
  // reason for rejected ones if they want a record (e.g. for #87
  // mining cross-references).
  const isKept = candidate.kept;
  const isPositive = candidate.truth === 1;
  if (isKept && isPositive) {
    return (
      <div className="flex items-center gap-1">
        <select
          value={candidate.subclass ?? ""}
          onChange={(e) => onChange({ subclass: e.target.value || null })}
          className="rounded border border-rule/60 bg-bg px-1 py-0.5 text-[11px]"
          disabled={saving}
        >
          <option value="">--</option>
          {LAB_SUBCLASSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {saving && <Loader2 className="size-3 animate-spin text-muted" />}
      </div>
    );
  }
  return (
    <div className="flex items-center gap-1">
      <select
        value={candidate.reason ?? ""}
        onChange={(e) => onChange({ reason: e.target.value || null })}
        className={cn(
          "rounded border border-rule/60 bg-bg px-1 py-0.5 text-[11px]",
          isKept && !isPositive && "border-orange-400/60",
        )}
        disabled={saving}
      >
        <option value="">--</option>
        {LAB_REASONS.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>
      {saving && <Loader2 className="size-3 animate-spin text-muted" />}
    </div>
  );
}
