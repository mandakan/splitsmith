/**
 * ShareCameras -- which camera each shooter starts on for share-link
 * viewers. Two levels, one look:
 *
 * - DefaultCameras: the shooters' saved defaults (``compare_camera``),
 *   which every link without its own choice opens on, as do the Compare
 *   grid and the export grid. Saved through the compare-camera route, a
 *   REVIEW write, so the hosted copy of a desktop match can set it.
 * - LinkCameras: one link's own choice per shooter (``ShareInfo.cameras``),
 *   with "Default" to follow the saved one. Its line under the link says
 *   what the link opens on.
 *
 * Viewers can always switch while watching. Each choice saves on the spot.
 * The derivation lives in lib/shareCameras.
 */
import { useState } from "react";

import { Field } from "@/components/ui/Field";
import { Label } from "@/components/ui/Label";
import { Segmented } from "@/components/ui/Segmented";
import { ApiError, api, type ShareInfo } from "@/lib/api";
import {
  DEFAULT,
  PRIMARY,
  allChoices,
  applyToAll,
  everyoneToPrimary,
  linkEveryone,
  linkValue,
  linkWith,
  mountLabel,
  opensOn,
} from "@/lib/shareCameras";
import type { ShareCameraData, ShooterRow } from "@/lib/useShareCameraData";

const MIXED = "mixed";
const opts = (values: string[]) => values.map((v) => ({ value: v, label: v === DEFAULT ? "Default" : mountLabel(v) }));

/** The rows themselves: an Everyone row when there is more than one
 *  shooter, then one per shooter, then the outcome line. */
function CameraRows({
  rows,
  valueOf,
  extra,
  onOne,
  onEveryone,
  saving,
  status,
  error,
}: {
  rows: ShooterRow[];
  valueOf: (row: ShooterRow) => string;
  /** An option offered first on every row ("Default" for a link). */
  extra?: string;
  onOne: (row: ShooterRow, value: string) => void;
  onEveryone: (value: string) => void;
  saving: boolean;
  status: string | null;
  error: string | null;
}) {
  const values = rows.map(valueOf);
  const common = values.every((v) => v === values[0]) ? values[0] : MIXED;
  const lead = extra ? [extra] : [];
  return (
    <>
      <div className="rounded-md border border-rule">
        {rows.length > 1 ? (
          <Field label="Everyone">
            <Segmented
              label="Everyone starts on"
              value={common}
              options={opts([...lead, ...allChoices(rows)])}
              onChange={onEveryone}
              disabled={saving}
            />
          </Field>
        ) : null}
        {rows.map((row) => (
          <Field key={row.slug} label={row.name}>
            <Segmented
              label={`${row.name} starts on`}
              value={valueOf(row)}
              options={opts([...lead, ...row.choices])}
              onChange={(v) => onOne(row, v)}
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
    </>
  );
}

function withoutLine(without: string[], value: string): string {
  if (without.length === 0) return "";
  const has = without.length === 1 ? "has" : "have";
  const keeps = without.length === 1 ? "keeps" : "keep";
  return ` ${without.join(", ")} ${has} no ${mountLabel(value).toLowerCase()} and ${keeps} their camera.`;
}

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.detail : "Could not save the camera";
}

/** The saved defaults: what every link without its own cameras opens on. */
export function DefaultCameras({ data }: { data: ShareCameraData }) {
  const { rows, setDefault } = data;
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  if (rows === null) return <p className="text-sm text-muted">Reading cameras...</p>;
  if (rows.length === 0) return null;

  async function save(changes: { slug: string; selector: string }[], done: string) {
    setSaving(true);
    setError(null);
    try {
      for (const c of changes) {
        await api.setCompareCamera(c.slug, c.selector === PRIMARY ? null : c.selector);
        setDefault(c.slug, c.selector);
      }
      setStatus(done);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-labelledby="share-cameras-title" className="space-y-2">
      <div>
        <span id="share-cameras-title">
          <Label>Default cameras</Label>
        </span>
        <p className="mt-1 text-sm text-muted">
          Links without their own cameras open on these, on every stage. Viewers can still switch while watching.
        </p>
      </div>
      <CameraRows
        rows={rows}
        valueOf={(r) => r.defaultValue}
        saving={saving}
        status={status}
        error={error}
        onOne={(row, v) => void save([{ slug: row.slug, selector: v }], `${row.name} starts on ${mountLabel(v)}.`)}
        onEveryone={(v) => {
          if (v === PRIMARY) return void save(everyoneToPrimary(rows), "Everyone on their primary camera.");
          const { changes, without } = applyToAll(rows, v);
          void save(changes, `Everyone on ${mountLabel(v)}.${withoutLine(without, v)}`);
        }}
      />
    </section>
  );
}

/** One link's cameras: the line saying what it opens on, and its editor. */
export function LinkCameras({
  share,
  data,
  onSaved,
}: {
  share: ShareInfo;
  data: ShareCameraData;
  onSaved: (share: ShareInfo) => void;
}) {
  const { rows } = data;
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  if (!rows || rows.length === 0) return null;
  const link = share.cameras ?? null;
  const line = opensOn(rows, link)
    .map((o) => `${o.name} ${o.label}`)
    .join(" · ");

  async function save(cameras: Record<string, string> | null, done: string) {
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.setShareCameras(share.id, cameras));
      setStatus(done);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 text-sm text-muted">
          {link ? "Opens on: " : "Opens on the default cameras: "}
          <span className="text-ink-2">{line}</span>
        </p>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="flex-none text-sm text-ink-2 underline-offset-2 hover:text-ink hover:underline"
        >
          {open ? "Done" : "Cameras"}
        </button>
      </div>
      {open ? (
        <CameraRows
          rows={rows}
          valueOf={(r) => linkValue(link, r.slug)}
          extra={DEFAULT}
          saving={saving}
          status={status}
          error={error}
          onOne={(row, v) =>
            void save(
              linkWith(link, row.slug, v),
              v === DEFAULT ? `${row.name} follows the default.` : `${row.name} starts on ${mountLabel(v)}.`,
            )
          }
          onEveryone={(v) => {
            const { cameras, without } = linkEveryone(link, rows, v);
            void save(cameras, v === DEFAULT ? "This link follows the defaults." : `Everyone on ${mountLabel(v)}.${withoutLine(without, v)}`);
          }}
        />
      ) : null}
    </div>
  );
}
