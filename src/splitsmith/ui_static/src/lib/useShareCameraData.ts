/**
 * The share dialog's camera data: every shooter with more than one camera,
 * their choices and their saved default (``compare_camera``), loaded once
 * for both the default section and each link's editor
 * (``components/results/ShareCameras``).
 */
import { useCallback, useEffect, useState } from "react";

import { api } from "./api";
import { cameraChoices, currentChoice, type ShooterCameras } from "./shareCameras";

export interface ShooterRow extends ShooterCameras {
  /** The saved default as a choice (the primary's mount when none). */
  defaultValue: string;
}

/** The shooters with a camera to choose, loaded once for the dialog. */
export interface ShareCameraData {
  rows: ShooterRow[] | null;
  setDefault: (slug: string, value: string) => void;
}

export function useShareCameraData(): ShareCameraData {
  const [rows, setRows] = useState<ShooterRow[] | null>(null);
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const { shooters } = await api.listMatchShooters();
        const loaded = await Promise.all(
          shooters.map(async (s) => {
            const cameras = s.cameras ?? [];
            const choices = cameraChoices(cameras);
            const project = choices.length > 1 ? await api.getProject(s.slug).catch(() => null) : null;
            return {
              slug: s.slug,
              name: s.name,
              choices,
              defaultValue: currentChoice(project?.compare_camera, choices, cameras),
            };
          }),
        );
        if (alive) setRows(loaded.filter((r) => r.choices.length > 1));
      } catch {
        if (alive) setRows([]);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);
  const setDefault = useCallback((slug: string, value: string) => {
    setRows((prev) => prev?.map((r) => (r.slug === slug ? { ...r, defaultValue: value } : r)) ?? prev);
  }, []);
  return { rows, setDefault };
}

