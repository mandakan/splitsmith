import type {
  BulkCameraSetItem,
  CameraMount,
  MatchProject,
  StageEntry,
  StageVideo,
} from "@/lib/api";

export function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export interface CameraGroup {
  id: string;
  label: string;
  make: string | null;
  model: string | null;
  mount: CameraMount | null;
  videoCount: number;
  videoPaths: Set<string>;
  members: BulkCameraSetItem[];
}

/** Group assigned videos by make+model+mount and label them Camera A/B/C. */
export function groupByCamera(
  assigned: { video: StageVideo; stage: StageEntry }[],
): CameraGroup[] {
  const map = new Map<string, CameraGroup>();
  for (const { video, stage } of assigned) {
    const key = `${video.camera_make ?? ""}|${video.camera_model ?? ""}|${video.camera_mount ?? ""}`;
    let g = map.get(key);
    if (!g) {
      g = {
        id: key,
        label: "",
        make: video.camera_make,
        model: video.camera_model,
        mount: video.camera_mount,
        videoCount: 0,
        videoPaths: new Set(),
        members: [],
      };
      map.set(key, g);
    }
    g.videoCount += 1;
    g.videoPaths.add(video.path);
    g.members.push({ stage_number: stage.stage_number, video_id: video.video_id });
  }
  const groups = Array.from(map.values());
  groups.forEach((g, i) => {
    g.label = `Camera ${String.fromCharCode(65 + i)}`;
  });
  return groups;
}

/** A single clip in the ingest list. stageNumber === null means unassigned. */
export interface ClipItem {
  video: StageVideo;
  stageNumber: number | null;
  camera?: CameraGroup;
}

export interface StageGroup {
  stage: StageEntry;
  clips: ClipItem[];
}

export interface ClipModel {
  /** Flat keyboard-nav order: unassigned first, then per-stage in stage order. */
  order: ClipItem[];
  unassigned: ClipItem[];
  stageGroups: StageGroup[];
  cameras: CameraGroup[];
  totalVideos: number;
  assignedCount: number;
  remaining: number;
  willProcess: number;
  ignoredCount: number;
}

/**
 * Derive the whole ingest view model from a project. Unassigned videos sort
 * by capture timestamp (timestamp-less sink below, stable); assigned videos
 * keep their per-stage order. Cameras are grouped from assigned videos only,
 * matching the prior behavior.
 */
export function buildClipModel(project: MatchProject): ClipModel {
  const assigned: { video: StageVideo; stage: StageEntry }[] = project.stages.flatMap(
    (s) => (s.videos ?? []).map((video) => ({ video, stage: s })),
  );
  const cameras = groupByCamera(assigned);
  // Path -> camera index so per-clip lookups are O(1) instead of scanning
  // every camera group for each of N videos.
  const cameraByPath = new Map<string, CameraGroup>();
  for (const c of cameras) {
    for (const p of c.videoPaths) cameraByPath.set(p, c);
  }
  const cameraFor = (path: string): CameraGroup | undefined =>
    cameraByPath.get(path);

  const unassignedSorted = (project.unassigned_videos ?? [])
    .map((v, i) => ({ v, i }))
    .sort((a, b) => {
      const ta = a.v.match_timestamp;
      const tb = b.v.match_timestamp;
      if (ta && tb) {
        const cmp = ta.localeCompare(tb);
        return cmp !== 0 ? cmp : a.i - b.i;
      }
      if (ta) return -1;
      if (tb) return 1;
      return a.i - b.i;
    })
    .map((x) => x.v);

  const unassigned: ClipItem[] = unassignedSorted.map((v) => ({
    video: v,
    stageNumber: null,
    camera: cameraFor(v.path),
  }));

  const stageGroups: StageGroup[] = project.stages
    .map((stage) => ({
      stage,
      clips: (stage.videos ?? []).map((v) => ({
        video: v,
        stageNumber: stage.stage_number,
        camera: cameraFor(v.path),
      })),
    }))
    .filter((g) => g.clips.length > 0);

  const order: ClipItem[] = [
    ...unassigned,
    ...stageGroups.flatMap((g) => g.clips),
  ];

  const assignedCount = assigned.length;
  const totalVideos = assignedCount + unassigned.length;
  const willProcess = assigned.filter((a) => a.video.role !== "ignored").length;
  const ignoredCount =
    assigned.filter((a) => a.video.role === "ignored").length +
    unassignedSorted.filter((v) => v.role === "ignored").length;

  return {
    order,
    unassigned,
    stageGroups,
    cameras,
    totalVideos,
    assignedCount,
    remaining: unassigned.length,
    willProcess,
    ignoredCount,
  };
}

/** Move selection by delta within the flat order, clamped to the ends. */
export function selectDelta(
  order: ClipItem[],
  selectedPath: string | null,
  delta: number,
): string | null {
  if (order.length === 0) return null;
  const idx = order.findIndex((c) => c.video.path === selectedPath);
  if (idx === -1) return order[0].video.path;
  const next = Math.min(order.length - 1, Math.max(0, idx + delta));
  return order[next].video.path;
}

/** First unassigned clip's path, or null if the queue is empty. */
export function firstUnassignedPath(model: ClipModel): string | null {
  return model.unassigned[0]?.video.path ?? null;
}

/**
 * The unassigned clip to select after assigning `path`: the next one after it
 * in the queue, else the previous, else null. Used for auto-advance so the
 * operator keeps clearing the pile without reaching for the mouse.
 */
export function nextUnassignedAfter(model: ClipModel, path: string): string | null {
  const idx = model.unassigned.findIndex((c) => c.video.path === path);
  if (idx === -1) return firstUnassignedPath(model);
  const after = model.unassigned[idx + 1];
  if (after) return after.video.path;
  const before = model.unassigned[idx - 1];
  return before ? before.video.path : null;
}
