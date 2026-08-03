import { describe, expect, it } from "vitest";

import { formatBytes, formatEta } from "@/lib/format";

describe("formatEta", () => {
  it("counts seconds under a minute", () => {
    expect(formatEta(42)).toBe("~42 sec");
  });

  it("counts minutes under an hour", () => {
    expect(formatEta(6 * 60 + 20)).toBe("~6 min");
  });

  it("counts hours and minutes above an hour", () => {
    expect(formatEta(2 * 3600 + 25 * 60)).toBe("~2 h 25 min");
  });

  it("never reads as zero while bytes are still moving", () => {
    // Rounding a sub-second projection down to "~0 sec" reads as done
    // when it isn't.
    expect(formatEta(0.4)).toBe("~1 sec");
  });

  it("has nothing to say without an estimate", () => {
    expect(formatEta(null)).toBeNull();
  });

  it("has nothing to say about a nonsense estimate", () => {
    // A rate that underflows can produce Infinity; rendering it would
    // put "~Infinity min" in the dock.
    expect(formatEta(Infinity)).toBeNull();
    expect(formatEta(NaN)).toBeNull();
    expect(formatEta(-5)).toBeNull();
  });
});

describe("formatBytes", () => {
  it("scales to the unit that keeps the number small", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatBytes(3 * 1024 * 1024 * 1024)).toBe("3.00 GB");
  });
});
