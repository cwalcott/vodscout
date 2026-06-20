from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

CONFIG_PATH = Path("~/.config/vodchat/config.toml").expanduser()


@dataclass
class Config:
    chat_dir: Path
    downloader: str = "chat-downloader"
    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    emotes: dict[str, dict[str, str]] = field(default_factory=dict)


def load() -> Config:
    raise NotImplementedError


def save(config: Config) -> None:
    raise NotImplementedError


def setup_interactive() -> Config:
    """First-run interactive config setup."""
    raise NotImplementedError
