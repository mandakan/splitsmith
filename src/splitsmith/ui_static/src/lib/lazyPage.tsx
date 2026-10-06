/**
 * Route-level code splitting (#894). Each page module loads on first
 * navigation instead of with the app, so the first load is smaller and a
 * test that imports ``@/App`` no longer transforms all ~28 pages.
 *
 * The Suspense boundary is per page: the shells (header, nav, stage list)
 * stay on screen while a page's chunk loads, and the placeholder is an
 * empty area holding the page's space -- no spinner for a load that takes
 * milliseconds locally and a few hundred on hosted.
 *
 * A tab left open across a deploy asks for chunk names the new build no
 * longer serves. That import failure triggers one full reload, which picks
 * up the new build; a sessionStorage flag keeps it to one, so a genuinely
 * broken chunk shows "This page couldn't load." with a Reload button
 * rather than a reload loop. The flag clears once any page loads, so the
 * next deploy can reload again.
 */
import { Component, lazy, Suspense, type ComponentType, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

export const CHUNK_RELOAD_KEY = "splitsmith:chunk-reload";

/** A chunk fetch that failed (Chromium / Firefox / Safari wordings). */
function isChunkLoadError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /dynamically imported module|Importing a module script failed|error loading dynamically imported module/i.test(msg);
}

function readFlag(): boolean {
  try {
    return sessionStorage.getItem(CHUNK_RELOAD_KEY) != null;
  } catch {
    return false;
  }
}

function writeFlag(on: boolean): void {
  try {
    if (on) sessionStorage.setItem(CHUNK_RELOAD_KEY, "1");
    else sessionStorage.removeItem(CHUNK_RELOAD_KEY);
  } catch {
    // Storage blocked: at worst a stale tab reloads once per failure.
  }
}

class PageLoadBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="flex flex-wrap items-center gap-3 px-7 py-8 text-md text-ink-2">
        <span>This page couldn&apos;t load.</span>
        <Button size="sm" onClick={() => window.location.reload()}>
          Reload
        </Button>
      </div>
    );
  }
}

export function lazyPage<P extends object = Record<string, never>>(
  load: () => Promise<Record<string, unknown>>,
  name: string,
): ComponentType<P> {
  const Lazy = lazy(async () => {
    try {
      const mod = await load();
      writeFlag(false);
      return { default: mod[name] as ComponentType<P> };
    } catch (err) {
      if (isChunkLoadError(err) && !readFlag()) {
        writeFlag(true);
        window.location.reload();
        // Keep suspending while the page reloads.
        return new Promise<{ default: ComponentType<P> }>(() => {});
      }
      throw err;
    }
  });
  function LazyPage(props: P) {
    return (
      <PageLoadBoundary>
        <Suspense fallback={<div data-page-loading className="min-h-[40vh]" />}>
          <Lazy {...props} />
        </Suspense>
      </PageLoadBoundary>
    );
  }
  LazyPage.displayName = `lazyPage(${name})`;
  return LazyPage;
}
