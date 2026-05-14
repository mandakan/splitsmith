import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { ModeProvider } from "@/lib/mode";
import { Audit } from "@/pages/Audit";
import { Coach } from "@/pages/Coach";
import { CreateMatch } from "@/pages/CreateMatch";
import { Design } from "@/pages/Design";
import { Export } from "@/pages/Export";
import { Home } from "@/pages/Home";
import { Ingest } from "@/pages/Ingest";
import { Lab } from "@/pages/Lab";
import { Pick } from "@/pages/Pick";
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
          <Route element={<AppShell />}>
            <Route index element={<Home />} />
            <Route path="ingest" element={<Ingest />} />
            <Route path="audit" element={<Audit />} />
            <Route path="audit/:stage" element={<Audit />} />
            <Route path="coach" element={<Coach />} />
            <Route path="coach/:stage" element={<Coach />} />
            <Route path="export" element={<Export />} />
            <Route path="export/:stage" element={<Export />} />
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
