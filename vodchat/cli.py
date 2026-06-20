import click


@click.group()
def main() -> None:
    """Find interesting moments in Twitch VOD chat."""


@main.command()
@click.argument("streamer", required=False)
@click.option("--url", help="VOD URL or ID (no credentials required).")
@click.option("--all", "fetch_all", is_flag=True, help="Fetch all undownloaded VODs.")
def fetch(streamer: str | None, url: str | None, fetch_all: bool) -> None:
    """Download chat for a VOD."""
    raise NotImplementedError


@main.command("list")
@click.argument("streamer")
def list_vods(streamer: str) -> None:
    """Show downloaded VODs for a streamer."""
    raise NotImplementedError


@main.command()
@click.argument("vod_id")
def watched(vod_id: str) -> None:
    """Interactive watched-range editor for a VOD."""
    raise NotImplementedError


@main.command()
@click.argument("target")
@click.option("--all", "analyze_all", is_flag=True, help="Analyze all VODs for a streamer.")
def analyze(target: str, analyze_all: bool) -> None:
    """Find interesting moments in a VOD (or all VODs for a streamer with --all)."""
    raise NotImplementedError
