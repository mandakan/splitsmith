import type {
  BulkCameraSetItem,
  CameraMount,
  MatchProject,
  StageEntry,
  StageVideo,
  VideoRole,
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

/**
 * Client-side mirror of the backend ``MatchProject.assign_video`` (project.py).
 * Returns a NEW project with ``videoPath`` moved to ``toStage`` (or back to the
 * unassigned tray when null), applying the same auto-primary-upgrade rule, so
 * the Ingest page can reflect a click instantly instead of waiting on the
 * server round-trip. The POST response is authoritative and reconciles any
 * divergence - this only needs to be close enough to avoid a visible flash.
 *
 * Placement in the arrays (not a field on the video) is what ``buildClipModel``
 * reads as a clip's stage, so the move is purely detach-from-one-array,
 * push-into-another. Unchanged videos keep their references; only the touched
 * video, its source array, and its target array are rebuilt (immutable so the
 * caller can roll back to the previous project on error).
 */
export function applyAssignmentLocally(
  project: MatchProject,
  videoPath: string,
  toStage: number | null,
  role: VideoRole,
): MatchProject {
  const moved =
    project.stages
      .flatMap((s) => s.videos ?? [])
      .find((v) => v.path === videoPath) ??
    (project.unassigned_videos ?? []).find((v) => v.path === videoPath) ??
    null;
  // Unknown path: leave the project untouched. The server will 404 and the
  // caller resyncs; guessing here would only desync the optimistic view.
  if (moved == null) return project;

  const detach = (list: StageVideo[]): StageVideo[] =>
    list.filter((v) => v.path !== videoPath);

  // Back to the unassigned tray: the server clears the role to "secondary".
  if (toStage == null) {
    return {
      ...project,
      stages: project.stages.map((s) => ({ ...s, videos: detach(s.videos ?? []) })),
      unassigned_videos: [
        ...detach(project.unassigned_videos ?? []),
        { ...moved, role: "secondary" },
      ],
    };
  }

  return {
    ...project,
    unassigned_videos: detach(project.unassigned_videos ?? []),
    stages: project.stages.map((s) => {
      const videos = detach(s.videos ?? []);
      if (s.stage_number !== toStage) return { ...s, videos };
      // Auto-upgrade: a "secondary" drop onto a stage with no primary becomes
      // the primary; an explicit primary demotes the incumbent.
      const hasPrimary = videos.some((v) => v.role === "primary");
      const effective: VideoRole =
        role === "secondary" && !hasPrimary ? "primary" : role;
      const rebased =
        effective === "primary"
          ? videos.map((v) => (v.role === "primary" ? { ...v, role: "secondary" as VideoRole } : v))
          : videos;
      return { ...s, videos: [...rebased, { ...moved, role: effective }] };
    }),
  };
}

/**
 * Client-side mirror of the backend ``MatchProject.remove_video`` (project.py)
 * for the default (``reset_audit=false``) path the Ingest page uses: drop the
 * video from whichever stage or the unassigned tray holds it. The backend does
 * not promote another video to primary on a plain removal, so neither do we.
 * The POST response is authoritative and reconciles any divergence - this only
 * needs to be close enough to make the clip disappear instantly on click
 * instead of after the round-trip.
 */
export function removeVideoLocally(
  project: MatchProject,
  videoPath: string,
): MatchProject {
  const detach = (list: StageVideo[]): StageVideo[] =>
    list.filter((v) => v.path !== videoPath);
  return {
    ...project,
    stages: project.stages.map((s) => ({ ...s, videos: detach(s.videos ?? []) })),
    unassigned_videos: detach(project.unassigned_videos ?? []),
  };
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
