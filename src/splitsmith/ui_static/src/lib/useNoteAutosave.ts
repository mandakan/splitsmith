/**
 * useNoteAutosave -- a free-text note that saves itself (Coach review,
 * #1374 / #1376): after a pause in typing and on blur, never per keystroke.
 *
 * - One save in flight at a time; text typed while one flies goes out after
 *   it, so the server sees the edits in order.
 * - The draft follows the server's value only while nothing local is
 *   unsaved: a coach response for another reason (a flag on another shot)
 *   never overwrites what is being typed.
 * - A conflict (409) reloads. When the reloaded note is still the one this
 *   edit started from (the document moved for another reason), the draft is
 *   sent once more on the fresh revision; otherwise the other writer's note
 *   wins, the draft takes it, and ``issue`` is ``discarded``.
 * - Any other failure keeps the draft (typing is never thrown away) and sets
 *   ``issue`` to ``failed``; ``retry`` sends the draft again.
 *
 * ``issue`` is what ``SaveNotice`` renders. Pure state plus timers: the page
 * supplies ``save`` (which applies the response) and ``reload``.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import type { SaveIssue } from "@/lib/useStageEvents";

export const NOTE_SAVE_DELAY_MS = 800;

export interface NoteAutosaveOptions {
  /** The note as the latest server response has it ("" for none). */
  serverValue: string;
  /** Write ``text``; resolves once the response is applied. */
  save: (text: string) => Promise<void>;
  /** After a 409: reload the stage and return its note, or null when the reload failed. */
  reload: () => Promise<string | null>;
  delayMs?: number;
}

export interface NoteAutosave {
  draft: string;
  onChange: (text: string) => void;
  /** Save now (blur). */
  flush: () => void;
  saving: boolean;
  issue: SaveIssue | null;
  retry: () => void;
  dismiss: () => void;
}

const isConflict = (e: unknown) => e instanceof ApiError && e.status === 409;
const message = (e: unknown) => (e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e));

export function useNoteAutosave({ serverValue, save, reload, delayMs = NOTE_SAVE_DELAY_MS }: NoteAutosaveOptions): NoteAutosave {
  const [draft, setDraft] = useState(serverValue);
  const [saving, setSaving] = useState(false);
  const [issue, setIssue] = useState<SaveIssue | null>(null);
  // The server's note this tab last agreed with: what the draft is compared
  // against to know whether anything is unsaved.
  const baseRef = useRef(serverValue);
  const draftRef = useRef(serverValue);
  const inFlightRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveRef = useRef(save);
  const reloadRef = useRef(reload);
  saveRef.current = save;
  reloadRef.current = reload;
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // Follow the server while nothing local is unsaved.
  useEffect(() => {
    if (inFlightRef.current || timerRef.current !== null) return;
    if (draftRef.current !== baseRef.current) return;
    baseRef.current = serverValue;
    draftRef.current = serverValue;
    setDraft(serverValue);
  }, [serverValue]);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const send = useCallback(async (resent: boolean): Promise<void> => {
    clearTimer();
    if (inFlightRef.current) return;
    const text = draftRef.current;
    if (text === baseRef.current) return;
    const startedFrom = baseRef.current;
    inFlightRef.current = true;
    if (alive.current) setSaving(true);
    let next: "again" | "done" = "done";
    try {
      await saveRef.current(text);
      baseRef.current = text;
      if (alive.current) setIssue(null);
      next = draftRef.current !== text ? "again" : "done";
    } catch (e) {
      if (isConflict(e)) {
        const fresh = await reloadRef.current().catch(() => null);
        if (fresh === null) {
          if (alive.current) setIssue({ kind: "failed", message: message(e) });
        } else if (fresh === startedFrom && !resent) {
          // Awaited, so ``finally`` below runs after the re-send, not under it.
          inFlightRef.current = false;
          return await send(true);
        } else {
          // Another writer's note wins; the draft takes it.
          baseRef.current = fresh;
          draftRef.current = fresh;
          if (alive.current) {
            setDraft(fresh);
            setIssue({ kind: "discarded" });
          }
        }
      } else if (alive.current) {
        setIssue({ kind: "failed", message: message(e) });
      }
    } finally {
      inFlightRef.current = false;
      if (alive.current) setSaving(false);
    }
    if (next === "again") return send(false);
  }, []);

  const flush = useCallback(() => {
    void send(false);
  }, [send]);

  const onChange = useCallback(
    (text: string) => {
      draftRef.current = text;
      setDraft(text);
      clearTimer();
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        void send(false);
      }, delayMs);
    },
    [delayMs, send],
  );

  // Leaving (another shot, another stage) saves what was typed.
  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        clearTimer();
        void send(false);
      }
    },
    [send],
  );

  return {
    draft,
    onChange,
    flush,
    saving,
    issue,
    retry: flush,
    dismiss: () => setIssue(null),
  };
}
