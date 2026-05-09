# /splitsmith-match -- Claude Code skill

Drives splitsmith's full pipeline (discover -> assign -> beep -> shots ->
export) end-to-end via the MCP server, pausing only at HITL checkpoints.
Implementation of issue #211 layer 3f.

## How it works

When you invoke `/splitsmith-match` (or describe the task), Claude
Code reads `SKILL.md` and follows the runbook. The skill expects:

- The splitsmith MCP server wired into your client (Claude Desktop,
  Claude Code, etc.) -- see `splitsmith mcp --help`.
- A splitsmith project on disk (run `splitsmith ui` once to seed,
  or use the SPA).
- A folder of source videos (often a USB drive).

Optional fallback: if MCP isn't installed, the skill instructions
note that the agent should hit `http://127.0.0.1:5174/api/...`
directly. Run `splitsmith ui` in another terminal first.

## Install

The repo ships the skill at `skills/splitsmith-match/`; Claude Code
loads skills from `~/.claude/skills/`. Symlink to install (so
future edits in the repo flow through to the install):

```bash
ln -s "$(pwd)/skills/splitsmith-match" ~/.claude/skills/splitsmith-match
```

Or copy if you prefer a snapshot:

```bash
cp -R "$(pwd)/skills/splitsmith-match" ~/.claude/skills/
```

After install, `/splitsmith-match` is available globally in
Claude Code regardless of CWD.

## What gets written

- `<project>/.splitsmith-match-log.md` -- chronological run log;
  every step + every HITL decision. The skill appends; it never
  rewrites. On a re-run, the log gives the user a paper trail of
  what the previous run did.
- `<project>/audit/stage<N>.json` -- standard audit JSONs (the
  detection tools write these).
- `<project>/exports/...` -- per-stage trims + CSVs + FCPXMLs +
  reports + optional overlays + the match-level FCPXML / MP4 /
  YouTube sidecar.

## What it does NOT do

- Click around in the SPA for you. The skill points at SPA URLs
  for fix-up flows but doesn't drive a browser.
- Force-rerun detection. Idempotent by design: if a stage's beep
  is already set, the skill skips it (or asks before overwriting).
- Upload anything. YouTube upload is out of scope per #211; the
  skill writes the sidecar and stops.
