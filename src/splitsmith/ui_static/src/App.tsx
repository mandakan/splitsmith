import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { MatchShell } from "@/components/match/MatchShell";
import { ModeProvider } from "@/lib/mode";
import { Audit } from "@/pages/Audit";
import { Coach } from "@/pages/Coach";
import { Compare } from "@/pages/Compare";
import { CreateMatch } from "@/pages/CreateMatch";
import { Design } from "@/pages/Design";
import { Export } from "@/pages/Export";
import { Home } from "@/pages/Home";
import { Ingest } from "@/pages/Ingest";
import { Lab } from "@/pages/Lab";
import { Pick } from "@/pages/Pick";
import { Shooters } from "@/pages/Shooters";
import { PromoteReview } from "@/pages/PromoteReview";
import { Review } from "@/pages/Review";

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
          {/* Match-mode surfaces ride under the Shot Timer shell as
              their redesign issues ship. Ingest stays self-shelled
              (focused-task page, no sidebar). */}
          <Route path="ingest" element={<Ingest />} />
          <Route element={<MatchShell />}>
            <Route index element={<Home />} />
            <Route path="audit" element={<Audit />} />
            <Route path="audit/:stage" element={<Audit />} />
            <Route path="compare/:stage" element={<Compare />} />
            <Route path="shooters" element={<Shooters />} />
            <Route path="export" element={<Export />} />
            <Route path="export/:stage" element={<Export />} />
          </Route>
          {/* Not-yet-redesigned surfaces still mount under AppShell
              until their respective redesign issues land. */}
          <Route element={<AppShell />}>
            <Route path="coach" element={<Coach />} />
            <Route path="coach/:stage" element={<Coach />} />
            <Route path="lab" element={<Lab />} />
            <Route path="lab/:slug" element={<Lab />} />
            <Route path="review" element={<Review />} />
            <Route path="promote-review" element={<PromoteReview />} />
            <Route path="_design" element={<Design />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ModeProvider>
  );
}
