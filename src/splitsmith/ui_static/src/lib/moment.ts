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

export function momentToSearch(m: Moment): URLSearchParams {
  const params = new URLSearchParams();
  params.set("t", m.t.toFixed(2));
  if (m.cam) params.set("cam", m.cam);
  if (m.who && m.who.length > 0) params.set("who", m.who.join(","));
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
