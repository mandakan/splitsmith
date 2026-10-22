import { describe, expect, it } from "vitest";

import { MULTIPART_THRESHOLD_BYTES } from "./client";

describe("MULTIPART_THRESHOLD_BYTES", () => {
  it("keeps every serve-proxied upload under Cloud Run's 32 MiB request limit", () => {
    // A single-shot upload is a multipart form: the file plus a small
    // envelope. Cloud Run answers 413 before serve sees a body over 32 MiB,
    // so the largest single-shot file must leave room for that envelope.
    expect(MULTIPART_THRESHOLD_BYTES).toBeLessThanOrEqual(24 * 1024 * 1024);
  });
});
