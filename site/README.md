# splitsmith.app — marketing site

Single-page static site for [splitsmith.app](https://splitsmith.app).
Pure HTML + CSS, no build step, no dependencies (fonts load from Google
Fonts). Uses the same chronograph-LED design tokens as the production
UI — see `docs/ux-redesign/06-design-system.md`.

## Files

- `index.html` — the page
- `favicon.svg` — the brand mark (also inlined into the page header)

## Deploy on Cloudflare Pages

**Automated (default):** `.github/workflows/deploy-site.yml` publishes
`site/` to the `splitsmith` Cloudflare Pages project on every push to
`main` that touches `site/**`. It can also be triggered manually via
**Actions → Deploy marketing site → Run workflow**.

The workflow needs two repo secrets:

- `CLOUDFLARE_API_TOKEN` — token scoped to `Account: Cloudflare Pages — Edit`
- `CLOUDFLARE_ACCOUNT_ID` — your Cloudflare account ID

One-time Cloudflare setup (only needed before the first deploy):

1. Cloudflare dashboard → Workers & Pages → Create → Pages → Direct
   upload (or Connect to Git, if you prefer to skip the Action). Name
   the project **`splitsmith`**.
2. Assign the custom domain **`splitsmith.app`** to the project.

After that, every push to `main` ships.

**Manual upload (Wrangler):**

```bash
npx wrangler pages deploy site --project-name splitsmith
```

No build, no JS bundle, no server.
