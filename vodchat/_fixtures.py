"""Temporary in-memory favorite-emote store for the TUI.

This is the only stub left after slice 1 (the VOD list, moments, and emotes are
all real now). Emote favorites live in memory for the session instead of in a
<streamer>/favorites.json sidecar. Slice 3 replaces these two functions with the
real sidecar read/write and deletes this module.
"""

_FAVORITES: dict[str, set[str]] = {}


def fixture_favorites(streamer: str) -> set[str]:
    return _FAVORITES.setdefault(streamer, set())


def save_favorites(streamer: str, favorites: set[str]) -> None:
    _FAVORITES[streamer] = set(favorites)
