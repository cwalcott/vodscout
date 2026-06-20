import click

from vodchat import analyzer as an
from vodchat import config as cfg
from vodchat import fetcher


@click.group()
@click.pass_context
def main(ctx: click.Context) -> None:
    """Find interesting moments in Twitch VOD chat."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = cfg.load()


@main.command()
@click.argument("streamer", required=False)
@click.option("--url", help="VOD URL or ID (no credentials required).")
@click.option("--all", "fetch_all", is_flag=True, help="Fetch all undownloaded VODs.")
@click.pass_context
def fetch(
    ctx: click.Context, streamer: str | None, url: str | None, fetch_all: bool
) -> None:
    """Download chat for a VOD."""
    config = ctx.obj["config"]

    if url:
        try:
            out_path = fetcher.fetch_by_url(url, config)
            click.echo(f"Saved to {out_path}")
        except FileExistsError as e:
            click.echo(str(e))
        except Exception as e:
            raise click.ClickException(str(e))
    elif streamer:
        raise click.ClickException(
            "Streamer-based fetch not yet implemented. "
            "Use --url to fetch by VOD URL or ID instead."
        )
    else:
        raise click.UsageError("Provide a VOD --url or a streamer name.")


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
@click.option(
    "--all", "analyze_all", is_flag=True, help="Analyze all VODs for a streamer."
)
@click.option(
    "--no-tokens",
    "show_tokens",
    is_flag=True,
    flag_value=False,
    default=True,
    help="Omit top tokens from the report.",
)
@click.option(
    "--top", "top_n", default=10, show_default=True, help="Number of moments to show."
)
@click.pass_context
def analyze(
    ctx: click.Context, target: str, analyze_all: bool, show_tokens: bool, top_n: int
) -> None:
    """Find interesting moments in a VOD (or all VODs for a streamer with --all)."""
    if analyze_all:
        raise click.ClickException("--all not yet implemented.")
    config = ctx.obj["config"]
    try:
        _streamer, log_path = an.find_log(target, config.chat_dir)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    except ValueError as e:
        raise click.ClickException(str(e))
    messages = an.load_messages(log_path)
    moments = an.detect_spikes(messages, config.bucket_seconds)
    an.report(moments, target, top_n=top_n, show_tokens=show_tokens)
