# CLAUDE.md

Standing context for Claude Code sessions in this repo. Keep this thin —
add a rule here only once you've had to repeat the same correction more
than once. Architecture/design rationale belongs in `SPEC.md`, not here.

## Project

`vodscout` — CLI tool for Twitch VOD chat: download chat logs, track
watched/unwatched time ranges per VOD, analyze chat for interesting
moments.

Read `SPEC.md` first for the full architecture and rationale. Read
`DECISIONS.md` for a dated log of specific decisions made while building
(thresholds, UX choices, things tried and reverted).

## Stack

- Python
- Config: TOML (`~/.config/vodscout/config.toml`)
- Twitch's public GQL endpoint (`gql.twitch.tv`, public web Client-ID)
  for both chat download and streamer-name VOD discovery — no credentials.
  Single in-process backend (direct `requests`); the earlier
  `chat-downloader`/`TwitchDownloaderCLI` dual-backend plan was dropped
  (see DECISIONS.md 2026-06-20).

## Conventions

- Chat logs and their sidecars live under a configurable root, organized
  by streamer: `<chat_dir>/<streamer>/<vod_id>.txt` (chat log),
  `.meta.json` (VOD title/date/duration — written at fetch time, and also
  cached for recent VODs on a Twitch refresh so undownloaded ones show
  offline; a `.meta.json` without a `.txt` is an undownloaded cache entry),
  and `.watched.json` (watched ranges).
- Detection thresholds (bucket size, gap threshold) are overridable via
  config. Spike detection uses a top-N approach — no multiplier threshold;
  see DECISIONS.md. `analyze` has two views: overall chat-volume moments
  (default) and `--emote <name>` for one emote. `top_n` and `--emote`
  (analyze) and `--gap` (watched --infer, overrides the gap threshold for
  one run) are CLI flags; other thresholds are config-only.
- The three legs — fetcher, watched-range tracking, analyzer — should
  stay decoupled. They communicate only through the files on disk, not
  through shared in-process state. Don't reach across that boundary for
  convenience.

## Things to never do

- Never require a private Twitch secret (dev-app client secret, OAuth
  user token, etc.). The tool authenticates only with Twitch's *public*
  web Client-ID, like the web player — fine to hardcode. The Helix path
  that needed user credentials was removed (see DECISIONS.md 2026-06-21).
- Don't add scraping of Twitch's *rendered web pages* (e.g. headless
  browser against the videos page). The public GQL endpoint returns the
  same data cleanly. (GQL itself is in use and fine — that's how chat and
  VOD discovery both work.)
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
