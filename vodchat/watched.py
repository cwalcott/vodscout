from dataclasses import dataclass
from pathlib import Path


@dataclass
class WatchedRange:
    start_seconds: int
    end_seconds: int
    source: str  # "manual" or "chat-inferred"


@dataclass
class WatchedRanges:
    ranges: list[WatchedRange]
    last_updated: str


def load(vod_id: str, streamer: str, chat_dir: Path) -> WatchedRanges:
    raise NotImplementedError


def save(ranges: WatchedRanges, vod_id: str, streamer: str, chat_dir: Path) -> None:
    raise NotImplementedError


def infer_from_chat(
    vod_id: str, username: str, chat_dir: Path, gap_threshold_seconds: int = 540
) -> list[WatchedRange]:
    """Infer watched ranges from the user's own messages in the chat log."""
    raise NotImplementedError


def interactive_edit(vod_id: str, streamer: str, chat_dir: Path) -> None:
    """Drop into an interactive session to view/edit watched ranges."""
    raise NotImplementedError
