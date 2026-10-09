/**
 * LookHealth -- the Export rail's preflight for your own Look (#1276): its
 * saved templates are checked when it is chosen (the server caches the check
 * by the folder's content), and a template that fails is named before a
 * render leaves its card out. Never blocks Export. Local only: a Look on
 * splitsmith.app has no templates of its own, and a shipped Look is tested.
 *
 * It also says when the Look holds unedited copies of an older shipped
 * template (a Look duplicated before a release), whose cards miss what
 * shipped since, and switches them to the current ones in one click.
 */
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { api, type LookInfo } from "@/lib/api";
import { lookFailures } from "@/lib/lookHealth";

export function LookHealth({
  look,
  looks,
  hosted,
  onRefreshed,
}: {
  look: string;
  looks: LookInfo[];
  hosted: boolean;
  /** Called after the Look's copies were dropped (re-fetch the catalog). */
  onRefreshed?: () => void;
}) {
  const [failures, setFailures] = useState<string[]>([]);
  const [outdated, setOutdated] = useState<string[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const own = !hosted && looks.find((l) => l.name === look)?.source === "user";

  useEffect(() => {
    setFailures([]);
    setOutdated([]);
    setRefreshError(null);
    if (!own) return;
    let alive = true;
    api
      .checkLook(look, null, [])
      .then((r) => alive && setFailures(lookFailures(r.items)))
      .catch(() => undefined);
    api
      .outdatedLookTemplates(look)
      .then((r) => alive && setOutdated(r.files))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [look, own]);

  const switchToCurrent = async () => {
    setRefreshing(true);
    setRefreshError(null);
    try {
      await api.refreshLookTemplates(look);
      setOutdated([]);
      onRefreshed?.();
    } catch (err) {
      setRefreshError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  };

  if (failures.length === 0 && outdated.length === 0) return null;
  return (
    <>
      {outdated.length > 0 ? (
        <div role="status" className="border-t border-rule px-3.5 py-2 text-sm">
          <p className="text-ink-2">
            This Look uses older copies of the shipped cards ({outdated.join(", ")}), so they miss what has
            shipped since, like your brand logo, the event logo and the closing credit. You never edited them.
          </p>
          <div className="mt-2 flex items-center gap-3">
            <Button type="button" size="sm" onClick={() => void switchToCurrent()} disabled={refreshing}>
              Use the current cards
            </Button>
            {refreshError ? <span className="text-destructive">{refreshError}</span> : null}
          </div>
        </div>
      ) : null}
      {failures.length > 0 ? (
        <div role="alert" className="border-t border-rule px-3.5 py-2 text-sm">
          <p className="text-destructive">
            A template in this Look fails, so these cards would be left out of the video. Edit Look under Look,
            Advanced says why.
          </p>
          <ul className="mt-1 text-muted">
            {failures.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
}
