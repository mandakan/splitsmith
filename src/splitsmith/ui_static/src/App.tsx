import { useEffect, useState, type ReactNode } from "react";
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
import { MatchShell } from "@/components/match/MatchShell";
import { ShareShell } from "@/components/share/ShareShell";
import { DefaultShooterRedirect } from "@/components/match/DefaultShooterRedirect";
import { ModeProvider } from "@/lib/mode";
import { ConfirmProvider } from "@/components/useConfirm";
import { UploadProvider } from "@/lib/uploads";
import { AuthProvider, useAuth } from "@/lib/auth";
import { useDeploymentMode } from "@/lib/features";
import { ShooterScopedRoute } from "@/components/ShooterScopedRoute";
import { Login } from "@/pages/Login";
import { Audit } from "@/pages/Audit";
import { BeepReview } from "@/pages/BeepReview";
import { Coach } from "@/pages/Coach";
import { Compare } from "@/pages/Compare";
import { CreateMatch } from "@/pages/CreateMatch";
import { Design } from "@/pages/Design";
import { DevCorpus } from "@/pages/dev/DevCorpus";
import { DevRetrain } from "@/pages/dev/DevRetrain";
import { DevReviewQueue } from "@/pages/dev/DevReviewQueue";
import { DevValidate } from "@/pages/dev/DevValidate";
import { Export } from "@/pages/Export";
import { Home } from "@/pages/Home";
import { Ingest } from "@/pages/Ingest";
import { Lab } from "@/pages/Lab";
import { MergeMatches } from "@/pages/MergeMatches";
import { Pick } from "@/pages/Pick";
import { Shooters } from "@/pages/Shooters";
import { TakeOverview } from "@/pages/TakeOverview";
import { PromoteReview } from "@/pages/PromoteReview";
import { AdminWorkers } from "@/pages/AdminWorkers";
import { Results } from "@/pages/Results";
import { ResultsStage } from "@/pages/ResultsStage";
import { Review } from "@/pages/Review";
import { api } from "@/lib/api";

function RedirectLabSlug() {
  const { slug } = useParams<{ slug: string }>();
  return <Navigate to={`/dev/legacy/lab/${slug ?? ""}`} replace />;
}

/* Catch-all for bare match-scoped paths (``/audit/...``, ``/ingest``,
 * ``/shooters``, etc.) hit directly via bookmark or external link. Reads
 * the server's bound ``match_id`` via ``/api/health`` and redirects into
 * ``/match/:matchId/<original path>``. Falls through to ``/pick`` when no
 * match is bound. */
function LegacyMatchRedirect() {
  const location = useLocation();
  const [target, setTarget] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .getHealth()
      .then((h) => {
        if (!alive) return;
        if (h.bound && h.match_id) {
          const rest =
            location.pathname.startsWith("/") && location.pathname !== "/"
              ? location.pathname
              : "";
          setTarget(`/match/${h.match_id}${rest}${location.search}`);
        } else {
          setTarget("/pick");
        }
      })
      .catch(() => {
        if (alive) setTarget("/pick");
      });
    return () => {
      alive = false;
    };
  }, [location.pathname, location.search]);
  if (target == null) return null;
  return <Navigate to={target} replace />;
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
  const mode = useDeploymentMode();
  const location = useLocation();
  // Public share views are token-authorized server-side; the session
  // gate has no say there. Bypass before the loading branch so a share
  // link renders without waiting on /api/me.
  if (location.pathname.startsWith("/share/")) return <>{children}</>;
  if (status === "loading") {
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
  // Desktop is never gated -- no login route, no redirect, whatever /api/me did.
  if (mode === "local") return <>{children}</>;
  if (status === "anon" && location.pathname !== "/login") {
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
          <BrowserRouter>
            <AuthGate>
            <Routes>
              <Route path="login" element={<Login />} />
          {/* Picker lives outside any shell -- it has its own header and
              runs whether or not a project is bound. MatchShell redirects
              here when it sees /api/health.bound === false. */}
          <Route path="pick" element={<Pick />} />
          <Route path="pick/new" element={<DesktopGate screen="Match creation" links={false}><CreateMatch /></DesktopGate>} />
          <Route path="pick/merge" element={<DesktopGate screen="Match merge" links={false}><MergeMatches /></DesktopGate>} />
          {/* Canonical match-scoped surfaces (#353 Phase 3 PR C). All
              shooter / stage / overview / picker-within-match routes
              live under ``/match/:matchId/...``. Bare match-scoped paths
              are caught by LegacyMatchRedirect and re-routed into the
              prefix using ``/api/health.match_id`` so old bookmarks land
              on the right place. */}
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
                element={<ShooterScopedRoute element={<DesktopGate screen="Audit"><Audit /></DesktopGate>} />}
              />
              <Route
                path="audit/:slug/:stage"
                element={<ShooterScopedRoute element={<DesktopGate screen="Audit"><Audit /></DesktopGate>} />}
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
              <Route path="beep-review" element={<DesktopGate screen="Beep review"><BeepReview /></DesktopGate>} />
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
              <Route path="export" element={<DefaultShooterRedirect base="export" />} />
              <Route path="results" element={<Results />} />
              <Route
                path="results/:slug/:stage"
                element={<ShooterScopedRoute element={<ResultsStage />} />}
              />
            </Route>
          </Route>
          {/* Public share surface (#349): token-authorized, read-only,
              mobile-friendly. Mirrors the match results subtree shape so
              useMatchHref("results", ...) round-trips inside the share. */}
          <Route path="share/:token" element={<ShareShell />}>
            <Route index element={<Navigate to="results" replace />} />
            <Route path="results" element={<Results />} />
            <Route path="results/:slug/:stage" element={<ResultsStage />} />
          </Route>
          {/* Developer mode (#331). All four workflow steps + the
              retired Lab + fixture-editor surfaces sit under the
              cyan-accented DeveloperShell. */}
          <Route element={<DeveloperShell />}>
            <Route path="dev" element={<Navigate to="/dev/corpus" replace />} />
            <Route path="dev/corpus" element={<DesktopGate screen="Developer tools" links={false}><DevCorpus /></DesktopGate>} />
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
            <Route path="admin/workers" element={<AdminWorkers />} />
            {/* Legacy redirects so old bookmarks don't 404. */}
            <Route path="lab" element={<Navigate to="/dev/legacy/lab" replace />} />
            <Route path="lab/:slug" element={<RedirectLabSlug />} />
          </Route>
          {/* Bare match-scoped paths -- caught here and bounced into the
              ``/match/:matchId/`` prefix via LegacyMatchRedirect. ``/``
              also goes through here so a fresh-bound match lands on its
              own overview without the picker needing to plumb the id. */}
          <Route path="*" element={<LegacyMatchRedirect />} />
            </Routes>
          </AuthGate>
          </BrowserRouter>
          </UploadProvider>
        </ConfirmProvider>
      </AuthProvider>
    </ModeProvider>
  );
}
