# splitsmith.app — marketing site

Single-page static site for [splitsmith.app](https://splitsmith.app).
Pure HTML + CSS, no build step, no dependencies (fonts load from Google
Fonts). Uses the same chronograph-LED design tokens as the production
UI — see `docs/ux-redesign/06-design-system.md`.

## Files

- `index.html` — the page
- `favicon.svg` — the brand mark (also inlined into the page header)
- `../functions/api/waitlist.js` — Pages Function backing the
  "Hosted Splitsmith — coming soon" modal (POST /api/waitlist).
  Reads/writes the `WAITLIST` KV namespace bound in `wrangler.toml`.

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

## Waitlist backend (Pages Function + KV)

The "Sign in" chip in the header opens a "Hosted Splitsmith — coming
soon" modal whose form POSTs to `/api/waitlist`. The handler lives at
`functions/api/waitlist.js` and stores emails in the `WAITLIST` KV
namespace bound in `wrangler.toml`.

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

To run the marketing page with the Pages Function against a real KV
binding locally:

```bash
pnpm pages:dev      # serves site/ with functions/ + WAITLIST KV bound
```

(Without `pnpm pages:dev`, opening `site/index.html` directly works
for the static page but the waitlist POST will fail.)
