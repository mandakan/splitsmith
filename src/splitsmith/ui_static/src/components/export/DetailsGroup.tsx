/**
 * Details -- what is asked fresh per match (spec 2026-09-15 s1): the
 * bundle name, the title line, the description lead, and the upload
 * connection. The
 * publish options inside YouTubeConnect are preset-owned; they live here
 * because this is where publishing is expected to be found.
 */
import { YouTubeConnect } from "@/components/export/YouTubeConnect";
import { Field, inputClass } from "@/components/ui/Field";
import type { YouTubeSettings } from "@/lib/api";
import type { ExportSettings } from "@/lib/exportPresets";
import { cn } from "@/lib/utils";

export interface DetailsGroupProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  hosted: boolean;
  projectName: string;
  onProjectName: (v: string) => void;
  exportsDir: string | null;
  descriptionLead: string;
  onDescriptionLead: (v: string) => void;
  youtubeSettings: YouTubeSettings | null;
  onYouTubeSettingsChange: () => void;
  matchName: string;
}

export function DetailsGroup({
  settings,
  patch,
  busy,
  hosted,
  projectName,
  onProjectName,
  exportsDir,
  descriptionLead,
  onDescriptionLead,
  youtubeSettings,
  onYouTubeSettingsChange,
  matchName,
}: DetailsGroupProps) {
  const renderedMp4 = settings.mode === "single" && settings.outputFormat === "mp4";
  const publishing = renderedMp4 && settings.youtube;
  const titleCard = renderedMp4 && (settings.renderOptions.titlePage || settings.renderOptions.closingCard);
  return (
    <>
      <Field label="Bundle name" htmlFor="export-bundle-name" help={exportsDir ?? "exports/"}>
        <input
          id="export-bundle-name"
          type="text"
          value={projectName}
          onChange={(e) => onProjectName(e.target.value)}
          className={cn(inputClass, "max-w-xs font-mono text-sm")}
        />
      </Field>
      {titleCard ? (
        <Field
          label="Title line"
          htmlFor="export-title-info"
          help="Under the match name on the title page and the closing card: division, level, anything."
        >
          <input
            id="export-title-info"
            aria-label="Title page info line"
            type="text"
            className={cn(inputClass, "max-w-md")}
            value={settings.renderOptions.titleInfo}
            disabled={busy}
            onChange={(e) => patch({ renderOptions: { ...settings.renderOptions, titleInfo: e.target.value } })}
          />
        </Field>
      ) : null}
      {publishing ? (
        <Field
          label="Description"
          htmlFor="export-description-lead"
          help="What the video is, above the chapter list: division, camera, the day."
        >
          <textarea
            id="export-description-lead"
            aria-label="Description lead"
            rows={2}
            className={cn(inputClass, "max-w-md")}
            value={descriptionLead}
            onChange={(e) => onDescriptionLead(e.target.value)}
          />
        </Field>
      ) : null}
      {renderedMp4 && !hosted ? (
        <Field label="Upload">
          <YouTubeConnect
            settings={youtubeSettings}
            onSettingsChange={onYouTubeSettingsChange}
            options={settings.uploadOptions}
            onOptionsChange={(v) => patch({ uploadOptions: v })}
            matchName={matchName}
            showUploadControl={publishing}
            busy={busy}
          />
        </Field>
      ) : null}
    </>
  );
}
