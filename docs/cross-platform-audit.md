# Cross-platform audit

State of the splitsmith codebase against the standard list of macOS-to-Linux
porting hazards, taken at `2026-05-24` ahead of the first PyPI release. The
slim wheel runs end-to-end on Ubuntu in CI (`slim-smoke` job) so anything that
breaks the **detect** path on Linux is already caught. The risks below are the
ones the smoke job *doesn't* exercise: trim / render, multi-shooter compare,
overlay rendering, file-manager integration, and locale / XDG edge cases.

## Audited clean -- no change needed

| Concern | Verdict | Evidence |
|---|---|---|
| Case-sensitive imports / resources | clean | All 57 `splitsmith.X` import paths match on-disk lowercase casing. Resource lookups via `importlib.resources.files("splitsmith.data").joinpath(...)` use exact-cased filenames (`overlay_theme.json`, `JetBrainsMono-Bold.ttf`, `Antonio-VariableFont.ttf`). The only PascalCase `Path()` literal is `scripts/fetch_dtds.py`'s `Path("Contents/Frameworks/...")` -- intentional macOS-only Final Cut Pro path. |
| Multiprocessing fork hazards | clean | `multiprocessing.Pool` and `ProcessPoolExecutor` are not used anywhere in `src/`. The only concurrency is `ThreadPoolExecutor` in `ui/jobs.py` and `ui/server.py`. Threads don't share fork's CUDA / MKL / OpenMP-after-init footgun. |
| Unicode filename normalization (NFC vs NFD) | clean by design | `match_model.py` slugifies shooter and match names via `unicodedata.normalize("NFKD", name).encode("ascii", "ignore")`. Everything that lands on the filesystem is pure ASCII; the original display string lives in JSON. Sidesteps the whole NFC/NFD comparison-mismatch class. |
| ffmpeg encoder flavor (Debian vs Homebrew) | clean by design | `trim.py:_probe_available_encoders()` parses `ffmpeg -encoders` output and falls back to `libx264` if the requested encoder isn't present. `libx264` + native `aac` are in stock Debian/Ubuntu since well before any LTS target. |
| Subprocess returncode comparisons | clean | All checks are `== 0` / `!= 0` -- nothing matches specific nonzero values that could differ between Homebrew's binary and Debian's shell-wrapper variants. |
| `encoding="utf-8"` coverage | one fix, see below | Only one real text-mode `open()` in `src/` lacked the kwarg (the candidates CSV); fixed. |
| XDG basedir handling | one fix, see below | `runtime.py` and `user_config.py` correctly fell back when `XDG_*_HOME` was empty or unset, but accepted relative paths. Per the freedesktop spec relative values are malformed; fixed. |
| Hardcoded macOS font paths | one fix, see below | `_FONT_PRESETS` is macOS-heavy but `_FONT_FALLBACKS` includes DejaVu, and the final fallback is PIL's bitmap default. Silent fallback was the real bug -- now logged. |
| `xdg-open` / `open -R` / `explorer` failure | one fix, see below | The reveal endpoints used to run with `check=False` and swallow nonzero exits. Now propagated as HTTP 500 so the SPA can toast. |
| Hardcoded `/sbin/mount` | already gated | `ui/server.py:8808` is inside a per-OS branch -- only the macOS path hits the hardcoded binary. Linux uses `/proc/mounts`. Not changed (no real-world failure mode). |
| Symlinks | clean | `shutil.copytree(..., symlinks=True)` preserves them; reveal endpoint walks the symlink target so registered videos on external storage resolve correctly. |
| Temp files | clean | All temp work goes through `tempfile`, which respects `$TMPDIR`. No hardcoded `/tmp`. |

## Fixed in this audit

### 1. CSV writes pin UTF-8 (`cli.py`)
`<stem>-candidates.csv` is written from `audit_prep`. The `Path.open("w", newline="")` call previously inherited the locale encoding, which is `ascii` on Linux with `LANG=C` / `LANG=POSIX`. A non-ASCII shooter or club name would crash with `UnicodeEncodeError`. Now pins `encoding="utf-8"`.

Regression guard: `tests/test_cli_paths.py::test_detect_csv_open_pins_utf8` inspects the source and fails if a future refactor drops the kwarg.

### 2. XDG basedir reject non-absolute values (`runtime.py`, `user_config.py`)
Per the freedesktop XDG basedir spec, `XDG_CACHE_HOME` and `XDG_CONFIG_HOME` MUST be absolute. The previous code accepted any truthy value, so `XDG_CACHE_HOME=cache` (relative, e.g. a typo) would produce a path relative to CWD that the server would happily write into.

Now: relative values fall through to the platform default with a `logger.warning` so a mistyped override doesn't fail silently.

Tests: `tests/test_runtime.py::test_xdg_*` and `tests/test_user_config.py::test_xdg_config_home_*`, all Linux-only.

### 3. Overlay font tier logging (`overlay_render.py`)
`_load_font` walks a four-tier fallback chain (explicit path -> bundled `splitsmith-*` font -> `_FONT_PRESETS` -> `_FONT_FALLBACKS` -> `ImageFont.load_default()`). The final tier is PIL's built-in bitmap font, which is significantly lower-quality than any TrueType. On a minimal Linux box without `fonts-dejavu-core`, overlays would render with the bitmap font and look low-res without any indication of why.

Now: each tier choice is logged once per `(font_name, tier)` pair. Bundled and explicit hits are DEBUG; system-path resolution is INFO; the `_FONT_FALLBACKS` walk is WARNING; the PIL bitmap default is WARNING with a `apt install fonts-dejavu-core` hint.

Tests: `tests/test_overlay_render.py::test_load_font_pil_default_fallback_warns` + `test_load_font_bundled_emits_debug_only`.

### 4. Reveal endpoints surface helper failures (`ui/server.py`)
`/api/files/reveal` and `/api/shooters/{slug}/videos/reveal` previously called `subprocess.run(..., check=False)` and unconditionally returned 200. A headless Linux install without `xdg-open`, a Wayland session without DBUS, or `xdg-open` exiting nonzero would silently no-op -- the SPA had no way to surface the failure.

Now: factored into `_reveal_in_file_manager(resolved)`. Nonzero exit raises `HTTPException(500)`. `OSError` (helper not on PATH) raises `HTTPException(500)`. Windows opts out of the returncode check because `explorer /select,...` exits 1 even on a successful selection.

Tests: `tests/test_ui_server.py::test_videos_reveal_surfaces_nonzero_subprocess_exit` + `test_videos_reveal_surfaces_missing_helper_binary`.

## Deferred -- doc-only

### Multiprocessing start method
Linux defaults to `fork`; macOS defaults to `spawn`. If we ever add a `multiprocessing.Pool` to the ensemble path, forking after CLAP / PANN / MKL / OpenMP initialization is the classic deadlock/segfault footgun. Right now there are zero `Pool` / `ProcessPoolExecutor` usages, so there's nothing to fix. If we add one, we MUST call `multiprocessing.set_start_method("spawn", force=False)` at engine init.

### MPS device selection
The ONNX runtime backend doesn't currently advertise the macOS MPS device. Apple Silicon users run CPU-only. This is a feature gap, not a portability bug -- the current setup works correctly everywhere; macOS users just don't get GPU speedup.

### `/sbin/mount` not via `shutil.which`
`ui/server.py:8808` hardcodes `/sbin/mount` inside the macOS branch. `/sbin/mount` is part of the base system on every supported macOS version. If a sandbox ever moves the binary, the hardcoded path will fail. Not worth a `shutil.which` lookup until something actually breaks.

## Defense-in-depth (optional, not done)

### Extend `slim-smoke` to exercise a trim
The smoke job currently runs `splitsmith detect` against the bundled fixture, which exercises ffmpeg-as-decoder + CLAP + PANN inference end-to-end. It does NOT exercise `splitsmith trim` (libx264 / aac encode path), `splitsmith fcpxml`, or overlay rendering. A second smoke step would catch libx264-availability regressions on Debian-family hosts. Cost: ~30s + a small fixture. Skipped because the existing encoder probe + universal `libx264` fallback already covers this; revisit if a Linux trim regression slips through.

## Re-audit triggers

Re-run this audit when:
- A `multiprocessing.Pool` or `ProcessPoolExecutor` lands anywhere in `src/`.
- A new top-level `Path(...)` literal with mixed casing lands (look for `Path("Capital/...")`).
- A new `open()` call lands in `src/` without `encoding=`.
- A new `XDG_*` env-var consumer lands.
- The target Linux distro shifts to one shipping `ffmpeg` without `libx264` (i.e. an `--enable-gpl=no` build).
