# vodscout — Design Spec

## Purpose

A personal tool for finding parts of a Twitch VOD worth checking out, especially
parts not yet watched. It grew from manually downloaded chat logs and
`chat_freq.py`: choose an emote that means something in a familiar streamer's
chat, inspect where many messages contain it, and jump into the VOD.

The main interface is a Textual TUI. It combines chat downloads, watched-range
tracking, and simple message-frequency results. The user supplies the judgment
about which reactions are interesting. The older CLI spike analysis remains
available, but does not drive the TUI.

## Architecture

Three independent legs communicate through files on disk:

- `fetcher.py` acquires chat and VOD metadata.
- `watched.py` records and infers watched ranges.
- `analyzer.py` reads chat and computes frequency windows or legacy spikes.

The front ends are `ui.py` and `cli.py`. Cross-leg orchestration lives in
`actions.py` and `vodlist.py`: the TUI uses `actions.search`, the CLI uses
`actions.analyze`, and both reuse watched operations and VOD-list merging.
Watched inference imports chat-loading helpers from the analyzer; the analyzer
accepts plain ranges supplied by its caller and does not import watched tracking.
There is no shared in-process state between the legs. Textual dependencies stay
in `ui.py`; CLI output and interaction stay in the CLI and its report renderer.

## Fetcher and VOD library

Chat download and streamer-name VOD discovery use Twitch's public GQL endpoint
(`gql.twitch.tv`) with its public web Client-ID. The backend uses `requests`
directly. No private credentials, developer app, external downloader binary,
rendered-page scraping, or video downloading are required.

Users can provide a VOD URL/ID or browse a streamer's recent archived VODs.
Downloads write JSON-lines chat records with timestamps, usernames, message text,
and recognized emote names. Twitch emotes come from message fragments;
BTTV/FFZ/7TV names are resolved at fetch time. Analysis works offline.

A metadata sidecar stores title, publication date, and duration. Refreshing the
VOD list also caches metadata for recent undownloaded VODs, so they appear on
subsequent offline loads. Metadata writes are best-effort and create missing
streamer directories. Incomplete chat pagination fails the download and removes
the temporary chat file rather than leaving a successful-looking partial log.

Local downloaded VODs remain listed even when they disappear from Twitch's recent
list. Successful refreshes prune stale undownloaded metadata cache entries;
network failures preserve the available local list. Deleting a downloaded VOD
removes its chat and watched history but keeps its metadata, allowing it to stay
listed for re-download. A later refresh may prune that metadata if the VOD is no
longer recent.

## Watched-range tracking

Watched history consists of half-open time ranges `[start, end)` per VOD.
Manual edits and chat-inferred ranges supply the data; vodscout does not import
Twitch watch history or automatically observe VOD playback.

Chat inference clusters the user's messages by gaps. The default gap threshold
is 180 seconds, configurable and overridable with CLI `watched --infer --gap`.
Only the outermost session edges receive padding; real breaks stay unwatched.
Empty ranges are discarded. This is an assistive estimate of live viewing:
chat silence is imperfect evidence, and it does not capture catch-up playback.

TUI downloads infer history when none exists and a username is configured.
Opening a VOD also covers chats fetched outside the TUI. Existing watched files,
including explicitly empty ones, prevent automatic re-inference. Explicit
inference merges suggestions with saved ranges.

The TUI's `e` editor accepts one `H:MM:SS-H:MM:SS` range per line; saving replaces
the ranges. CLI `watched` supports display, `--add`, `--infer`, `--edit`, and
`--clear`. Clearing persists an empty file. Saving normalizes and merges ranges.
`m` marks a selected frequency result's 10-second interval with source `moment`;
undo is through the range editor. The playback lead-in does not expand this mark.

## Frequency analysis in the TUI

The VOD screen starts with the emote list focused, favorites first. Selecting an
emote matches its full name case-insensitively, using metadata or exact
whitespace tokens when provider metadata is missing. `OOOO` does not match
`LMAOOOOOOO`. Each matching message counts once, even if it repeats the emote.
The emote list's usage totals count occurrences; result counts count messages.

Results use fixed 10-second windows. Every nonempty matching window appears,
initially highest count first with chronological ties. Users can toggle time or
count sorting. There are no minimum-count or From/Until controls, rolling
baselines, merged runs, or top-N limits in this view.

Unwatched is initially selected. It excludes individual watched messages before
counting, including messages in a partially watched window. All bypasses that
exclusion; a result entirely covered by saved ranges is labeled watched.

`/` reveals optional literal, case-insensitive substring search. Enter applies
it, hides the field, and focuses results; Escape hides it and focuses emotes.
Selecting an emote always uses full-name matching, even after a text search.
Busiest chat counts all messages under the current watched filter and initially
sorts by count.

Enter on a result opens a Twitch link five seconds before its timestamp, clamped
to zero. Displayed timestamps remain the actual window starts. Links use the
system browser; reusing an existing player/tab is not implemented.

Favorites are per-streamer emote names, persisted in `favorites.json`. They pin
emotes in the list without boosting result counts or ranking. Saved names remain
selectable even if absent from the VOD's recognized emote metadata. `f` toggles
the selected emote; Ctrl+F opens a type-to-filter favorite picker.

## Legacy CLI analysis

`vodscout analyze <vod-id>` retains the older rolling-baseline detector. It uses
60-second buckets by default, flags buckets above their trailing baseline,
merges adjacent flagged buckets, and ranks runs by relative increase. Baselines
use up to 30 preceding buckets, require at least three samples, and must average
at least two messages/uses per bucket. This can exclude bursts of rare emotes.

The overall view counts messages and annotates each run with its top emotes.
`--emote <query>` resolves a case-insensitive exact or partial name to the
most-used match, then counts that emote's occurrences, including repetitions
within a message. This matching and counting differs from TUI emote selection.

`--top` limits the report (default 10); `--include-watched` retains watched
moments. Legacy watched filtering checks the peak timestamp after detection,
rather than excluding individual messages before counting. CLI links use the
same five-second playback lead-in as the TUI.

The separate `emotes` command reports usage totals for one VOD or a streamer's
downloaded chats. Neither CLI analysis command changes watched history.

## Interactive navigation

Run `vodscout` or `vodscout browse [streamer]`. Streamer selection uses the
argument, then `default_streamer`, then a prompt. The initial list reads local
chat and cached metadata without a network request.

- `r` refreshes recent VODs; `d` downloads the selected chat.
- Enter opens a downloaded VOD or confirms downloading an undownloaded one.
- The VOD window shows results and emotes side by side. Enter selects an emote
  or opens a result; Tab moves focus.
- `w` toggles All/Unwatched; `s` toggles sorting; `o` shows Busiest chat.
- `/` opens text search; `f` and Ctrl+F manage favorites.
- `m` marks a result watched; `e` edits history; `i` re-infers.
- Escape returns to the list, or first leaves an active text input.
- `x` confirms deletion from either screen. `q` quits, confirming if a download
  is active so cancellation is deliberate.

Downloads and Twitch list refreshes use background workers. The list remains
usable, downloads show progress, duplicate refresh requests are ignored, and
local files are re-read when merging refresh results. Undownloaded rows have no
watched-coverage value.

## Storage and configuration

```
<chat_dir>/
  <streamer>/
    <vod_id>.txt            # JSON-lines chat
    <vod_id>.meta.json      # VOD metadata; alone means undownloaded cache
    <vod_id>.watched.json   # watched ranges and last_updated
    favorites.json         # saved emote names
```

These files are the persistent source of truth. A synced directory, such as the
user's Synology Drive folder, carries chat and watched state across computers.
Vodscout has no separate sync service or conflict-resolution protocol.

Config is TOML at `~/.config/vodscout/config.toml`; first run prompts and writes it:

```toml
chat_dir = "~/SynologyDrive/vodscout"
twitch_username = "..."
default_streamer = "erobb221"

[analysis]
bucket_seconds = 60             # legacy CLI only; TUI windows stay 10 seconds
gap_threshold_seconds = 180     # watched inference
```

The baseline constants live in the analyzer. There are no TUI threshold settings.
For command examples and key bindings, see `README.md`; for dated design changes
and rejected approaches, see `DECISIONS.md`.

## Deferred and out of scope

- A browser companion could seek the existing player and record playback ranges.
  It is deferred until further use establishes the need; integration with the
  existing file-based history and sync would need a design.
- Retiring or replacing the legacy CLI detector is a separate behavior change.
- No combined scoring, semantic emote categories, or additional anomaly signals
  are planned for the current frequency view.
- Full chat clients, rendered Twitch page scraping, and video downloads remain
  out of scope.
