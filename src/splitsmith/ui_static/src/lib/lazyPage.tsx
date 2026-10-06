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
 * rather than a reload loop. With storage blocked the flag cannot be kept,
 * so there is no reload at all -- only the message. The flag clears once
 * any page loads, so the next deploy can reload again.
 *
 * The same boundary catches a page's own render error (there was no error
 * boundary anywhere before, so one blanked the app) and says so; it
 * resets when the location changes, so moving to another stage of the
 * same page recovers.
 */
import { Component, lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { useInRouterContext, useLocation } from "react-router-dom";

import { Button } from "@/components/ui/button";

export const CHUNK_RELOAD_KEY = "splitsmith:chunk-reload";

/** A chunk fetch that failed (Chromium / Firefox / Safari wordings). */
function isChunkLoadError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /dynamically imported module|Importing a module script failed|error loading dynamically imported module/i.test(msg);
}

/** The reload flag: set, unset, or ``null`` when storage is unavailable
 *  (then no reload is safe -- nothing would stop a loop). */
function readFlag(): boolean | null {
  try {
    return sessionStorage.getItem(CHUNK_RELOAD_KEY) != null;
  } catch {
    return null;
  }
}

function writeFlag(on: boolean): boolean {
  try {
    if (on) sessionStorage.setItem(CHUNK_RELOAD_KEY, "1");
    else sessionStorage.removeItem(CHUNK_RELOAD_KEY);
    return true;
  } catch {
    return false;
  }
}

class PageLoadBoundary extends Component<{ children: ReactNode; resetKey: string }, { error: unknown }> {
  state: { error: unknown } = { error: null };

  static getDerivedStateFromError(error: unknown) {
    return { error: error ?? new Error("page error") };
  }

  componentDidUpdate(prev: { resetKey: string }) {
    // A new location clears a failure; a healthy page is never remounted.
    if (this.state.error != null && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    if (this.state.error == null) return this.props.children;
    return (
      <div className="flex flex-wrap items-center gap-3 px-7 py-8 text-md text-ink-2">
        <span>
          {isChunkLoadError(this.state.error) ? "This page couldn't load." : "Something went wrong on this page."}
        </span>
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
      if (isChunkLoadError(err) && readFlag() === false && writeFlag(true)) {
        window.location.reload();
        // Keep suspending while the page reloads.
        return new Promise<{ default: ComponentType<P> }>(() => {});
      }
      throw err;
    }
  });
  function LazyPage(props: P) {
    return useInRouterContext() ? <RoutedPage {...props} /> : <Page resetKey="" {...props} />;
  }
  /** A new location clears a failed boundary, so an error on one stage
   *  does not stick to the next. */
  function RoutedPage(props: P) {
    return <Page resetKey={useLocation().pathname} {...props} />;
  }
  function Page({ resetKey, ...props }: P & { resetKey: string }) {
    return (
      <PageLoadBoundary resetKey={resetKey}>
        <Suspense fallback={<div data-page-loading className="min-h-[40vh]" />}>
          <Lazy {...(props as P)} />
        </Suspense>
      </PageLoadBoundary>
    );
  }
  LazyPage.displayName = `lazyPage(${name})`;
  return LazyPage;
}
