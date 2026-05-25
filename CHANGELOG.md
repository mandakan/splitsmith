# Changelog

## [0.3.0](https://github.com/mandakan/splitsmith/compare/v0.2.1...v0.3.0) (2026-05-25)


### Features

* **beep-review:** single home for beep work; trim audit page ([#399](https://github.com/mandakan/splitsmith/issues/399)) ([9ecf999](https://github.com/mandakan/splitsmith/commit/9ecf9998f94edeb5420e3a982f97ae4edb2114f0))
* **brand:** hero + og:image, new tagline, audit shortcut hints ([#401](https://github.com/mandakan/splitsmith/issues/401)) ([40aa55a](https://github.com/mandakan/splitsmith/commit/40aa55ac56da0e9c81ae373554b9dc8816f4b2e0))

## [0.2.1](https://github.com/mandakan/splitsmith/compare/v0.2.0...v0.2.1) (2026-05-24)


### Bug Fixes

* **docs:** use absolute GitHub URLs for README images on PyPI ([#398](https://github.com/mandakan/splitsmith/issues/398)) ([de435a6](https://github.com/mandakan/splitsmith/commit/de435a6981b0532f05c159a73da86a9d107bf6af))

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
