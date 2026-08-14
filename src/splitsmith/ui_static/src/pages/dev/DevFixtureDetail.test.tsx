/**
 * /dev/corpus/:slug -- the full-page replacement for legacy Lab.tsx's
 * below-the-fold fixture drawer.
 *
 * Three behaviours are load-bearing and pinned here:
 *
 *  1. Landing on a fixture the cached run doesn't cover must self-heal
 *     by running a *slug-scoped* eval. The whole point of the page is
 *     that an operator can open any fixture and start labeling; a
 *     full-corpus eval takes minutes and the cache dies with the server
 *     process, so "no run cached" must not be a dead end.
 *  2. When the run already covers the fixture, the working area renders
 *     immediately and *no* eval fires -- a spurious eval on every visit
 *     would burn a job per navigation.
 *  3. Prev/next walk the corpus in catalog order and keep the dev-mode
 *     ``?match=`` context, which DeveloperShell threads through its own
 *     links and which the batch-promote panel reads.
 *
 * jsdom has no AudioContext, so the audio-touching lab components are
 * mocked (SnippetPlayer decodes; ZoomedWaveform renders its buffer).
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import { api, type LabEvalRun, type LabFixtureRecord } from "@/lib/api";
import { DevFixtureDetail } from "@/pages/dev/DevFixtureDetail";

vi.mock("@/components/Waveform", () => ({ Waveform: () => null }));
vi.mock("@/components/lab/ZoomedWaveform", () => ({ ZoomedWaveform: () => null }));
vi.mock("@/components/lab/SnippetPlayer", () => ({ SnippetPlayer: () => null }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listLabFixtures: vi.fn(),
      getLastLabRun: vi.fn(),
      getFixturePeaks: vi.fn().mockResolvedValue({
        peaks: [0, 0.5, 0.2],
        duration: 20,
        beep_time: 1.5,
      }),
      getFixtureAudit: vi.fn().mockResolvedValue({
        stage_number: 1,
        stage_name: "B50",
        beep_time: 1.5,
        shots: [{ shot_number: 1, time: 3.0 }],
        videos: [],
      }),
      runLabEval: vi.fn().mockResolvedValue({ id: "job-1", status: "running" }),
      pollJob: vi.fn().mockResolvedValue({ id: "job-1", status: "succeeded" }),
      deleteFixture: vi.fn().mockResolvedValue(undefined),
    },
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

const SLUG = "stage-shots-hfo-masters-2026-stage1-s0fe3d797";

function record(slug: string): LabFixtureRecord {
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
    event_id: "hfo-masters-2026:1",
  };
}

function candidate(n: number, over: Record<string, unknown> = {}) {
  return {
    candidate_number: n,
    time: 3.0 + n * 0.25,
    ms_after_beep: 1500 + n * 250,
    confidence: 0.4,
    peak_amplitude: 0.5,
    score_c: 0.6,
    clap_diff: 0.02,
    gunshot_prob: 0.7,
    vote_a: 1,
    vote_b: 1,
    vote_c: 0,
    vote_total: 2,
    apriori_boost: 0,
    ensemble_score: 2,
    kept: true,
    truth: 1,
    matched_shot_number: n,
    reason: null,
    subclass: null,
    ...over,
  };
}

function runWith(slug: string): LabEvalRun {
  const metrics = {
    n_truth: 2,
    n_kept: 2,
    true_positives: 2,
    false_positives: 0,
    false_negatives: 0,
    precision: 1,
    recall: 1,
    f1: 1,
    voter_recall: { vote_a: 1, vote_b: 1, vote_c: 0.5 },
    fp_by_reason: {},
    positives_by_subclass: {},
  };
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
    config_hash: "cfg1234",
    built_at: "2026-08-14T10:00:00Z",
    summary: {
      n_fixtures: 1,
      n_truth: 2,
      n_kept: 2,
      true_positives: 2,
      false_positives: 0,
      false_negatives: 0,
      precision: 1,
      recall: 1,
      f1: 1,
      fp_by_reason: {},
      positives_by_subclass: {},
    },
    universe: {
      fixtures: [
        {
          slug,
          audit_path: `/fixtures/${slug}.json`,
          audio_path: `/fixtures/${slug}.wav`,
          source_video: null,
          expected_rounds: 12,
          candidates: [candidate(1), candidate(2)],
          truth_times: [3.25, 3.5],
          metrics,
          audit_mtime: 1,
          audio_mtime: 1,
        },
      ],
      voter_a_floor: 0.1,
      voter_b_threshold: 0.02,
      voter_c_threshold: 0.5,
      tolerance_ms: 75,
    },
  };
}

function renderDetail(slug: string, search = "") {
  return render(
    <MemoryRouter initialEntries={[`/dev/corpus/${slug}${search}`]}>
      <ConfirmProvider>
        <Routes>
          <Route path="dev/corpus/:slug" element={<DevFixtureDetail />} />
          <Route path="dev/corpus" element={<div>corpus list</div>} />
        </Routes>
      </ConfirmProvider>
    </MemoryRouter>,
  );
}

describe("DevFixtureDetail", () => {
  it("auto-runs a scoped eval when the fixture is missing from the cached run", async () => {
    vi.mocked(api.listLabFixtures).mockResolvedValue([record(SLUG)]);
    vi.mocked(api.getLastLabRun).mockRejectedValue(new Error("no run"));

    renderDetail(SLUG);

    await waitFor(() =>
      expect(api.runLabEval).toHaveBeenCalledWith(
        expect.objectContaining({ slugs: [SLUG] }),
      ),
    );
  });

  it("renders the labeling working area once the run contains the fixture", async () => {
    vi.mocked(api.listLabFixtures).mockResolvedValue([record(SLUG)]);
    vi.mocked(api.getLastLabRun).mockResolvedValue(runWith(SLUG));

    renderDetail(SLUG);

    // CandidateTable: its <summary> carries the candidate count.
    expect(await screen.findByText(/candidates \(2\)/i)).toBeInTheDocument();
    // StepThroughPanel: the filter select's default option.
    expect(
      screen.getByRole("option", { name: /borderline/i }),
    ).toBeInTheDocument();

    // The auto-eval is deliberately delayed past the hydration race, so
    // wait out that window before asserting it never fired.
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(api.runLabEval).not.toHaveBeenCalled();
  });

  it("walks the corpus with prev/next preserving ?match=", async () => {
    const slugs = ["fixture-alpha", "fixture-bravo", "fixture-charlie"];
    vi.mocked(api.listLabFixtures).mockResolvedValue(slugs.map(record));
    vi.mocked(api.getLastLabRun).mockResolvedValue(runWith("fixture-bravo"));

    renderDetail("fixture-bravo", "?match=m-1");

    const next = await screen.findByRole("link", { name: /next fixture/i });
    expect(next).toHaveAttribute("href", "/dev/corpus/fixture-charlie?match=m-1");
    expect(
      screen.getByRole("link", { name: /previous fixture/i }),
    ).toHaveAttribute("href", "/dev/corpus/fixture-alpha?match=m-1");
  });
});
