/**
 * Tuning + save-as-YAML panel, lifted out of legacy ``Lab.tsx`` (#886
 * follow-up) as ``TuningCard`` + ``SaveYamlButton`` combined into one
 * component for the redesigned Validate page. Unlike the promote
 * panels, legacy Lab.tsx keeps its own inline copy of this logic (the
 * same pattern ``useLabRun`` already established -- see that file's
 * header comment) since it is slated for deletion in PR 5; this
 * component is for the new Lab surfaces only.
 *
 * Sliders rescore the cached universe live via the caller's debounced
 * ``onChange`` (typically backed by ``useLabRun``); ``rescoreLoading``
 * just drives the header spinner so a slider tweak reads as "in
 * flight" during the ~120ms debounce + request round-trip.
 */
import { useCallback, useEffect, useState } from "react";
import { Loader2, RotateCcw, Save, Settings2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api, type LabEvalConfig, type LabEvalRun } from "@/lib/api";

export function TuningPanel({
  config,
  onChange,
  onReset,
  run,
  rescoreLoading,
}: {
  config: LabEvalConfig;
  onChange: (patch: Partial<LabEvalConfig>) => void;
  onReset: () => void;
  run: LabEvalRun | null;
  rescoreLoading: boolean;
}) {
  const cal = run?.universe;
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2">
          <Settings2 className="size-4" />
          Tuning
          {rescoreLoading && <Loader2 className="size-4 animate-spin text-muted" />}
        </CardTitle>
        <CardDescription>
          Sliders rescore the cached universe live; "Run eval" refreshes the universe.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Slider
          label={`Consensus K (${config.consensus} of 4)`}
          value={config.consensus}
          min={1}
          max={4}
          step={1}
          onChange={(v) => onChange({ consensus: v })}
        />
        <Slider
          label={`Apriori boost (${config.apriori_boost.toFixed(2)})`}
          value={config.apriori_boost}
          min={0}
          max={2}
          step={0.05}
          onChange={(v) => onChange({ apriori_boost: v })}
        />
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={config.use_expected_rounds}
            onChange={(e) => onChange({ use_expected_rounds: e.target.checked })}
          />
          Use expected_rounds (adaptive voter C + apriori boost)
        </label>
        {cal && (
          <details className="rounded border border-rule/60 bg-muted/30 px-3 py-2 text-xs">
            <summary className="cursor-pointer font-medium">Per-voter threshold overrides</summary>
            <div className="mt-3 space-y-2">
              <ThresholdRow
                label="Voter A floor"
                calibrated={cal.voter_a_floor}
                value={config.voter_a_floor_override}
                onChange={(v) => onChange({ voter_a_floor_override: v })}
                min={0}
                max={0.5}
                step={0.001}
              />
              <ThresholdRow
                label="Voter B threshold"
                calibrated={cal.voter_b_threshold}
                value={config.voter_b_threshold_override}
                onChange={(v) => onChange({ voter_b_threshold_override: v })}
                min={-0.05}
                max={0.2}
                step={0.001}
              />
              <ThresholdRow
                label="Voter C threshold"
                calibrated={cal.voter_c_threshold}
                value={config.voter_c_threshold_override}
                onChange={(v) => onChange({ voter_c_threshold_override: v })}
                min={0}
                max={1}
                step={0.005}
              />
            </div>
          </details>
        )}
        <div className="flex items-center gap-2 pt-1">
          <Button variant="ghost" size="sm" onClick={onReset}>
            <RotateCcw className="size-3.5" />
            Reset
          </Button>
          <SaveYamlButton run={run} />
        </div>
      </CardContent>
    </Card>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block text-xs">
      <div className="mb-1 font-medium text-ink">{label}</div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
      />
    </label>
  );
}

function ThresholdRow({
  label,
  calibrated,
  value,
  onChange,
  min,
  max,
  step,
}: {
  label: string;
  calibrated: number;
  value: number | null;
  onChange: (v: number | null) => void;
  min: number;
  max: number;
  step: number;
}) {
  const active = value !== null;
  const display = active ? value : calibrated;
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{label}</span>
        <span className="font-mono text-[10px] text-muted">
          {active ? `override ${display.toFixed(4)}` : `calibrated ${calibrated.toFixed(4)}`}
        </span>
      </div>
      <div className="mt-1 flex items-center gap-2">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={display}
          onChange={(e) => onChange(Number(e.target.value))}
          className="flex-1"
        />
        {active && (
          <button
            type="button"
            className="text-[10px] text-muted hover:text-ink"
            onClick={() => onChange(null)}
          >
            clear
          </button>
        )}
      </div>
    </div>
  );
}

function SaveYamlButton({ run }: { run: LabEvalRun | null }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Suggest a slug derived from the active config hash so accidental
  // double-clicks don't all collide on "ensemble.tuning.yaml".
  useEffect(() => {
    if (open && !name && run) {
      setName(`tuning-${run.config_hash}`);
    }
  }, [open, name, run]);

  const submit = useCallback(async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.saveLabConfig({ name: name.trim(), note: note.trim() || undefined, overwrite });
      setResult(res.path);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }, [name, note, overwrite]);

  return (
    <div className="relative">
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen((v) => !v)}
        disabled={!run}
        title={run ? "Save current tuning as configs/ensemble.<name>.yaml" : "Run eval first"}
      >
        <Save className="size-4" />
        Save as YAML
      </Button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-80 rounded-md border border-rule bg-surface-2 p-3 shadow-md">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted">
            Save tuning
          </div>
          <p className="mt-1 text-[11px] text-muted">
            Writes <span className="font-mono">configs/ensemble.&lt;name&gt;.yaml</span> with the active
            config + summary + provenance. Replayable via <span className="font-mono">splitsmith lab load-config</span>.
          </p>
          <label className="mt-2 block text-[11px]">
            <span className="text-muted">Name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="mt-1 w-full rounded border border-rule bg-bg px-2 py-1 font-mono text-xs"
              placeholder="tighter-d"
            />
          </label>
          <label className="mt-2 block text-[11px]">
            <span className="text-muted">Note (optional)</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              className="mt-1 w-full rounded border border-rule bg-bg px-2 py-1 text-xs"
              placeholder="Why this tuning is interesting..."
            />
          </label>
          <label className="mt-2 flex items-center gap-2 text-[11px]">
            <input
              type="checkbox"
              checked={overwrite}
              onChange={(e) => setOverwrite(e.target.checked)}
            />
            Overwrite if exists
          </label>
          {error && (
            <div className="mt-2 rounded bg-destructive/10 px-2 py-1 text-[11px] text-destructive">
              {error}
            </div>
          )}
          {result && (
            <div className="mt-2 rounded bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-700 dark:text-emerald-300">
              Saved: <span className="font-mono">{result}</span>
            </div>
          )}
          <div className="mt-3 flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
              Close
            </Button>
            <Button size="sm" onClick={submit} disabled={busy || !name.trim()}>
              {busy ? <Loader2 className="size-3.5 animate-spin" /> : "Save"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
