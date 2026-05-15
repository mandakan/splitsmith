# 08 -- Billing and quotas

This doc defines **how Splitsmith charges money** and **how it
enforces tier limits**. It covers Stripe integration, the entitlement
model, the v1 (display-only) and v2 (enforced) quota approaches, and
the failure modes (subscription cancellation, payment failure, refund).

This doc does **not** set prices. Pricing is a business decision,
revisited as we learn what users will pay. The architecture supports
any flat or per-feature scheme.

## Tier model (v1)

Two tiers:

- **Free.** Local-mode access (the desktop app stays free forever).
  Hosted mode read-only after first match -- a free user can sign
  up, run detection on one project, and view the result. Subsequent
  detection requires premium.
- **Premium.** Flat-rate subscription. Unlimited Tier 2 detection
  (audio upload + cloud ML), opt-in raw upload + Tier 3, all
  current and future hosted features.

A "match" for free-tier limit purposes is one `project_id`. Replays
on the same project don't count.

### What "free tier" means concretely

Free users in hosted mode can:

- Sign up via magic link.
- Upload audio for one project.
- Run Tier 2 detection on that project.
- View, audit, and export the result (CSV, FCPXML).
- Delete their data.

Free users CANNOT:

- Create a second project. (Pickerwarn: "Premium required.")
- Upload raw video. (Toggle disabled with paywall hint.)
- Use Tier 3.
- Invite squadmates (v2 feature anyway).

The "one project" limit is generous enough to demonstrate value and
strict enough to push converted use into the paid tier.

### Why flat-rate

Per-detection pricing was considered and rejected for v1:

- **Friction.** Users hesitate before clicking "detect" if they're
  paying per click. Bad for a tool whose UX depends on iterative
  re-runs.
- **Unpredictability.** A user with a 20-stage match doesn't want
  surprise charges.
- **Operational simplicity.** Flat rate = one Stripe product, one
  webhook, one entitlement bit in Postgres. Per-detection = usage
  metering, billing aggregation, line-item generation.

Flat rate also fits the "weekend warrior IPSC competitor" persona
the project guidance describes -- predictable monthly cost beats
optimal cost.

If usage shows we're losing money on whales, v2 can add per-stage
overage pricing on top of the flat tier. Today's data: a heavy
user costs us <$1/month in compute (per 04). Even a $5/month tier
has plenty of margin.

## Stripe integration

We use **Stripe Checkout** (Stripe-hosted page) + **Stripe Customer
Portal** (Stripe-hosted billing management) + **webhooks** for
entitlement updates.

We do not build our own checkout page. We do not store card data.
Card data never touches our infrastructure.

### Checkout flow

```
Browser                       API server                  Stripe
   |                              |                          |
   |-- POST /api/v1/billing/      |                          |
   |    checkout                  |                          |
   |                              |-- Customer.create -----> |
   |                              |   (if no stripe_customer_id) |
   |                              |<- customer_id -----------|
   |                              |                          |
   |                              |-- CheckoutSession.create>|
   |                              |   { customer: id,        |
   |                              |     mode: 'subscription',|
   |                              |     line_items: [{ price: PREMIUM }],|
   |                              |     success_url, cancel_url } |
   |                              |<- session_url -----------|
   |<-- { url } ------------------|                          |
   |                                                         |
   |-- redirect to session_url --------------------------->  |
   |                            [user enters card]          |
   |<-- redirect to success_url ---------------------------- |
   |                                                         |
   |                              [Stripe sends webhook]     |
   |                              |<-- POST /api/v1/billing/webhooks/stripe |
   |                              |    event=checkout.session.completed     |
   |                              |    or invoice.paid                      |
   |                              |-- update users.entitlement = 'premium'  |
   |                              |    + entitlement_until                  |
   |                              |-- 200 -> Stripe                         |
```

The `success_url` is `splitsmith.app/billing/success?session_id=
{CHECKOUT_SESSION_ID}`. The success page polls our API for
entitlement update (the webhook may arrive a few seconds after the
redirect).

### Customer Portal

For all post-signup billing operations -- update card, view invoices,
cancel subscription -- we redirect to Stripe Customer Portal:

```
POST /api/v1/billing/portal
->
  { url: "https://billing.stripe.com/p/session/..." }
```

The user manages everything Stripe-side. We never build "update
your card" forms.

### Webhooks

The webhook endpoint at `POST /api/v1/billing/webhooks/stripe`
handles these events:

| Event | Action |
| --- | --- |
| `checkout.session.completed` | Set `entitlement = 'premium'`, `entitlement_until = current_period_end + 24h grace` |
| `invoice.paid` | Extend `entitlement_until` |
| `invoice.payment_failed` | Email user; no entitlement change yet |
| `customer.subscription.deleted` | Schedule `entitlement = 'free'` at `entitlement_until` (no immediate downgrade) |
| `customer.subscription.updated` | Update `entitlement_until` to new `current_period_end` |
| `charge.refunded` | Set `entitlement = 'free'` immediately |

Every webhook payload is verified against Stripe's signature header
using the `STRIPE_WEBHOOK_SECRET` env var. Unverifiable webhooks
return 400.

We log every webhook in the `billing_events` table for replay /
debugging:

```sql
CREATE TABLE billing_events (
  id              TEXT PRIMARY KEY,        -- ULID
  stripe_event_id TEXT UNIQUE NOT NULL,    -- evt_... from Stripe
  type            TEXT NOT NULL,
  user_id         TEXT REFERENCES users(id),
  payload         JSONB NOT NULL,
  processed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Idempotency: `stripe_event_id` is unique. Re-delivered webhooks are
no-ops.

### Entitlement model

Two columns on the `users` table (already shown in 02):

- `entitlement` -- `'free'` | `'premium'`
- `entitlement_until` -- `TIMESTAMPTZ`, nullable

Authorisation logic for premium-gated actions:

```python
def has_premium(user: User, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    if user.entitlement == 'free':
        return False
    if user.entitlement_until is not None and user.entitlement_until < now:
        return False
    return True
```

The `+ 24h grace` on entitlement_until covers Stripe webhook delays
and one-time payment slips. Beyond 24h, the user lapses to free.

### Why entitlement, not a foreign key to a Stripe object

We could resolve "is this user premium?" by hitting Stripe's API
every request. We don't, because:

- **Latency.** Stripe API calls are 100-200ms. Adds to every request.
- **Outage tolerance.** Stripe outages should not break detection.
- **Local cache via webhooks.** We have the webhook signal; storing
  the derived entitlement is just caching it.

If our entitlement is ever wrong (webhook missed, manual fix needed),
we have an admin "reconcile this user with Stripe" tool that hits
Stripe's API and updates the row.

## v1 quota strategy: display, don't enforce (mostly)

For v1 the quota model is intentionally light:

- **Free tier:** the "1 project max" rule is enforced (project create
  endpoint checks count + entitlement; refuses with 402 Payment
  Required if the limit is hit).
- **Premium tier:** no usage caps. Storage, detection requests, raw
  uploads -- all unlimited within the per-upload size limits from
  05. We display usage in the user's settings page (storage used,
  detection runs this month) but don't enforce ceilings.

This matches the "we don't have product-market-fit data yet" stage.
Adding caps before knowing what real usage looks like risks scaring
off legitimate users.

If a single premium user starts costing us $50/month in compute (the
data from 04 says this would require an extreme workload), we add
quotas in v2 informed by what real usage looks like.

### What we measure

- **Storage used** -- per user, R2 bytes. Recomputed nightly from R2
  inventory, cached in Postgres.
- **Detection jobs run** -- counted from `compute_jobs` table per
  user per month.
- **Bytes uploaded this month** -- counted from `upload_sessions`
  per user per month.

These metrics are shown in the user's settings page and used for
internal cost-per-user dashboards. They are not currently used to
gate access (for premium users).

## v2 quota strategy: soft + hard limits

When v2 introduces enforced quotas (informed by v1 measurements):

- **Soft limit:** at 80% of cap, banner warning + email.
- **Hard limit:** at 100%, premium endpoints return 429 with
  `code: "quota_exceeded"`. User can upgrade tier or wait until the
  next billing cycle.
- **Pro-rated overage.** If we add a metered "overage" charge on top
  of the flat tier, it's metered via Stripe's usage records --
  one webhook per cycle aggregating from `compute_jobs` /
  `upload_sessions`.

The tables are ready (`compute_jobs.tier`, `upload_sessions.kind`,
`size_bytes` -- everything we need for usage records). v1 just
doesn't read them for enforcement.

## Failure modes

### Payment fails mid-cycle

- Stripe retries the charge per their dunning settings (typically 3
  retries over 2 weeks).
- Each `invoice.payment_failed` webhook triggers a user email (sent
  by Stripe).
- `entitlement_until` does not extend, so the user lapses to free
  at end-of-cycle if the dunning fails.
- Lapsed users keep their data; they just can't run new premium
  jobs.

### User cancels mid-cycle

- `customer.subscription.deleted` webhook arrives.
- `entitlement_until` is set to the period end.
- User retains premium until that date, then lapses.
- All their data is preserved.

### Refund / chargeback

- `charge.refunded` => immediate downgrade to free.
- Three chargebacks => account suspended pending review (manual,
  not automated in v1).

### User deletes account

- Hard delete: `DELETE /api/v1/account` -- queues a deletion job,
  user gets confirmation email, 7-day grace period to undo.
- After grace: all R2 data deleted (per-user prefix from 03 makes
  this easy), Postgres rows soft-deleted (`deleted_at` set), Stripe
  customer cancelled.
- After 90 days: Postgres rows hard-deleted.

The 7-day grace + 90-day soft delete is GDPR-shaped: hard delete
on request, but with a recovery window that catches accidents.

## Local mode and billing

Local mode has no billing UI. The desktop app shows nothing about
tiers, premium, or upgrade prompts.

The exception is the desktop's hosted-account hookup (07): once
linked to a hosted account, the desktop respects that account's
entitlement when offering Tier 2 detection ("Use cloud detection?
Premium required" if the linked account is free). Tier 1 always
works regardless.

## Self-hosted mode and billing

A self-hosted deployment can:

- Run with no billing wired up at all (`BILLING_BACKEND=none`). All
  users default to entitlement `premium`; no Stripe; no paywalls.
- Wire up their own Stripe with their own products. The
  `BillingBackend` interface is one method (`is_premium(user_id)`)
  -- they can implement it any way they want.

We document the self-hosted path but don't ship a v1 example beyond
the `none` mode.

## Pricing-page and marketing

Out of scope for this doc (see doc 00, "What this doc set is NOT").
Architecture supports any pricing scheme; the marketing page is a
separate decision.

## Open questions

- **Free trial of premium?** A 14-day trial would lift conversion
  but adds churn complexity. Defer to launch-time decision.
- **Annual vs monthly billing.** Easy to add both Stripe products
  later; not a v1 architecture concern.
- **Refunds policy.** Stripe handles the mechanics; the policy
  ("30-day refund window") is a business decision.
- **EU VAT / sales tax.** Stripe Tax handles this for ~$0.30/txn
  if we enable it. Probably worth enabling at launch.
- **Per-region pricing.** Stripe supports it; defer until usage
  data shows whether it matters.
- **Account deletion grace period length.** 7 days vs 30 days. 7
  matches the "oh shit I clicked the wrong button" recovery; 30 is
  more conservative. Pick during impl.
