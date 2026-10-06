import { useEffect } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes, useParams } from "react-router-dom";
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
    expect(await screen.findByText("Something went wrong on this page.")).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    spy.mockRestore();
  });

  it("words a page's own render error as such, not as a load failure", async () => {
    function Broken(): never {
      throw new Error("render bug");
    }
    const Page = lazyPage(async () => ({ Broken }), "Broken");
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<Page />);
    expect(await screen.findByText("Something went wrong on this page.")).toBeInTheDocument();
    expect(screen.queryByText("This page couldn't load.")).toBeNull();
    spy.mockRestore();
  });

  it("recovers when navigation moves to another stage of the same page", async () => {
    function Stage() {
      const { id } = useParams();
      if (id === "2") throw new Error("stage 2 is broken");
      return (
        <div>
          <p>stage {id}</p>
        </div>
      );
    }
    const Page = lazyPage(async () => ({ Stage }), "Stage");
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <MemoryRouter initialEntries={["/a/2"]}>
        <Link to="/a/3">next</Link>
        <Routes>
          <Route path="/a/:id" element={<Page />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Something went wrong on this page.")).toBeInTheDocument();
    fireEvent.click(screen.getByText("next"));
    expect(await screen.findByText("stage 3")).toBeInTheDocument();
    spy.mockRestore();
  });

  it("never reloads when the reload flag cannot be stored (no loop)", async () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const Page = lazyPage<{ who: string }>(
      () => Promise.reject(new TypeError("Failed to fetch dynamically imported module: /assets/Audit-abc.js")),
      "Hello",
    );
    render(<Page who="anna" />);
    expect(await screen.findByText("This page couldn't load.")).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    getItem.mockRestore();
    setItem.mockRestore();
    spy.mockRestore();
  });

  it("keeps the page mounted across a healthy navigation between stages", async () => {
    let mounts = 0;
    function Stage() {
      const { id } = useParams();
      useEffect(() => {
        mounts += 1;
      }, []);
      return <p>stage {id}</p>;
    }
    const Page = lazyPage(async () => ({ Stage }), "Stage");
    render(
      <MemoryRouter initialEntries={["/a/2"]}>
        <Link to="/a/3">next</Link>
        <Routes>
          <Route path="/a/:id" element={<Page />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByText("stage 2");
    fireEvent.click(screen.getByText("next"));
    await screen.findByText("stage 3");
    expect(mounts).toBe(1);
  });
});
