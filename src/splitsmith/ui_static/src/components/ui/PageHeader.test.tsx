import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("renders ordinal, title, sub-line, back link and actions", () => {
    render(
      <MemoryRouter>
        <PageHeader
          ordinal="03"
          title="B6 Rear"
          sub="Mathias Axell"
          back={{ label: "All stages", to: "/results" }}
          actions={<button>Share</button>}
        />
      </MemoryRouter>,
    );
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1).toHaveTextContent("03B6 Rear");
    expect(h1.className).toMatch(/font-display/);
    expect(screen.getByText("03").className).toMatch(/text-led/);
    expect(screen.getByRole("link", { name: /all stages/i })).toHaveAttribute("href", "/results");
    expect(screen.getByRole("button", { name: "Share" })).toBeInTheDocument();
    expect(screen.getByText("Mathias Axell")).toBeInTheDocument();
  });

  it("works without a router when there is no back link", () => {
    render(<PageHeader title="Splits" />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Splits");
  });
});
