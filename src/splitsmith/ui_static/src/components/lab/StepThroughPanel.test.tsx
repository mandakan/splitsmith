/**
 * StepThroughPanel's audio-arming and selection semantics (the
 * ``autoPlay`` / ``preserveSelection`` compat props for the deleted
 * legacy Lab page are gone -- #901 -- so these are now the panel's only
 * behavior, not an opt-in):
 *
 * Playback starts silent -- the panel is permanently on screen on
 * /dev/corpus/:slug, opening a fixture is a user gesture, so the
 * AudioContext resumes and an auto-playing loop would greet whoever
 * arrived. Once the operator starts playback the panel arms itself and
 * candidate changes auto-play; navigating to another fixture disarms
 * it again (a new fixture is a new labeling session).
 *
 * Selections from outside the panel's filter (the side-by-side
 * candidate table) are followed, and survive the list rebuild a label
 * save causes -- the panel falls back to the head of its list only
 * when the candidate is gone from the fixture entirely.
 *
 * jsdom has no AudioContext, so SnippetPlayer is stubbed -- but the stub
 * still surfaces the ``playing`` prop, which is exactly what decides
 * whether sound comes out.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { StepThroughPanel } from "@/components/lab/StepThroughPanel";
import { type LabEvalFixture } from "@/lib/api";

vi.mock("@/components/lab/SnippetPlayer", () => ({
  SnippetPlayer: ({
    playing,
    candidate,
  }: {
    playing: boolean;
    candidate: { candidate_number: number };
  }) => (
    <div
      data-testid="snippet"
      data-playing={String(playing)}
      data-cn={candidate.candidate_number}
    />
  ),
}));

function candidate(n: number, voteTotal: number): LabEvalFixture["candidates"][number] {
  return {
    candidate_number: n,
    time: 3 + n * 0.25,
    ms_after_beep: 1500 + n * 250,
    confidence: 0.4,
    peak_amplitude: 0.5,
    score_c: 0.6,
    clap_diff: 0.02,
    gunshot_prob: 0.7,
    vote_a: 1,
    vote_b: 1,
    vote_c: voteTotal > 2 ? 1 : 0,
    vote_total: voteTotal,
    apriori_boost: 0,
    ensemble_score: voteTotal,
    kept: true,
    truth: 1,
    matched_shot_number: n,
    reason: null,
    subclass: null,
  };
}

/** #1 is "borderline" (1-3 votes) so the panel's default filter keeps
 *  it; #2 is unanimous, so the default filter excludes it. Selecting #2
 *  therefore models "the operator picked a row this panel filters out". */
function fixtureWith(
  candidates = [candidate(1, 2), candidate(2, 4)],
  slug = "fixture-alpha",
): LabEvalFixture {
  return {
    slug,
    audit_path: `/fixtures/${slug}.json`,
    audio_path: `/fixtures/${slug}.wav`,
    source_video: null,
    expected_rounds: 12,
    candidates,
    truth_times: [3.25, 3.5],
    metrics: {
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
    },
    audit_mtime: 1,
    audio_mtime: 1,
  };
}

function renderPanel(props: Partial<React.ComponentProps<typeof StepThroughPanel>> = {}) {
  const onSelect = vi.fn();
  const view = render(
    <StepThroughPanel
      fixture={fixtureWith()}
      selectedCn={1}
      onSelect={onSelect}
      registerAdvancer={vi.fn()}
      savingLabel={null}
      onLabel={vi.fn()}
      {...props}
    />,
  );
  return { ...view, onSelect };
}

describe("StepThroughPanel audio arming", () => {
  it("starts silent", () => {
    renderPanel();
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-playing", "false");
  });

  it("yields to external audio: the full-stage player silences the loop", async () => {
    const { rerender } = renderPanel({ externalAudioPlaying: false });
    await userEvent.keyboard(" ");
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-playing", "true");

    rerender(
      <StepThroughPanel
        fixture={fixtureWith()}
        selectedCn={1}
        onSelect={vi.fn()}
        registerAdvancer={vi.fn()}
        savingLabel={null}
        onLabel={vi.fn()}
        externalAudioPlaying
      />,
    );
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-playing", "false");
  });

  it("plays once the operator asks for it, then stays armed across candidates", async () => {
    const { rerender, onSelect } = renderPanel();
    await userEvent.keyboard(" ");
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-playing", "true");

    // Armed: stepping to the next candidate keeps playing, which is what
    // makes step-through feel continuous once it has been started.
    rerender(
      <StepThroughPanel
        fixture={fixtureWith()}
        selectedCn={2}
        onSelect={onSelect}
        registerAdvancer={vi.fn()}
        savingLabel={null}
        onLabel={vi.fn()}
      />,
    );
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-playing", "true");
  });

  it("disarms when the fixture changes -- prev/next must not arrive with sound", async () => {
    const { rerender, onSelect } = renderPanel();
    await userEvent.keyboard(" ");
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-playing", "true");

    // The panel stays mounted across prev/next navigation; a new slug
    // is a new labeling session and starts silent again.
    rerender(
      <StepThroughPanel
        fixture={fixtureWith(undefined, "fixture-bravo")}
        selectedCn={1}
        onSelect={onSelect}
        registerAdvancer={vi.fn()}
        savingLabel={null}
        onLabel={vi.fn()}
      />,
    );
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-playing", "false");
  });

  it("stays armed across a label save of the same fixture", async () => {
    const { rerender, onSelect } = renderPanel();
    await userEvent.keyboard(" ");

    // A save hands back fresh candidate objects under the same slug --
    // that is mid-session, not a navigation, so playback continues.
    rerender(
      <StepThroughPanel
        fixture={fixtureWith([candidate(1, 2), { ...candidate(2, 4), subclass: "paper" }])}
        selectedCn={1}
        onSelect={onSelect}
        registerAdvancer={vi.fn()}
        savingLabel={null}
        onLabel={vi.fn()}
      />,
    );
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-playing", "true");
  });
});

describe("StepThroughPanel selection", () => {
  it("keeps a selection the filter excludes, across list rebuilds", () => {
    const onSelect = vi.fn();
    const props = {
      selectedCn: 2,
      onSelect,
      registerAdvancer: vi.fn(),
      savingLabel: null,
      onLabel: vi.fn(),
    };
    const { rerender } = render(<StepThroughPanel fixture={fixtureWith()} {...props} />);
    expect(onSelect).not.toHaveBeenCalled();
    // The panel follows the off-list selection instead of blanking.
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-cn", "2");

    // A label save hands back a run with fresh candidate objects, which
    // rebuilds the filtered list. The selection must survive that.
    rerender(
      <StepThroughPanel
        fixture={fixtureWith([candidate(1, 2), { ...candidate(2, 4), subclass: "paper" }])}
        {...props}
      />,
    );
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-cn", "2");
  });

  it("shows -- (not 0) as queue position for an off-list selection", () => {
    // Candidate #2 is unanimous, so the default borderline filter
    // excludes it; the panel follows the selection but it has no
    // position in the queue -- "0 / 1" would claim it does.
    renderPanel({ selectedCn: 2 });
    expect(screen.getByText(/-- \/ 1/)).toBeInTheDocument();
    expect(screen.getByText(/selection is outside this filter/)).toBeInTheDocument();
  });

  it("still falls back when the selected candidate leaves the fixture", () => {
    const onSelect = vi.fn();
    render(
      <StepThroughPanel
        fixture={fixtureWith([candidate(1, 2)])}
        selectedCn={2}
        onSelect={onSelect}
        registerAdvancer={vi.fn()}
        savingLabel={null}
        onLabel={vi.fn()}
      />,
    );
    expect(onSelect).toHaveBeenCalledWith(1);
  });
});
