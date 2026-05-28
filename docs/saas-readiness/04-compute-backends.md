# 04 -- Compute backends

This doc defines the **`ComputeBackend` abstraction** that decides
where the detection ensemble runs, the **three-tier compute model**
from doc 00 Q3, the **auto-selection** logic, and the **shape of the
cloud worker** that hosts Tier 2 + 3.

The detection ensemble itself is documented in CLAUDE.md and SPEC.md
under "Detection pipeline". This doc is about *where* it runs, not
*what* it does. The three voters (envelope onset, CLAP differential,
GBDT-with-PANN-feature) are the same in every tier; only the
execution location differs.

## The `ComputeBackend` abstraction

```python
class ComputeBackend(Protocol):
    """
    Runs the shot-detection ensemble for a single stage.
    Implementations decide where the work happens.
    """

    name: str  # 'local' | 'remote-tier2' | 'remote-tier3'

    async def detect_stage(
        self,
        project_id: str,
        stage_number: int,
        audio: AudioRef,           # see below
        config: DetectionConfig,
        progress: ProgressSink,
    ) -> DetectionResult: ...

    async def health(self) -> BackendHealth:
        """Quick check: model artifacts loaded, worker reachable."""
```

`AudioRef` is a tagged union:

```python
class AudioRef(BaseModel):
    kind: Literal['inline', 'storage', 'video']
    inline_bytes: bytes | None = None     # kind='inline'
    storage_path: str | None = None       # kind='storage'
    video_path: str | None = None         # kind='video'
    sample_rate: int | None = None        # required for 'inline'
```

The three kinds correspond to "audio is in memory" (typical for the
desktop where the user just opened a file), "audio is in
storage" (typical for hosted Tier 2 -- the browser uploaded WAV to
R2), and "audio still has to be extracted from a video" (Tier 1 +
Tier 3 from raw).

## The three tiers

### Tier 1 -- local desktop

**Backend:** `LocalComputeBackend`
**Where:** the user's machine, in-process or via the existing local
worker.
**Inputs:** raw video (or pre-extracted audio if the user supplies it).
**Outputs:** `DetectionResult` written to the local FilesystemStorage.

This is what `splitsmith ui` does today. No change in v1 except that
the orchestration code now goes through the `ComputeBackend`
interface instead of calling the ensemble module directly.

Pros: free, fully offline, native PyTorch speed.
Cons: needs CLAP + PANN downloaded (~150 MB) on first run, slow on
machines without GPU acceleration.

### Tier 2 -- audio upload + cloud ML (default for hosted premium)

**Backend:** `RemoteComputeBackend(tier='audio')`
**Where:** the browser handles cheap pieces, the cloud worker handles
heavy pieces.
**Inputs:** audio extracted in the browser via WebAudio (or in the
desktop and shipped over). Typically ~5 MB per stage as 16 kHz mono
WAV.
**Outputs:** `DetectionResult` written to S3Storage; the browser
displays it.

The split:

| Step                           | Where it runs (Tier 2) |
| ------------------------------ | ---------------------- |
| Extract audio from video       | Browser (WebAudio)     |
| Voter A -- envelope onset      | Browser (WASM, ~50 KB) |
| Run CLAP -> per-prompt sims    | Cloud worker           |
| Run PANN -> gunshot prob       | Cloud worker           |
| Voter B -- CLAP differential   | Browser (cheap math)   |
| Voter C -- GBDT                | Browser (ONNX-Web, ~3 MB) |
| Consensus + apriori            | Browser                |
| Audit JSON write               | Server (signed PUT to R2) |

Why this split:

- **CLAP and PANN are 150 MB combined.** Shipping them to every
  browser is a bandwidth + memory non-starter. They run server-side.
- **The GBDT is 3 MB and runs in milliseconds.** Shipping it to the
  browser saves a server round-trip per detection call and keeps
  the user's audio out of the cloud one more layer (only the audio
  goes up; the per-prompt similarities come back; the actual
  classification happens locally).
- **Envelope onset is pure DSP.** It compiles to small WASM and
  runs in the browser too.

The cloud worker's job in Tier 2 is narrowly: *take audio, return
features*. It does not see the final shot list, doesn't know the
consensus threshold, doesn't make per-shot decisions.

Pros: tiny upload (5 MB vs 5 GB), heavy models amortised, browser
keeps user-controlled control over the final consensus.
Cons: needs a network round-trip; CLAP runs serverside (privacy:
audio is in their datacenter for ~1s).

### Tier 3 -- full cloud (hosted, opt-in)

**Backend:** `RemoteComputeBackend(tier='video')`
**Where:** raw video on R2; the cloud worker does the entire pipeline
including audio extraction, beep detect, trim, ensemble.
**Inputs:** the raw video upload (5-30 GB per stage).
**Outputs:** stage trims written to R2; `DetectionResult` written to
R2; the browser fetches them.

This is the "I'm on a Chromebook and my video is on Drive" case.
The browser is a thin client.

Pros: any device, any bandwidth-after-upload.
Cons: full upload cost; the user's raw footage sits in our bucket
(opt-in per match per doc 00 Q2).

### Tier picker

```python
class TierPicker:
    async def pick(self, *, project: Project, stage: Stage) -> ComputeBackend:
        ...
```

Decision logic, in order:

1. **User override.** If `settings.compute_tier_override` is set
   for this project, honour it.
2. **Local data + capable browser?** -- pick Tier 1 (or Tier 2 if
   the WASM bench failed).
3. **Local data + incapable browser?** -- pick Tier 2.
4. **Cloud data?** -- pick Tier 3.
5. **Free-tier user?** -- always Tier 1; Tier 2/3 attempts return a
   paywall response (handled by the API layer, not the picker).

"Capable browser" is gated by a one-time bench (see "Browser
capability bench" below).

## Browser capability bench

On first hosted-mode visit, the browser runs:

1. **WASM cold start.** Load the envelope-onset WASM module, run it
   on a 5-second synthetic clip, measure latency. Target: <500ms.
   Above 1500ms => fall back to "cheap pieces server-side too" path
   (which means Tier 2 stops being available; the user is forced to
   Tier 3 for hosted detection).
2. **ONNX-Web cold start.** Load the GBDT, classify 100 fake feature
   vectors, measure. Target: <200ms total. Above 1000ms => same
   fallback as above.
3. **WebAudio decode.** Decode a 5-second AAC fragment to PCM,
   measure. Target: <300ms.

Bench results are cached in `localStorage` keyed by user-agent +
hardware concurrency. Re-run on UA change. Bench takes ~3 seconds
total on first visit; the user sees a "preparing your workspace"
spinner.

## The cloud worker

A FastAPI app + a Procrastinate worker, deployed together (Fly
machines or Railway services). The HTTP app receives detection
requests; the `splitsmith worker` process drains the queue.
Procrastinate's tables live in the same Postgres the API server
already uses, so the worker fleet doesn't need a separate broker.
Per-tenant queues (`user-<id>`) let us pin worker pools to specific
tenants later -- see [HOSTED-LOCAL.md](HOSTED-LOCAL.md) "Workers"
for the operational shape.

### Job shape

A detection job in Postgres:

```sql
CREATE TABLE compute_jobs (
  id              TEXT PRIMARY KEY,        -- ULID
  project_id      TEXT NOT NULL REFERENCES projects(id),
  stage_number    INT NOT NULL,
  tier            TEXT NOT NULL,           -- 'tier2' | 'tier3'
  status          TEXT NOT NULL,           -- 'queued' | 'running' | 'done' | 'failed'
  audio_storage_path TEXT,                 -- tier2: where the WAV is
  video_storage_path TEXT,                 -- tier3: where the source is
  result_storage_path TEXT,                -- where the DetectionResult JSON lives
  features_json   JSONB,                   -- tier2: per-prompt sims + PANN prob
  error_message   TEXT,
  enqueued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at      TIMESTAMPTZ,
  finished_at     TIMESTAMPTZ
);

CREATE INDEX compute_jobs_status_idx ON compute_jobs (status);
CREATE INDEX compute_jobs_project_idx ON compute_jobs (project_id);
```

The browser polls `GET /api/v1/projects/{pid}/jobs/{jid}` (or
subscribes via SSE) for status. The jobs drawer in the UI (already
shipped in the desktop redesign) reads from this table.

### Worker pool

- **Tier 2 worker:** runs CLAP + PANN. The model artifacts live in a
  baked-in Docker layer. Memory: ~600 MB resident with both models
  loaded. CPU only is fine for v1; GPU instances cost more than
  they save at our load. Concurrency: one job per worker process;
  spawn N processes per machine.
- **Tier 3 worker:** runs the full Python ensemble + ffmpeg. Needs
  more memory (~2 GB peak during ffmpeg), and IO bandwidth to R2.
  Same concurrency model.

For v1 the Tier 2 and Tier 3 workers are the same code with a flag.
We split into separate worker pools only if Tier 3 throughput becomes
a bottleneck.

### Idempotency

Job IDs are client-generated ULIDs. Re-submitting a job with the same
ID is a no-op (returns the existing job). This means the browser can
retry a failed POST without spawning duplicate work.

Result writes are idempotent because they're full-file overwrites at
known storage paths.

### Failure handling

- Worker crashes mid-job => Procrastinate retries up to 3 times
  with exponential backoff (configured via the task's
  ``retry`` policy). After 3 fails, the ``compute_jobs`` row
  moves to ``failed`` with ``error_message`` set.
- The user sees a "Retry" button. Retry creates a new job ID
  (different ULID) -- the failed job stays in the table for
  debugging.
- Sentry captures the exception with `project_id` + `stage_number`
  context; PII (audio bytes, full storage paths) is scrubbed.

## Cost model (rough, for sizing)

Tier 2 per stage:
- 5 MB audio upload (R2: free) + 5 MB egress to worker (free, in-
  region) + ~200 KB features back to browser.
- Worker: ~3 seconds of CPU on a 1-vCPU machine. At Fly.io's
  shared-CPU pricing (~$0.005/h) that's ~$0.000004/stage. Round up
  for cold starts and overhead: $0.001/stage.

Tier 3 per stage:
- 5-30 GB video upload (R2: free) + 5-30 GB egress to worker (free).
- Worker: ~30s-2min CPU + ffmpeg IO. ~$0.001-$0.005/stage.

A heavy user with 100 stages/month costs us ~$0.10-$0.50 in compute
+ ~$5-15 in R2 storage if they keep raw video. Either way, the
margins on a $5-10/month flat tier are healthy. Detail in 08.

## Local-mode degradation

If the local user's machine doesn't have CLAP/PANN downloaded yet,
the local backend falls back to "envelope-onset only" with a UI
banner asking the user to either (a) download the models (one-time
~150 MB), or (b) sign in to hosted to use Tier 2 without the
download.

This degradation path matters because the desktop's first-run
experience is "open the app, point at a video, see shots". We can't
require a 150 MB download before the user sees value. The envelope-
onset fallback is fast and produces a reasonable approximation; the
audit shows "ensemble unavailable -- voter A only" so the user
knows it's preliminary.

## Configuration

Per-project settings written to `match.json`:

```json
{
  "compute": {
    "tier_override": null,         // null | 'local' | 'tier2' | 'tier3'
    "raw_uploaded": false,         // tier3 only available if true
    "ensemble_consensus": 2        // 2-of-3 default, user-tuneable
  }
}
```

Per-user settings in Postgres `users` table (hosted mode only):

- `default_tier_override` -- "always force Tier 2", e.g.
- `bench_results` -- the cached browser-capability bench scores

Both are nullable; null means "use the picker default".

## Open questions

- **Tier 1 in the browser.** Could we ship CLAP + PANN as ONNX-Web
  and run *everything* in the browser? Probably yes someday; today
  the model sizes are too large. Track for v2 if WASM ML toolchains
  improve.
- **Per-tenant retrain.** Premium users might want to retrain the
  GBDT on their own audited data. v3+. Not in v1.
- **GPU workers for Tier 3.** A user with 30+ stages of raw video
  per match would benefit. Defer until usage shows the bottleneck.
- **Cold-start latency.** Fly.io machines suspend after inactivity.
  First detection request after suspend pays a ~5s cold start.
  Acceptable for v1; worth measuring under real load.
- **Local <-> remote feature parity.** If hosted's CLAP runs through
  ONNX and local's runs through PyTorch, do their per-prompt
  similarities match within a tolerance? Should be yes if we export
  with `opset=17` and disable dropout, but verify before launch.
