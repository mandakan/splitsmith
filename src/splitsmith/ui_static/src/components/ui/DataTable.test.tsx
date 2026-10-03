import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Table, Td, Th, Tr } from "./DataTable";

describe("DataTable", () => {
  it("numeric cells are right-aligned numerals; current row carries the led inset; dim cells are subtle", () => {
    render(
      <Table>
        <thead>
          <tr>
            <Th>Stage</Th>
            <Th align="right">Draw</Th>
          </tr>
        </thead>
        <tbody>
          <Tr current>
            <Td kind="ordinal">03</Td>
            <Td kind="num">1.97</Td>
          </Tr>
          <Tr>
            <Td kind="name" dim>
              B5 All
            </Td>
            <Td kind="num" dim>
              1.93
            </Td>
          </Tr>
        </tbody>
      </Table>,
    );
    expect(screen.getByText("Draw").className).toMatch(/text-right/);
    expect(screen.getByText("Draw").className).toMatch(/uppercase/);
    expect(screen.getByText("1.97").className).toMatch(/numeral/);
    expect(screen.getByText("1.97").className).toMatch(/text-right/);
    expect(screen.getByText("03").closest("tr")!.className).toMatch(/bg-surface-2/);
    expect(screen.getByText("1.93").className).toMatch(/text-subtle/);
  });

  it("the scroller contains absolutely positioned cell content (an sr-only header) so it cannot widen the page", () => {
    render(
      <Table>
        <thead>
          <tr>
            <Th>
              <span className="sr-only">Actions</span>
            </Th>
          </tr>
        </thead>
      </Table>,
    );
    const scroller = screen.getByText("Actions").closest(".overflow-x-auto")!;
    expect(scroller.className).toMatch(/\brelative\b/);
  });
});
