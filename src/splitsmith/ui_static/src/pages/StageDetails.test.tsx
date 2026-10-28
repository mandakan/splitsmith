import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MatchShellOutletContext } from "@/components/match/MatchShell";
import { api, type ShooterListEntry, type StageDetailsView } from "@/lib/api";
import { StageDetails } from "@/pages/StageDetails";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, getStageDetails: vi.fn(), putStageDetails: vi.fn() },
  };
});

function view(over: Partial<StageDetailsView> = {}): StageDetailsView {
  return {
    slug: "henrik",
    name: "Henrik Berg",
    division: "Production Optics",
    power_factor: "minor",
    power_factor_set: false,
    ssi_url: "https://shootnscoreit.com/event/participant/23/512019/",
    scorecard_order: [],
    others: [{ slug: "mathias", name: "Mathias Axell", order: [2, 3, 1], from_scorecards: true }],
    stages: [1, 2, 3].map((n) => ({
      stage_number: n,
      stage_name: ["Now You See Me", "Weak Sandman", "Strong Sandman"][n - 1],
      skipped: false,
      time_seconds: null,
      time_manual: false,
      shot_order: null,
      scorecard: null,
    })),
    ...over,
  };
}

function OutletCtx({ ctx }: { ctx: MatchShellOutletContext }) {
  return <Outlet context={ctx} />;
}

function renderPage(caps: MatchShellOutletContext["capabilities"] = ["edit", "review"]) {
  const ctx: MatchShellOutletContext = {
    project: null,
    health: null,
    shooters: [
      { slug: "henrik", name: "Henrik Berg" },
      { slug: "mathias", name: "Mathias Axell" },
    ] as unknown as ShooterListEntry[],
    refresh: vi.fn(),
    origin: "local",
    capabilities: caps,
  };
  return render(
    <MemoryRouter initialEntries={["/match/m1/stage-details/henrik"]}>
      <Routes>
        <Route path="/match/:matchId" element={<OutletCtx ctx={ctx} />}>
          <Route path="stage-details/:slug" element={<StageDetails />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

const PASTE = [
  "Station\t\tHF\tTID\tPTS\tA\tC\tD\tM\tPROC\tNS\tVRF",
  "1. Now You See Me\t\t2,9180\t12,68\t37\t5\t4\t0\t0\t0\t0\tY",
  "3. Strong Sandman\t\t1,4665\t14,32\t23\t2\t7\t0\t1\t0\t0\tY",
  "13. Bonus\t\t1\t1\t1\t1\t0\t0\t0\t0\t0\tY",
].join("\n");

describe("Stage details", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getStageDetails).mockResolvedValue(view());
    vi.mocked(api.putStageDetails).mockImplementation(async () => view());
  });

  it("fills rows from a paste, warns on a mismatch, and saves them", async () => {
    renderPage();
    const box = await screen.findByLabelText("Paste results here");
    expect(screen.getByRole("link", { name: /Henrik on Shoot'n Score It/ })).toHaveAttribute(
      "href",
      "https://shootnscoreit.com/event/participant/23/512019/",
    );

    fireEvent.paste(box, { clipboardData: { getData: () => PASTE } });

    expect(screen.getByLabelText("Time, stage 01")).toHaveValue("12.68");
    expect(screen.getByText("2 rows read. Not used: 13. Bonus.")).toBeInTheDocument();
    expect(screen.getByText(/2 A, 7 C and 1 M give 21 points, the entered value is 23/)).toBeInTheDocument();
    expect(api.putStageDetails).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putStageDetails).toHaveBeenCalledTimes(1));
    const [slug, body] = vi.mocked(api.putStageDetails).mock.calls[0];
    expect(slug).toBe("henrik");
    expect(body.rows[0]).toMatchObject({ time_seconds: 12.68, alphas: 5, stage_points: 37 });
    expect(body.rows[0].manual).toContain("alphas");
  });

  it("works out points and hit factor from typed hits", async () => {
    renderPage();
    await screen.findByLabelText("Time, stage 02");
    const type = (label: string, value: string) =>
      fireEvent.change(screen.getByLabelText(`${label}, stage 02`), { target: { value } });
    type("Time", "20,95");
    for (const [label, value] of [["A", "9"], ["C", "1"], ["D", "0"], ["M", "0"], ["NS", "0"], ["Proc", "0"]]) type(label, value);

    expect(screen.getByLabelText("Points, stage 02")).toHaveValue("48");
    expect(screen.getByLabelText("Hit factor, stage 02")).toHaveValue("2.2912");
  });

  it("copies another shooter's order without saving, and blocks Save on a duplicate", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Copy order from Mathias" }));

    expect(screen.getByLabelText("Order, stage 01")).toHaveValue("3");
    expect(screen.getByLabelText("Order, stage 02")).toHaveValue("1");
    expect(api.putStageDetails).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Order, stage 01"), { target: { value: "1" } });
    expect(screen.getByText("Two stages have the same place in the order.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("is read only without the edit capability", async () => {
    renderPage(["review"]);
    expect(await screen.findByLabelText("Time, stage 01")).toBeDisabled();
    expect(screen.getByText(/edited on the desktop/)).toBeInTheDocument();
  });
});
