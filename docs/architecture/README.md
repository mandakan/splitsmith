# Architecture diagram

`splitsmith.html` is a self-contained, interactive system diagram (pan/zoom,
search, guided views, light/dark, PNG/SVG export from the viewer's Export
menu). Open it in a browser; GitHub does not render it inline.

`splitsmith.architecture.json` is the source. It is an
[Archify](https://github.com/tt-a1i/archify) architecture spec; every node
carries `sources` paths that are verified against the pinned
`meta.repository.revision` at render time, and the viewer's `SRC` badges link
to them on GitHub.

To regenerate after changing the spec (update `meta.repository.revision` to the
commit whose file paths you are citing):

```bash
node <archify>/bin/archify.mjs deliver architecture \
  docs/architecture/splitsmith.architecture.json \
  docs/architecture/splitsmith.html \
  --quality showcase --repo-root .
```

The diagram is deliberately a ten-node system view: request path, job dispatch,
detection engine, exports, and the two side doors (CLI/MCP in-process, anonymous
share links). Per-module detail lives in SPEC.md under "Module responsibilities".
