/**
 * The corpus row link carries the active search/filter (#898): the
 * fixture detail page derives its prev/next walk from these params, so
 * dropping them here would silently turn "walk my filtered subset"
 * back into "walk the whole corpus".
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfirmProvider } from "@/components/useConfirm";
import type { DeveloperShellOutletContext } from "@/components/developer/DeveloperShell";
import { api, type LabFixtureRecord } from "@/lib/api";
import { DevCorpus } from "@/pages/dev/DevCorpus";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listLabFixtures: vi.fn(),
      getDevReviewQueue: vi.fn().mockResolvedValue({ pending: [], flagged: [] }),
      getRecentProjects: vi.fn().mockResolvedValue([]),
    },
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

function record(slug: string): LabFixtureRecord {
  return {
    slug,
    audit_path: `/fixtures/${slug}.json`,
    audio_path: `/fixtures/${slug}.wav`,
    has_audio: true,
    n_shots: 12,
    expected_rounds: 12,
    stage_time_seconds: 20,
    beep_time: 1.5,
    source: null,
    source_video: null,
    audit_mtime: 1,
    audio_mtime: 1,
    anchor_slug: null,
    event_id: "hfo-masters-2026:1",
    promoted_at: null,
    n_labeled_shots: 0,
    n_labeled_rejects: 0,
    in_calibration: false,
  };
}

const outletContext: DeveloperShellOutletContext = { model: null, refresh: () => {} };

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location">{`${loc.pathname}${loc.search}`}</div>;
}

describe("DevCorpus detail links", () => {
  it("carries the active search and filter into the fixture detail URL", async () => {
    vi.mocked(api.listLabFixtures).mockResolvedValue([
      record("alpha-one"),
      record("fixture-bravo"),
    ]);

    render(
      <MemoryRouter initialEntries={["/dev/corpus?match=m-1"]}>
        <ConfirmProvider>
          <Routes>
            <Route element={<Outlet context={outletContext} />}>
              <Route path="dev/corpus" element={<DevCorpus />} />
            </Route>
            <Route path="dev/corpus/:slug" element={<LocationProbe />} />
          </Routes>
        </ConfirmProvider>
      </MemoryRouter>,
    );

    await screen.findByRole("button", { name: "alpha-one" });
    await userEvent.type(screen.getByPlaceholderText(/search fixtures/i), "alpha");
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "fixture-bravo" })).toBeNull(),
    );

    await userEvent.click(screen.getByRole("button", { name: "alpha-one" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dev/corpus/alpha-one?match=m-1&q=alpha",
    );
  });
});
