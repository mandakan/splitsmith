/**
 * Live anomaly panel for the audit screen (issue #42).
 *
 * Mirrors what ``report.txt`` will say once the user clicks Generate, but
 * shown live while they keep / reject markers so the audit step is the
 * one place where audit work is actually decided. Anomaly rows that
 * reference a shot number are clickable -- one click puts the playhead
 * on the offending marker with the previous one in view, which is what
 * makes the panel actually useful for hand-auditing.
 *
 * Severity follows the report's ``[!]`` (warn) / ``[~]`` (info) split:
 * ``warn`` rules (double-detection, long pause, stage-time mismatch, no
 * shots) get the warning palette; ``info`` rules (shot-count band) stay
 * neutral so they don't drown out real issues.
 *
 * Anomalies are derived state -- they live in memory and recompute on
 * every marker mutation. They are NOT persisted into the audit JSON;
 * a future "snooze" feature would change that, but per #42's AC the
 * panel is purely a view over the in-memory truth.
 */

import { AlertTriangle, CheckCircle2, Info } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Anomaly } from "@/lib/anomalies";
import { cn } from "@/lib/utils";

export interface AnomalyPanelProps {
  anomalies: Anomaly[];
  /** Called when the user clicks an anomaly that references a shot.
   *  The page-level handler resolves ``shotNumber`` to the matching kept
   *  marker and scrolls + focuses it. Stage-level anomalies (count band,
   *  no shots) ignore the click. */
  onJumpToShot: (shotNumber: number) => void;
}

export function AnomalyPanel({ anomalies, onJumpToShot }: AnomalyPanelProps) {
  if (anomalies.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <CheckCircle2
              aria-hidden
              className="size-4 text-status-complete"
            />
            Anomalies
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          No anomalies. Stage looks clean.
        </CardContent>
      </Card>
    );
  }

  const warnCount = anomalies.filter((a) => a.severity === "warn").length;
  const infoCount = anomalies.length - warnCount;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <AlertTriangle
            aria-hidden
            className={cn(
              "size-4",
              warnCount > 0 ? "text-status-warning" : "text-muted-foreground",
            )}
          />
          Anomalies
          <span className="ml-1 text-xs font-normal text-muted-foreground">
            {warnCount > 0 ? `${warnCount} to review` : null}
            {warnCount > 0 && infoCount > 0 ? " - " : null}
            {infoCount > 0 ? `${infoCount} info` : null}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-1.5 pt-0 text-sm">
        {anomalies.map((a, i) => (
          <AnomalyRow
            key={`${a.kind}-${a.shot_number ?? "stage"}-${i}`}
            anomaly={a}
            onJumpToShot={onJumpToShot}
          />
        ))}
      </CardContent>
    </Card>
  );
}

function AnomalyRow({
  anomaly,
  onJumpToShot,
}: {
  anomaly: Anomaly;
  onJumpToShot: (shotNumber: number) => void;
}) {
  const clickable = anomaly.shot_number != null;
  const Icon = anomaly.severity === "warn" ? AlertTriangle : Info;

  // Tone classes are colour-blind-safe (Okabe-Ito derived; same palette
  // as marker colours) -- ``status-warning`` is the orange used elsewhere
  // for "needs attention", ``status-in-progress`` blue for purely
  // informational rows.
  const toneClasses =
    anomaly.severity === "warn"
      ? "border-status-warning/40 bg-status-warning/5"
      : "border-border bg-muted/30";
  const iconClasses =
    anomaly.severity === "warn"
      ? "text-status-warning"
      : "text-status-in-progress";

  const content = (
    <div
      className={cn(
        "flex items-start gap-2 rounded-md border px-2.5 py-1.5",
        toneClasses,
        clickable &&
          "cursor-pointer transition-colors hover:bg-accent hover:text-accent-foreground",
      )}
    >
      <Icon
        aria-hidden
        className={cn("mt-0.5 size-4 shrink-0", iconClasses)}
      />
      <span className="leading-snug">{anomaly.message}</span>
    </div>
  );

  if (!clickable) {
    return content;
  }

  return (
    <button
      type="button"
      onClick={() => {
        if (anomaly.shot_number != null) onJumpToShot(anomaly.shot_number);
      }}
      className="block w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-md"
      aria-label={`Jump to shot ${anomaly.shot_number}: ${anomaly.message}`}
      title={`Jump to shot ${anomaly.shot_number}`}
    >
      {content}
    </button>
  );
}
