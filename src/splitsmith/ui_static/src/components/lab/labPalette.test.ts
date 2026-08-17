/**
 * Pins the lab outcome palette against design-token drift.
 *
 * The palette entries are ``var(--token)`` references, so two entries
 * can silently collapse when a token retune gives their underlying
 * tokens the same value -- exactly what #525 did: --color-split-slow
 * became #FF2D2D, identical to --color-destructive, and the fixture
 * detail legend showed FP and FN as the same red. TS can't see that;
 * this test resolves each var against styles/index.css and asserts the
 * outcome colours stay pairwise distinct.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { LAB_PALETTE } from "./labPalette";

const css = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "../../styles/index.css"),
  "utf-8",
);

/** First definition wins, matching how the single-palette stylesheet
 *  declares tokens. Nested var() indirection is followed. */
function resolveVar(ref: string): string {
  const name = ref.match(/^var\((--[\w-]+)\)$/)?.[1];
  if (!name) return ref;
  const m = css.match(new RegExp(`${name}:\\s*([^;]+);`));
  expect(m, `token ${name} not found in index.css`).toBeTruthy();
  const value = m![1].trim();
  return value.startsWith("var(") ? resolveVar(value) : value;
}

describe("LAB_PALETTE", () => {
  it("resolves TP / FP / FN / rejected / candidate to distinct colours", () => {
    const outcomes = {
      tp: resolveVar(LAB_PALETTE.tp),
      fp: resolveVar(LAB_PALETTE.fp),
      fn: resolveVar(LAB_PALETTE.fn),
      rejected: resolveVar(LAB_PALETTE.rejected),
      candidatePrimary: resolveVar(LAB_PALETTE.candidatePrimary),
    };
    const entries = Object.entries(outcomes);
    for (let i = 0; i < entries.length; i++) {
      for (let j = i + 1; j < entries.length; j++) {
        expect(
          entries[i][1].toLowerCase(),
          `${entries[i][0]} and ${entries[j][0]} resolve to the same colour`,
        ).not.toBe(entries[j][1].toLowerCase());
      }
    }
  });

  it("keeps FP legible against the red-tinted waveform bars", () => {
    // --color-waveform-bar is LED red at low alpha; a red-family FP
    // line disappears into it. Pin FP away from the destructive red.
    expect(resolveVar(LAB_PALETTE.fp).toLowerCase()).not.toBe(
      resolveVar("var(--color-destructive)").toLowerCase(),
    );
  });
});
