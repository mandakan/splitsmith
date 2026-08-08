/**
 * Surviving the login redirect (#719).
 *
 * An operator who follows verification_uri_complete without a live
 * session gets bounced to /login; the magic link returns them to "/",
 * by which point the code in the URL is long gone. The stash is what
 * carries it across. sessionStorage, not localStorage: it is scoped to
 * the tab that started the flow and dies with it.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { peekApproveCode, stashApproveCode, takeApproveCode } from "@/lib/deviceApproveStash";

describe("deviceApproveStash", () => {
  beforeEach(() => sessionStorage.clear());

  it("round-trips a code", () => {
    stashApproveCode("ABCD-2345");
    expect(takeApproveCode()).toBe("ABCD-2345");
  });

  it("is single-use, so a later reload does not re-bounce", () => {
    stashApproveCode("ABCD-2345");
    takeApproveCode();
    expect(takeApproveCode()).toBeNull();
  });

  it("returns null when nothing was stashed", () => {
    expect(takeApproveCode()).toBeNull();
  });

  it("ignores a stashed value that is not a plausible user code", () => {
    sessionStorage.setItem("splitsmith.deviceApproveCode", "../../etc/passwd");
    expect(takeApproveCode()).toBeNull();
  });

  // peekApproveCode exists because AuthGate must decide whether to redirect
  // synchronously during render, and takeApproveCode()'s read-then-remove
  // is not safe to call there (see App.tsx's AuthGate for why). It has to
  // be genuinely non-destructive, or that whole reasoning falls apart.
  it("peek does not consume -- repeated peeks see the same value", () => {
    stashApproveCode("ABCD-2345");
    expect(peekApproveCode()).toBe("ABCD-2345");
    expect(peekApproveCode()).toBe("ABCD-2345");
    expect(takeApproveCode()).toBe("ABCD-2345");
    expect(peekApproveCode()).toBeNull();
  });

  it("peek also rejects an implausible stashed value", () => {
    sessionStorage.setItem("splitsmith.deviceApproveCode", "../../etc/passwd");
    expect(peekApproveCode()).toBeNull();
  });
});
