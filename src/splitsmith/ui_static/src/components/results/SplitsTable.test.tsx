import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { MatchProject, ShooterListEntry, StageEntry, StageFigures, StageScorecard, StageStatus } from "@/lib/api";
import { buildSplitsRows, scoreboardTotals } from "@/lib/splitsTable";

import { SplitsCards } from "./SplitsCards";
import { SplitsTable, type SplitsHrefs } from "./SplitsTable";

interface StageSpec {
  n: number;
  name: string;
  status: StageStatus;
  figures?: StageFigures | null;
  time?: number;
  scorecard?: StageScorecard | null;
  videos?: number;
}

const SCORECARD: StageScorecard = {
  hit_factor: 2.12,
  stage_points: 103,
  stage_pct: 71.5,
  alphas: 10,
  charlies: 16,
  deltas: 5,
  misses: 0,
  no_shoots: 0,
  procedurals: 0,
  dq: false,
};

const FIGURES: StageFigures = { draw: 1.84, avg_split: 0.52, fastest_split: 0.31, shot_count: 28, split_count: 9 };

function project(stages: StageSpec[]): MatchProject {
  return {
    name: "demo",
    stages: stages.map(
      (s): StageEntry =>
        ({
          stage_number: s.n,
          stage_name: s.name,
          time_seconds: s.time ?? 0,
          scorecard_updated_at: null,
          videos: Array.from({ length: s.videos ?? (s.status === "todo" ? 0 : 1) }, () => ({ role: "primary" })),
          skipped: false,
          placeholder: false,
          time_seconds_manual: false,
          status: s.status,
          stage_rounds: null,
          scorecard: s.scorecard ?? null,
          figures: s.figures ?? null,
        }) as unknown as StageEntry,
    ),
  } as unknown as MatchProject;
}

function shooter(slug: string, name: string): ShooterListEntry {
  return { slug, name, stage_statuses: [] } as unknown as ShooterListEntry;
}

const HREFS: SplitsHrefs = {
  stage: (slug, n) => `/m/results/${slug}/${n}`,
  audit: (slug, n) => `/m/audit/${slug}/${n}`,
};

const STAGES: StageSpec[] = [
  { n: 1, name: "Steel Rush", status: "ready", scorecard: { ...SCORECARD, hit_factor: 1.59 }, time: 20 },
  { n: 2, name: "Brass Monkey", status: "audited", figures: FIGURES, scorecard: SCORECARD, time: 48.63 },
  { n: 3, name: "Alpha", status: "todo" },
  { n: 4, name: "Bravo", status: "todo" },
  { n: 5, name: "Charlie", status: "todo" },
];

function renderTable(opts: { share?: boolean; multi?: boolean; filterSlug?: string | null } = {}) {
  const share = opts.share ?? false;
  const shooters = opts.multi ? [shooter("me", "Mathias"), shooter("anna", "Anna")] : [shooter("me", "Mathias")];
  const projects: Record<string, MatchProject | null> = { me: project(STAGES) };
  if (opts.multi) {
    projects.anna = project([{ n: 2, name: "Brass Monkey", status: "audited", figures: { ...FIGURES, draw: 1.44 }, time: 40 }]);
  }
  const rows = buildSplitsRows({ projects, shooters, leadSlug: "me", filterSlug: opts.filterSlug ?? null, share });
  return render(
    <MemoryRouter>
      <SplitsTable rows={rows} multi={Boolean(opts.multi)} filterSlug={opts.filterSlug ?? null} share={share} totals={scoreboardTotals(rows)} hrefs={HREFS} />
    </MemoryRouter>,
  );
}

function rowOf(text: string): HTMLElement {
  const cell = screen.getByText(text);
  const tr = cell.closest("tr");
  if (!tr) throw new Error(`no row for ${text}`);
  return tr;
}

describe("SplitsTable", () => {
  it("renders splits before scoring on an audited row, with a play link", () => {
    renderTable();
    const tr = rowOf("Brass Monkey");
    const cells = within(tr).getAllByRole("cell").map((c) => c.textContent);
    expect(cells.slice(0, 9)).toEqual(["02", "Brass Monkey", "1.84", "0.520", "0.310", "28", "48.63", "2.12", "10A 16C 5D"]);
    expect(within(tr).getByRole("link", { name: "Play stage 2" })).toHaveAttribute("href", "/m/results/me/2");
  });

  it("owner not-audited row says so, links to Audit, keeps its dimmed HF, has no play link", () => {
    renderTable();
    const tr = rowOf("Steel Rush");
    expect(within(tr).getByText(/not audited/)).toBeInTheDocument();
    expect(within(tr).getByRole("link", { name: "Audit" })).toHaveAttribute("href", "/m/audit/me/1");
    expect(within(tr).getByText("1.59")).toBeInTheDocument();
    expect(within(tr).queryByRole("link", { name: /Play/ })).toBeNull();
  });

  it("collapses a no-footage run into one line with an ordinal range", () => {
    renderTable();
    const tr = rowOf("Alpha to Charlie");
    expect(within(tr).getByText("03–05")).toBeInTheDocument();
    expect(within(tr).getByText("no footage")).toBeInTheDocument();
  });

  it("share surface reads no video and offers no Audit link", () => {
    renderTable({ share: true });
    expect(screen.queryByRole("link", { name: "Audit" })).toBeNull();
    expect(screen.getAllByText("no video").length).toBeGreaterThan(0);
    expect(screen.queryByText(/not audited/)).toBeNull();
    expect(screen.getByRole("link", { name: "Play stage 2" })).toBeInTheDocument();
  });

  it("multi-shooter on All: disclosure reveals one sub-row per shooter", () => {
    renderTable({ multi: true });
    expect(screen.queryByText("Anna")).toBeNull();
    fireEvent.click(within(rowOf("Brass Monkey")).getByRole("button", { name: "Show shooters" }));
    const anna = rowOf("Anna");
    expect(within(anna).getByText("1.44")).toBeInTheDocument();
    expect(within(anna).getByRole("link", { name: "Play stage 2 Anna" })).toHaveAttribute("href", "/m/results/anna/2");
    expect(screen.getByRole("button", { name: "Hide shooters" })).toBeInTheDocument();
  });

  it("renders the scoreboard footer in the scorecard's columns", () => {
    renderTable();
    const tr = rowOf("Scoreboard");
    expect(within(tr).getByText("20A 32C 10D")).toBeInTheDocument();
    expect(within(tr).getByText("1:08.6")).toBeInTheDocument();
  });
});

describe("SplitsCards", () => {
  it("renders one card per audited stage and one dim line per collapsed run", () => {
    const rows = buildSplitsRows({ projects: { me: project(STAGES) }, shooters: [shooter("me", "Mathias")], leadSlug: "me", filterSlug: null, share: false });
    render(
      <MemoryRouter>
        <SplitsCards rows={rows} share={false} hrefs={HREFS} />
      </MemoryRouter>,
    );
    const card = screen.getByRole("region", { name: "Stage 2 Brass Monkey" });
    expect(within(card).getByRole("link", { name: "Play stage 2" })).toBeInTheDocument();
    expect(within(card).getByText("0.520")).toBeInTheDocument();
    expect(within(card).getByText(/10A 16C 5D/)).toBeInTheDocument();
    expect(screen.getByText("Alpha to Charlie")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /not audited/ })).toHaveAttribute("href", "/m/audit/me/1");
  });
});
