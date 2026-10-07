import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Avatar } from "@/components/ui/AvatarStack";

describe("Avatar with an identity (#1249)", () => {
  it("draws the logo in place of the initials", () => {
    const { container } = render(<Avatar initials="MA" name="Mathias Axell" logo="/logo.png" />);
    const img = container.querySelector("img");
    expect(img?.getAttribute("src")).toBe("/logo.png");
    expect(container.textContent).toBe("");
  });

  it("rings the avatar in the shooter's accent", () => {
    const { container } = render(<Avatar initials="MA" accent="#22aaee" />);
    expect((container.firstChild as HTMLElement).style.boxShadow).toMatch(/#22aaee|rgb\(34, 170, 238\)/i);
  });

  it("is unchanged for a shooter who set nothing", () => {
    const before = render(<Avatar initials="MA" seed="anna" />).container.innerHTML;
    const after = render(<Avatar initials="MA" seed="anna" accent={null} logo={null} />).container.innerHTML;
    expect(after).toBe(before);
  });
});
