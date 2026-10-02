import { describe, expect, it } from "vitest";

import {
  bestCells,
  cellValue,
  nextSort,
  parseSort,
  sortCells,
  sortParam,
  type SplitsCell,
} from "./splitsTable";

function cell(
  slug: string,
  draw: number | null,
  time: number,
  hf: number | null,
  audited = true,
): SplitsCell {
  return {
    slug,
    shooterName: slug,
    status: audited ? "audited" : "ready",
    audited,
    skipped: false,
    draw,
    avgSplit: draw == null ? null : draw / 4,
    fastestSplit: null,
    shotCount: 15,
    timeSeconds: time,
    scorecard:
      hf == null ? null : ({ hit_factor: hf } as SplitsCell["scorecard"]),
    videoCount: 1,
  };
}

const MATHIAS = cell("mathias", 1.93, 13.57, 2.43);
const MARTIN = cell("martin", 1.65, 14.28, 3.92);
const ANTON = cell("anton", 1.67, 13.9, 3.88);
const NOT_AUDITED = cell("cleo", null, 0, 3.1, false);

describe("per-stage sort", () => {
  it("sorts the shooters on a column, a shooter without the figure last either way", () => {
    const cells = [MATHIAS, NOT_AUDITED, MARTIN, ANTON];
    expect(
      sortCells(cells, { key: "draw", dir: "asc" }).map((c) => c.slug),
    ).toEqual(["martin", "anton", "mathias", "cleo"]);
    expect(
      sortCells(cells, { key: "draw", dir: "desc" }).map((c) => c.slug),
    ).toEqual(["mathias", "anton", "martin", "cleo"]);
    expect(sortCells(cells, null)).toBe(cells);
  });

  it("HF comes from the scorecard even on a stage not audited", () => {
    expect(cellValue(NOT_AUDITED, "hf")).toBe(3.1);
    expect(cellValue(NOT_AUDITED, "time")).toBeNull();
    expect(
      sortCells([MATHIAS, NOT_AUDITED], { key: "hf", dir: "desc" })[0].slug,
    ).toBe("cleo");
  });

  it("a header click goes best first, reversed, then back to match order", () => {
    expect(nextSort(null, "draw")).toEqual({ key: "draw", dir: "asc" });
    expect(nextSort({ key: "draw", dir: "asc" }, "draw")).toEqual({
      key: "draw",
      dir: "desc",
    });
    expect(nextSort({ key: "draw", dir: "desc" }, "draw")).toBeNull();
    expect(nextSort({ key: "draw", dir: "asc" }, "hf")).toEqual({
      key: "hf",
      dir: "desc",
    });
  });

  it("round-trips through the URL", () => {
    expect(parseSort(sortParam({ key: "avgSplit", dir: "desc" }))).toEqual({
      key: "avgSplit",
      dir: "desc",
    });
    expect(parseSort("hits.asc")).toBeNull();
    expect(parseSort(null)).toBeNull();
  });
});

describe("best per stage", () => {
  it("marks the best of each column, every shooter on a tie, never shots", () => {
    const best = bestCells([MATHIAS, MARTIN, ANTON, NOT_AUDITED]);
    expect([...best.draw!]).toEqual(["martin"]);
    expect([...best.time!]).toEqual(["mathias"]);
    expect([...best.hf!]).toEqual(["martin"]);
    expect(best.shots).toBeUndefined();
    const tie = bestCells([cell("a", 1.5, 10, null), cell("b", 1.5, 11, null)]);
    expect([...tie.draw!].sort()).toEqual(["a", "b"]);
  });

  it("marks nothing in a column fewer than two shooters have", () => {
    expect(bestCells([MATHIAS, NOT_AUDITED]).draw).toBeUndefined();
    expect(bestCells([MATHIAS]).hf).toBeUndefined();
  });
});
