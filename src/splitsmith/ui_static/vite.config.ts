import path from "node:path";
/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Suite-wide timeout budget: 4x the worst test observed under load.
// See the comment on `test` for the measurements it comes from.
const TEST_BUDGET_MS = 30_000;

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    // Proxy API calls to the FastAPI backend in dev mode.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5174",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
  test: {
    // Component tests (MatchExport.test.tsx) need a DOM; plain-logic
    // suites (matchExportModel.test.ts, api.compareGrid.test.ts, ...)
    // run fine under jsdom too, so one environment covers both rather
    // than splitting test files across a node/jsdom pool.
    environment: "jsdom",
    setupFiles: ["./src/testSetup.ts"],
    // One budget for the whole suite (#878). Six files had grown their
    // own copy of a 30s timeout, each added by whoever next hit a red
    // run on a loaded box, and nothing stopped file seven from starting
    // at the default and being discovered the same way.
    //
    // The number is measured, not remembered. Worst observations across
    // three loaded full-suite runs, 2026-08-14:
    //
    //   ordinary test  6573 ms  MobileAudit.test.tsx, 409-on-save
    //   route-tree hook 4428 ms  App.routes.pickup.test.tsx
    //
    // Budget is 4x the worst, rounded up to 5s: 30s. The multiple is
    // generous because what it absorbs is machine load, which has no
    // ceiling, and a too-high budget only costs latency on a report
    // nobody is waiting for.
    //
    // Deliberately NOT scoped to the route files, which is where this
    // change started. Those six await `import("@/App")` and pull ~30
    // eagerly-imported page modules through vite's transform (~2s each,
    // 11.4s cumulative), so they looked like the expensive ones -- but
    // measuring said the worst ordinary test is ~50% slower than the
    // worst route hook. A per-glob project would have *lowered* their
    // budget while leaving MobileAudit.test.tsx failing at the 5s
    // default. "The route files are special" was folklore too.
    //
    // Both timeouts, not just hookTimeout: five of the six route files
    // import in `beforeAll`, modegate imports inside its single `it`,
    // and MobileAudit's failures are plain tests. The defaults differ
    // (testTimeout 5s, hookTimeout 10s), so both need saying.
    hookTimeout: TEST_BUDGET_MS,
    testTimeout: TEST_BUDGET_MS,
  },
});
