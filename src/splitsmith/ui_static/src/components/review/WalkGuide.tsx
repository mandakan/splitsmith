/**
 * The walk's reference, in the page: what a decision is, the keys, the
 * placement checklist and real examples (the figures from the shot-time
 * guide in docs/METHODOLOGY.md). Opened from the walk's header or with ?.
 */

import clean from "@/assets/shot-time/01-clean.png";
import go3s from "@/assets/shot-time/02-go3s-compressed.png";
import noisy from "@/assets/shot-time/03-noisy.png";
import separated from "@/assets/shot-time/04-separated-lead-in.png";
import continuous from "@/assets/shot-time/05-continuous-lead-in.png";
import earlier from "@/assets/shot-time/06-earlier-sound.png";
import swing from "@/assets/shot-time/07-snap-late.png";

const EXAMPLES: Array<{ src: string; caption: string }> = [
  { src: clean, caption: "A clean shot: the burst leaves a quiet background at one sharp point." },
  {
    src: go3s,
    caption: "A compressed camera (GO 3S) dips and swings inside one burst. The time is where the burst starts.",
  },
  {
    src: noisy,
    caption: "A noisy background: not the first wiggle a little larger than the rest, the point where the burst clearly rises.",
  },
  { src: separated, caption: "A sound that fades back before the blast (here a tone) is not the shot: time the burst." },
  { src: continuous, caption: "A lead-in that grows straight into the burst is part of the rise: time where it starts." },
  {
    src: earlier,
    caption: "An earlier, separate sound (an echo, another bay's shot) with background between it and the burst.",
  },
  {
    src: swing,
    caption: "If F lands on a swing inside the burst (dashed), place the shot by eye at the start with a click.",
  },
];

const KEYS: Array<[string, string]> = [
  ["Enter", "Correct as shown: confirm and go to the next stop"],
  ["S", "It is a shot (a rejected candidate or an unmarked sound becomes one)"],
  ["X", "It is not a shot"],
  ["F", "Put the shot on the rise foot (the green line): the rule, re-timed if the rule changes"],
  ["Left / Right", "Move the shot 1 ms (Shift: 5 ms); off the rule it is your placement"],
  ["Click", "Place the shot exactly there, in either strip (your placement)"],
  ["Space", "Listen: half a second before the stop to 0.7 s after; again to pause"],
  ["L", "Loop: repeat each stop's sound, and start the next one by itself after Enter"],
  ["Backspace", "Back to the previous stop"],
  ["?", "Show or hide this guide"],
  ["Cmd+Z", "Undo"],
];

export function WalkGuide({ onClose }: { onClose: () => void }) {
  return (
    <section aria-label="Review guide" className="space-y-4 rounded-md border border-border p-4 text-sm">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-base font-semibold text-ink">How to review a fixture</h2>
        <button type="button" className="text-muted hover:text-ink" onClick={onClose}>
          Hide guide (?)
        </button>
      </div>

      <div className="space-y-4">
        <div className="space-y-3">
          <p className="text-ink">
            The goal: every real shot is kept, each sits on its onset, and nothing else is kept. The onset
            is the <em>rise foot</em>: walking back from the shot&apos;s peak, the last moment the level is
            still clearly above the background just before it (about a twentieth of the peak).
          </p>
          <p className="text-ink">
            What only you can tell is <strong>which sounds are shots</strong>. The exact millisecond is a
            rule applied to the audio, and the rule may still change (a shot timer fires differently on a
            weak lead-in). A shot left on the rule is re-timed from its sound if it does; one you placed
            yourself is kept as you placed it, and listed for a second look. So use <kbd>F</kbd> and
            override only what the rule gets wrong.
          </p>
          <p className="text-ink">
            On a fixture a person labelled, whose shot count matches the stage&apos;s rounds, the walk
            visits the kept shots; the whole stage at the bottom shows everything else. On one snapped from
            another camera, or with a count that is off, it visits every candidate, every kept shot and
            every loud sound nobody marked; the switch at the top changes it. Stops come in time
            order. Decide each one, then <kbd>Enter</kbd>. Decisions save as you go; reopening the fixture
            resumes at the first stop you have not confirmed. At the end, check the shot count and sign off.
          </p>
          <div>
            <h3 className="mb-1 font-semibold text-ink">At each stop</h3>
            <ol className="list-decimal space-y-1 pl-5 text-ink">
              <li>
                <strong>Is it a shot?</strong> A burst that rises sharply out of the background. If unsure,
                listen (<kbd>Space</kbd>): another bay&apos;s shot sounds distant and duller; an echo sits in
                a louder sound&apos;s tail.
              </li>
              <li>
                <strong>Put it on the rule: press <kbd>F</kbd>.</strong> F seats the shot on the rise foot,
                and the line next to the time says <em>on the rise foot</em>. Check it against the waveform:
                the burst&apos;s rise, followed backwards to where it leaves the background. Only when F is
                clearly wrong (it stops on a swing inside the burst, or on a different sound) place it
                yourself with a click or the arrows.
              </li>
              <li>
                <strong>Same rule on every shot</strong>, loud or quiet: splits are only right if every shot is
                timed at the same point of its rise.
              </li>
              <li>
                <strong>When you cannot tell</strong>, put your best guess on the onset. A sound you can
                neither see nor hear as a shot is not a shot.
              </li>
            </ol>
          </div>
          <div>
            <h3 className="mb-1 font-semibold text-ink">Fixtures snapped from another camera</h3>
            <ul className="list-disc space-y-1 pl-5 text-ink">
              <li>
                <strong>Shots in the tail.</strong> The snap can put a stage&apos;s shots 50 to 150 ms late,
                in the tail of the real ones. The real shot then shows as a rejected candidate or an unmarked
                sound just before: make that the shot (<kbd>S</kbd>) and reject the one in the tail (
                <kbd>X</kbd>) at its stop. The warnings point this out.
              </li>
              <li>
                <strong>Two shots on one sound.</strong> &quot;Another shot N ms after&quot;: keep the one on the
                onset, reject the other.
              </li>
              <li>
                <strong>The count.</strong> The end screen compares kept shots with the stage&apos;s rounds.
                Extra shots and makeups happen; a missing shot is usually a sound you passed as not a shot.
              </li>
            </ul>
          </div>
          <div>
            <h3 className="mb-1 font-semibold text-ink">Keys</h3>
            <table className="w-full text-left">
              <tbody>
                {KEYS.map(([k, what]) => (
                  <tr key={k} className="border-t border-border">
                    <td className="py-0.5 pr-3 font-mono whitespace-nowrap text-ink">{k}</td>
                    <td className="py-0.5 text-muted">{what}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-1 text-muted">
              In the strips: the red line is the stop, solid when it is a shot; white lines are other shots,
              dashed grey ones rejected candidates; green is the rise foot.
            </p>
            <p className="mt-1 text-muted">
              The two close-ups draw the wave itself, each scaled to fill its box (the label says how loud
              the window really is against the stage&apos;s shots); the stage strip is drawn at the
              stage&apos;s shot level, so shots compare with each other. The 80 ms one is the window of the
              examples below, with the same ms ticks.
            </p>
            <p className="mt-1 text-muted">
              Times are kept to the millisecond, and the rise foot is the start of the first millisecond
              that rises: the green and red lines can sit up to 1 ms before the first sample that visibly
              swings. That is the grid, not a misplacement; splits are shown to 0.01 s.
            </p>
          </div>
        </div>

        <div className="space-y-3">
          <h3 className="font-semibold text-ink">Examples (real fixture audio, the green line is the shot time)</h3>
          {EXAMPLES.map((e) => (
            <figure key={e.src} className="space-y-1">
              <a href={e.src} target="_blank" rel="noreferrer" title="Open full size">
                <img src={e.src} alt={e.caption} className="w-full cursor-zoom-in rounded-sm bg-white" loading="lazy" />
              </a>
              <figcaption className="text-muted">{e.caption}</figcaption>
            </figure>
          ))}
        </div>
      </div>
    </section>
  );
}
