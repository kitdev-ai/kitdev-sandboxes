# CLAUDE.md

See [`AGENTS.md`](AGENTS.md). It is the single source of agent guidance for
this repository and applies in full to Claude Code.

Quick orientation:

- Read `PROMPT.md` and `docs/HANDOVER.md` before changing code or the server.
- Claim nothing as working, proven, or complete without a recorded result.
- Never print or commit a secret; verify credentials by their properties.
- Run the live host only from an exact committed revision staged root-only, and
  turn every manual server change into reviewed automation.
- Tests need `pyyaml` and `pytest`; without them the suite is not clean.
