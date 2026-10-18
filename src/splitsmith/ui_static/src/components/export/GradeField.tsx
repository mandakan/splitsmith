/**
 * The Look group's Grade row: a tile per colour grade (None, the Look's own,
 * the presets, your LUTs), a strength slider while one grades, and on the
 * desktop "Add LUT" for a .cube of your own. The rules are ``lib/grades``.
 */
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/Field";
import { api, type GradeInfo, type LookInfo } from "@/lib/api";
import type { ExportSettings } from "@/lib/exportPresets";
import { gradeTiles, grades, selectedTile } from "@/lib/grades";
import { previewSrc } from "@/lib/looks";
import { cn } from "@/lib/utils";

export interface GradeFieldProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  looks: readonly LookInfo[];
  busy: boolean;
  /** Your own LUTs are files on the desktop: hosted lists presets only. */
  hosted: boolean;
}

export function GradeField({ settings, patch, looks, busy, hosted }: GradeFieldProps) {
  const [presets, setPresets] = useState<GradeInfo[]>([]);
  const [user, setUser] = useState<GradeInfo[]>([]);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    api
      .listGrades()
      .then((r) => live && setPresets(r.presets))
      .catch(() => undefined);
    if (!hosted) {
      api
        .listUserGrades()
        .then((r) => live && setUser(r.user))
        .catch(() => undefined);
    }
    return () => {
      live = false;
    };
  }, [hosted]);

  const tiles = gradeTiles(looks, settings.look, presets, user);
  const selected = selectedTile(tiles, settings.grade);
  const strength = Math.round((settings.gradeStrength ?? 1) * 100);

  async function upload(file: File) {
    setUploading(true);
    setUploadError(null);
    try {
      const added = await api.uploadGrade(file);
      setUser((rows) => (rows.some((r) => r.id === added.id) ? rows : [...rows, added]));
      patch({ grade: added.id });
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "That file could not be read as a LUT.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <Field
      label="Grade"
      help="A colour grade for the footage. The overlay, cards and logos keep their own colours."
    >
      <div className="flex flex-col gap-3">
        <div role="radiogroup" aria-label="Grade" className="flex flex-wrap gap-2">
          {tiles.map((tile) => {
            const checked = tile.id === selected;
            return (
              <button
                key={tile.id}
                type="button"
                role="radio"
                aria-checked={checked}
                disabled={busy}
                onClick={() => !checked && patch({ grade: tile.id })}
                className={cn(
                  "flex w-32 flex-col gap-1.5 rounded-md border p-1.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-led disabled:opacity-50",
                  checked ? "border-ink bg-surface-2" : "border-rule-strong hover:border-ink-2",
                )}
              >
                <img
                  src={previewSrc(tile.preview) ?? undefined}
                  alt=""
                  className="aspect-video w-full rounded-sm bg-surface-3 object-cover"
                />
                <span className="inline-flex items-center gap-1.5 text-sm text-ink-2">
                  {checked ? <i data-tick aria-hidden className="size-1.5 shrink-0 rounded-full bg-ink" /> : null}
                  {tile.label}
                </span>
              </button>
            );
          })}
        </div>
        {grades(selected) ? (
          <label className="flex items-center gap-3 text-md text-ink-2">
            <span>Strength</span>
            <input
              type="range"
              aria-label="Grade strength"
              min={0}
              max={100}
              step={5}
              value={strength}
              disabled={busy}
              onChange={(e) => patch({ gradeStrength: Number(e.target.value) / 100 })}
              className="w-48 accent-[var(--color-ink)]"
            />
            <span className="numeral w-12 text-right">{strength} %</span>
          </label>
        ) : null}
        {hosted ? null : (
          <div className="flex items-center gap-3">
            <input
              ref={input}
              type="file"
              accept=".cube"
              className="hidden"
              aria-label="LUT file"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) void upload(file);
              }}
            />
            <Button variant="default" size="sm" disabled={busy || uploading} onClick={() => input.current?.click()}>
              {uploading ? "Adding..." : "Add LUT"}
            </Button>
            {uploadError ? <span className="text-sm text-destructive">{uploadError}</span> : null}
          </div>
        )}
      </div>
    </Field>
  );
}
