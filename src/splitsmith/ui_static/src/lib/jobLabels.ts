/**
 * Human labels for job kinds. Lives in lib/ so the ProgressStrip primitive
 * and the Jobs surface can both read it without importing each other.
 */
export const KIND_LABEL: Record<string, string> = {
  detect_beep: "Detect beep",
  trim: "Trim stage video",
  shot_detect: "Detect shots",
  export: "Export stage",
  match_export: "Match export",
  audio_extract: "Audio extract",
  model_download: "Download models",
  generate_proxy: "Generating preview",
  sync_match: "Sync to hosted",
};

export function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}
