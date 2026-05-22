/**
 * Lightweight directory-only filesystem picker used by the create-match
 * flow before any project exists (#322).
 *
 * Why a separate component instead of reusing ``FolderPicker``:
 * - The full picker is shooter-scoped (probes video files, generates
 *   thumbnails) and requires a bound project; this one only needs
 *   directory navigation and runs unbound.
 * - The create flow needs to pick a *parent* folder (e.g.
 *   ``~/Splitsmith/``) -- the project's own leaf gets generated from
 *   the match name. Showing video previews would just be visual noise.
 *
 * Modal shell + Shot Timer tokens. Esc / backdrop click cancel; Enter
 * on the action button confirms.
 */

import { ChevronRight, FolderOpen, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api, type FsListing } from "@/lib/api";
import { cn } from "@/lib/utils";

interface DirectoryPickerModalProps {
  /** Where to start. Defaults to the server's home-dir fallback. */
  initialPath?: string | null;
  /** Called with the absolute path of the chosen directory. */
  onSelect: (path: string) => void;
  onCancel: () => void;
}

export function DirectoryPickerModal({
  initialPath,
  onSelect,
  onCancel,
}: DirectoryPickerModalProps) {
  const [listing, setListing] = useState<FsListing | null>(null);
  const [path, setPath] = useState<string | null>(initialPath ?? null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const useButtonRef = useRef<HTMLButtonElement | null>(null);

  const load = useCallback(async (next?: string | null) => {
    setBusy(true);
    setError(null);
    try {
      const data = await api.listFolderUnbound(next ?? undefined);
      setListing(data);
      setPath(data.path);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load(initialPath ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Esc closes the modal; focus the primary action so Enter confirms.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCancel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  useEffect(() => {
    if (!busy && useButtonRef.current) {
      useButtonRef.current.focus();
    }
  }, [busy, path]);

  const dirs = listing?.entries.filter((e) => e.kind === "dir") ?? [];
  const breadcrumb = path ? path.split("/").filter(Boolean) : [];
  const sidebarBookmarks = listing?.suggested_starts ?? [];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Pick a parent folder"
      className="fixed inset-0 z-50 flex items-center justify-center bg-bg/70 p-4 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        className="relative flex h-[min(560px,85vh)] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-rule-strong bg-surface text-ink shadow-[0_24px_48px_-12px_rgba(0,0,0,0.7)]"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between gap-4 border-b border-rule px-5 py-3.5">
          <div>
            <h2 className="font-display text-sm font-bold uppercase tracking-[0.08em] text-ink">
              Pick a parent folder
            </h2>
            <p className="mt-0.5 font-mono text-[0.6875rem] uppercase tracking-[0.06em] text-muted">
              The project folder will be created inside the directory you choose.
            </p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            aria-label="Cancel"
            className="rounded-md p-1.5 text-subtle hover:bg-surface-2 hover:text-ink"
          >
            <X className="size-4" />
          </button>
        </header>

        <div className="flex min-h-0 flex-1">
          {/* Sidebar bookmarks */}
          <nav className="hidden w-44 shrink-0 overflow-y-auto border-r border-rule bg-bg-glow px-2 py-3 md:block">
            <div className="px-2 pb-1.5 font-mono text-[0.625rem] uppercase tracking-[0.1em] text-subtle">
              Shortcuts
            </div>
            {sidebarBookmarks.map((b) => (
              <button
                key={`${b.kind}-${b.path}`}
                type="button"
                onClick={() => void load(b.path)}
                className={cn(
                  "block w-full truncate rounded px-2 py-1.5 text-left font-mono text-[0.75rem] tabular-nums transition-colors",
                  path === b.path
                    ? "bg-led-tint text-led"
                    : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                )}
                title={b.path}
              >
                {b.label}
              </button>
            ))}
          </nav>

          {/* Main column */}
          <div className="flex min-w-0 flex-1 flex-col">
            <div className="flex items-center gap-1 overflow-x-auto border-b border-rule px-4 py-2 font-mono text-[0.75rem] tabular-nums text-ink-2">
              <button
                type="button"
                onClick={() => void load("/")}
                className="rounded px-1.5 py-0.5 text-subtle hover:bg-surface-2 hover:text-ink"
              >
                /
              </button>
              {breadcrumb.map((segment, i) => {
                const slicePath = "/" + breadcrumb.slice(0, i + 1).join("/");
                const isLast = i === breadcrumb.length - 1;
                return (
                  <span key={slicePath} className="flex items-center gap-1">
                    <ChevronRight className="size-3 text-whisper" />
                    <button
                      type="button"
                      onClick={() => void load(slicePath)}
                      disabled={isLast}
                      className={cn(
                        "truncate rounded px-1.5 py-0.5 hover:bg-surface-2 hover:text-ink",
                        isLast && "text-ink",
                      )}
                    >
                      {segment}
                    </button>
                  </span>
                );
              })}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto">
              {busy && !listing ? (
                <div className="px-4 py-6 font-mono text-xs uppercase tracking-[0.08em] text-muted">
                  Loading...
                </div>
              ) : error ? (
                <div className="m-4 rounded-md border border-rule-strong bg-led-tint px-3 py-2 text-sm text-ink-2">
                  {error}
                </div>
              ) : dirs.length === 0 ? (
                <div className="px-4 py-6 text-center font-mono text-xs uppercase tracking-[0.08em] text-muted">
                  No subfolders here. You can still use this folder.
                </div>
              ) : (
                <ul className="divide-y divide-rule">
                  {dirs.map((entry) => {
                    const childPath = path
                      ? path.endsWith("/")
                        ? `${path}${entry.name}`
                        : `${path}/${entry.name}`
                      : `/${entry.name}`;
                    return (
                      <li key={entry.name}>
                        <button
                          type="button"
                          onClick={() => void load(childPath)}
                          className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-2"
                        >
                          <FolderOpen className="size-4 text-subtle" />
                          <span className="truncate text-sm text-ink">
                            {entry.name}
                          </span>
                          <ChevronRight className="ml-auto size-4 text-whisper" />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-rule bg-surface-2 px-5 py-3.5">
          <div className="min-w-0 truncate font-mono text-[0.75rem] tabular-nums text-muted">
            {path ?? ""}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-rule px-3 py-1.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink-2 hover:border-rule-strong hover:bg-surface-3"
            >
              Cancel
            </button>
            <button
              ref={useButtonRef}
              type="button"
              onClick={() => path && onSelect(path)}
              disabled={!path || busy}
              className="rounded-md bg-led-fill px-3.5 py-1.5 font-mono text-[0.6875rem] font-bold uppercase tracking-[0.08em] text-ink shadow-[0_0_0_1px_var(--color-led-fill),0_0_18px_var(--color-led-glow)] hover:bg-led disabled:opacity-50 disabled:shadow-none"
            >
              Use this folder
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
