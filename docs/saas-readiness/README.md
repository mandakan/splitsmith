# SaaS readiness

Architecture doc set for adding a hosted SaaS option to Splitsmith
without breaking the local-first desktop app.

The local Python app stays the default and stays free, private, and
fully offline. The hosted mode is additive: web app + cloud workers
behind an off-the-shelf identity provider, with audio-only upload
(Tier 2) as the default detection path. Both modes ship from the
same codebase wired through three abstractions (Storage, Auth,
ComputeBackend).

## Reading order

The set is meant to be read in order on the first pass. After that,
the docs are reference: jump to whichever covers the system you're
touching.

1. **[00 -- Context and principles](./00-context-and-principles.md)**
   Why the initiative exists, the off-the-shelf-first working
   principle, the 7-question decision record from the
   2026-05-15 ideation, the locked-in v1/v2/v3 axes, and the derived
   principles every other doc respects.

2. **[01 -- Deployment modes](./01-deployment-modes.md)**
   What local mode promises (no regression today), what hosted mode
   promises (zero install, audio-only default), and the cross-mode
   invariants that keep them structurally identical (JSON canonical,
   same ensemble, same UI components). Self-hosted is not v1 but is
   not foreclosed.

3. **[02 -- Tenancy and identity](./02-tenancy-and-identity.md)**
   The `Auth` abstraction (`LoopbackAuth` + `MagicLinkAuth`), the
   magic-link flow, sessions, the desktop-link mechanism, and the
   project ACL data model. ACL tables ship in v1 with only the owner
   role used so v2 squad sharing is a row insert, not a migration.

4. **[03 -- Storage layer](./03-storage-layer.md)**
   The fsspec-backed `Storage` abstraction
   (`FilesystemStorage` + `S3Storage`), Cloudflare R2 over AWS S3
   (the $0-egress argument), the JSON-as-canonical invariant, and
   what does and does not live in Postgres. Index rebuilds from JSON
   keep the JSON files genuinely the source of truth.

5. **[04 -- Compute backends](./04-compute-backends.md)**
   The `ComputeBackend` abstraction, the three-tier model
   (Tier 1 local / Tier 2 audio + cloud ML / Tier 3 full cloud), the
   browser-capability bench that drives auto-selection, the cloud
   worker shape (`arq` + the `compute_jobs` table), and rough cost
   sizing per tier.

6. **[05 -- Uploads and streaming](./05-uploads-and-streaming.md)**
   The tus.io upload protocol, the audio-default flow, opt-in raw
   video (v2), the `upload_sessions` table, and signed-URL streaming
   straight from R2 to the browser. Bytes never proxy through the
   API server.

7. **[06 -- API surface](./06-api-surface.md)**
   `/api/v1/` from day one, ULID resource IDs, the JSON envelope
   (`{data, meta}` or `{error, trace_id}`), idempotency keys for
   expensive POSTs, error-code semantics, observability conventions,
   and the 6-month deprecation window for v1->v2 transitions.

8. **[07 -- Sync and migration](./07-sync-and-migration.md)**
   The three onboarding personas (desktop-first, hosted-first,
   tarball-import), the v1 one-way desktop->cloud sync flow, the
   tarball import fallback, and a sketch of v2 bidirectional sync as
   a forward-compat target.

9. **[08 -- Billing and quotas](./08-billing-and-quotas.md)**
   Stripe Checkout + Customer Portal + webhooks, the entitlement
   column model with a 24h grace, the v1 stance ("display usage,
   don't enforce caps for premium"), and how v2 layers soft + hard
   limits on top.

10. **[09 -- SaaS progress](./09-saas-progress.md)**
    Status doc with v1/v2/v3 checklists and an append-only decision
    log. Items flip from `[ ]` to `[x]` as work lands.

11. **[10 -- Singleton elimination map](./10-singleton-elimination.md)**
    The process-level state that must come out before the abstractions
    in 02/03/04 can do anything useful in a multi-tenant deployment.
    Names what stays singleton on purpose (ML model cache),
    what's a violation (`AppState._bound_root`, `JobRegistry`,
    `user_config.*`), and the order of elimination.

## Working principle (one-line restate)

Use off-the-shelf frameworks, libraries, and hosted services. Don't
invent things. Every architectural decision in this set names a
specific, existing, documented library or hosted service. Doc 00 has
the bias order (hosted service > library > custom code) and the
concrete picks.

## Locked-in axes

Quick lookup -- full version with caveats lives in doc 00.

| Axis           | v1                                          | v2                                  | v3+              |
| -------------- | ------------------------------------------- | ----------------------------------- | ---------------- |
| Sync model     | Two modes, shared codebase                  | Hybrid sync (local <-> cloud)       | (only if needed) |
| Raw data       | Stays local                                 | Opt-in upload + squad-share         | Public repo      |
| Compute        | Tier 2 default                              | + Tier 3 (raw cloud)                | --               |
| Collaboration  | Single-user                                 | Squad sharing                       | Public repo      |
| Storage        | `FilesystemStorage` + `S3Storage`; JSON canonical | + Postgres index for cross-match | --               |
| Auth           | Email magic link                            | + Google OAuth                      | --               |
| Migration      | Direct sync from desktop app                | Bidirectional sync                  | --               |
| Billing        | Stripe Checkout; flat-rate premium gate     | + usage metering                    | --               |
| Database       | SQLite (auth state only)                    | Promote to Postgres                 | --               |

## What this doc set is not

- An implementation plan -- those live in GitHub issues per shipped
  doc.
- A pricing strategy -- 08 describes how billing wires up, not what
  to charge.
- A go-to-market plan -- marketing, positioning, launch timing are
  out of scope.

## Conventions

- **Numbering.** Two-digit prefixes (`00-` ... `09-`) so the docs
  sort correctly in directory listings.
- **Tone.** Prose first, tables second, code examples only when they
  pin a contract (a Python `Protocol`, a SQL DDL, a JSON shape).
- **Naming libraries and services.** Always with a link the first
  time they appear in a given doc.
- **Open questions live with their doc.** Cross-cutting open
  questions live in doc 00.
- **Status updates land in doc 09**, not in commit messages or PR
  descriptions. The decision log at the bottom of 09 is append-only.
