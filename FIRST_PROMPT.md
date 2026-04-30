# First Prompt for Claude Code

Copy-paste this when starting a Claude Code session in the project directory.

---

You're working on `splitsmith`, a CLI tool that extracts IPSC shot splits from head-mounted camera footage and generates Final Cut Pro timelines. Read these in order before writing any code:

1. `README.md` — project overview
2. `SPEC.md` — full technical specification (this is the source of truth)
3. `CLAUDE.md` — code conventions and architecture rules
4. `examples/tallmilan-2026.json` — sample input data

Then:

1. Confirm the environment: `uv --version`, `ffmpeg -version`, `python --version` (need 3.11+).
2. Run `uv sync` to set up the venv from `pyproject.toml`.
3. Create the source layout: `src/splitsmith/` with empty modules per SPEC.md.
4. Set up `tests/` with a `conftest.py` and an empty `tests/fixtures/` directory.
5. Stop and ask me for a sample video before tuning detection thresholds. The detection modules need real audio to validate against.

When implementing, work in this order:
1. `config.py` (Pydantic models for config + data structures)
2. `video_match.py` (simplest module, easy to test)
3. `beep_detect.py` (needs sample audio)
4. `trim.py` (depends on beep detection)
5. `shot_detect.py` (needs sample audio)
6. `csv_gen.py` (straightforward output)
7. `fcpxml_gen.py` (most complex output, leave for later)
8. `report.py`
9. `cli.py` (wires everything together)

For each module: write the implementation, write fixture-based tests, get my sign-off before moving on. Don't try to build the whole pipeline in one shot.

If anything in SPEC.md or CLAUDE.md is unclear or seems wrong, ask. Don't paper over ambiguity.
