"""Interactive terminal shell — a front end over the same leg APIs as cli.py.

This is a second consumer of fetcher/watched/analyzer (alongside cli.py), and
it stays self-contained: the three legs never import this module, and all the
questionary/Rich interaction lives here so the rest of the package carries no
interactive-UI dependency. Built on questionary (prompts) + Rich (rendering).

Session state is just two things: the current streamer (its merged VOD list)
and the selected VOD. Slice 1 is navigation only — drilling into a VOD shows
its details; the per-VOD actions (analyze / watched / emotes / download /
delete) get wired in later slices.
"""

import questionary
from rich.console import Console

from vodchat import config as cfg
from vodchat import fetcher, vodlist

console = Console()

# Sentinel selection values, distinct from any row index.
_QUIT = "__quit__"
_BACK = "__back__"

# Longest VOD title shown in the list before truncating with an ellipsis.
_TITLE_MAX = 45


def run_shell(
    config: "cfg.Config", streamer: str | None = None, *, offline: bool = False
) -> None:
    """Entry point for `vodchat browse` and bare `vodchat`.

    Resolves the streamer from the argument, then config's default_streamer,
    then an interactive prompt — and opens that streamer's VOD list.
    """
    streamer = (streamer or config.default_streamer or _prompt_streamer()).strip()
    if not streamer:
        return
    _streamer_view(streamer, config, offline)


def _prompt_streamer() -> str:
    answer = questionary.text("Streamer to browse:").ask()
    return (answer or "").strip()


def _select(message: str, choices: list):
    """questionary.select with `q` bound to quit the shell (returns _QUIT).

    questionary has no built-in quit key, so we add one to the underlying
    prompt_toolkit application: pressing `q` exits the prompt with the _QUIT
    sentinel, the same value the explicit Quit choice carries.
    """
    question = questionary.select(
        message, choices=choices, instruction="(↑/↓ to move, q to quit)"
    )

    @question.application.key_bindings.add("q")
    def _quit(event) -> None:
        event.app.exit(result=_QUIT)

    return question.ask()


def _streamer_view(streamer: str, config: "cfg.Config", offline: bool) -> None:
    """List a streamer's VODs and let the user drill into one. Loops until quit."""
    while True:
        rows, login, note = vodlist.merged_vods(streamer, config, offline)
        if not rows:
            console.print(note or f"No VODs found for [bold]{streamer}[/].")
            return

        n_down = sum(1 for r in rows if r["downloaded"])
        console.print(f"\n[bold]{login}[/] — {len(rows)} VOD(s), {n_down} downloaded")
        if note:
            console.print(f"[yellow]{note}[/]")

        choices = [_vod_choice(i, r) for i, r in enumerate(rows)]
        choices.append(questionary.Separator())
        choices.append(questionary.Choice("Quit", value=_QUIT))

        selected = _select("Select a VOD:", choices)
        # None = Ctrl-C / Esc; treat like Quit.
        if selected is None or selected == _QUIT:
            return
        if _vod_view(rows[selected], login, config) == _QUIT:
            return


def _vod_choice(index: int, row: dict) -> "questionary.Choice":
    date = (row["created_at"] or "")[:10] or "??????????"
    dur = fetcher._format_duration(row["duration_seconds"])
    tags = ""
    if row["downloaded"]:
        tags += " [downloaded]"
    if row["watched"]:
        tags += " [watched]"
    title = row["title"] or "(no title)"
    if len(title) > _TITLE_MAX:
        title = title[: _TITLE_MAX - 1].rstrip() + "…"
    text = f"{date}  {dur:>9}  {title}{tags}"
    # Grey out already-downloaded rows so the not-yet-grabbed ones stand out
    # (mirrors the dimmed rows in `vodchat vods`). questionary renders a list of
    # (style, text) tuples as formatted text; a plain str stays default-colored.
    if row["downloaded"]:
        return questionary.Choice(title=[("fg:ansibrightblack", text)], value=index)
    return questionary.Choice(title=text, value=index)


def _vod_view(row: dict, login: str, config: "cfg.Config") -> str:
    """Detail + action menu for one VOD.

    Returns _BACK to return to the list or _QUIT to exit the shell. Actions
    beyond navigation are added in a later slice.
    """
    while True:
        _print_vod(row, login)
        action = _select(
            "Action:",
            [
                questionary.Choice("Back to list", value=_BACK),
                questionary.Choice("Quit", value=_QUIT),
            ],
        )
        if action is None or action == _BACK:
            return _BACK
        if action == _QUIT:
            return _QUIT


def _print_vod(row: dict, login: str) -> None:
    date = (row["created_at"] or "")[:10] or "unknown date"
    dur = fetcher._format_duration(row["duration_seconds"])
    status = []
    if row["downloaded"]:
        status.append("downloaded")
    if row["watched"]:
        status.append("watched")
    status_str = ", ".join(status) or "not downloaded"
    console.print(f"\n[bold]{row['title'] or '(no title)'}[/]")
    console.print(f"[dim]{login} · {date} · {dur} · {row['id']} · {status_str}[/]")
    console.print(f"[dim]https://twitch.tv/videos/{row['id']}[/]")
