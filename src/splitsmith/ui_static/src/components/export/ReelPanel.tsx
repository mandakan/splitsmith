/**
 * Export's "Highlight reel" mode (spec 2026-10-18-highlight-reel-design,
 * part 2): what stands in the way, the moment menu by group, the cut and
 * the format, and the one primary action. Every rule is in
 * `lib/highlightReel`; this maps it to primitives and runs the job.
 */
import { Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import { Segmented } from "@/components/ui/Segmented";
import { type HighlightMenuItem, type HighlightReadiness, type Job, api, apiErrorText } from "@/lib/api";
import {
  type ReelAfter,
  type ReelCut,
  type ReelFormat,
  blockerFix,
  blockingStages,
  defaultSelection,
  groupMenu,
  readinessLine,
  reelBody,
  renderBlock,
} from "@/lib/highlightReel";

const GROUP_TITLES = { default: "Highlights", stat: "More stats", lolz: "Just for fun" } as const;

export function ReelPanel({
  slug,
  href,
  youtubeConnected,
  onFinished,
}: {
  slug: string;
  href: (...segments: string[]) => string;
  /** YouTube is connected: offer the upload after the render. */
  youtubeConnected: boolean;
  /** A render finished: the page re-reads its export history. */
  onFinished: () => void;
}) {
  const [readiness, setReadiness] = useState<HighlightReadiness | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [cut, setCut] = useState<ReelCut>("normal");
  const [format, setFormat] = useState<ReelFormat>("portrait");
  const [force, setForce] = useState(false);
  const [after, setAfter] = useState<ReelAfter>("keep");
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api
      .getHighlights(slug)
      .then((r) => {
        if (!live) return;
        setReadiness(r);
        setChosen(defaultSelection(r.menu));
      })
      .catch((e) => {
        if (live) setLoadError(apiErrorText(e, "Could not read the stages."));
      });
    return () => {
      live = false;
    };
  }, [slug]);

  const groups = useMemo(() => (readiness ? groupMenu(readiness.menu) : null), [readiness]);
  if (loadError) return <p className="px-3.5 py-3 text-md text-muted">{loadError}</p>;
  if (readiness == null || groups == null) return <p className="px-3.5 py-3 text-md text-muted">Reading stages...</p>;

  const blockers = blockingStages(readiness);
  const block = renderBlock(readiness, chosen, force);
  const running = job != null && (job.status === "pending" || job.status === "running");

  function toggle(item: HighlightMenuItem) {
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(item.kind)) next.delete(item.kind);
      else next.add(item.kind);
      return next;
    });
  }

  async function render() {
    if (readiness == null) return;
    setError(null);
    try {
      const submitted = await api.submitHighlightReel(
        slug,
        reelBody(readiness.menu, chosen, { cut, format, force, after: youtubeConnected ? after : "keep" }),
      );
      setJob(submitted);
      const final = await api.pollJob(submitted.id, setJob);
      setJob(final);
      if (final.status === "failed") setError(final.error ?? "The reel could not be rendered.");
      else onFinished();
    } catch (e) {
      setError(apiErrorText(e, "The reel could not be queued."));
    }
  }

  const result = job?.status === "succeeded" ? (job.result as ReelResult | null) : null;
  return (
    <div className="flex flex-col gap-4 px-3.5 py-3">
      <div>
        <p className="text-md text-ink">{readinessLine(readiness)}</p>
        {blockers.length > 0 ? (
          <ul className="mt-1 space-y-0.5 text-sm">
            {blockers.map((s) => {
              const fix = blockerFix(s, slug, href);
              return (
                <li key={s.stage_number}>
                  <Link to={fix.to} className="text-muted underline-offset-2 hover:text-ink hover:underline">
                    {fix.text}
                  </Link>
                </li>
              );
            })}
          </ul>
        ) : null}
        {!readiness.unlocked && readiness.ready > 0 ? (
          <div className="mt-2">
            <Segmented<"wait" | "force">
              label="Unfinished stages"
              value={force ? "force" : "wait"}
              onChange={(v) => setForce(v === "force")}
              options={[
                { value: "wait", label: "Wait for every stage" },
                { value: "force", label: "Render the ready ones" },
              ]}
            />
          </div>
        ) : null}
      </div>

      {(["default", "stat", "lolz"] as const).map((group) =>
        groups[group].length > 0 ? (
          <div key={group}>
            <Label>{GROUP_TITLES[group]}</Label>
            <div className="mt-1.5 flex flex-wrap gap-1.5" role="group" aria-label={GROUP_TITLES[group]}>
              {groups[group].map((item) => {
                const on = chosen.has(item.kind);
                return (
                  <button key={item.kind} type="button" onClick={() => toggle(item)} aria-pressed={on}>
                    <Chip tone={on ? "ok" : "neutral"}>{titleCase(item.label)}</Chip>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null,
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Segmented<ReelCut>
          label="Cut"
          value={cut}
          onChange={setCut}
          options={[
            { value: "normal", label: "Full trailer" },
            { value: "short", label: "Shorts" },
          ]}
        />
        <Segmented<ReelFormat>
          label="Format"
          value={format}
          onChange={setFormat}
          options={[
            { value: "portrait", label: "Portrait" },
            { value: "landscape", label: "Landscape" },
          ]}
        />
        {youtubeConnected ? (
          <Segmented<ReelAfter>
            label="After render"
            value={after}
            onChange={setAfter}
            options={[
              { value: "keep", label: "Keep the file" },
              { value: "unlisted", label: "Upload unlisted" },
              { value: "private", label: "Upload private" },
              { value: "public", label: "Upload public" },
            ]}
          />
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" disabled={block != null || running} onClick={() => void render()}>
          {running ? <Loader2 className="size-3.5 animate-spin" aria-hidden="true" /> : null}
          Render reel
        </Button>
        {block ? <span className="text-sm text-muted">{block}</span> : null}
        {running && job?.message ? (
          <span className="text-sm text-muted" aria-live="polite">
            {job.message}
          </span>
        ) : null}
      </div>
      {error ? (
        <p role="alert" className="rounded-md border border-destructive/45 px-3 py-2 text-md text-destructive">
          {error}
        </p>
      ) : null}
      {result ? (
        <p role="status" className="text-md text-ink">
          Wrote {result.filename} (<span className="numeral">{result.duration_seconds.toFixed(1)}</span> s)
          {result.left_out.length > 0 ? ` without stage ${result.left_out.join(", ")}` : ""}. It is in the export
          history below the page, ready to upload.
        </p>
      ) : null}
    </div>
  );
}

interface ReelResult {
  filename: string;
  duration_seconds: number;
  degradations: string[];
  left_out: number[];
}

/** "FASTEST DRAW" -> "Fastest draw": the cards shout, the menu does not. */
function titleCase(label: string): string {
  const lower = label.toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}
