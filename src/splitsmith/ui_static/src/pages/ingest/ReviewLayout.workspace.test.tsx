/**
 * The three-region ingest workspace (clip list / player / stage drawer) must
 * have a DEFINITE height at the lg breakpoint, with its single row clamped via
 * minmax(0,1fr). With only a min-height, the implicit grid row auto-sizes to
 * the tallest column - a long clip list - so the list never scrolls internally
 * and the video pane stretches to thousands of px, pushing the letterboxed
 * picture out of view (bug report 2026-08-10, HFO Masters ingest).
 *
 * jsdom performs no layout, so this pins the class contract; the geometry was
 * verified headlessly against a live project (row 1546px -> 630px at 70vh,
 * list scroller 1506px content in 591px client).
 */
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { MatchProject, ShooterListEntry } from "@/lib/api";
import { ReviewLayout } from "@/pages/ingest/ReviewLayout";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      suggestCoverage: vi.fn().mockResolvedValue({ covers_stages: [] }),
    },
  };
});

const project = {
  name: "Test Match",
  stages: [
    {
      stage_number: 1,
      stage_name: "B50",
      videos: [
        {
          video_id: "v1",
          path: "raw/a.mp4",
          role: "primary",
          beep_time: null,
          match_timestamp: null,
          proxy_ready: true,
        },
      ],
    },
  ],
  unassigned_videos: [],
  raw_videos: [],
} as unknown as MatchProject;

const shooters = [
  { slug: "alice", name: "Alice", video_count: 1 },
] as unknown as ShooterListEntry[];

describe("ReviewLayout workspace sizing", () => {
  it("gives the three-region grid a definite lg height with a clamped row", () => {
    const { container } = render(
      <MemoryRouter>
        <ReviewLayout
          slug="alice"
          project={project}
          shooters={shooters}
          lastImportedPaths={null}
          moveBlocked={[]}
          onDismissBanner={() => {}}
          onMoveShooter={async () => {}}
          onAddMore={() => {}}
          onMoveAssignment={async () => true}
          onRemoveVideo={async () => {}}
          onConfirm={() => {}}
          onSaved={async () => {}}
          busy={false}
          lastScannedDir={null}
          onError={() => {}}
          beepPending={0}
        />
      </MemoryRouter>,
    );
    const grid = container.querySelector(".min-h-\\[70vh\\]");
    expect(grid).not.toBeNull();
    // Definite height so grid children (h-full chains) resolve and the clip
    // list scrolls inside the workspace instead of growing the page.
    expect(grid!.className).toContain("lg:h-[70vh]");
    // Clamp the implicit row: without minmax(0,1fr) an over-tall column
    // still stretches the row past the definite grid height.
    expect(grid!.className).toContain("lg:grid-rows-[minmax(0,1fr)]");
  });
});
