/**
 * The admin access page's derivation (spec 2026-10-03): access requests
 * split into the pending queue and the decided history, each worded for
 * a row. The page maps these to primitives and owns no wording of its own
 * for source, age or the failed-mail flag.
 */
import type { AccessRequest, AccessTiers } from "@/lib/api";

export interface RequestRow {
  id: string;
  email: string;
  note: string | null;
  sourceLabel: string;
  /** Time since the last request: "N min", "N h" or "N d". */
  age: string;
  pending: boolean;
  /** Approved, but the sign-in mail never went out. */
  mailFailed: boolean;
  status: AccessRequest["status"];
  tierGranted: string | null;
  decidedBy: string | null;
}

const SOURCE: Record<string, string> = {
  login: "Sign-in",
  form: "Form",
  waitlist: "Waitlist",
  import: "Waitlist import",
};

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export function ageLabel(iso: string, now: Date): string {
  const ms = Math.max(0, now.getTime() - new Date(iso).getTime());
  if (ms < HOUR) return `${Math.floor(ms / MINUTE)} min`;
  if (ms < 48 * HOUR) return `${Math.floor(ms / HOUR)} h`;
  return `${Math.floor(ms / DAY)} d`;
}

function toRow(r: AccessRequest, now: Date): RequestRow {
  return {
    id: r.id,
    email: r.email,
    note: r.note,
    sourceLabel: SOURCE[r.source] ?? r.source,
    age: ageLabel(r.last_requested_at, now),
    pending: r.status === "pending",
    mailFailed: r.status === "approved" && r.email_sent_at === null,
    status: r.status,
    tierGranted: r.tier_granted,
    decidedBy: r.decided_by,
  };
}

/** Keeps the server's order (pending first, newest request first). */
export function requestRows(
  rows: AccessRequest[],
  now: Date,
): { pending: RequestRow[]; decided: RequestRow[] } {
  const pending: RequestRow[] = [];
  const decided: RequestRow[] = [];
  for (const r of rows) {
    const row = toRow(r, now);
    (row.pending ? pending : decided).push(row);
  }
  return { pending, decided };
}

/** The tier an approval starts on: the sharing tier when the registry
 *  has one (the conservative grant), else the registry's default. */
export function approveTierDefault(tiers: AccessTiers): string {
  return tiers.tiers.some((t) => t.name === "sharing") ? "sharing" : tiers.default_tier;
}
