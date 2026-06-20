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
