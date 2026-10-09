/**
 * LookHealth -- the Export rail's preflight for your own Look (#1276): its
 * saved templates are checked when it is chosen (the server caches the check
 * by the folder's content), and a template that fails is named before a
 * render leaves its card out. Never blocks Export. Local only: a Look on
 * splitsmith.app has no templates of its own, and a shipped Look is tested.
 */
import { useEffect, useState } from "react";

import { api, type LookInfo } from "@/lib/api";
import { lookFailures } from "@/lib/lookHealth";

export function LookHealth({ look, looks, hosted }: { look: string; looks: LookInfo[]; hosted: boolean }) {
  const [failures, setFailures] = useState<string[]>([]);
  const own = !hosted && looks.find((l) => l.name === look)?.source === "user";

  useEffect(() => {
    setFailures([]);
    if (!own) return;
    let alive = true;
    api
      .checkLook(look, null, [])
      .then((r) => alive && setFailures(lookFailures(r.items)))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [look, own]);

  if (failures.length === 0) return null;
  return (
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
  );
}
