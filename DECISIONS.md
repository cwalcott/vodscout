# Decisions Log

Dated, one-line-ish entries for specific decisions made while building —
thresholds, UX choices, things tried and reverted. Architecture-level
decisions belong in `SPEC.md`; this file is for the smaller stuff that's
easy to forget the reasoning for.

Format:

```
## YYYY-MM-DD

- Changed X from A to B because C.
```

---

## 2026-06-23 — TUI: blank watched cell for undownloaded VODs + download spinner

- Two follow-ups to the percent-bar change below. (1) The watched column showed
  `░░░░░ 0%` for *undownloaded* VODs — a misleading "0% watched" on something you
  can't have watched yet (watched data only exists once the chat is downloaded).
  It's now blank for undownloaded rows; the bar appears only when downloading
  (progress) or downloaded (coverage). Downloaded-but-unwatched still shows `0%`,
  which is real. (2) At the start of a download the percent sits at 0% during the
  network connect, so you couldn't tell anything was happening. Added a braille
  spinner in the marker column that starts the instant you trigger a download and
  animates continuously until it finishes (then → `⬇`). Driven by a `set_interval`
  that's paused unless something is downloading.
- The watched column is now a **fixed width** (`add_column(width=11)`). It has to
  be: with undownloaded cells blank, the column would auto-size to nothing on an
  all-undownloaded list, then truncate the bar the moment a download started.

## 2026-06-23 — TUI: download progress is a percent bar, not a message count

- The in-flight download indicator showed `↓ N msgs` / `↓ connecting…` in the
  watched/coverage cell. That string is wider than the column (auto-sized to the
  ~10-char coverage bar), so the DataTable truncated it — the count was cut off
  mid-word. Switched to a percent: the `on_progress` hook already reports
  `(completed_seconds, vod_duration)`, so the cell now renders the *same* `▓░`
  bar + percent as watched coverage (via `_coverage_bar(done, total)`), growing
  as the fetched chat reaches through the VOD. Same width as the coverage bar →
  fits the column; the `⏳` marker (vs. `⬇`) is what flags it as a live download.
- `_downloading` now maps `vod_id -> (completed_seconds, total_seconds)` instead
  of a message count, so a table rebuild (refresh) re-renders the right percent.

## 2026-06-23 — TUI: Enter on an undownloaded VOD offers to download it

- Selecting an undownloaded VOD on the list no longer opens an empty "Not
  downloaded yet" window. Instead it pops a `ConfirmDownloadScreen` (modal); a
  confirm kicks off the same background download as `d`. There's nothing to show
  for a VOD whose chat isn't on disk, so the placeholder window was a dead end —
  offering the download is the only useful thing to do there.
- Confirm requires an explicit `y`; `n`/`esc` cancel. Enter is deliberately
  **not** bound on the dialog, so the same Enter that opened it can't immediately
  confirm a download (chosen over Enter-confirms, to avoid an accidental
  double-tap starting a fetch).
- Pre-checks before showing the dialog mirror `start_download`: a VOD already
  downloading just notifies; `--offline` notifies and skips the dialog (can't
  download offline anyway).
- Supersedes parts of the 2026-06-22 entry below: an undownloaded VOD no longer
  opens a window, so VodScreen is now only ever built for downloaded VODs.
  Removed the now-unreachable code — the placeholder branch, the VOD-window `d`
  download action + its `_list_downloading` helper, the `not downloaded` guards
  in the VodScreen actions, and the `switch_screen` in-place rebuild on
  completion (nothing to rebuild now). `d` still downloads the highlighted row
  from the list.

## 2026-06-22 — TUI download: grab a VOD's chat from inside the TUI

- Closed the "downloading is a later slice" placeholder. `d` downloads a VOD's
  chat from **both** the list (the highlighted row) and the VOD window (the open
  VOD). Both funnel through one path (`VodListScreen.start_download`).
- **Non-blocking background download (the whole point — corrected after a first
  cut shipped it as a blocking modal).** First attempt put the fetch in a
  `DownloadScreen` *modal* with a ProgressBar; it worked but a modal captures the
  whole UI, so "in the background" was a lie — you couldn't do anything else
  while it ran. Reworked: no modal. The worker is owned by `VodListScreen` (the
  always-mounted base screen, so it survives the user drilling into a VOD
  window), started via `run_worker(partial(self._do_download, vod_id),
  name=vod_id, group="downloads", thread=True, exit_on_error=False)`. You keep
  browsing — open other VODs, start other downloads — while it runs.
- **Feedback without a modal:** the downloading row shows a live indicator in
  place — a `⏳` marker + `↓ N msgs` count parked in the (otherwise-unused)
  coverage cell of an undownloaded row; flips to `⬇` + coverage on success, or
  reverts on failure. The `on_progress` hook feeds the count (throttled to ~5
  cross-thread UI hops/s). A start/finish toast bookends it. Multiple concurrent
  downloads are fine (one worker each, keyed by `vod_id` in `_downloading`).
- **Completion handled in `on_worker_state_changed`** on the list screen (the
  worker's node, so the message lands there even while the user is in a VOD
  window): SUCCESS → `refresh_row` (flip to downloaded) + toast, and if the user
  is *still* on that VOD's window, `switch_screen` rebuilds it with panes; ERROR
  → revert the row + error toast. `exit_on_error=False` is essential — Textual's
  default would route a failed fetch through `app._handle_exception` and tear
  down the whole TUI; a stray `FileExistsError` is treated as success.
- `fetcher.fetch_by_url` grew an optional `on_progress(done, total)` hook: given
  → drive the caller's progress, render no Rich (a background worker can't let
  Rich write to stdout under the live display); omitted → the CLI's Rich bar,
  unchanged. Factored the streaming loop into `_stream_chat` so both paths share
  it via an internal `report`. Both CLI sites (`_download_one`/`_download_many`)
  call with no hook, so the CLI is untouched.
- **Quit mid-download is cooperative-cancelled (fixed after testing it).** A
  thread worker can't be force-killed, and Python's interpreter exit *joins*
  the default-executor thread — so quitting while a download ran left the
  terminal frozen (TUI gone, no feedback) until the *whole* chat finished, and a
  hard-kill during that freeze orphaned the partial `.tmp`. Fix: `fetch_by_url`
  takes a `should_cancel()` callback (raises `fetcher.DownloadCancelled`, checked
  per page in `_stream_chat` and between the six third-party lookups); each
  download has a `threading.Event`, and `VodListScreen.on_unmount`
  (`cancel_all_downloads`) sets them on app teardown. Quit now aborts within a
  poll/request cycle (~0.2s in a probe vs. ~the full download), and the fetch's
  existing `except BaseException` cleans the `.tmp` — no orphan. Cancellation is
  surfaced as a silent finish (no error toast). There's still no per-download
  "cancel" *button* (parked); offline launch (`--offline`) refuses to download.
- UI stays untested per convention, but validated headlessly with Textual's
  `run_test` pilot before shipping — list + window paths, that it's genuinely
  non-blocking (navigate to another VOD mid-download), the window-stay rebuild, a
  failing fetch (app stays up, error toast, row reverts), and quit-mid-download
  (`on_unmount` sets the cancel flag; a process-level probe confirmed the
  interpreter-exit hang is gone). Fetcher hooks (`on_progress`, `should_cancel`)
  are covered in `test_fetcher` (`_iter_messages` mocked, no network).

## 2026-06-22 — recent-VOD metadata cache (undownloaded VODs show at startup)

- A Twitch refresh now caches a `.meta.json` sidecar for **every** recent VOD,
  not just downloaded ones (`fetcher.write_remote_meta`, written from a
  normalized `list_remote_vods` row; `write_meta` and it now share a private
  `_write_meta_file`). For an undownloaded VOD the sidecar is its only on-disk
  trace, so a later **offline** load surfaces it — the TUI's local-only startup
  (`_populate(offline=True)`, the f815a25 decision) now shows the ~10 recent VODs
  including ones not downloaded, with no network call and no manual `r`.
- New `fetcher.cached_vods(streamer, config)`: recent VODs with a sidecar but no
  `.txt` (downloaded ones are `local_vods`' job). `merged_vods` seeds rows from
  both, then `setdefault`s so a real download always wins over a cache entry.
- **Prune on refresh:** undownloaded cache entries no longer in Twitch's recent
  list are dropped (row + sidecar) so the list stays "your downloads + Twitch's
  recent VODs" and the cache can't grow unbounded. Downloaded VODs are *never*
  pruned (the "don't lose downloads" rule) — they're kept even after aging off
  Twitch. Pruning only runs on a **successful** remote fetch: a network failure
  preserves the cache (and still returns local + cached rows with a note).
- Chose "cache only, no auto-network on launch" (user's call) over a background
  auto-refresh worker: keeps the local-first, zero-network startup of f815a25;
  the cache is what makes that startup rich. Trade-off: the very first launch
  (cold cache) still needs one `r` to populate, and `r` is still how you pull
  newly-aired VODs. Cross-front-end: `vods <streamer>` seeds the same cache, and
  `vods --offline` now shows cached undownloaded VODs too.

## 2026-06-21 — TUI: VOD list loads local-only, `r` refreshes from Twitch

- Resolved the slice-1 follow-up (the list hit Twitch synchronously on launch,
  freezing the UI for a beat). `on_mount` now loads local downloads only (instant,
  no network); `r` triggers the remote merge via `merged_vods`. `--offline` keeps
  `r` local (it just re-reads disk). An empty local list, when remote is available,
  hints "press r to fetch from Twitch".
- Chose this over a background worker + "Loading…" state (user's call): simpler,
  and a deliberate keypress to hit the network fits a local-first tool. The worker
  stays an option if a non-blocking refresh is wanted later.

## 2026-06-21 — TUI slice 4: watched (auto-infer on open + inline editor)

- Built watched tracking in the TUI as designed (see the "watched in the TUI:
  design" note below). Coverage already showed in the list/header since slice 1;
  this adds the editing:
  - **Auto-infer on first open**: opening a downloaded VOD with no `.watched.json`
    (and `twitch_username` set) runs `infer_from_chat` and persists via
    `actions.add_ranges` — only if non-empty, so an empty result writes no file and
    the VOD isn't falsely flagged as having watched data. `i` re-infers on demand
    (merges).
  - **Inline editor (`e`)**: a `WatchedEditScreen` modal (TextArea prefilled with
    current ranges, one `H:MM:SS-H:MM:SS` per line). Save (ctrl-s) is a full
    *replace* — parse each line via `parse_range` → `watched.save`; an empty box →
    `watched.clear`. So deleting a line drops that range, which is exactly why no
    "carve" leg verb was needed. A bad line shows an error and keeps the editor
    open; esc cancels.
  - Any watched change recomputes the row's coverage and re-filters the Unwatched
    moments immediately.
- No new leg code — reuses `watched.py` (`infer_from_chat`/`parse_range`/`save`/
  `clear`/`vod_end_seconds`) and `actions.add_ranges`. UI stays untested per
  convention; the leg functions it calls are covered in `test_watched`.
- Realizes the long-deferred "interactive watched editing" (SPEC's REPL idea) as a
  text box + auto-infer rather than a prompt sequence. With this, the TUI rebuild
  is feature-complete (slices 0–4); `_fixtures.py` is already gone, so the planned
  slice 5 was just this docs pass.

## 2026-06-21 — TUI slice 3: favorite emotes persisted; ranking boost dropped

- Favorites now persist as a per-streamer `<chat_dir>/<streamer>/favorites.json`
  sidecar (new `favorites.py`: `load`/`save` keyed on streamer — no `find_log`,
  since favorites aren't per-VOD). The TUI loads them when a VOD opens; `f` toggles
  and saves; favorited emotes pin to the top of the emote pane. Replaced the
  in-memory `_fixtures` stub, which is deleted.
- Dropped the parked "favorite emotes *boost* moment ranking" idea (SPEC analyzer
  note). It re-introduces the moment-scoring boost already reverted as confusing
  (the `SIGNAL_BOOST` merged-timeline cut), it wasn't part of the redesign's actual
  need (favorites just pin in the emote list + are the thing you drill into), and a
  magnitude boost muddies the clean "overall view = chat-volume ranking" semantic.
  Favorites stay a pure emote-pane affordance: pinned first + one keypress to drill
  into a favorite's own spikes. If it ever comes back, a non-reordering visual mark
  (flag favorite-involving moments without resorting) is the fallback over a boost.

## 2026-06-21 — front end: questionary shell → Textual TUI (slices 0–1)

- Reversed the earlier "questionary + Rich, defer the full TUI" decision (2026-06-21
  shell slice 0+1). The sequence-of-prompts model didn't gel: output scrolls instead
  of holding a view, you can't see moments and emotes together, and there's no
  persistent All/Unwatched toggle. That was exactly the "revisit if it proves
  limiting" trigger logged at the time. Replaced questionary with a full-screen
  **Textual** TUI; dropped the questionary dep, added textual. All interactive deps
  stay confined to `ui.py` (legs untouched), so it was a contained swap as SPEC
  promised.
- UI shape (settled by mocking options with the user): **drill-in** navigation — a
  VOD *list* screen; Enter pushes a full *VOD window* (not a master-detail split);
  the window shows top moments (left) + emotes (right) side by side. Keys: `w`
  All/Unwatched (drives the moment list), `f` favorite emote (pinned first), `o`
  overall, Enter open moment link / drill emote, Esc back, `q` quit.
- Built **stub-first** (slice 0): real layout/navigation/keybindings against a
  throwaway `_fixtures` module, looked at it on a real terminal, then swapped each
  fixture for the real call per slice. Cheap because in Textual the layout *is* the
  work and is data-source-agnostic — only the fake data gets thrown away.
- Slice 1 wired real data: list ← `vodlist.merged_vods`, moments ← `actions.analyze`,
  emotes ← `actions.emote_counts`, emote-drill ← `actions.analyze(emote=)`. Emotes
  were pulled into slice 1 (planned for slice 2) because the emote-drill is coupled
  to the moments pane — leaving emotes fake would hit `EmoteNotFound` on a real VOD.
  Toggle is instant: analyze once with `include_watched=True`, filter watched/all at
  render time (no re-analysis per toggle). Coverage bar = `watched.load` ÷ duration.
  Enter on a moment opens the real timestamped link via `webbrowser`.
- Still stubbed after slice 1: favorite *persistence* (in-memory `_fixtures`) → slice
  3 sidecar; watched-editing → slice 4. Known follow-up: `merged_vods` runs
  synchronously on launch (brief UI freeze on slow networks) — move to a Textual
  worker with a loading state.

## 2026-06-21 — watched in the TUI: design (slice 4, not built yet)

- Settled how watched will work before building it: (1) coverage in the list +
  window header; (2) **auto-infer on first open** of a downloaded VOD with no
  `.watched.json` when `twitch_username` is set — persist if non-empty (skip persist
  when empty, so the VOD isn't falsely flagged as having watched data); `i` re-runs
  it; (3) manual correction = an **inline editable text box** (`e`) prefilled with
  current ranges one-per-line `H:MM:SS-H:MM:SS`, saved by splitting lines through
  `watched.parse_range` → `watched.save` (source `manual`). The text box subsumes
  add/remove/clear/split, so **no new watched-leg "carve" function is needed**.
- Dropped the interactive timeline/scrub-marking idea (again): with text-editing as
  the correction surface, the timeline lost its only justification (being the marking
  surface) — consistent with the "timeline tried, passed" note below. Coverage % in
  the header is enough.
- Softens SPEC's "infer is a suggestion you review/confirm before saving" stance for
  the TUI auto-on-open path only (persists silently because it's immediately visible
  and one keystroke to edit). The CLI's `watched --infer` keeps its confirm prompt.

## 2026-06-21 — terminal timeline: tried, decided to pass

- Prototyped the SPEC v1 "terminal timeline" as a one-line volume sparkline
  (`▁▂▃▄▅▆▇█` heights = chat volume per slice, watched slices dimmed, top-N
  moment slices colored) shown atop the `analyze` report plus a standalone
  `timeline` command/shell action. Built it end-to-end (renderer in `analyzer`,
  `AnalysisResult` timeline fields, both front ends, tests) and looked at it on
  real VODs.
- Decided NOT to keep it — reverted before commit. It read as decorative, not
  functional: the `analyze` report already answers the tool's core question
  ("where do I jump?") with ranked, timestamped, clickable links, and you act on
  those links, not on the shape of a bar. Its one semi-useful angle —
  watched-coverage at a glance — is largely redundant too, since `analyze`
  already filters to unwatched moments by default, so the bar just restated
  existing output visually rather than enabling a new decision. Not worth the
  surface area for a tool we want to keep lean.
- The "exact terminal timeline rendering approach" open question stays listed in
  SPEC: this is "tried and passed for now," not "ruled out forever." If a
  timeline comes back, it should earn its place by doing something the moment
  list can't (e.g. being the interactive surface for *marking* watched ranges),
  not just visualizing what's already printed.
- Where the next real value likely is instead (parked, more substantive than the
  timeline): the cross-VOD "what should I watch next?" digest (one ranked line
  per downloaded-but-unwatched VOD — answers a question the tool currently can't,
  since it only helps *within* a chosen VOD), and the per-streamer favorite-emote
  ranking boost (changes the actual ranking, not just presentation).

## 2026-06-20

- Used tomlkit instead of tomllib + tomli-w: single dep, preserves user comments/formatting when writing back to config.
- Added Rich for terminal output: analyzer report and watched interactive session will need it; easier to add now than retrofit.
- Skipped pydantic: config is shallow (flat keys + per-streamer emote dicts), a dataclass is sufficient.
- Skipped mypy: solo side project, annotation friction not worth the bug-catch benefit at this scale.
- Dev tooling: uv for venv/install workflow, ruff for lint+format (replaces black/isort/flake8).
- Default chat_dir in interactive setup is ~/Documents/vodchat: visible in Finder, natural on macOS; user with a sync folder (e.g. Synology) will override it.
- Chat download uses Twitch's unofficial GQL endpoint directly (DIY, ~30 lines of requests): chat-downloader was broken (stale client ID), TwitchDownloaderCLI requires an external binary, and all alternatives use the same GQL endpoint anyway. ToS risk is the same regardless of implementation layer.
- Dropped dual-backend (chat-downloader + TwitchDownloaderCLI) design: files are ephemeral, no archive-consistency requirement, one backend is simpler to maintain.
- Chat log format: JSON-lines in .txt files. One JSON object per line: {"time": <int seconds>, "user": <login>, "msg": <text>, "emotes": [<emote_id>, ...]}. "emotes" key omitted when empty. Third-party emotes (BTTV/FFZ/7TV) appear as plain text in "msg" since they're not in Twitch's emote system.
- Analyzer uses top-N approach instead of fixed multiplier threshold: flag any bucket strictly above its trailing baseline (MIN_BASELINE=2.0 floor, MIN_BASELINE_SAMPLES=3, BASELINE_BUCKETS=30 window), merge adjacent flagged buckets, return top N by magnitude. Removed spike_multiplier from Config. Fixed threshold returned nothing on test VODs; top-N always surfaces the most relatively active moments.
- Top tokens instead of sample messages in report: show 5 most frequent whitespace-split tokens from all messages in the merged spike window, with counts. Sample messages were emote spam and gave no context about why chat spiked.
- Report format: compact 2-line per moment (metadata + tokens on line 1, link on line 2). --no-tokens flag collapses to 1 line. Dropped Rich table — columns were truncating URLs.
- Default top_n=10 for `analyze` report; --top N CLI flag to override.

## 2026-06-20 — watched-range leg

- `watched.load` returns empty ranges (not an error) when the `.watched.json` file is missing: a VOD with no recorded watched data is the normal starting state, not a failure.
- `save` normalizes on write: sort ranges by start, merge overlapping/adjacent. On a merged span, manual source wins over chat-inferred (manual is the trustworthy source of truth per SPEC).
- `watched.load`/`save` take `(vod_id, chat_dir)` — dropped the `streamer` arg from the stub signatures; the streamer is derived by reusing `analyzer.find_log` (one place owns "find this vod_id under any streamer").
- `infer_from_chat` gap-threshold default resolved to 600s (config `gap_threshold_seconds`), not the stub's 540 — CLI passes the config value through; the function keeps a default for direct/test calls. Gap of exactly the threshold does NOT split (uses strict `>`).
- `PAD_SECONDS = 120`: each inferred cluster is padded 2 min on each side (start clamped at 0). Chat lags the moment and people watch before/after typing. Assistive only — `watched --infer` prints suggestions and asks to confirm before merging.
- Watched UX is non-interactive for now (`watched <vod>` prints; `--add START-END`, `--infer --user`, `--edit` via $EDITOR). Deferred the interactive REPL session (SPEC's menu entry point) — its exact prompts are an open question, better felt out later. Removed the `interactive_edit` stub.
- Analyzer integration: `mark_watched(moments, ranges)` takes plain `(start, end)` tuples, not the watched dataclasses — analyzer stays decoupled, reads watched data only through the on-disk file (loaded by cli.py). Range membership is half-open `[start, end)`.
- `analyze` report dims + appends `[watched]` to moments inside watched ranges.

## 2026-06-20 — watched-leg follow-ups (user feedback)

- `analyze` now hides watched moments BY DEFAULT (unwatched-only is the whole point); `--include-watched` opts back into the full list with `[watched]` tags. Replaced the earlier `--unwatched` flag, which had the default backwards. For a VOD with no watched data nothing is filtered, so fresh-VOD behavior is unchanged.
- Added `twitch_username` config key (top-level). `watched --infer` uses it as the default user; `--user` still overrides. Prompted for (optional) in interactive setup. This was the deferred item from the plan — pulled forward on user request.
- Open-ended `--add` ranges: `START-end` / `START-` mark to the VOD end; `-END` / `start-END` mark from 0. "End" resolves to the last chat-message timestamp via `watched.vod_end_seconds` (we don't know true VOD length, and the analyzer never flags past the last message anyway). `parse_range` takes a lazy `end_resolver` so the log is only loaded when an open end is actually used.
- Implemented `list <streamer>`: lists local VOD IDs newest-first (numeric-descending sort, since VOD IDs grow over time), appends `[watched]` when a `.watched.json` sits next to the log, prints a trailing count. Missing streamer dir / no logs both error with the same "No downloaded VODs" message.
- Dropped `analyze <streamer> --all` (was an unimplemented stub from SPEC's command sketch). Looping the per-VOD report over every log just produces a wall of N×top-N moments, which fights the analyzer's purpose (decide what to watch in *one* VOD at a time). The genuinely useful cross-VOD feature — a "which unwatched VOD should I watch next?" digest (one ranked line per VOD, not N full reports) — is a different, design-heavier thing; parked as a possible later feature, not built. Removed the `--all` flag/branch; `analyze` now takes a `vod_id` only.

## 2026-06-20 — per-emote spikes + emotes command

- Chat log now stores emote **names**, not IDs (reverses the earlier "emote_id" format decision). Per-emote spike detection, the `emotes` exploration command, and the report are all name-facing; IDs would force a persisted ID→name lookup table for no benefit. The name is already in the GQL fragment text, so it's a one-line fetcher change (`f["text"]` instead of `f["emote"]["emoteID"]`). Files are ephemeral — existing logs need a re-fetch to get name-based emote analysis (old logs show numeric IDs as "emote names" until then).
- Dropped semantic emote labels (the `[streamer.emotes]` name→label config, SPEC signal 3) entirely: too much manual setup for nicer report wording. Removed `Config.emotes`, its load/save plumbing, and `_KNOWN_KEYS`. Favorites (a plain per-streamer emote *list* that boosts ranking, seeded by the `emotes` command) is the intended next step — parked, not built.
- Two separate analyses instead of one merged timeline. An earlier cut merged chat-volume spikes and auto-discovered per-emote spikes into a single ranked timeline (multi-signal `_Hit`/`_merge_hits`/`SIGNAL_BOOST` scoring); it read as confusing jargon ("chat-rate" + two unlabeled rows of emote-ish strings). Reverted before commit. Now:
  - `analyze <vod>` — **overall view**: moments where chat *volume* spiked, each annotated with the top emotes used in that window (emotes-only context, no raw-token line — tokens were the confusing part). No "signals" concept surfaced to the user.
  - `analyze <vod> --emote <name>` — **per-emote view**: moments where one chosen emote spiked above its own normal rate. No usage-threshold gate (the user picked the emote deliberately, so `min_emote_count` is gone). Shows both `N×` (relative to the emote's baseline) and `N uses` (absolute) — a rare emote can jump 9× off a tiny base without mattering, so both numbers together are the honest signal.
- Kept `_find_runs(counts)` as the shared bucket/baseline/run-merge core; `detect_spikes` runs it over message counts, `detect_emote_spikes` over one emote's per-bucket counts. Dropped `_Hit`, `_merge_hits`, `SIGNAL_BOOST`, `MERGE_BUCKETS`, and `Moment.signals/score`. `Moment` now carries `top_emotes` (overall view) or `count` (per-emote view).
- Report: one function, `emote=None` → overall (timestamp, magnitude, top emotes, link), `emote=<name>` → per-emote (timestamp, magnitude, uses, link). Dropped the `--no-tokens` flag (no more token line). Discovery flow is `emotes` → `analyze` → `analyze --emote X`.
- `emotes <target>` command: numeric `target` → that VOD (via `find_log`); otherwise a streamer name → aggregate `count_emotes` over every `*.txt` under the streamer dir. `--top` (default 20).
- `--emote` matching is forgiving (`resolve_emote`), resolved against the emotes actually in the VOD: case-insensitive exact wins outright, else substring either direction (so `lmaoo`, `lmao`, and over-typed `lmaooooooo` all find `LMAOOOOOOOOOO`). Multiple matches → pick the most-used and print the candidates (never a silent guess). Chose substring + usage-ranking over a fuzzy-match lib (e.g. difflib/Levenshtein): no dependency, and predictable/explainable beats "clever" here. difflib typo-tolerance (`kapa`→`Kappa`) is a possible later add on top.
- Third-party emotes (BTTV/FFZ/7TV) are now captured too — supersedes the earlier "they appear as plain text and aren't counted" note. They're not in Twitch's emote system, so the fetcher pulls the channel's emote sets (global + channel-specific) from each provider's public API once per VOD and recognizes them as whole-token text matches in each message (`_scan_third_party`), appending matches to `emotes`. First-party still come from GQL fragments; no double-count since their names are scanned only against the third-party set.
- Done at fetch time, not analyze time: keeps the analyzer/`emotes` command file-only and offline (no network, no need to resolve a streamer→Twitch-ID). The broadcaster's Twitch user ID now comes from the GQL video metadata (`owner{id}`), which the provider APIs key on.
- Each emote source (3 providers × global + channel = 6 fetches) is independently best-effort: a provider being down — or, very commonly, not having the channel registered (channel endpoint 404s) — must not drop the other sources, including that provider's globals, nor abort the chat download. (First cut wrapped each provider as a unit, so a channel 404 silently discarded that provider's globals; split to per-source guards.)
- Another reason existing logs need a re-fetch (beyond the ID→name switch): older logs predate third-party capture.

## 2026-06-21 — watched-inference: carve/bridge instead of cluster-padding

- Reworked `infer_from_chat` after real-VOD feedback: the old symmetric `PAD_SECONDS=120` padded *every* cluster edge, so it bled 2 min into each side of genuine long breaks (a 25-min away-from-keyboard gap showed ~4 min as watched) and — because two 120s pads overlap — secretly enforced a ~4-min minimum hole regardless of threshold. New model: a silence longer than the threshold is a real break, left *fully* unwatched; interior range boundaries sit on the messages themselves with no bleed. Short silences are still bridged.
- `PAD_SECONDS` (120, every edge) → `EDGE_PAD_SECONDS` (30, outermost edges only). The lead/trail cushion now applies only before the very first message and after the very last — it extends into VOD start/end, never into a break.
- `gap_threshold_seconds` default 600 → 120, then 120 → 180 after trying it. 120 (2 min) was too fragmented on a real VOD; the right value depends heavily on personal chat cadence with a given streamer, so 180 (3 min) is a less-twitchy default, not a claim of correctness. Still config-overridable.
- Added a `--gap <seconds>` flag to `watched --infer` (overrides config for that run). The threshold already flowed through `infer_from_chat` as a parameter; this just wires a CLI option to it so the tune-and-look loop (`--infer --gap 240`, look, `--gap 300`, look) doesn't require editing config.toml each time. Settle on a value, then bake it into config as the personal default. No flag without `--infer` guard — it's silently ignored otherwise (help text scopes it to --infer).

## 2026-06-21 — interactive shell (slice 0+1: skeleton + navigation)

- Decided to add an interactive shell on top of the CLI (not replacing it): the CLI stays scriptable; the shell exists to kill stateless-re-invocation tedium (re-typing streamer + vod id per command). It's a second consumer of the leg APIs, parallel to cli.py — not a new leg. Session state = current streamer + selected VOD.
- Library choice: **questionary + Rich**, after surveying the field. Considered and rejected for now: a full TUI (Textual/prompt_toolkit) — deferred, the lightweight sequence-of-prompts model is enough to settle the interaction shape first (same "feel it out before specifying" reasoning that deferred the `watched` REPL). InquirerPy — its native fuzzy prompt was the only edge over questionary, but it's stale (last release 0.3.3, 2022) vs. questionary being actively maintained and more widely used; the emote-picker fuzzy need is already covered by the existing forgiving `resolve_emote` + questionary's `autocomplete`. fzf — rejected, external binary cuts against the zero-setup principle.
- All interactive deps confined to `ui.py`; the three legs never import it, so the choice is swappable later without architectural churn.
- Extracted the merged local+remote list builder from `cli._vod_list` into `vodlist.merged_vods` so both front ends show the same list from one place. `cli.py`'s `_render`/`_download_*` stay put (click-specific output); `ui.py` renders with Rich.
- Entry point: `@click.group(invoke_without_command=True)` so bare `vodchat` launches the shell; explicit `vodchat browse [streamer]` too. Streamer resolves arg → `default_streamer` config key → prompt. `ui` is lazy-imported inside the command callbacks so non-interactive commands (`analyze`, etc.) don't pull questionary/prompt_toolkit at startup.
- Added `default_streamer` config key (top-level), written by save and prompted for (optional) in interactive setup.
- Slice 1 scope: navigation only (list → arrow-select VOD → detail view → back/quit). Per-VOD actions (analyze/watched/emotes/download, plus new delete + `watched --clear`) are later slices, each landing as both a shell action and a real CLI command so nothing is trapped behind interactivity. Keyboard/prompt-wording decisions deferred until there's real use to react to.
- First UX tweaks after a real run: (1) downloaded rows are greyed in the select list (questionary renders a `(style, text)` tuple as formatted text — `fg:ansibrightblack`), mirroring the dimmed rows in `vodchat vods`; a plain str stays default-colored. (2) Long VOD titles truncated to `_TITLE_MAX=45` chars + ellipsis. (3) `q` quits the shell from any select menu — questionary has no built-in quit key, so a `_select` helper adds a `q` binding to the underlying prompt_toolkit app (`event.app.exit(result=_QUIT)`); the explicit Quit choice stays for discoverability.

## 2026-06-21 — interactive shell slice 2: VOD actions

- Wired the read-only analyses onto the shell's VOD detail view: Analyze (chat volume), Analyze an emote, Top emotes, Watched ranges (view). The action menu loops on the VOD so several analyses can be run before going back; output prints above, the menu reappears below.
- Extracted the analyze orchestration out of `cli.analyze` into a new `actions.py` (`analyze` → `AnalysisResult(moments, emote, emote_matches)`, raising `EmoteNotFound`; plus `emote_counts`). Both front ends call it. Why a new module and not `analyzer`: it composes analyzer + watched, and `watched` already imports `analyzer`, so analyzer can't import watched (cycle) — the cross-leg glue lives one level up, same as `vodlist`. `analyzer.report` (already Rich) stays the shared moments renderer, called directly by `ui`.
- `cli.analyze` and the `emotes` VOD branch refactored onto `actions`; CLI behavior preserved (incl. the `EmoteNotFound` → "See `vodchat emotes <id>`" hint, kept CLI-side via the typed exception).
- Actions are offered only for downloaded VODs (they need the chat log); a not-yet-downloaded VOD shows just Back/Quit with a "download to analyze — coming soon" note. Download lands in slice 3.
- Emote picker: `questionary.autocomplete` seeded with the VOD's emotes (most-used first), `match_middle=True` so `lma` finds `LMAOOOOOOOOOO`; pulled forward from the slice-4 polish list on user request. Whatever is typed still goes through the forgiving `resolve_emote`, so a free-typed name works too.
- Analyze options in the shell use defaults only (top 10, unwatched-only) — no per-run prompts for count/include-watched, to keep it a single keypress. The CLI still exposes `--top`/`--include-watched` for control.
- Tests: `test_actions.py` covers `analyze` (overall, per-emote, watched-filter default + opt-in, missing log, EmoteNotFound) and `emote_counts`. `ui` rendering stays untested (needs a TTY), consistent with `cli`.

## 2026-06-21 — interactive shell slice 3: download / delete / clear

- Shell can now acquire and remove VODs, not just read. VOD view: a not-yet-downloaded VOD offers "Download chat" (reuses `fetcher.fetch_by_url`, flips the row to downloaded in place so the analysis actions unlock without leaving the view); a downloaded VOD offers "Delete VOD…" (confirm → `actions.delete_vod`, then back to the list so it re-merges) and "Clear watched ranges" (only shown when the row has watched data). Streamer list gained "⬇ Download all not-downloaded (N)".
- New verbs are CLI commands too, so nothing is shell-only: `vodchat delete <id>` (`-y`/`--yes` skips the confirm; find_log precheck gives a clean error before prompting) and `vodchat watched <id> --clear`.
- `actions.delete_vod(vod_id, config)` removes the chat log + `.meta.json` + `.watched.json`, returning the paths actually removed (sidecars are optional). Lives in `actions` (not `fetcher`) because it's cross-cutting and both front ends use it; locates the VOD via `analyzer.find_log` so "find this id under any streamer" stays in one place.
- `watched.clear(vod_id, chat_dir)` deletes the `.watched.json` (returns True if one existed). Clearing = "no watched data": `load` already treats a missing file as empty ranges, so removal returns the VOD to pristine state — cleaner than writing an empty-ranges file. This is the parked `watched --clear` item, now built.
- Delete confirmation defaults to NO (`questionary.confirm(default=False)` / `click.confirm`); download-all defaults to YES. Destructive = guarded, additive = one keypress.
- Drive-by: `analyzer.find_log`'s "not found" message still said `vodchat fetch --url` (renamed to `vodchat vods --url` when fetch+list merged). Fixed.
- Tests: `delete_vod` (removes all sidecars + returns paths, only-existing-sidecars, missing-log raises) and `watched.clear` (removes file + load-treats-missing-as-empty, idempotent false). Verified the `delete`/`--clear` CLI wiring end-to-end with a CliRunner smoke test.

## 2026-06-21 — interactive shell: watched submenu (manual add)

- "Watched ranges" in the VOD view is now a submenu (View / Add a manual range / Clear all / Back) instead of a one-shot view, and "Clear" moved off the top-level VOD menu into it — all watched operations grouped in one place. Closes the gap that the shell could previously only *list* watched ranges, not edit them.
- Scope: started with manual add only, then (same session, user reversed) added Infer-from-chat and `$EDITOR` editing too, so the shell's watched submenu is now View / Add / Infer / Edit / Clear — full parity with the CLI's `watched` ops. Add reuses `watched.parse_range` (same `START-END` / open-ended forms, incl. `…-end` via `vod_end_seconds`). Infer mirrors `watched --infer`: username from config (prompted only if unset), gap from the config threshold with no per-run prompt (defaults-only stance — use the CLI's `--gap` to tune); shows suggestions, confirms before merging. Edit uses `click.edit` on the `.watched.json` and re-`load`s to validate — the one place the shell touches click (for `$EDITOR` launch), kept to a single commented import since questionary/prompt_toolkit has no editor-launch primitive. Factored a shared `_render_ranges(ranges, header)` used by both the saved-ranges view and the infer preview.
- New shared `actions.add_ranges(vod_id, config, ranges)` = load → extend → save (save normalizes/merges). Both the shell's add action AND the CLI's `watched --add` / `--infer` merge step now go through it, removing the duplicated load/append/save dance from cli.
- The submenu propagates `q` (quit-shell) up through `_vod_view` so the "q quits the shell from anywhere" rule holds one level deeper; plain Back returns to the VOD view.
- Test: `actions.add_ranges` (overlapping ranges merge on save + persist). CLI `watched --add` smoke-tested through the refactor.

## 2026-06-21 — docs sync + removed dead `downloader` config

- Doc cleanup pass: SPEC.md/CLAUDE.md still described the dual-backend (`chat-downloader`/`TwitchDownloaderCLI`) chat-download plan that was dropped 2026-06-20 for direct GQL; the command sketch still listed the interactive `watched` editor and the removed `analyze <streamer> --all`; the gap-threshold default still read "8–10 min" (now 180s) and an open question still mentioned a "spike multiplier" (now top-N). All brought in line with the code.
- Removed the `downloader` config option entirely (Config field, `_DOWNLOADERS`, load/save, and the setup-interactive prompt). It was dead: the fetcher always downloads chat directly from GQL and never read `config.downloader`, so setup was asking users to pick a backend that did nothing. Existing config files keep the key harmlessly; it's ignored.

## 2026-06-21 — merged `fetch` + `list` into one `vods` command

- Collapsed `fetch` and `list` into a single `vods <streamer>` command. After the metadata-sidecar rework they shared almost all their code (both build the merged local+remote view); keeping two commands meant two ways to see the same list. One place now both shows VODs and downloads them.
- Shape A (show-by-default, download-is-explicit), chosen over Shape B (always prompt). Bare `vods <streamer>` is a read-only merged listing (numbered, newest-first, `[downloaded]`/`[watched]` tags) ending in a hint when there are ungrabbed VODs. Downloading is opt-in: `--all` (every undownloaded), `--get 1,3` (by list number), `--pick`/`-i` (interactive prompt — the old `fetch` UX, now opt-in), `--url <x>` (one-off by URL/ID). Rationale: keeps the friction-free, scriptable, offline-capable glance the `list` rework introduced; downloading shouldn't be a prompt you dismiss on every run.
- Named `vods`, not `fetch`/`list`: a noun that lists-by-default and acts-with-flags (cf. `git branch`/`git remote`), and parallel to the existing `emotes <streamer>` noun-command. `fetch` mis-sells a default that mostly lists; `list` undersells one that also downloads. Dropped both old names (no aliases — still in dev, cheap to retrain muscle memory).
- `--offline` is list-only and errors if combined with a download flag (you can't download offline anyway). Row numbers for `--get`/`--pick` index the displayed (merged, sorted) list; picking an already-downloaded row is a silent no-op. A failed VOD mid-batch is reported and skipped, not fatal.
- Refactored cli into `_vod_list` (merge → ordered rows + login + note), `_render`, `_download_one`, `_download_many`. `fetcher.downloaded_ids`/`undownloaded_vods` are now unused by the CLI (only their tests) but kept as tested helpers.

## 2026-06-21 — `list` reworked: local+remote merge, metadata sidecars

- `list <streamer>` is no longer a bare local ID dump. It now merges two sources: your local downloads (source of truth) and — unless `--offline` — a default Twitch check for recent VODs. Output is newest-first with `[downloaded]` / `[watched]` tags; not-yet-downloaded recent VODs show plain (available to grab). Each row: date, duration, id, title.
- Local is authoritative and never deleted/hidden: a downloaded VOD that's aged off Twitch's ~10-VOD recent window — or been removed from Twitch entirely — still shows, rendered from its sidecar. Remote only *adds* (new VODs) and *tops up* metadata; it never prunes. (User requirement: VODs get removed and they don't want to lose what's downloaded.)
- Remote-by-default with graceful fallback: a network failure prints local rows plus a "Couldn't reach Twitch" note rather than erroring. `--offline` skips the call entirely. Only errors when there's nothing local AND nothing remote.
- Metadata sidecar `<vod_id>.meta.json` (id, title, created_at, duration_seconds) written by the fetcher at download time (`write_meta`), best-effort (a sidecar failure never fails the chat download). This is what makes the offline/local view rich instead of ID-only. `_video_metadata` gained `publishedAt` to populate `created_at`.
- No legacy backfill of pre-existing downloads (decided with user: still in dev, little downloaded, fine to wipe and re-fetch). So no per-VOD metadata-lookup path; sidecar-less local VODs would just show as ID-only with blank title/date, but in practice everything going forward has a sidecar.
- `list_remote_vods` now returns `duration_seconds` (int) instead of a pre-formatted `duration` string; `_format_duration` formats at display time (used by both `list` and the `fetch` picker). One source of truth for duration.
- Follow-up parked: `fetch`'s own interactive listing now overlaps with `list`; intent is to slim `fetch` toward pure acquisition once this settles. Not done yet.

## 2026-06-21 — streamer-name discovery: Helix → GQL (drop credentials)

- Replaced the Helix-based VOD discovery with a query against the same public GQL endpoint already used for chat download. `list_remote_vods(streamer)` now sends a raw (non-persisted) GraphQL query — `user(login){videos(type:ARCHIVE, sort:TIME)}` — with the public web Client-ID, no auth. Removed `_app_token`, `_helix_get`, `_HELIX_URL`, `_OAUTH_URL`, and the OAuth client-credentials dance.
- Dropped `twitch_client_id` / `twitch_client_secret` entirely: the Config fields, load/save plumbing, and the interactive-setup prompts. They were needed *only* for the Helix listing step. Existing config files keep the keys harmlessly; they're just ignored now.
- Why reverse the earlier Helix decision (and SPEC's Path D rejection): chat download already runs on the unofficial GQL endpoint, so keeping Helix for *just discovery* charged every user a Twitch dev-app registration (the biggest onboarding friction in the tool) to avoid GQL for one query — while GQL was already in use for the heavier download. "ToS risk is the same regardless of layer" (the chat-download note) applies here too. Net: zero-setup `fetch <streamer>`.
- Raw GraphQL query, not a persisted-query hash: Twitch accepts the full query, so there's no second hardcoded hash to rotate-break like `_CHAT_HASH`. Switch to the persisted hash only if raw queries ever get refused.
- GQL fields normalized to the shape `cli._fetch_by_streamer` already consumed: `id`, `title`, `user_login` (from top-level `user.login`, canonical/lowercased — same diff key as before), `created_at` (← `publishedAt`), `duration` (← `lengthSeconds`, formatted `H:MM:SS` via `_format_duration` instead of Helix's `"6h33m10s"` string). Unknown streamer → `data.user is null` → `ValueError("Streamer 'x' not found.")`, which cli maps to a clean error (same as before).

## 2026-06-20 — Path C (streamer-name fetch via Helix)

- App access token is minted per invocation (client-credentials flow against `id.twitch.tv/oauth2/token`), not cached to disk. One extra ~100ms auth call per run, but no new on-disk credential artifact to manage/invalidate — keeps the fetcher leg stateless. Revisit if run latency becomes annoying.
- `list_remote_vods` lists `type=archive` only, `first=10`. Archives are the rewatchable VODs; highlights/uploads are out of scope for watch-progress analysis. (Started at 20, dropped to 10 — 20 rows was more than wanted to scan for "what's new since I last fetched".) No `--limit` flag yet; add later if wanted.
- Local/remote diff keys on the canonical Helix `user_login` (from the video objects), not the user-typed streamer string — matches how `fetch_by_url` stores logs (`owner.login`, lowercased), so casing differences don't cause re-downloads.
- Path C is discovery + selection only; the actual download reuses the existing `fetch_by_url(vod_id, config)` per chosen VOD. No second download path.
- Interactive selection is numbered multi-pick: prompt accepts `1,3 5` / `all` / blank-to-cancel (`parse_selection`, pure + tested). `--all` skips the prompt and fetches everything undownloaded. A failed VOD in a batch is reported and skipped, not fatal to the rest.
- Interactive list shows ALL recent VODs (not just undownloaded), with already-downloaded ones dimmed + tagged `[downloaded]` and a header count. First cut hid downloaded VODs entirely, which made the list look like it had "missed" recent streams when really they were already on disk — the gap between "newest on Twitch" and "top of the list" was invisible. Showing everything keeps the numbering aligned with the channel's timeline. Downloaded rows are still numbered but unselectable in effect: picking one (or `all`) skips it, and an all-downloaded selection prints "Nothing to fetch". `--all` (non-interactive) still silently fetches only the new ones. Added `downloaded_ids(streamer, config)` as the on-disk primitive; `undownloaded_vods` is expressed in terms of it.
- `fetcher.py` stays click-free (matches existing style — `cli.py` owns all interaction). The streamer orchestration lives in `cli._fetch_by_streamer`; the module exposes `list_remote_vods` + the pure helpers `undownloaded_vods` / `parse_selection`. Removed the `fetch_by_streamer` stub.
