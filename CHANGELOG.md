# Changelog

## 0.2.0 (2026-05-24)

First public release.

Extract IPSC shot splits from head-mounted camera footage. Detect shots
via a 3-voter ensemble (envelope onset / CLAP / GBDT-with-PANN), produce
a CSV of splits, and emit an FCPXML timeline with per-shot markers and
optional overlay clips for Final Cut Pro.

Install:

```
uv tool install splitsmith
```

After install, run `splitsmith fetch-models` to pre-download the ~440 MB
of ONNX detection artifacts (otherwise they download on first detection).
