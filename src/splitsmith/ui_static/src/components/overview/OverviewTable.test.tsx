import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { ShooterListEntry, TriageCell, TriageResponse } from "@/lib/api";
import { buildOverviewRows, nextAction } from "@/lib/overview";

import { OverviewTable } from "./OverviewTable";

function cell(over: Partial<TriageCell>): TriageCell {
  return {
    slug: "s1",
    shooter_name: "Mathias Axell",
    stage_number: 1,
    stage_name: "Stage",
    status: "todo",
    beep_confidence: null,
    anomalies: [],
    needs_attention: null,
    video_count: 0,
    beep_time: null,
    beep_reviewed: false,
    shot_count: 0,
    draw: null,
    avg_split: null,
    time_seconds: 0,
    ...over,
  };
}
function shooter(slug: string, name: string): ShooterListEntry {
  return { slug, name, selected_shooter_id: null, selected_competitor_id: null, stages_audited: 0, stages_total: 4, video_count: 1, cameras: [], stages_missing_trim: 0, stage_statuses: [] };
}
const TRIAGE: TriageResponse = {
  beep_low_confidence_threshold: 0.5,
  flagged_count: 0,
  cells: [
    cell({ stage_number: 1, stage_name: "B100 Höger", time_seconds: 48.6 }),
    cell({ stage_number: 3, stage_name: "B6 Rear", status: "audited", video_count: 1, beep_time: 5.32, beep_reviewed: true, beep_confidence: 0.91, shot_count: 30, draw: 1.97, avg_split: 0.386, time_seconds: 32.12 }),
    cell({ stage_number: 6, stage_name: "B5 All", status: "in_progress", video_count: 1, beep_time: 6.01, beep_reviewed: true, beep_confidence: 0.88, shot_count: 33, anomalies: [{}, {}, {}, {}] as never, draw: 1.93, avg_split: 0.44, time_seconds: 38.2 }),
    cell({ stage_number: 10, stage_name: "B3", status: "ready", video_count: 1, beep_time: 4.9, beep_reviewed: false, beep_confidence: 0.42, time_seconds: 24.0 }),
  ],
};
const HREFS = {
  audit: (slug: string, stage: number) => `/audit/${slug}/${stage}`,
  splits: (slug: string, stage: number) => `/results/${slug}/${stage}`,
  footage: (slug: string) => `/ingest/${slug}`,
  beep: (_slug: string, stage: number) => `/beep-review?stage=${stage}`,
};

function renderTable(over: Partial<React.ComponentProps<typeof OverviewTable>> = {}, shooters = [shooter("s1", "Mathias Axell")], triage = TRIAGE) {
  const rows = buildOverviewRows({ triage, shooters, leadSlug: "s1", jobs: [] });
  const next = nextAction(rows);
  const onAccept = vi.fn();
  render(
    <MemoryRouter>
      <OverviewTable rows={rows} multi={shooters.length > 1} filterSlug={null} currentStage={next?.row.stageNumber ?? null} threshold={0.5} editDenied={false} hrefs={HREFS} onAccept={onAccept} {...over} />
    </MemoryRouter>,
  );
  return { rows, onAccept };
}

describe("OverviewTable", () => {
  it("renders one row per stage with the loop's action", () => {
    renderTable();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(4);
    expect(within(rows[0]).getByRole("link", { name: /add footage/i })).toHaveAttribute("href", "/ingest/s1");
    expect(within(rows[1]).getByRole("link", { name: /splits/i })).toHaveAttribute("href", "/results/s1/3");
    expect(within(rows[3]).getByRole("link", { name: /confirm beep/i })).toHaveAttribute("href", "/beep-review?stage=10");
  });

  it("the next-action row is current, its Audit is primary and Accept is a ghost", async () => {
    const { onAccept } = renderTable();
    const six = screen.getAllByRole("row")[3];
    expect(six.className).toMatch(/bg-surface-2/);
    expect(within(six).getByRole("link", { name: /^audit$/i }).className).toMatch(/btn-primary/);
    await userEvent.click(within(six).getByRole("button", { name: /accept/i }));
    expect(onAccept).toHaveBeenCalledWith("s1", 6);
    // stage 10's Audit-class action is Confirm beep; it is not primary
    expect(within(screen.getAllByRole("row")[4]).getByRole("link", { name: /confirm beep/i }).className).not.toMatch(/btn-primary/);
  });

  it("hides Accept when edit is denied; flags and confidence show as warn chips", () => {
    renderTable({ editDenied: true });
    expect(screen.queryByRole("button", { name: /accept/i })).toBeNull();
    expect(screen.getByText("4 flags")).toBeInTheDocument();
    expect(screen.getByText("confirm")).toBeInTheDocument();
  });

  it("dims provisional figures and inks audited ones", () => {
    renderTable();
    const audited = screen.getAllByRole("row")[2];
    const provisional = screen.getAllByRole("row")[3];
    expect(within(audited).getByText("1.97").className).not.toMatch(/text-subtle/);
    expect(within(provisional).getByText("1.93").className).toMatch(/text-subtle/);
  });

  it("multi-shooter: parent shows counts and expands to one row per shooter", async () => {
    const triage: TriageResponse = {
      ...TRIAGE,
      cells: [TRIAGE.cells[2], cell({ ...TRIAGE.cells[2], slug: "s2", shooter_name: "Anna", status: "audited" })],
    };
    renderTable({}, [shooter("s1", "Mathias Axell"), shooter("s2", "Anna")], triage);
    const parent = screen.getAllByRole("row")[1];
    expect(within(parent).getByText("1 / 2 audited")).toBeInTheDocument();
    await userEvent.click(within(parent).getByRole("button", { name: /show shooters/i }));
    expect(screen.getByText("Anna")).toBeInTheDocument();
    expect(screen.getByText("Mathias Axell")).toBeInTheDocument();
  });
});
