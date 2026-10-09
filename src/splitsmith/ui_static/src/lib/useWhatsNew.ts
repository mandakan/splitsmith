/**
 * What's new, shared by every surface that shows it: the sheet (opened once
 * per session when there is something unseen), the bar's button and the
 * "New" chips. One fetch per page; a failed fetch (signed out, offline)
 * shows nothing at all.
 */
import { useEffect, useState } from "react";

import { api, type WhatsNewPayload } from "@/lib/api";
import { CHIP_PREFIX, chipActive, unseen } from "@/lib/whatsNew";

interface State {
  payload: WhatsNewPayload | null;
  open: boolean;
}

let state: State = { payload: null, open: false };
let fetched = false;
let autoOpened = false;
const listeners = new Set<(s: State) => void>();

function set(next: Partial<State>): void {
  state = { ...state, ...next };
  listeners.forEach((notify) => notify(state));
}

function load(): void {
  if (fetched) return;
  fetched = true;
  api
    .getWhatsNew()
    .then((payload) => {
      const fresh = unseen(payload.entries, payload.seen).length > 0;
      set({ payload, open: state.open || (fresh && !autoOpened) });
      if (fresh) autoOpened = true;
    })
    .catch(() => undefined);
}

async function markSeen(ids: string[]): Promise<void> {
  if (ids.length === 0) return;
  try {
    set({ payload: await api.markWhatsNewSeen(ids) });
  } catch {
    // Seen-ness is a courtesy; a failed write shows the entries again next time.
  }
}

export function openWhatsNew(): void {
  set({ open: true });
}

/** Close the sheet; everything it listed counts as seen. */
export function closeWhatsNew(): void {
  const shown = state.payload ? unseen(state.payload.entries, state.payload.seen).map((e) => e.id) : [];
  set({ open: false });
  void markSeen(shown);
}

/** The feature was used: its "New" chip goes away for good. */
export function dismissNewChip(key: string): void {
  if (!state.payload || state.payload.seen.includes(`${CHIP_PREFIX}${key}`)) return;
  if (!state.payload.entries.some((e) => e.chip === key)) return;
  void markSeen([`${CHIP_PREFIX}${key}`]);
}

export function useWhatsNew(): State & { unseenCount: number; chip: (key: string) => boolean } {
  const [current, setCurrent] = useState<State>(state);
  useEffect(() => {
    listeners.add(setCurrent);
    load();
    setCurrent(state);
    return () => {
      listeners.delete(setCurrent);
    };
  }, []);
  const { payload } = current;
  return {
    ...current,
    unseenCount: payload ? unseen(payload.entries, payload.seen).length : 0,
    chip: (key: string) => chipActive(payload?.entries ?? null, payload?.seen ?? null, key),
  };
}

/** Tests: forget the fetched feed and the session's auto-open. */
export function resetWhatsNewForTests(): void {
  state = { payload: null, open: false };
  fetched = false;
  autoOpened = false;
  listeners.clear();
}
