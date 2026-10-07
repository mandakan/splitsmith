/**
 * Details -- what is asked fresh per match (spec 2026-09-15 s1): the
 * bundle name, the title line, the description lead, and the upload
 * connection. The
 * publish options inside YouTubeConnect are preset-owned; they live here
 * because this is where publishing is expected to be found.
 */
import { BrandingField } from "@/components/export/BrandingField";
import { YouTubeConnect } from "@/components/export/YouTubeConnect";
import { Field, inputClass } from "@/components/ui/Field";
import type { YouTubeSettings } from "@/lib/api";
import type { ExportSettings } from "@/lib/exportPresets";
import { cn } from "@/lib/utils";

export interface DetailsGroupProps {
  settings: ExportSettings;
  patch: (p: Partial<ExportSettings>) => void;
  busy: boolean;
  projectName: string;
  onProjectName: (v: string) => void;
  exportsDir: string | null;
  descriptionLead: string;
  onDescriptionLead: (v: string) => void;
  youtubeSettings: YouTubeSettings | null;
  onYouTubeSettingsChange: () => void;
  matchName: string;
  /** The upload runs on the linked desktop's own YouTube account. */
  onDesktop?: boolean;
}

export function DetailsGroup({
  settings,
  patch,
  busy,
  projectName,
  onProjectName,
  exportsDir,
  descriptionLead,
  onDescriptionLead,
  youtubeSettings,
  onYouTubeSettingsChange,
  matchName,
  onDesktop = false,
}: DetailsGroupProps) {
  const single = settings.mode === "single";
  // The grid is always a rendered MP4, so it publishes like one shooter's.
  const renderedMp4 = (single && settings.outputFormat === "mp4") || settings.mode === "compare";
  const publishing = renderedMp4 && settings.youtube;
  const drawsCards = renderedMp4;
  const titleCard = drawsCards && (settings.renderOptions.titlePage || settings.renderOptions.closingCard);
  return (
    <>
      {single ? (
        <Field label="Bundle name" htmlFor="export-bundle-name" help={exportsDir ?? "exports/"}>
          <input
            id="export-bundle-name"
            type="text"
            value={projectName}
            onChange={(e) => onProjectName(e.target.value)}
            className={cn(inputClass, "max-w-xs font-mono text-sm")}
          />
        </Field>
      ) : null}
      {titleCard ? (
        <Field
          label="Title line"
          htmlFor="export-title-info"
          help="Under the match name on the title page and the closing card: level, squad, anything."
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
      {titleCard ? <BrandingField busy={busy} /> : null}
      {titleCard ? (
        <Field label="Division" help="As the scoreboard has it, power factor included: Classic Major.">
          <label className="flex items-center gap-2 text-md text-ink-2">
            <input
              type="checkbox"
              aria-label="Show division"
              checked={settings.renderOptions.titleDivision}
              disabled={busy}
              onChange={(e) =>
                patch({ renderOptions: { ...settings.renderOptions, titleDivision: e.target.checked } })
              }
              className="accent-[var(--color-ink)]"
            />
            {single ? "Under the shooter's name" : "Next to each shooter's name"}
          </label>
        </Field>
      ) : null}
      {drawsCards && settings.renderOptions.closingCard ? (
        <Field label="Credit" help="A small line and mark at the bottom of the closing card.">
          <label className="flex items-center gap-2 text-md text-ink-2">
            <input
              type="checkbox"
              aria-label="Made with splitsmith"
              checked={settings.renderOptions.madeWith}
              disabled={busy}
              onChange={(e) => patch({ renderOptions: { ...settings.renderOptions, madeWith: e.target.checked } })}
              className="accent-[var(--color-ink)]"
            />
            Made with splitsmith
          </label>
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
      {renderedMp4 || onDesktop ? (
        <Field label="Upload">
          <YouTubeConnect
            settings={youtubeSettings}
            onSettingsChange={onYouTubeSettingsChange}
            options={settings.uploadOptions}
            onOptionsChange={(v) => patch({ uploadOptions: v })}
            matchName={matchName}
            showUploadControl={publishing}
            busy={busy}
            onDesktop={onDesktop}
          />
        </Field>
      ) : null}
    </>
  );
}
