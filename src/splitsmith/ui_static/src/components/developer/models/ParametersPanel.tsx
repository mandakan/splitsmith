/**
 * ParametersPanel -- what the shipped ensemble was built with: each voter
 * C head's thresholds and cross-validated figures, the GBDT's arguments,
 * the per-camera amplitude floors, the CLAP prompts and the model files.
 */
import { Label, Table, Td, Th, Tr } from "@/components/ui";
import { bytes, pct, type EnsembleModel } from "@/lib/modelMap";

export function ParametersPanel({ ensemble }: { ensemble: EnsembleModel }) {
  const heads = ensemble.heads;
  const row = (label: string, render: (i: number) => string) => (
    <Tr key={label}>
      <Td>{label}</Td>
      {heads.map((_, i) => (
        <Td key={i} kind="num">
          {render(i)}
        </Td>
      ))}
    </Tr>
  );
  const fixed = (v: number | null | undefined, d = 4) => (v === null || v === undefined ? "--" : v.toFixed(d));
  return (
    <section className="flex flex-col gap-3">
      <Label>Parameters</Label>
      <div className="grid gap-4 xl:grid-cols-2">
        <Table>
          <thead>
            <tr>
              <Th>Voter C head</Th>
              {heads.map((h) => (
                <Th key={h.camera_class} align="right">
                  {h.camera_class}
                </Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {row("Fixtures", (i) => String(heads[i].n_fixtures))}
            {row("Candidates", (i) => heads[i].n_candidates.toLocaleString())}
            {row("Shots among them", (i) => heads[i].n_positives.toLocaleString())}
            {row("Voter A floor", (i) => fixed(heads[i].voter_a_floor))}
            {row("Voter B threshold", (i) => fixed(heads[i].voter_b_threshold))}
            {row("Voter C threshold", (i) => fixed(heads[i].voter_c_threshold))}
            {row("CV precision %", (i) => pct(heads[i].cv?.precision))}
            {row("CV recall %", (i) => pct(heads[i].cv?.recall))}
            {row("CV F1 %", (i) => pct(heads[i].cv?.f1))}
            {row("TP / FP / FN", (i) => {
              const cv = heads[i].cv;
              return cv ? `${cv.tp} / ${cv.fp} / ${cv.fn}` : "--";
            })}
            {row("ONNX graph", (i) => bytes(heads[i].onnx_bytes))}
          </tbody>
        </Table>
        <div className="flex flex-col gap-4">
          <Table>
            <tbody>
              <Tr>
                <Td>Built</Td>
                <Td kind="num">{ensemble.built_at?.slice(0, 16).replace("T", " ") ?? "--"}</Td>
              </Tr>
              <Tr>
                <Td>Target recall (threshold pick)</Td>
                <Td kind="num">{pct(ensemble.target_recall, 0)} %</Td>
              </Tr>
              <Tr>
                <Td>Match tolerance</Td>
                <Td kind="num">{ensemble.tolerance_ms ?? "--"} ms</Td>
              </Tr>
              <Tr>
                <Td>Voter C features</Td>
                <Td kind="num">{ensemble.feature_dim ?? "--"}</Td>
              </Tr>
              <Tr>
                <Td>Default camera class</Td>
                <Td kind="num">{ensemble.default_camera_class ?? "--"}</Td>
              </Tr>
              {Object.entries(ensemble.hyperparameters).map(([k, v]) => (
                <Tr key={k}>
                  <Td>
                    GBDT <span className="font-mono text-sm text-muted">{k}</span>
                  </Td>
                  <Td kind="num">{String(v)}</Td>
                </Tr>
              ))}
              {Object.entries(ensemble.amp_floor_by_camera_model).map(([k, v]) => (
                <Tr key={k}>
                  <Td>Amplitude floor, {k}</Td>
                  <Td kind="num">{v.toFixed(2)}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </div>
        <div className="flex flex-col gap-2">
          <Label>CLAP prompts</Label>
          <ul className="flex flex-col gap-1 rounded-[10px] border border-rule bg-surface p-3">
            {ensemble.clap_prompts.map((p) => {
              const shot = ensemble.clap_shot_prompts.includes(p);
              return (
                <li key={p} className="flex items-center gap-2 text-sm">
                  <i
                    aria-hidden
                    className="size-1.5 shrink-0 rounded-full"
                    style={{ background: shot ? "var(--color-done)" : "var(--color-whisper)" }}
                  />
                  <span className={shot ? "text-ink-2" : "text-muted"}>{p}</span>
                </li>
              );
            })}
          </ul>
          <p className="text-sm text-subtle">Green: the shot side of voter B's differential.</p>
        </div>
        <Table>
          <thead>
            <tr>
              <Th>Shared model</Th>
              <Th>sha256</Th>
              <Th align="right">Size</Th>
            </tr>
          </thead>
          <tbody>
            {ensemble.artifacts.map((a) => (
              <Tr key={a.name}>
                <Td kind="name">{a.filename}</Td>
                <Td>
                  <span className="font-mono text-sm text-muted" title={a.sha256}>
                    {a.sha256.slice(0, 12)}
                  </span>
                </Td>
                <Td kind="num">{bytes(a.size_bytes)}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </div>
    </section>
  );
}
