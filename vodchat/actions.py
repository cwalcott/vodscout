"""Cross-leg orchestration shared by both front ends (cli.py and ui.py).

Like vodlist.py, this is front-end-neutral glue, not a leg: it composes the
analyzer and watched modules — which can't depend on each other (watched
imports analyzer, so analyzer can't import watched) — so the command-line and
interactive front ends run the exact same analysis. Nothing here prints or
prompts; callers own all I/O and error display.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from vodchat import analyzer, watched
from vodchat import config as cfg


class EmoteNotFound(ValueError):
    """Raised when the requested emote matches nothing in the VOD's chat."""


@dataclass
class AnalysisResult:
    moments: list["analyzer.Moment"]
    emote: str | None  # resolved emote name, or None for the overall view
    emote_matches: list[str]  # all matches (len > 1 == ambiguous); [] when overall


def analyze(
    vod_id: str,
    config: "cfg.Config",
    *,
    emote: str | None = None,
    include_watched: bool = False,
) -> AnalysisResult:
    """Run spike detection for a VOD, filtered by watched ranges.

    Overall view (emote=None) ranks chat-volume spikes; per-emote view ranks one
    emote's spikes. Watched ranges are read through the on-disk file, keeping the
    legs decoupled. Unwatched-only unless include_watched. Raises
    FileNotFoundError (no log), ValueError (vod under multiple streamers), or
    EmoteNotFound (no emote match).
    """
    _streamer, log_path = analyzer.find_log(vod_id, config.chat_dir)
    messages = analyzer.load_messages(log_path)

    resolved: str | None = None
    matches: list[str] = []
    if emote:
        matches = analyzer.resolve_emote(emote, analyzer.count_emotes(messages))
        if not matches:
            raise EmoteNotFound(f"No emote matching {emote!r} in this VOD.")
        resolved = matches[0]
        moments = analyzer.detect_emote_spikes(
            messages, config.bucket_seconds, resolved
        )
    else:
        moments = analyzer.detect_spikes(messages, config.bucket_seconds)

    ranges = watched.load(vod_id, config.chat_dir).ranges
    analyzer.mark_watched(moments, [(r.start_seconds, r.end_seconds) for r in ranges])
    if not include_watched:
        moments = [m for m in moments if not m.watched]

    return AnalysisResult(moments=moments, emote=resolved, emote_matches=matches)


def emote_counts(vod_id: str, config: "cfg.Config") -> Counter:
    """Per-emote usage counts for one VOD. Raises if the chat log isn't found."""
    _streamer, log_path = analyzer.find_log(vod_id, config.chat_dir)
    return analyzer.count_emotes(analyzer.load_messages(log_path))


def add_ranges(
    vod_id: str,
    config: "cfg.Config",
    ranges: list["watched.WatchedRange"],
) -> "watched.WatchedRanges":
    """Merge new watched ranges into a VOD's existing ones and persist.

    Loads current ranges, extends with `ranges`, and saves — save() sorts and
    merges overlaps, so the result is normalized. Shared by the CLI's
    `watched --add` / `--infer` and the shell's add-range action. Returns the
    saved (normalized) WatchedRanges.
    """
    current = watched.load(vod_id, config.chat_dir)
    current.ranges.extend(ranges)
    watched.save(current, vod_id, config.chat_dir)
    return current


def delete_vod(vod_id: str, config: "cfg.Config") -> list[Path]:
    """Delete a VOD's chat log and its sidecars (.meta.json, .watched.json).

    Returns the paths actually removed (sidecars may not all exist). Raises
    FileNotFoundError if the VOD isn't downloaded, ValueError if its id is
    ambiguous across streamers — both via find_log.
    """
    _streamer, log_path = analyzer.find_log(vod_id, config.chat_dir)
    streamer_dir = log_path.parent
    candidates = [
        log_path,
        streamer_dir / f"{vod_id}.meta.json",
        streamer_dir / f"{vod_id}.watched.json",
    ]
    removed = []
    for path in candidates:
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed
