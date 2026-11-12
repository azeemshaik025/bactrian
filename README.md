# bactrian

Lets a coding agent research the web without reading it. The agent writes the search and the question. A separate
process fetches the pages, a model with no tools reads them, and the answer opens in your browser. The agent gets back
one line:

```
The answer is open in the user's browser; you cannot see it. pages read: 4, failed: 1, answered: yes.
```

A Bactrian camel has two humps, and this has two models: the split from Google DeepMind's CaMeL. The agent plans and
never sees untrusted text. The reader sees the untrusted text and can do nothing. A page can lie to the reader, but it
cannot make your agent run a command, write a file or send your data anywhere, because your agent never reads it.

## Install

Needs Python 3.9 or newer and Claude Code logged in: the searcher and the reader run through `claude -p`.

Claude Code:

```bash
claude mcp add --scope user bactrian -- python3 /path/to/bactrian/server.py
```

Codex:

```bash
codex mcp add bactrian -- python3 /path/to/bactrian/server.py
```

Codex asks before every MCP tool call, and `codex exec` rejects the call outright. To let `research` run without
asking, extend the entry `codex mcp add` wrote to `~/.codex/config.toml`:

```toml
[mcp_servers.bactrian]
command = "python3"
args = ["/path/to/bactrian/server.py"]
tool_timeout_sec = 300

[mcp_servers.bactrian.tools.research]
approval_mode = "approve"
```

## Choose the models

The reader defaults to `sonnet` and the searcher to `haiku`. Set `BACTRIAN_READER` or `BACTRIAN_SEARCHER` to any model
name `claude -p` accepts:

```bash
claude mcp add --scope user -e BACTRIAN_READER=opus bactrian -- python3 /path/to/bactrian/server.py
```

In Codex, put them under `[mcp_servers.bactrian.env]`.

## Checked on every call

Before each research call, bactrian starts the reader and the searcher just long enough to read the tool lists they
were given, and refuses to run unless the reader has no tools and the searcher has only WebSearch. It takes about half a
second and no model call. If a Claude Code update ever stopped honouring `--tools ""`, bactrian stops instead of
running a reader that can act.

## Close the other ways in

The tool protects you only if it is your agent's only way to read the web. In Claude Code, deny the built-in web tools
and any browser tools in your settings:

```json
{ "permissions": { "deny": ["WebFetch", "WebSearch", "mcp__claude-in-chrome", "mcp__Claude_Browser"] } }
```

Shell commands such as `curl` can still read the web. Claude Code's sandbox with an empty `network.allowedDomains` stops
them; see [Configure the sandboxed Bash tool](https://code.claude.com/docs/en/sandboxing).

In Codex, the same goes at the top of `~/.codex/config.toml` (not yet tested). Codex's `workspace-write` sandbox keeps
shell commands off the network by default.

```toml
web_search = "disabled"

[features]
browser_use = false
in_app_browser = false
computer_use = false
```

## What it does not stop

- A page that lies. You get a wrong answer, with the sources listed so you can check.
- Text a page hides from people. The reader sees it, so it can end up in your answer.
- A page deciding `answered`. That one bit is the only thing a page can send your agent.
- Your search query leaving your machine, as with any web search.

## Checks

```bash
python3 test_server.py && python3 test_web.py
```

`web.py` is the first version: a command where a planner model writes a restricted Python plan that an interpreter
runs. `examples/` holds the poisoned pages used to attack both versions.
