# Hosted benchmarks

`scripts/match_bench.py` replays a finished local match on a hosted server as
if it were a new hosted match, so a whole pipeline (raw clips to the rendered
compare grid) can be timed and costed on different workers. It works for any
local match whose clips are assigned and whose beeps are reviewed.

## Steps

```
uv run python scripts/match_bench.py manifest <match-dir> -o m.json [--stages 1]
uv run python scripts/match_bench.py upload m.json --server <url> --log upload.json
uv run python scripts/match_bench.py seed m.json --server <url> -o run.json --label 4cpu
uv run python scripts/match_bench.py run run.json -o result.json [--phases grid] [--grid-options g.json]
uv run python scripts/match_bench.py report result.json [more.json ...]
```

- `manifest` is media-free: clip to shooter, stages, role, camera, reviewed
  beeps, stage times. `--stages` cuts a dry-run subset.
- `upload` runs once per account: the raw pool (`raw/`) is account-level and
  a match only refers to it. Idempotent by name and size; its log is the
  upload throughput measurement.
- `seed` creates a fresh match per run and attaches the clips; no bytes move.
- `run` drives the seeded match through its phases, in order: `settle`
  (assigning a clip already queues its beep detection), `detect` (any clip
  left without a beep), `beeps` (each beep within `--beep-tolerance` of the
  manifest's is confirmed, any other corrected; trim and shot detection
  chain from that), `export` (each shooter's stage export, which writes the
  trims the grid reads) and `grid`. It writes every job created during the
  run with its `timings`, the beep corrections, and any orphans: a running
  job with no update for `--stale` seconds lost its worker and is recorded,
  not waited on. `--phases` reruns part of the pipeline on a match an
  earlier run took further; a failing phase still writes what was
  collected.
- `report` prints per-phase, per-kind and per-worker figures for one or more
  results and a cost estimate. A worker's billed span runs from its process
  start to its last job's end; prices are flags (`--vcpu-s`, `--gib-s`,
  `--egress-gb`) with approximate Cloud Run defaults; pass zeros for a
  self-hosted worker.

Authenticate with a hosted session: `--cookie` or `SPLITSMITH_BENCH_COOKIE`
(the `splitsmith_session` value). Use staging and a dedicated account with
the `raw_upload` and `hosted_compute` features, never production.

## Reading the results

Each job's `timings` has `queue_wait_ms`, `total_ms`, `phases` and the body's
`meta`, plus a `worker` record: `name` (`agent:<id>`,
`cloudrun:<execution>#<task>` or `host:<name>`), `vcpu` and `memory_mb` from
cgroups, `uptime_at_start_ms` (on a one-shot drain's first job, the startup
cost), `storage_read_bytes` / `storage_written_bytes`, `peak_rss_mb` /
`peak_child_rss_mb`, and `ran_alone`. Bytes and memory are process-wide, so
they are the job's own only when `ran_alone` is true.

## Traps

- Proxies (`raw_proxy/`) are per account: made at upload, never again. Time
  them from the upload, not from a run.
- The self-hosted agent keeps a persistent raw-video cache; Cloud Run starts
  empty every execution. Clear the agent's cache before a run that compares
  them, or report the warm and cold numbers separately.
- The render segment cache makes a rerendered grid faster than the first;
  clear it between `--phases grid` runs that compare workers.
- Cloud Run writes to R2 leave GCP as internet egress, which is billed per
  GB; reads are free. Include `storage_written_bytes` in any cost figure.
- Each one-shot Cloud Run execution exits once the queue is momentarily
  empty, so executions started by hand for a fan-out are gone by the next
  phase; start them again per phase (`gcloud run jobs execute ... --async`).
- A killed execution leaves its job `running` for good and cancel cannot
  clear it (#1677); a new submit of that kind and stage is deduplicated
  against it. Mark the row failed before resubmitting.
- Deleting a bench match leaves the raw pool alone unless
  `delete_raw_uploads` is passed, and then only unreferenced objects.
