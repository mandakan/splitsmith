# Timestamped comments on shared stage video - design

Date: 2026-08-13
Status: approved pending review
Surface: the stage video page, `/share/{token}/results/{slug}/{stage}` and its
operator-route twin `/results/{slug}/{stage}`.

## Problem

A share link today is a one-way broadcast. A club mate who spots something at
4.3 s into your run has no way to say so in place - the conversation moves to
a chat app and loses the video, or it happens in person and is lost entirely.
The domain already has a first-class way to address a moment; what is missing
is a way to attach a sentence to one.

## What already exists, and what this actually adds

Moment deep links shipped (spec `2026-08-12-moment-deep-links-design.md`,
`ui_static/src/lib/moment.ts`): `?t=4.32&cam=&who=` addresses a
stage-relative-to-beep timestamp, works identically on operator and share
routes, and survives re-trims. `coaching_note` on the audit doc is a per-shot
free-text note the owner writes. Neither is a conversation.

So the new capability is narrow and the risk is concentrated in one place: a
comment is **a write from an unauthenticated visitor**, and the anonymous
share surface is GET-only by construction. `_share_alias` rejects any non-GET
before it looks at the path (`ui/server.py`), and `_SHARE_PATH_RE`'s docstring
calls itself "the entire anonymous surface". This design's central question is
how to admit one write without falsifying that claim.

## Decisions made during brainstorming

- **Commenters**: anyone holding a link. No login required.
- **Visibility**: public - everyone with the link sees the thread.
- **Anchor**: the commenter picks a time or a shot; the compose box snaps to a
  nearby shot and offers it.
- **Attribution**: a signed-in visitor comments under their account
  `display_name`; everyone else gets a stable, server-generated IPSC-themed
  handle. No free-text names.
- **Accounts**: no new signup path in v1. The "limited account" tier
  (comment-capable accounts, possibly with other capabilities) is a separate
  product decision, deliberately not made here.
- **Bookmarks**: not built. Comments ship on top of the shipped `Moment`; the
  bookmark forward-design in the moment spec section 5 stays forward-design.
- **Surfaces**: stage video page only. Compare is deferred because a comment
  there needs a second anchor shape - which shooter in the grid it is about -
  and that decision is better made once the single-shooter shape has run.
- **Write path**: a write-capable share scope (option A below).
- **Owner delete**: a release condition, not a follow-up.

## 1. The write path

`ShareTokenRow.scope` already exists with `server_default="read"`, and #779
shipped `db/share_guard.py` with:

```python
_WRITE_CAPABLE_SCOPES: frozenset[str] = frozenset()
```

and a docstring saying a later write-capable scope joins that set while the
capability table decides what it may write. This design takes that seam rather
than cutting a new one:

1. `"comment"` joins `_WRITE_CAPABLE_SCOPES`.
2. `_share_alias` gains a **second, separate** allowlist, `_SHARE_WRITE_PATH_RE`,
   with its own method set (`POST`, `DELETE`). `_SHARE_PATH_RE` keeps its
   GET-only meaning and its docstring stays true; the two are never merged.
3. A request matching the write allowlist is admitted only when the resolved
   token's scope is in `_WRITE_CAPABLE_SCOPES`. Everything else is the existing
   uniform 404.
4. The #756 capability table carries `comment.write`, so the SPA gates the
   compose box on the same fact the server enforces.

Two properties fall out for free and are worth stating as guarantees:

- **Existing links cannot post.** Every token minted before this ships carries
  `scope="read"`, so turning the feature on cannot retroactively open a link
  that is already in someone's inbox. Comment capability is chosen at mint
  time, per link.
- **Fail-closed is preserved.** `share_request_is_read_only()` returns True for
  any scope outside the write set, including unknown or mistyped values. Adding
  one member does not change that.

Rejected alternatives:

- **A separate anonymous comments API outside `/api/share/`.** Keeps the share
  surface literally GET-only, but creates a second route that resolves share
  tokens. CLAUDE.md is explicit that token resolution and owner impersonation
  must have exactly one implementation. It also loses the per-link opt-in,
  which would have to be reinvented rather than inherited from `scope`.
- **Comments as a `state_doc`.** No new table, but `state_docs` are
  load-whole / save-whole under optimistic concurrency: concurrent anonymous
  appends to one document produce 409 storms and lost comments. They would also
  sync to desktop mirrors as though they were owner state (#757). Wrong shape.

## 2. Data model

One new table, `match_comments`, under the standard tenant RLS policy.

| Column | Type | Note |
| --- | --- | --- |
| `id` | ULID PK | Sortable by creation, so thread order needs no extra column |
| `user_id` | FK `users.id` CASCADE | **The match owner, not the author.** RLS tenant column |
| `match_id` | str | From the resolved token row, never the client. Paired with `user_id` the same way `state_docs` pairs them, rather than a single-column FK |
| `slug` | str | Shooter whose run is being commented on |
| `stage_number` | int | 1-based |
| `anchor_t` | float | Seconds after the beep, 2 decimals. **Always set** |
| `anchor_kind` | str | `"time"` or `"shot"` |
| `anchor_shot_id` | str or None | Set only when `anchor_kind == "shot"` |
| `author_kind` | str | `"account"` or `"handle"` |
| `author_user_id` | FK `users.id` or None | Set when the commenter was signed in |
| `author_handle` | str | Display string, denormalized at write time |
| `author_key_hash` | str | Hash of the caller's opaque per-browser key |
| `share_token_id` | FK `share_tokens.id` | Which link the comment arrived through |
| `body` | str | Plain text, 1000 characters maximum |
| `created_at` | datetime | Server clock |
| `deleted_at` | datetime or None | Soft delete |

Four choices that need defending:

**`anchor_t` is always set, even for a shot anchor.** The shot id is a label;
`t` is the truth. This applies the moment spec's own rule - the stored anchor
never lies, the label can. A re-detect, a renumber, or a recycled `cand-<n>`
(#842) therefore degrades a shot-anchored comment to a plain time pin. It is
never hidden and never silently re-attaches to a different shot, which is the
failure that would actually mislead a reader.

**`user_id` is the owner, not the author.** Counterintuitive, and correct: the
comment is about the owner's footage, it dies with the owner's match through
the existing CASCADE, and an anonymous author has no account for it to belong
to. Tenancy stays exactly what it is in every other table. `author_user_id` is
the separate, nullable column that records a signed-in author.

**`author_key_hash` is convenience, not a security boundary.** The client
generates a random opaque key once and keeps it in localStorage; it exists so a
commenter can delete their own comment without an account. Anyone can mint one,
so it must never gate anything whose exposure matters - only self-delete.

**`share_token_id` is the moderation primitive.** It makes "remove everything
that came in through the link I sent to that guy" a single query, and it
composes with revocation (#788).

## 3. Attribution: the handle is server-generated

The tempting build is a client-side wordlist that posts a chosen handle. That
is wrong: if the client supplies `author_handle`, anyone with `curl` can sign a
comment with the match owner's name, which is exactly the impersonation the
design set out to avoid.

**Invariant: `author_handle` is never client-supplied.** localStorage holds only
the opaque `author_key`. The server derives the display name:

- Signed-in commenter: `users.display_name`, read server-side.
- Anonymous commenter: `HMAC(server_secret, author_key)` indexed into a curated
  IPSC wordlist - `adjective x noun x squad-number`, giving names like
  "Prone Popper 47" or "Steady Comstock 12". Roughly 160k combinations
  (40 x 40 x 100), stable per browser, with no stored mapping to reverse.

A `POST` body carrying `author_handle`, `author_user_id`, `user_id` or
`match_id` has every one of those fields ignored. The request model does not
declare them.

## 4. Anchoring in practice

The compose box reads the current playhead and converts to stage-relative
seconds through the same path the moment feature uses. If the playhead is
within a tolerance of a shot in that shooter's shot table, the box offers
"shot 7" and stores `anchor_kind="shot"` with both `anchor_shot_id` and
`anchor_t`; otherwise it offers "4.32 s" and stores `anchor_kind="time"`. One
persistence shape, two labels.

Tolerance: 0.12 s, chosen to sit below the low end of the Production Optics
split range the project treats as typical (0.15-0.40 s), so the snap can never
straddle two adjacent shots in a fast string.

Rendering reuses the scrub-bar moment marker from the deep-link work: distinct
shape plus an accessible label, never colour-only. Clicking a comment seeks to
its anchor; the existing "copy link at moment" action is unchanged.

## 5. Moderation

Owner delete ships with v1 as a release condition. An unauthenticated write on
a URL that can be forwarded to anyone, with no removal path, is the one failure
mode that cannot be walked back after the fact.

The controls, in order of how much they matter:

1. **Per-link opt-in.** `scope` is chosen at mint time. A link is either
   read-only or comment-capable for its whole life; there is no toggle that
   changes an outstanding link's capability.
2. **Owner soft delete**, one comment at a time.
3. **Bulk delete by `share_token_id`** - retire a link and everything that came
   through it.
4. **Bulk delete by `author_key_hash`** - remove one persistent nuisance
   without touching the rest of the thread.
5. **Rate limits**: per token and per source, plus a per-stage comment cap. The
   1000-character body cap is enforced by the request model.

Deletion is soft: the row keeps `deleted_at` and stops being served to anyone,
including the owner's normal list. An owner who bulk-deletes by token and
regrets it can be recovered from by hand. Nothing purges soft-deleted rows in
v1; if that becomes a size problem it is a retention decision to make then, not
a reason to hard-delete now.

## 6. Read path and its one deliberate consequence

Reading the thread is added to `_SHARE_PATH_RE` as
`shooters/[^/]+/stages/\d+/comments`, which means **a plain `read`-scoped link
can see the thread but cannot post to it.** Look-don't-touch on links that are
already in the wild is the intended behaviour: the conversation is part of what
the page shows, and only participation is gated.

## 7. API surface

Anonymous, through the token (the share alias supplies match id and tenant):

- `GET    /api/share/{token}/shooters/{slug}/stages/{n}/comments` - any scope
- `POST   /api/share/{token}/shooters/{slug}/stages/{n}/comments` - `comment` scope
- `DELETE /api/share/{token}/shooters/{slug}/stages/{n}/comments/{id}` -
  `comment` scope, author's own only, matched on `author_key_hash`

Owner-side, ordinary authenticated routes under the existing match alias:

- `GET    /api/shooters/{slug}/stages/{n}/comments`
- `DELETE /api/shooters/{slug}/stages/{n}/comments/{id}`
- `DELETE /api/match/comments?share_token_id=...`
- `DELETE /api/match/comments?author_key_hash=...`

(The `match/...` prefix rather than a literal `matches/{match_id}/...` follows
`/api/match/shares`: the alias middleware supplies the match id, so no route
takes it from the client.)

The caller's `author_key` travels as a request header on `GET` as well as on
`POST` and `DELETE` - the `GET` needs it to compute `mine`. It is optional on
`GET`: a caller that sends none simply gets `mine: false` everywhere, which is
the correct answer for a first-time reader.

A comment payload returns `id`, `anchor_t`, `anchor_kind`, `anchor_shot_id`,
`author_handle`, `author_kind`, `body`, `created_at`, and `mine` (computed
against the caller's `author_key`). It never returns `author_key_hash`,
`author_user_id`, `user_id` or `share_token_id` to an anonymous caller; the
owner-side response adds `share_token_id` and `author_key_hash` because the
bulk-delete actions need them.

## 8. Error handling

| Failure | Behaviour |
| --- | --- |
| `POST` through a `read`-scoped token | Uniform 404 - identical to an unknown token, so the write surface is not discoverable |
| `POST`/`DELETE` to a path outside the write allowlist | Uniform 404 |
| Body empty, over the cap, or rate limited | 422 / 429 with a stable `detail.code`; the caller holds a valid write token, so an honest error is right |
| `anchor_shot_id` no longer resolves | Renders as a time pin at `anchor_t` |
| `author_key` missing or unmatched on `DELETE` | 404 |
| Comment list fails to load | Player and page keep working; the thread area shows an inline retry |
| Match row gone behind a live token | Existing uniform-404 seam in `_share_alias` applies unchanged |

## 9. Testing

The adversarial cases carry the weight here. The happy path either works or
obviously does not; the containment properties fail silently.

Pytest:

- A `read`-scoped token gets the uniform 404 on `POST`, and the response is
  byte-identical to the unknown-token 404.
- A `comment`-scoped token gets 404 on every path outside `_SHARE_WRITE_PATH_RE`
  and on every method outside its set.
- `share_request_is_read_only()` still returns True for `read` scope after
  `_WRITE_CAPABLE_SCOPES` gains a member, and `ShareReadOnlyError` still fires
  on a read-scoped write attempt. This guards against the set change having
  weakened the check for everyone.
- Match-id substitution and cross-match slug substitution both 404.
- A `POST` body carrying `author_handle`, `author_user_id`, `user_id` or
  `match_id` has all four ignored; the stored row matches the token row.
- Anonymous list responses omit `author_key_hash` and `share_token_id`.
- Handle derivation is stable for a fixed `author_key` and differs across keys.
- Owner bulk delete by token and by author key affects exactly the intended rows.

Vitest:

- Anchor snapping picks shot versus time on both sides of the 0.12 s tolerance.
- A comment whose `anchor_shot_id` does not resolve renders as a time pin.
- The compose box is absent when `comment.write` is missing from the capability
  set.

Manual: post through a comment-scoped link on staging, then revoke it and
confirm both that posting stops and that the thread still reads correctly for
an unrevoked link.

## Out of scope

- Compare (multi-shooter grid) commenting.
- Replies, threading, reactions, editing.
- Notifications of any kind to the owner or to commenters.
- Purging soft-deleted comments. A match delete hard-deletes its whole thread
  (nothing cascades from the registry row, so that step is explicit); routine
  retention of soft-deleted rows is a later decision.
- Bookmarks, and the limited-account tier.
- Re-attributing anonymous comments to an account created later.
