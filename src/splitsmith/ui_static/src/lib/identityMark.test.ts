import { afterEach, describe, expect, it } from "vitest";

import { identityMark } from "@/lib/identityMark";

afterEach(() => window.history.replaceState(null, "", "/"));

describe("identityMark", () => {
  it("is nothing for a shooter who set nothing", () => {
    expect(identityMark("anna", undefined)).toEqual({ accent: null, logo: null });
    expect(identityMark("anna", { accent: null, logo: null, club: null })).toEqual({ accent: null, logo: null });
  });

  it("names the logo by its content so a new one is fetched, scoped like every request", () => {
    window.history.replaceState(null, "", "/match/m1/compare");
    const mark = identityMark("anna", { accent: "#22aaee", logo: "logo-0123456789ab.png", club: "Bromma PK" });
    expect(mark.accent).toBe("#22aaee");
    expect(mark.logo).toBe("/api/matches/m1/shooters/anna/identity/logo?v=logo-0123456789ab.png");
  });

  it("reads through a share link on a share page", () => {
    window.history.replaceState(null, "", "/share/tok123/results");
    expect(identityMark("anna", { accent: null, logo: "logo-0123456789ab.png", club: null }).logo).toBe(
      "/api/share/tok123/shooters/anna/identity/logo?v=logo-0123456789ab.png",
    );
  });
});
