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

## 2026-06-20 — Path C (streamer-name fetch via Helix)

- App access token is minted per invocation (client-credentials flow against `id.twitch.tv/oauth2/token`), not cached to disk. One extra ~100ms auth call per run, but no new on-disk credential artifact to manage/invalidate — keeps the fetcher leg stateless. Revisit if run latency becomes annoying.
- `list_remote_vods` lists `type=archive` only, `first=10`. Archives are the rewatchable VODs; highlights/uploads are out of scope for watch-progress analysis. (Started at 20, dropped to 10 — 20 rows was more than wanted to scan for "what's new since I last fetched".) No `--limit` flag yet; add later if wanted.
- Local/remote diff keys on the canonical Helix `user_login` (from the video objects), not the user-typed streamer string — matches how `fetch_by_url` stores logs (`owner.login`, lowercased), so casing differences don't cause re-downloads.
- Path C is discovery + selection only; the actual download reuses the existing `fetch_by_url(vod_id, config)` per chosen VOD. No second download path.
- Interactive selection is numbered multi-pick: prompt accepts `1,3 5` / `all` / blank-to-cancel (`parse_selection`, pure + tested). `--all` skips the prompt and fetches everything undownloaded. A failed VOD in a batch is reported and skipped, not fatal to the rest.
- Interactive list shows ALL recent VODs (not just undownloaded), with already-downloaded ones dimmed + tagged `[downloaded]` and a header count. First cut hid downloaded VODs entirely, which made the list look like it had "missed" recent streams when really they were already on disk — the gap between "newest on Twitch" and "top of the list" was invisible. Showing everything keeps the numbering aligned with the channel's timeline. Downloaded rows are still numbered but unselectable in effect: picking one (or `all`) skips it, and an all-downloaded selection prints "Nothing to fetch". `--all` (non-interactive) still silently fetches only the new ones. Added `downloaded_ids(streamer, config)` as the on-disk primitive; `undownloaded_vods` is expressed in terms of it.
- `fetcher.py` stays click-free (matches existing style — `cli.py` owns all interaction). The streamer orchestration lives in `cli._fetch_by_streamer`; the module exposes `list_remote_vods` + the pure helpers `undownloaded_vods` / `parse_selection`. Removed the `fetch_by_streamer` stub.
