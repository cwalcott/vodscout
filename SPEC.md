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

Two ways to point the tool at VODs, neither requiring credentials:

- **By VOD URL or ID.** User provides a VOD URL or ID directly (already
  knows it, e.g. copied from twitch.tv). Tool downloads chat for that VOD.
- **By streamer name.** User provides a streamer name. Tool lists the
  streamer's recent archived VODs, diffs against what's already downloaded
  locally, and lets the user pick which to fetch.

Both use the same access mechanism: Twitch's public GQL endpoint
(`gql.twitch.tv`) with the public web Client-ID — the same endpoint and
the same chat-replay data established tools (`TwitchDownloaderCLI`,
`chat-downloader`) and the web player itself use. Chat download and
streamer-name discovery are two queries against that one endpoint. No
Twitch Developer app, no user-supplied credentials.

At download time the fetcher also writes a `<vod_id>.meta.json` sidecar
(title, publish date, duration) next to the chat log. This lets `list`
show a rich view of downloaded VODs offline, and feeds VOD titles into
analyzer/emote reports. Best-effort — a sidecar write failure never fails
the chat download.

> **History / reversal.** An earlier design did streamer-name discovery
> through Twitch's *official* Helix API, which requires each user to
> register their own dev app (client ID + secret). That was dropped
> (2026-06-21): chat download already runs on the unofficial GQL endpoint,
> so the Helix path was paying a real onboarding cost (dev-app setup) to
> avoid GQL for *just the listing step* — a distinction that bought
> nothing while GQL was already in use for the heavier chat download. GQL
> can list a channel's archived videos with no credentials (it's what the
> channel "Videos" page does), so discovery moved onto it too and the
> credential plumbing was removed. (Scraping the rendered videos *page*
> via a headless browser remains rejected — fragile, and unnecessary when
> the GQL query returns clean structured data.)

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

The core mechanic is shared: bucket messages into fixed time windows,
compute a rolling baseline (trailing average over some window), and flag
buckets exceeding it. This drives two distinct, separately-invoked
analyses rather than one merged timeline (a merged "multi-signal" timeline
was tried and reverted — it read as confusing jargon):

1. **Overall view** (`analyze <vod>`). Moments where overall chat *volume*
   spiked above its recent normal. Each moment is annotated with the top
   emotes used in that window — emotes are the readable signal of *what*
   the moment was (raw word tokens were tried as context and dropped as
   noise). Output: timestamp, magnitude ("N× normal"), top emotes, a
   direct timestamped VOD link.
2. **Per-emote view** (`analyze <vod> --emote <name>`). Moments where one
   chosen emote spiked above *its own* normal rate. No usage threshold —
   the user picked the emote deliberately. Output per moment: timestamp,
   magnitude (× the emote's baseline) and absolute uses (a rare emote can
   jump 9× off a tiny base without mattering, so both numbers matter). The
   `--emote` argument is resolved forgivingly against the emotes present in
   the VOD: case-insensitive, and partial — `lmaoo` finds `LMAOOOOOOOOOO`
   — picking the most-used match and reporting it.

Emotes are central to both, so the chat log stores emote **names** (not
IDs) — the analyzer, the `emotes` command, and reports all speak the same
human-readable language. Both first-party Twitch emotes and third-party
BTTV/FFZ/7TV emotes are counted: the fetcher resolves the channel's
third-party emote sets at download time and records them by name in the
log, so the analyzer needs no network access and treats all emotes
uniformly.

A separate `emotes` command surfaces top emotes by usage for a single
VOD or aggregated across all of a streamer's downloaded chats — read-only
insight into what a chat actually spams. The natural flow is `emotes`
(what gets spammed here) → `analyze` (the hype moments) → `analyze
--emote X` (when specifically X popped off).

*Considered and dropped:* a per-streamer config mapping emotes to
semantic labels ("hype"/"sad"/"rage") for nicer report wording. Too much
manual setup for the payoff; raw emote names are clear enough. A lighter
**per-streamer "favorite emotes" list** — a plain list (not a label map)
used to *boost* ranking of moments involving those emotes — is the likely
next addition here, seeded by the `emotes` command, but is not built yet.

When watched ranges exist for the VOD, the report filters out watched
moments by default (`--include-watched` opts back in).

v1 explicitly does *not* include (deferred to later): message-length/caps
anomaly detection, and any combined cross-signal scoring.

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
      <vod_id>.txt            # chat log (JSON-lines)
      <vod_id>.meta.json      # VOD metadata (title, date, duration)
      <vod_id>.watched.json   # watched ranges
  ```
- **Config file.** TOML, e.g. `~/.config/vodchat/config.toml`:
  ```toml
  chat_dir = "~/SynologyDrive/chats"
  downloader = "chat-downloader"  # or "twitchdownloadercli"
  twitch_username = "..."  # your login, default for `watched --infer`
  ```
  First run with no config present should prompt interactively and write
  the file, rather than requiring manual setup.
- **Detection thresholds** (bucket size, gap threshold for
  watched-inference) are hardcoded defaults, overridable via the config
  file. The spike-baseline constants (`MIN_BASELINE`, baseline window,
  etc.) live in the analyzer, not config. `--emote` is the one analysis
  CLI flag; revisit exposing more if per-run experimentation is wanted.

## Commands (rough sketch, not final)

```
vodchat fetch --url <vod-url>          # download chat for one VOD
vodchat fetch <streamer>               # list/pick recent VODs (no credentials)
vodchat fetch <streamer> --all         # download all undownloaded, no prompt
vodchat list <streamer>                # your downloads + recent Twitch VODs, merged
vodchat list <streamer> --offline      # local downloads only, no Twitch call
vodchat emotes <vod-id>                # top emotes for one VOD
vodchat emotes <streamer>              # top emotes across a streamer's VODs
vodchat watched <vod-id>                # interactive watched-range editor
vodchat analyze <vod-id>                # top moments by chat volume (+ top emotes)
vodchat analyze <vod-id> --emote <name> # top moments for one emote
vodchat analyze <streamer> --all        # analyze everything for that streamer
```

## Explicitly out of scope (for now)

- Browser extension version (possible v2 if the CLI tool proves out;
  would reuse the analyzer's core logic, add DOM injection / manifest
  plumbing on top)
- Full Twitch chat client
- Scraping Twitch's rendered web pages (e.g. a headless browser against
  the channel videos page) — the GQL endpoint returns the same data
  cleanly, so page-scraping buys nothing. (Note: the tool *does* use
  Twitch's public GQL endpoint for chat download and VOD discovery; see
  the Fetcher section.)
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
