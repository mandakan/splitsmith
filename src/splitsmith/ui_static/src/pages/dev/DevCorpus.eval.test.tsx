/**
 * Subset eval from the Corpus filter bar. The toolbar's eval button
 * runs /api/lab/eval over exactly the fixtures the active filter +
 * query shows (minus the ones with no WAV, which cannot eval). With
 * the default "all" filter and an empty query it submits an unscoped
 * run -- the canonical full-corpus eval -- so Corpus can launch any
 * scope while Validate keeps its full-corpus button.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import type { DeveloperShellOutletContext } from "@/components/developer/DeveloperShell";
import { api, type LabEvalRun, type LabFixtureRecord } from "@/lib/api";
import { DevCorpus } from "@/pages/dev/DevCorpus";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listLabFixtures: vi.fn(),
      getDevReviewQueue: vi.fn().mockResolvedValue({ pending: [], flagged: [], done: [] }),
      getRecentProjects: vi.fn().mockResolvedValue([]),
      getLastLabRun: vi.fn(),
      runLabEval: vi.fn(),
      pollJob: vi.fn(),
    },
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

function record(slug: string, over: Partial<LabFixtureRecord> = {}): LabFixtureRecord {
  return {
    slug,
    audit_path: `/fixtures/${slug}.json`,
    audio_path: `/fixtures/${slug}.wav`,
    has_audio: true,
    n_shots: 12,
    expected_rounds: 12,
    stage_time_seconds: 20,
    beep_time: 1.5,
    source: null,
    source_video: null,
    audit_mtime: 1,
    audio_mtime: 1,
    anchor_slug: null,
    event_id: null,
    promoted_at: null,
    n_labeled_shots: 0,
    n_labeled_rejects: 0,
    in_calibration: false,
    ...over,
  };
}

const PENDING = record("stage-shots-hfo-masters-2026-stage1-s0fe3d797", {
  promoted_at: "2026-08-14T12:00:00+00:00",
});
const LABELED = record("stage-shots-blacksmith-2026-stage6", { n_labeled_shots: 12 });
const NO_AUDIO = record("stage-shots-hfo-masters-2026-stage2-s0fe3d797", {
  promoted_at: "2026-08-14T12:40:00+00:00",
  has_audio: false,
});

function runCovering(slugs: string[]): LabEvalRun {
  const fixtureOf = (slug: string) => ({
    slug,
    audit_path: `/fixtures/${slug}.json`,
    audio_path: `/fixtures/${slug}.wav`,
    source_video: null,
    expected_rounds: 12,
    candidates: [],
    truth_times: [1, 2],
    metrics: {
      n_truth: 12,
      n_kept: 10,
      true_positives: 9,
      false_positives: 1,
      false_negatives: 3,
      precision: 0.9,
      recall: 0.75,
      f1: 0.818,
      voter_recall: { vote_a: 1, vote_b: 1, vote_c: 1 },
      fp_by_reason: {},
      positives_by_subclass: {},
    },
    audit_mtime: 1,
    audio_mtime: 1,
  });
  return {
    config: {
      consensus: 2,
      apriori_boost: 1.0,
      tolerance_ms: 75,
      use_expected_rounds: true,
      voter_a_floor_override: null,
      voter_b_threshold_override: null,
      voter_c_threshold_override: null,
    },
    config_hash: "cfg1",
    built_at: "2026-08-17T10:00:00Z",
    summary: {
      n_fixtures: slugs.length,
      n_truth: 10 * slugs.length,
      n_kept: 10 * slugs.length,
      true_positives: 9 * slugs.length,
      false_positives: slugs.length,
      false_negatives: slugs.length,
      precision: 0.9,
      recall: 0.9,
      f1: 0.9,
      fp_by_reason: {},
      positives_by_subclass: {},
    },
    universe: {
      fixtures: slugs.map(fixtureOf),
      voter_a_floor: 0.1,
      voter_b_threshold: 0.02,
      voter_c_threshold: 0.5,
      tolerance_ms: 75,
    },
  };
}

function renderCorpus() {
  const outletContext: DeveloperShellOutletContext = { model: null, refresh: () => {} };
  return render(
    <MemoryRouter initialEntries={["/dev/corpus"]}>
      <ConfirmProvider>
        <Routes>
          <Route element={<Outlet context={outletContext} />}>
            <Route path="dev/corpus" element={<DevCorpus />} />
          </Route>
        </Routes>
      </ConfirmProvider>
    </MemoryRouter>,
  );
}

function arm(finalRun: LabEvalRun) {
  vi.mocked(api.listLabFixtures).mockResolvedValue([PENDING, LABELED, NO_AUDIO]);
  // Mount hydration: no cached run yet. After the eval job, the fresh run.
  vi.mocked(api.getLastLabRun)
    .mockRejectedValueOnce(Object.assign(new Error("404"), { status: 404 }))
    .mockResolvedValue(finalRun);
  vi.mocked(api.runLabEval).mockResolvedValue({ id: "job-1", status: "running" } as never);
  vi.mocked(api.pollJob).mockResolvedValue({ id: "job-1", status: "succeeded" } as never);
}

describe("DevCorpus subset eval", () => {
  it("runs the eval scoped to the filtered, audio-bearing fixtures", async () => {
    arm(runCovering([PENDING.slug]));
    renderCorpus();
    await screen.findByText(PENDING.slug);

    // "needs review" matches PENDING and NO_AUDIO; only PENDING can eval.
    await userEvent.click(screen.getByRole("button", { name: /needs review/i }));
    const evalBtn = await screen.findByRole("button", { name: /eval these 1/i });
    await userEvent.click(evalBtn);

    await waitFor(() =>
      expect(api.runLabEval).toHaveBeenCalledWith(
        expect.objectContaining({ slugs: [PENDING.slug] }),
      ),
    );
    // Subset summary strip over the fixtures just evaled: precision
    // 9/10, recall 9/12 -- distinct so each figure lands where meant.
    expect(await screen.findByText(/90\.0%/)).toBeInTheDocument();
    expect(screen.getByText(/75\.0%/)).toBeInTheDocument();
  });

  it("submits an unscoped run for the whole corpus (filter=all, no query)", async () => {
    arm(runCovering([PENDING.slug, LABELED.slug]));
    renderCorpus();
    await screen.findByText(PENDING.slug);

    const evalBtn = await screen.findByRole("button", { name: /eval corpus \(2\)/i });
    await userEvent.click(evalBtn);

    await waitFor(() => expect(api.runLabEval).toHaveBeenCalled());
    expect(vi.mocked(api.runLabEval).mock.calls[0][0]).toEqual(
      expect.objectContaining({ slugs: undefined }),
    );
  });
});
