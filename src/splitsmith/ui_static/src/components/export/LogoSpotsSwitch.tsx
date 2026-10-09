/**
 * The previews' "Show where logos go" switch (lib/logoSpots): explains what
 * the dashed squares are and that a video never shows them.
 */
export function LogoSpotsSwitch({ on, onChange }: { on: boolean; onChange: (on: boolean) => void }) {
  return (
    <label className="flex items-start gap-2 px-3.5 py-2 text-sm text-muted">
      <input
        type="checkbox"
        checked={on}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 accent-[var(--color-ink)]"
      />
      <span>
        <span className="text-ink-2">Show where logos go.</span> A dashed square marks each logo spot that has no
        logo yet: the shooter&apos;s, your brand and the event&apos;s. Only the preview shows them, never the video.
      </span>
    </label>
  );
}
