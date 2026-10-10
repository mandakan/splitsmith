/**
 * Every fixture in the corpus survives a load and an unedited save
 * unchanged: the walk saves after every decision, so any field the save
 * rewrites is rewritten across the corpus.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { deriveMarkers } from "./audit-doc";
import { buildFixtureJson } from "./fixtureDoc";

const FIXTURES = join(__dirname, "..", "..", "..", "..", "..", "tests", "fixtures");

function corpus(): Array<[string, Record<string, unknown>]> {
  return readdirSync(FIXTURES)
    .filter((f) => f.startsWith("stage-shots-") && f.endsWith(".json") && !f.endsWith("-report.json"))
    .map((f): [string, Record<string, unknown>] => [f, JSON.parse(readFileSync(join(FIXTURES, f), "utf8"))])
    .filter(([, d]) => Array.isArray(d.shots));
}

describe("buildFixtureJson over the corpus", () => {
  const fixtures = corpus();

  it("finds the corpus", () => {
    expect(fixtures.length).toBeGreaterThan(100);
  });

  it("writes every fixture's shots back exactly as loaded when nothing was edited", () => {
    const changed: string[] = [];
    for (const [name, doc] of fixtures) {
      const saved = buildFixtureJson({ base: doc as never, markers: deriveMarkers(doc as never), appendEvents: [] });
      if (JSON.stringify(saved.shots) !== JSON.stringify(doc.shots)) changed.push(name);
    }
    expect(changed).toEqual([]);
  });
});
