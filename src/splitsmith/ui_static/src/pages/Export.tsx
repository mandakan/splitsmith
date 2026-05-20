/**
 * Export route (/export) -- final-cut bundle configurator (#330).
 *
 * Two-column workspace per polished/08:
 *
 *   Left -- numbered sections the user fills in top-to-bottom:
 *     1. Output mode (single-shooter timeline -- compare-grid disabled
 *        until #328 lands)
 *     2. Stages chip selector (only audited stages exportable)
 *     3. Trim padding presets + custom inputs
 *     4. Transitions between stages + duration + title-card style
 *     5. Overlay (none / include) -- variant tiles (counter, timer,
 *        banner) are out of scope until the backend exposes them
 *     6. Output formats (FCPXML always; CSV + text report always
 *        written as siblings) + destination preview
 *
 *   Right -- sticky summary rail with live stage/duration counts and
 *   the LED Export CTA.
 *
 * Mounted under <MatchShell />, so the page chrome (Shot Timer header,
 * per-match sidebar with stage status) is shared with /audit + /overview.
 */

import {
  Check,
  CheckCircle2,
  ChevronDown,
  ExternalLink,
  FileBarChart,
  FileText,
  Film,
  Loader2,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { Navigate, useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  ApiError,
  api,
  type ExportOverview,
  type Job,
  type MatchExportResult,
  type MatchProject,
  type OverlayCodec,
  type StageExportStatus,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type OutputMode = "single" | "compare";
type PaddingPreset = "full" | "action" | "highlight" | "custom";

const PADDING_PRESETS: Record<
  Exclude<PaddingPreset, "custom">,
  { label: string; head: number; tail: number; help: string }
> = {
  full: {
    label: "Full",
    head: 5.0,
    tail: 5.0,
    help: "Matches the per-stage export defaults.",
  },
  action: {
    label: "Action",
    head: 0.5,
    tail: 1.0,
    help: "Tight: 0.5s before beep, 1s after final shot.",
  },
  highlight: {
    label: "Highlight",
    head: 1.5,
    tail: 2.0,
    help: "Mid: 1.5s before beep, 2s after final shot.",
  },
};

const TRANSITIONS: {
  kind: "none" | "zoom" | "static";
  label: string;
  body: string;
}[] = [
  {
    kind: "none",
    label: "Hard cut",
    body: "Adjacent stages butt-cut on the spine. Closest to a raw stitch.",
  },
  {
    kind: "static",
    label: "Static frame",
    body: "FCP 'Lights / Static' between stages -- a steady held frame.",
  },
  {
    kind: "zoom",
    label: "Zoom blur",
    body: "FCP 'Blurs / Zoom' between stages. Punchy match-reel feel.",
  },
];

const TITLE_STYLES: {
  kind: "none" | "slate" | "lower-third";
  label: string;
}[] = [
  { kind: "none", label: "No title card" },
  { kind: "slate", label: "Pre-stage slate" },
  { kind: "lower-third", label: "Lower-third banner" },
];

export function Export() {
  const { slug } = useParams<{ slug: string }>();
  if (!slug) return <Navigate to="/shooters" replace />;
  return <ExportInner slug={slug} />;
}

function ExportInner({ slug }: { slug: string }) {
  const [project, setProject] = useState<MatchProject | null>(null);
  const [overview, setOverview] = useState<ExportOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<MatchExportResult | null>(null);

  const reload = useCallback(async () => {
    try {
      const [proj, ov] = await Promise.all([
        api.getProject(slug),
        api.getExportOverview(slug),
      ]);
      setProject(proj);
      setOverview(ov);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [slug]);

  useEffect(() => {
    void reload();
  }, [reload]);


  // === Form state ===
  const [mode, setMode] = useState<OutputMode>("single");
  const [selection, setSelection] = useState<Set<number>>(() => new Set());
  const [preset, setPreset] = useState<PaddingPreset>("full");
  const [headPad, setHeadPad] = useState<number>(PADDING_PRESETS.full.head);
  const [tailPad, setTailPad] = useState<number>(PADDING_PRESETS.full.tail);
  const [transitionKind, setTransitionKind] = useState<
    "none" | "zoom" | "static"
  >("none");
  const [transitionDurationSeconds, setTransitionDurationSeconds] =
    useState<number>(0.5);
  const [titleKind, setTitleKind] = useState<"none" | "slate" | "lower-third">(
    "none",
  );
  const [titleDurationSeconds, setTitleDurationSeconds] = useState<number>(1.5);
  const [includeOverlay, setIncludeOverlay] = useState<boolean>(false);
  const [overlayCodec, setOverlayCodec] = useState<OverlayCodec>("auto");
  const [outputFormat, setOutputFormat] = useState<
    "fcpxml" | "fcp7xml" | "mp4"
  >("fcpxml");
  const [projectName, setProjectName] = useState<string>("");

  // Sync the project name once project loads.
  useEffect(() => {
    if (project && !projectName) setProjectName(project.name);
  }, [project, projectName]);

  // Stage eligibility
  const stages: StageExportStatus[] = overview?.stages ?? [];
  const auditedStages = useMemo(
    () => stages.filter((s) => !s.skipped && s.ready_to_export),
    [stages],
  );
  const eligibleNumbers = useMemo(
    () =>
      auditedStages
        .filter((s) => s.source_reachable !== false)
        .map((s) => s.stage_number),
    [auditedStages],
  );
  const eligibleSet = useMemo(
    () => new Set(eligibleNumbers),
    [eligibleNumbers],
  );
  const sourceMissingNumbers = useMemo(
    () =>
      auditedStages
        .filter((s) => s.source_reachable === false)
        .map((s) => s.stage_number),
    [auditedStages],
  );
  const sourceMissingSet = useMemo(
    () => new Set(sourceMissingNumbers),
    [sourceMissingNumbers],
  );

  // Pre-select all eligible stages on first load.
  useEffect(() => {
    if (eligibleNumbers.length > 0 && selection.size === 0) {
      setSelection(new Set(eligibleNumbers));
    }
  }, [eligibleNumbers, selection.size]);

  // Drop any stages that became ineligible.
  useEffect(() => {
    setSelection((prev) => {
      const next = new Set<number>();
      for (const n of prev) if (eligibleSet.has(n)) next.add(n);
      return next.size === prev.size ? prev : next;
    });
  }, [eligibleSet]);

  const toggleStage = useCallback(
    (n: number) => {
      if (!eligibleSet.has(n)) return;
      setSelection((prev) => {
        const next = new Set(prev);
        if (next.has(n)) next.delete(n);
        else next.add(n);
        return next;
      });
    },
    [eligibleSet],
  );

  const orderedSelection = useMemo(
    () => stages.map((s) => s.stage_number).filter((n) => selection.has(n)),
    [stages, selection],
  );

  // Custom preset auto-sync.
  function selectPreset(next: PaddingPreset) {
    setPreset(next);
    if (next !== "custom") {
      setHeadPad(PADDING_PRESETS[next].head);
      setTailPad(PADDING_PRESETS[next].tail);
    }
  }

  // Estimated stats for the summary rail. Duration is summed from each
  // stage's time_seconds (off the project, since overview rows don't
  // carry it) plus head/tail pads, with transitions added in when non-
  // cut. Not exact but close enough for a "~mm:ss" indicator.
  const stageTimeByNumber = useMemo(() => {
    const m = new Map<number, number>();
    for (const s of project?.stages ?? []) m.set(s.stage_number, s.time_seconds);
    return m;
  }, [project]);
  const estimate = useMemo(() => {
    const selectedCount = orderedSelection.length;
    let duration = 0;
    for (const n of orderedSelection) {
      duration += (stageTimeByNumber.get(n) ?? 0) + headPad + tailPad;
    }
    if (transitionKind !== "none" && selectedCount > 1) {
      duration += transitionDurationSeconds * (selectedCount - 1);
    }
    if (titleKind === "slate" && selectedCount > 0) {
      duration += titleDurationSeconds * selectedCount;
    }
    return { duration };
  }, [
    orderedSelection,
    stageTimeByNumber,
    headPad,
    tailPad,
    transitionKind,
    transitionDurationSeconds,
    titleKind,
    titleDurationSeconds,
  ]);

  const busy = job?.status === "pending" || job?.status === "running";
  const canExport =
    !busy && mode === "single" && orderedSelection.length > 0 && !!project;

  async function submitExport() {
    if (!canExport || !project) return;
    setError(null);
    setResult(null);
    try {
      const submitted = await api.exportMatch(slug, {
        stage_numbers: orderedSelection,
        head_pad_seconds: headPad,
        tail_pad_seconds: tailPad,
        include_secondaries: false,
        pip_layout: "stacked",
        output_format: outputFormat,
        transition_kind: transitionKind,
        transition_duration_seconds: transitionDurationSeconds,
        title_kind: titleKind,
        title_duration_seconds: titleDurationSeconds,
        intro_path: undefined,
        outro_path: undefined,
        youtube_sidecar: false,
        youtube_preset: false,
        include_overlay: includeOverlay,
        overlay_codec: overlayCodec,
        overlay_max_height: null,
        overlay_max_fps: null,
        project_name: projectName || project.name,
      });
      setJob(submitted);
      const final = await api.pollJob(submitted.id, setJob);
      if (final.status === "succeeded" && final.result) {
        setResult(final.result as unknown as MatchExportResult);
      } else if (final.status === "failed") {
        setError(final.error ?? "Export failed");
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }

  async function reveal(path: string) {
    try {
      await api.revealFile(path);
    } catch {
      // Reveal is non-critical
    }
  }

  if (!project && !error) {
    return (
      <div className="px-7 py-6 text-sm text-muted">Loading project...</div>
    );
  }

  return (
    <div className="px-7 py-5">
      <div className="mb-5">
        <Kicker className="mb-2">Final cut &middot; bundle</Kicker>
        <h1 className="mb-2 font-display text-4xl font-bold uppercase leading-none tracking-tight text-ink">
          Export
        </h1>
        <p className="max-w-[40rem] text-sm text-muted">
          Pick stages, trim, transitions, and overlays. Splitsmith writes
          a final-cut bundle (FCPXML + CSV + text report) to the
          project's exports folder. Open the FCPXML in Final Cut to
          finish the cut.
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded-md border border-led/40 bg-led/10 px-3 py-2 text-sm text-led">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_340px]">
        {/* Left column: sections */}
        <div className="flex min-w-0 flex-col gap-5">
          {/* Section 1: Output mode */}
          <Section number={1} title="Output mode" help="Single-shooter timeline today; compare grid arrives in #328.">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <ModeOption
                selected={mode === "single"}
                onClick={() => setMode("single")}
                title="Single-shooter timeline"
                body="One FCPXML with selected stages back-to-back on the spine."
                icon={<Film className="size-4" />}
              />
              <ModeOption
                disabled
                selected={mode === "compare"}
                onClick={() => setMode("compare")}
                title="Multi-shooter compare grid"
                body="Side-by-side grid per stage, audio from one shooter. Arrives with #328."
                icon={<Film className="size-4" />}
                badge="#328"
              />
            </div>
          </Section>

          {/* Section 2: Stages */}
          <Section
            number={2}
            title="Stages"
            help={stageSectionHelp(
              eligibleNumbers.length,
              sourceMissingNumbers.length,
              auditedStages.length,
              stages.length,
            )}
          >
            {sourceMissingNumbers.length > 0 && (
              <div className="mb-3 flex items-start gap-2.5 rounded-md border border-live/40 bg-live/10 px-3 py-2 text-[0.8125rem] text-ink-2">
                <span
                  aria-hidden
                  className="mt-1 inline-block size-2 shrink-0 rounded-full bg-live shadow-[0_0_8px_var(--color-live-glow)]"
                />
                <div className="min-w-0">
                  <div className="font-display text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-live">
                    Source offline
                  </div>
                  <div className="mt-0.5 text-muted">
                    {sourceMissingNumbers.length} audited{" "}
                    {sourceMissingNumbers.length === 1 ? "stage" : "stages"} can't
                    export -- the original video files aren't reachable. Mount
                    the source drive (or use Relink) and reload the page.
                  </div>
                </div>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              {stages.map((s) => (
                <StageChip
                  key={s.stage_number}
                  stage={s}
                  selected={selection.has(s.stage_number)}
                  eligible={eligibleSet.has(s.stage_number)}
                  sourceMissing={sourceMissingSet.has(s.stage_number)}
                  onToggle={() => toggleStage(s.stage_number)}
                />
              ))}
            </div>
          </Section>

          {/* Section 3: Trim padding */}
          <Section number={3} title="Trim padding" help="Padding around each stage's beep and last shot.">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {(Object.keys(PADDING_PRESETS) as Array<
                Exclude<PaddingPreset, "custom">
              >).map((key) => (
                <PresetCard
                  key={key}
                  selected={preset === key}
                  onClick={() => selectPreset(key)}
                  title={PADDING_PRESETS[key].label}
                  body={`${PADDING_PRESETS[key].head}s / ${PADDING_PRESETS[key].tail}s`}
                />
              ))}
              <PresetCard
                selected={preset === "custom"}
                onClick={() => selectPreset("custom")}
                title="Custom"
                body="set below"
              />
            </div>
            {preset === "custom" && (
              <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <NumInput
                  label="Before beep (s)"
                  value={headPad}
                  step={0.1}
                  min={0}
                  onChange={setHeadPad}
                />
                <NumInput
                  label="After last shot (s)"
                  value={tailPad}
                  step={0.1}
                  min={0}
                  onChange={setTailPad}
                />
              </div>
            )}
          </Section>

          {/* Section 4: Transitions */}
          <Section number={4} title="Transitions" help="How adjacent stages connect on the spine.">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {TRANSITIONS.map((t) => (
                <PresetCard
                  key={t.kind}
                  selected={transitionKind === t.kind}
                  onClick={() => setTransitionKind(t.kind)}
                  title={t.label}
                  body={t.body}
                />
              ))}
            </div>
            {transitionKind !== "none" && (
              <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <NumInput
                  label="Duration (s)"
                  value={transitionDurationSeconds}
                  step={0.1}
                  min={0.1}
                  onChange={setTransitionDurationSeconds}
                />
              </div>
            )}
            <div className="mt-4 border-t border-rule pt-4">
              <div className="mb-2 font-mono text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-muted">
                Title card
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                {TITLE_STYLES.map((t) => (
                  <PresetCard
                    key={t.kind}
                    selected={titleKind === t.kind}
                    onClick={() => setTitleKind(t.kind)}
                    title={t.label}
                  />
                ))}
              </div>
              {titleKind !== "none" && (
                <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <NumInput
                    label="Title hold (s)"
                    value={titleDurationSeconds}
                    step={0.1}
                    min={0.5}
                    onChange={setTitleDurationSeconds}
                  />
                </div>
              )}
            </div>
          </Section>

          {/* Section 5: Overlay */}
          <Section number={5} title="Overlay" help="Burned-in shot counter + splits. Heavier render -- opt in.">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <PresetCard
                selected={!includeOverlay}
                onClick={() => setIncludeOverlay(false)}
                title="No overlay"
                body="Faster export. FCPXML still carries shot markers."
              />
              <PresetCard
                selected={includeOverlay}
                onClick={() => setIncludeOverlay(true)}
                title="Shot counter + splits"
                body="Render shot index + split time per shot, burned in."
              />
            </div>
            {includeOverlay && (
              <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <SelectField
                  label="Overlay codec"
                  value={overlayCodec}
                  onChange={(v) => setOverlayCodec(v as OverlayCodec)}
                  options={[
                    { value: "auto", label: "Auto" },
                    { value: "h264", label: "H.264" },
                    { value: "prores422", label: "ProRes 422" },
                  ]}
                />
              </div>
            )}
          </Section>

          {/* Section 6: Output */}
          <Section number={6} title="Output" help="Bundle written to the project's exports folder.">
            <FormatRow
              icon={<Film className="size-4" />}
              name="FCPXML"
              suffix={
                outputFormat === "fcp7xml"
                  ? ".fcpxml (xmeml 7)"
                  : outputFormat === "mp4"
                    ? ".mp4 (rendered)"
                    : ".fcpxml (1.10)"
              }
              detail="Final Cut Pro X timeline with stages, markers, transitions."
              selected
            >
              <SelectField
                label="Variant"
                value={outputFormat}
                onChange={(v) => setOutputFormat(v as typeof outputFormat)}
                options={[
                  { value: "fcpxml", label: "FCPXML 1.10 (Final Cut Pro)" },
                  { value: "fcp7xml", label: "FCP 7 XML (Premiere / Resolve)" },
                  { value: "mp4", label: "MP4 (rendered)" },
                ]}
              />
            </FormatRow>
            <FormatRow
              icon={<FileBarChart className="size-4" />}
              name="Splits CSV"
              suffix=".csv"
              detail="Per-shot splits. Always written alongside the FCPXML."
              selected
              locked
            />
            <FormatRow
              icon={<FileText className="size-4" />}
              name="Text report"
              suffix=".txt"
              detail="Human-readable summary. Always written alongside the FCPXML."
              selected
              locked
            />
            <div className="mt-4">
              <label className="mb-1.5 block font-mono text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-muted">
                Destination
              </label>
              <div className="flex items-stretch gap-2">
                <code className="flex-1 truncate rounded-md border border-rule bg-surface-3 px-3 py-2 font-mono text-xs text-ink-2">
                  {project?.exports_dir ?? `${project?.name ?? ""}/exports/`}
                </code>
                {project?.exports_dir && (
                  <button
                    type="button"
                    onClick={() => void reveal(project.exports_dir!)}
                    className="inline-flex items-center gap-1.5 rounded-md border border-rule bg-surface-2 px-3 py-2 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-ink-2 hover:bg-surface-3 hover:text-ink"
                  >
                    Reveal <ExternalLink className="size-3" />
                  </button>
                )}
              </div>
              <p className="mt-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
                Bundle name:{" "}
                <input
                  type="text"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  className="rounded border border-rule bg-surface-3 px-2 py-0.5 font-mono text-[0.6875rem] text-ink-2 outline-none focus:border-led"
                />
              </p>
            </div>
          </Section>
        </div>

        {/* Right column: summary rail */}
        <aside className="lg:sticky lg:top-[6.5rem] lg:self-start">
          <div className="overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
            <div className="border-b border-rule px-5 py-3.5">
              <div className="font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
                Bundle summary
              </div>
              <div className="mt-1 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                Pre-flight check
              </div>
            </div>
            <div className="flex flex-col gap-2.5 px-5 py-4 font-mono text-[0.75rem] uppercase tracking-[0.04em] text-muted tabular-nums">
              <SummaryStat
                label="Stages"
                value={`${orderedSelection.length} / ${eligibleNumbers.length}`}
              />
              <SummaryStat
                label="Padding"
                value={`${headPad.toFixed(1)}s / ${tailPad.toFixed(1)}s`}
              />
              <SummaryStat
                label="Transitions"
                value={
                  transitionKind === "none"
                    ? "hard cut"
                    : `${transitionKind} ${transitionDurationSeconds.toFixed(1)}s`
                }
              />
              <SummaryStat
                label="Titles"
                value={titleKind === "none" ? "off" : titleKind}
              />
              <SummaryStat
                label="Overlay"
                value={includeOverlay ? "on" : "off"}
              />
              <SummaryStat
                label="~ Duration"
                value={formatDuration(estimate.duration)}
              />
            </div>
            <div className="border-t border-rule bg-surface px-5 py-3 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-subtle">
              Will write:
              <div className="mt-1.5 flex flex-col gap-0.5 text-ink-2 normal-case tracking-normal">
                <span className="truncate">
                  {projectName || project?.name}.fcpxml
                </span>
                <span className="truncate text-muted">
                  {projectName || project?.name}.csv
                </span>
                <span className="truncate text-muted">
                  {projectName || project?.name}.txt
                </span>
              </div>
            </div>
            <div className="border-t border-rule px-5 py-4">
              <Button
                type="button"
                onClick={() => void submitExport()}
                disabled={!canExport}
                className="w-full bg-led-fill text-ink shadow-[0_0_0_1px_var(--color-led),0_0_18px_var(--color-led-glow)] hover:bg-led hover:text-ink"
              >
                {busy ? (
                  <Loader2 className="size-3.5 animate-spin" />
                ) : (
                  <Check className="size-3.5" strokeWidth={3} />
                )}
                <span className="font-display uppercase tracking-[0.08em]">
                  {busy ? "Exporting..." : "Export bundle"}
                </span>
              </Button>
              {busy && job?.message && (
                <div className="mt-2 font-mono text-[0.625rem] uppercase tracking-[0.06em] text-muted">
                  {job.message}
                </div>
              )}
              {result && (
                <ResultPanel result={result} onReveal={reveal} />
              )}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Result                                                                     */
/* -------------------------------------------------------------------------- */

function ResultPanel({
  result,
  onReveal,
}: {
  result: MatchExportResult;
  onReveal: (path: string) => void;
}) {
  return (
    <div className="mt-3 rounded-lg border border-done/40 bg-done/10 px-3 py-2.5 text-[0.8125rem] text-ink-2">
      <div className="mb-1.5 inline-flex items-center gap-1.5 font-display font-bold uppercase tracking-[0.08em] text-done">
        <CheckCircle2 className="size-3.5" strokeWidth={2.5} /> Exported
      </div>
      <div className="font-mono text-[0.6875rem] uppercase tracking-[0.04em] text-muted tabular-nums">
        {result.stage_count} stages &middot;{" "}
        {formatDuration(result.duration_seconds)}
        {result.anomalies.length > 0 && (
          <> &middot; {result.anomalies.length} warnings</>
        )}
      </div>
      <button
        type="button"
        onClick={() => onReveal(result.fcpxml_path)}
        className="mt-2 inline-flex items-center gap-1.5 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.1em] text-led hover:text-led-soft"
      >
        Reveal bundle <ExternalLink className="size-3" />
      </button>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Section primitives                                                         */
/* -------------------------------------------------------------------------- */

function Section({
  number,
  title,
  help,
  children,
}: {
  number: number;
  title: string;
  help?: string;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-2xl border border-rule-strong bg-gradient-to-b from-surface to-surface-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_18px_36px_-24px_rgba(0,0,0,0.6)]">
      <div className="flex items-start gap-3 border-b border-rule bg-gradient-to-b from-surface-2 to-transparent px-5 py-3.5">
        <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-led-deep bg-led/10 font-mono text-[0.6875rem] font-bold tabular-nums text-led">
          {pad2(number)}
        </span>
        <div className="min-w-0">
          <div className="font-display text-sm font-bold uppercase tracking-[0.06em] text-ink">
            {title}
          </div>
          {help && (
            <div className="mt-0.5 text-[0.75rem] text-muted">{help}</div>
          )}
        </div>
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

function Kicker({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-2.5 font-mono text-[0.625rem] font-bold uppercase tracking-[0.2em] text-led",
        className,
      )}
    >
      <span
        aria-hidden
        className="inline-block h-px w-[26px] bg-led shadow-[0_0_4px_var(--color-led-glow)]"
      />
      {children}
    </div>
  );
}

function ModeOption({
  selected,
  disabled,
  onClick,
  title,
  body,
  icon,
  badge,
}: {
  selected: boolean;
  disabled?: boolean;
  onClick: () => void;
  title: string;
  body: string;
  icon?: ReactNode;
  badge?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={selected}
      className={cn(
        "relative flex items-start gap-3 overflow-hidden rounded-xl border-[1.5px] p-4 text-left transition-all",
        selected
          ? "border-led bg-led/10 shadow-[0_0_0_1px_var(--color-led-deep),0_0_18px_var(--color-led-glow)]"
          : disabled
            ? "border-rule bg-surface-2 text-muted opacity-50"
            : "border-rule-strong bg-bg-glow hover:border-ink-2",
      )}
    >
      {selected && (
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-[3px] bg-led shadow-[0_0_12px_var(--color-led-glow)]"
        />
      )}
      <span
        className={cn(
          "mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full border-[1.5px]",
          selected
            ? "border-led bg-led"
            : "border-rule-strong bg-surface",
        )}
      >
        {selected && <span className="size-2 rounded-full bg-bg" />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="mb-1 inline-flex items-center gap-2 font-display text-sm font-bold uppercase tracking-[0.04em] text-ink">
          {icon}
          <span>{title}</span>
          {badge && (
            <span className="rounded border border-rule-strong bg-surface-3 px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.1em] text-muted">
              {badge}
            </span>
          )}
        </div>
        <p className="text-[0.8125rem] leading-relaxed text-muted">{body}</p>
      </div>
    </button>
  );
}

function PresetCard({
  selected,
  onClick,
  title,
  body,
}: {
  selected: boolean;
  onClick: () => void;
  title: string;
  body?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={cn(
        "relative flex flex-col items-start gap-1 overflow-hidden rounded-xl border-[1.5px] p-3 text-left transition-all",
        selected
          ? "border-led bg-led/10 shadow-[0_0_0_1px_var(--color-led-deep),0_0_14px_var(--color-led-glow)]"
          : "border-rule-strong bg-bg-glow hover:border-ink-2",
      )}
    >
      {selected && (
        <span
          aria-hidden
          className="absolute inset-y-0 left-0 w-[2px] bg-led shadow-[0_0_10px_var(--color-led-glow)]"
        />
      )}
      <div className="font-display text-[0.8125rem] font-bold uppercase tracking-[0.04em] text-ink">
        {title}
      </div>
      {body && (
        <div
          className={cn(
            "font-mono text-[0.6875rem] tabular-nums",
            selected ? "text-ink-2" : "text-muted",
          )}
        >
          {body}
        </div>
      )}
    </button>
  );
}

function StageChip({
  stage,
  selected,
  eligible,
  sourceMissing,
  onToggle,
}: {
  stage: StageExportStatus;
  selected: boolean;
  eligible: boolean;
  sourceMissing: boolean;
  onToggle: () => void;
}) {
  let title: string;
  if (eligible) {
    title = `Stage ${stage.stage_number} -- ${stage.stage_name}`;
  } else if (sourceMissing) {
    title = "Source video offline -- reconnect the drive and reload.";
  } else if (stage.skipped) {
    title = "Stage skipped.";
  } else {
    title = "Stage not audited yet.";
  }
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={!eligible}
      aria-pressed={selected}
      title={title}
      className={cn(
        "inline-flex min-h-9 items-center gap-2 rounded-md border px-3 py-1.5 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.06em] transition-all",
        !eligible &&
          !sourceMissing &&
          "cursor-not-allowed border-rule bg-surface-2 text-subtle opacity-50",
        !eligible &&
          sourceMissing &&
          "cursor-not-allowed border-live/40 bg-live/10 text-live",
        eligible &&
          selected &&
          "border-led bg-led/10 text-ink shadow-[0_0_0_1px_var(--color-led-deep),0_0_10px_var(--color-led-glow)]",
        eligible &&
          !selected &&
          "border-rule-strong bg-surface-3 text-muted hover:bg-surface-4 hover:text-ink",
      )}
    >
      <span className="font-mono tabular-nums">
        {pad2(stage.stage_number)}
      </span>
      <span>{stage.stage_name}</span>
      {sourceMissing && (
        <span
          aria-hidden
          className="ml-1 inline-block size-1.5 rounded-full bg-live shadow-[0_0_6px_var(--color-live-glow)]"
        />
      )}
    </button>
  );
}

function stageSectionHelp(
  eligibleCount: number,
  sourceMissingCount: number,
  auditedCount: number,
  totalCount: number,
): string {
  if (eligibleCount > 0) {
    return `${eligibleCount} of ${totalCount} stages exportable.`;
  }
  if (sourceMissingCount > 0 && auditedCount === sourceMissingCount) {
    return "Audited, but every source video is offline. Mount the source drive and reload.";
  }
  if (auditedCount === 0) {
    return "No stage is exportable yet. Finish auditing a stage first.";
  }
  return "No stage is exportable. Finish auditing or reconnect missing sources.";
}

function NumInput({
  label,
  value,
  step,
  min,
  onChange,
}: {
  label: string;
  value: number;
  step?: number;
  min?: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-mono text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-muted">
        {label}
      </span>
      <input
        type="number"
        value={value}
        step={step}
        min={min}
        onChange={(e) => {
          const n = parseFloat(e.target.value);
          if (Number.isFinite(n)) onChange(n);
        }}
        className="rounded-md border border-rule bg-surface-3 px-3 py-2 font-mono text-sm tabular-nums text-ink outline-none focus:border-led focus:bg-bg-glow focus:shadow-[0_0_0_3px_var(--color-led-tint)]"
      />
    </label>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-mono text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-muted">
        {label}
      </span>
      <div className="relative">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full appearance-none rounded-md border border-rule bg-surface-3 px-3 py-2 pr-8 font-mono text-sm text-ink outline-none focus:border-led"
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <ChevronDown
          aria-hidden
          className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted"
        />
      </div>
    </label>
  );
}

function FormatRow({
  icon,
  name,
  suffix,
  detail,
  selected,
  locked,
  children,
}: {
  icon: ReactNode;
  name: string;
  suffix: string;
  detail: string;
  selected: boolean;
  locked?: boolean;
  children?: ReactNode;
}) {
  return (
    <div
      className={cn(
        "mb-3 overflow-hidden rounded-xl border-[1.5px] last:mb-0",
        selected ? "border-led" : "border-rule",
      )}
    >
      <div
        className={cn(
          "flex items-start gap-3 px-4 py-3",
          selected ? "bg-led/10" : "bg-bg-glow",
        )}
      >
        <span
          className={cn(
            "mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded border-[1.5px]",
            selected
              ? "border-led bg-led-fill text-ink"
              : "border-rule-strong bg-surface",
          )}
        >
          {selected && <Check className="size-3" strokeWidth={3} />}
        </span>
        <span
          className={cn(
            "inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-rule-strong bg-surface-3",
            selected ? "text-led" : "text-muted",
          )}
        >
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-1.5">
            <span className="font-display text-sm font-bold uppercase tracking-[0.04em] text-ink">
              {name}
            </span>
            <span className="font-mono text-[0.625rem] tabular-nums text-muted">
              {suffix}
            </span>
            {locked && (
              <span className="ml-auto rounded border border-rule-strong bg-surface-2 px-1.5 py-0.5 font-mono text-[0.5625rem] font-bold uppercase tracking-[0.12em] text-muted">
                Always
              </span>
            )}
          </div>
          <div className="mt-0.5 text-[0.75rem] text-muted">{detail}</div>
          {children && <div className="mt-3">{children}</div>}
        </div>
      </div>
    </div>
  );
}

function SummaryStat({
  label,
  value,
}: {
  label: string;
  value: ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span>{label}</span>
      <span className="font-bold text-ink">{value}</span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
