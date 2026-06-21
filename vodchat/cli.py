from collections import Counter

import click

from vodchat import analyzer as an
from vodchat import config as cfg
from vodchat import fetcher, vodlist
from vodchat import watched as wt


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Find interesting moments in Twitch VOD chat.

    Run with no command to launch the interactive shell.
    """
    ctx.ensure_object(dict)
    ctx.obj["config"] = cfg.load()
    if ctx.invoked_subcommand is None:
        from vodchat import ui

        ui.run_shell(ctx.obj["config"])


@main.command()
@click.argument("streamer", required=False)
@click.option(
    "--offline", is_flag=True, help="Don't query Twitch; show local downloads only."
)
@click.pass_context
def browse(ctx: click.Context, streamer: str | None, offline: bool) -> None:
    """Interactively browse a streamer's VODs and act on them.

    Opens a navigable session: pick a streamer (or pass one / set
    default_streamer), arrow through the merged VOD list, and drill into a VOD.
    """
    from vodchat import ui

    ui.run_shell(ctx.obj["config"], streamer, offline=offline)


@main.command()
@click.argument("streamer", required=False)
@click.option("--url", help="Download one VOD by URL or ID, then exit.")
@click.option(
    "--all", "get_all", is_flag=True, help="Download every not-yet-downloaded VOD."
)
@click.option(
    "--get",
    "get_sel",
    metavar="N[,N...]",
    help="Download VODs by their list number (e.g. 1,3 / all).",
)
@click.option(
    "-i",
    "--pick",
    "interactive",
    is_flag=True,
    help="Prompt to choose what to download.",
)
@click.option(
    "--offline", is_flag=True, help="Don't query Twitch; show local downloads only."
)
@click.pass_context
def vods(
    ctx: click.Context,
    streamer: str | None,
    url: str | None,
    get_all: bool,
    get_sel: str | None,
    interactive: bool,
    offline: bool,
) -> None:
    """Browse a streamer's VODs and download their chat.

    Lists your downloads merged with recent VODs on Twitch (newest first,
    tagged [downloaded]/[watched]). Your downloads are the source of truth and
    are always shown — even if a VOD has aged off or been removed from Twitch.

    Listing is read-only; download with --all, --get <n>, --pick, or --url.
    """
    config = ctx.obj["config"]

    if url:
        _download_one(url, config)
        return
    if not streamer:
        raise click.UsageError("Provide a streamer name, or --url <vod>.")

    downloading = get_all or bool(get_sel) or interactive
    if offline and downloading:
        raise click.UsageError(
            "--offline only lists; drop it to download (--all / --get / --pick)."
        )

    ordered, login, note = vodlist.merged_vods(streamer, config, offline)
    if not ordered:
        raise click.ClickException(note or f"No downloaded VODs for {streamer!r}.")

    _render(ordered, login)
    if note:
        click.echo(note)

    if not downloading:
        avail = sum(1 for r in ordered if not r["downloaded"])
        if avail and not offline:
            click.echo(
                f"({avail} not downloaded — grab with --all, --get <n>, or --pick)"
            )
        return

    # Interactive pick fills in --get from a prompt (skipped if --all/--get given).
    if interactive and not (get_all or get_sel):
        get_sel = click.prompt(
            "Download which? (e.g. 1,3 / all / blank to skip)",
            default="",
            show_default=False,
        )

    if get_all:
        chosen = [r for r in ordered if not r["downloaded"]]
    else:
        try:
            indices = fetcher.parse_selection(get_sel or "", len(ordered))
        except ValueError as e:
            hint = None if interactive else "--get"
            raise click.BadParameter(str(e), param_hint=hint)
        # Picking a downloaded row is a no-op — skip what's already on disk.
        chosen = [ordered[i] for i in indices if not ordered[i]["downloaded"]]

    if not chosen:
        click.echo("Nothing to download.")
        return
    _download_many(chosen, config)


def _render(ordered: list[dict], login: str) -> None:
    n_down = sum(1 for r in ordered if r["downloaded"])
    click.echo(f"{login} — {len(ordered)} VOD(s), {n_down} downloaded")
    for i, r in enumerate(ordered, 1):
        date = (r["created_at"] or "")[:10] or "??????????"
        dur = fetcher._format_duration(r["duration_seconds"])
        tags = ""
        if r["downloaded"]:
            tags += " [downloaded]"
        if r["watched"]:
            tags += " [watched]"
        row = f"  {i:>2}. {date}  {dur:>9}  {r['id']}  {r['title']}{tags}"
        # Dim already-downloaded rows so the not-yet-grabbed ones stand out.
        click.echo(click.style(row, dim=True) if r["downloaded"] else row)


def _download_one(url: str, config: "cfg.Config") -> None:
    try:
        out_path = fetcher.fetch_by_url(url, config)
        click.echo(f"Saved to {out_path}")
    except FileExistsError as e:
        click.echo(str(e))
    except Exception as e:
        raise click.ClickException(str(e))


def _download_many(videos: list[dict], config: "cfg.Config") -> None:
    for v in videos:
        try:
            out_path = fetcher.fetch_by_url(v["id"], config)
            click.echo(f"Saved to {out_path}")
        except FileExistsError as e:
            click.echo(str(e))
        except Exception as e:
            # One bad VOD shouldn't abort the rest of the batch.
            click.echo(f"Failed {v['id']}: {e}")


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
@click.option(
    "--gap",
    "gap_seconds",
    type=int,
    help="Silence (seconds) that splits sessions for --infer "
    "(overrides config; default 180). Lower = more, shorter ranges.",
)
@click.pass_context
def watched(
    ctx: click.Context,
    vod_id: str,
    add_spec: str | None,
    edit_file: bool,
    infer: bool,
    username: str | None,
    gap_seconds: int | None,
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
            gap = (
                gap_seconds if gap_seconds is not None else config.gap_threshold_seconds
            )
            suggested = wt.infer_from_chat(vod_id, username, chat_dir, gap)
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
    "--emote",
    help="Top moments for one emote instead of overall chat volume "
    "(case-insensitive, partial match — e.g. 'lmaoo' finds 'LMAOOOOOOOOOO').",
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
    emote: str | None,
    top_n: int,
    include_watched: bool,
) -> None:
    """Find interesting moments in a VOD.

    By default, ranks moments where overall chat volume spiked, annotated with
    the emotes most used in each. With --emote, ranks moments where that one
    emote spiked above its own normal rate (see `vodchat emotes` to discover
    which emotes a chat spams).
    """
    config = ctx.obj["config"]
    try:
        _streamer, log_path = an.find_log(vod_id, config.chat_dir)
    except FileNotFoundError as e:
        raise click.ClickException(str(e))
    except ValueError as e:
        raise click.ClickException(str(e))
    messages = an.load_messages(log_path)
    if emote:
        matches = an.resolve_emote(emote, an.count_emotes(messages))
        if not matches:
            raise click.ClickException(
                f"No emote matching {emote!r} in this VOD. "
                f"See `vodchat emotes {vod_id}` for what's used."
            )
        emote = matches[0]
        if len(matches) > 1:
            others = "  ".join(matches[:5])
            click.echo(f"Multiple emotes match — using {emote}. Matches: {others}")
        moments = an.detect_emote_spikes(messages, config.bucket_seconds, emote)
    else:
        moments = an.detect_spikes(messages, config.bucket_seconds)

    # Read watched ranges through the on-disk file (keeps the legs decoupled).
    watched_ranges = wt.load(vod_id, config.chat_dir).ranges
    an.mark_watched(moments, [(r.start_seconds, r.end_seconds) for r in watched_ranges])
    # Unwatched-only is the default — the whole point is to surface moments you
    # haven't seen. --include-watched opts back into the full list.
    if not include_watched:
        moments = [m for m in moments if not m.watched]

    an.report(moments, vod_id, top_n=top_n, emote=emote)


@main.command("emotes")
@click.argument("target")
@click.option(
    "--top", "top_n", default=20, show_default=True, help="Number of emotes to show."
)
@click.pass_context
def emotes(ctx: click.Context, target: str, top_n: int) -> None:
    """Top emotes by usage for a VOD (numeric id) or a streamer (all VODs)."""
    config = ctx.obj["config"]

    counts: Counter = Counter()
    if target.isdigit():
        try:
            _streamer, log_path = an.find_log(target, config.chat_dir)
        except (FileNotFoundError, ValueError) as e:
            raise click.ClickException(str(e))
        counts = an.count_emotes(an.load_messages(log_path))
        label = f"VOD {target}"
    else:
        streamer_dir = config.chat_dir / target
        logs = sorted(streamer_dir.glob("*.txt")) if streamer_dir.is_dir() else []
        if not logs:
            raise click.ClickException(f"No downloaded VODs for {target!r}.")
        for path in logs:
            counts.update(an.count_emotes(an.load_messages(path)))
        label = f"{target} ({len(logs)} VOD(s))"

    top = counts.most_common(top_n)
    if not top:
        click.echo(f"No emotes found for {label}.")
        return

    click.echo(f"Top emotes — {label}\n")
    width = max(len(emote) for emote, _ in top)
    for emote, count in top:
        click.echo(f"  {emote:<{width}}  {count}")
