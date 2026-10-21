# A sting that draws with the frames either side of the cut (#1535)

## Goal

A transition template (a sting, #1245) can opt in to receive the outgoing
and incoming edge frames of its boundary and draw the whole picture: push,
slide, page turn, column drop, zoom-through. Its clip replaces the
boundary's `xfade`; the audio stays the boundary's crossfade.

A sting without the opt-in, and every boundary that is not a sting, keeps
today's context, digest, argv and pixels byte for byte.

## Decisions

| Question | Decision |
|---|---|
| Example sting | Both: a plain `push` in the splitsmith Look, and Broadcast's `push` rebuilt to move the footage |
| Transparent output | Composited over opaque black; `looks check` warns ahead of time |
| Page size | The sequence size, no cap (sharp footage at 4K, slower only on a cache miss) |
| Picture handed over | The edges as encoded: overlay, lower third and watermark burnt in |
| Cards | Out of scope; the mount and opt-in are named generically so cards can follow |
| Hosted | Unchanged: own templates stay desktop only; the shipped pushes render hosted as the wipe does |

## 1. The template contract

- **Opt-in.** `<meta name="splitsmith-frames" content="edges">` in a
  `transition` slot template, read from the template bytes by
  `look.sting`. Any other `content`, or the tag in another slot, is
  ignored, and `looks check` warns.
- **Data.** `data.transition.frames`:

  ```json
  {"fps": 30, "count": 75,
   "from": ["https://look.invalid/frames/<digest>/from/0001.jpg", "..."],
   "to":   ["https://look.invalid/frames/<digest>/to/0001.jpg", "..."]}
  ```

  `count = ceil(duration * fps)`, the number of frames the renderer
  samples; `from[n]` and `to[n]` are the pictures at `seek(n / fps)`.
  `from` is the outgoing (tail) edge, `to` the incoming (head) edge. Where
  an edge's handle ran out, the boundary holds a frame (`tpad`); the list
  repeats the held file's URL the same way (the tail's last, the head's
  first), so the list is what the boundary shows and a template does no
  index arithmetic.
- **Helper.** `_shared/frames.js` exposes `window.splitsmithFrames.at(n)`,
  a Promise of `{from, to}` as decoded `ImageBitmap`s (each `null` when
  the context carries no `frames`), closing the
  previous pair so at most two frames are held. A template's `seek(t)`
  awaits it (`seek` may already return a Promise) and draws onto a
  `<canvas>`. Preloading every frame is about 1.2 GB decoded at 1080p, so
  the helper never does it. Templates may use the URLs directly.
- **Output.** The clip is composited over opaque black at the sequence
  size and used in place of `xfade`. `duration()` returns the transition's
  length as for every sting.
- **Without the tag** nothing changes: no `frames` key, today's digest,
  today's argv.

## 2. Sandbox and extraction

- **Mount.** `look.sandbox.prepare(template, context, frames_dir=None)`.
  When set, the sandbox mounts that directory as `/frames/<digest>/`
  (digest as in section 3). Only the renderer passes it. URL rewriting
  never creates the mount, so no `data` text can name it: the existing
  `test_user_text_naming_a_file_is_never_mounted` gains a stage named
  after the frames path. Symlinks out are refused, files are capped at
  `MAX_ASSET_BYTES`, the mount is read-only, as for every other mount.
- **Extraction.** `look.frames.extraction_command(edge, out_dir, *, rate,
  ffmpeg_binary)` is pure argv: `ffmpeg -i <edge> -vf fps=<rate> -q:v 2
  <out_dir>/%04d.jpg`. It runs through the boundary runner, so unit tests
  mock it. `edge_frame_urls(written, *, count, pad_frames, end, base)`
  builds each clamped list from the files ffmpeg actually wrote, never
  from an expected count. `pad_frames` comes from the boundary's own
  `tail_pad_seconds` / `head_pad_seconds`.
- **Failure.** An edge that yields no frames, a template error or a
  timeout makes the boundary a cut plus a degradation, as any failed
  boundary is today. No browser makes it a fade plus a degradation, as
  today.

## 3. Renderers and the cache

- **One shared step.** Both drivers call `look.sting.frames_sting_prepare`
  (template, context without frames, the two edge files, pads, fps,
  rasterizer, ffmpeg binary, runner, clip path). It extracts both edges
  into `<work>/<boundary>_frames/{from,to}/`, completes the context,
  renders the frames through the rasterizer with the frames mount, and
  writes the alpha MOV with `look.motion.write_motion_clip`. It runs as
  the boundary encode's `prepare`, so a cache hit extracts and draws
  nothing.
- **When the sting is decided.** `sting_for_boundary` keeps deciding the
  sting before the edges are encoded (template present, browser present).
  For a frames sting it loads the template once with the frames-free
  context, exactly as an overlay sting is loaded today, to read
  `sounds()`: the boundary's timeline entry carries those cues on a cache
  hit too, and on a hit there is no second load. No frame is pulled from
  that load. Drawing waits for `prepare`, which loads it again with
  `frames`. So **`duration()` and `sounds()` must not depend on the
  frames** (they get the transition's length, as today), and a template
  loaded without `frames` must not throw; `frames.js` answers `null`
  pictures then, and `looks check`'s ordinary probe is that load.
- **Digest.** `template_digest` over the context without `frames`, plus
  the fps and both edges' segment cache keys. With the cache off there are
  no edge keys and nothing is cached, as today. The boundary's virtual
  inputs carry the digest for the sting clip, as single-shooter does
  today, so a re-cut edge, a template file or `_shared/` redraws the sting
  and nothing else does.
- **Boundary graph** (frames sting only):
  `color=black:s=<W>x<H>:r=<rate>:d=<seconds>` with the clip laid over it
  (`motion_overlay_filters`), then `format=yuv420p`. Inputs 0 and 1 stay
  inputs; their video is unused, their audio feeds `acrossfade` (single
  shooter) or each of the N+1 per-stream crossfades (grid). The edges are
  still encoded: the template's frames come from them and the audio is
  theirs. `_build_boundary_command` and `build_boundary_segment_command`
  take `sting_replaces: bool = False`; at `False` the argv is today's.
- **Grid sting caching.** Today the grid writes the sting clip before the
  boundary encode and never keys the boundary by the sting's digest, so a
  grid sting renders its frames on every export. The grid moves the clip
  write into its encode's `prepare` and adds the digest to the boundary's
  virtual inputs, for overlay stings too. This changes the key of an
  existing grid sting boundary (a one-time miss, no pixel change), so
  `segment_cache.KEY_VERSION` is bumped.

## 4. Authoring, examples, docs, tests

- **`looks check`.** A frames sting is probed with two sample edges
  generated with Pillow at check time: a bar moving across a mid grey (the
  moving clip) and a still gradient. It reports script errors, and any
  frame where more than 0.5 % of pixels have alpha below 255 (the threshold
  tolerates antialiased edges), and warns about a `splitsmith-frames` tag
  outside the transition slot.
- **`looks preview`.** Draws a frames sting over the sample edges.
- **Rail preview** (`export_preview`). `from` is the stage's tail frame,
  `to` its head frame: the stills the preview already grabs, so a request
  runs no new ffmpeg. `PREVIEW_REVISION` bumps. The motion WebP plays the
  frames sting over black, not over the frame.
- **Shipped stings.**
  - `data/looks/splitsmith/sting-push.html` (`sting:push`): the outgoing
    picture moves off left as the incoming moves in from the right,
    eased.
  - Broadcast `push` (`broadcast-sting.html`) rebuilt: the outgoing stage
    is pushed off by the accent panel, which still carries the next
    stage's name, and the panel leaves to show the incoming stage moving
    in. Recorded in `_history.json`; Look previews and gallery clips
    re-rendered (`render_look_thumbnails.py --look-previews`).
  - The `sting` starter stays an overlay sting; a second starter
    `sting-frames` is added to `_starters/`.
- **Docs.** `docs/looks/authoring.md#stings` documents the contract; the
  `rendering.md` and `looks.md` rule files describe the opt-in, the mount
  and the replacing boundary. A What's new entry for the two pushes.
- **Tests.**
  - Unit: tag parsing; `edge_frame_urls` clamping for tail pad, head pad
    and no pad; the sandbox serves the mount only when the renderer passes
    it; user text naming the mount is never mounted; digest moves with an
    edge key and not with the frame URLs.
  - Argv byte-equality: an overlay sting and every non-sting kind produce
    today's boundary argv in both renderers.
  - Integration (`@pytest.mark.integration`, media from
    `tests/synthetic_media.py`): in both renderers, a push between a red
    stage and a blue stage shows, mid-boundary, the left half red and the
    right half blue (pixels read from the output MP4); a second render
    with the same cache extracts and draws nothing; touching one edge's
    stage redraws it.
  - `looks check`: a template that clears to transparent is reported; the
    two shipped pushes are not.
  - Every new test is run against the pre-change code (fix deleted) and
    must fail there.
- **Visual check.** `render_match_frames.py` and `render_grid_frames.py`
  with `--transition sting:wipe` against main (unchanged) and with
  `sting:push` (new), shown as an artifact before the PR is called done.
