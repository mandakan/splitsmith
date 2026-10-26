import { describe, expect, it } from "vitest";

import {
  angleOf,
  compareHeads,
  corpusFlow,
  corpusMembership,
  heldOutTone,
  paramRows,
  type ModelMap,
  corpusRows,
  filterFixtures,
  fixtureFlags,
  fixtureStatus,
  foldsFor,
  heldOutGap,
  layoutFlow,
  NO_FILTER,
  versionSeries,
  type ModelMapFixture,
  type VoterCHead,
} from "./modelMap";

function fx(stem: string, over: Partial<ModelMapFixture> = {}): ModelMapFixture {
  return {
    stem,
    match: "alpha-2026",
    stage: 1,
    shooter_id: "sabc12345",
    camera_class: "headcam",
    camera: "Insta360 GO 3S",
    n_shots: 12,
    expected_rounds: 12,
    has_wav: true,
    review_status: "reviewed",
    superseded_by: null,
    wrong_clip: false,
    voter_c: "headcam",
    match_group: "alpha-2026",
    stage_group: "alpha-2026:1",
    beep_ranker: null,
    ...over,
  };
}

describe("fixtureStatus", () => {
  it("is the head a fixture calibrated, whatever happened to it since", () => {
    expect(fixtureStatus(fx("a", { superseded_by: "b", review_status: "needs_review" }))).toBe("headcam");
    expect(fixtureStatus(fx("a", { review_status: "needs_review" }))).toBe("headcam");
    expect(fixtureStatus(fx("a", { voter_c: "handheld" }))).toBe("handheld");
  });

  it("sorts the rest by why they are not in the build", () => {
    expect(fixtureStatus(fx("a", { voter_c: null, superseded_by: "b", review_status: "needs_review" }))).toBe(
      "superseded",
    );
    expect(fixtureStatus(fx("a", { voter_c: null, review_status: "needs_review" }))).toBe("review");
    expect(fixtureStatus(fx("a", { voter_c: null }))).toBe("unused");
  });

  it("flags a calibrating fixture that is leaving or unreviewed", () => {
    expect(fixtureFlags(fx("a", { superseded_by: "b", review_status: "needs_review" }))).toEqual({
      leaving: true,
      unreviewed: false,
    });
    expect(fixtureFlags(fx("a", { review_status: "needs_review" }))).toEqual({ leaving: false, unreviewed: true });
    expect(fixtureFlags(fx("a", { voter_c: null, review_status: "needs_review" }))).toEqual({
      leaving: false,
      unreviewed: false,
    });
  });
});

describe("corpusRows", () => {
  it("groups by the fold's match, stages in order, tiles by shooter", () => {
    const rows = corpusRows([
      fx("b2", { stage: 2, shooter_id: "s2" }),
      fx("b1", { stage: 2, shooter_id: "s1" }),
      fx("a1", { stage: 1 }),
      fx("z", { match_group: "beta-2026", match: "alpha-2026" }),
    ]);
    expect(rows.map((r) => r.match)).toEqual(["alpha-2026", "beta-2026"]);
    expect(rows[0].stages.map((s) => s.stage)).toEqual([1, 2]);
    expect(rows[0].stages[1].fixtures.map((f) => f.stem)).toEqual(["b1", "b2"]);
    expect(rows[0].count).toBe(3);
  });
});

describe("filterFixtures", () => {
  it("ANDs every filter and matches text on stem or camera", () => {
    const all = [fx("a"), fx("b", { camera_class: "handheld", voter_c: "handheld", camera: "Apple iPhone" })];
    expect(filterFixtures(all, { ...NO_FILTER, cameraClass: "handheld" }).map((f) => f.stem)).toEqual(["b"]);
    expect(filterFixtures(all, { ...NO_FILTER, text: "iphone" }).map((f) => f.stem)).toEqual(["b"]);
    expect(filterFixtures(all, { ...NO_FILTER, status: "headcam", text: "iphone" })).toEqual([]);
  });
});

describe("corpusFlow + layoutFlow", () => {
  const fixtures = [
    fx("a"),
    fx("b", { voter_c: null }),
    fx("c", { camera_class: "handheld", voter_c: "handheld" }),
    fx("d", { camera_class: "handheld", voter_c: null, superseded_by: "c" }),
    fx("e", { camera_class: "handheld", voter_c: null, review_status: "needs_review" }),
  ];

  it("sends every fixture down exactly one path", () => {
    const { nodes, links } = corpusFlow(fixtures);
    const into = (id: string) => links.filter((l) => l.target === id).reduce((s, l) => s + l.value, 0);
    expect(into("cls:headcam") + into("cls:handheld")).toBe(5);
    const statuses = nodes.filter((n) => n.column === 2).map((n) => n.id);
    expect(statuses.reduce((s, id) => s + into(id), 0)).toBe(5);
    expect(links.find((l) => l.target === "st:superseded")?.stems).toEqual(["d"]);
  });

  it("draws no node for a status no fixture has", () => {
    const { nodes } = corpusFlow([fx("a")]);
    expect(nodes.map((n) => n.id)).toEqual(["corpus", "cls:headcam", "st:headcam"]);
  });

  it("lays bands out so each node's height matches its flow", () => {
    const laid = layoutFlow(corpusFlow(fixtures), { width: 300, height: 200, gap: 10 });
    const corpus = laid.nodes.find((n) => n.id === "corpus")!;
    const head = laid.nodes.find((n) => n.id === "cls:headcam")!;
    const hand = laid.nodes.find((n) => n.id === "cls:handheld")!;
    expect(head.h + hand.h).toBeCloseTo(corpus.h, 6);
    expect(hand.y).toBeCloseTo(head.y + head.h + 10, 6);
    expect(laid.nodes.every((n) => n.y + n.h <= 200 + 1e-6)).toBe(true);
    expect(laid.links.every((l) => l.d.startsWith("M"))).toBe(true);
  });
});

describe("foldsFor", () => {
  const fixtures = [
    fx("a"),
    fx("b", { stage: 2, stage_group: "alpha-2026:2", camera_class: "handheld", voter_c: "handheld" }),
    fx("c", { match_group: "beta-2026", stage_group: "beta-2026:1" }),
    fx("d", { voter_c: null }),
  ];

  it("folds leave-one-match-out by match over calibrating fixtures only", () => {
    expect(foldsFor("lomo", fixtures)).toEqual([
      { key: "alpha-2026", fixtures: 2, headcam: 1, handheld: 1 },
      { key: "beta-2026", fixtures: 1, headcam: 1, handheld: 0 },
    ]);
  });

  it("groups the stage split by stage and has no folds for the candidate split", () => {
    expect(foldsFor("grouped", fixtures).map((f) => f.key).sort()).toEqual([
      "alpha-2026:1",
      "alpha-2026:2",
      "beta-2026:1",
    ]);
    expect(foldsFor("stratified", fixtures)).toEqual([]);
  });
});

describe("heldOutGap", () => {
  it("is each honest split's loss against the by-candidate split", () => {
    const m = (recall: number, precision: number) => ({ precision, recall, f1: 0, tp: 0, fp: 0, fn: 0 });
    const head = {
      heldout: [
        { split: "stratified", at_shipped_threshold: m(0.95, 0.9), own_threshold: null, at_own_threshold: null },
        { split: "lomo", at_shipped_threshold: m(0.9, 0.85), own_threshold: null, at_own_threshold: null },
      ],
    } as unknown as VoterCHead;
    const gap = heldOutGap(head);
    expect(gap).toHaveLength(1);
    expect(gap[0].split).toBe("lomo");
    expect(gap[0].recall).toBeCloseTo(-0.05, 6);
  });
});

describe("versionSeries", () => {
  it("marks the versions that moved the corpus", () => {
    const v = (added: string[], n: Record<string, number>) => ({
      sha: "x",
      date: "2026-01-01",
      subject: "",
      built_at: null,
      model_set: "base",
      n_fixtures: n,
      recall: {},
      precision: {},
      added,
      removed: [],
    });
    const s = versionSeries([v(["a"], { all: 1 }), v([], { headcam: 1, handheld: 2 })]);
    expect(s.map((p) => [p.total, p.changed])).toEqual([
      [1, true],
      [3, false],
    ]);
  });
});

describe("heldOutTone", () => {
  it("ranks a missed shot over extra detections, and says nothing without data", () => {
    expect(heldOutTone(fx("a", { heldout_lomo: { tp: 9, fp: 2, fn: 1 } }))).toBe("missed");
    expect(heldOutTone(fx("a", { heldout_lomo: { tp: 9, fp: 2, fn: 0 } }))).toBe("extra");
    expect(heldOutTone(fx("a", { heldout_lomo: { tp: 9, fp: 0, fn: 0 } }))).toBe("clean");
    expect(heldOutTone(fx("a"))).toBeNull();
  });
});

describe("comparing two models", () => {
  const m = (recall: number) => ({ precision: 0.9, recall, f1: 0, tp: 0, fp: 0, fn: 0 });
  const head = (cls: "headcam" | "handheld", recall: number, byMatch: Record<string, number>, set = "clap") =>
    ({
      model_set: set,
      camera_class: cls,
      n_fixtures: 1,
      n_candidates: 10,
      n_positives: 5,
      voter_a_floor: 0.01,
      voter_b_threshold: 0.03,
      voter_c_threshold: recall,
      cv: null,
      onnx: null,
      onnx_bytes: null,
      heldout: [{ split: "lomo", at_shipped_threshold: m(recall), own_threshold: null, at_own_threshold: null }],
      heldout_matches: [],
      heldout_stages: null,
      heldout_candidates: null,
      lomo_by_match: Object.fromEntries(Object.entries(byMatch).map(([k, v]) => [k, m(v)])),
    }) as VoterCHead;
  const model = (fixtures: ModelMapFixture[], heads: VoterCHead[]) =>
    ({ fixtures, ensemble: { heads, target_recall: 0.95, tolerance_ms: 75, feature_dim: 57 } }) as unknown as ModelMap;

  const a = model(
    [fx("both"), fx("onlyA"), fx("onlyB", { voter_c: null }), fx("none", { voter_c: null })],
    [head("headcam", 0.82, { alpha: 0.7, beta: 0.9 })],
  );
  const b = model(
    [fx("both"), fx("onlyA", { voter_c: null }), fx("onlyB"), fx("none", { voter_c: null })],
    [head("headcam", 0.9, { beta: 0.95, gamma: 0.8 }), head("handheld", 0.95, {})],
  );

  it("says which model each fixture calibrated", () => {
    expect(corpusMembership(a, b)).toEqual({ both: "both", onlyA: "a", onlyB: "b", none: "neither" });
  });

  it("lines up splits and matches per head, a head only one model has included", () => {
    const heads = compareHeads(a, b);
    expect(heads.map((h) => [h.modelSet, h.cameraClass])).toEqual([
      ["clap", "headcam"],
      ["clap", "handheld"],
    ]);
    const lomo = heads[0].rows.find((r) => r.split === "lomo")!;
    expect([lomo.a?.recall, lomo.b?.recall]).toEqual([0.82, 0.9]);
    expect(heads[0].rows.find((r) => r.split === "grouped")).toEqual({ split: "grouped", a: null, b: null });
    expect(heads[0].matches.map((x) => [x.match, x.a?.recall ?? null, x.b?.recall ?? null])).toEqual([
      ["alpha", 0.7, null],
      ["beta", 0.9, 0.95],
      ["gamma", null, 0.8],
    ]);
    expect(heads[1].rows.find((r) => r.split === "lomo")?.a).toBeNull();
    // A build that recorded no per-match figures is told apart from a match it never saw.
    expect(heads[0].perMatch).toEqual({ a: true, b: true });
    expect(heads[1].perMatch).toEqual({ a: false, b: false });
  });

  it("marks the parameters that differ", () => {
    const rows = paramRows(a, b);
    expect(rows.find((r) => r.label === "Target recall")?.differs).toBe(false);
    expect(rows.find((r) => r.label === "clap headcam: voter C threshold")).toMatchObject({
      a: "0.8200",
      b: "0.9000",
      differs: true,
    });
    expect(rows.find((r) => r.label === "clap handheld: fixtures")).toMatchObject({ a: "--", b: "1", differs: true });
  });

  it("pairs a build from before model sets with the clap set, and lists base on its own", () => {
    const old = model([fx("both")], [head("headcam", 0.8, {}, "legacy")]);
    const sets = model([fx("both")], [head("headcam", 0.9, {}, "base"), head("headcam", 0.85, {}, "clap")]);
    const heads = compareHeads(old, sets);
    expect(heads.map((h) => [h.modelSet, h.cameraClass])).toEqual([
      ["base", "headcam"],
      ["clap", "headcam"],
    ]);
    const lomo = (i: number) => heads[i].rows.find((r) => r.split === "lomo")!;
    expect([lomo(0).a, lomo(0).b?.recall]).toEqual([null, 0.9]);
    expect([lomo(1).a?.recall, lomo(1).b?.recall]).toEqual([0.8, 0.85]);
    expect(paramRows(old, sets).find((r) => r.label === "clap headcam: voter C threshold")).toMatchObject({
      a: "0.8000",
      b: "0.8500",
    });
  });
});

describe("angleOf", () => {
  it("names the camera suffix of a secondary and calls the rest primary", () => {
    expect(angleOf(fx("stage-shots-alpha-2026-stage1-s97dcec94-apple-iphone17pro"))).toBe("apple-iphone17pro");
    expect(angleOf(fx("stage-shots-alpha-2026-stage1-s97dcec94"))).toBe("primary");
  });
});
