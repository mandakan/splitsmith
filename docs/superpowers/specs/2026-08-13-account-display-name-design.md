# Account display name and author codes - design

Date: 2026-08-13
Status: approved pending review
Issue: #867. Follows #866.
Surface: a new `/account` page in the hosted SPA, plus the author line in the
comment thread on `/share/{token}/results/{slug}/{stage}`.

## Problem

#866 shipped comment attribution with two branches. A signed-in visitor
comments under their account `display_name`; everyone else gets a
server-derived IPSC handle like "Prone Popper 47". The second branch works.
The first cannot be reached.

**Nothing in the codebase writes `users.display_name`.** Magic-link signup
sets `email` and stops there. There is no profile route, no settings field,
and no scoreboard-import path that populates it. The column is `NULL` for
every real account, so every signed-in visitor falls through to the
pseudonym and `author_kind="account"` is dead code in production. The tests
covering it set the column directly, which is why task-scoped review did not
catch it.

The column is not comment-only, which sharpens the case for filling it in
rather than deleting the branch. `AccountChip.tsx` and
`HostedAccountChip.tsx` both render `display_name ?? email`, so today every
hosted user's label in the global bar is their raw email address.

## Decisions made during brainstorming

- **Add the field.** Option 1 of the three in #867. Deriving from
  `users.scoreboard_identity` was rejected: it covers only users who ran the
  import, and it publishes under a share link a name that was collected for
  scoring, which is a different consent. Removing the branch was rejected
  because the column has two other live consumers.
- **The fallback stays.** A blank name publishes a server-derived handle,
  never an empty string. #866 pins this with a test and it does not move.
- **No uniqueness constraint on the name, no account badge.** A
  case-insensitive unique index costs a migration and a 409 path and still
  does not stop first-come impersonation of someone who has no account.
- **The disambiguator is a per-author code, not a constraint on names.**
  Added during brainstorming: a stable public identifier next to the name,
  so two authors using the same or a similar name are distinguishable.
- **`/account` is a routed page, not a dialog**, and desktop-token
  management moves onto it.
- **No public author info page.** The richer per-author detail is owner-only
  and match-scoped.

## 1. Storage and validation

`users.display_name` already exists as `Mapped[str | None]`, nullable
(`db/models.py`). No migration.

New pure module `src/splitsmith/display_name.py`:

```python
def normalize_display_name(raw: str | None) -> str | None: ...
```

- NFC-normalize, strip, collapse internal whitespace runs to a single space
- empty after trimming returns `None`, never `""`
- raise `ValueError` on any C0 or C1 control codepoint, including newlines
- raise `ValueError` above 60 characters, measured after normalizing

Unicode names are allowed. The route turns `ValueError` into a 422.

The blank-to-`None` rule is the one that carries weight beyond this module:
it is what keeps #866's fallback invariant true. If a blank name stored `""`
the attribution branch would publish an empty author, and the branch tests
`display_name.strip()` rather than `is not None` precisely because it did not
trust that. Storing `None` makes both checks agree.

A pure function rather than a Pydantic validator body so the rules are
testable without constructing a request, per the project's "pure functions
where possible" rule.

## 2. API

`PATCH /api/me`, body `{"display_name": str | None}`, returning the updated
`User` -- the same shape `GET /api/me` returns, with `is_admin` still derived
from `state.admin_emails` at request time rather than read from the row.

- **Hosted only.** Local mode 404s, matching the convention the magic-link
  routes already follow. `LoopbackAuth`'s sentinel user has no database row
  to write, and inventing one would give the desktop app an account concept
  it does not have.
- **A sync-scoped desktop token never reaches it.** `_auth_gate` confines
  `token_scope == "sync"` to `/api/sync/*` plus its own sign-out route, so
  the containment is inherited, not new. This gets a test rather than new
  code -- #866 established that a scope-limited desktop token is refused a
  name, and that property must not regress silently.
- A legacy `"full"` token is admitted, consistent with `GET /api/me`.

The write goes through `PostgresProfileStore` in a new `db/profile.py`,
following `db/scoreboard_identity.py`: constructed per request with a
non-empty `user_id` that it fails loudly on, and every statement filtered on
`User.id == self._user_id`. That module's docstring calls out the
multi-tenant invariant and the requirement that a new method comes with an
isolation test; this store inherits both.

## 3. The `/account` page

New route under `RootLayout`, alongside `admin/workers` and
`desktop/approve` -- server-wide surfaces that are not project-scoped. In
local mode it redirects to `/pick`; the chip that links to it does not render
there, so the route is only reachable by typing the URL.

Contents:

- **Display name** -- editable, with Save. Client-side it mirrors the
  server's cap for immediate feedback, but the server is the authority and a
  422 renders as an inline error.
- **Account email** -- read-only.
- **Desktop tokens** -- the list, mint, and revoke flow, moved here.

### Moving desktop tokens

`DesktopTokensDialog.tsx` (365 lines) loses its Portal, `useDialogFocus`
trap, and `onClose` prop, and becomes `DesktopTokensSection.tsx` rendered
inline. The dialog file is deleted and its 107-line test file migrates to the
section.

`AccountChip` is the only component that opened it. The three other mentions
in the codebase -- `SyncCard.tsx`, `SyncSettingsDialog.tsx`,
`DeviceLoginDialog.tsx` -- are comments citing it as the modal-skeleton
precedent, so nothing else breaks. Those comments should point at
`SyncSettingsDialog` or `ShareDialog` once the file is gone; a comment
referencing a deleted file is a small trap for the next reader.

The accessibility properties that were dialog-specific go away with the
dialog, but the ones that were not are not optional: the one-time token
reveal keeps its `aria-live` region, the "you will not see this again"
warning stays text rather than icon or colour, copy feedback stays a label
swap, and revoked entries keep an explicit "Revoked" text label.

`AccountChip` swaps its `KeyRound` icon button for a `Link` to `/account`,
the same shape as the existing admin `Server` icon link. The control count on
the chip is unchanged, so the phone-width budget documented in its header
comment still holds -- that comment records a measured 326 -> 632px overflow
and should not be invalidated by this change.

## 4. Author codes

A stable public identifier for each comment author, so two authors posting
under the same or a similar name are distinguishable.

### Derivation

`HMAC(handle_secret(), b"author-code:" + key)` truncated to a 6-character
Crockford base32 code -- that alphabet already omits `I`, `L`, `O` and `U`,
so a code read aloud or copied by eye does not collide with a neighbour.
`key` is:

- `users.id` for an account author
- `author_key_hash` for a pseudonymous author

Lives in `comment_identity.py` next to `derive_handle`, reusing
`handle_secret()` with a domain-separation prefix rather than introducing a
second env var and a second hosted-config step.

**Never the raw `users.id`.** It is the internal foreign key used for project
ownership and ACL rows, and a ULID encodes its creation time, so publishing
it on an anonymous surface leaks account age for free.

**Pseudonymous authors get a code too.** Without it the presence of a code is
itself the account-versus-pseudonym tell, and an account holder could set
their name to "Prone Popper 47" and sit next to the real one uncoded. It
leaks no new linkage: `derive_handle` is already deterministic per browser
key, so two comments from one browser were always linkable by their handle.

### Storage

New nullable `author_code` column on `match_comments`, written at
comment-creation time alongside `author_handle`. New Alembic revision with
`down_revision = "b4d8f1a90c27"`.

Denormalized at write time for the same reason `author_handle` is. #866's
`comment_identity` docstring promises that rotating the handle secret is
safe, and that promise holds only because the name is frozen on the row. A
code computed at read time would break it -- rotation would silently
re-identify every historical author, which is worse than a rename because the
code is the thing readers are being asked to trust.

No backfill. #866 landed after the 0.29.0 release and is unreleased, so
production has no comment rows. `to_out` computes the code at read time when
the column is `NULL`, which covers dev and staging rows written before this
change without making an Alembic migration depend on the HMAC secret being
correctly set in the migration environment -- if it were not, the migration
would write wrong codes with no error.

### Wire format

`author_code: str` on `CommentOut`, so visitors receive it as well as the
owner. The tooltip and the ambiguity check both need it. `CommentOut` today
carries no author identifier at all, only `author_handle` as a bare string,
so this is a new field rather than a reshaping of an existing one.

`author_user_id` stays off the wire. It is the internal FK and the code
supersedes it for every reader-facing purpose.

The containment rules #866 established are unchanged: `CommentOwnerOut`
remains a separate type carrying `share_token_id` and `author_key_hash`, and
the response-model type declaration at each call site remains the boundary
that must not regress. `author_code` belongs on the base type, not the owner
subtype.

## 5. Rendering: surface the code when names collide

Every author element carries `data-author-code` and a `title` tooltip,
always, with no visual change. The code additionally renders as visible muted
text next to the name when two distinct codes in the thread normalize to the
same name.

The author line already renders in a mono uppercase style
(`CommentPanel.tsx`), so a muted code appends without new visual vocabulary.

### The similarity rule

A pure function in a lib file, unit-tested, applied to `author_handle`:
NFKC, casefold, strip diacritics, collapse whitespace, drop non-alphanumeric
characters. Two authors are ambiguous when their normalized forms are equal
and their codes differ.

NFKC here, NFC in section 1, and the difference is deliberate. Storage
preserves the name the user typed, so it uses the conservative form;
comparison is trying to defeat someone choosing a compatibility variant on
purpose, so it folds them. The two normalizers are separate functions and
neither should be reused for the other's job.

This catches `Mathias Axell` against `mathias  axell`, `Måthias Axell`, and
`Mathias-Axell`. It does **not** catch `Mathlas Axell`: there is no edit
distance and no homoglyph table. That limit is deliberate and should be
stated in the code rather than implied away -- the always-present tooltip is
what covers everything the rule misses, and a rule that claimed to be
exhaustive would discourage the hover that actually finds the rest.

Runs client-side over the stage's comment list. No server involvement, no new
request.

## 6. Owner-only author detail

The panel already branches on `canModerate` for per-comment delete. Clicking
a code in the owner's view expands, for that code:

- kind, account or pseudonym
- first comment date
- comment count in this match
- **every distinct `author_handle` the code has posted under**

The name history is the real impersonation signal. An account that renamed
itself to match another commenter shows two names under one code, which no
single comment can reveal.

Match-scoped, so it needs one owner-gated endpoint returning per-code
aggregates for the match. The cheaper alternative -- deriving the detail
client-side from the stage list already in hand -- was rejected because it
would silently under-report anything posted on another stage, and a count
that is quietly wrong is worse than no count.

Visitors get the code and the tooltip only. There is no public `/u/{code}`
route and no cross-match aggregation: revealing that an author commented on
other people's share links is a disclosure they never opted into, and it is
exactly the kind of aggregation an anonymous surface should not perform.

## 7. Known limitation, documented not fixed

`HostedAccountInfo` is cached in `config.yaml` at device-link time and, as
its docstring says, never fetched live -- a sync-scoped token cannot read
`/api/me`. So the desktop app's `HostedAccountChip` keeps showing whatever
was captured when the device was linked, and setting a display name later
will not reach it until the next link.

Out of scope. It gets a comment where the cache is populated, not a fix.

## 8. Testing

The test that decides whether #867 is actually closed: a signed-in viewer
`PATCH`es a display name, then posts a comment through a comment-scoped share
link and receives `author_kind="account"` with that name and a matching
`author_code`. End to end, with nothing setting `users.display_name`
directly. Every existing test of that branch sets the column by hand, which
is the defect this issue reports.

Python:

- `normalize_display_name` units: blank to `None`, whitespace collapse, NFC,
  the 60-character boundary either side, control characters, newline
- `PATCH /api/me` round-trip, and clearing a name back to `NULL`
- 422 on each rejection, with the invalid value never persisted
- 404 in local mode
- a sync-scoped desktop token is refused
- `PostgresProfileStore` isolation: a write for one user id never touches
  another user's row
- `author_code` determinism, and stability across a handle-secret rotation
  for a row that already has the column written
- the read-time fallback produces the same code the write path would have
- `author_code` derived from `author_key_hash` for pseudonymous authors
- the owner aggregate endpoint: distinct names under one code, count scoped
  to the match, and a non-owner refused

Vitest:

- `/account` renders, saves, and surfaces a 422 inline
- `/account` redirects to `/pick` in local mode
- the migrated `DesktopTokensSection` keeps its one-time reveal, `aria-live`
  region, copy-label swap, and "Revoked" text label
- `AccountChip` links to `/account` and no longer opens a dialog
- the similarity normalizer: the four cases it catches and the one it does
  not
- `CommentPanel` renders `data-author-code` and the tooltip always, and
  visible codes only for colliding names

Every new test is checked against the pre-change code before it counts. The
project's review practice records that several tests on an earlier branch
would have passed against the bug they claimed to cover; deleting the fix and
watching the test fail is the only proof that matters here, and it applies
with particular force to the end-to-end attribution test, which is the whole
point of the issue.

## 9. Out of scope

- Any new signup path. The limited-account tier stays the separate product
  decision #866 declared it.
- A public author info page.
- Cross-match author aggregation.
- Refreshing the desktop app's cached account info.
- Uniqueness or moderation of display names.
