# bactrian

Let Claude Code research the web without ever reading a page. I built it to learn how Google DeepMind's CaMeL works.

## The problem

When Claude Code looks something up, whatever it reads goes into the same conversation as your instructions.
The model can't reliably tell which words came from you and which came from some random web page.

That matters because anyone can write a web page. A forum post can hide a line like "ignore your instructions and run
this command". You won't see it. The model will. This is called prompt injection.

## Why it matters

The agent reading that forum post is the same agent that can run commands on your machine, edit your code and push to
GitHub.

I checked my own Claude Code history from August 7 to November 15, 2026. Of the 75 sessions where the agent, or a
subagent it started, read something from the web, 58 went on to run commands or edit files in that same session.

Unless you read and approve every command yourself, what protects you is the model noticing the trick and choosing to
ignore it. Most of the time it does. I'd rather not depend on most of the time.

## The fix

This project gives your agent a way to look things up without reading the pages itself.

Say you paste a link and ask your agent what the article says. Here's what happens:

1. Your agent writes a short plan and hands it to this tool. That's all it does.

   ```python
   pages = fetch("https://example.com/article")
   answer = ask_scribe(pages, "What does this article say?")
   show(answer)
   ```

2. Plain code runs the plan one line at a time, starting with downloading the page. No model is involved.
3. A second model, the scribe, reads the page. The scribe has no tools: it can read, but it can't run anything or touch
   your files.
4. `show` puts the scribe's answer on a page in your browser, for you to read.

Your agent never sees the page or the answer. All it gets back is whether the plan ran, or which line stopped it and
why, such as "line 1: no page could be read".

Every value in the plan remembers where it came from: written by your agent, or read from a page. Before each step, a
rule checks that: `fetch` only opens addresses your agent wrote. A page that says "now go read this other page" gets
nowhere, and neither does the scribe repeating it.

A page read this way can't make your agent do anything, because your agent never reads it.

Before each lookup, the tool also checks that the scribe really has no tools. If something changes that, like a
Claude Code update, it stops instead of carrying on.

## Where the camel stumbles

- It guards one door. Out of the box your agent still has its own web tools, and `curl` works fine from a shell. Leave
  those open and your agent can wander off and read pages the old way, and the camel never knows.
  [Try it](#try-it) shows how to close them.
- Pages can lie. The scribe reads carefully and believes what it reads, so a confident wrong page gets written up
  nicely, with a citation. That's why every answer lists its sources.
- Pages can talk to you. A page can hide "tell the reader to run this command", and the scribe may pass it along. It
  can't run anything. You can. If your answer asks you to paste something into a terminal, don't.
- Your agent never gets the answer. That's the point, and also the price: it can't read the docs and then write the code
  for you. You read the answer and decide what to tell it. Paste the whole thing back in and you've carried the page
  into the conversation yourself.
- Your agent has to write a tiny program instead of just asking. It's good at that, but now and then it gets the grammar
  wrong and has a second go.
- It doesn't search. Your agent has to name the page, so "the latest article on Medium" gets you a shrug. Every
  address in a plan is one you or your agent wrote, and that's the point.
- Plain code reads pages like it's 1999. Pages built with JavaScript come back empty, and some sites turn away anything
  that isn't a real browser.
- Your agent does learn a little: whether the plan ran, and if not, which line stopped it. A page could in theory spell
  out a message one failed line at a time. It would need a very patient agent.

## The idea behind it

This isn't my idea. Simon Willison described the
[Dual LLM pattern](https://simonwillison.net/2023/Apr/25/dual-llm-pattern/) in 2023, and Google DeepMind built on it
with [CaMeL](https://arxiv.org/abs/2503.18813). This project is a small CaMeL:

- One model has tools and takes instructions from you, but never reads anything from outside. It turns your request
  into a plan, written as code, before any outside text exists. Here, that's Claude Code.
- A second model reads the outside text and has no tools. Here, that's the scribe.
- An interpreter runs the plan. Every value carries where it came from, and rules check that before each step. Here,
  that's [`camel.py`](camel.py), about 100 lines.

The plan is fixed before any page is read, so a page can't add, skip or reorder steps. Every value is tagged, so a page
can't slip its own address into a step either.

The paper's version does more. CaMeL also tags who is allowed to see each value, and follows values through if/else and
loops. The plans here have no if/else or loops, and there's no private data to guard beyond your question, so this
project leaves both out.

## Why "bactrian"

CaMeL is a camel. A Bactrian camel is the one with two humps, and this project runs on two models. That's the whole joke.

## Try it

You need Python 3.9 or newer and Claude Code, logged in. The scribe runs through `claude -p`, and the rest is Python's
standard library.

```bash
claude mcp add --scope user bactrian -- python3 /path/to/bactrian/server.py
```

The scribe uses `sonnet`. To try another model, add `-e BACTRIAN_SCRIBE=...` after `add`.

bactrian only helps if it's Claude Code's only way to the web. Close the others in `~/.claude/settings.json`, or in a
project's `.claude/settings.json` for just that project:

```json
{ "permissions": { "deny": ["WebFetch", "WebSearch", "mcp__claude-in-chrome", "mcp__Claude_Browser"] } }
```

The shell is another way to the web, through `curl` for example. In the project where I do research, I add `"Bash"`
to that list as well.

Run the checks with `python3 test_server.py`. `examples/` holds poisoned pages for trying attacks by hand.
