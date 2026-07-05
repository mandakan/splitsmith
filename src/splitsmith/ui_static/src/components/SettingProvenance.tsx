/** Settings provenance badge (#216).
 *
 * Renders a tiny pill next to a setting label that names *which layer*
 * supplied the current effective value. Hover reveals every layer's
 * value so the user can answer "why is this off?" without diving
 * into config files.
 *
 * Layers, ordered top wins: ``cli`` > ``project`` > ``global`` >
 * ``default``. The CLI layer is rare in the UI (the daemon doesn't
 * take per-call CLI flags), but the badge handles it for completeness.
 */

import type {
  AutomationFieldProvenance,
  AutomationProvenanceSource,
} from "@/lib/api";

const SOURCE_LABEL: Record<AutomationProvenanceSource, string> = {
  cli: "CLI",
  project: "Project",
  global: "Global",
  default: "Default",
};

const SOURCE_CLASSES: Record<AutomationProvenanceSource, string> = {
  // Project override -- the user actively chose this for the project.
  // Stand out a bit to make the override visible at a glance.
  project: "border-led/40 bg-led/10 text-led",
  // CLI override -- transient and dev-only; same accent as project so
  // the pattern reads consistently.
  cli: "border-led/40 bg-led/10 text-led",
  // Global / default come from layers the user usually doesn't see.
  // Muted styling keeps them present-but-quiet.
  global: "border-rule bg-muted text-muted",
  default: "border-rule bg-muted text-muted",
};

function formatValue(value: boolean | number | null | undefined): string {
  if (value === true) return "on";
  if (value === false) return "off";
  if (typeof value === "number") return value.toString();
  return "(unset)";
}

export function SettingProvenance({
  provenance,
}: {
  provenance: AutomationFieldProvenance;
}) {
  const label = SOURCE_LABEL[provenance.source];
  const tooltipLines = [
    `Effective source: ${label}`,
    `CLI: ${formatValue(provenance.cli_value)}`,
    `Project: ${formatValue(provenance.project_value)}`,
    `Global: ${formatValue(provenance.global_value)}`,
  ].join("\n");
  return (
    <span
      title={tooltipLines}
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium ${SOURCE_CLASSES[provenance.source]}`}
      aria-label={`Setting source: ${label}`}
    >
      {label}
    </span>
  );
}
