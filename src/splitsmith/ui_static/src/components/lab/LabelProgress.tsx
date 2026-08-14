import { type LabEvalFixture } from "@/lib/api";

/** Per-fixture labeling progress: count of candidates carrying any
 *  ``reason`` or ``subclass`` over the total candidate universe.
 *  Rough by design -- not every candidate is worth labeling, but the
 *  ratio still ranks fixtures by how much labeling effort has gone in. */
export function LabelProgress({ fixture }: { fixture: LabEvalFixture }) {
  const total = fixture.candidates.length;
  const labeled = fixture.candidates.filter((c) => c.reason || c.subclass).length;
  if (total === 0) return <>--</>;
  const pct = Math.round((labeled / total) * 100);
  return (
    <span title={`${labeled} of ${total} candidates carry a label`}>
      {labeled}/{total} ({pct}%)
    </span>
  );
}
