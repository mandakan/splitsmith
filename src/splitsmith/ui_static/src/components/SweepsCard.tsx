/**
 * SweepsCard: read-only browser for the ensemble-sweep dashboard.
 *
 * Backend: ``/api/lab/sweeps`` (lists run_ids), ``/api/lab/sweeps/<id>``
 * (per-combo + per-fixture rows), ``/api/lab/sweeps/<id>/plot/<name>.png``
 * (rendered matplotlib output served from ``build/sweeps/<id>/``).
 *
 * No launch UI yet -- sweeps are produced by ``scripts/run_sweep.py``;
 * the card just surfaces what's on disk so the Lab page can show the
 * actionable view without context-switching to a terminal.
 */

import { useEffect, useMemo, useState } from "react";
import { AlertCircle, BarChart3, Loader2, LineChart, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  api,
  type SweepRunDetail,
  type SweepRunSummary,
} from "@/lib/api";

function pct(v: number): string {
  return (v * 100).toFixed(1) + "%";
}

function ShortFixture({ name }: { name: string }) {
  // Match the Python plotter's _short_fixture helper so labels look the
  // same in the UI as in the rendered PNGs.
  const compact = name.replace(/^stage-shots-/, "");
  return <span className="font-mono text-[10px]">{compact}</span>;
}

function MetricCell({ value }: { value: number }) {
  return (
    <span className="tabular-nums">{pct(value)}</span>
  );
}

export function SweepsCard() {
  const [runs, setRuns] = useState<SweepRunSummary[] | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SweepRunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = () => {
    setLoading(true);
    setError(null);
    api
      .listSweepRuns()
      .then((rs) => {
        setRuns(rs);
        if (rs.length && !selectedRunId) {
          setSelectedRunId(rs[0].run_id);
        }
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    reload();
    // run-on-mount only; manual refresh covers re-fetches
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedRunId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    api
      .getSweepRun(selectedRunId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  const bestCombo = useMemo(() => {
    if (!detail) return null;
    return detail.combos.find(
      (c) => c.combo_idx === detail.summary.best_combo_idx,
    );
  }, [detail]);

  const swept = detail?.summary.swept_keys ?? [];

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <LineChart className="size-4 text-led" />
            Parameter sweeps
          </CardTitle>
          <CardDescription>
            Read-only dashboard of past <code>scripts/run_sweep.py</code> runs.
            Backed by <code>build/sweeps/runs.parquet</code>.
          </CardDescription>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={reload}
          disabled={loading}
          aria-label="Refresh sweep list"
        >
          {loading ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <RefreshCw className="size-4" />
          )}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
            <AlertCircle className="size-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {runs && runs.length === 0 && (
          <div className="flex items-start gap-2 rounded-md border border-muted bg-muted/30 p-3 text-xs text-muted">
            <BarChart3 className="size-4 shrink-0" />
            <span>
              No sweeps yet. Produce one with{" "}
              <code>
                uv run python scripts/run_sweep.py --grid scripts/sweep_grids/voter_c_slack_fine.yaml
              </code>{" "}
              then click refresh.
            </span>
          </div>
        )}

        {runs && runs.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted">
              Runs ({runs.length})
            </p>
            <div className="grid gap-2">
              {runs.map((r) => {
                const active = r.run_id === selectedRunId;
                return (
                  <button
                    key={r.run_id}
                    type="button"
                    onClick={() => setSelectedRunId(r.run_id)}
                    className={
                      "w-full rounded-md border px-3 py-2 text-left transition " +
                      (active
                        ? "border-led bg-led/5"
                        : "border-muted hover:border-muted/40")
                    }
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="truncate font-mono text-[11px]">
                        {r.run_id}
                      </span>
                      <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
                        F1 {r.best_f1.toFixed(3)}
                      </Badge>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted">
                      <span>
                        {r.n_combos} combo{r.n_combos === 1 ? "" : "s"}
                      </span>
                      <span>{r.n_fixtures} fixtures</span>
                      <span>P {pct(r.best_precision)}</span>
                      <span>R {pct(r.best_recall)}</span>
                      {r.swept_keys.length > 0 ? (
                        <span className="font-mono">
                          swept: {r.swept_keys.join(" x ")}
                        </span>
                      ) : (
                        <span className="italic">defaults snapshot</span>
                      )}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {detail && bestCombo && (
          <div className="space-y-4 border-t pt-4">
            <div className="flex flex-wrap items-baseline gap-2">
              <p className="text-sm font-semibold">Best aggregate</p>
              <Badge variant="outline" className="font-mono text-[10px]">
                combo_idx {bestCombo.combo_idx}
              </Badge>
              <span className="ml-auto text-xs text-muted tabular-nums">
                F1 <span className="font-semibold text-ink">{detail.summary.best_f1.toFixed(4)}</span>
                {"  "}P {pct(detail.summary.best_precision)} R {pct(detail.summary.best_recall)}{"  "}
                kept {detail.summary.best_kept} (TP {detail.summary.best_true_pos}, FP{" "}
                {detail.summary.best_false_pos}, FN {detail.summary.best_false_neg})
              </span>
            </div>

            {swept.length > 0 && (
              <details>
                <summary className="cursor-pointer text-xs text-muted">
                  Best-combo parameters ({Object.keys(bestCombo.params).length})
                </summary>
                <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] sm:grid-cols-3">
                  {Object.entries(bestCombo.params)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([k, v]) => {
                      const isSwept = swept.includes(k);
                      return (
                        <div key={k} className="flex items-center justify-between gap-2">
                          <code
                            className={
                              isSwept
                                ? "text-ink font-semibold"
                                : "text-muted"
                            }
                          >
                            {k}
                          </code>
                          <span className="font-mono tabular-nums">{String(v)}</span>
                        </div>
                      );
                    })}
                </div>
              </details>
            )}

            {detail.available_plots.length > 0 && (
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-muted">
                  Plots
                </p>
                <div className="grid gap-3 md:grid-cols-2">
                  {/* Show the composite overview large; everything else as smaller thumbs */}
                  {detail.available_plots.includes("overview") && (
                    <a
                      key="overview"
                      href={api.sweepPlotUrl(detail.summary.run_id, "overview")}
                      target="_blank"
                      rel="noreferrer"
                      className="col-span-full block rounded-md border bg-muted/30 p-2"
                    >
                      <img
                        src={api.sweepPlotUrl(detail.summary.run_id, "overview")}
                        alt="sweep overview"
                        className="mx-auto w-full"
                        loading="lazy"
                      />
                    </a>
                  )}
                  {detail.available_plots
                    .filter((p) => p !== "overview")
                    .map((p) => (
                      <a
                        key={p}
                        href={api.sweepPlotUrl(detail.summary.run_id, p)}
                        target="_blank"
                        rel="noreferrer"
                        className="block rounded-md border bg-muted/30 p-2"
                      >
                        <p className="mb-1 truncate font-mono text-[10px] text-muted">
                          {p}
                        </p>
                        <img
                          src={api.sweepPlotUrl(detail.summary.run_id, p)}
                          alt={p}
                          className="mx-auto w-full"
                          loading="lazy"
                        />
                      </a>
                    ))}
                </div>
              </div>
            )}

            {bestCombo.per_fixture.length > 0 && (
              <details>
                <summary className="cursor-pointer text-xs text-muted">
                  Per-fixture (best combo, {bestCombo.per_fixture.length} rows)
                </summary>
                <div className="mt-2 max-h-[420px] overflow-auto rounded-md border">
                  <table className="w-full text-[11px]">
                    <thead className="sticky top-0 bg-muted/60">
                      <tr>
                        <th className="px-2 py-1 text-left">fixture</th>
                        <th className="px-2 py-1 text-left">camera</th>
                        <th className="px-2 py-1 text-right">kept</th>
                        <th className="px-2 py-1 text-right">TP</th>
                        <th className="px-2 py-1 text-right">FP</th>
                        <th className="px-2 py-1 text-right">FN</th>
                        <th className="px-2 py-1 text-right">P</th>
                        <th className="px-2 py-1 text-right">R</th>
                        <th className="px-2 py-1 text-right">F1</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...bestCombo.per_fixture]
                        .sort((a, b) => b.f1 - a.f1)
                        .map((r) => (
                          <tr key={r.fixture} className="border-t">
                            <td className="px-2 py-1">
                              <ShortFixture name={r.fixture} />
                            </td>
                            <td className="px-2 py-1 text-muted">
                              {r.camera_class}
                            </td>
                            <td className="px-2 py-1 text-right tabular-nums">
                              {r.n_kept}
                            </td>
                            <td className="px-2 py-1 text-right tabular-nums">
                              {r.true_pos}
                            </td>
                            <td className="px-2 py-1 text-right tabular-nums text-destructive">
                              {r.false_pos}
                            </td>
                            <td className="px-2 py-1 text-right tabular-nums text-orange-600">
                              {r.false_neg}
                            </td>
                            <td className="px-2 py-1 text-right tabular-nums">
                              <MetricCell value={r.precision} />
                            </td>
                            <td className="px-2 py-1 text-right tabular-nums">
                              <MetricCell value={r.recall} />
                            </td>
                            <td className="px-2 py-1 text-right tabular-nums font-semibold">
                              <MetricCell value={r.f1} />
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </details>
            )}

            {bestCombo.per_class.length > 0 && (
              <details>
                <summary className="cursor-pointer text-xs text-muted">
                  Per camera class (best combo)
                </summary>
                <div className="mt-2 grid gap-1 text-[11px]">
                  {bestCombo.per_class.map((r) => (
                    <div
                      key={r.camera_class}
                      className="flex items-center justify-between rounded border px-2 py-1"
                    >
                      <span className="font-mono">{r.camera_class}</span>
                      <span className="text-muted tabular-nums">
                        kept {r.n_kept}, P {pct(r.precision)}, R {pct(r.recall)}, F1 {r.f1.toFixed(3)}
                      </span>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
