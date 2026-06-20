from pathlib import Path

import click

from vodchat import analyzer as an
from vodchat import config as cfg
from vodchat import fetcher
from vodchat import watched as wt


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
@click.pass_context
def list_vods(ctx: click.Context, streamer: str) -> None:
    """Show downloaded VODs for a streamer."""
    config = ctx.obj["config"]
    streamer_dir = config.chat_dir / streamer
    if not streamer_dir.is_dir():
        raise click.ClickException(f"No downloaded VODs for {streamer!r}.")

    logs = list(streamer_dir.glob("*.txt"))
    if not logs:
        raise click.ClickException(f"No downloaded VODs for {streamer!r}.")

    # Newest first — VOD IDs increase over time, so a numeric sort puts recent
    # VODs at the top. Fall back to string sort if an ID isn't purely numeric.
    def sort_key(path: Path) -> tuple[int, str]:
        stem = path.stem
        return (int(stem) if stem.isdigit() else -1, stem)

    for path in sorted(logs, key=sort_key, reverse=True):
        watched = path.with_suffix(".watched.json").exists()
        click.echo(f"  {path.stem}{' [watched]' if watched else ''}")
    click.echo(f"{len(logs)} VOD(s) for {streamer}")


@main.command()
@click.argument("vod_id")
@click.option(
    "--add",
    "add_spec",
    metavar="START-END",
    help="Add a manual watched range, e.g. 1:00:00-1:30:00.",
)
@click.option(
    "--edit", "edit_file", is_flag=True, help="Open the watched-range file in $EDITOR."
)
@click.option(
    "--infer",
    "infer",
    is_flag=True,
    help="Suggest watched ranges from your own chat messages (assistive).",
)
@click.option(
    "--user",
    "username",
    help="Your Twitch login for --infer (defaults to twitch_username in config).",
)
@click.pass_context
def watched(
    ctx: click.Context,
    vod_id: str,
    add_spec: str | None,
    edit_file: bool,
    infer: bool,
    username: str | None,
) -> None:
    """View or edit watched ranges for a VOD."""
    config = ctx.obj["config"]
    chat_dir = config.chat_dir

    try:
        if edit_file:
            path = wt._watched_path(vod_id, chat_dir)
            if not path.exists():
                wt.save(wt.WatchedRanges([], ""), vod_id, chat_dir)
            click.edit(filename=str(path))
            wt.load(vod_id, chat_dir)  # validate it still parses
        elif add_spec:
            current = wt.load(vod_id, chat_dir)
            try:
                new_range = wt.parse_range(
                    add_spec, end_resolver=lambda: wt.vod_end_seconds(vod_id, chat_dir)
                )
            except ValueError as e:
                raise click.BadParameter(str(e), param_hint="--add")
            current.ranges.append(new_range)
            wt.save(current, vod_id, chat_dir)
        elif infer:
            username = username or config.twitch_username
            if not username:
                raise click.UsageError(
                    "No username for --infer. Pass --user <login> or set "
                    "twitch_username in your config."
                )
            suggested = wt.infer_from_chat(
                vod_id, username, chat_dir, config.gap_threshold_seconds
            )
            if not suggested:
                click.echo(f"No messages from {username!r} found in this VOD's chat.")
                return
            click.echo("Suggested ranges from your chat activity:")
            _print_ranges(wt.WatchedRanges(suggested, ""))
            if click.confirm("Merge these into the watched ranges?", default=True):
                current = wt.load(vod_id, chat_dir)
                current.ranges.extend(suggested)
                wt.save(current, vod_id, chat_dir)
            else:
                click.echo("Discarded.")
                return

        _print_ranges(wt.load(vod_id, chat_dir))
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    except ValueError as e:
        raise click.ClickException(str(e))


def _print_ranges(watched_ranges: "wt.WatchedRanges") -> None:
    if not watched_ranges.ranges:
        click.echo("No watched ranges recorded.")
        return
    total = 0
    for r in watched_ranges.ranges:
        start = an._format_timestamp(r.start_seconds)
        end = an._format_timestamp(r.end_seconds)
        click.echo(f"  {start} – {end}  ({r.source})")
        total += r.end_seconds - r.start_seconds
    click.echo(f"Total watched: {an._format_timestamp(total)}")


@main.command()
@click.argument("vod_id")
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
@click.option(
    "--include-watched",
    "include_watched",
    is_flag=True,
    help="Also show moments inside your watched ranges (marked [watched]).",
)
@click.pass_context
def analyze(
    ctx: click.Context,
    vod_id: str,
    show_tokens: bool,
    top_n: int,
    include_watched: bool,
) -> None:
    """Find interesting moments in a VOD."""
    config = ctx.obj["config"]
    try:
        _streamer, log_path = an.find_log(vod_id, config.chat_dir)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    except ValueError as e:
        raise click.ClickException(str(e))
    messages = an.load_messages(log_path)
    moments = an.detect_spikes(messages, config.bucket_seconds)

    # Read watched ranges through the on-disk file (keeps the legs decoupled).
    watched_ranges = wt.load(vod_id, config.chat_dir).ranges
    an.mark_watched(moments, [(r.start_seconds, r.end_seconds) for r in watched_ranges])
    # Unwatched-only is the default — the whole point is to surface moments you
    # haven't seen. --include-watched opts back into the full list.
    if not include_watched:
        moments = [m for m in moments if not m.watched]

    an.report(moments, vod_id, top_n=top_n, show_tokens=show_tokens)
