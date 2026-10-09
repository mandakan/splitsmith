/**
 * ExportWarnings -- what a finished export did not do, line by line: a card
 * a template's error left out, a transition that became a cut. A count
 * alone ("1 warning") left the user to guess which card went missing.
 */
export function ExportWarnings({ anomalies }: { anomalies: readonly string[] }) {
  if (anomalies.length === 0) return null;
  return (
    <ul aria-label="Warnings" className="mt-1.5 flex flex-col gap-1">
      {anomalies.map((a, i) => (
        <li key={`${i}-${a}`} className="text-sm text-muted">
          {a}
        </li>
      ))}
    </ul>
  );
}
