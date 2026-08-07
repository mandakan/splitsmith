/**
 * ShellChrome context (#550).
 *
 * The contract inner shells rely on: they can find the slot RootLayout
 * published, and declaring an accent resets when the shell unmounts so a
 * dev-mode cyan hairline never leaks onto a match surface.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ShellChromeProvider,
  useShellAccent,
  useShellContextSlot,
  useShellOwnsMobileAccount,
  type ShellChromeValue,
} from "@/components/layout/shellChromeContext";

function SlotReader() {
  const slot = useShellContextSlot();
  return <div data-testid="slot">{slot ? slot.id : "none"}</div>;
}

function AccentDeclarer({ accent }: { accent: "led" | "beep" }) {
  useShellAccent(accent);
  return null;
}

function OwnsMobileDeclarer() {
  useShellOwnsMobileAccount();
  return null;
}

function makeValue(over: Partial<ShellChromeValue> = {}): ShellChromeValue {
  return {
    contextSlot: null,
    setAccent: vi.fn(),
    setOwnsMobileAccount: vi.fn(),
    ...over,
  };
}

describe("ShellChrome context", () => {
  it("hands the published slot to a consumer", () => {
    const el = document.createElement("div");
    el.id = "ctx-slot";
    render(
      <ShellChromeProvider value={makeValue({ contextSlot: el })}>
        <SlotReader />
      </ShellChromeProvider>,
    );
    expect(screen.getByTestId("slot")).toHaveTextContent("ctx-slot");
  });

  it("returns null outside a provider rather than throwing", () => {
    render(<SlotReader />);
    expect(screen.getByTestId("slot")).toHaveTextContent("none");
  });

  it("declares the accent on mount", () => {
    const setAccent = vi.fn();
    render(
      <ShellChromeProvider value={makeValue({ setAccent })}>
        <AccentDeclarer accent="beep" />
      </ShellChromeProvider>,
    );
    expect(setAccent).toHaveBeenCalledWith("beep");
  });

  it("resets the accent to led when the declaring shell unmounts", () => {
    const setAccent = vi.fn();
    const { unmount } = render(
      <ShellChromeProvider value={makeValue({ setAccent })}>
        <AccentDeclarer accent="beep" />
      </ShellChromeProvider>,
    );
    setAccent.mockClear();
    unmount();
    expect(setAccent).toHaveBeenCalledWith("led");
  });

  it("lets a shell claim the mobile account menu, and releases it", () => {
    const setOwnsMobileAccount = vi.fn();
    const { unmount } = render(
      <ShellChromeProvider value={makeValue({ setOwnsMobileAccount })}>
        <OwnsMobileDeclarer />
      </ShellChromeProvider>,
    );
    expect(setOwnsMobileAccount).toHaveBeenCalledWith(true);
    setOwnsMobileAccount.mockClear();
    unmount();
    expect(setOwnsMobileAccount).toHaveBeenCalledWith(false);
  });
});
