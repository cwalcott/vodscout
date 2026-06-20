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
