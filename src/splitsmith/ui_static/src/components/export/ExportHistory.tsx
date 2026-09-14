import { Download } from "lucide-react";

import { Label } from "@/components/ui/Label";
import type { ExportRun } from "@/lib/api";
import { stageLabel } from "@/lib/exportPlan";

/** What each export run produced, newest first (#629).
 *
 *  Purely presentational: every input is a prop, nothing is fetched here.
 *  The list arrives already ordered by the server -- do not re-sort, or a
 *  clock skew between two workers becomes a reordering bug.
 *
 *  Rendered in both deployment modes. Only the *reveal* affordance was
 *  ever desktop-specific; the download endpoint reads local disk on
 *  desktop and object storage on hosted, so one link works for both.
 *
 *  An artefact whose ``available`` is false renders as a struck-through
 *  name and nothing clickable. This is not a rare edge: the same page
 *  offers a cleanup dialog that deletes export files, and the history is
 *  durable by design, so a run's record outlives its files. A link there
 *  would carry ``download``, which saves the 404 body to disk under the
 *  video's own filename. */
export function ExportHistory({
  runs,
  exportFileUrl,
}: {
  runs: ExportRun[];
  exportFileUrl: (filename: string) => string;
}) {
  return (
    <section className="rounded-[10px] border border-rule bg-surface">
      <h2 className="border-b border-rule px-3.5 py-2">
        <Label>Export history</Label>
      </h2>
      {runs.length === 0 ? (
        <p className="px-3.5 py-3 text-md text-muted">No exports yet</p>
      ) : (
        <ul>
          {runs.map((r) => (
            <li key={r.run_id} className="border-b border-rule px-3.5 py-2.5 last:border-b-0">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-md">
                <span className="font-medium text-ink">{stageLabel(r.stage_numbers)}</span>
                <span className="text-sm text-muted">{r.formats.join(", ")}</span>
                <span className="numeral text-sm text-muted">{r.duration_seconds.toFixed(1)}s</span>
                {r.anomaly_count > 0 && (
                  <span className="numeral text-sm text-live">
                    {r.anomaly_count} {r.anomaly_count === 1 ? "anomaly" : "anomalies"}
                  </span>
                )}
                <time dateTime={r.finished_at} className="numeral ml-auto text-sm text-muted">
                  {new Date(r.finished_at).toLocaleString()}
                </time>
              </div>
              <div className="mt-1 flex flex-col gap-0.5">
                {r.artifacts.map((a) =>
                  a.available ? (
                    <a
                      key={a.filename}
                      href={exportFileUrl(a.filename)}
                      download={a.filename}
                      className="inline-flex items-center gap-1.5 font-mono text-sm text-ink-2 hover:text-ink"
                    >
                      <Download className="size-3" /> {a.filename}
                    </a>
                  ) : (
                    <span
                      key={a.filename}
                      title="Deleted -- this run's record is kept, the file is not"
                      className="inline-flex items-center gap-1.5 font-mono text-sm text-subtle line-through"
                    >
                      <Download className="size-3" /> {a.filename}
                    </span>
                  ),
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
