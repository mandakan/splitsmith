import { describe, expect, it } from "vitest";

import type { ShooterListEntry, StageDetailsView } from "@/lib/api";
import {
  applyPaste,
  copyOrder,
  detailsNotice,
  disagreement,
  duplicateOrders,
  moveInOrder,
  parseNumber,
  parsePastedResults,
  rowsFromView,
  shownScores,
  toRequest,
  withCells,
  type EditRow,
} from "@/lib/stageDetails";

// The Black Handgun 2023 results table as a browser copies it from
// Shoot'n Score It (Janne, Production Optics): Swedish headers, decimal
// commas, a stage button cell holding "N. Name".
const BLACK_HANDGUN = [
  "Station\t\tHF\tTID\tPTS\tA\tC\tD\tM\tPROC\tNS\tVRF",
  "1. Now You See Me\t\t2,9180\t12,68\t37\t5\t4\t0\t0\t0\t0\tY",
  "2. Weak Sandman\t\t2,2912\t20,95\t48\t9\t1\t0\t0\t0\t0\tY",
  "3. Strong Sandman\t\t1,4665\t14,32\t21\t2\t7\t0\t1\t0\t0\tY",
  "4. Dragonfly\t\t3,0509\t48,51\t148\t26\t6\t0\t0\t0\t0\tY",
  "5. Feed Me Bananas\t\t2,9427\t38,74\t114\t21\t3\t0\t0\t0\t0\tY",
  "6. Guleboj\t\t3,9362\t29,47\t116\t22\t2\t0\t0\t0\t0\tY",
  "7. Pling Pling\t\t3,4123\t31,65\t108\t18\t6\t0\t0\t0\t0\tY",
  "8. Florida Man\t\t3,3842\t41,96\t142\t23\t9\t0\t0\t0\t0\tY",
  "9. Looper\t\t2,8733\t19,49\t56\t10\t2\t0\t0\t0\t0\tY",
  "10. Rocket Science\t\t4,4207\t13,12\t58\t11\t1\t0\t0\t0\t0\tY",
  "11. The Remedy\t\t3,2067\t33,68\t108\t18\t6\t0\t0\t0\t0\tY",
  "12. Kriss Kross\t\t2,4929\t17,65\t44\t9\t3\t0\t0\t0\t1\tY",
].join("\n");

const TWELVE = Array.from({ length: 12 }, (_, i) => i + 1);

function view(n = 12): StageDetailsView {
  return {
    slug: "janne",
    name: "Janne Hauta",
    division: "Production Optics",
    power_factor: "minor",
    power_factor_set: false,
    ssi_url: null,
    scorecard_order: [],
    others: [],
    stages: Array.from({ length: n }, (_, i) => ({
      stage_number: i + 1,
      stage_name: `S${i + 1}`,
      skipped: false,
      time_seconds: null,
      time_manual: false,
      shot_order: null,
      scorecard: null,
    })),
  };
}

function row(cells: Partial<EditRow["cells"]>, manual: string[] = Object.keys(cells)): EditRow {
  const base = rowsFromView(view(1))[0];
  const next = withCells(base, cells);
  next.manual = new Set(manual.filter((m) => m !== "time_seconds" && m !== "shot_order")) as EditRow["manual"];
  return next;
}

const HITS = { deltas: "0", misses: "0", no_shoots: "0", procedurals: "0" };

describe("parseNumber", () => {
  it("reads decimal commas and points, nothing else", () => {
    expect(parseNumber("12,68")).toBe(12.68);
    expect(parseNumber(" 2.9180 ")).toBe(2.918);
    expect(parseNumber("")).toBeNull();
    expect(parseNumber("Y")).toBeNull();
  });
});

describe("parsePastedResults", () => {
  it("reads the Shoot'n Score It table", () => {
    const out = parsePastedResults(BLACK_HANDGUN, TWELVE);
    expect(out.understood).toBe(true);
    expect(out.unused).toEqual([]);
    expect(out.rows).toHaveLength(12);
    expect(out.rows[0]).toEqual({
      stage_number: 1,
      values: {
        hit_factor: "2.918",
        time_seconds: "12.68",
        stage_points: "37",
        alphas: "5",
        charlies: "4",
        deltas: "0",
        misses: "0",
        procedurals: "0",
        no_shoots: "0",
      },
    });
    expect(out.rows[11].values.no_shoots).toBe("1");
  });

  it("reads English headers, leaves a missing column alone, lists unknown stages", () => {
    const text = ["Stage\tTime\tPoints\tA", "1. One\t12.5\t40\t8", "13. Extra\t9\t10\t2", "Total\t\t\t"].join("\n");
    const out = parsePastedResults(text, [1, 2]);
    expect(out.rows).toEqual([{ stage_number: 1, values: { time_seconds: "12.5", stage_points: "40", alphas: "8" } }]);
    expect(out.unused).toEqual(["13. Extra", "Total"]);
  });

  it("reads columns split by runs of spaces", () => {
    const out = parsePastedResults("Station   HF   TID\n2. Two   2,5   10,0", [2]);
    expect(out.rows[0].values).toEqual({ hit_factor: "2.5", time_seconds: "10" });
  });

  it("says when there is no header", () => {
    expect(parsePastedResults("5\t4\t0", [1]).understood).toBe(false);
  });
});

describe("scores", () => {
  it("works out points and hit factor from hits and time", () => {
    const r = row({ alphas: "5", charlies: "4", ...HITS, time_seconds: "12,68" });
    expect(shownScores(r, "minor")).toEqual({
      points: { value: "37", derived: true },
      hf: { value: "2.918", derived: true },
    });
    expect(shownScores(r, "major").points.value).toBe("41");
  });

  it("floors points at zero", () => {
    const r = row({ alphas: "1", charlies: "0", deltas: "0", misses: "2", no_shoots: "0", procedurals: "0" });
    expect(shownScores(r, "minor").points.value).toBe("0");
  });

  it("keeps a typed value and warns when it disagrees", () => {
    const r = row({ alphas: "2", charlies: "7", deltas: "0", misses: "1", no_shoots: "0", procedurals: "0", stage_points: "23" });
    expect(shownScores(r, "minor").points).toEqual({ value: "23", derived: false });
    expect(disagreement(r, "minor")).toBe(
      "2 A, 7 C and 1 M give 21 points, the entered value is 23. Kept as entered.",
    );
    expect(disagreement(withCells(r, { stage_points: "21" }), "minor")).toBeNull();
  });

  it("works points out again once a typed value is emptied", () => {
    const typed = row({ alphas: "5", charlies: "4", ...HITS, stage_points: "40" });
    const emptied = withCells(typed, { stage_points: "" });
    expect(emptied.manual.has("stage_points")).toBe(false);
    expect(shownScores(emptied, "minor").points).toEqual({ value: "37", derived: true });
    expect(toRequest([emptied], null).rows[0].manual).not.toContain("stage_points");
  });

  it("previews whole counts the way the save rounds them", () => {
    const r = row({ alphas: "2,6", charlies: "0", ...HITS });
    expect(shownScores(r, "minor").points.value).toBe("15");
  });

  it("needs every hit field before working out points", () => {
    expect(shownScores(row({ alphas: "5", charlies: "4" }), "minor").points).toEqual({ value: "", derived: false });
  });
});

describe("paste into rows", () => {
  it("fills the rows unsaved and marks score fields manual", () => {
    const rows = applyPaste(rowsFromView(view()), parsePastedResults(BLACK_HANDGUN, TWELVE).rows);
    expect(rows[0].cells.time_seconds).toBe("12.68");
    expect(rows[0].pasted.has("stage_points")).toBe(true);
    expect(rows[0].manual.has("alphas")).toBe(true);
    const req = toRequest(rows, null);
    expect(req.rows[0]).toMatchObject({ time_seconds: 12.68, alphas: 5, stage_points: 37, hit_factor: 2.918 });
    expect(req.rows[0].manual).toContain("hit_factor");
  });
});

describe("order", () => {
  it("copies an order and finds duplicates", () => {
    const rows = copyOrder(rowsFromView(view(3)), [3, 1, 2]);
    expect(rows.map((r) => r.cells.shot_order)).toEqual(["2", "3", "1"]);
    const dup = rows.map((r, i) => (i === 0 ? withCells(r, { shot_order: "1" }) : r));
    expect([...duplicateOrders(dup)].sort()).toEqual([1, 3]);
  });

  it("moves a stage before another in shooting order", () => {
    const rows = copyOrder(rowsFromView(view(3)), [1, 2, 3]);
    const moved = moveInOrder(rows, 3, 1);
    expect(moved.map((r) => r.cells.shot_order)).toEqual(["2", "3", "1"]);
  });
});

describe("detailsNotice", () => {
  const entry = (slug: string, name: string, n: number) =>
    ({ slug, name, stages_without_time: n }) as unknown as ShooterListEntry;

  it("names every shooter with stages lacking a time", () => {
    expect(detailsNotice([entry("m", "Mathias Axell", 0)])).toBeNull();
    expect(detailsNotice([entry("j", "Janne Hauta", 4), entry("m", "Mathias Axell", 0)])?.title).toBe(
      "Janne has stages without a time",
    );
    expect(detailsNotice([entry("j", "Janne Hauta", 4), entry("e", "Erik", 1)])?.title).toBe(
      "Janne and Erik have stages without a time",
    );
  });
});
