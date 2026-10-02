import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type {
  MatchProject,
  ShooterListEntry,
  StageEntry,
  StageFigures,
  StageScorecard,
  StageStatus,
} from "@/lib/api";
import {
  buildSplitsRows,
  nextSort,
  scoreboardTotals,
  type SplitsSort,
} from "@/lib/splitsTable";

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

const FIGURES: StageFigures = {
  draw: 1.84,
  avg_split: 0.52,
  fastest_split: 0.31,
  shot_count: 28,
  split_count: 9,
};

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
          videos: Array.from(
            { length: s.videos ?? (s.status === "todo" ? 0 : 1) },
            () => ({ role: "primary" }),
          ),
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
  {
    n: 1,
    name: "Steel Rush",
    status: "ready",
    scorecard: { ...SCORECARD, hit_factor: 1.59 },
    time: 20,
  },
  {
    n: 2,
    name: "Brass Monkey",
    status: "audited",
    figures: FIGURES,
    scorecard: SCORECARD,
    time: 48.63,
  },
  { n: 3, name: "Alpha", status: "todo" },
  { n: 4, name: "Bravo", status: "todo" },
  { n: 5, name: "Charlie", status: "todo" },
];

function multiRows(share = false) {
  const shooters = [
    shooter("me", "Mathias"),
    shooter("anna", "Anna"),
    shooter("bo", "Bo"),
  ];
  const projects: Record<string, MatchProject | null> = {
    me: project(STAGES),
    anna: project([
      {
        n: 2,
        name: "Brass Monkey",
        status: "audited",
        figures: { ...FIGURES, draw: 1.44, avg_split: 0.6 },
        time: 40,
        scorecard: { ...SCORECARD, hit_factor: 3.4 },
      },
    ]),
    bo: project([
      {
        n: 2,
        name: "Brass Monkey",
        status: "ready",
        scorecard: { ...SCORECARD, hit_factor: 1.1 },
      },
    ]),
  };
  return buildSplitsRows({
    projects,
    shooters,
    leadSlug: "me",
    filterSlug: null,
    share,
  });
}

function renderTable(opts: { share?: boolean } = {}) {
  const share = opts.share ?? false;
  const rows = buildSplitsRows({
    projects: { me: project(STAGES) },
    shooters: [shooter("me", "Mathias")],
    leadSlug: "me",
    filterSlug: null,
    share,
  });
  return render(
    <MemoryRouter>
      <SplitsTable
        rows={rows}
        grouped={false}
        share={share}
        totals={scoreboardTotals(rows)}
        hrefs={HREFS}
        sort={null}
        onSort={() => {}}
        markBest
      />
    </MemoryRouter>,
  );
}

function GroupedTable({
  markBest = true,
  share = false,
}: {
  markBest?: boolean;
  share?: boolean;
}) {
  const rows = multiRows(share);
  const [sort, setSort] = useState<SplitsSort | null>(null);
  return (
    <MemoryRouter>
      <SplitsTable
        rows={rows}
        grouped
        share={share}
        totals={scoreboardTotals(rows)}
        hrefs={HREFS}
        sort={sort}
        onSort={(key) => setSort((prev) => nextSort(prev, key))}
        markBest={markBest}
      />
    </MemoryRouter>
  );
}

/** Shooter names of one stage's group, top to bottom. */
function shootersOf(stage: string): string[] {
  const first = rowOf(stage);
  const span = Number(
    within(first).getAllByRole("cell")[0].getAttribute("rowspan") ?? 1,
  );
  const out: string[] = [];
  let tr: Element | null = first;
  for (let i = 0; i < span && tr; i++, tr = tr.nextElementSibling) {
    const cells = within(tr as HTMLElement).getAllByRole("cell");
    out.push(cells[i === 0 ? 2 : 0].textContent ?? "");
  }
  return out;
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
    const cells = within(tr)
      .getAllByRole("cell")
      .map((c) => c.textContent);
    expect(cells.slice(0, 9)).toEqual([
      "02",
      "Brass Monkey",
      "1.84",
      "0.520",
      "0.310",
      "28",
      "48.63",
      "2.12",
      "10A 16C 5D",
    ]);
    expect(
      within(tr).getByRole("link", { name: "Play stage 2" }),
    ).toHaveAttribute("href", "/m/results/me/2");
  });

  it("owner not-audited row says so, links to Audit, keeps its dimmed HF, has no play link", () => {
    renderTable();
    const tr = rowOf("Steel Rush");
    expect(within(tr).getByText(/not audited/)).toBeInTheDocument();
    expect(within(tr).getByRole("link", { name: "Audit" })).toHaveAttribute(
      "href",
      "/m/audit/me/1",
    );
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
    expect(
      screen.getByRole("link", { name: "Play stage 2" }),
    ).toBeInTheDocument();
  });

  it("several shooters: the stage spans one row per shooter, every shooter always shown", () => {
    render(<GroupedTable />);
    expect(shootersOf("Brass Monkey")).toEqual(["Mathias", "Anna", "Bo"]);
    expect(screen.queryByRole("button", { name: /Show shooters/ })).toBeNull();
    const anna = rowOf("Anna");
    expect(within(anna).getByText("1.44")).toBeInTheDocument();
    expect(
      within(anna).getByRole("link", { name: "Play stage 2 Anna" }),
    ).toHaveAttribute("href", "/m/results/anna/2");
    expect(within(rowOf("Bo")).getByText(/not audited/)).toBeInTheDocument();
  });

  it("a header sorts the shooters inside each stage: best first, reversed, back to match order", () => {
    render(<GroupedTable />);
    const draw = screen.getByRole("button", { name: "Draw" });
    fireEvent.click(draw);
    expect(screen.getByRole("columnheader", { name: "Draw" })).toHaveAttribute(
      "aria-sort",
      "ascending",
    );
    expect(shootersOf("Brass Monkey")).toEqual(["Anna", "Mathias", "Bo"]);
    fireEvent.click(draw);
    expect(shootersOf("Brass Monkey")).toEqual(["Mathias", "Anna", "Bo"]);
    fireEvent.click(draw);
    expect(screen.getByRole("columnheader", { name: "Draw" })).toHaveAttribute(
      "aria-sort",
      "none",
    );
    // HF reads the scorecard, so the shooter not audited sorts by it too.
    fireEvent.click(screen.getByRole("button", { name: "HF" }));
    expect(shootersOf("Brass Monkey")).toEqual(["Anna", "Mathias", "Bo"]);
    // Stages never move.
    expect(
      screen
        .getAllByRole("cell")
        .filter((c) => /^0\d/.test(c.textContent ?? ""))
        .map((c) => c.textContent),
    ).toEqual(["01", "02", "03–05"]);
  });

  it("share link: the same grouped table, sortable and marked, with no owner actions", () => {
    render(<GroupedTable share />);
    expect(shootersOf("Brass Monkey")).toEqual(["Mathias", "Anna", "Bo"]);
    expect(within(rowOf("Bo")).getByText("no video")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Audit" })).toBeNull();
    expect(screen.queryByText(/not audited/)).toBeNull();
    // A stage nobody has video of folds into one line.
    expect(
      within(rowOf("Steel Rush")).getByText("no video"),
    ).toBeInTheDocument();
    expect(within(rowOf("Anna")).getByText("1.44")).toHaveClass("text-done");
    fireEvent.click(screen.getByRole("button", { name: "Draw" }));
    expect(shootersOf("Brass Monkey")).toEqual(["Anna", "Mathias", "Bo"]);
    expect(
      within(rowOf("Anna")).getByRole("link", { name: "Play stage 2 Anna" }),
    ).toBeInTheDocument();
  });

  it("marks each column's best per stage, and drops the marks when off", () => {
    const { unmount } = render(<GroupedTable />);
    expect(within(rowOf("Anna")).getByText("1.44")).toHaveClass("text-done");
    expect(screen.getByText("0.520")).toHaveClass("text-done");
    expect(within(rowOf("Anna")).getByText("3.40")).toHaveClass("text-done");
    expect(screen.getByText("1.84")).not.toHaveClass("text-done");
    unmount();
    render(<GroupedTable markBest={false} />);
    expect(within(rowOf("Anna")).getByText("1.44")).not.toHaveClass(
      "text-done",
    );
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
    const rows = buildSplitsRows({
      projects: { me: project(STAGES) },
      shooters: [shooter("me", "Mathias")],
      leadSlug: "me",
      filterSlug: null,
      share: false,
    });
    render(
      <MemoryRouter>
        <SplitsCards
          rows={rows}
          share={false}
          hrefs={HREFS}
          grouped={false}
          sort={null}
          markBest
        />
      </MemoryRouter>,
    );
    const card = screen.getByRole("region", { name: "Stage 2 Brass Monkey" });
    expect(
      within(card).getByRole("link", { name: "Play stage 2" }),
    ).toBeInTheDocument();
    expect(within(card).getByText("0.520")).toBeInTheDocument();
    expect(within(card).getByText(/10A 16C 5D/)).toBeInTheDocument();
    expect(screen.getByText("Alpha to Charlie")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /not audited/ })).toHaveAttribute(
      "href",
      "/m/audit/me/1",
    );
  });

  it("several shooters: one card per stage with a line per shooter, in sort order, best marked", () => {
    render(
      <MemoryRouter>
        <SplitsCards
          rows={multiRows()}
          share={false}
          hrefs={HREFS}
          grouped
          sort={{ key: "draw", dir: "asc" }}
          markBest
        />
      </MemoryRouter>,
    );
    const card = screen.getByRole("region", { name: "Stage 2 Brass Monkey" });
    const names = within(card)
      .getAllByRole("listitem")
      .map((li) => li.querySelector("span")?.textContent);
    expect(names).toEqual(["Anna", "Mathias", "Bo"]);
    expect(within(card).getByText("1.44")).toHaveClass("text-done");
  });
});
