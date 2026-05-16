import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { DeveloperShell } from "@/components/developer/DeveloperShell";
import { MatchShell } from "@/components/match/MatchShell";
import { ModeProvider } from "@/lib/mode";
import { ShooterScopedRoute } from "@/components/ShooterScopedRoute";
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
import { PromoteReview } from "@/pages/PromoteReview";
import { Review } from "@/pages/Review";

function RedirectLabSlug() {
  const { slug } = useParams<{ slug: string }>();
  return <Navigate to={`/dev/legacy/lab/${slug ?? ""}`} replace />;
}

export function App() {
  return (
    <ModeProvider>
      <BrowserRouter>
        <Routes>
          {/* Picker lives outside AppShell -- it has its own header and
              runs whether or not a project is bound. AppShell redirects
              here when it sees /api/health.bound === false. */}
          <Route path="pick" element={<Pick />} />
          <Route path="pick/new" element={<CreateMatch />} />
          <Route path="pick/merge" element={<MergeMatches />} />
          {/* Match-mode surfaces ride under the Shot Timer shell as
              their redesign issues ship. Ingest stays self-shelled
              (focused-task page, no sidebar).

              Shooter-scoped routes (#353 phase 1): /audit /export /ingest
              /coach take an explicit /:slug so the URL alone identifies
              which shooter is in focus. Legacy slug-less forms remain
              live as backwards-compat shims; ShooterScopedRoute redirects
              them to the canonical /<page>/<bound-slug>/[:stage] form.
              The /:slug routes use the slug as the React Router key so a
              switch (chip-strip click -> Link -> URL change) remounts the
              page cleanly instead of forcing a window.location.reload.

              /compare /shooters /beep-review stay slug-less: compare is
              inherently multi-shooter, /shooters is the shooter manager
              itself, beep-review is a cross-shooter queue. */}
          <Route
            path="ingest"
            element={<ShooterScopedRoute page="ingest" element={<Ingest />} />}
          />
          <Route
            path="ingest/:slugOrStage"
            element={<ShooterScopedRoute page="ingest" element={<Ingest />} />}
          />
          <Route element={<MatchShell />}>
            <Route index element={<Home />} />
            <Route
              path="audit"
              element={<ShooterScopedRoute page="audit" element={<Audit />} />}
            />
            <Route
              path="audit/:slugOrStage"
              element={<ShooterScopedRoute page="audit" element={<Audit />} />}
            />
            <Route
              path="audit/:slug/:stage"
              element={<ShooterScopedRoute page="audit" element={<Audit />} />}
            />
            <Route path="compare/:stage" element={<Compare />} />
            <Route
              path="coach"
              element={<ShooterScopedRoute page="coach" element={<Coach />} />}
            />
            <Route
              path="coach/:slugOrStage"
              element={<ShooterScopedRoute page="coach" element={<Coach />} />}
            />
            <Route
              path="coach/:slug/:stage"
              element={<ShooterScopedRoute page="coach" element={<Coach />} />}
            />
            <Route path="shooters" element={<Shooters />} />
            <Route path="beep-review" element={<BeepReview />} />
            <Route
              path="export"
              element={<ShooterScopedRoute page="export" element={<Export />} />}
            />
            <Route
              path="export/:slugOrStage"
              element={<ShooterScopedRoute page="export" element={<Export />} />}
            />
            <Route
              path="export/:slug/:stage"
              element={<ShooterScopedRoute page="export" element={<Export />} />}
            />
          </Route>
          {/* Developer mode (#331). All four workflow steps + the
              retired Lab + fixture-editor surfaces sit under the
              cyan-accented DeveloperShell. */}
          <Route element={<DeveloperShell />}>
            <Route path="dev" element={<Navigate to="/dev/corpus" replace />} />
            <Route path="dev/corpus" element={<DevCorpus />} />
            <Route path="dev/review" element={<DevReviewQueue />} />
            <Route path="dev/validate" element={<DevValidate />} />
            <Route path="dev/retrain" element={<DevRetrain />} />
            <Route path="dev/legacy/lab" element={<Lab />} />
            <Route path="dev/legacy/lab/:slug" element={<Lab />} />
          </Route>
          {/* Fixture editor + design system stay AppShell-mounted: the
              editor is a single-purpose tool that the dev review queue
              links into via /review?fixture=..., and /_design is the
              token palette browser. */}
          <Route element={<AppShell />}>
            <Route path="review" element={<Review />} />
            <Route path="promote-review" element={<PromoteReview />} />
            <Route path="_design" element={<Design />} />
            {/* Legacy redirects so old bookmarks don't 404. */}
            <Route path="lab" element={<Navigate to="/dev/legacy/lab" replace />} />
            <Route path="lab/:slug" element={<RedirectLabSlug />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ModeProvider>
  );
}
