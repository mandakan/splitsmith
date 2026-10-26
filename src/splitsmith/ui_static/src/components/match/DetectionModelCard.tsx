/**
 * The opt-in for the head camera audio model (spec 2026-10-26).
 *
 * Shot detection needs no downloaded model by default (the ``base`` model
 * set). Turning this on chooses ``clap``: the server downloads the 120 MB
 * CLAP model once, and detection uses it once it is here. A machine-level
 * setting, so local only, shown beside the sync row the way the global
 * auto-sync switch is. Wording lives in ``lib/detectionModel``.
 */
import { AudioLines } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api, type DetectionModelSettings } from "@/lib/api";
import { detectionModelLine } from "@/lib/detectionModel";

/** How often the card re-reads the setting while a download runs. */
const POLL_MS = 2000;

export function DetectionModelCard() {
  const [settings, setSettings] = useState<DetectionModelSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setSettings(await api.getDetectionModel());
    } catch {
      setError("Could not read the detection model setting.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll only while a download runs, so an idle card makes no requests.
  const downloading = settings?.active_job != null;
  useEffect(() => {
    if (!downloading) return;
    const id = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(id);
  }, [downloading, load]);

  const on = settings?.model_set === "clap";
  const toggle = async () => {
    setSaving(true);
    setError(null);
    try {
      setSettings(await api.putDetectionModel(on ? "base" : "clap"));
    } catch {
      setError("Could not change the detection model.");
    } finally {
      setSaving(false);
    }
  };

  const line = detectionModelLine(settings);
  return (
    <section
      aria-label="Audio model for head cameras"
      className="mb-4 rounded-[10px] border border-rule bg-surface px-3.5 py-2"
    >
      <div className="flex flex-wrap items-center gap-3">
        <span
          aria-hidden="true"
          className="inline-flex size-[22px] shrink-0 items-center justify-center rounded-md border border-rule-strong text-muted"
        >
          <AudioLines className="size-3" />
        </span>
        <h2 className="text-md font-medium text-ink">Audio model for head cameras</h2>
        <p className="min-w-0 flex-1 text-sm text-ink-2">
          Downloads a 120 MB model once. Improves shot detection on head cameras a little.
        </p>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label="Audio model for head cameras"
          disabled={settings == null || saving}
          onClick={() => void toggle()}
          className="min-h-8 rounded border border-rule px-3 text-sm text-ink disabled:opacity-50"
        >
          {on ? "On" : "Off"}
        </button>
      </div>
      {line ? (
        <p className="mt-1 text-sm text-muted" aria-live="polite">
          {line}
        </p>
      ) : null}
      {error ? <p className="mt-1 text-sm text-destructive">{error}</p> : null}
    </section>
  );
}
