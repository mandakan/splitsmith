import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExportWarnings } from "@/components/export/ExportWarnings";

describe("ExportWarnings", () => {
  it("says what the render left out, not only how much", () => {
    render(
      <ExportWarnings
        anomalies={["the title page card 'Bromma' was left out: its template card.html failed (line 4: boom)"]}
      />,
    );
    expect(screen.getByRole("list", { name: "Warnings" }).textContent).toContain("line 4: boom");
  });

  it("renders nothing without warnings", () => {
    const { container } = render(<ExportWarnings anomalies={[]} />);
    expect(container.textContent).toBe("");
  });
});
