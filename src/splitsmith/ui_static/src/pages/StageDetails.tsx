/**
 * Stage details (spec 2026-10-22, mockup B): one shooter's stage times,
 * scores and shooting order, typed or pasted from Shoot'n Score It. Every
 * value is a plain field; points and hit factor are worked out (dashed)
 * where nothing was entered. Save sends the whole page
 * (``PUT /api/shooters/{slug}/stage-details``); the derivations live in
 * ``lib/stageDetails.ts``.
 */
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useOutletContext, useParams } from "react-router-dom";
import { GripVertical, Loader2 } from "lucide-react";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { Button } from "@/components/ui/button";
import { Table, Td, Th, Tr } from "@/components/ui/DataTable";
import { Label } from "@/components/ui/Label";
import { PageHeader } from "@/components/ui/PageHeader";
import { Segmented } from "@/components/ui/Segmented";
import { ApiError, api, capabilityDenied, type StageDetailsView } from "@/lib/api";
import { useMatchHref } from "@/lib/matchHref";
import {
  applyPaste,
  copyOrder,
  disagreement,
  duplicateOrders,
  firstName,
  HIT_FIELDS,
  moveInOrder,
  parsePastedResults,
  rowsFromView,
  shownScores,
  timedCount,
  toRequest,
  withCells,
  type CellField,
  type EditRow,
  type PowerFactor,
} from "@/lib/stageDetails";
import { cn } from "@/lib/utils";

const CELL =
  "w-14 rounded-md border border-rule-strong bg-surface-2 px-1.5 py-1 text-right font-mono text-sm text-ink disabled:opacity-60";

const HIT_HEAD: Record<(typeof HIT_FIELDS)[number], string> = {
  alphas: "A",
  charlies: "C",
  deltas: "D",
  misses: "M",
  no_shoots: "NS",
  procedurals: "Proc",
};

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function StageDetails() {
  const { slug } = useParams<{ slug: string }>();
  const ctx = useOutletContext<MatchShellOutletContext | undefined>();
  const navigate = useNavigate();
  const href = useMatchHref();
  const readOnly = capabilityDenied(ctx?.capabilities, "edit");

  const [view, setView] = useState<StageDetailsView | null>(null);
  const [rows, setRows] = useState<EditRow[]>([]);
  const [pf, setPf] = useState<PowerFactor | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [pasteNote, setPasteNote] = useState<string | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);

  const load = useCallback(
    (next: StageDetailsView) => {
      setView(next);
      setRows(rowsFromView(next));
      setPf(next.power_factor_set ? (next.power_factor as PowerFactor) : null);
    },
    [],
  );

  useEffect(() => {
    if (!slug) return;
    let alive = true;
    setView(null);
    setError(null);
    setPasteNote(null);
    api
      .getStageDetails(slug)
      .then((v) => alive && load(v))
      .catch((e: unknown) => alive && setError(e instanceof ApiError ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [slug, load]);

  const effectivePf: PowerFactor = pf ?? ((view?.power_factor as PowerFactor | undefined) ?? "minor");
  const divisionPf: PowerFactor = /major/i.test(view?.division ?? "") ? "major" : "minor";
  const dups = useMemo(() => duplicateOrders(rows), [rows]);
  const dirty = useMemo(
    () => view !== null && JSON.stringify(toRequest(rows, pf)) !== JSON.stringify(toRequest(rowsFromView(view), view.power_factor_set ? (view.power_factor as PowerFactor) : null)),
    [rows, pf, view],
  );

  if (error && !view) {
    return (
      <div className="flex flex-col gap-4 p-4 md:p-6">
        <PageHeader title="Stage details" />
        <p className="text-md text-destructive">{error}</p>
      </div>
    );
  }
  if (!view || !slug) {
    return (
      <div className="flex items-center gap-2 p-6 text-md text-muted">
        <Loader2 className="size-4 animate-spin" /> Loading stage details
      </div>
    );
  }

  const name = firstName(view.name);
  const setCell = (stage: number, field: CellField, value: string) =>
    setRows((rs) => rs.map((r) => (r.stage_number === stage ? withCells(r, { [field]: value }) : r)));

  const onPaste = (text: string) => {
    const out = parsePastedResults(
      text,
      rows.map((r) => r.stage_number),
    );
    if (!out.understood) {
      setPasteNote("No header row with a Station or Stage column. Copy the table from its header down.");
      return;
    }
    setRows((rs) => applyPaste(rs, out.rows));
    const placed = `${out.rows.length} ${out.rows.length === 1 ? "row" : "rows"} read`;
    setPasteNote(
      out.unused.length === 0
        ? `${placed}, all placed. Check them, then save.`
        : `${placed}. Not used: ${out.unused.join(", ")}.`,
    );
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      load(await api.putStageDetails(slug, toRequest(rows, pf)));
      setPasteNote(null);
      ctx?.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const shooters = ctx?.shooters ?? [];

  return (
    <div className="flex flex-col gap-5 p-4 md:p-6">
      <PageHeader
        title="Stage details"
        sub={[view.name, view.division].filter(Boolean).join(", ")}
        actions={
          shooters.length > 1 ? (
            <select
              aria-label="Shooter"
              className="rounded-md border border-rule-strong bg-surface-2 px-2.5 py-1.5 text-md text-ink"
              value={slug}
              onChange={(e) => navigate(href("stage-details", e.target.value))}
            >
              {shooters.map((s) => (
                <option key={s.slug} value={s.slug}>
                  {s.name}
                </option>
              ))}
            </select>
          ) : null
        }
      />
      {readOnly ? (
        <p className="text-md text-muted">This match is edited on the desktop. Stage details are read only here.</p>
      ) : null}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <Label>Stages</Label>
            <span className="text-sm text-muted">
              <span className="numeral">{timedCount(rows)}</span> of{" "}
              <span className="numeral">{rows.filter((r) => !r.skipped).length}</span> have a time
            </span>
            <span className="flex-1" />
            <Label>Power factor</Label>
            <Segmented
              label="Power factor"
              value={effectivePf}
              options={[
                { value: "minor", label: "Minor" },
                { value: "major", label: "Major" },
              ]}
              onChange={(v) => setPf(v === divisionPf ? null : v)}
              disabled={readOnly}
            />
            {view.others.map((o) => (
              <Button
                key={o.slug}
                size="sm"
                disabled={readOnly}
                onClick={() => setRows((rs) => copyOrder(rs, o.order))}
                title={o.from_scorecards ? `From ${firstName(o.name)}'s scorecard times` : undefined}
              >
                Copy order from {firstName(o.name)}
              </Button>
            ))}
          </div>
          <Table aria-label="Stage details">
            <thead>
              <tr>
                <Th>Order</Th>
                <Th>Stage</Th>
                <Th align="right">Time</Th>
                {HIT_FIELDS.map((f) => (
                  <Th key={f} align="right">
                    {HIT_HEAD[f]}
                  </Th>
                ))}
                <Th align="right">Points</Th>
                <Th align="right">HF</Th>
                <Th align="right">%</Th>
                <Th>DQ</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const { points, hf } = shownScores(r, effectivePf);
                const warn = disagreement(r, effectivePf);
                const cell = (field: CellField, label: string, wide = false) => (
                  <input
                    aria-label={`${label}, stage ${pad2(r.stage_number)}`}
                    inputMode="decimal"
                    disabled={readOnly}
                    className={cn(CELL, wide && "w-20", r.pasted.has(field) && "bg-surface-3")}
                    value={r.cells[field]}
                    onChange={(e) => setCell(r.stage_number, field, e.target.value)}
                  />
                );
                const shown = (field: "stage_points" | "hit_factor", s: { value: string; derived: boolean }, label: string) => (
                  <input
                    aria-label={`${label}, stage ${pad2(r.stage_number)}`}
                    inputMode="decimal"
                    disabled={readOnly}
                    className={cn(
                      CELL,
                      "w-20",
                      s.derived && "border-dashed bg-transparent text-ink-2",
                      r.pasted.has(field) && "bg-surface-3",
                      field === "stage_points" && warn && "border-status-warning",
                    )}
                    value={s.value}
                    onChange={(e) => setCell(r.stage_number, field, e.target.value)}
                  />
                );
                return (
                  <Fragment key={r.stage_number}>
                    <Tr
                      className={cn(r.skipped && "opacity-50", dragging === r.stage_number && "opacity-40")}
                      onDragOver={(e) => dragging !== null && e.preventDefault()}
                      onDrop={() => {
                        if (dragging !== null && dragging !== r.stage_number)
                          setRows((rs) => moveInOrder(rs, dragging, r.stage_number));
                        setDragging(null);
                      }}
                    >
                      <Td>
                        <span className="flex items-center gap-1">
                          <span
                            draggable={!readOnly}
                            onDragStart={() => setDragging(r.stage_number)}
                            onDragEnd={() => setDragging(null)}
                            className="cursor-grab text-subtle"
                            aria-hidden
                          >
                            <GripVertical className="size-4" />
                          </span>
                          <input
                            aria-label={`Order, stage ${pad2(r.stage_number)}`}
                            inputMode="numeric"
                            disabled={readOnly}
                            placeholder={orderHint(view, r.stage_number)}
                            className={cn(CELL, "w-10 text-center", dups.has(r.stage_number) && "border-status-warning")}
                            value={r.cells.shot_order}
                            onChange={(e) => setCell(r.stage_number, "shot_order", e.target.value)}
                          />
                        </span>
                      </Td>
                      <Td kind="name" className="whitespace-nowrap">
                        <span className="numeral mr-2 text-muted">{pad2(r.stage_number)}</span>
                        {r.stage_name}
                      </Td>
                      <Td kind="num">{cell("time_seconds", "Time", true)}</Td>
                      {HIT_FIELDS.map((f) => (
                        <Td key={f} kind="num">
                          {cell(f, HIT_HEAD[f])}
                        </Td>
                      ))}
                      <Td kind="num">{shown("stage_points", points, "Points")}</Td>
                      <Td kind="num">{shown("hit_factor", hf, "Hit factor")}</Td>
                      <Td kind="num">{cell("stage_pct", "Stage percent")}</Td>
                      <Td>
                        <input
                          type="checkbox"
                          aria-label={`DQ, stage ${pad2(r.stage_number)}`}
                          disabled={readOnly}
                          checked={r.dq === true}
                          onChange={(e) =>
                            setRows((rs) =>
                              rs.map((x) =>
                                x.stage_number === r.stage_number
                                  ? { ...x, dq: e.target.checked, manual: new Set([...x.manual, "dq"]) }
                                  : x,
                              ),
                            )
                          }
                        />
                      </Td>
                    </Tr>
                    {warn ? (
                      <tr>
                        <td colSpan={14} className="px-3 pb-2 text-sm text-status-warning">
                          Stage {pad2(r.stage_number)}: {warn}
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </Table>
          <div className="flex flex-wrap items-center gap-3">
            {dups.size > 0 ? (
              <span className="text-sm text-status-warning">Two stages have the same place in the order.</span>
            ) : null}
            {error ? <span className="text-sm text-destructive">{error}</span> : null}
            <span className="flex-1" />
            <Button variant="ghost" disabled={!dirty || saving} onClick={() => load(view)}>
              Discard
            </Button>
            <Button
              variant="primary"
              disabled={readOnly || !dirty || saving || dups.size > 0}
              onClick={() => void save()}
            >
              {saving ? "Saving" : "Save"}
            </Button>
          </div>
        </div>
        <aside className="flex h-fit flex-col gap-3 rounded-[10px] border border-rule bg-surface p-4">
          <Label>Paste results</Label>
          <ol className="list-decimal space-y-1 pl-5 text-md text-ink-2">
            <li>
              Open{" "}
              {view.ssi_url ? (
                <a className="text-ink underline decoration-rule-strong" href={view.ssi_url} target="_blank" rel="noreferrer">
                  {name} on Shoot'n Score It
                </a>
              ) : (
                <span>{name}'s results on Shoot'n Score It</span>
              )}
              .
            </li>
            <li>Select the table from "Station" down to the last stage and copy it.</li>
            <li>Paste it below. The rows fill in by stage number for you to check before saving.</li>
          </ol>
          <textarea
            aria-label="Paste results here"
            disabled={readOnly}
            placeholder="Paste here"
            rows={4}
            className="w-full resize-none rounded-md border border-dashed border-rule-strong bg-transparent px-3 py-2 text-sm text-ink placeholder:text-subtle"
            value=""
            onPaste={(e) => {
              e.preventDefault();
              onPaste(e.clipboardData.getData("text/plain"));
            }}
            onChange={(e) => onPaste(e.target.value)}
          />
          {pasteNote ? <p className="text-sm text-ink-2">{pasteNote}</p> : null}
          <p className="text-sm text-muted">
            From paper or a photo, type straight into the rows. Any part is fine: a time alone is enough to trim.
          </p>
        </aside>
      </div>
    </div>
  );
}

/** The order the scorecards imply, as a greyed hint in an empty cell. */
function orderHint(view: StageDetailsView, stage: number): string {
  const at = view.scorecard_order.indexOf(stage);
  return at < 0 ? "" : String(at + 1);
}
