# Open Graph images for share links

Design doc, 2026-08-09.

## Problem

A share link pasted into Slack, Discord, iMessage or X previews as a
bare URL. The SPA shell at `src/splitsmith/ui_static/index.html` carries
one `<title>splitsmith</title>` and no meta tags, and every non-API route
is served that same file verbatim by the SPA fallback
(`ui/server.py:14558`). Crawlers do not run JavaScript, so nothing the
React app renders can reach them.

Two share surfaces need previews:

- `/share/{token}` -- the match results overview.
- `/share/{token}/results/{slug}/{stage}` -- one shooter's run on one
  stage.

Share links are hosted-only. Local mode has no per-user share-token store
(`ui/server.py:1294`), so everything in this document is gated behind
hosted mode and returns 404 outside it.

## What the cards show

Rendered candidates were reviewed at true 1200x630 and at 200px
thumbnail size, which is where most people actually see them.

### Match card -- roster

Match name on the left in the condensed display face, shooter roster on
the right, stage count and date in the top rule. No hero numeral.

Summed stage time was rejected. IPSC ranks by hit factor and match
percentage; accumulated raw time across stages is not a figure the sport
produces, and a hero number that means nothing is worse than no hero
number. The roster is the content: it scales from one shooter to a
squad, and it is what a reader wants to know about a shared match.

### Stage card -- draw and average split

Two numerals of equal weight -- **draw** and **average split** -- with
the stage name, shooter and match in a column beside them. Stage number,
shot count and official stage time sit in the top rule.

Stage time was demoted deliberately. Splits are what this tool produces;
the stage time is imported from the scorecard. The card leads with the
figures splitsmith computes.

## Defining draw and average split

The taxonomy already exists. `CoachIntervalClass`
(`ui_static/src/lib/api.ts:1068`) is:

```
first_shot | split | transition | movement | reload | activation
```

- **Draw** is the `first_shot` interval.
- **Average split** is the mean of intervals classed `split`.
  Transitions, movement, reloads and activations are excluded by
  construction, not by a new threshold.

### Fallback for unclassified stages

A stage can be detected and audited without ever being coached, in which
case no interval carries a class. The fallback is the rule
`fcpxml_gen.split_color_band` already encodes: index 1 is the draw, and
any interval above `SplitColorThresholds.transition_min`
(`config.py:357`, default 1.0 s) is not a split.

Classification is all-or-nothing per stage: the coach path is used only
when every interval carries a class, and otherwise the threshold rule
covers the whole stage. Mixing the two within one run would produce an
average whose definition varies by which intervals happened to be
reviewed.

No new threshold is introduced. The card model records which path
produced its figures, so the fallback is testable and later auditable.

Tuning `transition_min` for this role is issue #773 -- it was chosen for
FCPXML marker colouring, and this design gives it more weight than it was
picked for.

### Zero-shot stages

A stage with no detected shots has no figures. It does not get a stage
card; its URL falls back to the match card image.

A stage where every interval is a transition or reload has a draw but no
splits. The card drops the average numeral entirely and the layout closes
up around the draw -- the two numerals sit in a flex row, so removing one
leaves no hole. It does not print a zero or a dash.
`compare/overlay_summary.py` already follows that rule ("only what can
actually be computed, never invented") and this follows it too.

### Relationship to #772

Issue #772 covers the same two figures on the video stage summary and the
results page, where splits are currently averaged over transitions and
reloads and the page omits the draw. That issue and this design share one
definition. Whichever lands first owns the shared Python helper; the
other consumes it. This design does not wait on #772 -- the share card is
correct on its own -- but the two must not end up with two definitions.

## Modules

### `src/splitsmith/share_card.py` -- pure, core

Pydantic models and pure builder functions. No file I/O, no browser.
Architecture rules 2 and 3.

```
RosterEntry:  name, division
MatchCard:    match_name, match_date, stage_count, roster: list[RosterEntry]
StageCard:    stage_number, stage_name, shooter_name, match_name,
              shot_count, draw, avg_split: float | None,
              split_count, interval_count, source: "coach" | "threshold"
```

Roster order is alphabetical by name, matching the slot-order convention
`compare/` already uses.

Builders take already-loaded data and return a card model. They do not
read files or touch the database.

### `src/splitsmith/share_card_html.py` -- pure

Card model to a 1200x630 HTML document. Pure string building, same shape
and constraints as `overlay_html.py`:

- `@font-face` rules point at `file://` URLs naming the bundled TTFs
  under `src/splitsmith/data/fonts/`.
- The document is written to a real file and navigated to, never handed
  to `page.set_content()`. `overlay_raster.py`'s module docstring records
  the measurement behind that constraint: with `set_content()` the
  bundled face silently fails to load and Chromium substitutes a host
  font, with no error.
- Every text box has `overflow: hidden` and long names clamp in the CSS.
  No Python measures text. This is the categorical fix `overlay_html.py`
  was built for.

### Colour tokens

Colours come from `overlay_theme.load_theme("splitsmith")`, which already
carries `ink`, `muted`, `rule`, `accent`, `accent_fill`, `accent_text`,
`split`, `split_good` and `stroke`. The card also needs a surface fill
and a dimmer label grey, so `scripts/build_overlay_theme.py` gains
`surface` and `subtle`, built from the same `index.css` block as the
rest.

Adding them to the build rather than hardcoding hexes preserves the
property that module already documents: the design system cannot silently
drift from the renderers.

### Rasterizing

Through the existing `overlay_raster.ChromiumRasterizer`, whose `png()`
takes `(html, *, width, height)`. It is the only browser-aware module in
the codebase and stays so. Playwright is already a core dependency and
Chromium is already installed in the production image
(`Dockerfile:204`). No new dependency.

Unit tests inject a fake through the existing `Rasterizer` Protocol and
never launch a browser.

### `src/splitsmith/ui/share_og.py` -- hosted-only router

Follows the lazy-import, always-registered idiom `sync_api.py` and
`device_auth_api.py` use, so a local-slim install still imports cleanly
and every route 404s outside hosted mode.

## Routes

### HTML shells

```
GET /share/{token}
GET /share/{token}/results/{slug}/{stage}
```

Registered before the SPA catch-all. Each returns `index.html` with meta
tags injected, for every client -- no user-agent sniffing. Injecting real
meta tags is harmless for a browser, and sniffing is a maintenance
liability that fails silently for every crawler not on the list.

`Cache-Control: no-cache` is preserved from the existing fallback: the
shell still points at a content-hashed bundle.

Every share shell also carries `<meta name="robots" content="noindex">`.
A share link is unlisted, not public.

Meta content:

| Tag | Match | Stage |
| --- | --- | --- |
| `og:title` | Tallmilan 2026 | Stage 3 -- Per told me to do it! |
| `og:description` | Mathias Axell / Production Optics / 7 stages | Mathias Axell / draw 1.28s / avg split 0.182s / 14 shots |
| `og:url` | canonical share URL | canonical stage URL |
| `og:image` | absolute PNG URL | absolute PNG URL |
| `og:image:width` / `:height` | 1200 / 630 | 1200 / 630 |
| `og:image:alt` | Splitsmith results card for Tallmilan 2026, 7 stages, 1 shooter | Splitsmith stage card: stage 3, draw 1.28s, average split 0.182s |
| `twitter:card` | `summary_large_image` | `summary_large_image` |

Absolute URLs are built from `state.public_base_url`, already required in
hosted mode and already used to build the share URL itself
(`ui/server.py:6026`).

### PNG endpoints

```
GET /api/share/{token}/og.png
GET /api/share/{token}/og/{slug}/{stage}.png
```

Both must be added to `_SHARE_PATH_RE` (`ui/server.py:936`). That regex
is the containment boundary for the entire anonymous surface -- the share
middleware impersonates the owner's tenant, so only read-only,
match-scoped routes that never let the client supply a match id belong
there. Both qualify.

Bytes are served through the endpoint rather than redirecting to a
presigned URL. Presigned URLs expire; an `og:image` has to stay
resolvable for as long as the link circulates.

## Generation and caching

The storage key carries a content hash of the card model:

```
share-cards/{token}/match-{hash}.png
share-cards/{token}/stage-{slug}-{n}-{hash}.png
```

The `og:image` URL is built at request time from live data, so the hash
in it is always current. A re-audit changes the figures, which changes
the hash, which changes the URL -- and Slack or X refetch rather than
serving a stale cached preview. Content addressing is what makes the
freshness problem disappear instead of needing an invalidation pass.

- **Match card**: rendered synchronously inside
  `POST /api/match/shares` (`ui/server.py:6034`). One render, so the
  link the owner pastes immediately previews without a cold render. This
  warms the first hash rather than pinning it: if the match data changes
  afterwards, the new hash simply misses the cache and renders on first
  fetch, the same as any stage card.
- **Stage cards**: rendered on first fetch and cached. Pre-rendering
  every stage at share creation would mean stages x shooters renders
  inside one request -- 28 for a 7-stage, 4-shooter match.
- Cache hits serve bytes with a long `max-age`, safe because the URL is
  content-addressed.

## Error handling

| Case | Response |
| --- | --- |
| Unknown token | Shell with generic Splitsmith meta; PNG 404 |
| Revoked token | Identical to unknown -- the meta must not reveal that a token once existed |
| Stage not in match | Falls back to the match card image |
| Stage with no shots | Falls back to the match card image |
| Chromium fails or times out | Bundled static brand plate, short `max-age` |

A link preview must never render a 500. The static fallback plate ships
as `src/splitsmith/data/share_card_fallback.png`, built once from the
existing brand artboard (`scripts/og/og.html`) and checked in, so the
error path needs no browser.

## Testing

Card model, pure:

- Coach-classified stage and the same run unclassified, asserting the two
  paths produce different averages where transitions exist -- a test that
  cannot pass if the classification is being ignored.
- Multi-shooter roster ordering.
- Zero-shot stage, and a stage whose every interval is a transition.
- `source` field reports the path actually taken.

HTML, pure: string assertions, no browser.

Raster: fake `Rasterizer` for unit tests. One `@pytest.mark.integration`
test drives real Chromium and asserts the PNG decodes at exactly
1200x630. Per CLAUDE.md, an integration test that needs media builds it
rather than skipping in CI; this one needs no media at all.

Routes:

- Both shells carry correct `og:*` and `twitter:*` values, and `noindex`.
- A revoked token's shell is byte-identical to an unknown token's.
- Second PNG fetch does not re-render -- assert the rasterizer was called
  exactly once.
- Every PNG route 404s outside hosted mode.

Per CLAUDE.md's review practice, each new test must be checked against
the pre-change code: delete the fix, watch the test fail. A test that
passes against the bug it claims to cover is not coverage.

## Out of scope

- The marketing site at `splitsmith.app`. It already has OG tags and a
  brand plate (`site/index.html`, `scripts/capture_hero_og.py`).
- Video frames on the card. Considered and dropped: it needs media access
  at generation time and pushes footage into link previews, which is a
  disclosure decision that belongs with #754, not here.
- Per-share slice granularity (#630) and artifact-scoped shares (#753).
  This design assumes the current model, one token per match.

## Follow-ups

- #772 -- draw and non-anomaly average on the video summary and results
  page, and the shared definition.
- #773 -- tune `transition_min` against the corpus.
