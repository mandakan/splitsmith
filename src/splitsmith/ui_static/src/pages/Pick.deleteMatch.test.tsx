/**
 * Delete lives on the Matches row menu, for every row including one whose
 * folder is gone (which cannot be opened, so the old Export-footer delete
 * never reached it). The confirm names which copy is deleted and which is
 * kept; hosted and desktop deletes are separate actions.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type RecentProjectDetail } from "@/lib/api";
import { ConfirmProvider } from "@/components/useConfirm";
import { ModeProvider } from "@/lib/mode";
import { Pick } from "@/pages/Pick";

const mode = vi.hoisted(() => ({ value: "local" as "local" | "hosted" }));

vi.mock("@/lib/features", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/features")>();
  return { ...actual, useDeploymentMode: () => ({ mode: mode.value, resolved: true }) };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getHealth: vi.fn().mockResolvedValue({ status: "ok", version: "1", bound: false }),
      getScoreboardIdentity: vi.fn().mockResolvedValue(null),
      getRecentProjectsDetail: vi.fn(),
      deleteProject: vi.fn(),
    },
  };
});

function row(over: Partial<RecentProjectDetail>): RecentProjectDetail {
  return {
    path: "/m/a",
    name: "Match A",
    last_opened_at: "2026-09-27T00:00:00Z",
    kind: "match",
    match_id: "a",
    shooter_count: 1,
    stage_count: 4,
    stages_audited: 0,
    video_count: 1,
    match_date: null,
    club: null,
    last_modified_at: null,
    status: "in_progress",
    manual: false,
    shooter_names: ["Anna"],
    origin: "local",
    synced: false,
    next_step: null,
    ...over,
  };
}

function renderPick() {
  return render(
    <MemoryRouter initialEntries={["/pick"]}>
      <ModeProvider>
        <ConfirmProvider>
          <Pick />
        </ConfirmProvider>
      </ModeProvider>
    </MemoryRouter>,
  );
}

async function openDelete(name: string) {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: `${name} actions` }));
  await user.click(screen.getByRole("menuitem"));
  return { user, dialog: await screen.findByRole("dialog") };
}

describe("Matches row delete", () => {
  beforeEach(() => {
    vi.mocked(api.deleteProject).mockReset();
    vi.mocked(api.deleteProject).mockResolvedValue({
      summary: { match_id: null, errors: [] } as never,
      projects: [],
    });
    mode.value = "local";
  });

  it("removes a row whose folder is gone, which cannot be opened", async () => {
    const missing = row({ path: "/gone/ess", name: "ESs Black Handgun 2026", kind: "missing" });
    vi.mocked(api.getRecentProjectsDetail).mockResolvedValueOnce([missing]).mockResolvedValueOnce([]);
    renderPick();
    expect(await screen.findByRole("button", { name: "Open ESs Black Handgun 2026" })).toBeDisabled();

    const { user, dialog } = await openDelete("ESs Black Handgun 2026");
    expect(within(dialog).getByText(/only removes the entry from this list/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));

    await waitFor(() =>
      expect(api.deleteProject).toHaveBeenCalledWith("/gone/ess", {
        deleteLocalFiles: false,
        deleteRawUploads: false,
      }),
    );
    await waitFor(() => expect(screen.queryByText("ESs Black Handgun 2026")).not.toBeInTheDocument());
  });

  it("desktop: a synced match says the hosted copy is kept", async () => {
    vi.mocked(api.getRecentProjectsDetail).mockResolvedValue([row({ synced: true })]);
    renderPick();
    const { dialog } = await openDelete("Match A");
    expect(within(dialog).getByText(/copy on splitsmith\.app is not deleted/)).toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox", { name: /project folder on disk/ })).not.toBeChecked();
  });

  it("hosted: a desktop-synced match says the desktop copy is kept", async () => {
    mode.value = "hosted";
    vi.mocked(api.getRecentProjectsDetail).mockResolvedValue([row({ origin: "desktop" })]);
    renderPick();
    const { user, dialog } = await openDelete("Match A");
    expect(within(dialog).getByText(/synced from a desktop, and that copy is not deleted/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete match" }));
    await waitFor(() => expect(api.deleteProject).toHaveBeenCalledTimes(1));
  });
});
