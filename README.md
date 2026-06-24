# vodscout

A CLI tool for Twitch VOD chat. It downloads a VOD's chat log, tracks which
parts of the VOD you've already watched, and analyzes chat activity to surface
interesting moments — biased toward the parts you *haven't* seen yet.

Twitch VODs are long. Chat activity (volume spikes, bursts of a particular
emote) is a decent proxy for "something happened here." Channel-level stats
tools won't tell you where the good parts of *this* VOD are — this does, and it
skips the moments you've already watched.

No Twitch account, developer app, or credentials required. vodscout talks to the
same public endpoint the web player uses.

## How it works

Three independent pieces that share files on disk but not internal state:

- **Fetcher** — downloads a VOD's chat log (and a small metadata sidecar),
  organized by streamer. Point it at a VOD URL/ID, or at a streamer name to
  browse and pick from their recent VODs.
- **Watched tracking** — records the time ranges you've watched, so analysis can
  focus on the rest. Ranges are entered manually, or *inferred* from your own
  chat messages in the VOD (assistive — a suggestion you review, not ground
  truth).
- **Analyzer** — buckets chat into time windows, finds where volume (or one
  emote) spiked above its recent normal, and prints ranked moments with direct
  timestamped VOD links.

## Install

Requires Python 3.11+. The project uses [uv](https://docs.astral.sh/uv/).

```bash
# Run from a clone
git clone https://github.com/cwalcott/vodscout
cd vodscout
uv run vodscout --help

# Or install the `vodscout` command onto your PATH
uv tool install .
# (pip works too: pip install .)
```

## First run

On first launch, vodscout prompts for a few settings and writes
`~/.config/vodscout/config.toml`:

```toml
chat_dir = "~/Documents/vodscout"   # where chat logs and sidecars are stored
twitch_username = "..."            # your login — default for `watched --infer`
default_streamer = "..."           # streamer the bare `vodscout` opens to

# Optional — detection thresholds (defaults shown):
# [analysis]
# bucket_seconds = 60              # chat-volume bucket size
# gap_threshold_seconds = 180      # silence that splits inferred watched sessions
```

## Interactive mode

Run `vodscout` with no command (or `vodscout browse <streamer>`) for a full-screen
TUI. Arrow through a streamer's VODs — your downloads merged with their recent
Twitch VODs — and drill into one to see top moments and top emotes side by side.

| Key | Action |
| --- | --- |
| `↑`/`↓` | Move through the list |
| `r` | Refresh the VOD list from Twitch |
| `d` | Download the highlighted VOD's chat (runs in the background) |
| `Enter` | Open a downloaded VOD (or confirm a download for one that isn't) |
| `w` | Toggle the moment list between All / Unwatched |
| `f` | Favorite the highlighted emote (pins it to the top) |
| `/` | Search the VOD's emotes to favorite one |
| `e` | Edit watched ranges inline |
| `i` | Re-infer watched ranges from your chat |
| `Enter` (on a moment/emote) | Open its timestamped link / drill into the emote's own spikes |
| `Esc` | Back |

Downloads are non-blocking — keep browsing while a chat downloads; the row shows
a live progress bar and flips to downloaded when it finishes.

## CLI

The CLI is fully scriptable and covers the same ground:

```bash
# Browse a streamer's VODs (your downloads + recent Twitch VODs, merged)
vodscout vods <streamer>
vodscout vods <streamer> --offline        # local downloads only, no Twitch call
vodscout vods <streamer> --all            # download all not-yet-downloaded VODs
vodscout vods <streamer> --get 1,3        # download those rows from the list
vodscout vods <streamer> --pick           # list, then prompt for which to download
vodscout vods --url <vod-url-or-id>       # download one VOD by URL/ID

# Top emotes (discover what a chat spams)
vodscout emotes <vod-id>                  # for one VOD
vodscout emotes <streamer>                # across all of a streamer's downloads

# Interesting moments
vodscout analyze <vod-id>                 # top moments by chat volume (+ top emotes)
vodscout analyze <vod-id> --emote <name>  # top moments for one emote
vodscout analyze <vod-id> --include-watched   # don't skip watched moments

# Watched ranges
vodscout watched <vod-id>                          # show ranges
vodscout watched <vod-id> --add 1:00:00-1:30:00    # add a manual range
vodscout watched <vod-id> --infer                  # suggest ranges from your chat
vodscout watched <vod-id> --edit                   # edit the ranges file in $EDITOR
vodscout watched <vod-id> --clear                  # remove all ranges

vodscout delete <vod-id>                  # delete a VOD's chat log + sidecars
```

The natural flow: `emotes` (what gets spammed here) → `analyze` (the hype
moments) → `analyze --emote X` (when X specifically popped off).

`--emote` matches forgivingly — case-insensitive and partial, so `lmaoo` finds
`LMAOOOOOOOOOO`, picking the most-used match.

## Files on disk

Everything lives under `chat_dir`, organized by streamer:

```
<chat_dir>/
  <streamer>/
    <vod_id>.txt            # chat log (JSON-lines, stores emote names)
    <vod_id>.meta.json      # VOD title, date, duration
    <vod_id>.watched.json   # watched ranges
    favorites.json          # per-streamer favorite emotes
```

These are plain files — your downloads are the source of truth and are never
deleted by a refresh, even after a VOD ages off or is removed from Twitch.

## Notes

- **No credentials.** vodscout authenticates only with Twitch's *public* web
  Client-ID, the same one the web player uses — never a developer app, OAuth
  token, or secret.
- **Unofficial endpoint.** Chat download and VOD discovery both use Twitch's
  public GQL endpoint (`gql.twitch.tv`), the same one established chat-downloader
  tools and the web player rely on. It isn't an officially supported API, so it
  could change. This tool is for personal use; respect Twitch's Terms of
  Service.
- **Chat only.** vodscout never downloads video — it's chat logs and analysis,
  nothing else.
- Watch-progress isn't something Twitch exposes, so watched tracking is
  necessarily manual or inferred from your own chat — see above.

See [`SPEC.md`](SPEC.md) for the full architecture and rationale, and
[`DECISIONS.md`](DECISIONS.md) for a dated log of decisions made while building.

## License

[MIT](LICENSE)
