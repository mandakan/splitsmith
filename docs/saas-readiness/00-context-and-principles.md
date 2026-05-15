# 00 -- Context and principles

This document captures the **why** behind the SaaS readiness initiative,
the principles that shape every other doc in this set, and the
decision record from the ideation conversation that produced this plan.

## Why this initiative exists

Splitsmith today is a local-first Python app: install via PyPI, run
`splitsmith ui`, work fully offline. That model is the right default for
the user (an IPSC competitive shooter) -- it's free, private, and
performant on the user's own hardware.

But the user wants to add a **hosted SaaS option that complements --
not replaces -- local mode**, primarily so that:

- Users without capable hardware can still run the detection workflow
- Squadmates can share matches without exporting tarballs over Dropbox
- Cross-device access (phone preview, desktop editing) becomes possible
- A curated public match repository can grow over time

The plan keeps both modes alive in the same codebase and pipes the
hosted path's needs through the same orchestration code that already
runs locally.

## Working principle (highest)

> **Use off-the-shelf frameworks and libraries. Use off-the-shelf
> hosted services where they make sense and are cost-efficient.
> Do not invent things.**

Splitsmith is a single-author project. Every component built in-house
-- and every piece of infrastructure self-hosted -- is one we have to
maintain in perpetuity. The SaaS readiness work multiplies the surface
area (auth, storage, jobs, billing, uploads, an admin/billing UI). If
we roll any of those ourselves, the project will spend more time on
plumbing than on its actual job (extracting shot splits from head-cam
footage).

Every architectural decision in this doc set names a **specific,
existing, documented library or hosted service**. If we can't find one
that fits, we revisit the decision before writing custom code or
running custom infrastructure.

The bias order is:

1. **Hosted service** -- rent, don't run. If a managed offering is
   cheap enough at our scale, we use it (Stripe for billing, Clerk
   for auth, Cloudflare R2 for storage, Sentry for errors, Resend for
   email, Neon for Postgres).
2. **Library** -- open-source, well-maintained, fits the use case.
   Use it inside our own deploy (fsspec for storage abstraction,
   SQLAlchemy for ORM, arq for job queue).
3. **Custom code** -- only when no library or service exists, and
   even then prefer a thin shim around an existing primitive.

**Cost efficiency means total cost of ownership.** A service that
costs $20/month but saves 40 hours of yearly maintenance wins over a
free self-hosted alternative. We track each pick in this doc and
revisit yearly. As scale grows, some hosted picks may flip to
self-hosted (e.g. Clerk -> Auth.js + SQLite if user count makes
Clerk's per-user pricing dominate) but only after the cost cross-over
is measured, not preemptively.

### Hosted services (rent, don't run)

| Concern | Service | Notes |
| --- | --- | --- |
| Auth (magic link) | [Clerk](https://clerk.com/) or [WorkOS](https://workos.com/) | Free tier covers <100s of users; pick after a v1 prototype with each |
| Object storage | [Cloudflare R2](https://www.cloudflare.com/products/r2/) | S3-compatible API, **$0 egress**, ~$0.015/GB stored. Massive cost advantage over S3 for media workloads. |
| Database hosting | [Neon](https://neon.tech/) (Postgres) or [Turso](https://turso.tech/) (SQLite-as-a-service) | Free tiers for v1; both scale up with usage |
| Background workers (hosted) | [Inngest](https://www.inngest.com/) or [Trigger.dev](https://trigger.dev/) | Use only if `arq` + small Redis instance turns into too much ops |
| Email (magic links + transactional) | [Resend](https://resend.com/) or [Postmark](https://postmarkapp.com/) | No SMTP self-hosting; generous free tiers |
| Billing | [Stripe Checkout](https://stripe.com/docs/payments/checkout) + [Stripe webhooks](https://stripe.com/docs/webhooks) | Stripe-hosted checkout page; we never touch card data |
| Errors / monitoring | [Sentry](https://sentry.io/) | Hosted; free tier covers our scale |
| Analytics (optional v2) | [PostHog](https://posthog.com/) or [Plausible](https://plausible.io/) | Privacy-respecting; PostHog also handles feature flags + session replay |
| Deploy target | [Fly.io](https://fly.io/) or [Railway](https://railway.app/) | Both support Python + workers + Postgres + R2 without bespoke infra |
| CDN | [Cloudflare](https://www.cloudflare.com/) | Free tier; sits in front of R2 anyway |

### Libraries (open-source, in-deploy)

| Concern | Library | Notes |
| --- | --- | --- |
| Storage abstraction | [`fsspec`](https://filesystem-spec.readthedocs.io/) | One API across filesystem / S3 / R2 / GCS / Azure |
| Database / migrations | [SQLAlchemy](https://www.sqlalchemy.org/) + [Alembic](https://alembic.sqlalchemy.org/) | Already idiomatic for FastAPI |
| Auth (self-host fallback) | [Auth.js](https://authjs.dev/) backed by SQLite | If Clerk/WorkOS pricing flips at scale |
| Job queue (in-deploy) | [arq](https://arq-docs.helpmanual.io/) (Redis-backed) or [Procrastinate](https://procrastinate.readthedocs.io/) (Postgres-backed, no Redis) | Both well-maintained, ~asyncio-native |
| ML inference | [ONNX Runtime](https://onnxruntime.ai/) + [Web variant](https://onnxruntime.ai/docs/tutorials/web/) | Same model artifact runs server-side and in-browser |
| ML export | [`sklearn-onnx`](https://onnx.ai/sklearn-onnx/) for GBDT | CLAP/PANN already PyTorch -> ONNX exportable |
| Resumable upload | [tus.io](https://tus.io/) protocol; [tus-py-server](https://github.com/tus-project) + [tus-js-client](https://github.com/tus/tus-js-client) | Proven; resumes mid-upload after network drops |
| Stripe SDK | [`stripe-python`](https://github.com/stripe/stripe-python) | First-party |
| API patterns | Standard `/api/v1/` prefix; OpenAPI auto-generated by FastAPI | Already in stack |

We re-evaluate each pick when it's time to implement, but the bias is
strong: if the off-the-shelf option does 80% of what we need, we adopt
it and live with the 20%.

## Decision record (2026-05-15 ideation)

The plan was shaped by a four-question clarifying round followed by a
three-question refinement round. Capturing the questions + answers here
so future maintainers can see *how* the plan was scoped, not just
*what* the plan says.

### Round 1 -- foundational decisions

#### Q1. Sync model: how do local and hosted relate?

**Answer: All three over time.** Start with two-modes-shared-code as
v1, build hybrid sync (local <-> cloud bidirectional) as v2, defer
cloud-first unless usage proves it's needed.

**Implication:** v1 has no sync engine. The local desktop app and the
hosted web app both run the same backend code but talk to different
storage + auth backends. The v1 abstractions (Storage, Auth,
ComputeBackend) must not foreclose v2's sync engine -- specifically the
data layer must support "this match exists in both places, reconcile
them" without a schema rewrite.

#### Q2. Raw data residency in hosted mode

**Answer: Raw uploaded, opt-in.** Default: raw stays local. Optional
per-match: upload raw to cloud to enable cross-device playback +
share-with-squadmate features. Storage interface must handle "raw is
here / raw is there / raw is unreachable" as a first-class state per
video.

#### Q3. Compute model

**Answer:** Three-tier with auto-selection:

- **Tier 1 -- local desktop:** today's Python ensemble, native speed,
  fully offline. Free.
- **Tier 2 -- audio upload + cloud ML (premium):** client extracts
  audio (~5 MB/stage instead of ~5 GB/stage for video), uploads it to
  the hosted service, server runs CLAP + PANN (the heavy models),
  returns features. Client runs the cheap pieces locally (envelope
  onset detector, GBDT). Result: minimal data leaves the user's
  machine, but heavy compute is amortised across the user base.
- **Tier 3 -- full cloud (premium):** raw video is uploaded; server
  runs the full pipeline.

Auto-selection: first-run benchmark in the browser; if the GBDT-in-
browser pass exceeds a latency threshold the UI prefers Tier 2 even
when the data is local. The user can override in settings.

**No commitment to in-browser ML beyond the cheap pieces.** CLAP/PANN
stay server-side -- they're 150 MB combined and not worth shipping to
every browser.

#### Q4. Collaboration scope

**Answer: All three over time.** v1 = single-user accounts (each user
sees their own matches only). v2 = squad-level sharing (invite
squadmates to view/edit a specific match). v3 = public match repository
(the community-curated catalog from the earlier scoreboard memory).

**Implication:** the data model has `User`, `Project`,
`ProjectMember` tables from v1 even though only the owner ACL is
exposed. Adding ACLs after-the-fact would require a painful migration.

### Round 2 -- scope and entry-point decisions

#### Q5. v1 scope

**Answer: Abstractions + minimal hosted MVP.** v1 ships:

- The three abstractions (Storage, Auth, ComputeBackend) in the
  codebase, with local-mode implementations matching today's behaviour
- A working hosted deployment with: magic-link signup, single-user
  accounts, audio upload, Tier 2 compute, S3-compatible storage,
  Stripe Checkout gating premium access, a flat tier with no usage
  metering
- A migration path: desktop app gains "Sign in to hosted" + per-match
  "Sync to cloud" actions

**Not in v1:** raw video upload, squad sharing, public match
repository, hybrid bidirectional sync, per-tenant retrain.

**Why not "abstractions only":** the abstractions are easy to get
wrong without a real consumer. Building the hosted MVP at the same
time forces the abstractions to be honest. Without the MVP we'd ship
unconstrained interfaces that turn out to not fit reality.

**Why not "full hosted launch":** every feature deferred to v2 has a
clear architectural ramp from v1 -- shipping v1 alone is meaningful and
reduces risk on v2.

#### Q6. Migration path (local -> hosted)

**Answer: Hosted login from local app -> direct sync** as the
preferred flow; tarball import as the fallback for users who don't
want to install the desktop app.

**Implication:** the desktop app gains a "Sign in to cloud" action.
Once signed in, each match in the picker has a "Sync to cloud" button.
The sync flow uploads audio + match.json + per-shooter state + audits.
Raw video sync stays opt-in per match (see Q2). This same machinery
becomes the v2 bidirectional sync engine.

#### Q7. Auth provider

**Answer: Email magic link.** No passwords, no MFA, no password reset
flows. The magic link IS the second factor. Lowest implementation
surface area; modern UX; well-supported by both hosted services
(Clerk, WorkOS, Magic) and open-source libraries (Auth.js).

**Federated providers** (Google, GitHub) are deferred until usage
proves friction.

## Locked-in decisions table

| Axis              | v1                                                          | v2                                       | v3+                |
| ----------------- | ----------------------------------------------------------- | ---------------------------------------- | ------------------ |
| Sync model        | Two modes, shared codebase                                  | Hybrid sync (local <-> cloud)            | (only if needed)   |
| Raw data          | Stays local                                                 | Opt-in upload + squad-share              | Public repo        |
| Compute           | Tier 2 default (audio up, cloud ML, GBDT in browser)        | + Tier 3 (raw cloud)                     | --                 |
| Collaboration     | Single-user                                                 | Squad sharing                            | Public repo        |
| Storage           | Pluggable (`FilesystemStorage` + `S3Storage`); JSON canonical | + Postgres index for cross-match queries | --                 |
| Auth              | Email magic link                                            | + Google OAuth                           | --                 |
| Migration         | Direct sync from desktop app                                | Bidirectional sync                       | --                 |
| Billing           | Stripe Checkout; flat-rate premium gate                     | + usage metering                         | --                 |
| Database          | SQLite for auth state only                                  | Promote to Postgres                      | --                 |

## Derived principles

These follow from the working principle + the locked-in decisions.
They shape the rest of the doc set.

1. **Off-the-shelf-first.** Already stated. Every doc names libraries,
   not abstract patterns.

2. **The local experience never regresses.** Every abstraction has a
   no-op / local-friendly default implementation. Today's `splitsmith
   ui` user must not see new login prompts, sync nudges, or quota
   warnings.

3. **The data model accommodates v2 today, even if only v1 is exposed.**
   ACL tables exist; only the owner ACL is used. Storage paths support
   "elsewhere" addressing; only local addresses are written.

4. **Hosted-mode default is privacy-preserving.** Audio-only upload
   (Tier 2) is the default for premium users; raw upload is opt-in.

5. **Heavy data is never on the critical path twice.** If a video is
   already on the user's disk, the hosted UI does not re-upload it to
   stream it back. The Storage interface knows "this is here".

6. **JSON files stay the canonical per-project record.** Even in
   hosted mode, `match.json` + `shooters/<slug>/project.json` are the
   source of truth, just stored on S3 instead of local disk. The
   database indexes them; it does not own them. This keeps the local
   and hosted modes structurally identical and makes "export this
   match" trivial in either direction.

7. **API surface is versioned.** `/api/v1/` from day one. Breaking
   changes ship as `/api/v2/` with a deprecation window.

8. **No bespoke job queue, identity provider, or upload protocol.**
   Each of those is a tarpit; off-the-shelf solutions exist for all
   three.

## What this doc set is NOT

- **An implementation plan.** Implementation lives in GitHub issues
  once a doc is approved. The doc set says *what* and *why*; issues
  say *how* and *when*.
- **A pricing strategy doc.** Pricing decisions belong to the
  business, not the architecture. 08-billing-and-quotas.md describes
  *how* billing integrates, not *what* to charge.
- **A go-to-market plan.** Marketing, positioning, and launch timing
  are out of scope.

## Open questions to validate during implementation

These are flagged here because they're cross-cutting; per-doc open
questions live in the relevant doc.

- **Auth provider final pick.** Clerk vs WorkOS vs Auth.js depends on
  cost at the user counts we expect. Cheapest at <100 users is
  probably Auth.js + SQLite; cheapest at >1000 is probably Clerk free
  tier; WorkOS becomes interesting if we want enterprise SSO later.
  Pick after one v1-shaped prototype with each.
- **Deploy target.** Fly.io vs Railway vs Render all work. Fly's
  region pinning is nice for EU users (the user is in Sweden) and its
  pricing is predictable. Railway's developer experience is the
  smoothest. Decide when implementing 06-api-surface.md.
- **Job queue: arq vs Procrastinate.** arq needs Redis. Procrastinate
  needs Postgres (which we have anyway in v2+ but not v1). v1 might
  ship with arq + a tiny Redis instance because v1 doesn't have
  Postgres yet.
- **WASM ML bundle size budget.** GBDT-only stays under 5 MB. If we
  want to add envelope onset (currently pure DSP) as a WASM module
  too, the bundle grows but we lose a server round-trip per detection
  call. Measure after the first hosted MVP runs.

## Reading order

The doc set is meant to be read in order on the first pass:

1. **This doc (00)** -- you're here.
2. **01-deployment-modes.md** -- what each mode promises.
3. **02-tenancy-and-identity.md** through **04-compute-backends.md** --
   the three foundational abstractions.
4. **05-uploads-and-streaming.md** through **08-billing-and-quotas.md**
   -- wire formats + flows + business integration.
5. **09-saas-progress.md** -- track shipped vs in-flight work.

After the first pass, the docs are reference: jump to whichever covers
the system you're touching.
