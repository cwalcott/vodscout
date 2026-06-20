from pathlib import Path

from .config import Config


def fetch_by_url(url: str, config: Config) -> Path:
    """Download chat for a VOD URL/ID. Returns path to the saved chat log."""
    raise NotImplementedError


def list_remote_vods(streamer: str, config: Config) -> list[dict]:
    """List recent VODs for a streamer via Twitch Helix API."""
    raise NotImplementedError


def fetch_by_streamer(streamer: str, config: Config, fetch_all: bool = False) -> None:
    """List/pick undownloaded VODs for a streamer and fetch selected ones."""
    raise NotImplementedError
