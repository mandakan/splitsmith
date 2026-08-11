/**
 * StageCompareLink - per-stage compare CTA shared by Results (share) + Home.
 * Covers: hidden below 2 comparable shooters; href round-trips the match vs
 * share context via useMatchHref.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { StageCompareLink } from "@/components/match/StageCompareLink";

function renderAt(path: string, routePattern: string, comparableCount: number) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path={routePattern}
          element={
            <StageCompareLink stageNumber={3} comparableCount={comparableCount} />
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("StageCompareLink", () => {
  it("renders nothing when fewer than 2 shooters are comparable", () => {
    renderAt("/match/m1/results", "/match/:matchId/*", 1);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("links to the owner compare route in a match context", () => {
    renderAt("/match/m1/results", "/match/:matchId/*", 2);
    const link = screen.getByRole("link", {
      name: /compare shooters on stage 3/i,
    });
    expect(link).toHaveAttribute("href", "/match/m1/compare/3");
  });

  it("links to the anonymous share compare route in a share context", () => {
    renderAt("/share/tok/results", "/share/:token/*", 3);
    const link = screen.getByRole("link", {
      name: /compare shooters on stage 3/i,
    });
    expect(link).toHaveAttribute("href", "/share/tok/compare/3");
  });
});
