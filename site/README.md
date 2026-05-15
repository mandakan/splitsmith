# splitsmith.app — marketing site

Single-page static site for [splitsmith.app](https://splitsmith.app).
Pure HTML + CSS, no build step, no dependencies (fonts load from Google
Fonts). Uses the same chronograph-LED design tokens as the production
UI — see `docs/ux-redesign/06-design-system.md`.

## Files

- `index.html` — the page
- `favicon.svg` — the brand mark (also inlined into the page header)

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
