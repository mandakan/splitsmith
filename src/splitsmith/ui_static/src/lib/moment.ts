// A Moment is the shareable "what I am looking at" unit: seconds after the
// start beep plus, on Compare, the focused camera and visible shooters.
// This module is the single serializer/parser for its URL form - the future
// bookmark feature stores this same object and navigates via momentToSearch.

export type Moment = {
  t: number;
  cam?: string;
  who?: string[];
};

const T_LIMIT = 3600;

// Mirrors _WHO_MAX in src/splitsmith/ui/share_og.py: caps a compare
// moment's roster so an unbounded who= can't pad an arbitrarily long
// list into the URL (and, downstream, the OG card render).
export const WHO_MAX = 12;

export function momentToSearch(m: Moment): URLSearchParams {
  const params = new URLSearchParams();
  // Clamp to the same bound parseMoment enforces (|t| <= T_LIMIT), or a
  // moment captured past the hour mark (a long clip) would round-trip
  // through a link our own UI just minted and get rejected on the other
  // end.
  const t = Math.max(-T_LIMIT, Math.min(T_LIMIT, m.t));
  params.set("t", t.toFixed(2));
  if (m.cam) params.set("cam", m.cam);
  if (m.who && m.who.length > 0) params.set("who", m.who.slice(0, WHO_MAX).join(","));
  return params;
}

export function parseMoment(params: URLSearchParams): Moment | null {
  const raw = params.get("t");
  if (raw == null || raw.trim() === "") return null;
  const t = Number(raw);
  if (!Number.isFinite(t) || Math.abs(t) > T_LIMIT) return null;
  const moment: Moment = { t: Math.round(t * 100) / 100 };
  const cam = params.get("cam");
  if (cam) moment.cam = cam;
  const who = params.get("who");
  if (who) {
    const slugs = who
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (slugs.length > 0) moment.who = slugs;
  }
  return moment;
}

export function momentHref(pathname: string, m: Moment): string {
  return `${pathname}?${momentToSearch(m).toString()}`;
}

export function resolveMomentView(
  moment: Moment,
  slugs: ReadonlySet<string>,
): { cam: string | null; who: string[] | null } {
  const who = moment.who?.filter((s) => slugs.has(s)) ?? [];
  return {
    cam: moment.cam && slugs.has(moment.cam) ? moment.cam : null,
    who: who.length > 0 ? who : null,
  };
}
