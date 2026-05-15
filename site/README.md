# splitsmith.app — marketing site

Single-page static site for [splitsmith.app](https://splitsmith.app).
Pure HTML + CSS, no build step, no dependencies (fonts load from Google
Fonts). Uses the same chronograph-LED design tokens as the production
UI — see `docs/ux-redesign/06-design-system.md`.

## Files

- `index.html` — the page
- `favicon.svg` — the brand mark (also inlined into the page header)

## Deploy on Cloudflare Pages

Either via Git integration or direct upload:

**Git integration (recommended):**

1. Cloudflare dashboard → Pages → Create a project → Connect to Git.
2. Pick this repo.
3. Build settings:
   - Framework preset: **None**
   - Build command: *(leave blank)*
   - Build output directory: **`site`**
4. Set the custom domain to `splitsmith.app`.

**Direct upload (Wrangler):**

```bash
npx wrangler pages deploy site --project-name splitsmith
```

That's it — no build, no JS bundle, no server.
