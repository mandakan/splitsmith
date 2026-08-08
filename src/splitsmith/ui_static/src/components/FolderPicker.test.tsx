/**
 * FolderPicker dialog (add-videos UX rework, spec 2026-08-08).
 *
 * Covers: whole-folder commit, N-files commit, commit error staying
 * open, selection reset on navigation, sidebar navigation (volume +
 * Computer), empty-folder rules (allowEmptyFolder on/off), and the
 * single-scroll-container layout contract.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FolderPicker } from "@/components/FolderPicker";
import { ApiError, api, type FsEntry, type FsListing } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listFolder: vi.fn(),
      listFolderUnbound: vi.fn(),
      probeFile: vi.fn().mockResolvedValue({
        duration: null,
        thumbnail_url: null,
        width: null,
        height: null,
        codec: null,
        size_bytes: null,
      }),
    },
  };
});

function entry(over: Partial<FsEntry> & { name: string; kind: FsEntry["kind"] }): FsEntry {
  return {
    video_count: null,
    size_bytes: null,
    mtime: null,
    duration: null,
    thumbnail_url: null,
    ...over,
  };
}

const moviesListing: FsListing = {
  path: "/Users/op/Movies",
  parent: "/Users/op",
  entries: [
    entry({ name: "match-day", kind: "dir", video_count: 3, mtime: 1754600000 }),
    entry({ name: "GH010001.MP4", kind: "video", size_bytes: 1024, mtime: 1754600100 }),
    entry({ name: "GH010002.MP4", kind: "video", size_bytes: 2048, mtime: 1754600200 }),
  ],
  suggested_starts: [
    { path: "/Users/op", label: "Home", kind: "home" },
    { path: "/Volumes/SDCARD", label: "SDCARD", kind: "removable" },
  ],
};

const matchDayListing: FsListing = {
  path: "/Users/op/Movies/match-day",
  parent: "/Users/op/Movies",
  entries: [entry({ name: "GH019999.MP4", kind: "video", mtime: 1754600300 })],
  suggested_starts: moviesListing.suggested_starts,
};

const dirsOnlyListing: FsListing = {
  path: "/Users/op/Empty",
  parent: "/Users/op",
  entries: [entry({ name: "sub", kind: "dir", video_count: 2 })],
  suggested_starts: moviesListing.suggested_starts,
};

function defaultProps() {
  return {
    slug: "alice",
    title: "Add footage",
    onCommitFolder: vi.fn<(path: string) => Promise<void>>().mockResolvedValue(undefined),
    onCommitFiles: vi
      .fn<(files: { path: string; mtime: number | null }[]) => Promise<void>>()
      .mockResolvedValue(undefined),
    onClose: vi.fn(),
  };
}

describe("FolderPicker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listFolder).mockResolvedValue(moviesListing);
    vi.mocked(api.listFolderUnbound).mockResolvedValue(dirsOnlyListing);
  });

  it("commits the whole folder and closes on success", async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<FolderPicker {...props} />);
    const button = await screen.findByRole("button", { name: /add this folder/i });
    await user.click(button);
    expect(props.onCommitFolder).toHaveBeenCalledWith("/Users/op/Movies");
    await waitFor(() => expect(props.onClose).toHaveBeenCalled());
  });

  it("commits N checked files with paths + mtimes", async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<FolderPicker {...props} />);
    await user.click(await screen.findByRole("checkbox", { name: /select GH010001/i }));
    await user.click(screen.getByRole("checkbox", { name: /select GH010002/i }));
    await user.click(screen.getByRole("button", { name: /add 2 files/i }));
    expect(props.onCommitFiles).toHaveBeenCalledWith([
      { path: "/Users/op/Movies/GH010001.MP4", mtime: 1754600100 },
      { path: "/Users/op/Movies/GH010002.MP4", mtime: 1754600200 },
    ]);
    await waitFor(() => expect(props.onClose).toHaveBeenCalled());
  });

  it("surfaces a commit error inline and stays open", async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    props.onCommitFolder.mockRejectedValue(new ApiError(400, "scan blew up"));
    render(<FolderPicker {...props} />);
    await user.click(await screen.findByRole("button", { name: /add this folder/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("scan blew up");
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("resets the file selection when navigating into a folder", async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<FolderPicker {...props} />);
    await user.click(await screen.findByRole("checkbox", { name: /select GH010001/i }));
    expect(screen.getByRole("button", { name: /add 1 file/i })).toBeInTheDocument();
    vi.mocked(api.listFolder).mockResolvedValue(matchDayListing);
    await user.click(screen.getByRole("button", { name: /match-day/i }));
    expect(
      await screen.findByRole("button", { name: /add this folder/i }),
    ).toBeInTheDocument();
  });

  it("navigates to a mounted volume from the Places sidebar", async () => {
    const user = userEvent.setup();
    render(<FolderPicker {...defaultProps()} />);
    await screen.findByRole("button", { name: /add this folder/i });
    await user.click(screen.getByRole("button", { name: "SDCARD" }));
    await waitFor(() =>
      expect(api.listFolder).toHaveBeenLastCalledWith(
        "alice",
        "/Volumes/SDCARD",
        expect.anything(),
      ),
    );
  });

  it("always offers a Computer entry that navigates to /", async () => {
    const user = userEvent.setup();
    render(<FolderPicker {...defaultProps()} />);
    await screen.findByRole("button", { name: /add this folder/i });
    await user.click(screen.getByRole("button", { name: "Computer" }));
    await waitFor(() =>
      expect(api.listFolder).toHaveBeenLastCalledWith("alice", "/", expect.anything()),
    );
  });

  it("disables the folder commit on a video-less folder unless allowEmptyFolder", async () => {
    vi.mocked(api.listFolderUnbound).mockResolvedValue(dirsOnlyListing);
    const props = defaultProps();
    const { unmount } = render(
      <FolderPicker
        {...props}
        slug={undefined}
        unbound
        contentMode="directories"
        onCommitFiles={undefined}
        title="Pick a parent folder"
        folderLabel="Use this folder"
      />,
    );
    expect(await screen.findByRole("button", { name: /use this folder/i })).toBeDisabled();
    unmount();

    render(
      <FolderPicker
        {...props}
        slug={undefined}
        unbound
        contentMode="directories"
        onCommitFiles={undefined}
        title="Pick a parent folder"
        folderLabel="Use this folder"
        allowEmptyFolder
      />,
    );
    expect(await screen.findByRole("button", { name: /use this folder/i })).toBeEnabled();
  });

  it("has exactly one scroll container (the listing) and no max-h-80 cap", async () => {
    const { baseElement } = render(<FolderPicker {...defaultProps()} />);
    await screen.findByRole("button", { name: /add this folder/i });
    expect(baseElement.querySelector(".max-h-80")).toBeNull();
    const listing = baseElement.querySelector("ul.overflow-y-auto");
    expect(listing).not.toBeNull();
    expect(listing!.className).toContain("flex-1");
    expect(listing!.className).toContain("min-h-0");
  });

  it("swaps the breadcrumb bar for a path input on the pencil affordance", async () => {
    const user = userEvent.setup();
    render(<FolderPicker {...defaultProps()} />);
    await screen.findByRole("button", { name: /add this folder/i });
    await user.click(screen.getByRole("button", { name: /edit path/i }));
    const input = screen.getByRole("textbox", { name: /folder path/i });
    await user.clear(input);
    await user.type(input, "/Volumes/SDCARD{Enter}");
    await waitFor(() =>
      expect(api.listFolder).toHaveBeenLastCalledWith(
        "alice",
        "/Volumes/SDCARD",
        expect.anything(),
      ),
    );
  });

  it("scopes Escape to the path editor first, and only a second Escape closes the dialog", async () => {
    const user = userEvent.setup();
    const props = defaultProps();
    render(<FolderPicker {...props} />);
    await screen.findByRole("button", { name: /add this folder/i });
    await user.click(screen.getByRole("button", { name: /edit path/i }));
    expect(screen.getByRole("textbox", { name: /folder path/i })).toHaveFocus();

    await user.keyboard("{Escape}");
    // Edit mode closed, but the dialog itself is still up.
    expect(screen.queryByRole("textbox", { name: /folder path/i })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(props.onClose).not.toHaveBeenCalled();

    await user.keyboard("{Escape}");
    expect(props.onClose).toHaveBeenCalled();
  });

  it("disables the primary action while the listing errored, even with allowEmptyFolder", async () => {
    const props = defaultProps();
    vi.mocked(api.listFolder).mockRejectedValue(new ApiError(500, "boom"));
    render(
      <FolderPicker {...props} allowEmptyFolder initialPath="/Users/op/Movies" />,
    );
    expect(await screen.findByRole("button", { name: /retry/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /add this folder/i })).toBeDisabled();
  });

  it("announces busy state in a polite live region, and clears it once loaded", async () => {
    let resolveListing: (value: FsListing) => void = () => {};
    const pending = new Promise<FsListing>((resolve) => {
      resolveListing = resolve;
    });
    vi.mocked(api.listFolder).mockReturnValue(pending);
    render(<FolderPicker {...defaultProps()} />);

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/reading folder/i);

    resolveListing(moviesListing);
    await screen.findByRole("button", { name: /add this folder/i });
    await waitFor(() => expect(status.textContent?.trim()).toBe(""));
  });
});
