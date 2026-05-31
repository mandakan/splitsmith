# 11 -- Environment strategy

How the hosted deployment is split into **staging** and **production**
across every service provider, and how the brand domain is carved up
between the marketing site and the application. Decided 2026-05-31;
this doc is the reference the provisioning work follows.

Two environments, no more. `staging` is a full mirror of `production`
that catches migration, config, and integration breakage before real
users see it. A third (per-developer / preview) environment is not
worth the provisioning overhead for a single-operator project --
Cloudflare Pages preview deploys and the local docker-compose stack
already cover that need.

## Domain layout

The brand domain is `splitsmith.app`. The apex serves marketing; the
application lives on a subdomain. Putting the app on a subdomain keeps
its cookies, TLS, and deploy lifecycle isolated from the marketing
site, which is a separately-hosted Cloudflare Pages project.

The product subdomain is **`my.`**, not `app.`: on a `.app` TLD,
`app.splitsmith.app` stutters, and the TLD already says "app". `my.`
is the established "logged-in product" convention and reads cleanly.

| Hostname | Serves | Provider | Env |
| --- | --- | --- | --- |
| `splitsmith.app`, `www.splitsmith.app` | Marketing site | Cloudflare Pages (`splitsmith`) | prod |
| *(per-branch preview URLs)* | Marketing preview | Cloudflare Pages auto-previews | n/a |
| `my.splitsmith.app` | The application | Railway | prod |
| `my.staging.splitsmith.app` | The application | Railway | staging |
| `models.splitsmith.app` | Model artifacts, read-only | R2 public bucket | shared |
| `login@splitsmith.app` (From) | Transactional email | Lettermint | prod only |

`www` is a 301 redirect to the apex (apex is canonical). The marketing
site is not foreclosed from a dedicated staging URL later, but Pages
per-branch previews cover review today, so none is provisioned.

### Consequence: the app origin is `my.`, not the apex

`SPLITSMITH_PUBLIC_URL` is the **app** origin, so magic-link callbacks
resolve there:

- prod: `https://my.splitsmith.app/auth/callback?token=...`
- staging: `https://my.staging.splitsmith.app/auth/callback?token=...`

Session cookies are host-scoped to the app subdomain -- they never
reach the marketing apex. The email *From* stays the apex
(`login@splitsmith.app`); DMARC aligns through the Lettermint DKIM and
`lm-bounces` records on the apex, so the differing app origin is
irrelevant to deliverability.

## Per-provider environment matrix

| Provider | prod | staging | Isolation mechanism |
| --- | --- | --- | --- |
| Railway | `production` env: `serve` + `worker` | `staging` env: `serve` + `worker` | Native Railway environments in one project, both built from the repo Dockerfile |
| Neon | `main` branch | long-lived `staging` branch | Copy-on-write branch; staging can reset from prod for realistic data. RLS behaves identically on both |
| R2 | bucket `splitsmith-uploads-prod` | bucket `splitsmith-uploads-staging` | Separate buckets; the read-only `splitsmith-models` bucket is shared |
| Lettermint | route `production`, real sends | `console` backend, links to logs only | Staging never sends real mail -- zero stray-email / reputation risk |
| Cloudflare Pages | `splitsmith.app` (production branch `main`) | per-branch preview deploys | Built-in Pages environments |

### Neon: direct connection, not the pooler

Both branches use Neon's **direct** connection endpoint, not
`-pooler`. asyncpg prepared statements do not survive PgBouncer
transaction pooling, and the app uses `NullPool` so it does not need
the pooler. This is the same constraint doc 02 and the deploy notes
call out.

## Promotion flow

```
PR  --(merge)-->  main  --(auto)-->  staging
                   |
                   +--(release-please release)-->  production
```

- A merge to `main` auto-deploys the Railway **staging** environment
  (and publishes the marketing Pages production build, since the site
  tracks `main`).
- A release -- the release-please release PR (currently #403) being
  merged, which tags a version -- promotes the same image to the
  Railway **production** environment. Prod is gated behind an explicit
  release, never a raw push.

This reuses the release-please flow already in the repo rather than
inventing a separate promotion mechanism.

## Environment variables per Railway environment

Both `serve` and `worker` in a given environment share these. The
`worker` has no login routes but the shared wiring still requires
`SPLITSMITH_PUBLIC_URL` and the email vars to be present.

| Variable | prod | staging |
| --- | --- | --- |
| `SPLITSMITH_MODE` | `hosted` | `hosted` |
| `SPLITSMITH_DATABASE_URL` | Neon `main`, direct endpoint | Neon `staging`, direct endpoint |
| `SPLITSMITH_PUBLIC_URL` | `https://my.splitsmith.app` | `https://my.staging.splitsmith.app` |
| `SPLITSMITH_EMAIL_BACKEND` | `lettermint` | `console` |
| `LETTERMINT_API_TOKEN` | set | unset (console) |
| `SPLITSMITH_EMAIL_FROM` | `Splitsmith <login@splitsmith.app>` | unset (console) |
| `LETTERMINT_ROUTE` | `production` (optional) | unset |
| `SPLITSMITH_S3_*` | `splitsmith-uploads-prod` bucket creds | `splitsmith-uploads-staging` bucket creds |

A public https `SPLITSMITH_PUBLIC_URL` turns on the Secure cookie flag
in both environments.

## DNS plan (Cloudflare zone `splitsmith.app`)

Already in place: the Lettermint sending records (`_dmarc`,
`lettermint._domainkey`, `lm-bounces`), the apex marketing CNAME, the
`models` R2 CNAME, and Cloudflare Email Routing (inbound MX + SPF +
`cf2024` DKIM).

To add during provisioning:

- `www.splitsmith.app` -- CNAME to the Pages project + a redirect rule
  to the apex.
- `my.splitsmith.app` -- CNAME to the Railway production domain.
  DNS-only until Railway issues the cert, then it can be proxied.
- `my.staging.splitsmith.app` -- CNAME to the Railway staging domain,
  same cert caveat.

## Provisioning order (the actionable steps)

Nothing in the codebase blocks this; it is operational. Drive Neon and
R2 entirely via MCP, Railway via its MCP / CLI.

1. **Neon** -- create the project; `main` is prod. Create the long-lived
   `staging` branch. Run migrations against both (`alembic upgrade head`,
   or let `serve` migrate on boot). Use the direct connection strings.
2. **R2** -- create `splitsmith-uploads-prod` and
   `splitsmith-uploads-staging` buckets + scoped S3 tokens. Leave the
   existing public `splitsmith-models` bucket alone.
3. **Railway** -- one project, `staging` + `production` environments,
   each with `serve` + `worker` from the Dockerfile. Set the env-var
   matrix above. Generate the two app domains, then add the matching
   Cloudflare CNAMEs.
4. **Cloudflare** -- add the `www`, `my`, and `my.staging` records;
   add the `www` -> apex redirect rule.
5. **Verify** -- staging first: magic-link login via the console log,
   an upload, a detection job through the worker, an export. Then cut a
   release to promote prod and repeat the smoke against
   `my.splitsmith.app`.
