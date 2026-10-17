/**
 * Frames per second for frame steps and frame numbers: the primary
 * camera's clip as the server read it from the MP4, else 30. The cameras
 * splitsmith sees record at 25 to 60; anything outside 1-240 is a broken
 * file, not a frame rate.
 */
import type { CoachVideoEntry } from "@/lib/api";

export const DEFAULT_FPS = 30;

export function clipFps(videos: CoachVideoEntry[] | undefined): number {
  const fps = videos?.find((v) => v.role === "primary")?.fps;
  return typeof fps === "number" && fps >= 1 && fps <= 240 ? fps : DEFAULT_FPS;
}
