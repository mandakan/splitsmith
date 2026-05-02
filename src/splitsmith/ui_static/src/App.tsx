import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { ThemeProvider } from "@/lib/theme";
import { Audit } from "@/pages/Audit";
import { Design } from "@/pages/Design";
import { Export } from "@/pages/Export";
import { Home } from "@/pages/Home";
import { Ingest } from "@/pages/Ingest";

export function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<Home />} />
            <Route path="ingest" element={<Ingest />} />
            <Route path="audit" element={<Audit />} />
            <Route path="audit/:stage" element={<Audit />} />
            <Route path="export" element={<Export />} />
            <Route path="export/:stage" element={<Export />} />
            <Route path="_design" element={<Design />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
}
