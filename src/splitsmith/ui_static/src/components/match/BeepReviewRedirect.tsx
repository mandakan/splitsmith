/**
 * The desktop Beep review page folded into Audit as its step 1 (UX PR
 * 5). Old links keep working for one release: ``?stage=N`` lands on
 * that stage's Audit for the default shooter, ``?focus=slug::stage::video``
 * opens Audit's step 1 on that camera, and a bare ``/beep-review`` goes
 * to the first beep still pending (or the default shooter's Audit when
 * the queue is clean).
 */
import { useEffect, useState } from "react";
import { Navigate, useParams, useSearchParams } from "react-router-dom";

import { api } from "@/lib/api";
import { pickDefaultShooterSlug } from "@/lib/defaultShooter";
import { matchHref } from "@/lib/matchHref";

export function BeepReviewRedirect() {
  const { matchId } = useParams<{ matchId?: string }>();
  const [searchParams] = useSearchParams();
  const [target, setTarget] = useState<string | null>(null);
  const stageParam = searchParams.get("stage");
  const focusParam = searchParams.get("focus");

  useEffect(() => {
    let alive = true;
    const focus = focusParam?.split("::");
    if (focus && focus.length === 3) {
      setTarget(`${matchHref(matchId, "audit", focus[0], focus[1])}?beep=${encodeURIComponent(focus[2])}`);
      return;
    }
    (async () => {
      try {
        if (!stageParam) {
          const queue = await api.getBeepQueue(false);
          const pending = queue.stages.flatMap((g) => g.items).find((it) => it.status !== "confirmed");
          if (pending) {
            if (alive) setTarget(matchHref(matchId, "audit", pending.slug, String(pending.stage_number)));
            return;
          }
        }
        const roster = await api.listMatchShooters();
        const slug = pickDefaultShooterSlug(roster.shooters);
        if (!alive) return;
        setTarget(
          slug
            ? stageParam
              ? matchHref(matchId, "audit", slug, stageParam)
              : matchHref(matchId, "audit", slug)
            : matchHref(matchId, "shooters"),
        );
      } catch {
        if (alive) setTarget(matchHref(matchId, "audit"));
      }
    })();
    return () => {
      alive = false;
    };
  }, [matchId, stageParam, focusParam]);

  if (!target) return null;
  return <Navigate to={target} replace />;
}
