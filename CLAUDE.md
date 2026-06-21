# CLAUDE.md

Standing context for Claude Code sessions in this repo. Keep this thin —
add a rule here only once you've had to repeat the same correction more
than once. Architecture/design rationale belongs in `SPEC.md`, not here.

## Project

`vodchat` — CLI tool for Twitch VOD chat: download chat logs, track
watched/unwatched time ranges per VOD, analyze chat for interesting
moments. Working name, not final.

Read `SPEC.md` first for the full architecture and rationale. Read
`DECISIONS.md` for a dated log of specific decisions made while building
(thresholds, UX choices, things tried and reverted).

## Stack

- Python
- Config: TOML (`~/.config/vodchat/config.toml`)
- Chat download: `chat-downloader` package by default; optional
  `TwitchDownloaderCLI` (external binary) as an alternative backend
- Twitch Helix API for streamer-name VOD discovery (requires user's own
  client ID/secret — never hardcode credentials into the tool)

## Conventions

- Chat logs and watched-range files live under a configurable root,
  organized by streamer:
  `<chat_dir>/<streamer>/<vod_id>.txt` and
  `<chat_dir>/<streamer>/<vod_id>.watched.json`
- Detection thresholds (bucket size, gap threshold) are overridable via
  config. Spike detection uses a top-N approach — no multiplier threshold;
  see DECISIONS.md. `analyze` has two views: overall chat-volume moments
  (default) and `--emote <name>` for one emote. `top_n` and `--emote` are
  CLI flags; other thresholds are config-only.
- The three legs — fetcher, watched-range tracking, analyzer — should
  stay decoupled. They communicate only through the files on disk, not
  through shared in-process state. Don't reach across that boundary for
  convenience.

## Things to never do

- Never embed/hardcode a Twitch Client ID or Secret in source, tests, or
  examples. Credentials are always user-supplied via config.
- Don't add scraping of Twitch's web pages or calls to Twitch's
  unofficial internal GraphQL/gql endpoint. This was explicitly
  considered and rejected — see SPEC.md.
- Don't add video (not chat) downloading — out of scope.

## Workflow

- This is a side project worked on across multiple sessions, not in one
  sitting. Prefer leaving the codebase in a working, runnable state over
  large unfinished refactors.
- When a real design decision gets made or reversed during a session
  (e.g. a threshold value, a UX detail), add a dated one-line entry to
  `DECISIONS.md` rather than letting it live only in chat history.
- Before committing, check that `DECISIONS.md` and `CLAUDE.md` are up to
  date. Commits are the right moment to flush any decisions made during the
  session.
