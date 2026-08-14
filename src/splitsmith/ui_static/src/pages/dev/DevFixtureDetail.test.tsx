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
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import { api, type LabEvalRun, type LabFixtureRecord } from "@/lib/api";
import { DevFixtureDetail } from "@/pages/dev/DevFixtureDetail";

vi.mock("@/components/Waveform", () => ({ Waveform: () => null }));
vi.mock("@/components/lab/ZoomedWaveform", () => ({ ZoomedWaveform: () => null }));
// Stubbed for jsdom (no AudioContext), but the stub keeps the one prop
// that decides whether the page makes noise.
vi.mock("@/components/lab/SnippetPlayer", () => ({
  SnippetPlayer: ({ playing }: { playing: boolean }) => (
    <div data-testid="snippet" data-playing={String(playing)} />
  ),
}));

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
      applyLabLabels: vi.fn(),
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

/** #1 sits in the step-through panel's default "borderline" filter
 *  (1-3 votes); #2 is unanimous, so the panel filters it out. Selecting
 *  #2 in the candidate table models the selection source the panel does
 *  not own. */
function twoCandidates() {
  return [candidate(1), candidate(2, { vote_total: 4, vote_c: 1, ensemble_score: 4 })];
}

function runWith(slug: string, candidates = twoCandidates()): LabEvalRun {
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
          candidates,
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

  it("holds the scoped eval until last-run hydration settles", async () => {
    // A slow /api/lab/last-run must not let the auto-eval fire with the
    // stale DEFAULT_CONFIG -- a scoped eval under a config hash that
    // differs from the cached run's REPLACES the tuned universe. The
    // eval is gated on the hook's hydrated flag, not a grace timer, so
    // however slow the fetch is, no eval fires before it settles.
    vi.mocked(api.listLabFixtures).mockResolvedValue([record(SLUG)]);
    vi.mocked(api.getLastLabRun).mockReturnValue(new Promise(() => {}));

    renderDetail(SLUG);

    await screen.findByRole("heading", { name: SLUG });
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(api.runLabEval).not.toHaveBeenCalled();
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

    // Covered fixture: the hydrated run satisfies the page, so the
    // auto-eval must never fire (a job per navigation otherwise).
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(api.runLabEval).not.toHaveBeenCalled();
  });

  it("does not start audio playback on arrival", async () => {
    // The labeling panel is on screen from the first paint, and getting
    // here is a user gesture (a corpus row click), so the AudioContext
    // is resumable -- an auto-playing looped gunshot snippet would greet
    // whoever opened the fixture. Legacy gated this behind an explicit
    // "Step through" toggle; this page has no such gate, so playback has
    // to start paused.
    vi.mocked(api.listLabFixtures).mockResolvedValue([record(SLUG)]);
    vi.mocked(api.getLastLabRun).mockResolvedValue(runWith(SLUG));

    renderDetail(SLUG);

    expect(await screen.findByTestId("snippet")).toHaveAttribute(
      "data-playing",
      "false",
    );
  });

  it("keeps the operator's selection across a label save", async () => {
    // A save returns a run with fresh candidate objects, which rebuilds
    // the step-through panel's filtered list. Candidate #2 is outside
    // that list (unanimous, so the default borderline filter drops it),
    // which is exactly the case where the panel used to yank the
    // selection back to the head of its own list mid-labeling.
    vi.mocked(api.listLabFixtures).mockResolvedValue([record(SLUG)]);
    vi.mocked(api.getLastLabRun).mockResolvedValue(runWith(SLUG));
    vi.mocked(api.applyLabLabels).mockResolvedValue({
      path: `/fixtures/${SLUG}.json`,
      counts: { paper: 1 },
      run: runWith(SLUG, [
        candidate(1),
        candidate(2, {
          vote_total: 4,
          vote_c: 1,
          ensemble_score: 4,
          subclass: "paper",
        }),
      ]),
    });

    renderDetail(SLUG);
    await screen.findByText(/candidates \(2\)/i);

    const row = document.querySelector('[data-cn="2"]') as HTMLElement;
    await userEvent.click(row);
    expect(screen.getByText(/row #2 selected/i)).toBeInTheDocument();

    // "p" -> subclass paper on the selected truth-positive candidate.
    await userEvent.keyboard("p");
    await waitFor(() => expect(api.applyLabLabels).toHaveBeenCalled());
    // The relabeled run has landed once the row's dropdown shows it.
    await waitFor(() =>
      expect(
        within(document.querySelector('[data-cn="2"]') as HTMLElement).getByRole(
          "combobox",
        ),
      ).toHaveValue("paper"),
    );

    expect(screen.getByText(/row #2 selected/i)).toBeInTheDocument();
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
