# vodscout

A terminal tool for exploring Twitch VOD chat. Download a VOD's chat, keep track
of the parts you've watched, and choose familiar emotes to find reactions worth
checking out. Results show matching messages per 10 seconds, highest count first,
with links into the VOD.

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
- **Analyzer** — counts messages matching an emote or text search in 10-second
  windows, or shows the busiest chat windows. The TUI can exclude watched parts.
  The CLI retains its older baseline-spike reports.

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
# bucket_seconds = 60              # legacy CLI spike bucket size
# gap_threshold_seconds = 180      # silence that splits inferred watched sessions
```

## Interactive mode

Run `vodscout` with no command (or `vodscout browse <streamer>`) for a full-screen
TUI. Arrow through a streamer's VODs — your downloads merged with their recent
Twitch VODs — and open one to choose an emote from the list (favorites first).
Press Enter to see its matching messages in 10-second windows, highest count
first. Emotes match their full names, ignoring case: `OOOO` does not include
`LMAOOOOOOO`. Each message counts once even if it repeats the emote.

There are no minimum or time-bound fields: every nonempty matching window is
available. Toggle time/count sorting and All/Unwatched as needed. Unwatched
excludes watched messages before counting. Busiest chat counts all messages.
For occasional partial text searches, press `/`, type a query, and press Enter;
the search field hides again after submission (or Escape). Text search is a
literal, case-insensitive substring, not regex.

Result links open five seconds before the displayed timestamp (or at the start
of the VOD), giving context for the reaction. Marking watched still records the
result window itself.

| Key | Action |
| --- | --- |
| `↑`/`↓` | Move through the list |
| `r` | Refresh the VOD list from Twitch |
| `d` | Download the highlighted VOD's chat (runs in the background) |
| `Enter` | Open a downloaded VOD (or confirm a download for one that isn't) |
| `w` | Toggle search between All / Unwatched |
| `m` | Mark the selected 10-second result watched (undo via `e`) |
| `f` | Favorite the highlighted emote (pins it to the top) |
| `/` | Reveal the optional partial text search |
| `Ctrl+F` | Find an emote to favorite |
| `s` | Toggle time/count sorting |
| `o` | Busiest chat |
| `e` | Edit watched ranges inline |
| `i` | Re-infer watched ranges from your chat |
| `Enter` (on a moment/emote) | Open its timestamped link / search the full emote name |
| `Esc` | Leave a text field, then return to the VOD list |

Downloads are non-blocking — keep browsing while a chat downloads; the row shows
a live progress bar and flips to downloaded when it finishes.

## CLI

The CLI remains scriptable. Its `analyze` command retains the older baseline
spike analysis; the new frequency search is currently in the TUI:

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

vodscout delete <vod-id>                  # delete chat + watched history; keep metadata
```

For the legacy CLI workflow, use `emotes` to find names, then `analyze` or
`analyze --emote X` for baseline-spike reports.

`--emote` matches forgivingly — case-insensitive and partial, so `lmaoo` finds
`LMAOOOOOOOOOO`, picking the most-used match. Unlike the TUI, this report counts
emote occurrences (including repeats) and filters watched moments by their peak
timestamp.

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
deleted by a refresh, even after a VOD ages off or is removed from Twitch. Set
`chat_dir` to your synced folder to carry chat, favorites, and watched history
across computers; vodscout does not run its own sync service.

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
- Watched tracking uses manual ranges and inference from your live chat activity.
  Automatic playback tracking and reuse of an existing browser player are not
  implemented.

See [`SPEC.md`](SPEC.md) for the full architecture and rationale, and
[`DECISIONS.md`](DECISIONS.md) for a dated log of decisions made while building.

## License

[MIT](LICENSE)
