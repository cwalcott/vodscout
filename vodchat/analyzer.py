from dataclasses import dataclass
from pathlib import Path

from .watched import WatchedRange


@dataclass
class Moment:
    timestamp_seconds: int
    signals: list[str]
    magnitude: float
    samples: list[str]
    watched: bool


def analyze(vod_id: str, streamer: str, chat_dir: Path, watched_ranges: list[WatchedRange] | None = None) -> list[Moment]:
    """Return ranked interesting moments from a VOD's chat log."""
    raise NotImplementedError


def report(moments: list[Moment], vod_id: str, top_n: int = 20) -> None:
    """Print the analysis report to the terminal."""
    raise NotImplementedError
