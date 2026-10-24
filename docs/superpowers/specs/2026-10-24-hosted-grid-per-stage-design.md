# Hosted compare grid, one job per stage (#1691)

Status: design, 2026-10-24. Epic #1675.

## Why

A hosted whole-match compare grid cannot finish. On the HFO Masters 2026
benchmark (4 shooters, 12 stages, 48 exported trims, Cloud Run 4 vCPU /
8 GiB) the `compare-grid` job is killed while loading: `_load_compare_bundles`
pulls every stage's exported trim (about 230 MB each, about 11 GB) onto a
filesystem that is memory. Even with the memory, it renders the stages one
after another in one job: one stage takes 205 to 343 s, so twelve take about
43 minutes, close to the 3600 s task timeout.

A grid is the last step after auditing and exporting, so nobody is waiting
on it the way they wait on a trim, but it should be ready while the user is
still at the screen.

## Goals

- **A basic grid at 1080p is ready 10 to 15 minutes after the user asks**,
  for a match the size of HFO (12 stages, 4 shooters). Basic means the
  minimum of overlays and effects: synced tiles, no shot overlay, no
  summaries, no cards, no transitions.
- **Any grid finishes**: 4K, a dressed grid (overlay, summary holds, cards,
  transitions, match summary), a larger match. No OOM kill, no task
  timeout. These have no speed target yet.
- **Memory per job is bounded by one stage**, whatever the number of stages.
- **A re-render that changes only cards, transitions or the stage order
  reuses the stage pieces already rendered** and takes minutes.

## Non-goals

- GPU or larger workers. Measured: one stage rendered in 206 s on 8 vCPU
  and 205 s on 4 vCPU, so a bigger worker does not help. Parallel stages do.
- A speed target for 4K or the dressed grid. Measure them after this ships.
- Any change to what a grid looks like.
- The local (desktop, CLI) render path. It keeps rendering in one process
  with the local segment cache; it must keep working unchanged.

## Anti-goals

- **A stitched grid that does not look like one render**: stages that
  differ in frame rate, stream layout, audio mix, overlay timing, or a
  visible seam at a stage boundary. The output must be the file the
  one-process render would have produced, segment for segment.
- A grid job that reports success while stages are still rendering, or a
  user who cannot cancel it.

## Constraints

- Cloud Run worker job: 4 vCPU, 8 GiB, filesystem in memory, 3600 s task
  timeout, fan-out of up to 8 executions (`SPLITSMITH_WORKER_MAX_EXECUTIONS`
  in `deploy/cloudrun`), one job at a time per execution (procrastinate
  `concurrency=1`), about 20 to 35 s to start an execution.
- Sources and products live in R2. Trims, exports and beep detection already
  read their source from presigned URLs (`MatchProject.source_url`,
  `.claude/rules/media.md`, "Streaming a source to ffmpeg"); a URL read is
  trusted only with empty stderr and falls back to a download.
- A request never runs ffmpeg or pulls media (`.claude/rules/media.md`).
- No new dependencies.

## The renderer today

`compare/mp4_grid.render_grid_mp4` already builds the grid from pieces:

- a **spine** (`plan_grid_spine`): the ordered items (title page, stage
  slates, stages, match summary, closing card) and the boundaries
  (transitions) between them. Planning is pure: it needs every stage's plan
  (`build_stage_plans`: tiles, beep offsets, durations, frame rate from the
  audio shooter) but no frames;
- one **segment per item**, encoded through `_GridEncoder`, which consults a
  content-addressed `SegmentCache` (`segment_cache.py`, keyed on the ffmpeg
  argv and the identity of every input file);
- for a transition, **edge segments** of both neighbours plus the boundary
  segment built from them; a stage next to a transition is narrowed by half
  the transition at that end;
- a **concat stitch** (demuxer, stream copy) of the segments in order.

Every segment carries the same stream layout (video plus the mix plus one
audio track per label), which is what lets the stitch copy streams.

## The design

### Stage jobs and a stitch

1. The request (`POST .../match/compare-export`) queues the `compare-grid`
   job as today. That job is the one the user sees: its id, progress,
   result and cancel are what the SPA already uses.
2. The `compare-grid` job plans the whole render (spine and stage plans)
   from metadata, then queues **one `compare-grid-stage` job per stage**
   and waits for them.
3. Each **stage job** renders exactly its stage's segment, as the
   one-process render would have encoded it for this request (narrowed for
   its neighbours' transitions, with its overlay, summary hold, lower third
   and free cell), and stores it in R2. It reads only its own stage's
   tiles. It renders nothing else.
4. When every stage job is terminal, the `compare-grid` job runs the normal
   render. Every stage segment is a cache hit, so it encodes only the cards,
   edges, boundaries and match summary, then stitches, pushes the output and
   reports as today.

A stage job runs the same planning and the same encode code as the
one-process render, restricted to one item (for example a stage filter on
`render_grid_mp4` that plans the whole spine but encodes only that stage and
skips the stitch). My call: one code path for both, so the segments cannot
drift apart.

**The stitch pass must not pull every tile either.** An edge segment reads a
transition's half second at each end of two neighbouring stages, and the
match summary reads one freeze frame per tile per stage. Those reads go
through presigned URLs with `-ss` (a few range requests each), or the stage
jobs store the frames the stitch needs; never by pulling the trims, which is
the 11 GB this design exists to avoid.

Stage jobs fan out through the existing dispatcher: twelve queued stage jobs
read as a backlog and get executions up to the cap. Serve starts one execution
per two queued jobs (`SPLITSMITH_WORKER_JOBS_PER_EXECUTION`), so twelve
stage jobs and the grid job get seven: two rounds. (Revised after review:
the request, not the grid job, queues the stage jobs, because only serve's
deferrer wakes executions; the grid job renders any stage still queued.)

Expected for HFO at 1080p, basic (estimate, to be measured): about 1 minute
to start and plan, one round of stage renders, under a minute to stitch.

### A shared segment cache

The one-process render already skips any segment whose key is in the cache.
Making that cache shared is what joins the stage jobs to the stitch:

- On a hosted worker the `SegmentCache` is backed by R2 under the match
  (for example `matches/<id>/render-segments/<key>.mp4`). A stage job pushes
  its segment there; the stitch finds it by the same key.
- **The key must be the same on every worker.** Today an input outside the
  work dir is keyed by its file identity on this disk, which differs between
  workers (download time, path). For a tile read from storage the key must
  use the storage object's identity (key plus version or etag), and a
  presigned URL must never be part of a key (it changes per signing and is
  a credential).
- The stitch should not pull every stage segment onto its memory disk
  before concatenating if the concat demuxer can read them from presigned
  URLs; at 1080p a stage segment is small enough that pulling is also
  acceptable. Builder's call, measured.
- Eviction: the segments of a match go with the match (and its exports
  delete path). An age or size bound on the prefix is the builder's call.
- The local path keeps its local cache unchanged.

### Stage tiles

A stage job reads only its stage's tiles: about 0.9 GB for four 4K exported
trims, which fits beside one render. Reading them from presigned URLs, as
trims and exports do, is preferred, with the same stderr rule and download
fallback; pulling one stage is acceptable. A tile with no export is cut from
the source by the stage job (`_cut_stage_from_source`, today's #1681 path),
one stage at a time.

### Planning without the media

The parent and every stage job need the whole spine to know a stage's
narrowing and index, and planning needs each tile's duration and the audio
shooter's frame rate. Today the worker pulls the trims and probes them. Plans
for every stage must be built without pulling every trim: from a probe over
a presigned URL (one small range read each), or from metadata stored with the
export. Every job must arrive at the same plans for the same request, or the
segments will not join.

### Waiting, progress, cancel, failure

- **Fan-in is the parent waiting.** The `compare-grid` job polls its stage
  jobs (state store, not HTTP) until all are terminal. It holds one
  execution while it waits, which costs one fan-out slot and cents. My call:
  it is simpler and safer than a last-child-submits-the-stitch handoff, and
  it keeps one job the user watches.
- **Progress** on the parent reflects the stages: rendered of total, then
  stitching.
- **Cancel** of the parent cancels its queued and running stage jobs.
- **A failed stage** (error, OOM kill ended by the orphan sweep #1677, or a
  timeout) leaves its segment missing. The parent then renders that stage
  itself in the stitch pass, as today's one-process render would; if that
  fails too, the stage is a failed stage in the result (`failed`, partial
  success), as today.
- **The parent's wait has a deadline** well inside the task timeout, after
  which it renders what is missing itself or fails cleanly.

## Measure first

Before building, render one HFO stage on staging at 1080p, basic and
dressed, to put numbers on the stage job: wall time, peak RSS, where the
time goes (decode of four 4K tiles, filter graph, encode). If decode
dominates at 1080p, a cheaper tile input is worth a look (see below).

## Left to the builder

- The R2 layout and eviction of the shared segments.
- Whether the stitch reads segments over URLs or pulls them.
- How planning gets durations and frame rates without the media.
- How the parent waits (poll interval, deadline) and how cancel reaches
  stage jobs.
- Whether a stage job starts its own Chromium (it must when the stage has
  an overlay, summary hold, lower third or free cell). Chromium starts per
  job instead of once per render; measure it.
- A cheaper tile input for 1080p: a 2x2 cell at 1080p is 960x540, and the
  trim's 720p web rendition already exists. It is cut from the audit trim
  (a different window and an encode generation), so it is a quality and
  sync question, not a free win. Only if the measurement says decode
  dominates.

## Tests that pin it

- A whole-match hosted grid renders with per-job peak memory bounded by one
  stage (a fake storage and a counting renderer suffice for the bound).
- The stitched output of the split render is segment-for-segment identical
  (same cache keys, same concat list) to the one-process render for the
  same request, with and without transitions and cards.
- A cache key for a stored tile is the same on two workers and contains no
  URL.
- A failed or killed stage job is rendered by the parent, or reported as a
  failed stage.
- Cancel of the parent cancels its stage jobs.
- The local render path is unchanged (existing grid tests stay green).

## Decided

- **Fan-out cap 13** (user, 2026-10-24): `SPLITSMITH_WORKER_MAX_EXECUTIONS`
  goes from 8 to 13 in both `deploy/cloudrun/*-serve.env.yaml`, so twelve
  stage jobs and the waiting parent run in one round. Both environments at
  the cap use 104 of the region's 200 vCPU and 208 of its 400 GiB.
- **The default canvas stays as it is** (4K UHD on the export page, 1080p
  offered as faster). My call, since the question was not answered: the
  10 to 15 minute target is measured at 1080p, and changing the default is
  a separate, user-facing decision.
