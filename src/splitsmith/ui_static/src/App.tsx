import { useEffect, type ReactNode } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useParams,
} from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { DesktopGate } from "@/components/DesktopOnlyNotice";
import { DeveloperShell } from "@/components/developer/DeveloperShell";
import { DropGuard } from "@/components/DropGuard";
import { RootLayout } from "@/components/layout/RootLayout";
import { MatchShell } from "@/components/match/MatchShell";
import { ShareShell } from "@/components/share/ShareShell";
import { DefaultShooterRedirect } from "@/components/match/DefaultShooterRedirect";
import { ModeProvider } from "@/lib/mode";
import { useIsMobile } from "@/lib/useIsMobile";
import { ConfirmProvider } from "@/components/useConfirm";
import { UploadProvider } from "@/lib/uploads";
import { UploadDock } from "@/components/UploadDock";
import { AuthProvider, useAuth } from "@/lib/auth";
import { useDeploymentMode } from "@/lib/features";
import { peekApproveCode, stashApproveCode, takeApproveCode } from "@/lib/deviceApproveStash";
import { ShooterScopedRoute } from "@/components/ShooterScopedRoute";
import { Login } from "@/pages/Login";
import { Audit } from "@/pages/Audit";
import { MobileAudit } from "@/pages/MobileAudit";
import { BeepReview } from "@/pages/BeepReview";
import { MobileBeepReview } from "@/pages/MobileBeepReview";
import { Coach } from "@/pages/Coach";
import { Compare } from "@/pages/Compare";
import { CreateMatch } from "@/pages/CreateMatch";
import { Design } from "@/pages/Design";
import { DesktopApprove } from "@/pages/DesktopApprove";
import { DevCorpus } from "@/pages/dev/DevCorpus";
import { DevFixtureDetail } from "@/pages/dev/DevFixtureDetail";
import { DevRetrain } from "@/pages/dev/DevRetrain";
import { DevReviewQueue } from "@/pages/dev/DevReviewQueue";
import { DevValidate } from "@/pages/dev/DevValidate";
import { Export } from "@/pages/Export";
import { MatchExport } from "@/pages/MatchExport";
import { Home } from "@/pages/Home";
import { Ingest } from "@/pages/Ingest";
import { Jobs } from "@/pages/Jobs";
import { Lab } from "@/pages/Lab";
import { MergeMatches } from "@/pages/MergeMatches";
import { Pick } from "@/pages/Pick";
import { Shooters } from "@/pages/Shooters";
import { TakeOverview } from "@/pages/TakeOverview";
import { PromoteReview } from "@/pages/PromoteReview";
import { AdminWorkers } from "@/pages/AdminWorkers";
import { Account } from "@/pages/Account";
import { Results } from "@/pages/Results";
import { ResultsStage } from "@/pages/ResultsStage";
import { Review } from "@/pages/Review";
import { Triage } from "@/pages/Triage";

function RedirectLabSlug() {
  const { slug } = useParams<{ slug: string }>();
  return <Navigate to={`/dev/legacy/lab/${slug ?? ""}`} replace />;
}

/* Beep review is the one match-scoped screen with a real mobile surface
 * (slice 3, #326 follow-up) - every other match-scoped route still goes
 * through DesktopGate. Below the 768 px breakpoint this renders the
 * card-pager MobileBeepReview instead of gating the desktop layout. */
function BeepReviewRoute() {
  const isMobile = useIsMobile();
  return isMobile ? <MobileBeepReview /> : <BeepReview />;
}

/* Audit joins beep review as a match-scoped screen with a real mobile
 * surface (mobile audit design, 2026-08-12). Below 768 px this renders
 * the wrapped-row MobileAudit; the desktop screen stays behind
 * DesktopGate untouched. */
export function AuditRoute() {
  const isMobile = useIsMobile();
  return isMobile ? (
    <MobileAudit />
  ) : (
    <DesktopGate screen="Audit">
      <Audit />
    </DesktopGate>
  );
}

function Standby() {
  return (
    <div
      className="grid min-h-dvh place-items-center bg-bg"
      role="status"
      aria-label="Loading"
    >
      <span className="font-mono text-xs uppercase tracking-[0.16em] text-subtle">
        Standby...
      </span>
    </div>
  );
}

/* Auth gate. Blocks the app on the initial ``/api/me`` resolve so an
 * anonymous hosted visitor never flashes protected chrome, then:
 *  - ``authed`` (hosted, when the session cookie resolves) -> render the app,
 *  - ``anon`` (hosted, signed out) -> redirect to /login, except when
 *    already there.
 * Local mode is NEVER redirected: the login surface is hosted-only, so even
 * if ``/api/me`` fails for a transient reason in local mode (which would set
 * status to ``anon``), the desktop user must not be stranded on /login. The
 * mode check is the hard guarantee; ``/api/me`` returning the loopback user
 * is the normal-case reason status stays ``authed`` there. */
function AuthGate({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const { mode, resolved } = useDeploymentMode();
  const location = useLocation();

  // Device-flow pickup (#719). Whether to redirect has to be decided
  // synchronously, in THIS render, using the render-safe peek -- not an
  // effect. This used to matter because the ordinary route tree's own
  // catch-all raced the pickup with an async getHealth()-driven redirect
  // to /pick (both wrote history with ``replace``); that race is gone now
  // that the catch-all is a synchronous <Navigate> (#739). The peek stays
  // synchronous regardless, since {children} mounting for even one commit
  // before a pending pickup is handled would still be wrong - it is kept
  // for the reasons that remain: StrictMode stash semantics (see the
  // effect below) and mode-independence (see the comment further down).
  //
  // Consuming the stash (the actual sessionStorage mutation) is a
  // separate, effect-only step below: takeApproveCode() is a
  // read-then-remove side effect, so it cannot run in the render body --
  // StrictMode double-invokes render on mount, and the first (discarded)
  // invocation would consume the stash before the second (committed) one
  // ever saw it, silently defeating the redirect. The effect is gated on
  // the same peeked value, so its own StrictMode double-invoke (mount ->
  // cleanup -> mount) just consumes it once and then no-ops.
  //
  // Deliberately NOT gated on deployment mode. useDeploymentMode()
  // starts at "local" and flips async once /api/server/features lands,
  // so a mode check here is a race against /api/me: if auth resolves
  // first, the local-mode early return below mounts the ordinary tree,
  // the catch-all navigates off "/", and by the time mode flips the
  // pathname no longer matches - the only pickup window this feature
  // gets, missed, with a dead code left to ambush a later visit. The
  // check bought nothing anyway: sessionStorage is
  // origin-scoped and stashApproveCode() only ever runs on the hosted
  // anonymous path, so a local install cannot hold a stash (pinned by a
  // test in App.routes.test.tsx). The unresolved-mode gate below (#734)
  // still applies to everything else: it stops the ordinary tree from
  // mounting on the provisional "local" default, closing the same race
  // for every other mode-dependent feature.
  const pendingPickupCode =
    status === "authed" && location.pathname === "/" ? peekApproveCode() : null;

  useEffect(() => {
    if (pendingPickupCode) takeApproveCode();
  }, [pendingPickupCode]);

  // Public share views are token-authorized server-side; the session
  // gate has no say there. Bypass before the loading branch so a share
  // link renders without waiting on /api/me.
  if (location.pathname.startsWith("/share/")) return <>{children}</>;
  if (status === "loading") return <Standby />;
  // Ahead of the local-mode early return on purpose: see the comment on
  // pendingPickupCode. A pending pickup implies a stash, which only the
  // hosted anonymous path can ever write.
  if (pendingPickupCode) {
    return <Navigate to={`/desktop/approve?code=${pendingPickupCode}`} replace />;
  }
  // Hold the route tree until the deployment mode has genuinely resolved
  // (#734). Mounting on the provisional "local" default lets mode-gated
  // surfaces fire local-only requests against a hosted server and
  // reintroduces the mount-then-navigate races described above for every
  // future mode-dependent feature. The share and pickup branches stay
  // above this line: neither depends on mode, and the pickup must win
  // the first commit regardless of how slow /api/server/features is.
  if (!resolved) return <Standby />;
  // Desktop is never gated -- no login route, no redirect, whatever /api/me did.
  if (mode === "local") return <>{children}</>;
  if (status === "anon" && location.pathname !== "/login") {
    // Device-flow codes have to survive the login round trip (#719): the
    // magic link returns to "/" with no query string, so park the code
    // before we lose it.
    if (location.pathname === "/desktop/approve") {
      const code = new URLSearchParams(location.search).get("code");
      if (code) stashApproveCode(code);
    }
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

export function App() {
  return (
    <ModeProvider>
      <AuthProvider>
        <ConfirmProvider>
          <UploadProvider>
            <UploadDock />
            <DropGuard />
          <BrowserRouter>
            <AuthGate>
            <Routes>
              <Route path="login" element={<Login />} />
          {/* Public share surface (#349): token-authorized, read-only,
              mobile-friendly. Mirrors the match results subtree shape so
              useMatchHref("results", ...) round-trips inside the share.
              Deliberately outside RootLayout (#550) -- an anonymous
              share visitor must never see an account menu or mode
              switch. */}
          <Route path="share/:token" element={<ShareShell />}>
            <Route index element={<Navigate to="results" replace />} />
            <Route path="results" element={<Results />} />
            <Route path="results/:slug/:stage" element={<ResultsStage />} />
            {/* Compare-behind-a-token (#700): read-only, desktop-only.
                isShareView() (lib/shareView) gates the operator-only
                affordances (Audit/Coach tabs, audit CTAs) off this
                mount; api plumbing needs no changes here since
                scopeRequestPath already rewrites Compare's fetches into
                the share prefix. */}
            <Route
              path="compare/:stage"
              element={
                <DesktopGate screen="Compare">
                  <Compare />
                </DesktopGate>
              }
            />
          </Route>

          <Route element={<RootLayout />}>
          {/* Picker: no context sidebar, inherits the global bar. */}
          <Route path="pick" element={<Pick />} />
          <Route path="pick/new" element={<DesktopGate screen="Match creation" links={false}><CreateMatch /></DesktopGate>} />
          <Route path="pick/merge" element={<DesktopGate screen="Match merge" links={false}><MergeMatches /></DesktopGate>} />
          {/* Admin surfaces are server-wide, not project-scoped. They
              used to route through AppShell purely because it was the
              only shell left that would take them, which meant no
              account menu and an empty sidebar. They nest directly
              under RootLayout now (#550). */}
          <Route path="admin/workers" element={<AdminWorkers />} />
          {/* Account settings (#867). Under RootLayout for the same
              reason the admin surfaces are: server-wide, not
              project-scoped. Hosted-only - the page itself redirects to
              /pick in local mode. */}
          <Route path="account" element={<Account />} />
          {/* Device-flow approval screen (#719). Under RootLayout so it
              carries the account chip -- the operator needs to see which
              account they are approving for. */}
          <Route path="desktop/approve" element={<DesktopApprove />} />
          {/* Canonical match-scoped surfaces (#353 Phase 3 PR C). All
              shooter / stage / overview / picker-within-match routes
              live under ``/match/:matchId/...``. Bare match-scoped paths
              fall through to the catch-all below and land on /pick. */}
          <Route path="match/:matchId">
            <Route
              path="ingest/:slug"
              element={<ShooterScopedRoute element={<DesktopGate screen="Ingest"><Ingest /></DesktopGate>} />}
            />
            <Route path="ingest" element={<DefaultShooterRedirect base="ingest" />} />
            <Route element={<MatchShell />}>
              <Route index element={<Home />} />
              <Route
                path="audit/:slug"
                element={<ShooterScopedRoute element={<AuditRoute />} />}
              />
              <Route
                path="audit/:slug/:stage"
                element={<ShooterScopedRoute element={<AuditRoute />} />}
              />
              <Route path="audit" element={<DefaultShooterRedirect base="audit" />} />
              <Route path="compare/:stage" element={<DesktopGate screen="Compare"><Compare /></DesktopGate>} />
              <Route
                path="coach/:slug"
                element={<ShooterScopedRoute element={<DesktopGate screen="Coach"><Coach /></DesktopGate>} />}
              />
              <Route
                path="coach/:slug/:stage"
                element={<ShooterScopedRoute element={<DesktopGate screen="Coach"><Coach /></DesktopGate>} />}
              />
              <Route path="coach" element={<DefaultShooterRedirect base="coach" />} />
              <Route path="shooters" element={<DesktopGate screen="Shooter management"><Shooters /></DesktopGate>} />
              <Route path="beep-review" element={<BeepReviewRoute />} />
              {/* Take overview: carve-up review for one multi-stage raw
                  recording. :filename is the raw video's basename. */}
              <Route
                path="take/:slug/:filename"
                element={<ShooterScopedRoute element={<DesktopGate screen="Take review"><TakeOverview /></DesktopGate>} />}
              />
              <Route
                path="export/:slug"
                element={<ShooterScopedRoute element={<DesktopGate screen="Export"><Export /></DesktopGate>} />}
              />
              <Route
                path="export/:slug/:stage"
                element={<ShooterScopedRoute element={<DesktopGate screen="Export"><Export /></DesktopGate>} />}
              />
              <Route
                path="export"
                element={
                  <DesktopGate screen="Match export">
                    <MatchExport />
                  </DesktopGate>
                }
              />
              <Route path="results" element={<Results />} />
              <Route
                path="results/:slug/:stage"
                element={<ShooterScopedRoute element={<ResultsStage />} />}
              />
              {/* Triage is responsive by design - it doubles as the desktop
                  flagged-stage worklist, so no DesktopGate (slice 4). */}
              <Route path="triage" element={<Triage />} />
              <Route path="jobs" element={<Jobs />} />
            </Route>
          </Route>
          {/* Developer mode (#331). All four workflow steps + the
              retired Lab + fixture-editor surfaces sit under the
              cyan-accented DeveloperShell. */}
          <Route element={<DeveloperShell />}>
            <Route path="dev" element={<Navigate to="/dev/corpus" replace />} />
            <Route path="dev/corpus" element={<DesktopGate screen="Developer tools" links={false}><DevCorpus /></DesktopGate>} />
            <Route path="dev/corpus/:slug" element={<DesktopGate screen="Developer tools" links={false}><DevFixtureDetail /></DesktopGate>} />
            <Route path="dev/review" element={<DesktopGate screen="Developer tools" links={false}><DevReviewQueue /></DesktopGate>} />
            <Route path="dev/validate" element={<DesktopGate screen="Developer tools" links={false}><DevValidate /></DesktopGate>} />
            <Route path="dev/retrain" element={<DesktopGate screen="Developer tools" links={false}><DevRetrain /></DesktopGate>} />
            <Route path="dev/legacy/lab" element={<DesktopGate screen="Developer tools" links={false}><Lab /></DesktopGate>} />
            <Route path="dev/legacy/lab/:slug" element={<DesktopGate screen="Developer tools" links={false}><Lab /></DesktopGate>} />
          </Route>
          {/* Fixture editor + design system stay AppShell-mounted: the
              editor is a single-purpose tool that the dev review queue
              links into via /review?fixture=..., and /_design is the
              token palette browser. */}
          <Route element={<AppShell />}>
            <Route path="review" element={<DesktopGate screen="Fixture editor" links={false}><Review /></DesktopGate>} />
            <Route path="promote-review" element={<DesktopGate screen="Promote review" links={false}><PromoteReview /></DesktopGate>} />
            <Route path="_design" element={<DesktopGate screen="Design system" links={false}><Design /></DesktopGate>} />
            {/* Legacy redirects so old bookmarks don't 404. */}
            <Route path="lab" element={<Navigate to="/dev/legacy/lab" replace />} />
            <Route path="lab/:slug" element={<RedirectLabSlug />} />
          </Route>
          </Route>
          {/* Catch-all: /api/health retired the bound-state concept, so a
              bare or unknown path has exactly one destination - the
              picker. Synchronous on purpose: an async redirect here is
              what used to race effect-driven navigation (#739). */}
          <Route path="*" element={<Navigate to="/pick" replace />} />
            </Routes>
          </AuthGate>
          </BrowserRouter>
          </UploadProvider>
        </ConfirmProvider>
      </AuthProvider>
    </ModeProvider>
  );
}
