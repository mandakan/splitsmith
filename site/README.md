# splitsmith.app — marketing site

Single-page static site for [splitsmith.app](https://splitsmith.app).
Pure HTML + CSS, no build step, no dependencies (fonts load from Google
Fonts). Uses the same chronograph-LED design tokens as the production
UI — see `docs/ux-redesign/06-design-system.md`.

## Files

- `index.html` — the page (redone 2026-09-14 on the restructured UI:
  island nav, editorial hero, the loop as a bento of real screenshots,
  outputs, install, a Hosted section with the invite request form,
  detection; hosted is invite-only, so the primary action is the local
  install)
- `img/` — screenshots of the app on the demo match
  (`scripts/seed_demo_match.py --media`, 1440x900, cropped with PIL);
  re-shoot them after a visible UI change
- `og.png` — the link card, a 1200x630 render of the hero
- `favicon.svg` — the brand mark (also inlined into the page header)
- The "Request an invite" form in the Hosted section posts straight to
  the hosted app's `POST /api/v1/access-requests` (`https://my.splitsmith.app`)
  now that the app has that route. The Pages Function that used to serve
  this (`functions/api/waitlist.js`) is retired; see "Waitlist backend"
  below for the one-time migration of what it collected.

## Deploy on Cloudflare Pages

The Pages project `splitsmith` is already created and live at
[splitsmith.pages.dev](https://splitsmith.pages.dev). It's wired to
`splitsmith.app` as a custom domain; DNS for the apex + `www` resolves
through Cloudflare in the same account.

**CI deploys** — `.github/workflows/deploy-marketing.yml` runs
`wrangler pages deploy site` on every push to `main` that touches
`site/**` or `wrangler.toml`. Needs repo secrets `CLOUDFLARE_API_TOKEN`
(Pages:Edit + Workers:Edit) and `CLOUDFLARE_ACCOUNT_ID`.

**Manual deploy:**

```bash
export CLOUDFLARE_API_TOKEN=…
export CLOUDFLARE_ACCOUNT_ID=…
npx wrangler pages deploy site --project-name splitsmith --branch main
```

`wrangler.toml` at the repo root pins `pages_build_output_dir = "./site"`
so `wrangler pages deploy` (without args) works from anywhere in the tree.

That's it — no build, no JS bundle, no server.

## Waitlist backend (retired; KV kept for the one-time migration)

The invite form used to POST to `/api/waitlist`, a Pages Function
(`functions/api/waitlist.js`) that stored emails in the `WAITLIST` KV
namespace. That function is deleted and the form now posts directly to
the hosted app (see above) — the app's `access_requests` table is the
one list going forward.

The `WAITLIST` KV namespace still holds every signup collected before
the cutover. The binding in `wrangler.toml` and the `waitlist:list` /
`waitlist:get` scripts below stay in place only until that data has
been imported into `access_requests` with `scripts/import_waitlist.py`
(see that script's module docstring for the exact dump + import
commands); a follow-up commit then removes the binding and these
scripts.

**One-time bootstrap (do this once per Cloudflare account):**

```bash
pnpm install                  # installs wrangler as a dev dep
pnpm wrangler login           # opens browser, stores token in ~/.wrangler

pnpm kv:create                # prints: id = "<production-id>"
pnpm kv:create-preview        # prints: id = "<preview-id>"
```

Paste the two ids into the `[[kv_namespaces]]` block in
`wrangler.toml`, commit, and push. CI redeploys with the binding wired
up; the function reads it as `env.WAITLIST`.

**Inspect / export the list:**

```bash
# List every email key (uses the binding name from wrangler.toml).
pnpm waitlist:list

# Get the JSON record for one email.
pnpm waitlist:get email:someone@example.com

# Dump the whole list to a local file (requires `jq`).
pnpm waitlist:list | jq -r '.[].name' | while read k; do
  printf "%s\t" "$k"
  pnpm waitlist:get "$k" -- --remote
done > waitlist.tsv
```

Storage shape:

```
email:<lowercased-email>   ->  {"ts","ip_hash","ua","source"}
rl:<sha256(ip)[:16]>       ->  "<count>"   (TTL 1h, rate-limit counter)
```

**Spam defenses today:** honeypot field (`hp`), per-IP rate limit
(5/hour). No CAPTCHA — add Cloudflare Turnstile if/when we see abuse.
The handler is not currently double-opt-in; bouncing addresses get
caught when we actually send (port the list to Resend / Buttondown
first and let that provider handle deliverability).

## Local preview

```bash
pnpm pages:dev      # serves site/ with functions/ bound
```

(Without `pnpm pages:dev`, opening `site/index.html` directly still
works: the invite form posts straight to `https://my.splitsmith.app`,
not to a local function.)
