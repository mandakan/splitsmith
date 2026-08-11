import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Snackbar } from "@/components/Snackbar";

describe("Snackbar", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("renders nothing visible when snack is null but keeps the live region", () => {
    render(<Snackbar snack={null} onDismiss={() => {}} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("shows the message and fires the action", () => {
    const onAction = vi.fn();
    const onDismiss = vi.fn();
    render(
      <Snackbar
        snack={{ message: "Shot 03 - Movement", tone: "status", actionLabel: "Undo", onAction }}
        onDismiss={onDismiss}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Shot 03 - Movement");
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("auto-dismisses after the timeout", () => {
    const onDismiss = vi.fn();
    render(<Snackbar snack={{ message: "saved", tone: "status" }} onDismiss={onDismiss} />);
    act(() => vi.advanceTimersByTime(6000));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("errors use an assertive alert region and do not auto-dismiss", () => {
    const onDismiss = vi.fn();
    render(<Snackbar snack={{ message: "patch failed", tone: "error" }} onDismiss={onDismiss} />);
    expect(screen.getByRole("alert")).toHaveTextContent("patch failed");
    act(() => vi.advanceTimersByTime(20000));
    expect(onDismiss).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
