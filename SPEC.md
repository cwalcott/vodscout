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

**Underlying chat download mechanism:** initially considered two
backends — `chat-downloader` (Python package, in-process) vs. shelling
out to `TwitchDownloaderCLI` (external binary, what the user's existing
`td` alias used) — with a plan to support both. That dual-backend plan
was dropped (see DECISIONS.md 2026-06-20): `chat-downloader` was broken
(stale client ID), `TwitchDownloaderCLI` needs an external binary, and
every alternative ultimately talks to the same GQL endpoint anyway. So
the fetcher now reads chat directly from `gql.twitch.tv` itself (~30 lines
of `requests`), a single in-process backend with no external dependency.
Chat logs are ephemeral, so there's no archive-format-consistency reason
to keep a second backend.

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
  ranges where the gap exceeds it. Pad the outermost edges of each
  cluster slightly (a real break is left fully unwatched; see DECISIONS.md
  2026-06-21). Threshold is configurable (`gap_threshold_seconds`,
  currently 180s — tuned down from an initial 8–10 min guess) and
  overridable per-run via `--gap`. This is a *suggestion* the user
  reviews/edits, not ground truth — chat
  silence doesn't mean not-watching, and it's blind to VODs watched
  without chatting at all.

**Entry point.** `vodchat watched <vod-id>` shows current ranges; edits
are flag-driven: `--add START-END` (manual range), `--infer` (suggest from
your chat, with `--user`/`--gap`), `--edit` ($EDITOR). The originally-
envisioned interactive REPL — drop into a session that offers add/infer/
timeline and confirm-save — was deferred (its exact prompts are an open
question, easier to feel out later); see DECISIONS.md 2026-06-20.

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

## Interactive shell (front end, not a fourth leg)

The CLI is fully retained — it's scriptable, composable, and good for one-off
invocations. On top of it (not replacing it) there's an **interactive shell**:
`vodchat browse [streamer]`, or a bare `vodchat` with no subcommand. The point
is to kill the tedium of stateless re-invocation — re-typing the streamer, then
copying a VOD id, then re-typing it for each follow-up command. The shell holds
that context.

It is a *second consumer* of the three legs' APIs, parallel to `cli.py` — not a
new leg. Session state is just two things: the **current streamer** (its merged
VOD list) and the **selected VOD**. Flow: resolve a streamer (arg →
`default_streamer` config key → prompt) → arrow through the merged local+remote
VOD list → drill into a VOD → act on it (analyze / `--emote` / watched / emotes
/ download / delete).

Implementation is contained in `ui.py`: the three legs never import it, and all
interactive-UI dependencies live there, so swapping the approach later (or going
to a full TUI) is a contained change. Built on **questionary** (prompts — arrow
select, checkbox, confirm, autocomplete) for input and **Rich** for rendering.
A full-screen TUI (Textual/prompt_toolkit) was considered and deferred: the
lightweight sequence-of-prompts model is enough to settle the interaction shape
first; revisit if it proves limiting.

Cross-leg orchestration the two front ends share lives in small, front-end-
neutral modules they both import — not in the legs, and not duplicated:
`vodlist.merged_vods` (the local+remote VOD list) and `actions.analyze` /
`actions.emote_counts` (spike detection + watched-range filtering). These
compose `analyzer` and `watched`, which can't import each other (`watched`
imports `analyzer`), so the glue belongs one level up. The shared *renderer*
for moments is `analyzer.report` (already Rich), called by both front ends.

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
  twitch_username = "..."     # your login, default for `watched --infer`
  default_streamer = "..."    # streamer the bare `vodchat` shell opens to
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
vodchat                                # interactive shell (uses default_streamer if set)
vodchat browse <streamer>              # interactive shell, opened to a streamer
vodchat vods <streamer>                # browse: your downloads + recent Twitch VODs, merged
vodchat vods <streamer> --offline      # browse local downloads only, no Twitch call
vodchat vods <streamer> --all          # download all not-yet-downloaded VODs
vodchat vods <streamer> --get 1,3      # download those rows from the list
vodchat vods <streamer> --pick         # list, then prompt for which to download
vodchat vods --url <vod-url>           # download one VOD by URL/ID
vodchat emotes <vod-id>                # top emotes for one VOD
vodchat emotes <streamer>              # top emotes across a streamer's VODs
vodchat watched <vod-id>                # show watched ranges
vodchat watched <vod-id> --add 1:00:00-1:30:00   # add a manual range
vodchat watched <vod-id> --infer        # suggest ranges from your own chat
vodchat watched <vod-id> --edit         # edit the ranges file in $EDITOR
vodchat analyze <vod-id>                # top moments by chat volume (+ top emotes)
vodchat analyze <vod-id> --emote <name> # top moments for one emote
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

- Exact bucket-size default for chat-rate spikes (currently 60s). Spike
  detection settled on top-N over a rolling baseline — no multiplier
  threshold (see DECISIONS.md 2026-06-20).
- Gap-threshold default landed at 180s but is personal/streamer-dependent,
  not a claim of correctness; open whether density-weighting before the
  gap (not just a fixed threshold) is worth the complexity.
- Exact interactive UX/prompts for `vodchat watched`
- Exact terminal timeline rendering approach
