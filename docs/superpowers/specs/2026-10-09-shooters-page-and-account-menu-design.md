# Shooters page and the account menu

Date: 2026-10-09. Status: approved in conversation, awaiting spec review.

## Why

A shooter's look (logo, colour, club) can only be edited from the Footage
page's shooter sheet, which is the wrong home: it is about a person, not
footage, and it follows them from match to match through the shooter book.
Everything about *you* is scattered too: on the desktop the top-right pill
is only the splitsmith.app link, "You" is a button on the Matches header and
nowhere else, and splitsmith.app has its own `/account`. The user expected
all of it behind the account pill.

## Decisions (from the conversation)

- A **Shooters page** for the account, and a **shortcut from the shooter
  pills** in a match. Both open the same sheet.
- **No per-match overrides** for now: an edit always goes to the shooter
  book.
- The page lists **the shooters you have filmed** (every shooter in your
  matches), not every name in imported results.
- The **account pill** is the home for "me and my stuff", on every page,
  in both modes.

## Design

### 1. The account menu

A pill at the top right of `GlobalBar` on every page, local and hosted:
your initials, or your logo once set (the "You" identity: the shooter book
entry of `ScoreboardIdentity.shooter_id`, else the account brand's logo).
Its menu:

| Item | Where |
|---|---|
| You | `/you`: your shooter profile and your brand |
| Shooters | `/shooters` (new) |
| Branding | `/you#brand` (the brand section of You) |
| splitsmith.app | local: "Signed in as ..." + Sign out, or Sign in (absorbs `HostedAccountChip`); hosted: Account (`/account`) and Sign out |

`HostedAccountChip`'s behaviour (link state, revoke warning, the device
login dialog) moves into the menu unchanged; the chip itself goes. The
mobile bar keeps a compact pill with the same menu. The Matches page header
keeps its You button and gains **Shooters** beside it.

### 2. The Shooters page (`/shooters`)

Account level, outside any match (like `/you`), local and hosted.

- **Rows**: everyone you have filmed, by SSI shooter id: name (the most
  recent match's), club, colour swatch, avatar with their logo, and the
  number of matches they are in. You are pinned first, marked "You".
- **Shooters without an SSI id** cannot be in the book (it is keyed by that
  id). They are listed after the rest, once per match, as "Set in
  <match>", with their match's look, read-only, and a line saying their
  look can be set once they are linked to the scoreboard.
- **Edit** opens the shooter sheet (section 4) for that SSI id.
- Sorted: You, then by most recent match, then by name.

Data: `GET /api/me/shooters` returns the rows. It reuses the backfill's
match enumeration (`account_backfill.local_backfill_source` reads
`projects.json` locally; `hosted_backfill_source` reads the account's
project docs), widened to every shooter project rather than only those with
a look set, and joins the book. Each row: `shooter_id | null`, `name`,
`club`, `accent`, `logo_url | null`, `match_count`, `last_match_at`,
`you: bool`, and for a row without an id, `match_id` and `slug`. No new
store; nothing is written by listing.

### 3. Shooter pills in a match

The header's shooter pills (`EDITING ...`) get a small menu: **Open**
(switch to this shooter, today's click) and **Edit look**, which opens the
same sheet for that shooter's SSI id (or explains why it cannot, for a
shooter without one).

### 4. The shooter sheet

`components/footage/IdentitySheet` moves to `components/shooters/` and
edits **the book entry only** (`/api/me/shooter-book/{id}` and its logo
routes, which exist). The match scope (`scope="match"`, "keep this edit in
this match") leaves the UI. Inside the sheet, a small live title-card
preview of the shooter's look (the export-preview route's demo backdrop,
with logo spots on).

The Footage page loses identity editing; adding a shooter stays.

### 5. The book wins

`identity_media.identity_source` flips: **the book's entry when it has one
for the shooter's SSI id; else the match's own record; else nothing.** An
old per-match record therefore never silently overrides what the Shooters
page shows, and stays the look for a shooter the book has no entry for (no
SSI id, or never edited). `effective_identity` follows. Every caller
already goes through these two functions (renders, previews, the roster,
Compare), so the flip is one place.

This can change an existing render: a shooter whose match record differs
from their book entry now draws the book's look. The match records are left
on disk untouched; nothing is migrated or deleted.

The server routes keep `scope="match"` for now (an older client may send
it); the SPA stops sending it.

## Not in this work

Squads or teams, per-match overrides, a standalone Looks page, and anything
that writes a match's identity record.

## Testing

- `identity_source` / `effective_identity`: the book wins over a match
  record; a match record still applies with no book entry; nothing set is
  `none` (pin the flip with a test that fails on the old order).
- `GET /api/me/shooters`: local and hosted fixtures; a shooter in two
  matches is one row with `match_count` 2 and the newest name; a shooter
  without an id is a row per match; You first; nothing written.
- SPA: the account menu's items per mode (local signed in / signed out,
  hosted); the Shooters page rows and order (pure `lib/shooters.ts`); the
  pill menu's Edit look; the sheet writes the book only.
- Browser check on a demo match: the pill menu, the page, the sheet with
  its preview, and a render picking up a book edit over an old match record.
