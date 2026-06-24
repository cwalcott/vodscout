"""Per-streamer favorite emotes — a plain list, persisted as a sidecar.

Favorites are keyed by streamer (not by VOD), so unlike watched ranges this
needs no `find_log`: the file is `<chat_dir>/<streamer>/favorites.json`. Used by
the TUI to pin favorited emotes to the top of the emote pane; a missing or
unreadable file just means "no favorites yet" (never an error).
"""

import json
from pathlib import Path


def _favorites_path(streamer: str, chat_dir: Path) -> Path:
    return chat_dir / streamer / "favorites.json"


def load(streamer: str, chat_dir: Path) -> set[str]:
    """A streamer's favorite emote names. Missing/unreadable file -> empty set."""
    path = _favorites_path(streamer, chat_dir)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    return set(data.get("emotes", []))


def save(favorites: set[str], streamer: str, chat_dir: Path) -> None:
    """Persist a streamer's favorite emotes (sorted, for stable diffs)."""
    path = _favorites_path(streamer, chat_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"emotes": sorted(favorites)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
