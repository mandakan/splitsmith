/**
 * StepThroughPanel's two opt-in props, and the legacy defaults they
 * must not disturb.
 *
 * ``autoPlay`` -- the panel loops a gunshot snippet as soon as a
 * candidate is selected. That is correct behind legacy Lab.tsx's
 * explicit "Step through" toggle (the operator asked for audio) and
 * wrong when the panel is permanently on screen, as it is on
 * /dev/corpus/:slug: opening a fixture is a user gesture, so the
 * AudioContext resumes and the page starts making noise on arrival.
 *
 * ``preserveSelection`` -- the panel re-snaps the selection to the head
 * of its own filtered list whenever that list is rebuilt, and a label
 * save rebuilds it (the run comes back with fresh candidate objects).
 * With a candidate table beside the panel feeding it selections, that
 * means every save moves the operator off the row they just labeled.
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
function fixtureWith(candidates = [candidate(1, 2), candidate(2, 4)]): LabEvalFixture {
  return {
    slug: "fixture-alpha",
    audit_path: "/fixtures/fixture-alpha.json",
    audio_path: "/fixtures/fixture-alpha.wav",
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

describe("StepThroughPanel autoPlay", () => {
  it("plays on selection by default -- legacy Lab.tsx semantics", () => {
    renderPanel();
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-playing", "true");
  });

  it("starts silent when autoPlay is false", () => {
    renderPanel({ autoPlay: false });
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-playing", "false");
  });

  it("plays once the operator asks for it, then stays armed", async () => {
    const { rerender, onSelect } = renderPanel({ autoPlay: false });
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
        autoPlay={false}
        preserveSelection
      />,
    );
    expect(screen.getByTestId("snippet")).toHaveAttribute("data-playing", "true");
  });
});

describe("StepThroughPanel preserveSelection", () => {
  it("re-snaps to the head of its own list by default -- legacy semantics", () => {
    const { onSelect } = renderPanel({ selectedCn: 2 });
    expect(onSelect).toHaveBeenCalledWith(1);
  });

  it("keeps a selection the filter excludes, across list rebuilds", () => {
    const onSelect = vi.fn();
    const props = {
      selectedCn: 2,
      onSelect,
      registerAdvancer: vi.fn(),
      savingLabel: null,
      onLabel: vi.fn(),
      preserveSelection: true,
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
    renderPanel({ selectedCn: 2, preserveSelection: true });
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
        preserveSelection
      />,
    );
    expect(onSelect).toHaveBeenCalledWith(1);
  });
});
