from dataclasses import dataclass
from pathlib import Path

import tomlkit

CONFIG_PATH = Path("~/.config/vodchat/config.toml").expanduser()

_DOWNLOADERS = ["chat-downloader", "twitchdownloadercli"]


@dataclass
class Config:
    chat_dir: Path
    downloader: str = "chat-downloader"
    # your own login, used as the default for `watched --infer`
    twitch_username: str = ""
    # Detection thresholds — overridable via [analysis] in config.toml
    bucket_seconds: int = 60
    # silence past this (seconds) splits watched-inference sessions
    gap_threshold_seconds: int = 180


def load() -> "Config":
    if not CONFIG_PATH.exists():
        return setup_interactive()

    with CONFIG_PATH.open() as f:
        doc = tomlkit.load(f)

    chat_dir = Path(str(doc.get("chat_dir", ""))).expanduser()
    if not chat_dir.parts:
        raise ValueError(f"chat_dir missing or empty in {CONFIG_PATH}")

    analysis = doc.get("analysis") or {}
    return Config(
        chat_dir=chat_dir,
        downloader=str(doc.get("downloader", "chat-downloader")),
        twitch_username=str(doc.get("twitch_username", "")),
        bucket_seconds=int(analysis.get("bucket_seconds", 60)),
        gap_threshold_seconds=int(analysis.get("gap_threshold_seconds", 180)),
    )


def save(config: "Config") -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if CONFIG_PATH.exists():
        with CONFIG_PATH.open() as f:
            doc = tomlkit.load(f)
    else:
        doc = tomlkit.document()

    doc["chat_dir"] = str(config.chat_dir)
    doc["downloader"] = config.downloader
    doc["twitch_username"] = config.twitch_username

    defaults = Config(chat_dir=config.chat_dir)
    non_default_thresholds = (
        config.bucket_seconds != defaults.bucket_seconds
        or config.gap_threshold_seconds != defaults.gap_threshold_seconds
    )
    if non_default_thresholds or "analysis" in doc:
        analysis: dict = doc.get("analysis") or tomlkit.table()  # type: ignore[assignment]
        analysis["bucket_seconds"] = config.bucket_seconds
        analysis["gap_threshold_seconds"] = config.gap_threshold_seconds
        doc["analysis"] = analysis

    with CONFIG_PATH.open("w") as f:
        tomlkit.dump(doc, f)


def setup_interactive() -> "Config":
    import click

    click.echo("No config found at ~/.config/vodchat/config.toml — let's set it up.\n")

    chat_dir_str = click.prompt(
        "Chat directory (where VOD logs will be stored)",
        default="~/Documents/vodchat",
    )
    chat_dir = Path(chat_dir_str).expanduser()

    downloader = click.prompt(
        "Downloader backend",
        default="chat-downloader",
        type=click.Choice(_DOWNLOADERS),
    )

    twitch_username = click.prompt(
        "Your Twitch username (used to infer watched ranges from your chat)",
        default="",
        show_default=False,
    )

    config = Config(
        chat_dir=chat_dir,
        downloader=downloader,
        twitch_username=twitch_username,
    )
    save(config)
    click.echo(f"\nConfig saved to {CONFIG_PATH}\n")
    return config
