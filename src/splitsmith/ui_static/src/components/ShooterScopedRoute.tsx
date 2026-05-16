/**
 * ShooterScopedRoute -- canonicalises shooter-bound URLs (#353 phase 1).
 *
 * The shooter-bound pages (Audit, Export, Ingest, Coach) accept three URL
 * shapes for backwards-compat reasons:
 *
 *   /page                     -- no slug, no stage. Use bound shooter.
 *   /page/:slugOrStage        -- one param, ambiguous:
 *                                  - numeric  -> legacy /page/:stage
 *                                  - non-numeric -> new /page/:slug
 *   /page/:slug/:stage        -- canonical new form.
 *
 * This component sits between the route and the actual page. It resolves
 * which case applies, redirects legacy / ambiguous forms to the canonical
 * /:slug or /:slug/:stage URL, and renders the wrapped page with
 * key={slug} so a shooter switch (chip-strip Link -> URL change) remounts
 * the page cleanly instead of needing window.location.reload().
 *
 * When the URL slug differs from the server-bound shooter it also fires
 * api.selectActiveShooter(slug) so the legacy ``state.project_root`` on
 * the server points at the right shooter. Phases 2-3 of #353 will lift
 * the binding off the server singleton, at which point that side-effect
 * goes away.
 */

import { useEffect, useMemo, useState } from "react";
import { Navigate, useParams } from "react-router-dom";

import { api } from "@/lib/api";

type Page = "audit" | "export" | "ingest" | "coach";

interface Props {
  page: Page;
  element: React.ReactElement;
}

export function ShooterScopedRoute({ page, element }: Props) {
  const params = useParams<{
    slug?: string;
    stage?: string;
    slugOrStage?: string;
  }>();

  // Step 1: figure out the canonical (slug, stage) pair from the matched
  // URL shape. Cases:
  //   /page                       -> {slug: undef,  stage: undef}
  //   /page/<number>              -> legacy, treat as {slug: undef, stage: <number>}
  //   /page/<non-number>          -> new slug-only, {slug: <non-number>, stage: undef}
  //   /page/<slug>/<stage>        -> canonical, both set
  const { slug, stage } = useMemo(() => {
    if (params.slug !== undefined) {
      return { slug: params.slug, stage: params.stage };
    }
    if (params.slugOrStage === undefined) {
      return { slug: undefined, stage: undefined };
    }
    if (/^\d+$/.test(params.slugOrStage)) {
      return { slug: undefined, stage: params.slugOrStage };
    }
    return { slug: params.slugOrStage, stage: undefined };
  }, [params]);

  // Step 2: when no slug is in the URL, resolve the bound shooter and
  // redirect to the canonical URL.
  const [boundSlug, setBoundSlug] = useState<string | null | undefined>(
    undefined,
  );
  useEffect(() => {
    if (slug !== undefined) return;
    let alive = true;
    api
      .getHealth()
      .then((h) => {
        if (!alive) return;
        // project_root ends with "shooters/<slug>" when bound inside a
        // match; the slug is the last path segment. For legacy single-
        // shooter projects there's no match -> no slug to fill in; we
        // leave boundSlug=null and just render the page (it'll use
        // whatever the server has bound).
        const root = h.project_root ?? "";
        const m = root.match(/[\\/]shooters[\\/]([^\\/]+)[\\/]?$/);
        setBoundSlug(m ? m[1] : null);
      })
      .catch(() => {
        if (alive) setBoundSlug(null);
      });
    return () => {
      alive = false;
    };
  }, [slug]);

  if (slug === undefined) {
    if (boundSlug === undefined) {
      // Health still loading. Nothing visible -- this is fast (a single
      // /api/health round-trip already in-flight from MatchShell).
      return null;
    }
    if (boundSlug === null) {
      // Legacy single-shooter project (no Match folder). Just render the
      // page without remounting -- nothing to canonicalise.
      return element;
    }
    const target = stage ? `/${page}/${boundSlug}/${stage}` : `/${page}/${boundSlug}`;
    return <Navigate to={target} replace />;
  }

  // Step 3: slug in URL. If it differs from the server-bound one, fire a
  // selectActiveShooter side-effect so legacy endpoints read the right
  // project root. Don't gate the render on it -- the page mounts fresh
  // (we key on slug), so any in-flight reads land on the new shooter
  // once selectActiveShooter resolves. That's a phase-1 compromise; #353
  // phases 2-3 remove the round-trip entirely.
  return (
    <SlugBindWrapper slug={slug}>
      {/* The slug key forces a fresh mount of `element` whenever the URL
          slug changes -- replaces the previous reload-on-switch. */}
      <ShooterKeyedElement slug={slug} element={element} />
    </SlugBindWrapper>
  );
}

function SlugBindWrapper({
  slug,
  children,
}: {
  slug: string;
  children: React.ReactNode;
}) {
  // Gate the children render until the server's project_root matches the
  // URL slug. Audit / Export / Coach all fire their own getProject on
  // mount; without this gate they'd race the rebind and momentarily show
  // the previous shooter's data. Phase 2 of #353 eliminates this by
  // making the slug per-request instead of singleton state.
  const [bound, setBound] = useState(false);
  useEffect(() => {
    let alive = true;
    setBound(false);
    (async () => {
      try {
        const h = await api.getHealth();
        if (!alive) return;
        const root = h.project_root ?? "";
        const m = root.match(/[\\/]shooters[\\/]([^\\/]+)[\\/]?$/);
        const currentBound = m ? m[1] : null;
        if (currentBound === slug) {
          setBound(true);
          return;
        }
        try {
          await api.selectActiveShooter(slug);
          if (alive) setBound(true);
        } catch (err: unknown) {
          // 404 = slug not in match; the page will render its own error.
          // Any other failure: still let the page try -- the worst case
          // is a stale-shooter flash, not a stuck UI.
          // eslint-disable-next-line no-console
          console.warn("selectActiveShooter failed", err);
          if (alive) setBound(true);
        }
      } catch {
        if (alive) setBound(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [slug]);

  if (!bound) {
    // Render nothing while binding: tiny window in the common case
    // (already-bound shooter), brief loading state on a real switch.
    return null;
  }
  return <>{children}</>;
}

function ShooterKeyedElement({
  slug,
  element,
}: {
  slug: string;
  element: React.ReactElement;
}) {
  // React's reconciler treats elements with different keys as distinct
  // mounts. Cloning the element with a slug-derived key gives us a clean
  // remount on shooter switch without each page having to thread reset
  // logic through its many effects.
  return <span key={slug} style={{ display: "contents" }}>{element}</span>;
}
