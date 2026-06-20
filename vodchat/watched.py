import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from vodchat import analyzer


@dataclass
class WatchedRange:
    start_seconds: int
    end_seconds: int
    source: str  # "manual" or "chat-inferred"


@dataclass
class WatchedRanges:
    ranges: list[WatchedRange]
    last_updated: str


def _watched_path(vod_id: str, chat_dir: Path) -> Path:
    """Locate the .watched.json sibling of a VOD's chat log.

    Reuses analyzer.find_log so "find this vod_id under any streamer" lives
    in one place (and raises the same multi-streamer error).
    """
    _streamer, log_path = analyzer.find_log(vod_id, chat_dir)
    return log_path.with_suffix(".watched.json")


def _merge_ranges(ranges: list[WatchedRange]) -> list[WatchedRange]:
    """Sort by start, then merge overlapping/adjacent ranges.

    A merged span is "manual" if any of its inputs were manual — manual is
    the trustworthy source of truth, so it wins over chat-inferred.
    """
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda r: r.start_seconds)
    merged = [
        WatchedRange(
            ordered[0].start_seconds, ordered[0].end_seconds, ordered[0].source
        )
    ]
    for r in ordered[1:]:
        last = merged[-1]
        if r.start_seconds <= last.end_seconds:  # overlap or adjacency
            last.end_seconds = max(last.end_seconds, r.end_seconds)
            if r.source == "manual":
                last.source = "manual"
        else:
            merged.append(WatchedRange(r.start_seconds, r.end_seconds, r.source))
    return merged


def load(vod_id: str, chat_dir: Path) -> WatchedRanges:
    """Load watched ranges for a VOD. Missing file -> empty (not an error)."""
    path = _watched_path(vod_id, chat_dir)
    if not path.exists():
        return WatchedRanges(ranges=[], last_updated="")
    data = json.loads(path.read_text())
    ranges = [
        WatchedRange(r["start_seconds"], r["end_seconds"], r["source"])
        for r in data.get("ranges", [])
    ]
    return WatchedRanges(ranges=ranges, last_updated=data.get("last_updated", ""))


def save(watched: WatchedRanges, vod_id: str, chat_dir: Path) -> None:
    """Normalize (sort + merge) and write watched ranges to disk."""
    path = _watched_path(vod_id, chat_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = _merge_ranges(watched.ranges)
    payload = {
        "ranges": [
            {
                "start_seconds": r.start_seconds,
                "end_seconds": r.end_seconds,
                "source": r.source,
            }
            for r in merged
        ],
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    watched.ranges = merged


def _parse_timestamp(s: str) -> int:
    """Parse 'H:MM:SS', 'MM:SS', or a raw integer-seconds string to seconds."""
    s = s.strip()
    if ":" not in s:
        return int(s)
    parts = s.split(":")
    if len(parts) > 3:
        raise ValueError(f"Invalid timestamp: {s!r}")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def parse_range(spec: str, end_resolver=None) -> WatchedRange:
    """Parse a 'START-END' spec into a manual WatchedRange.

    START/END accept H:MM:SS, MM:SS, or raw seconds. Splits on the last '-'
    so that an all-seconds form like '60-120' still parses (no ':' ambiguity).

    Open-ended forms:
      - empty/`start` START (e.g. '-1:00:00', 'start-1:00:00') -> from 0
      - empty/`end` END (e.g. '2:45:00-', '2:45:00-end') -> to the VOD end,
        obtained by calling end_resolver() lazily (only when actually needed).
    """
    if "-" not in spec:
        raise ValueError(f"Range must be START-END, got {spec!r}")
    start_str, end_str = (s.strip() for s in spec.rsplit("-", 1))

    start = 0 if start_str in ("", "start") else _parse_timestamp(start_str)

    if end_str in ("", "end"):
        if end_resolver is None:
            raise ValueError(f"Open-ended range needs a VOD length: {spec!r}")
        end = end_resolver()
    else:
        end = _parse_timestamp(end_str)

    if end <= start:
        raise ValueError(f"Range end must be after start: {spec!r}")
    return WatchedRange(start, end, "manual")


def vod_end_seconds(vod_id: str, chat_dir: Path) -> int:
    """The last chat-message timestamp for a VOD — a stand-in for VOD length."""
    _streamer, log_path = analyzer.find_log(vod_id, chat_dir)
    messages = analyzer.load_messages(log_path)
    return max((m["time"] for m in messages), default=0)


# Lead/trail padding applied to each inferred cluster. Chat lags the moment
# that prompted it, and people watch a bit before/after they type — a couple
# minutes of slop keeps inferred ranges from hugging message timestamps too
# tightly. Assistive only; the user reviews before it's saved.
PAD_SECONDS = 120


def infer_from_chat(
    vod_id: str, username: str, chat_dir: Path, gap_threshold_seconds: int = 600
) -> list[WatchedRange]:
    """Infer watched ranges from the user's own messages in the chat log.

    Gap-based session segmentation: cluster the user's message timestamps,
    splitting into a new range wherever the gap between consecutive messages
    exceeds gap_threshold_seconds. Each cluster is padded by PAD_SECONDS on
    each side (start clamped at 0). Assistive, not authoritative — chat
    silence doesn't mean not-watching.
    """
    _streamer, log_path = analyzer.find_log(vod_id, chat_dir)
    messages = analyzer.load_messages(log_path)

    name = username.lower()
    times = sorted(m["time"] for m in messages if m["user"].lower() == name)
    if not times:
        return []

    clusters: list[list[int]] = [[times[0]]]
    for t in times[1:]:
        if t - clusters[-1][-1] > gap_threshold_seconds:
            clusters.append([t])
        else:
            clusters[-1].append(t)

    return [
        WatchedRange(
            start_seconds=max(0, c[0] - PAD_SECONDS),
            end_seconds=c[-1] + PAD_SECONDS,
            source="chat-inferred",
        )
        for c in clusters
    ]
