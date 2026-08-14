export function LabelBreakdown({
  fpByReason,
  positivesBySubclass,
}: {
  fpByReason: Record<string, number>;
  positivesBySubclass: Record<string, number>;
}) {
  const fpEntries = Object.entries(fpByReason).filter(([, n]) => n > 0);
  const subEntries = Object.entries(positivesBySubclass).filter(([, n]) => n > 0);
  if (fpEntries.length === 0 && subEntries.length === 0) {
    return null;
  }
  return (
    <div className="rounded border border-rule/60 p-3 text-xs">
      <div className="mb-2 font-semibold uppercase tracking-wide text-muted">
        Label breakdown
      </div>
      {fpEntries.length > 0 && (
        <div className="mb-2">
          <div className="text-[10px] uppercase text-orange-500">false positives by class</div>
          <ul className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono">
            {fpEntries
              .sort((a, b) => b[1] - a[1])
              .map(([k, n]) => (
                <li key={k} className="flex justify-between">
                  <span>{k}</span>
                  <span className="text-muted">{n}</span>
                </li>
              ))}
          </ul>
        </div>
      )}
      {subEntries.length > 0 && (
        <div>
          <div className="text-[10px] uppercase text-emerald-600">positives by subclass</div>
          <ul className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono">
            {subEntries
              .sort((a, b) => b[1] - a[1])
              .map(([k, n]) => (
                <li key={k} className="flex justify-between">
                  <span>{k}</span>
                  <span className="text-muted">{n}</span>
                </li>
              ))}
          </ul>
        </div>
      )}
    </div>
  );
}
