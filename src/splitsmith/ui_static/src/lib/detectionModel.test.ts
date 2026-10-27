import { describe, expect, it } from "vitest";

import { detectionModelLine, detectedWithLabel } from "./detectionModel";

const settings = (over: Partial<Parameters<typeof detectionModelLine>[0] & object> = {}) => ({
  model_set: "clap",
  required: ["clap_audio_encoder", "clap_text_embeddings"],
  present: false,
  active_job: null,
  ...over,
});

describe("the detection model card's status line", () => {
  it("says nothing while off or before the settings load", () => {
    expect(detectionModelLine(null)).toBeNull();
    expect(detectionModelLine(settings({ model_set: "base", required: [], present: true }))).toBeNull();
  });

  it("names the download, the ready state and the gap between them", () => {
    expect(detectionModelLine(settings({ active_job: "j1" }))).toBe("Downloading...");
    expect(detectionModelLine(settings({ present: true }))).toBe("Ready");
    expect(detectionModelLine(settings())).toBe("Not downloaded yet. Detection runs without it until it is.");
  });
});

describe("the Audit label for the model a detection ran with", () => {
  it("names the model, and says nothing for a detection from before model sets", () => {
    expect(detectedWithLabel({ _model_set: "clap", candidates: [] })).toBe("Detected with the audio model");
    expect(detectedWithLabel({ _model_set: "base", candidates: [] })).toBe("Detected without the audio model");
    expect(detectedWithLabel({ candidates: [] })).toBeNull();
    expect(detectedWithLabel(undefined)).toBeNull();
  });
});
