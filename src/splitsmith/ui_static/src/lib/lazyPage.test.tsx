import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CHUNK_RELOAD_KEY, lazyPage } from "@/lib/lazyPage";

function Hello({ who }: { who: string }) {
  return <p>hello {who}</p>;
}

const reload = vi.fn();

beforeEach(() => {
  sessionStorage.clear();
  reload.mockReset();
  vi.stubGlobal("location", { ...window.location, reload });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("lazyPage", () => {
  it("renders the page once its module loads, passing props through", async () => {
    const Page = lazyPage<{ who: string }>(async () => ({ Hello }), "Hello");
    render(<Page who="anna" />);
    expect(await screen.findByText("hello anna")).toBeInTheDocument();
  });

  it("holds the page's space with an empty placeholder while loading", () => {
    const Page = lazyPage<{ who: string }>(() => new Promise<{ Hello: typeof Hello }>(() => {}), "Hello");
    const { container } = render(<Page who="anna" />);
    expect(container.querySelector("[data-page-loading]")).not.toBeNull();
    expect(screen.queryByRole("status")).toBeNull(); // no spinner
  });

  it("reloads once when a chunk from an older deploy cannot be fetched", async () => {
    const Page = lazyPage<{ who: string }>(
      () => Promise.reject(new TypeError("Failed to fetch dynamically imported module: /assets/Audit-abc.js")),
      "Hello",
    );
    render(<Page who="anna" />);
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(sessionStorage.getItem(CHUNK_RELOAD_KEY)).not.toBeNull();
  });

  it("shows a reloadable error on a second chunk failure instead of reloading in a loop", async () => {
    sessionStorage.setItem(CHUNK_RELOAD_KEY, "1");
    const err = new TypeError("Failed to fetch dynamically imported module: /assets/Audit-abc.js");
    const Page = lazyPage<{ who: string }>(() => Promise.reject(err), "Hello");
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<Page who="anna" />);
    expect(await screen.findByText("This page couldn't load.")).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledTimes(1);
    spy.mockRestore();
  });

  it("clears the reload flag once a page loads, so the next deploy can reload again", async () => {
    sessionStorage.setItem(CHUNK_RELOAD_KEY, "1");
    const Page = lazyPage<{ who: string }>(async () => ({ Hello }), "Hello");
    render(<Page who="anna" />);
    await screen.findByText("hello anna");
    expect(sessionStorage.getItem(CHUNK_RELOAD_KEY)).toBeNull();
  });

  it("does not reload for an error that is not a chunk fetch", async () => {
    const Page = lazyPage<{ who: string }>(() => Promise.reject(new Error("boom in module init")), "Hello");
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<Page who="anna" />);
    expect(await screen.findByText("This page couldn't load.")).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});
