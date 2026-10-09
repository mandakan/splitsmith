import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { LogoPlanField } from "@/components/export/LogoPlanField";
import type { LogoSpot } from "@/lib/logoPlan";

function Harness({ start }: { start: LogoSpot[] }) {
  const [spots, setSpots] = useState<LogoSpot[]>(start);
  return (
    <>
      <LogoPlanField spots={spots} onChange={setSpots} busy={false} />
      <output data-testid="spots">{spots.join(",")}</output>
    </>
  );
}

const pressed = (name: string) => screen.getByRole("button", { name }).getAttribute("aria-pressed");

describe("LogoPlanField", () => {
  it("shows Polished for the default and says what it adds", () => {
    render(<Harness start={["summaries", "thumbnail", "wipe"]} />);
    expect(pressed("Polished")).toBe("true");
    expect(screen.queryByLabelText("Your brand on the wipe")).toBeNull();
    expect(screen.getByText(/Also the shooter's logo on the summaries, a designed thumbnail and your brand on the wipe/)).toBeInTheDocument();
  });

  it("Cards only clears every spot", () => {
    render(<Harness start={["summaries", "thumbnail", "wipe"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Cards only" }));
    expect(screen.getByTestId("spots")).toHaveTextContent(/^$/);
    expect(screen.getByText(/Nowhere else\./)).toBeInTheDocument();
  });

  it("Choose keeps the spots and opens one checkbox per spot", () => {
    render(<Harness start={["summaries", "thumbnail", "wipe"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Choose" }));
    expect(pressed("Choose")).toBe("true");
    fireEvent.click(screen.getByLabelText("Your brand on the wipe"));
    expect(screen.getByTestId("spots")).toHaveTextContent(/^summaries,thumbnail$/);
    // Ticking it back to the Polished set keeps Choose open.
    fireEvent.click(screen.getByLabelText("Your brand on the wipe"));
    expect(pressed("Choose")).toBe("true");
    expect(screen.getByTestId("spots")).toHaveTextContent("summaries,thumbnail,wipe");
  });

  it("Everything adds your brand over the footage", () => {
    render(<Harness start={["summaries", "thumbnail", "wipe"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Everything" }));
    expect(screen.getByTestId("spots")).toHaveTextContent("summaries,thumbnail,watermark,wipe");
    expect(pressed("Everything")).toBe("true");
  });

  it("opens on Choose for a set no preset names", () => {
    render(<Harness start={["wipe"]} />);
    expect(pressed("Choose")).toBe("true");
    expect(screen.getByLabelText("Your brand on the wipe")).toBeChecked();
    expect(screen.getByLabelText("Shooter logo on the summaries")).not.toBeChecked();
  });
});
