# Share-write foundation: scope-keyed share defense + match capabilities

Issues: #779 (share tokens run under the owner's tenant, RLS is no defense
against writes) and #756 (derive match writability as a capability, not an
origin check). Designed 2026-08-12, brainstorm-first per kickoff memory.

## Problem

Two related gaps, one mechanism:

1. **#779**: the share alias pins `current_tenant` to the token owner, so a
   write issued while serving a share request passes RLS as the owner. The
   only hard stop is the GET-only `_SHARE_PATH_RE` whitelist in
   `_share_alias`; `current_share_request` is consulted by route code as a
   convention, not enforced anywhere. One layer, load-bearing.
2. **#756**: the SPA decides write affordances from `origin === "desktop"`
   and the share pathname. Only Home hides what a mirror cannot do; every
   other match page offers buttons that 403. Meanwhile the server's mirror
   guard has grown five hand-listed exception regexes - a de-facto
   capability system encoded as routes.

The next chunk (coach share tokens) needs SELECTIVE writes through share
auth, so any blanket read-only defense must key off a token scope from day
one.

## Decisions (user-approved 2026-08-12)

- Scope: foundation only. Coach write-scoped tokens are a later chunk that
  flips the scope switch; all tokens shipped here are read-only.
- Capability shape: an extensible list of named capabilities, not a boolean.
- Guard design: the server guard is DRIVEN BY the capability table (one
  route-to-required-capability mapping serves both the 403 decision and the
  SPA payload), not computed alongside it.
- #779 mechanism: READ ONLY transaction keyed off token scope, plus the two
  cheap complements (store-level refusal, byte-identity test net).

## 1. Capability model

A small closed set, extensible later:

- `edit` - the full mutation surface: trims, stages, shooter add/delete,
  ingest, exports. Default requirement for any write route not otherwise
  classified, so new write routes are edit-gated unless deliberately opened.
- `review` - the phone-triage set mirrors already allow: beep-queue confirm,
  beep writes (`_mirror_beep_write_re`), audit accept and needs-attention
  (`_mirror_triage_write_re`), coach patch (`_mirror_coach_patch_re`),
  coach reclassify (`_mirror_coach_reclassify_re`).
- `share_manage` - the `match/shares` routes.

Computed server-side in one function next to the alias guard:

| Context                    | Capabilities                 |
| -------------------------- | ---------------------------- |
| Hosted native match        | `edit, review, share_manage` |
| Desktop-origin mirror      | `review, share_manage`       |
| Local mode                 | `edit, review`               |
| Share request (read scope) | none                         |

Share-token scopes map to capability sets in the same module: `read` maps
to the empty set. A later `coach` scope adds one mapping entry; nothing
else changes.

## 2. Guard unification (#756 server side)

Replace the five exception regexes inside `_match_id_alias`
(server.py:6507-6520) with a route classification table: (method, path
pattern) -> required capability, defaulting to `edit` for unlisted write
routes. The guard 403s `read_only_mirror` (wording unchanged) when the
match's capability set lacks the required capability. Safe methods (GET,
HEAD, OPTIONS) never consult the table.

The same capability set is serialized as `capabilities: [...]`:

- on the match context payload, next to where `origin` is added
  (server.py:6982);
- on share payloads, where it is the token scope's set (empty today).

A parity test asserts that every route the old regexes allowed or denied
for a mirror gets the same verdict from the table.

The share alias's GET-only whitelist stays untouched as the containment
layer; the opaque-404 behavior for non-GET share requests is unchanged.

## 3. SPA sweep (#756 client side)

- `api.ts`: `MatchCapability` type, `capabilities` field on match context
  payloads, `hasCapability()` helper.
- Every match-scoped page (Home, Ingest, BeepReview, Review, Export,
  MatchExport, Compare, PromoteReview) gates write affordances on `edit`
  instead of `origin`. Per-surface rule from the issue: hide when the
  action is one of several; disable-with-reason where absence would read
  as broken (the Export pages).
- The MatchShell read-only banner condition switches to "capability set
  lacks `edit`". Copy may still mention desktop origin when
  `origin === "desktop"` - origin remains legitimate as provenance (picker
  flag) and behavior (#821 proxy-poll arming), never as a writability test.
- `isReadOnlyMirrorError` stays as the backstop: a 403 reaching a generic
  error path is a page bug, not a guard bug.
- Forward-compat check (the point of #756 gap 2): force the capability set
  writable for a desktop-origin match and every page must become fully
  functional without touching page code.

## 4. #779 defense in depth (scope-keyed)

- **Schema**: `share_tokens.scope`, text `NOT NULL DEFAULT 'read'`, Alembic
  migration. Only value shipped: `read`. The table stays outside RLS.
- **Middleware**: `_share_alias` records the resolved token's scope in a
  ContextVar alongside `current_share_request`.
- **DB enforcement**: for read scope, the existing `after_begin` hook
  (engine.py, where the tenant GUC is set - per-transaction, NullPool-safe)
  additionally issues `SET TRANSACTION READ ONLY`. Any write anywhere in
  the request fails loudly in Postgres (SQLSTATE 25006 -> 500 -> Sentry),
  including code that never heard of the ContextVar. No-op on non-Postgres,
  which is fine: the share surface is hosted-only.
- **Store complement**: `ProjectStateStore` and share-touched siblings
  raise on mutation entry points when the current share request is
  read-scoped. One check at the choke point instead of one per route.
- **Test net**:
  - parametrized test walking all 11 `_SHARE_PATH_RE` routes against a
    seeded match, asserting `state_docs` rows byte-identical before/after;
  - a docker-marked Postgres test proving the READ ONLY transaction
    rejects a write issued mid-share-request.

## 5. Error handling

- READ ONLY violations surface as 500s by design - they indicate a bug
  (a whitelisted route grew a write side effect) and should be loud, not
  swallowed.
- Mirror guard keeps the `read_only_mirror` detail string; share alias
  keeps the opaque 404 for anything outside the whitelist.
- Unlisted write routes require `edit` by default - the safe failure mode
  for future routes is "over-restricted", never "silently writable".

## 6. Delivery

Two PRs, in order:

- **PR A (#779)**: scope column + migration, scope ContextVar, READ ONLY
  transaction hook, store-level guard, test net. Backend-only,
  self-contained. Run `pytest -m docker` locally before merge (DB change).
- **PR B (#756)**: capability model + route classification table, guard
  refactor with parity test, `capabilities` payload field, SPA sweep with
  vitest coverage, forward-compat check.

Resolved during exploration: the #756 "worth checking" item about mirror
deletion needs no change - match removal goes through
`POST /api/me/recent-projects/delete`, which is not alias-routed, so
mirror recovery already works and Home's comment and the guard are both
right.

Out of scope: coach write tokens and the scope picker UI (next chunk, on
top of this foundation), transfer endgame (#631), mirror contents (#757).
