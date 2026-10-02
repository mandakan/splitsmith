/**
 * ShareCameras -- the share dialog's "Cameras" section: which angle each
 * shooter starts on for everyone watching the match's share links (and the
 * Compare grid and the export grid, which read the same saved default).
 * Viewers can still switch while watching. One row per shooter with more
 * than one camera, plus an "Everyone" row that sets them all at once and
 * names any shooter without that camera. Each choice saves on the spot
 * through the compare-camera route, a REVIEW write, so it works on the
 * hosted copy of a desktop match and syncs back. Derivation lives in
 * lib/shareCameras.
 */
import { useEffect, useState } from "react";

import { Field } from "@/components/ui/Field";
import { Label } from "@/components/ui/Label";
import { Segmented } from "@/components/ui/Segmented";
import { ApiError, api } from "@/lib/api";
import {
  PRIMARY,
  allChoices,
  applyToAll,
  everyoneToPrimary,
  cameraChoices,
  currentChoice,
  mountLabel,
  type ShooterCameras,
} from "@/lib/shareCameras";

interface Row extends ShooterCameras {
  value: string;
}

const MIXED = "mixed";

export function ShareCameras() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const { shooters } = await api.listMatchShooters();
        const loaded = await Promise.all(
          shooters.map(async (s) => {
            const choices = cameraChoices(s.cameras ?? []);
            const project = choices.length > 1 ? await api.getProject(s.slug).catch(() => null) : null;
            return {
              slug: s.slug,
              name: s.name,
              choices,
              value: currentChoice(project?.compare_camera, choices, s.cameras ?? []),
            };
          }),
        );
        if (alive) setRows(loaded.filter((r) => r.choices.length > 1));
      } catch {
        if (alive) setRows([]);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (rows === null) return <p className="text-sm text-muted">Reading cameras...</p>;
  if (rows.length === 0) return null;

  async function save(changes: { slug: string; selector: string }[]): Promise<boolean> {
    setSaving(true);
    setError(null);
    try {
      for (const c of changes) {
        await api.setCompareCamera(c.slug, c.selector === PRIMARY ? null : c.selector);
        setRows((prev) => prev?.map((r) => (r.slug === c.slug ? { ...r, value: c.selector } : r)) ?? prev);
      }
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Could not save the camera");
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function setOne(row: Row, selector: string) {
    if (await save([{ slug: row.slug, selector }])) setStatus(`${row.name} starts on ${mountLabel(selector)}.`);
  }

  async function setEveryone(selector: string) {
    if (!rows) return;
    if (selector === PRIMARY) {
      if (await save(everyoneToPrimary(rows))) setStatus("Everyone on their primary camera.");
      return;
    }
    const { changes, without } = applyToAll(rows, selector);
    if (!(await save(changes))) return;
    const rest = without.length > 0 ? ` ${without.join(", ")} ${without.length === 1 ? "has" : "have"} no ${mountLabel(selector).toLowerCase()} and keep${without.length === 1 ? "s" : ""} their camera.` : "";
    setStatus(`Everyone on ${mountLabel(selector)}.${rest}`);
  }

  const common = rows.every((r) => r.value === rows[0].value) ? rows[0].value : MIXED;
  const options = (values: string[]) => values.map((v) => ({ value: v, label: mountLabel(v) }));

  return (
    <section aria-labelledby="share-cameras-title" className="space-y-2">
      <div>
        <span id="share-cameras-title">
          <Label>Cameras</Label>
        </span>
        <p className="mt-1 text-sm text-muted">
          Where viewers start, on every stage. They can still switch while watching.
        </p>
      </div>
      <div className="rounded-md border border-rule">
        {rows.length > 1 ? (
          <Field label="Everyone">
            <Segmented
              label="Everyone starts on"
              value={common}
              options={options(allChoices(rows))}
              onChange={(v) => void setEveryone(v)}
              disabled={saving}
            />
          </Field>
        ) : null}
        {rows.map((row) => (
          <Field key={row.slug} label={row.name}>
            <Segmented
              label={`${row.name} starts on`}
              value={row.value}
              options={options(row.choices)}
              onChange={(v) => void setOne(row, v)}
              disabled={saving}
            />
          </Field>
        ))}
      </div>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : status ? (
        <p role="status" className="text-sm text-muted">
          {status}
        </p>
      ) : null}
    </section>
  );
}
