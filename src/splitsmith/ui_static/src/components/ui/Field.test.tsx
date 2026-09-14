import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Field, inputClass } from "./Field";

describe("Field", () => {
  it("associates the label with the control and announces the error", () => {
    render(
      <Field label="Display name" htmlFor="dn" hint="3 / 60" help="Shown on comments" error="Too long">
        <input id="dn" className={inputClass} defaultValue="Mat" />
      </Field>,
    );
    expect(screen.getByLabelText("Display name")).toHaveValue("Mat");
    expect(screen.getByText("3 / 60")).toBeInTheDocument();
    expect(screen.getByText("Shown on comments")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Too long");
  });
});
