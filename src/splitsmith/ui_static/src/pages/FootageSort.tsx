/**
 * Sort footage (/match/:matchId/footage-sort/:scanId, spec 2026-10-01):
 * the review of one shared folder sorted across the match's shooters.
 * Cameras whose clock is unknown come first (one named clip sets the
 * clock), then the clips the engine would not decide, then one table per
 * shooter with the confident proposals pre-checked; leftovers and clips
 * already imported fold away. Every decision goes to the server, which
 * re-runs the engine and answers with the new view; ``lib/footageSort``
 * groups and words it. Local mode only, reached from Footage.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/Chip";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import { Segmented } from "@/components/ui/Segmented";
import { Sheet } from "@/components/ui/Sheet";
import { Table, Td, Th, Tr } from "@/components/ui/DataTable";
import { ApiError, api, type SortClipView, type SortDecisions, type SortImportResult, type SortView } from "@/lib/api";
import {
  assignClip,
  cameraLabel,
  clockText,
  importCount,
  reasonText,
  resetClip,
  setChecked,
  setsClock,
  shooterName,
  skipClip,
  sortSections,
  stageLabel,
  whereNow,
} from "@/lib/footageSort";
import { matchHref } from "@/lib/matchHref";

const SELECT = "min-w-0 rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 text-md text-ink disabled:opacity-50";
const POLL_MS = 1000;

export function FootageSort() {
  const { matchId, scanId = "" } = useParams<{ matchId: string; scanId: string }>();
  const [view, setView] = useState<SortView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<SortClipView | null>(null);
  const [linkMode, setLinkMode] = useState<"symlink" | "copy">("symlink");
  const [result, setResult] = useState<SortImportResult | null>(null);

  const load = useCallback(async () => {
    try {
      setView(await api.getFootageSort(scanId));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, [scanId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (view?.status !== "scanning") return;
    const timer = setTimeout(() => void load(), POLL_MS);
    return () => clearTimeout(timer);
  }, [view, load]);

  async function decide(decisions: SortDecisions) {
    setBusy(true);
    setError(null);
    try {
      const next = await api.putFootageSortDecisions(scanId, decisions);
      setView(next);
      setOpen((o) => (o ? (next.clips.find((c) => c.clip_id === o.clip_id) ?? null) : null));
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function runImport() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.importFootageSort(scanId, linkMode));
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  }

  const footageHref = matchHref(matchId, "ingest");
  const count = view ? importCount(view) : 0;
  const ready = view?.status === "ready";
  const sub = view
    ? `${view.source_dir} · ${view.clips.length} videos${view.skipped_files ? ` · ${view.skipped_files} other files skipped` : ""}`
    : undefined;

  return (
    <div className="px-4 py-4 md:px-7 md:py-5">
      <PageHeader
        title="Sort footage"
        sub={sub}
        back={{ label: "Footage", to: footageHref }}
        actions={
          ready ? (
            <>
              <button
                type="button"
                onClick={() => setLinkMode((m) => (m === "symlink" ? "copy" : "symlink"))}
                aria-pressed={linkMode === "copy"}
                title="Link videos in place, or copy them into the match folder"
              >
                <Chip tick="muted">{linkMode === "symlink" ? "Link in place" : "Copy files"}</Chip>
              </button>
              <Button variant="primary" onClick={() => void runImport()} disabled={busy || count === 0}>
                Import <span className="numeral">{count}</span> {count === 1 ? "clip" : "clips"}
              </Button>
            </>
          ) : null
        }
      />

      {error ? <p className="mt-3 rounded-md border border-destructive px-3 py-2 text-sm text-destructive">{error}</p> : null}

      {view === null ? null : view.status === "scanning" ? (
        <p className="mt-4 text-md text-muted">Reading the videos...</p>
      ) : view.status === "failed" ? (
        <p className="mt-4 text-md text-destructive">The scan failed: {view.error}</p>
      ) : view.status === "imported" ? (
        <Imported view={view} result={result} footageHref={footageHref} />
      ) : (
        <Review view={view} busy={busy} onOpen={setOpen} onDecide={(d) => void decide(d)} />
      )}

      {open && view ? (
        <AssignSheet
          key={open.clip_id}
          view={view}
          clip={open}
          busy={busy}
          onClose={() => setOpen(null)}
          onDecide={(d) => void decide(d)}
        />
      ) : null}
    </div>
  );
}

function Review({
  view,
  busy,
  onOpen,
  onDecide,
}: {
  view: SortView;
  busy: boolean;
  onOpen: (clip: SortClipView) => void;
  onDecide: (d: SortDecisions) => void;
}) {
  const s = sortSections(view);
  const rowProps = { view, busy, onOpen, onDecide };
  return (
    <div className="mt-5 flex flex-col gap-6">
      {s.anchorCameras.length > 0 || s.needsYou.length > 0 ? (
        <section className="flex flex-col gap-2">
          <Label>Needs you</Label>
          {s.anchorCameras.map(({ camera, clips }) => (
            <div key={camera.key} className="flex flex-col gap-2">
              <p className="text-md text-ink-2">
                {cameraLabel(camera)}: the clock lines up with no scorecard. Open one clip and name its run;
                the rest of this camera follows.
              </p>
              <ClipTable clips={clips} {...rowProps} />
            </div>
          ))}
          {s.needsYou.length > 0 ? <ClipTable clips={s.needsYou} {...rowProps} /> : null}
        </section>
      ) : null}

      {s.byShooter.map((group) => (
        <section key={group.key} className="flex flex-col gap-2">
          <Label>{group.name}</Label>
          <ClipTable clips={group.clips} {...rowProps} checkable />
        </section>
      ))}

      {s.skipped.length > 0 ? (
        <details className="flex flex-col gap-2">
          <summary className="cursor-pointer text-sm text-muted">
            <span className="numeral">{s.skipped.length}</span> skipped: no squad run follows them
          </summary>
          <div className="mt-2">
            <ClipTable clips={s.skipped} {...rowProps} />
          </div>
        </details>
      ) : null}

      {s.imported.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-sm text-muted">
            <span className="numeral">{s.imported.length}</span> already imported
          </summary>
          <ul className="mt-2 flex flex-col gap-1 text-sm text-muted">
            {s.imported.map((c) => (
              <li key={c.clip_id} className="font-mono">
                {c.clip_id} · {shooterName(view, c.imported_by)}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function ClipTable({
  view,
  clips,
  busy,
  checkable = false,
  onOpen,
  onDecide,
}: {
  view: SortView;
  clips: SortClipView[];
  busy: boolean;
  checkable?: boolean;
  onOpen: (clip: SortClipView) => void;
  onDecide: (d: SortDecisions) => void;
}) {
  return (
    <Table>
      <thead>
        <tr>
          {checkable ? <Th className="w-8" aria-label="Import" /> : null}
          <Th className="w-20">Clip</Th>
          <Th>Stage</Th>
          <Th>File</Th>
          <Th>Why</Th>
          <Th aria-label="Actions" />
        </tr>
      </thead>
      <tbody>
        {clips.map((clip) => {
          const camera = view.cameras.find((c) => c.key === clip.proposal.camera_key);
          const clock = camera ? clockText(camera) : null;
          return (
            <Tr key={clip.clip_id}>
              {checkable ? (
                <Td>
                  <input
                    type="checkbox"
                    aria-label={`Import ${clip.filename}`}
                    checked={clip.checked}
                    disabled={busy}
                    onChange={(e) => onDecide(setChecked(view, clip.clip_id, e.target.checked))}
                  />
                </Td>
              ) : null}
              <Td>
                {clip.thumbnail ? (
                  <img
                    src={api.footageSortThumbUrl(view.scan_id, clip.index)}
                    alt=""
                    loading="lazy"
                    className="h-9 w-16 rounded bg-surface-2 object-cover"
                  />
                ) : (
                  <span className="block h-9 w-16 rounded bg-surface-2" />
                )}
              </Td>
              <Td kind="name">
                {clip.proposal.stage !== null && clip.proposal.confidence !== "skipped"
                  ? `${checkable ? "" : `${shooterName(view, clip.proposal.shooter)} · `}${stageLabel(clip.proposal.stage)}`
                  : "None"}
              </Td>
              <Td>
                <div className="font-mono text-ink">{clip.filename}</div>
                <div className="flex flex-wrap items-center gap-1.5 text-sm text-muted">
                  {camera ? cameraLabel(camera) : null}
                  {clock ? <Chip tick="muted">{clock}</Chip> : null}
                  {whereNow(view, clip) ? <Chip tick="muted">{whereNow(view, clip)}</Chip> : null}
                  {clip.proposal.role === "primary" && clip.proposal.reason.run_size > 1 ? (
                    <Chip tick="draw">primary</Chip>
                  ) : null}
                </div>
              </Td>
              <Td className="text-sm text-muted">
                {reasonText(view, clip)}
                {clip.proposal.confidence === "medium" && clip.proposal.decided_by === "engine" ? " · check it" : ""}
              </Td>
              <Td className="text-right">
                <Button size="sm" onClick={() => onOpen(clip)} disabled={busy}>
                  {clip.proposal.confidence === "needs_you" ? "Choose" : "Change"}
                </Button>
              </Td>
            </Tr>
          );
        })}
      </tbody>
    </Table>
  );
}

function AssignSheet({
  view,
  clip,
  busy,
  onClose,
  onDecide,
}: {
  view: SortView;
  clip: SortClipView;
  busy: boolean;
  onClose: () => void;
  onDecide: (d: SortDecisions) => void;
}) {
  const [shooter, setShooter] = useState(clip.proposal.shooter ?? view.shooters[0]?.key ?? "");
  const stages = view.shooters.find((s) => s.key === shooter)?.stages ?? [];
  const [stage, setStage] = useState<number | null>(clip.proposal.stage);
  const camera = view.cameras.find((c) => c.key === clip.proposal.camera_key);
  const clock = camera ? clockText(camera) : null;
  const anchoring = setsClock(view, clip.clip_id);

  return (
    <Sheet open onClose={onClose} label={clip.filename}>
      <div className="flex items-center gap-3 border-b border-rule px-4 py-3">
        <span className="min-w-0 flex-1 truncate font-mono text-md text-ink" title={clip.clip_id}>
          {clip.filename}
        </span>
        <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close">
          &#10005;
        </Button>
      </div>
      <div className="flex flex-col gap-4 overflow-y-auto px-4 py-4">
        <div className="overflow-hidden rounded-[10px] bg-black">
          <video
            key={clip.clip_id}
            controls
            preload="metadata"
            src={api.footageSortVideoUrl(view.scan_id, clip.index)}
            className="aspect-video w-full object-contain"
          />
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
          {camera ? <span>{cameraLabel(camera)}</span> : null}
          {clock ? <Chip tick="muted">{clock}</Chip> : null}
          {clip.start ? (
            <span className="numeral">
              {new Date(clip.start).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
            </span>
          ) : null}
        </div>
        <p className="text-sm text-ink-2">{reasonText(view, clip)}</p>
        <div className="flex flex-col gap-2">
          <Label>Shooter</Label>
          <Segmented
            label="Shooter"
            value={shooter}
            options={view.shooters.map((s) => ({ value: s.key, label: s.name }))}
            onChange={(key) => {
              setShooter(key);
              setStage(null);
            }}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Label>Stage</Label>
          <select
            aria-label="Stage"
            className={SELECT}
            value={stage === null ? "" : String(stage)}
            onChange={(e) => setStage(e.target.value === "" ? null : Number(e.target.value))}
          >
            <option value="">Choose a stage</option>
            {stages.map((n) => (
              <option key={n} value={n}>
                {stageLabel(n)}
              </option>
            ))}
          </select>
        </div>
        {anchoring ? (
          <p className="text-sm text-muted">This also sets the clock for every clip from this camera.</p>
        ) : null}
      </div>
      <div className="mt-auto flex items-center gap-2 border-t border-rule px-4 py-3">
        <Button
          size="sm"
          onClick={() => {
            if (stage === null) return;
            onDecide(assignClip(view, clip.clip_id, shooter, stage));
            onClose();
          }}
          disabled={busy || stage === null}
        >
          Use this
        </Button>
        <Button size="sm" onClick={() => onDecide(skipClip(view, clip.clip_id))} disabled={busy}>
          Skip clip
        </Button>
        <span className="flex-1" />
        {clip.proposal.decided_by === "user" ? (
          <Button size="sm" variant="ghost" onClick={() => onDecide(resetClip(view, clip.clip_id))} disabled={busy}>
            Undo my choice
          </Button>
        ) : null}
      </div>
    </Sheet>
  );
}

function Imported({
  view,
  result,
  footageHref,
}: {
  view: SortView;
  result: SortImportResult | null;
  footageHref: string;
}) {
  return (
    <div className="mt-5 flex flex-col gap-3">
      {result ? (
        <>
          <p className="text-md text-ink">
            Imported <span className="numeral">{result.imported.length}</span>{" "}
            {result.imported.length === 1 ? "clip" : "clips"}
            {" · "}
            {view.shooters
              .map((s) => [s.name, result.imported.filter((i) => i.shooter === s.key).length] as const)
              .filter(([, n]) => n > 0)
              .map(([name, n]) => `${name} ${n}`)
              .join(", ")}
          </p>
          <p className="font-mono text-sm text-muted">{result.report}</p>
        </>
      ) : (
        <p className="text-md text-muted">This folder was imported.</p>
      )}
      <div>
        <Button asChild>
          <Link to={footageHref}>Back to Footage</Link>
        </Button>
      </div>
    </div>
  );
}
