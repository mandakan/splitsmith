/**
 * Developer / Retrain (#331).
 *
 * Live build view -- pipeline visualization, log tail, before/after
 * compare strip, per-voter detail, history log, decision footer
 * (save candidate / roll back / promote). Wires to
 * /api/lab/rebuild-calibration which kicks off the same
 * scripts/build_ensemble_artifacts.py the CLI uses.
 *
 * Honest read on scope: this surface intentionally renders the
 * polished structure for surfaces that don't yet have backend support
 * (history log, promote/rollback actions). Those controls are present
 * but disabled with explanatory tooltips so the layout is real even
 * when the data isn't. The pipeline visualization + log tail ARE wired
 * to a real job once you click Run.
 */

import {
  AlertTriangle,
  ChevronRight,
  Clock,
  Pause,
  Play,
  Save,
  Undo2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";

import { api, type Job } from "@/lib/api";
import { cn } from "@/lib/utils";

import type { DeveloperShellOutletContext } from "@/components/developer/DeveloperShell";

interface PipelineStage {
  num: string;
  name: string;
  desc: string;
}

const STAGES: PipelineStage[] = [
  { num: "01", name: "Load fixtures", desc: "Walk audit dir, pair JSON+WAV" },
  { num: "02", name: "Voter A floor", desc: "Auto-calibrate envelope onset threshold" },
  { num: "03", name: "CLAP scoring", desc: "Run prompt-similarity differential" },
  { num: "04", name: "Feature build", desc: "Stack hand-crafted + CLAP + PANN" },
  { num: "05", name: "GBDT 5-fold CV", desc: "Train + pick voter C threshold" },
  { num: "06", name: "Write artifact", desc: "Save calibration JSON + joblib" },
];

export function DevRetrain() {
  const { model, refresh } = useOutletContext<DeveloperShellOutletContext>();
  const [job, setJob] = useState<Job | null>(null);
  const [logLines, setLogLines] = useState<
    { ts: string; level: "INFO" | "OK" | "WARN"; msg: string }[]
  >([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stageIdx, setStageIdx] = useState(-1);

  useEffect(() => {
    if (!job) return;
    if (job.status === "succeeded" || job.status === "failed") return;
    let alive = true;
    const interval = window.setInterval(async () => {
      try {
        const j = await api.getJob(job.id);
        if (!alive) return;
        setJob(j);
        // Map job progress to stage index for the pipeline animation.
        const idx = Math.min(STAGES.length - 1, Math.floor((j.progress ?? 0) * STAGES.length));
        setStageIdx(idx);
        const msg = j.message ?? j.status;
        setLogLines((prev) => {
          if (prev.length > 0 && prev[prev.length - 1].msg === msg) return prev;
          return [
            ...prev.slice(-40),
            {
              ts: new Date().toLocaleTimeString(undefined, { hour12: false }),
              level: j.status === "failed" ? "WARN" : "INFO",
              msg,
            },
          ];
        });
        if (j.status === "succeeded" || j.status === "failed") {
          window.clearInterval(interval);
          setRunning(false);
          setStageIdx(STAGES.length - 1);
          if (j.status === "succeeded") {
            setLogLines((prev) => [
              ...prev,
              {
                ts: new Date().toLocaleTimeString(undefined, { hour12: false }),
                level: "OK",
                msg: "Build complete -- new artifact written to src/splitsmith/data/",
              },
            ]);
            refresh();
          }
        }
      } catch (e) {
        window.clearInterval(interval);
        setRunning(false);
        setError(String(e));
      }
    }, 1000);
    return () => {
      alive = false;
      window.clearInterval(interval);
    };
  }, [job, refresh]);

  async function startRebuild() {
    setError(null);
    setLogLines([]);
    setStageIdx(0);
    setRunning(true);
    try {
      const j = await api.rebuildLabCalibration({});
      setJob(j);
    } catch (e) {
      setRunning(false);
      const msg = String(e);
      setError(
        msg.includes("404") || msg.includes("lab")
          ? "Retrain requires --lab to be enabled. Launch with `splitsmith ui --lab`."
          : msg,
      );
    }
  }

  return (
    <div className="min-w-0 px-7 py-7">
      <header className="mb-6 flex items-end gap-7">
        <div className="flex-1">
          <div className="mb-2 flex items-center gap-2.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.18em] text-beep">
            <span aria-hidden className="h-px w-6 bg-beep" />
            Step 04 / Retrain
          </div>
          <h1 className="font-display text-[2rem] font-bold uppercase leading-none tracking-tight text-ink">
            Build a new artifact
          </h1>
          <p className="mt-2 max-w-xl text-[0.875rem] text-muted">
            Runs scripts/build_ensemble_artifacts.py end-to-end on the corpus. The shipped
            model stays untouched until you explicitly promote.
          </p>
        </div>
        <button
          type="button"
          onClick={startRebuild}
          disabled={running}
          className="inline-flex h-10 items-center gap-2 rounded-md bg-beep px-4 font-mono text-[0.75rem] font-bold uppercase tracking-[0.08em] text-bg shadow-[0_0_12px_var(--color-beep-glow)] hover:opacity-90 disabled:opacity-50"
        >
          <Play className="size-3.5" />
          {running ? "Building..." : "Run build"}
        </button>
      </header>

      {error && (
        <div className="mb-6 rounded-md border border-[rgba(255,45,45,0.4)] bg-[color:var(--color-led-tint)] px-4 py-3 text-[0.8125rem] text-led">
          {error}
        </div>
      )}

      {/* Shipped vs candidate */}
      <CompareStrip model={model} job={job} />

      {/* Pipeline */}
      <PipelineCard job={job} stageIdx={stageIdx} running={running} />

      {/* Log tail */}
      <LogTail lines={logLines} running={running} />

      {/* Per-voter detail */}
      <VoterGrid />

      {/* History */}
      <HistoryCard model={model} />

      {/* Action zone */}
      <footer className="sticky bottom-0 mt-8 flex items-center gap-3 border-t border-rule bg-surface px-7 py-3 -mx-7 -mb-7 backdrop-blur">
        <button
          type="button"
          disabled
          className="inline-flex items-center gap-2 rounded-md border border-rule px-3 py-2 font-mono text-[0.75rem] font-bold uppercase tracking-[0.08em] text-ink-2 disabled:opacity-60"
          title="Saving candidates as artifacts isn't wired yet"
        >
          <Save className="size-3.5" />
          Save as candidate
        </button>
        <button
          type="button"
          disabled
          className="inline-flex items-center gap-2 rounded-md border border-[rgba(255,45,45,0.4)] px-3 py-2 font-mono text-[0.75rem] font-bold uppercase tracking-[0.08em] text-led disabled:opacity-60"
          title="Rollback isn't wired yet"
        >
          <Undo2 className="size-3.5" />
          Roll back &middot; {model?.active_version ?? "--"}
        </button>
        <div className="flex-1" />
        <button
          type="button"
          disabled={!job || job.status !== "succeeded"}
          className={cn(
            "inline-flex items-center gap-2 rounded-md px-4 py-2 font-mono text-[0.75rem] font-bold uppercase tracking-[0.08em]",
            job?.status === "succeeded"
              ? "bg-beep text-bg shadow-[0_0_12px_var(--color-beep-glow)] hover:opacity-90"
              : "border border-rule text-muted disabled:opacity-50",
          )}
          title={
            job?.status === "succeeded"
              ? "Replace the shipped artifact with the just-built candidate"
              : "Promote becomes active once a build succeeds"
          }
        >
          Promote to shipped
          <ChevronRight className="size-3.5" />
        </button>
      </footer>
    </div>
  );
}

function CompareStrip({ model, job }: { model: DeveloperShellOutletContext["model"]; job: Job | null }) {
  const shipped = {
    role: "Shipped",
    ver: model?.active_version ?? "v0.0.0",
    recall: model?.recall ?? 0,
    fixtures: model?.fixture_count ?? 0,
  };
  const candidate = job
    ? {
        role: "Candidate",
        ver: "in build",
        recall: 0,
        fixtures: 0,
      }
    : null;
  return (
    <section className="mb-6 grid grid-cols-[1fr_64px_1fr] items-stretch gap-3">
      <ComparePanel data={shipped} accent="ink" />
      <div aria-hidden className="flex items-center justify-center">
        <span className="size-7 rounded-full border border-beep bg-bg shadow-[0_0_12px_var(--color-beep-glow)] flex items-center justify-center text-beep">
          <ChevronRight className="size-4" />
        </span>
      </div>
      {candidate ? (
        <ComparePanel data={candidate} accent="beep" />
      ) : (
        <div className="flex items-center justify-center rounded-md border border-dashed border-rule bg-surface px-4 py-6 text-center font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
          No candidate yet
          <br />
          Click Run build above
        </div>
      )}
    </section>
  );
}

function ComparePanel({
  data,
  accent,
}: {
  data: { role: string; ver: string; recall: number; fixtures: number };
  accent: "ink" | "beep";
}) {
  return (
    <div
      className={cn(
        "rounded-md border bg-surface px-4 py-3",
        accent === "beep"
          ? "border-[rgba(6,182,212,0.4)] bg-[color:var(--color-beep-tint)]"
          : "border-rule",
      )}
    >
      <div className="flex items-center gap-2 font-mono text-[0.625rem] font-bold uppercase tracking-[0.18em] text-muted">
        <span
          className={cn(
            "size-1.5 rounded-full",
            accent === "beep" ? "bg-beep shadow-[0_0_6px_var(--color-beep-glow)]" : "bg-ink-2",
          )}
        />
        {data.role}
      </div>
      <div
        className={cn(
          "mt-1 font-display text-[1.5rem] font-bold uppercase tracking-tight",
          accent === "beep" ? "text-beep" : "text-ink",
        )}
      >
        ensemble {data.ver}
      </div>
      <div className="mt-2 grid grid-cols-3 border-t border-dashed border-rule pt-2 font-mono text-[0.6875rem] tabular-nums">
        <div>
          <div className="text-muted">Fixtures</div>
          <b className="text-ink">{data.fixtures || "--"}</b>
        </div>
        <div>
          <div className="text-muted">Recall</div>
          <b className={accent === "beep" ? "text-done" : "text-ink"}>
            {data.recall ? data.recall.toFixed(3) : "--"}
          </b>
        </div>
        <div>
          <div className="text-muted">Built</div>
          <b className="text-ink">--</b>
        </div>
      </div>
    </div>
  );
}

function PipelineCard({
  job,
  stageIdx,
  running,
}: {
  job: Job | null;
  stageIdx: number;
  running: boolean;
}) {
  const failedAt = job?.status === "failed" ? stageIdx : -1;
  return (
    <section className="mb-6 overflow-hidden rounded-md border border-rule bg-surface">
      <header className="flex items-center justify-between border-b border-rule px-5 py-3">
        <div>
          <div className="flex items-center gap-2 font-mono text-[0.625rem] font-bold uppercase tracking-[0.18em] text-beep">
            <span
              className={cn(
                "size-1.5 rounded-full",
                running ? "bg-beep shadow-[0_0_6px_var(--color-beep-glow)]" : "bg-muted",
              )}
            />
            Artifact build pipeline
          </div>
          <div className="font-mono text-[0.6875rem] text-muted">
            scripts/build_ensemble_artifacts.py &middot; runner local-worker
          </div>
        </div>
        <div className="flex items-center gap-5 font-mono text-[0.6875rem] tabular-nums">
          <span>
            <span className="text-muted">Stage </span>
            <b className="text-ink">
              {stageIdx >= 0 ? `${stageIdx + 1} / ${STAGES.length}` : "-- / --"}
            </b>
          </span>
          <span>
            <span className="text-muted">Progress </span>
            <b className="text-beep">{job ? `${Math.round((job.progress ?? 0) * 100)}%` : "--"}</b>
          </span>
        </div>
      </header>

      <div className="grid grid-cols-6 gap-3 p-5">
        {STAGES.map((stage, i) => {
          const done = i < stageIdx || job?.status === "succeeded";
          const active = i === stageIdx && running;
          const failed = failedAt === i;
          return (
            <div
              key={stage.num}
              className={cn(
                "relative overflow-hidden rounded-md border bg-bg-glow p-3",
                failed
                  ? "border-[rgba(255,45,45,0.4)] bg-[color:var(--color-led-tint)]"
                  : active
                    ? "border-[rgba(6,182,212,0.4)] bg-[color:var(--color-beep-tint)]"
                    : done
                      ? "border-rule"
                      : "border-rule opacity-60",
              )}
            >
              <div className="flex items-center justify-between">
                <span
                  className={cn(
                    "font-mono text-[0.625rem] font-bold tabular-nums",
                    active ? "text-beep" : done ? "text-done" : "text-muted",
                  )}
                >
                  {stage.num}
                </span>
                <span
                  className={cn(
                    "rounded-full px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.06em]",
                    failed
                      ? "bg-[color:var(--color-led-tint)] text-led"
                      : active
                        ? "bg-[color:var(--color-beep-tint)] text-beep"
                        : done
                          ? "bg-[color:var(--color-done-tint)] text-done"
                          : "bg-surface-3 text-muted",
                  )}
                >
                  {failed ? "fail" : active ? "run" : done ? "done" : "idle"}
                </span>
              </div>
              <div className="mt-2 font-display text-[0.875rem] font-bold uppercase tracking-tight text-ink">
                {stage.name}
              </div>
              <div className="mt-1 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                {stage.desc}
              </div>
              {active && (
                <div className="mt-3 h-1 overflow-hidden rounded-full bg-surface-3">
                  <div
                    className="h-full animate-pulse rounded-full bg-beep"
                    style={{ width: "45%" }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function LogTail({
  lines,
  running,
}: {
  lines: { ts: string; level: "INFO" | "OK" | "WARN"; msg: string }[];
  running: boolean;
}) {
  return (
    <section className="mb-6 overflow-hidden rounded-md border border-rule bg-[#06080A]">
      <header className="flex items-center justify-between border-b border-rule px-5 py-2.5">
        <div className="flex items-center gap-2 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.18em] text-beep">
          <span
            className={cn(
              "size-1.5 rounded-full",
              running ? "bg-beep shadow-[0_0_6px_var(--color-beep-glow)]" : "bg-muted",
            )}
          />
          Live training output
        </div>
        <div className="flex items-center gap-1 font-mono text-[0.625rem]">
          {(["INFO", "DEBUG", "PAUSE"] as const).map((lvl) => (
            <button
              key={lvl}
              type="button"
              className={cn(
                "rounded border border-rule bg-surface-2 px-1.5 py-0.5 uppercase tracking-[0.08em]",
                lvl === "INFO" ? "text-beep" : "text-muted",
              )}
            >
              {lvl === "PAUSE" ? <Pause className="size-2.5 inline" /> : lvl}
            </button>
          ))}
        </div>
      </header>
      <div className="h-44 overflow-y-auto bg-[#06080A] px-5 py-3 font-mono text-[0.6875rem] leading-relaxed">
        {lines.length === 0 ? (
          <div className="text-whisper">tail -f build.log -- run a build to see output</div>
        ) : (
          lines.map((line, i) => (
            <div
              key={i}
              className="grid grid-cols-[70px_60px_1fr] gap-3"
              style={{
                textShadow: "0 0 4px rgba(6,182,212,0.1)",
              }}
            >
              <span className="text-whisper">{line.ts}</span>
              <span
                className={cn(
                  "font-bold uppercase",
                  line.level === "INFO" && "text-beep",
                  line.level === "OK" && "text-done",
                  line.level === "WARN" && "text-live",
                )}
              >
                {line.level}
              </span>
              <span className="text-ink-2">{line.msg}</span>
            </div>
          ))
        )}
        {running && (
          <div className="mt-1 inline-block h-3 w-1.5 animate-pulse bg-beep" aria-hidden />
        )}
      </div>
    </section>
  );
}

const VOTERS = [
  {
    key: "A",
    accent: "led",
    name: "Envelope onset",
    sub: "candidate gen",
    feat: "12x hand-crafted envelope features",
  },
  {
    key: "B",
    accent: "beep",
    name: "CLAP semantic",
    sub: "shot vs not-shot differential",
    feat: "shot/not-shot prompt similarity",
  },
  {
    key: "C",
    accent: "live",
    name: "GBDT classifier",
    sub: "feature interaction",
    feat: "31 features (envelope + CLAP + PANN)",
  },
] as const;

function VoterGrid() {
  return (
    <section className="mb-6">
      <div className="mb-2 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.18em] text-subtle">
        Voter detail
      </div>
      <div className="grid grid-cols-3 gap-4">
        {VOTERS.map((v) => (
          <div
            key={v.key}
            className="relative overflow-hidden rounded-md border border-rule bg-surface px-4 py-3.5"
          >
            <span
              aria-hidden
              className={cn(
                "absolute left-0 top-0 h-full w-[3px]",
                v.accent === "led" && "bg-led",
                v.accent === "beep" && "bg-beep",
                v.accent === "live" && "bg-live",
              )}
            />
            <div className="flex items-start justify-between">
              <div>
                <span
                  className={cn(
                    "inline-flex size-7 items-center justify-center rounded-md font-display text-[0.875rem] font-bold",
                    v.accent === "led" && "bg-[color:var(--color-led-tint)] text-led",
                    v.accent === "beep" && "bg-[color:var(--color-beep-tint)] text-beep",
                    v.accent === "live" && "bg-[color:var(--color-live-tint)] text-live",
                  )}
                >
                  {v.key}
                </span>
                <div className="mt-2 font-display text-[0.9375rem] font-bold uppercase tracking-tight text-ink">
                  {v.name}
                </div>
                <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                  {v.sub}
                </div>
              </div>
            </div>
            <div className="mt-3 border-t border-dashed border-rule pt-3 font-mono text-[0.6875rem] text-muted">
              {v.feat}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function HistoryCard({ model }: { model: DeveloperShellOutletContext["model"] }) {
  // Single-row history showing the active model only; persistent multi-row
  // history requires storing prior calibration_versions on disk -- not
  // wired yet, so the table is honest about there being one entry today.
  return (
    <section className="overflow-hidden rounded-md border border-rule bg-surface">
      <header className="flex items-center justify-between border-b border-rule px-5 py-3">
        <div className="flex items-center gap-2 font-display text-[1rem] font-bold uppercase tracking-tight text-ink">
          <Clock className="size-4 text-muted" />
          Build history
        </div>
        <div className="font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
          Tap a row to compare
        </div>
      </header>
      <div className="grid grid-cols-[150px_110px_90px_1fr_140px_110px] border-b border-rule bg-surface-2 px-5 py-2 font-mono text-[0.625rem] font-bold uppercase tracking-[0.12em] text-subtle">
        <span>Version</span>
        <span>When</span>
        <span>Recall</span>
        <span>Note</span>
        <span>By</span>
        <span>Status</span>
      </div>
      <ul>
        <li className="grid grid-cols-[150px_110px_90px_1fr_140px_110px] items-center gap-3 border-b border-rule px-5 py-3">
          <span className="flex items-center gap-2 font-mono text-[0.8125rem] font-bold text-ink">
            <span className="size-1.5 rounded-full bg-beep shadow-[0_0_6px_var(--color-beep-glow)]" />
            {model?.active_version ?? "--"}
          </span>
          <span className="font-mono text-[0.6875rem] tabular-nums text-muted">
            {model?.built_at ? new Date(model.built_at).toLocaleDateString() : "--"}
          </span>
          <span className="font-mono text-[0.8125rem] font-bold tabular-nums text-done">
            {model?.recall.toFixed(3) ?? "--"}
          </span>
          <span className="font-mono text-[0.75rem] text-ink-2">Shipped artifact</span>
          <span className="font-mono text-[0.75rem] text-muted">--</span>
          <span className="inline-flex items-center gap-2 rounded-full bg-[color:var(--color-beep-tint)] px-2 py-0.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.08em] text-beep">
            active
          </span>
        </li>
        <li className="px-5 py-4 text-center font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
          <AlertTriangle className="mr-1 inline size-3 text-live" />
          Prior builds aren't persisted yet -- this is a v1 of the retrain surface.
        </li>
      </ul>
    </section>
  );
}
