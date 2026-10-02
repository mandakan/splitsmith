/**
 * Output -- the technical half of the Export form (spec 2026-09-15 s1):
 * what file comes out and how it is encoded. The mode control itself
 * sits in the Section header so it stays reachable while the group is
 * folded; this is everything under it.
 */
import { SelectField } from "@/components/export/SelectField";
import { CamOptionsPanel } from "@/components/render/CamOptionsPanel";
import { Field } from "@/components/ui/Field";
import { Segmented } from "@/components/ui/Segmented";
import { READ_ONLY_MIRROR_MESSAGE, type GridFreeCell, type ShooterListEntry } from "@/lib/api";
import { gridCameraChoices, gridMultiCamShooters, type CameraChoice } from "@/lib/camOptions";
import { bareHint } from "@/lib/exportPlan";
import type { ExportSettings } from "@/lib/exportPresets";
import { mountLabel } from "@/lib/shareCameras";
import { CANVAS_CHOICES, gridFreeCells } from "@/pages/matchExportModel";

const FREE_CELL_OPTIONS: ReadonlyArray<{ value: GridFreeCell; label: string }> = [
  { value: "blank", label: "Blank" },
  { value: "stage", label: "Stage card" },
  { value: "splits", label: "Splits" },
  { value: "match", label: "Match card" },
];

const FREE_CELL_HELP: Record<GridFreeCell, string> = {
  blank: "The cell no shooter fills stays black.",
  stage: "The stage's number, name, rounds and targets, on every stage.",
  splits: "Each shooter's draw, average and best split on the stage, the best of each marked.",
  match: "The match name, date and the shooters, on every stage.",
};

const DESKTOP_RENDERS_MP4 = "Your desktop renders an MP4 and uploads it.";

export interface OutputGroupProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  editDenied: boolean;
  shooters: ShooterListEntry[];
  audioFrom: string;
  onAudioFrom: (slug: string) => void;
  cameraOptions: { value: string; label: string }[];
  compareCamera: string;
  onChangeCamera: (v: string) => void;
  secondaryCount: number;
  /** The cameras of the selected stages (``cameraChoices``). */
  camChoices: CameraChoice[];
  bareSelected: number;
  /** The linked desktop renders: always an MP4 with the YouTube preset. */
  onDesktop?: boolean;
}

export function OutputGroup({
  settings,
  patch,
  busy,
  editDenied,
  shooters,
  audioFrom,
  onAudioFrom,
  cameraOptions,
  compareCamera,
  onChangeCamera,
  secondaryCount,
  camChoices,
  bareSelected,
  onDesktop = false,
}: OutputGroupProps) {
  const { mode, outputFormat, includeOverlay, overlayCodec, youtube, camOptions } = settings;
  if (mode === "trims") {
    return (
      <Field
        label="Grid camera"
        help="Which of this shooter's cameras the compare grid uses. Saved on the shooter; the trims cover every camera on the stage."
      >
        <SelectField
          label="Camera for the grid"
          value={compareCamera}
          onChange={onChangeCamera}
          options={cameraOptions}
          disabled={editDenied}
          title={READ_ONLY_MIRROR_MESSAGE}
          className="w-full max-w-xs"
        />
      </Field>
    );
  }
  if (mode === "compare") {
    return (
      <>
        <Field label="Reference" help="Sets the frame rate from this shooter's footage; every shooter is in the mixed track.">
          <Segmented
            label="Reference shooter"
            value={audioFrom}
            onChange={onAudioFrom}
            options={shooters.map((s) => ({
              value: s.slug,
              label: s.name,
              tick: s.slug === audioFrom ? ("movement" as const) : undefined,
            }))}
          />
        </Field>
        <Field label="Canvas" help="1080p renders faster.">
          <Segmented
            label="Canvas"
            value={settings.canvas}
            onChange={(id) => patch({ canvas: id })}
            options={CANVAS_CHOICES.map((c) => ({ value: c.id, label: c.label }))}
          />
        </Field>
        <CamOptionsPanel
          value={camOptions}
          onChange={(v) => patch({ camOptions: v })}
          secondaryCount={gridMultiCamShooters(shooters)}
          choices={gridCameraChoices(shooters)}
          savedLabel=""
          editing={false}
          grid
          busy={busy}
        />
        {gridFreeCells(shooters.length) > 0 ? (
          <Field label="Free square" help={FREE_CELL_HELP[settings.gridFreeCell]}>
            <Segmented<GridFreeCell>
              label="Free square"
              value={settings.gridFreeCell}
              disabled={busy}
              onChange={(v) => patch({ gridFreeCell: v })}
              options={FREE_CELL_OPTIONS}
            />
          </Field>
        ) : null}
        <Field
          label="YouTube"
          help={
            youtube
              ? "Writes the title, a description with a chapter per stage, tags and a thumbnail beside the grid, ready to upload."
              : "Off: no upload sidecar."
          }
        >
          <Segmented<"off" | "on">
            label="YouTube"
            value={youtube ? "on" : "off"}
            onChange={(v) => patch({ youtube: v === "on" })}
            options={[
              { value: "off", label: "Off" },
              { value: "on", label: "Sidecar" },
            ]}
          />
        </Field>
      </>
    );
  }
  return (
    <>
      <Field label="Format" help="The splits CSV and the text report are always written alongside.">
        <SelectField
          label="Timeline format"
          value={outputFormat}
          onChange={(v) => patch({ outputFormat: v })}
          options={[
            { value: "fcpxml", label: "FCPXML 1.10 (Final Cut Pro)" },
            { value: "fcp7xml", label: "FCP 7 XML (Premiere / Resolve)" },
            { value: "mp4", label: "MP4 (rendered)" },
          ]}
          disabled={onDesktop}
          title={DESKTOP_RENDERS_MP4}
          className="w-full max-w-xs"
        />
      </Field>
      {includeOverlay ? (
        <Field
          label="Overlay codec"
          help="The overlay is a transparent MOV, so the codec has to carry alpha: Auto picks HEVC on macOS and ProRes 4444 elsewhere."
        >
          <SelectField
            label="Overlay codec"
            value={overlayCodec}
            onChange={(v) => patch({ overlayCodec: v })}
            options={[
              { value: "auto", label: "Auto" },
              { value: "hevc-alpha", label: "HEVC + alpha (macOS)" },
              { value: "prores-4444", label: "ProRes 4444" },
            ]}
            className="w-56"
          />
        </Field>
      ) : null}
      {outputFormat === "mp4" ? (
        <Field
          label="YouTube"
          help={
            youtube
              ? `Encodes with the YouTube preset and writes the title, description with chapters and tags (paste-ready), per-shot captions (.srt) and a thumbnail beside the video.${bareHint("captions", bareSelected) ? ` ${bareHint("captions", bareSelected)}` : ""}`
              : "Off: the default encode, no upload sidecar."
          }
        >
          <Segmented<"off" | "on">
            label="YouTube"
            value={youtube ? "on" : "off"}
            onChange={(v) => patch({ youtube: v === "on" })}
            options={[
              { value: "off", label: "Off" },
              { value: "on", label: "Preset + sidecar" },
            ]}
            disabled={onDesktop}
          />
        </Field>
      ) : null}
      <CamOptionsPanel
        value={camOptions}
        onChange={(v) => patch({ camOptions: v })}
        secondaryCount={secondaryCount}
        choices={camChoices}
        savedLabel={compareCamera ? mountLabel(compareCamera) : "Primary"}
        editing={outputFormat !== "mp4"}
        busy={busy}
      />
    </>
  );
}
