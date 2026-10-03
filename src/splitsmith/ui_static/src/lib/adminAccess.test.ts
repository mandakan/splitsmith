import { describe, expect, it } from "vitest";

import type { AccessRequest } from "@/lib/api";
import { approveTierDefault, requestRows } from "@/lib/adminAccess";

const base: AccessRequest = {
  id: "r1",
  email: "a@x.se",
  note: null,
  source: "login",
  status: "pending",
  requested_at: "2026-10-01T12:00:00Z",
  last_requested_at: "2026-10-03T10:00:00Z",
  decided_at: null,
  decided_by: null,
  tier_granted: null,
  email_sent_at: null,
};
const now = new Date("2026-10-03T12:00:00Z");

describe("requestRows", () => {
  it("splits pending from decided and words the source", () => {
    const { pending, decided } = requestRows(
      [base, { ...base, id: "r2", source: "waitlist", status: "declined" }],
      now,
    );
    expect(pending.map((r) => r.id)).toEqual(["r1"]);
    expect(pending[0].sourceLabel).toBe("Sign-in");
    expect(decided[0].sourceLabel).toBe("Waitlist");
  });
  it("words every known source and passes an unknown one through", () => {
    const label = (source: string) => requestRows([{ ...base, source }], now).pending[0].sourceLabel;
    expect(label("form")).toBe("Form");
    expect(label("import")).toBe("Waitlist import");
    expect(label("other")).toBe("other");
  });
  it("age counts from the last request", () => {
    expect(requestRows([base], now).pending[0].age).toBe("2 h");
  });
  it("words the age in minutes, hours and days", () => {
    const age = (iso: string) => requestRows([{ ...base, last_requested_at: iso }], now).pending[0].age;
    expect(age("2026-10-03T11:15:00Z")).toBe("45 min");
    expect(age("2026-10-01T13:00:00Z")).toBe("47 h");
    expect(age("2026-10-01T12:00:00Z")).toBe("2 d");
  });
  it("flags an approval whose mail never went out", () => {
    const row = { ...base, status: "approved" as const, email_sent_at: null };
    expect(requestRows([row], now).decided[0].mailFailed).toBe(true);
    expect(
      requestRows([{ ...row, email_sent_at: "2026-10-03T11:00:00Z" }], now).decided[0].mailFailed,
    ).toBe(false);
  });
  it("never flags a declined request's mail", () => {
    expect(requestRows([{ ...base, status: "declined" }], now).decided[0].mailFailed).toBe(false);
  });
});

describe("approveTierDefault", () => {
  it("prefers sharing", () => {
    expect(
      approveTierDefault({
        tiers: [
          { name: "full", features: [] },
          { name: "sharing", features: [] },
        ],
        default_tier: "full",
      }),
    ).toBe("sharing");
  });
  it("falls back to the registry default", () => {
    expect(approveTierDefault({ tiers: [{ name: "full", features: [] }], default_tier: "full" })).toBe("full");
  });
});
