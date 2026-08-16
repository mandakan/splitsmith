import { Download } from "lucide-react";

import type { ExportRun } from "@/lib/api";

/** "Stage 3", "Stages 1-3" for a contiguous run, "Stages 1, 2, 4"
 *  otherwise. A range label over a gapped selection would be a lie, and
 *  gaps are normal since #521 let a stage be removed without renumbering. */
export function stageLabel(stages: number[]): string {
  if (stages.length === 0) return "No stages";
  if (stages.length === 1) return `Stage ${stages[0]}`;
  const sorted = [...stages].sort((a, b) => a - b);
  const contiguous = sorted.every((n, i) => i === 0 || n === sorted[i - 1] + 1);
  return contiguous
    ? `Stages ${sorted[0]}-${sorted[sorted.length - 1]}`
    : `Stages ${sorted.join(", ")}`;
}

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
    <section className="mt-6 rounded-lg border border-rule">
      <h2 className="border-b border-rule px-5 py-3 font-display text-[0.6875rem] font-bold uppercase tracking-[0.1em] text-ink-2">
        Export history
      </h2>
      {runs.length === 0 ? (
        <p className="px-5 py-4 text-[0.8125rem] text-muted">No exports yet</p>
      ) : (
        <ul className="divide-y divide-rule">
          {runs.map((r) => (
            <li key={r.run_id} className="px-5 py-3">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="font-display text-[0.8125rem] font-semibold text-ink">
                  {stageLabel(r.stage_numbers)}
                </span>
                <span className="font-mono text-[0.6875rem] uppercase tracking-[0.04em] text-muted">
                  {r.formats.join(", ")}
                </span>
                <span className="font-mono text-[0.6875rem] text-muted tabular-nums">
                  {r.duration_seconds.toFixed(1)}s
                </span>
                {r.anomaly_count > 0 && (
                  <span className="font-mono text-[0.6875rem] text-live tabular-nums">
                    {r.anomaly_count} {r.anomaly_count === 1 ? "anomaly" : "anomalies"}
                  </span>
                )}
                <time
                  dateTime={r.finished_at}
                  className="ml-auto font-mono text-[0.6875rem] text-muted tabular-nums"
                >
                  {new Date(r.finished_at).toLocaleString()}
                </time>
              </div>
              <div className="mt-1.5 flex flex-col gap-0.5">
                {r.artifacts.map((a) =>
                  a.available ? (
                    <a
                      key={a.filename}
                      href={exportFileUrl(a.filename)}
                      download={a.filename}
                      className="inline-flex items-center gap-1.5 font-mono text-[0.6875rem] text-led hover:text-led-soft"
                    >
                      <Download className="size-3" /> {a.filename}
                    </a>
                  ) : (
                    <span
                      key={a.filename}
                      title="Deleted -- this run's record is kept, the file is not"
                      className="inline-flex items-center gap-1.5 font-mono text-[0.6875rem] text-muted line-through"
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
