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
  // Truth decides the vocabulary, kept does not -- same rule as the
  // keyboard shortcuts in DevFixtureDetail. A truth-positive candidate
  // is a real shot whether or not the ensemble kept it, so an FN
  // (rejected, truth=1) takes a subclass (paper/steel/...) exactly like
  // a TP; the reason list is only for candidates that are not shots.
  // Gating subclass on kept showed FN rows the FP reason list, which
  // is unanswerable for a real shot.
  const isKept = candidate.kept;
  const isPositive = candidate.truth === 1;
  if (isPositive) {
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
          isKept && "border-orange-400/60",
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
