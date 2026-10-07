---
name: whats-new
description: Use when a change is user-facing (a new feature, a visible improvement, a fix users would notice) and before opening its PR, to add the What's new entry in src/splitsmith/data/whats_new.json in the house style. Also use when asked to write, review or edit What's new / release notes for the app.
---

# Writing a What's new entry

The app shows `src/splitsmith/data/whats_new.json` in a sheet the first
time a user opens a version with entries they have not seen. It is the only
place most users learn a feature exists, so every user-facing PR adds its
entry in the same PR. `tests/test_whats_new.py` enforces the mechanical
rules below; the judgement ones are yours.

## Does this change need one?

Yes when someone using the app would notice: a new control, option, card,
transition, page, a faster or clearer flow, a fix to something they hit.
No for refactors, tests, CI, docs-only changes, hosted plumbing, internal
performance nobody sees, and a fix to a feature that has not shipped yet.
One entry per feature, not per PR: a later slice of the same feature edits
the existing entry rather than adding a second.

## The shape

```json
{
  "id": "look-editor",
  "date": "2026-10-07",
  "title": "Make your own Look",
  "body": "Duplicate a Look under Look, Advanced on the Export page, pick its colours and card styles, and see it on your own footage before you save.",
  "chip": "look-editor"
}
```

- **Add it at the top**: the file is newest first.
- **id**: lower-case kebab slug, unique, never reused or renamed (the seen set
  stores it).
- **date**: the day the PR merges.
- **chip** (optional): when the feature has one obvious place in the UI, name
  it here and render `<NewChip feature="...">` there
  (`components/whatsNew/WhatsNew`), and call `dismissNewChip("...")` when
  the feature is used. It shows for 60 days. Use it sparingly: one per
  feature, only where a user would otherwise walk past it.

## The voice

- **Title**: what the user can now do or get, as a short phrase, 48 characters
  at most, no full stop. "Transitions between stages", not "Added transition
  support".
- **Body**: one or two sentences, 200 characters at most, ending with a full
  stop. Say what it does and **where to find it**, in the UI's own words
  ("under Details on the Export page"). Second person or neutral; never "we".
- Name things as the UI names them; no code identifiers, file names, flags,
  issue or PR numbers, version numbers.
- Plain and concrete. No hype ("powerful", "exciting", "seamless"), no
  exclamation marks, no slop words (delve, leverage, robust, unlock,
  harness, ...).
- ASCII only: straight quotes, `...` never the ellipsis character, and no
  dash used as punctuation (no `--`, no spaced hyphen, no em or en dash);
  use a comma, a colon or two sentences.
- If a feature works differently on splitsmith.app, say so in the body only
  when it matters to someone deciding whether to try it.

## Check

```bash
uv run pytest -n0 tests/test_whats_new.py
```

Then read the entry once as a shooter who has never seen the code. If it
does not tell them where to look, rewrite it.
