/** The lab, fixture and dev calls of ``api`` (split out of ``lib/api.ts``, #1580). */

import type { ModelMap } from "@/lib/modelMap";
import { request } from "./client";
import type {
  DevReviewQueueResponse,
  DeveloperModelInfo,
  FixtureReviewSignals,
  Job,
  LabEvalConfig,
  LabEvalRun,
  LabFixtureRecord,
  PeaksResult,
  PromoteReport,
  StageAudit,
  SweepRunDetail,
  SweepRunSummary,
} from "../api";

export const labApi = {
  getFixtureAudit: (fixturePath: string) =>
    request<StageAudit>(`/api/fixture/audit?path=${encodeURIComponent(fixturePath)}`),

  saveFixtureAudit: (fixturePath: string, payload: StageAudit) =>
    request<StageAudit>(`/api/fixture/audit?path=${encodeURIComponent(fixturePath)}`, {
      method: "PUT",
      json: payload,
    }),

  getFixtureReviewSignals: (fixturePath: string) =>
    request<FixtureReviewSignals>(`/api/fixture/review-signals?path=${encodeURIComponent(fixturePath)}`),

  getFixturePeaks: (fixturePath: string, bins = 1200) =>
    request<PeaksResult>(
      `/api/fixture/peaks?path=${encodeURIComponent(fixturePath)}&bins=${bins}`,
    ),

  fixtureAudioUrl: (fixturePath: string) =>
    `/api/fixture/audio?path=${encodeURIComponent(fixturePath)}`,

  fixtureVideoUrl: (videoPath: string) =>
    `/api/fixture/video?path=${encodeURIComponent(videoPath)}`,

  // -----------------------------------------------------------------------
  // Lab: fixture management + ensemble eval + tuning. Mirrors splitsmith.lab
  // (the Python module) -- see src/splitsmith/lab/core.py for shapes.
  // -----------------------------------------------------------------------

  listLabFixtures: () => request<LabFixtureRecord[]>("/api/lab/fixtures"),

  /** Hydrate the Lab page after a navigation: returns the most recent
   *  eval/rescore in this server session, or 404 when none has run. */
  getLastLabRun: () => request<LabEvalRun>("/api/lab/last-run"),

  /** Submit the eval and return the Job snapshot. The SPA polls via
   *  {@link api.pollJob} and then fetches the result with
   *  {@link api.getLastLabRun} once the job succeeds. */
  runLabEval: (payload: { slugs?: string[]; config?: Partial<LabEvalConfig>; persist?: boolean }) =>
    request<Job>("/api/lab/eval", { method: "POST", json: payload }),

  rescoreLabUniverse: (config: Partial<LabEvalConfig>) =>
    request<LabEvalRun>("/api/lab/rescore", { method: "POST", json: { config } }),

  saveLabConfig: (payload: { name: string; note?: string; overwrite?: boolean }) =>
    request<{ path: string }>("/api/lab/save-config", { method: "POST", json: payload }),

  applyLabLabels: (payload: {
    audit_path: string;
    labels: {
      candidate_number: number;
      time: number;
      reason?: string | null;
      subclass?: string | null;
    }[];
  }) =>
    request<{
      path: string;
      counts: Record<string, number>;
      run: LabEvalRun | null;
    }>("/api/lab/labels", {
      method: "POST",
      json: payload,
    }),

  rebuildLabCalibration: (payload: { target_recall?: number; tolerance_ms?: number; fixtures?: string[] } = {}) =>
    request<Job>("/api/lab/rebuild-calibration", { method: "POST", json: payload }),

  /** Developer-mode model info: active version, recall, fixture count,
   *  + step-counter map used to colour the workflow stepper. */
  getDeveloperModel: () => request<DeveloperModelInfo>("/api/dev/model"),

  /** The lab's Models page: corpus, shipped heads, held-out splits, history. */
  getDevModels: (model?: string) =>
    request<ModelMap>(model ? `/api/dev/models?model=${encodeURIComponent(model)}` : "/api/dev/models"),

  /** Review-queue rollup: promoted-from-match items, GitHub submissions,
   *  ad-hoc fixture review tasks. Drives /dev/review. */
  getDevReviewQueue: () => request<DevReviewQueueResponse>("/api/dev/review-queue"),

  /** "Approve to corpus": stamp review.confirmed_at on the fixture so
   *  it stops pending without a label pass. */
  confirmReviewFixture: (slug: string, method?: string) =>
    request<{ slug: string; confirmed_at: string; status: string }>(
      `/api/dev/review-queue/${encodeURIComponent(slug)}/confirm`,
      method
        ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ method }) }
        : { method: "POST" },
    ),

  promoteFromAnchor: (payload: {
    anchor_path: string;
    secondary_wav_path: string;
    slug: string;
    camera_id: string;
    mount: string;
    position: string;
    audio_source?: string;
    agc_state?: string;
    snap_window_ms?: number;
    overwrite?: boolean;
  }) =>
    request<{ job: Job; fixture_path: string; anchor_path: string; slug: string }>(
      "/api/lab/promote-from-anchor",
      { method: "POST", json: payload },
    ),

  getPromoteReport: (slug: string) =>
    request<PromoteReport>(`/api/lab/promote-report?slug=${encodeURIComponent(slug)}`),

  deleteFixture: (slug: string) =>
    request<{ removed: string[] }>(
      `/api/lab/fixture?slug=${encodeURIComponent(slug)}`,
      { method: "DELETE" },
    ),

  /** Ensemble parameter-sweep dashboard (read-only). Backed by
   *  ``build/sweeps/runs.parquet`` written by ``scripts/run_sweep.py``. */
  listSweepRuns: () => request<SweepRunSummary[]>("/api/lab/sweeps"),

  /** Full per-combo + per-fixture detail for one sweep run. */
  getSweepRun: (runId: string) =>
    request<SweepRunDetail>(`/api/lab/sweeps/${encodeURIComponent(runId)}`),

  /** Build a URL the SPA can drop into an ``<img src>`` -- request goes
   *  through FastAPI so the gitignored build/ tree is never directly
   *  exposed. ``plotName`` is the file stem without ``.png``. */
  sweepPlotUrl: (runId: string, plotName: string): string =>
    `/api/lab/sweeps/${encodeURIComponent(runId)}/plot/${encodeURIComponent(plotName)}.png`,

};
