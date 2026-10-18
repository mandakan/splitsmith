---
paths:
  - "src/splitsmith/share_card*.py"
  - "src/splitsmith/comment_identity.py"
  - "src/splitsmith/display_name.py"
  - "src/splitsmith/coach.py"
  - "src/splitsmith/stage_summary_data.py"
  - "src/splitsmith/match_summary.py"
  - "src/splitsmith/hud_extras.py"
  - "src/splitsmith/compare/free_cell.py"
  - "src/splitsmith/ui/capabilities.py"
  - "src/splitsmith/ui/share_og.py"
  - "src/splitsmith/ui/comments.py"
  - "src/splitsmith/db/share_guard.py"
  - "src/splitsmith/db/share_tokens.py"
  - "src/splitsmith/db/comments.py"
  - "src/splitsmith/db/profile.py"
  - "src/splitsmith/ui_static/src/lib/splits.ts"
  - "src/splitsmith/ui_static/src/components/share/**"
  - "src/splitsmith/ui_static/src/components/comments/**"
  - "src/splitsmith/ui_static/src/pages/Account*.tsx"
  - "tests/test_share_*.py"
  - "tests/test_comment*.py"
---

# Share links: preview cards, the anonymous surface, comments

The root CLAUDE.md repeats the three share rules that live in `ui/server.py`.

## Preview cards

- A share link previews as a 1200x630 card. The match card is a roster,
  **deliberately with no summed stage time**: IPSC ranks by hit factor, and
  accumulated raw time is not a figure the sport produces. The stage card leads
  with draw and average non-anomaly split, the numbers splitsmith computes.
- **The split rule lives in `coach.statistic_splits`**, and the share card, the
  video summary and the results page all use that one definition;
  `share_card.stage_figures` only shapes its output into a card's two headline
  figures. An uncoached stage
  falls back to the auto-classifier's own cutoff, `coach.split_stat_split_max()`
  (`CoachAutoClassifyConfig.split_max_s`), not an independent constant, or the
  figures would jump the moment a stage got classified. The thresholds
  (`split_max_s`, `transition_max_s`) are read through
  `coach.auto_classify_config()`, which honours a `SPLITSMITH_CONFIG` YAML
  (defaults `split_max_s` 1.0, `transition_max_s` 2.0). `lib/splits.ts` mirrors
  the split cutoff as a literal (`SPLIT_STAT_SPLIT_MAX`) and moves with it.
  `splitsmith match reclassify` re-runs the auto-classifier over stored audits
  (manual overrides survive) after a threshold change.
- `ui/share_og.py`'s `build_stage_card` heals legacy unclassified audit docs in
  memory and **never persists**: a share-only route must not mutate a doc an
  anonymous caller reached through impersonation. The heal's guard is
  `coach.heal_unclassified`, shared with `get_stage_coach`, the compare payload
  and the overlay renderer; **a new consumer of `statistic_splits` that reads
  raw audit shots calls it**, never re-derives the condition.

## The anonymous surface (`ui/share_og.py`)

- Four route families: the card PNGs, a JSON `og-meta` route, and the HTML
  shells at `/share/{token}`, `/share/{token}/results` and
  `/share/{token}/results/{slug}/{stage}`.
- **The shells sit outside the middleware that pins a share's owner**
  (`_share_alias` only rewrites `/api/...` paths), so a shell fetches its data
  through an in-process ASGI sub-request back into the anonymous API. Token
  resolution and owner impersonation must have exactly one implementation, in
  `_share_alias`. **Do not "simplify" a shell handler to read the tenant
  directly**: that would be a second implementation of impersonation.
- **`og:image` URLs are content-addressed** (`?v=<card_hash>`), and that query
  is the freshness mechanism: a re-audit moves the figures, the hash and the
  URL, so crawlers refetch. Without it a crawler keeps serving a stale card
  behind a long `Cache-Control`. Nothing invalidates a cache.
- Meta tags are injected server-side for every client: crawlers do not run
  JavaScript, so a client-side helmet would reach none of them.

## Writes and comments

- The share surface is not categorically read-only. `_SHARE_PATH_RE` (GET) and
  `_SHARE_WRITE_ROUTES` (POST/DELETE, method-paired) in `ui/server.py` are two
  separate allowlists on purpose and **must never merge**: merging would make
  `_SHARE_PATH_RE`'s GET-only contract false and let a write shape through the
  read table's fullmatch.
- `comment` is the only write-capable scope
  (`db.share_guard._WRITE_CAPABLE_SCOPES`). A plain `read` link can list the
  thread (it is deliberately in both allowlists) but never post or delete;
  `db.share_guard.scope_may_write`, called by `_share_alias`, enforces that.
  `ui/capabilities.py`'s comment moderation and read-only POSTs are deliberately
  absent from `_SHARE_WRITE_ROUTES`. Every non-admitted (method, shape, scope)
  combination collapses to the same opaque 404 as an unknown token.
- **`author_handle` is server-derived** (`comment_identity.derive_handle`, or
  the signed-in viewer's own `display_name`), never client-supplied; the request
  model has no field for it. Adding a display-name input to the compose box's
  POST body would let anyone sign a comment with someone else's name.
- A signed-in visitor comments under `users.display_name`, written only through
  `PATCH /api/me` from the `/account` page. **`splitsmith.db.profile` is the
  single writer**, which keeps "can this branch be reached?" answerable by grep
  (a branch shipped once with no writer and was dead in production). An account
  with a blank name falls back to a generated handle, pinned in
  `tests/test_comments_signed_in.py`.
