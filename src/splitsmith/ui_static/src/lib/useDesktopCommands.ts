/**
 * The match's desktop commands (#1100) for a hosted page: loaded once,
 * then polled every 10 s only while a request is waiting or running.
 * ``enabled`` is false everywhere but a desktop-synced match, where it
 * costs nothing.
 */
import { useCallback, useEffect, useState } from "react";

import { api, apiErrorText, type DesktopCommand, type DesktopPresence } from "@/lib/api";
import { isActiveCommand } from "@/lib/desktopCommands";

export const DESKTOP_COMMAND_POLL_MS = 10_000;

export interface DesktopCommands {
  commands: DesktopCommand[];
  presence: DesktopPresence | null;
  error: string | null;
  requestRedetect: (slug: string, stageNumber: number) => Promise<void>;
  cancel: (id: string) => Promise<void>;
  refresh: () => Promise<void>;
}

export function useDesktopCommands(enabled: boolean): DesktopCommands {
  const [commands, setCommands] = useState<DesktopCommand[]>([]);
  const [presence, setPresence] = useState<DesktopPresence | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.listDesktopCommands();
      setCommands(list.commands);
      setPresence(list.presence);
      setError(null);
    } catch (e) {
      setError(apiErrorText(e, "Could not load desktop requests."));
    }
  }, []);

  useEffect(() => {
    if (enabled) void refresh();
  }, [enabled, refresh]);

  const anyActive = commands.some(isActiveCommand);
  useEffect(() => {
    if (!enabled || !anyActive) return;
    const timer = window.setInterval(() => void refresh(), DESKTOP_COMMAND_POLL_MS);
    return () => window.clearInterval(timer);
  }, [enabled, anyActive, refresh]);

  const requestRedetect = useCallback(
    async (slug: string, stageNumber: number) => {
      setError(null);
      try {
        await api.requestDesktopCommand({ kind: "shot_detect", slug, stage_number: stageNumber });
      } catch (e) {
        setError(apiErrorText(e, "Could not send the request."));
        return;
      }
      await refresh();
    },
    [refresh],
  );

  const cancel = useCallback(
    async (id: string) => {
      try {
        await api.cancelDesktopCommand(id);
      } catch (e) {
        setError(apiErrorText(e, "Could not cancel the request."));
        return;
      }
      await refresh();
    },
    [refresh],
  );

  return { commands, presence, error, requestRedetect, cancel, refresh };
}
