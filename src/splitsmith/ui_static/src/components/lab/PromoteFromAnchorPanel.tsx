/**
 * Promote-from-anchor trigger (issue #125). Moved out of legacy
 * ``Lab.tsx`` (#886 follow-up) alongside {@link PromoteStagesPanel} --
 * see that file's header for the popover-vs-section ``variant`` split.
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertCircle, Link2, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, type Job, type LabFixtureRecord } from "@/lib/api";

export function PromoteFromAnchorPanel({
  fixtures,
  variant = "popover",
}: {
  fixtures: LabFixtureRecord[];
  /** ``popover``: legacy Lab.tsx's absolutely-positioned 384px dropdown.
   *  ``section``: layout-neutral full-width block for the Corpus page. */
  variant?: "popover" | "section";
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [anchorSlug, setAnchorSlug] = useState("");
  const [secondaryWav, setSecondaryWav] = useState("");
  const [slug, setSlug] = useState("");
  const [cameraId, setCameraId] = useState("");
  const [mount, setMount] = useState("tripod");
  const [position, setPosition] = useState("bay-fixed");
  const [audioSource, setAudioSource] = useState("internal");
  const [snapWindowMs, setSnapWindowMs] = useState(60);
  const [overwrite, setOverwrite] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [resolvedPaths, setResolvedPaths] = useState<{
    fixture_path: string;
    anchor_path: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (
      !job ||
      job.status === "succeeded" ||
      job.status === "failed" ||
      job.status === "cancelled"
    )
      return;
    let stopped = false;
    const tick = async () => {
      try {
        const j = await api.getJob(job.id);
        if (stopped) return;
        setJob(j);
        if (j.status === "succeeded" && resolvedPaths) {
          setOpen(false);
          navigate(
            `/promote-review?fixture=${encodeURIComponent(resolvedPaths.fixture_path)}&anchor=${encodeURIComponent(resolvedPaths.anchor_path)}`,
          );
        }
      } catch (err) {
        if (!stopped) setError(String(err));
      }
    };
    const id = window.setInterval(tick, 1500);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [job, resolvedPaths, navigate]);

  const submit = useCallback(async () => {
    const anchor = fixtures.find((f) => f.slug === anchorSlug);
    if (!anchor) return;
    setSubmitting(true);
    setError(null);
    try {
      const resp = await api.promoteFromAnchor({
        anchor_path: anchor.audit_path,
        secondary_wav_path: secondaryWav,
        slug,
        camera_id: cameraId,
        mount,
        position,
        audio_source: audioSource,
        snap_window_ms: snapWindowMs,
        overwrite,
      });
      setJob(resp.job);
      setResolvedPaths({
        fixture_path: resp.fixture_path,
        anchor_path: resp.anchor_path,
      });
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  }, [
    anchorSlug,
    audioSource,
    cameraId,
    fixtures,
    mount,
    overwrite,
    position,
    secondaryWav,
    slug,
    snapWindowMs,
  ]);

  const running = job && (job.status === "pending" || job.status === "running");

  const fieldCls = "w-full rounded border border-rule bg-bg px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-led";

  const isSection = variant === "section";

  return (
    <div className={isSection ? "contents" : "relative"}>
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5"
        onClick={() => setOpen((v) => !v)}
        disabled={!!running}
      >
        {running ? <Loader2 className="size-3.5 animate-spin" /> : <Link2 className="size-3.5" />}
        Promote from anchor
      </Button>
      {open && (
        <div
          className={
            isSection
              ? "mt-3 w-full rounded-md border border-rule bg-surface p-5"
              : "absolute right-0 top-full z-20 mt-1 w-96 rounded-md border border-rule bg-surface-2 p-4 shadow-md"
          }
          style={isSection ? { boxShadow: "inset 0 1px 0 rgba(6,182,212,0.1)" } : undefined}
        >
          <div
            className={
              isSection
                ? "mb-3 flex items-center gap-2.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.18em] text-beep"
                : "text-xs font-semibold uppercase tracking-wide text-muted mb-3"
            }
          >
            {isSection && <span aria-hidden className="h-px w-6 bg-beep" />}
            Promote from anchor
          </div>
          <div className="flex flex-col gap-2.5">
            <div>
              <div className="text-xs text-muted mb-1">Anchor fixture</div>
              <select
                className={fieldCls}
                value={anchorSlug}
                onChange={(e) => setAnchorSlug(e.target.value)}
              >
                <option value="">Pick anchor...</option>
                {fixtures.map((f) => (
                  <option key={f.slug} value={f.slug}>
                    {f.slug} ({f.n_shots} shots)
                  </option>
                ))}
              </select>
            </div>
            <div>
              <div className="text-xs text-muted mb-1">Secondary WAV path (absolute)</div>
              <input
                className={fieldCls}
                placeholder="/path/to/secondary.wav"
                value={secondaryWav}
                onChange={(e) => setSecondaryWav(e.target.value)}
              />
            </div>
            <div>
              <div className="text-xs text-muted mb-1">Target fixture slug</div>
              <input
                className={fieldCls}
                placeholder="tallmilan-2026-stage5-phone"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-xs text-muted mb-1">Camera ID</div>
                <input
                  className={fieldCls}
                  placeholder="apple-iphone17pro"
                  value={cameraId}
                  onChange={(e) => setCameraId(e.target.value)}
                />
              </div>
              <div>
                <div className="text-xs text-muted mb-1">Snap window (ms)</div>
                <input
                  className={fieldCls}
                  type="number"
                  min={10}
                  max={500}
                  value={snapWindowMs}
                  onChange={(e) => setSnapWindowMs(Number(e.target.value))}
                />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <div className="text-xs text-muted mb-1">Mount</div>
                <select className={fieldCls} value={mount} onChange={(e) => setMount(e.target.value)}>
                  {["head","chest","belt","helmet","hand","tripod","monopod","gimbal"].map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
              <div>
                <div className="text-xs text-muted mb-1">Position</div>
                <select className={fieldCls} value={position} onChange={(e) => setPosition(e.target.value)}>
                  {["shooter","ro","squadmate","bay-fixed"].map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                </select>
              </div>
              <div>
                <div className="text-xs text-muted mb-1">Audio source</div>
                <select className={fieldCls} value={audioSource} onChange={(e) => setAudioSource(e.target.value)}>
                  {["internal","lav-wired","lav-wireless","shotgun-hotshoe"].map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
            </div>
            <label className="flex items-center gap-2 text-xs cursor-pointer text-muted">
              <input
                type="checkbox"
                checked={overwrite}
                onChange={(e) => setOverwrite(e.target.checked)}
              />
              Overwrite if slug exists
            </label>
            {running && (
              <div className="flex items-center gap-2 text-xs text-muted">
                <Loader2 className="size-3.5 animate-spin" />
                {job?.message ?? "running..."}
                {job?.progress != null && (
                  <span className="ml-auto font-mono">{Math.round(job.progress * 100)}%</span>
                )}
              </div>
            )}
            {error && (
              <div className="text-xs text-destructive flex gap-1.5">
                <AlertCircle className="size-3.5 mt-0.5 shrink-0" />
                {error}
              </div>
            )}
            <div className="flex gap-2 pt-1">
              <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={submit}
                disabled={submitting || !!running || !anchorSlug || !secondaryWav || !slug || !cameraId}
              >
                {submitting ? <Loader2 className="size-3.5 animate-spin mr-1" /> : null}
                Promote
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
