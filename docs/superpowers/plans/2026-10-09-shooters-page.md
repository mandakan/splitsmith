# Shooters page and account menu: implementation plan

Spec: `docs/superpowers/specs/2026-10-09-shooters-page-and-account-menu-design.md`.
Executed inline (the user asked to plan and go); one review of the whole
branch at the end.

## Rulings against the spec (made while planning)

- **The splitsmith.app chips stay in the bar** next to the new account
  pill instead of moving into its menu: `HostedAccountChip` opens its
  sign-in dialog from inside itself, and inside a popover the dialog would
  unmount with the menu. The pill holds everything about you; the chip
  stays the cloud connection. Cost if wrong: one more refactor later.
- **No title-card preview inside the shooter sheet**: the preview route is
  match- and stage-scoped, the Shooters page is account-level. The sheet
  shows the logo and accent as drawn. Cost: the user cannot see a card
  from the sheet; the Export preview with logo spots covers it.

- **Footage keeps a shortcut**: its shooter menu's "Identity" becomes
  "Edit look" and opens the same book-only sheet, instead of going away:
  the Look editor's "Add a logo" deep-links there (`?identity=<slug>`).
  The match-level `IdentitySheet` is deleted. Cost: one more entry point.

## Tasks

1. **The book wins** (`ui/identity_media.py`): `identity_source` returns
   `book` when the book has a set entry for the shooter's SSI id, else
   `match` when the match record sets anything, else `none`.
   `effective_identity` follows. Tests in `tests/test_identity_media.py`
   (or the module's existing test file): book over match; match without a
   book entry; nothing. The first must fail on the old order.

2. **The roster** (`ui/shooter_roster.py`, route in `ui/me_identity_api.py`):
   - `RosterSeen` (shooter_id | None, name, club, match_id, match_name,
     slug, updated_at, identity) from every shooter project: locally the
     recent projects' match folders (`load_match_or_legacy`), hosted the
     account's matches (`matches_store.list` + `load_docs_for_matches`).
   - `build_roster(seen, book, you_id) -> list[RosterRow]`, pure: one row
     per SSI id (newest name, match count, last match, the book's look
     when set else the newest match's), one row per match for a shooter
     without an id; order You, then newest match, then name.
   - `GET /api/me/shooters` -> `{"rows": [...]}`; `logo_url` is the book
     logo route for a book look, else the match's identity logo route
     (`/api/matches/{id}/shooters/{slug}/identity/logo`).
   - Tests: pure ordering/merging; the route on a local fixture with two
     matches.

3. **SPA data**: `api.listShooters()`, types; `lib/shooters.ts` (pure:
   row display, "Set in <match>" wording, avatar initials) with tests.

4. **Shooter sheet**: `components/shooters/ShooterSheet.tsx` from
   `IdentitySheet`, book only (`putShooterBookEntry`, book logo routes),
   no scope, no "Use shooter book". Props: `shooterId`, `name`,
   `identity`, `onChanged`. Tests moved/adapted.

5. **Shooters page** `/shooters` (`pages/Shooters.tsx`, route beside
   `you`): `PageHeader`, a `Table` of rows (avatar with logo, name, club,
   swatch, match count), Edit opens the sheet; rows without an id are
   read-only with the line from `lib/shooters`. Test with mocked api.

6. **Account menu**: `components/account/AccountMenu.tsx`, an avatar pill
   (your book logo when set, else initials of your name, else a person
   icon) with `Menu`: You, Shooters, Branding (`/you#brand`), and on
   hosted Account. In `GlobalBar` before the chips, and in the mobile
   drawer. The Matches header gains a Shooters button. Test the items per
   mode.

7. **Shooter pills**: the match header's shooter strip gets a menu per
   pill (Open, Edit look) opening the sheet for that shooter's id; a
   shooter without one shows why instead.

8. **Footage**: drop `IdentitySheet` from `pages/Ingest.tsx` and the
   identity action from `ShootersPanel`; a pointer to Shooters stays.

9. **Docs and notes**: CLAUDE.md (precedence, roster, menu), What's new
   entry, spec status. Final review, fix pass, PR, merge on green.
