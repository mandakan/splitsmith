# You, your brand and the shooter book

Status: draft for review, 2026-10-08. Phase 1 of the account identity work;
public splitsmith.app profiles are a later spec (see the end).

## Why

Today every match starts from nothing: a shooter's accent, club line and logo
live in that match (`<shooter>/identity/`), and your own brand lives on a Look.
You set yourself up again in every match, and every Look you make needs the
brand copied in. Phase 1 makes three things set-once:

1. **You**: which shooter in any match is you.
2. **Your brand**: the maker's mark (logo and line) on every video you render.
3. **The shooter book**: a shooter's identity follows them from match to match.

## The key: the SSI shooter id

Every shooter project already stores `selected_shooter_id`, the SSI shooter
index id, picked when the scoreboard is linked. It is globally stable across
matches, which makes it the one key for all three. Nothing new is invented.

A shooter with no SSI id (no scoreboard linked, a placeholder) gets none of
this: no "you", no carry-over. Matching by name is ruled out: two shooters with
the same name, or one spelled two ways, would put the wrong logo on a stranger.

## What the account holds

`AccountIdentity`, one per account:

- `shooter_id: int | None`: your SSI shooter id.
- `identity: ShooterIdentity`: your accent, club line and logo (the existing
  model; the logo a content-named file, the existing checks).
- `brand: Brand | None`: your maker brand, a logo (content-named, the
  `look_brand` checks) and a line, the same shape as `LookBrand`.

Set on the Account page, in a new "You" section:

- **Shooter**: search the shooter index by name (the existing `find_shooter`,
  through an account-level route not tied to a match) and pick yourself; or,
  from any match's roster, "This is me" on a shooter takes their SSI id.
- **Your look as a shooter**: accent, club line, logo (the same controls as a
  shooter's identity sheet).
- **Your brand**: logo and line.

## Your brand on the video

On every video you render (title page and closing card, the slots that draw a
brand today), unless:

- the chosen Look carries its own brand: the Look wins, so a club Look still
  shows the club;
- the export turns it off: `account_brand: bool = True` on every request body,
  `ExportPresetBody`, `MatchExportRequestData`, `CardOptions`, the preview
  request and both CLIs (`--no-account-brand`), shown as a checkbox under
  Details beside "Made with splitsmith".

The request layer resolves it, as it does the event logo: the account's brand
becomes `MatchTitle.brand` (a resolved logo path and line), and
`look_brand.brand_json` returns the Look's brand when it has one, else the
title's. Renderers never read the account. A render with no account brand, or
with the switch off, has the context, digest and pixels it has today.

## You in a match

The shooter whose `selected_shooter_id` equals the account's `shooter_id` is
you. The roster shows a small "You" mark; nothing else changes on its own. Your
account identity is your entry in the shooter book (below), so it applies to
you by the same rule as everyone else's.

## The shooter book

A per-account store of identities keyed by SSI shooter id:
`ShooterBookEntry(shooter_id, identity: ShooterIdentity, updated_at, label)`
(the label is the name last seen, for listing only, never for matching).

**Writes.** Setting a shooter's identity in a match (the existing identity
routes) also writes the book entry for their SSI id, unless the edit is marked
"Only this match". Your own entry is also written from the Account page.

**Reads.** When a render or a preview resolves a shooter's identity
(`identity_media.resolved_identity_for` and `grid_identities`, the one place
every export already goes through):

1. the match's own identity, when it has any field set, and it is the whole
   record (a match that sets only an accent does not borrow the book's logo:
   one record is easier to reason about than a per-field merge, and with
   automatic writes the two agree unless "Only this match" was used);
2. else the book entry for the shooter's SSI id;
3. else nothing, which renders exactly as today.

The book is read at render time, so a match never copies it: the next export
of an old match picks up the current entry. A video already rendered is a file
and keeps what it had.

**The identity sheet** says where a shooter's look comes from ("From your
shooter book", "Set for this match"), offers "Only this match" when editing,
and "Use shooter book" to drop a match-local record.

**Backfill.** The first time the book is opened (locally: on first start
after the upgrade), it is seeded from existing matches: every shooter with an
SSI id and an identity, the most recently updated match winning. Logged, never
repeated, and nothing is written to a match.

## Storage

Per account, never a `state_docs` kind (a per-match kind would enter the sync
manifest; the export presets follow the same rule):

- **Local**: `~/.splitsmith/account/identity.json` and
  `~/.splitsmith/account/shooter_book.json`, the files under
  `~/.splitsmith/account/files/` (content-named; symlinks refused, as for
  fonts and brand logos).
- **Hosted**: an `account_identity` row and a `shooter_book` table keyed by
  `(user_id, shooter_id)`; the files in R2 under the user's own prefix
  (`users/<user_id>/account/<name>`), mirrored to local disk at render time
  like a shooter logo (`ensure_local_*`), a missing file a card without it.
  This is the first per-user file on hosted (Looks refuse a brand logo for want
  of one), so the key rule and the delete route are new and narrow: only
  content names under that prefix, only through these routes.

**Desktop and hosted do not sync the book or the account identity in phase 1**,
as export presets do not. Matches keep syncing as they do; a match identity
edited on one side reaches the other with the match, and that side's book picks
it up the next time someone edits it there. Syncing the account itself is a
later step, decided with public profiles.

## Surfaces

- Account page: the "You" section (shooter, your look, your brand).
- Roster (Footage page): the "You" mark; "This is me" on a shooter's menu.
- Identity sheet: the source line, "Only this match", "Use shooter book".
- Export page, Details: "Your brand" checkbox; remembered in presets.
- `GET/PUT /api/me/identity`, `POST/DELETE /api/me/identity/{logo,brand-logo}`,
  `GET /api/me/shooter-book`, `DELETE /api/me/shooter-book/{shooter_id}`,
  `GET /api/me/shooter-search?q=`.
- CLIs: `--no-account-brand` on `match export` and `compare export`; the book
  and the account identity apply to CLI renders too, read from the local files.
- What's new: one entry.

## Security

- Uploads reuse the existing sniffing and caps (`sniff_logo`, `LOGO_MAX_SIDE`,
  2 MB); the client's filename is never read.
- A book entry is only ever written by its owner's own edits; no route accepts
  another account's entry, and hosted rows are scoped by the authenticated
  user. The share surface never reads the book or the account identity: a
  share shows the videos and the match's own identities, as today.
- Files under the user's R2 prefix are reachable only through the owner's
  routes; nothing on the share GET allowlist changes.

## Tests

- Resolution order, both renderers and the preview: match record, then the
  book, then nothing; "Only this match" keeps the book unchanged; a shooter
  without an SSI id never reads the book.
- The default path is pixel-identical: no account brand and an empty book
  render every frame as on main (both frame scripts).
- Brand precedence: Look brand over account brand; the switch off is the card
  it always was; the preview cache key moves with the brand only when drawn.
- Backfill: most recent wins, runs once, writes no match.
- Hosted: rows and files are per user; another user's id is a 404; the share
  surface cannot reach either.
- Request, preset and CLI wiring for `account_brand`.

## Later: public profiles (not this spec)

A shooter publishes their identity on splitsmith.app, keyed by their SSI id;
other users' books can fetch it. Open questions for that spec: how a claim on
an SSI id is verified (SSI has no sign-in to prove one: admin approval, or proof
through footage uploaded as that shooter), moderation and reporting for a logo
that appears in other people's videos, fetched profiles as suggestions accepted
per shooter and pinned once accepted, and syncing the account between desktop
and hosted.
