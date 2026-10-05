/**
 * The Audit players' scrub source: the full-resolution preference (a
 * machine-level setting, local mode only) plus the videos whose rendition
 * failed to play on this page. ``choose`` is what a player calls where it
 * used to pin ``kind=trim``; ``markFailed`` is its ``error`` handler. A
 * failure is remembered per rendition (path + ``scrub_version``), so a
 * re-cut rendition gets a fresh chance instead of leaving the video on
 * the full-resolution trim for the rest of the session.
 */
import { useCallback, useEffect, useState } from "react";

import { api, type StageVideo } from "@/lib/api";
import { useDeploymentMode } from "@/lib/features";
import { scrubSource, type ScrubChoice } from "@/lib/scrubSource";

type ScrubVideo = Pick<StageVideo, "path" | "trim_version" | "scrub_version">;

const failureKey = (video: ScrubVideo) => `${video.path}\n${video.scrub_version ?? ""}`;

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

  const markFailed = useCallback((video: ScrubVideo) => {
    const key = failureKey(video);
    setFailed((prev) => (prev.has(key) ? prev : new Set(prev).add(key)));
  }, []);

  const choose = useCallback(
    (video: ScrubVideo): ScrubChoice =>
      scrubSource({
        trimVersion: video.trim_version,
        scrubVersion: video.scrub_version,
        fullRes,
        failed: failed.has(failureKey(video)),
      }),
    [fullRes, failed],
  );

  return { fullRes, available, setFullRes, choose, markFailed };
}
