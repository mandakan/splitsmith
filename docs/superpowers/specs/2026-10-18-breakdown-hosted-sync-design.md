# Breakdown regions sync both ways (design)

Date: 2026-10-18. Status: draft for review.

## Why

Regions (``events`` on the stage audit doc) are desktop-owned. On a match
synced from the desktop, hosted refuses the events PUT (403
``read_only_mirror``), and ``sync.merge.merge_audit_doc`` keeps the
desktop's list whatever hosted holds. So reviewing a breakdown on a phone
needs the desktop's own server on the LAN with a token. With regions merged
like the stage note, the phone reviews on splitsmith.app and the desktop
picks the result up on its next sync.

## What syncs

- **``events``**, merged per region, three-way against the base.
- **``events_seeded``**: the larger of the two sides (a version number;
  seeding only moves it up).
- **Shot classes** already merge per shot (``COACH_FIELDS`` holds
  ``interval_class`` and its source); the classification step below keeps
  them consistent with the merged regions.

Nothing else changes: shots, beep, stage note, needs_attention and the
``audit_events`` union merge as today.

## The region merge

Units are regions keyed by ``id``, compared on ``{kind, start, end, source,
note}``.

| base | local | remote | result |
|---|---|---|---|
| has | has, unchanged | has, changed | remote's |
| has | has, changed | has, unchanged | local's |
| has | changed | changed, differently | newer doc wins; conflict surfaced |
| has | absent | unchanged | deleted (local deleted it) |
| has | unchanged | absent | deleted (remote deleted it) |
| has | absent | changed | conflict: the edit wins over the delete (a deletion is cheap to repeat; a lost edit is not) |
| has | changed | absent | the same, local's edit kept |
| absent | has | absent | local's (added locally) |
| absent | absent | has | remote's (added remotely) |
| absent | has | has, equal | one copy |
| absent | has | has, different | **both kept**: the same id minted twice (``next_event_id`` is max + 1 on each side). Remote's is renamed to ``next_event_id`` over the merged list; noted |

The base carries the tombstones: a region the base has and one side lacks
was deleted on that side, so no tombstone list is stored. No base (never
synced since this shipped) makes every region "added" on both sides: the
union, nothing deleted, which is the conservative failure.

**Lanes after the merge.** The merged list must pass ``validate_lanes`` (the
GET answers 422 otherwise). For each pair that overlaps in a lane, or a
second draw, the region from the older doc is dropped and a conflict names
both ids. A pair that already overlapped on one side cannot happen (every
writer validates).

**Proposals.** Seeding stays desktop-only for persistence, as today: a
hosted mirror seeds in memory on the Breakdown GET and does not store it.
When the phone saves (a Keep, a Delete, an add), the PUT stores the whole
list it holds, proposals included, and now stamps ``events_seeded`` with
the version that list was seeded at. The desktop then merges those regions
like any others. The seeder is deterministic over the same shots and
classes, so a desktop that seeded the same stage holds the same ids with the
same spans, and the merge sees one copy, or the phone's edit as a change.
A desktop on an older ``SEED_VERSION`` re-seeds only a stage whose regions
are all untouched proposals, which a phone-edited stage is not.

**Classification after the merge.** ``_apply_pull`` runs the region-aware
classifier (the ``_classify_doc`` rule: confirmed regions hint ``reload``,
manual classes untouched) on a merged doc whose confirmed regions changed,
before writing it, so a reload confirmed on the phone classifies its gap on
the desktop too. The classifier moves to a module ``sync.run`` can import
(``coach`` already holds the rule; ``_classify_doc`` is a thin wrapper).

## Hosted writes on a mirror

- ``PUT .../stages/{n}/events`` joins ``_REVIEW_ROUTES``, so a desktop
  mirror accepts it, **but only for a match whose desktop merges regions**
  (below). Otherwise it stays 403 and the phone stays read-only, so an
  older desktop can never overwrite a phone's edits on its next push.
- The capability is ``breakdown``: ``capabilities_for_origin`` grants it to a
  hosted-native match always, and to a desktop mirror when the match row
  records ``desktop_merges_events``. The SPA's read-only rule
  (``deriveStageView``) reads ``breakdown`` instead of ``edit``, and every
  Breakdown read-only path (phone and desktop) follows it.
- The desktop declares support on every sync request (a
  ``X-Splitsmith-Sync-Features: events`` header); the sync routes record it
  on the match row the first time they see it. A desktop downgraded after
  that would push over phone edits; accepted as a known edge (no
  client-version negotiation exists to catch it), noted in the release.

## Edge cases

- **A push racing a phone edit**: the push carries the version it pulled
  and 409s; the desktop pulls, merges, pushes. Unchanged from today.
- **A re-detection on the desktop** drops ``auto`` regions and
  ``events_seeded`` (``merge_detection_into``). Merged against a phone that
  kept proposals as manual: those are manual on remote, absent locally,
  present in the base as auto, so "base has, local absent, remote changed":
  the edit wins and the region comes back confirmed. That is right: the user
  confirmed it.
- **Region notes** are stripped on the share surface already; the merge
  carries them like any field.
- **The audit event log**: each PUT appends ``events_put``; the union keeps
  both sides' entries, as today.

## Tests

- ``merge_audit_doc`` cases for every table row, lane overlap resolution,
  the id collision rename, and ``events_seeded`` taking the larger.
- ``_apply_pull`` reclassifies a gap after a merged confirmed reload.
- The capability: an older desktop's mirror (no header seen) refuses the PUT
  403; after a sync with the header it accepts; hosted-native unchanged.
- An end-to-end sync test: phone keeps a proposal and adds a region on
  hosted, desktop edits another region, one sync converges both sides.

## Out of scope

Phone pairing, hosted seeding persistence, and merging ``events`` in the
compare or share payloads (they read confirmed regions as stored).
