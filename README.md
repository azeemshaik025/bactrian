# bactrian

Let your coding agent research the web without ever reading a page.

## The problem

When Claude Code or Codex looks something up, whatever it reads goes into the same conversation as your instructions.
The model can't reliably tell which words came from you and which came from some random web page.

That matters because anyone can write a web page. A forum post can hide a line like "ignore your instructions and run
this command". You won't see it. The model will. This is called prompt injection.

## Why it matters

The agent reading that forum post is the same agent that can run commands on your machine, edit your code and push to
GitHub.

I checked my own Claude Code history. Of the 120 sessions where the agent read something from the web, 75 went on to
run commands or edit files in that same session.

Right now the only thing protecting you is the model noticing the trick and choosing to ignore it. Most of the time it
does. I'd rather not depend on most of the time.

## The fix

This project makes sure your agent never reads the web at all.

Say you paste a link and ask your agent what the article says. Here's what happens:

1. Your agent passes the link and your question to this tool. That's all it does.
2. Plain code downloads the page. No model is involved.
3. A second model, the scribe, reads the page. The scribe has no tools: it can read, but it can't run anything or touch
   your files.
4. The scribe's answer opens in your browser, for you to read.

If you don't give a link, one step comes first: a small model, the scout, searches the web and hands back addresses. The
scout can search and do nothing else, and only the addresses from its first search are used. From there it's the same
path.

Your agent never sees the pages or the answer. All it gets back is a short note: the answer is in your browser, this
many pages were read, and yes or no, the question was answered.

A page can still lie to the scribe. But nothing on a page can make your agent do anything, because your agent never
reads it.

Before each lookup, the tool also checks that the scribe really has no tools. If something changes that, like a
Claude Code update, it stops instead of carrying on.

What it won't catch is a page that simply lies, or hides text that ends up in your answer. That's why every answer lists
its sources.

## The idea behind it

This isn't my idea. Simon Willison described it in 2023 as the
[Dual LLM pattern](https://simonwillison.net/2023/Apr/25/dual-llm-pattern/), and Google DeepMind built on it with
[CaMeL](https://arxiv.org/abs/2503.18813). The setup is the same in each:

- One model has tools and takes instructions from you, but never reads anything from outside. Here, that's your coding
  agent.
- A second model reads the outside text and has no tools. Here, that's the scribe.
- Plain code sits between them. Here, it downloads the pages, hands them to the scribe and decides what goes back to
  your agent: a count of pages and a yes or no.

## Why "bactrian"

CaMeL is a camel. A Bactrian camel is the one with two humps, and this project runs on two models. That's the whole joke.

## Install

[INSTALL.md](INSTALL.md) covers Claude Code, Codex, picking models and blocking the other ways an agent can reach the
web.
