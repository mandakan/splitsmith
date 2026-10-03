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

## Waitlist backend (retired)

The invite form used to POST to `/api/waitlist`, a Pages Function that
stored emails in the `WAITLIST` KV namespace. The form now posts to the
hosted app's `/api/v1/access-requests`, and the app's `access_requests`
table is the one list. The KV entries were imported with
`scripts/import_waitlist.py` on 2026-10-03 and the binding removed; the
namespace itself (id `8e8512c2613e416bab5a445630f11000`) is left in the
Cloudflare account, unbound.

## Local preview

```bash
pnpm pages:dev      # serves site/ with functions/ bound
```

(Without `pnpm pages:dev`, opening `site/index.html` directly still
works: the invite form posts straight to `https://my.splitsmith.app`,
not to a local function.)
