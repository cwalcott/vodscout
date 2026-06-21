"""Interactive terminal UI — a Textual front end over the same leg APIs as cli.py.

This is a second consumer of fetcher/watched/analyzer (alongside cli.py). The
three legs never import this module, and all the Textual interaction lives here,
so the rest of the package carries no interactive-UI dependency.

Flow: a VOD *list* screen (the streamer's merged local+remote VODs); selecting a
VOD pushes a full *VOD window* with top moments (left) and emotes (right) side by
side, a `w` All/Unwatched toggle that drives the moment list, and `f` to favorite
an emote (pinned first). The list, moments, and emotes are real now — favorite
*persistence* is still an in-memory stub (slice 3) and watched-editing is not
wired yet (slice 4).
"""

import webbrowser
from collections import Counter

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from vodchat import _fixtures as fx
from vodchat import actions, fetcher, vodlist
from vodchat import analyzer as an
from vodchat import config as cfg
from vodchat import watched as wt


def _coverage_bar(watched_seconds: int, duration_seconds: int, width: int = 5) -> str:
    """A tiny ▓░ progress bar + percentage, e.g. '▓▓▓░░  62%'."""
    if not duration_seconds:
        return f"{'░' * width}   0%"
    frac = max(0.0, min(1.0, watched_seconds / duration_seconds))
    filled = round(frac * width)
    bar = "▓" * filled + "░" * (width - filled)
    return f"{bar} {round(frac * 100):>3}%"


def _watched_seconds(vod_id: str, config: "cfg.Config") -> int:
    """Total watched time for a VOD, for the coverage bar. 0 if none/unreadable."""
    try:
        ranges = wt.load(vod_id, config.chat_dir).ranges
    except (FileNotFoundError, ValueError):
        return 0
    return sum(r.end_seconds - r.start_seconds for r in ranges)


class VodListScreen(Screen):
    """The streamer's VOD list. Enter drills into a VOD window."""

    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="vodlist", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#vodlist", DataTable)
        table.add_columns("date", "length", "title", "watched", "")
        self._rows: dict[str, dict] = {}
        self._populate()
        table.focus()

    def _populate(self) -> None:
        table = self.query_one("#vodlist", DataTable)
        table.clear()
        self._rows = {}
        try:
            rows, login, note = vodlist.merged_vods(
                self.app.streamer, self.app.config, self.app.offline
            )
        except Exception as e:
            self.notify(f"Couldn't load VODs: {e}", severity="error")
            return

        self.app.sub_title = login
        for v in rows:
            v["watched_seconds"] = (
                _watched_seconds(v["id"], self.app.config) if v["downloaded"] else 0
            )
            self._rows[v["id"]] = v
            date = (v["created_at"] or "")[:10] or "??????????"
            dur = fetcher._format_duration(v["duration_seconds"])
            cov = _coverage_bar(v["watched_seconds"], v["duration_seconds"])
            dl = "⬇" if v["downloaded"] else " "
            table.add_row(date, dur, v["title"] or "(no title)", cov, dl, key=v["id"])

        if note:
            self.notify(note, severity="warning")
        elif not rows:
            self.notify("No VODs found.", severity="warning")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        vod = self._rows.get(event.row_key.value)
        if vod:
            self.app.push_screen(VodScreen(vod))

    def action_refresh(self) -> None:
        self._populate()


class VodScreen(Screen):
    """One VOD: top moments (left) + emotes (right), with an All/Unwatched mode."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("w", "toggle_mode", "All/Unwatched"),
        ("f", "favorite", "★ emote"),
        ("o", "overall", "Overall"),
        ("q", "app.quit", "Quit"),
    ]

    def __init__(self, vod: dict) -> None:
        super().__init__()
        self.vod = vod
        self.show_all = False  # Unwatched is the default view
        self.current_emote: str | None = None  # None = overall chat-volume view
        self.favorites: set[str] = set(fx.fixture_favorites(self._streamer))
        self._raw_moments: list[an.Moment] = []  # all moments (watched-flagged)
        self._emote_counts: Counter = Counter()

    @property
    def _streamer(self) -> str:
        return self.app.streamer

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="vodheader")
        if self.vod["downloaded"]:
            with Horizontal(id="panes"):
                yield DataTable(id="moments", cursor_type="row", zebra_stripes=True)
                yield DataTable(id="emotes", cursor_type="row", zebra_stripes=True)
        else:
            yield Static(
                "\n  [dim]Not downloaded yet. Downloading its chat is a later "
                "slice — for now there's nothing to analyze.[/dim]",
                id="placeholder",
            )
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_header()
        if not self.vod["downloaded"]:
            return
        self.query_one("#moments", DataTable).border_title = "Top moments"
        self.query_one(
            "#emotes", DataTable
        ).border_title = "Emotes  (f: ★ · tab: focus)"
        self._load_moments()
        self._load_emotes()
        self._populate_moments()
        self._populate_emotes()

    # --- data loading (the real leg calls) -------------------------------

    def _load_moments(self) -> None:
        """Fetch all moments for the current view (overall or current_emote),
        watched-flagged; the All/Unwatched filter is applied at render time."""
        try:
            result = actions.analyze(
                self.vod["id"],
                self.app.config,
                emote=self.current_emote,
                include_watched=True,
            )
            self._raw_moments = result.moments
        except actions.EmoteNotFound:
            self._raw_moments = []
            self.notify(f"No {self.current_emote!r} spikes here.", severity="warning")
        except (FileNotFoundError, ValueError) as e:
            self._raw_moments = []
            self.notify(str(e), severity="error")

    def _load_emotes(self) -> None:
        try:
            self._emote_counts = actions.emote_counts(self.vod["id"], self.app.config)
        except (FileNotFoundError, ValueError) as e:
            self._emote_counts = Counter()
            self.notify(str(e), severity="error")

    # --- rendering -------------------------------------------------------

    def _refresh_header(self) -> None:
        v = self.vod
        date = (v["created_at"] or "")[:10] or "unknown date"
        dur = fetcher._format_duration(v["duration_seconds"])
        cov = _coverage_bar(v.get("watched_seconds", 0), v["duration_seconds"]).strip()
        mode = "All" if self.show_all else "Unwatched"
        showing = (
            f"{self.current_emote} spikes" if self.current_emote else "chat volume"
        )
        self.query_one("#vodheader", Static).update(
            f"[b]{v['title'] or '(no title)'}[/b]\n"
            f"[dim]{self._streamer} · {date} · {dur} · {cov} watched[/dim]\n"
            f"mode: [b]{mode}[/b]  (w toggles)     showing: [b]{showing}[/b]"
        )

    def _visible(self, moments: list[an.Moment]) -> list[an.Moment]:
        ordered = sorted(moments, key=lambda m: m.magnitude, reverse=True)
        if self.show_all:
            return ordered
        return [m for m in ordered if not m.watched]

    def _populate_moments(self) -> None:
        table = self.query_one("#moments", DataTable)
        table.clear(columns=True)
        per_emote = self.current_emote is not None
        table.add_columns("#", "time", "mag", "uses" if per_emote else "top emotes")

        moments = self._visible(self._raw_moments)
        if not moments:
            table.add_row("", "—", "", "[dim](nothing in this view)[/dim]")
            return
        for i, m in enumerate(moments, 1):
            ts = an._format_timestamp(m.timestamp_seconds)
            mag = f"{m.magnitude:.1f}×"
            if per_emote:
                detail = f"{m.count} uses"
            else:
                detail = (
                    "  ".join(f"{e} [dim]({n})[/dim]" for e, n in m.top_emotes)
                    or "[dim]—[/dim]"
                )
            if self.show_all and m.watched:
                detail += "  [dim]\\[watched][/dim]"
            table.add_row(str(i), ts, mag, detail, key=str(m.timestamp_seconds))

    def _populate_emotes(self) -> None:
        table = self.query_one("#emotes", DataTable)
        table.clear(columns=True)
        table.add_columns("emote", "uses")
        items = self._emote_counts.most_common()
        if not items:
            table.add_row("[dim](no emotes)[/dim]", "")
            return
        favs = [it for it in items if it[0] in self.favorites]
        rest = [it for it in items if it[0] not in self.favorites]
        for name, n in favs + rest:
            star = "★ " if name in self.favorites else "  "
            table.add_row(f"{star}{name}", str(n), key=name)

    # --- actions ---------------------------------------------------------

    def action_toggle_mode(self) -> None:
        if not self.vod["downloaded"]:
            return
        self.show_all = not self.show_all
        self._refresh_header()
        self._populate_moments()

    def action_overall(self) -> None:
        if self.vod["downloaded"] and self.current_emote is not None:
            self.current_emote = None
            self._load_moments()
            self._refresh_header()
            self._populate_moments()

    def action_favorite(self) -> None:
        if not self.vod["downloaded"]:
            return
        table = self.query_one("#emotes", DataTable)
        if not table.has_focus:
            self.notify("Tab to the Emotes pane first, then f to favorite.")
            return
        if table.row_count == 0:
            return
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        name = row_key.value
        if name not in self._emote_counts:
            return  # the "(no emotes)" placeholder row
        if name in self.favorites:
            self.favorites.discard(name)
        else:
            self.favorites.add(name)
        fx.save_favorites(self._streamer, self.favorites)
        self._populate_emotes()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "moments":
            ts = event.row_key.value
            if ts and str(ts).isdigit():
                link = an._vod_link(self.vod["id"], int(ts))
                webbrowser.open(link)
                self.notify(f"Opening {link}")
        elif event.data_table.id == "emotes":
            name = event.row_key.value
            if name in self._emote_counts:
                self.current_emote = name
                self._load_moments()
                self._refresh_header()
                self._populate_moments()


class VodchatApp(App):
    """Top-level Textual app. Holds the cross-screen state the screens read:
    the resolved config, the current streamer, and the offline flag."""

    CSS = """
    #vodlist { height: 1fr; }

    #vodheader { height: auto; padding: 1 2; background: $panel; }
    #panes { height: 1fr; }
    #moments { width: 2fr; border: round $primary; }
    #emotes { width: 1fr; border: round $primary; }
    #placeholder { height: 1fr; padding: 2; }
    """

    def __init__(
        self, config: "cfg.Config", streamer: str, offline: bool = False
    ) -> None:
        super().__init__()
        self.config = config
        self.streamer = streamer
        self.offline = offline

    def on_mount(self) -> None:
        self.title = "vodchat"
        self.sub_title = self.streamer
        self.push_screen(VodListScreen())


def run_shell(
    config: "cfg.Config", streamer: str | None = None, *, offline: bool = False
) -> None:
    """Entry point for `vodchat browse` and bare `vodchat`.

    Resolves the streamer (argument → config.default_streamer → a one-off
    prompt), then launches the Textual app against the real merged VOD list.
    """
    streamer = (streamer or config.default_streamer or "").strip()
    if not streamer:
        import click

        streamer = (
            click.prompt("Streamer to browse", default="", show_default=False) or ""
        ).strip()
    if not streamer:
        return
    VodchatApp(config, streamer, offline).run()
