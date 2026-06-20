# vodchat — Design Spec

> Name is not final. Working title.

## What this is

A CLI tool for Twitch VOD chat: download chat logs for VODs, track which
parts of a VOD you've already watched, and analyze chat activity to find
interesting moments — especially ones in parts you haven't seen yet.

Originally grew out of a personal `td` alias wrapping `TwitchDownloaderCLI`,
plus a one-off Python script for finding chat activity spikes and emote
usage in a downloaded chat log.

## Why

Twitch VODs are long. Chat activity (volume spikes, specific emote usage)
is a decent proxy for "something interesting happened here." Existing
tools (TwitchTracker, etc.) do channel-level stats, not "help me find
the good parts of *this* VOD I haven't watched yet."

## Architecture: three independent legs

The project is split into three pieces that share file-based conventions
but don't depend on each other's internals.

### 1. Fetcher

**Job:** get a VOD's chat log onto disk, organized by streamer.

Two paths, depending on whether the user has configured Twitch API
credentials:

- **Path A — no credentials required.** User provides a VOD URL or ID
  directly (already knows it, e.g. copied from twitch.tv). Tool downloads
  chat for that VOD. No Twitch dev app needed — this uses the same
  chat-replay mechanism that established tools (`TwitchDownloaderCLI`,
  `chat-downloader`) already use openly.
- **Path C — requires user's own Twitch API credentials.** User provides
  a streamer name. Tool calls Twitch's official Helix API (`Get Users`,
  `Get Videos`) to list recent VODs, diffs against what's already
  downloaded locally, and lets the user pick which to fetch. Requires the
  user to register their own free Twitch Developer app (client ID +
  secret) — this cannot be baked into the tool and shared across users,
  since a distributed CLI can't keep a secret secret.

  (Path B — scraping the public videos page via headless browser — and
  Path D — using Twitch's unofficial internal GraphQL/gql endpoint — were
  considered and rejected. B is fragile and still circumvents the
  intended access method; D has been explicitly flagged elsewhere as
  against Twitch's ToS.)

If no credentials are configured, streamer-name-based fetch should fail
with a clear message pointing at URL/ID-based fetch as the alternative,
and/or at credential setup.

**Underlying chat download mechanism:** considered two options —
`chat-downloader` (Python package, in-process, no external binary) vs.
shelling out to `TwitchDownloaderCLI` (external binary, what the user's
existing `td` alias already uses). Plan: support both, `chat-downloader`
as the zero-extra-dependency default, `TwitchDownloaderCLI` as a
configurable alternative for output-format consistency with existing
archives.

### 2. Watched-range tracking

**Job:** record which time ranges of a VOD the user has already watched,
so the analyzer can focus on what's left.

Twitch does not expose per-user VOD watch-progress through any API
(official or otherwise) — this isn't obtainable, full stop. So tracking
is necessarily either manual or inferred from the user's own chat
activity in that VOD.

- **Manual ranges.** User enters time ranges they've watched. Primary,
  trustworthy source of truth.
- **Chat-inferred ranges (assistive, not authoritative).** If the user
  provides their Twitch username, the tool can look at their own message
  timestamps in the VOD's chat and infer likely-watched ranges via
  gap-based session segmentation: cluster messages where the gap between
  consecutive messages is below some threshold, split into separate
  ranges where the gap exceeds it. Pad slightly before the first and
  after the last message in each cluster. Threshold should be
  configurable (default in the 8–10 minute range as a starting point).
  This is a *suggestion* the user reviews/edits, not ground truth — chat
  silence doesn't mean not-watching, and it's blind to VODs watched
  without chatting at all.

**Interactive entry point.** A `vodchat watched <vod-id>` command should
drop into an interactive session: show current ranges, offer to add a
manual range, offer to run chat-inference, show a timeline view, confirm
and save. Not purely a flag-driven command — ranges are easier to riff on
interactively than to get right in one CLI invocation.

**Editing.** Should support: interactive add/toggle from the inferred
suggestion list, direct edit of the underlying file via `$EDITOR`, and
(later) quick merge/trim verbs ("extend last range by 10 min", "split at
1:15:00").

**Persistence.** Flat file per VOD, sitting next to the chat log:

```
<chat_dir>/<streamer>/<vod_id>.txt            # chat log
<chat_dir>/<streamer>/<vod_id>.watched.json   # watched ranges
```

```json
{
  "ranges": [
    {"start_seconds": 0, "end_seconds": 6300, "source": "manual"},
    {"start_seconds": 12000, "end_seconds": 14400, "source": "chat-inferred"}
  ],
  "last_updated": "2026-06-20T10:00:00Z"
}
```

### 3. Analyzer

**Job:** take a chat log (+ optional watched ranges) and produce a list
of "interesting moments," ranked, ideally biased toward unwatched parts
of the VOD.

v1 signal types:

1. **Chat-rate spikes.** Bucket messages into fixed time windows, compute
   a rolling baseline (trailing average over some window), flag buckets
   exceeding the baseline by some multiplier. Output: timestamp,
   magnitude ("Nx normal"), a few representative sample messages.
2. **Per-emote spikes.** Same bucket/baseline/multiplier approach, run
   independently per emote — but only for emotes clearing a minimum
   total-usage threshold across the VOD, to avoid noise from one-off
   rare emotes spiking trivially against a near-zero baseline.
3. **Labeled emotes as event markers.** Optional per-streamer config
   mapping specific emotes to semantic labels (e.g. "hype", "sad",
   "rage"), so reports can say "Hype emote surged at 1:23:45" instead of
   a raw emote name. Falls back to raw emote names if unconfigured.

Detectors run independently, then merge into one ranked timeline —
moments hit by multiple signals (e.g. both an overall spike and a
specific labeled-emote spike) should rank higher / read as
higher-confidence, rather than appearing as separate weaker entries.

When watched ranges exist for the VOD, the report should be able to
filter to (or clearly mark) moments outside the watched ranges.

v1 explicitly does *not* include (deferred to later): message-length/caps
anomaly detection, combined numeric scoring beyond simple
multi-signal-agreement boosting.

**Output (v1):** CLI report. Top N moments, each with timestamp, a direct
timestamped VOD link (`https://twitch.tv/videos/<id>?t=<XXhXXmXXs>`), and
why it's interesting. A simple terminal timeline (e.g. ASCII sparkline of
activity, or a row representing watched vs. unwatched vs. flagged
moments) is worth doing here too, since the watched-range timeline and
the analyzer's moment-timeline are likely the same underlying renderer
with different highlighted intervals.

## Shared conventions

- **Directory layout**, configurable root, organized by streamer:
  ```
  <chat_dir>/
    <streamer>/
      <vod_id>.txt
      <vod_id>.watched.json
  ```
- **Config file.** TOML, e.g. `~/.config/vodchat/config.toml`:
  ```toml
  chat_dir = "~/SynologyDrive/chats"
  downloader = "chat-downloader"  # or "twitchdownloadercli"
  twitch_client_id = "..."
  twitch_client_secret = "..."

  [shroud.emotes]
  shroudW = "hype"
  shroudSad = "sad"
  shroudRage = "rage"
  ```
  First run with no config present should prompt interactively and write
  the file, rather than requiring manual setup.
- **Detection thresholds** (bucket size, spike multiplier, gap threshold
  for watched-inference, minimum emote usage count) are hardcoded
  defaults, overridable via the config file. Not exposed as CLI flags in
  v1 — revisit if per-run experimentation turns out to be wanted.

## Commands (rough sketch, not final)

```
vodchat fetch --url <vod-url>          # Path A: download chat for one VOD
vodchat fetch <streamer>               # Path C: list/pick recent VODs (needs credentials)
vodchat fetch <streamer> --all         # Path C: download all undownloaded, no prompt
vodchat list <streamer>                # show what's downloaded locally
vodchat watched <vod-id>                # interactive watched-range editor
vodchat analyze <vod-id>                # interesting-moments report
vodchat analyze <streamer> --all        # analyze everything for that streamer
```

## Explicitly out of scope (for now)

- Browser extension version (possible v2 if the CLI tool proves out;
  would reuse the analyzer's core logic, add DOM injection / manifest
  plumbing on top)
- Full Twitch chat client
- Anything relying on Twitch's unofficial internal GraphQL endpoint
- Downloading actual video files (this tool is chat-only)

## Open questions (intentionally unresolved — figure out while building)

- Exact bucket size and spike multiplier defaults for chat-rate spikes
- Exact gap-threshold default for chat-inferred watched ranges (and
  whether density-weighting before the gap, not just a fixed threshold,
  turns out to be worth the complexity)
- Exact interactive UX/prompts for `vodchat watched`
- Exact terminal timeline rendering approach
- Whether `chat-downloader` or `TwitchDownloaderCLI` should be the
  *actual* default once both are working, vs. just "supported"
