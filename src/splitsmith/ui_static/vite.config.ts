import path from "node:path";
/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

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
  },
});
