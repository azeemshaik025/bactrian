# Installing bactrian

Needs Python 3.9 or newer and Claude Code logged in, since both models run through `claude -p`. Standard library only.

## Claude Code

```bash
claude mcp add --scope user bactrian -- python3 /path/to/bactrian/server.py
```

## Codex

```bash
codex mcp add bactrian -- python3 /path/to/bactrian/server.py
```

## Models

Two models do the work. The scout finds the pages and uses `haiku`. The scribe reads them, writes your answer and uses
`sonnet`. Pick others with `BACTRIAN_SCOUT` and `BACTRIAN_SCRIBE`, for example
`claude mcp add -e BACTRIAN_SCRIBE=opus ...`, or under `[mcp_servers.bactrian.env]` in Codex.

## Close the other ways in

bactrian only helps if it is your agent's only way to the web.

Claude Code, in `~/.claude/settings.json`:

```json
{ "permissions": { "deny": ["WebFetch", "WebSearch", "mcp__claude-in-chrome", "mcp__Claude_Browser"] } }
```

Codex, at the top of `~/.codex/config.toml` (not yet tested):

```toml
web_search = "disabled"

[features]
browser_use = false
in_app_browser = false
computer_use = false
```

Shell commands such as `curl` can still reach the web. Keep them off the network with your agent's sandbox.

## Run the checks

```bash
python3 test_server.py && python3 test_web.py
```

`web.py` is the first version, where a planner model writes a small Python program for an interpreter to run.
`examples/` holds the poisoned pages used to attack both versions.
