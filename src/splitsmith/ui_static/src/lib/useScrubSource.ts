/**
 * The Audit players' scrub source: the full-resolution preference (a
 * machine-level setting, local mode only) plus the videos whose rendition
 * failed to play on this page. ``choose`` is what a player calls where it
 * used to pin ``kind=trim``; ``markFailed`` is its ``error`` handler.
 */
import { useCallback, useEffect, useState } from "react";

import { api, type StageVideo } from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import { scrubSource, type ScrubChoice } from "@/lib/scrubSource";

export function useScrubSource() {
  const { mode, resolved } = useDeploymentMode();
  const [fullRes, setFullResState] = useState(false);
  const [available, setAvailable] = useState(false);
  const [failed, setFailed] = useState<ReadonlySet<string>>(() => new Set());

  useEffect(() => {
    if (!resolved || mode !== "local") return;
    let alive = true;
    api
      .getScrubSettings()
      .then((s) => {
        if (!alive) return;
        setFullResState(s.full_res_scrub);
        setAvailable(true);
      })
      .catch(() => {
        // The switch stays hidden; the rendition still plays.
      });
    return () => {
      alive = false;
    };
  }, [mode, resolved]);

  const setFullRes = useCallback((value: boolean) => {
    setFullResState(value);
    void api.setScrubSettings(value).catch(() => {
      // Kept for this page; the next load reads the saved value.
    });
  }, []);

  const markFailed = useCallback((path: string) => {
    setFailed((prev) => (prev.has(path) ? prev : new Set(prev).add(path)));
  }, []);

  const choose = useCallback(
    (video: Pick<StageVideo, "path" | "trim_version" | "scrub_version">): ScrubChoice =>
      scrubSource({
        trimVersion: video.trim_version,
        scrubVersion: video.scrub_version,
        fullRes,
        failed: failed.has(video.path),
      }),
    [fullRes, failed],
  );

  return { fullRes, available, setFullRes, choose, markFailed };
}
