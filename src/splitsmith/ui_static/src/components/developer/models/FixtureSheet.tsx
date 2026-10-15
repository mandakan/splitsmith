/**
 * FixtureSheet -- one fixture's place in the models: its recording, its
 * status against the shipped build, the folds that hold it out, and the
 * beep ranker's verdict, with a way into the editor.
 */
import { Link } from "react-router-dom";

import { Chip, Label } from "@/components/ui";
import { Sheet } from "@/components/ui/Sheet";
import {
  STATUS_LABEL,
  angleOf,
  fixtureStatus,
  matchKey,
  type ModelMapFixture,
  type VoterCHead,
} from "@/lib/modelMap";

const HELD_OUT_TEXT = {
  right: "Picked the beep with this match held out.",
  wrong: "Picked another candidate with this match held out.",
  unreachable: "The beep is not among its candidates, so no model can pick it.",
} as const;

export function FixtureSheet({
  fixture,
  heads,
  onClose,
}: {
  fixture: ModelMapFixture | null;
  heads: VoterCHead[];
  onClose: () => void;
}) {
  const f = fixture;
  const head = f?.voter_c ? heads.find((h) => h.camera_class === f.voter_c) : undefined;
  return (
    <Sheet open={f !== null} onClose={onClose} label="Fixture">
      {f ? (
        <div className="flex h-full flex-col gap-5 overflow-y-auto p-5">
          <div className="flex flex-col gap-1">
            <Label>Fixture</Label>
            <span className="break-all font-mono text-md text-ink">{f.stem}</span>
            <div className="mt-1 flex flex-wrap gap-1.5">
              <Chip tick={f.voter_c ? "fire" : "muted"}>{STATUS_LABEL[fixtureStatus(f)]}</Chip>
              <Chip>{f.camera_class}</Chip>
              <Chip>{angleOf(f) === "primary" ? "Primary angle" : "Second camera"}</Chip>
              {f.wrong_clip ? <Chip tone="warn">Wrong video clip</Chip> : null}
              {!f.has_wav ? <Chip tone="warn">WAV not fetched</Chip> : null}
            </div>
          </div>

          <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-1.5 text-sm">
            <dt className="text-muted">Match</dt>
            <dd className="text-ink-2">{matchKey(f)}</dd>
            <dt className="text-muted">Stage</dt>
            <dd className="numeral text-ink-2">{f.stage ?? "--"}</dd>
            <dt className="text-muted">Shooter</dt>
            <dd className="font-mono text-ink-2">{f.shooter_id ?? "--"}</dd>
            <dt className="text-muted">Camera</dt>
            <dd className="text-ink-2">{f.camera ?? "--"}</dd>
            <dt className="text-muted">Shots</dt>
            <dd className="numeral text-ink-2">
              {f.n_shots}
              {f.expected_rounds ? <span className="text-muted"> ({f.expected_rounds} rounds on the stage)</span> : null}
            </dd>
            <dt className="text-muted">Review</dt>
            <dd className="text-ink-2">{f.review_status === "needs_review" ? "Awaiting review" : "Reviewed"}</dd>
          </dl>

          <div className="flex flex-col gap-1.5">
            <Label>Voter C</Label>
            {head ? (
              <p className="text-sm text-ink-2">
                One of the {head.n_fixtures} fixtures the {head.camera_class} classifier was trained on. Its threshold{" "}
                <span className="numeral">{head.voter_c_threshold?.toFixed(4)}</span> was picked over all of them.
              </p>
            ) : null}
            {f.superseded_by ? (
              <p className="text-sm text-ink-2">
                Replaced by <span className="break-all font-mono">{f.superseded_by}</span>
                {head ? ", so the next retrain leaves it out." : "; training leaves it out."}
              </p>
            ) : head ? null : (
              <p className="text-sm text-ink-2">
                Not in this build. It joins the {f.camera_class} classifier at the next retrain
                {f.review_status === "needs_review" ? ", once reviewed (or now, unless the build runs reviewed only)" : ""}.
              </p>
            )}
          </div>

          {f.voter_c ? (
            <div className="flex flex-col gap-1.5">
              <Label>Held out in</Label>
              <ul className="flex flex-col gap-1 text-sm text-ink-2">
                <li>By candidate: its candidates spread over all five folds.</li>
                <li>
                  By stage: the fold that holds <span className="font-mono">{f.stage_group}</span>, with every other
                  recording of that stage.
                </li>
                <li>
                  Leave match out: the <span className="font-mono">{f.match_group}</span> fold.
                </li>
              </ul>
            </div>
          ) : null}

          <div className="flex flex-col gap-1.5">
            <Label>Beep ranker</Label>
            {f.beep_ranker ? (
              <>
                <p className="text-sm text-ink-2">
                  {f.beep_ranker.held_out ? HELD_OUT_TEXT[f.beep_ranker.held_out] : "In its corpus, not scored."}
                </p>
                {f.beep_ranker.tags.length ? (
                  <div className="flex flex-wrap gap-1.5">
                    {f.beep_ranker.tags.map((t) => (
                      <Chip key={t}>{t}</Chip>
                    ))}
                  </div>
                ) : null}
              </>
            ) : (
              <p className="text-sm text-muted">Not in the beep calibration manifest.</p>
            )}
          </div>

          <div className="mt-auto flex gap-3 border-t border-rule pt-4">
            <Link to={`/dev/review/${encodeURIComponent(f.stem)}`} className="text-sm text-ink hover:underline">
              Open in the editor
            </Link>
          </div>
        </div>
      ) : null}
    </Sheet>
  );
}
