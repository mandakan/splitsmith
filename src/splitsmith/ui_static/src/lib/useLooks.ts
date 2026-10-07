/**
 * The Looks catalog, fetched once per page however many surfaces mount
 * the hook (#1246). Until it answers the page works from
 * ``BUILTIN_LOOKS`` and gates Export; when it cannot be fetched the
 * state says so (``failed``) and the requests send the stored names for
 * the server to validate (``lib/looks.requestLook``).
 */
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { BUILTIN_LOOKS, type LooksState } from "@/lib/looks";

export type { LooksState } from "@/lib/looks";

let settled: LooksState | null = null;
let pending: Promise<LooksState> | null = null;

function fetchLooks(): Promise<LooksState> {
  if (settled) return Promise.resolve(settled);
  if (!pending) {
    pending = api
      .listLooks()
      .then(
        (r): LooksState => ({
          looks: r.looks.length > 0 ? r.looks : BUILTIN_LOOKS,
          transitions: r.transitions ?? [],
          loaded: true,
          failed: false,
        }),
      )
      .catch((): LooksState => ({ looks: BUILTIN_LOOKS, transitions: [], loaded: true, failed: true }))
      .then((state) => {
        settled = state;
        pending = null;
        return state;
      });
  }
  return pending;
}

export function useLooks(): LooksState {
  const [state, setState] = useState<LooksState>(() => settled ?? { looks: BUILTIN_LOOKS, transitions: [], loaded: false, failed: false });
  useEffect(() => {
    let alive = true;
    void fetchLooks().then((s) => {
      if (alive) setState(s);
    });
    return () => {
      alive = false;
    };
  }, []);
  return state;
}

/** Tests: forget the fetched catalog. */
export function resetLooksForTests(): void {
  settled = null;
  pending = null;
}
