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
const listeners = new Set<(state: LooksState) => void>();

function fetchLooks(): Promise<LooksState> {
  if (settled) return Promise.resolve(settled);
  if (!pending) {
    pending = api
      .listLooks()
      .then(
        (r): LooksState => ({
          looks: r.looks.length > 0 ? r.looks : BUILTIN_LOOKS,
          transitions: r.transitions ?? [],
          fonts: r.fonts ?? [],
          loaded: true,
          failed: false,
        }),
      )
      .catch((): LooksState => ({ looks: BUILTIN_LOOKS, transitions: [], loaded: true, failed: true }))
      .then((state) => {
        settled = state;
        pending = null;
        listeners.forEach((notify) => notify(state));
        return state;
      });
  }
  return pending;
}

export function useLooks(): LooksState {
  const [state, setState] = useState<LooksState>(() => settled ?? { looks: BUILTIN_LOOKS, transitions: [], loaded: false, failed: false });
  useEffect(() => {
    let alive = true;
    listeners.add(setState);
    void fetchLooks().then((s) => {
      if (alive) setState(s);
    });
    return () => {
      alive = false;
      listeners.delete(setState);
    };
  }, []);
  return state;
}

/** Fetch the catalog again and hand it to every mounted surface: after
 *  the Look editor saves, duplicates or deletes a Look (#1264). */
export function refreshLooks(): Promise<LooksState> {
  // A fetch already in flight started before the write; ask again.
  settled = null;
  pending = null;
  return fetchLooks();
}

/** Tests: forget the fetched catalog. */
export function resetLooksForTests(): void {
  settled = null;
  pending = null;
  listeners.clear();
}
