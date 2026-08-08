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

import { stashApproveCode, takeApproveCode } from "@/lib/deviceApproveStash";

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
});
