/**
 * The Looks catalog, fetched once per page (#1246). Until it answers, and
 * when it cannot, the page works from ``BUILTIN_LOOKS``: the gallery shows
 * the default Look alone and every request sends the defaults.
 */
import { useEffect, useState } from "react";

import { api, type LookInfo } from "@/lib/api";
import { BUILTIN_LOOKS } from "@/lib/looks";

export interface LooksState {
  looks: LookInfo[];
  loaded: boolean;
}

let settled: LookInfo[] | null = null;

export function useLooks(): LooksState {
  const [state, setState] = useState<LooksState>(() =>
    settled ? { looks: settled, loaded: true } : { looks: BUILTIN_LOOKS, loaded: false },
  );
  useEffect(() => {
    let alive = true;
    void api
      .listLooks()
      .then((r) => {
        settled = r.looks.length > 0 ? r.looks : BUILTIN_LOOKS;
        if (alive) setState({ looks: settled, loaded: true });
      })
      .catch(() => {
        if (alive) setState({ looks: BUILTIN_LOOKS, loaded: true });
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
}
