"""Interactive terminal UI — a Textual front end over the same leg APIs as cli.py.

This is a second consumer of fetcher/watched/analyzer (alongside cli.py). The
three legs never import this module, and all the Textual interaction lives here,
so the rest of the package carries no interactive-UI dependency.

Flow: a VOD *list* screen (downloads + recent VODs cached from the last refresh;
`r` refreshes from Twitch, `d` downloads the highlighted VOD); selecting a
*downloaded* VOD pushes a full *VOD window* with top moments (left) and emotes
(right) side by side, a `w` All/Unwatched toggle that drives the moment list, and
`f` to favorite the highlighted emote (pinned first) — or `/` to search-and-favorite
via a type-to-filter picker over the VOD's emotes. All wired to the real legs:
list/moments/emotes, the
`<streamer>/favorites.json` favorites sidecar, and watched tracking — auto-inferred
from your chat on first open of a VOD, with `e` to edit the ranges inline and `i`
to re-infer. An undownloaded VOD has no window to open, so selecting one instead
asks to confirm a download (or `d` grabs the highlighted row without the prompt).
The fetch runs as a background worker on the (always-mounted) list screen, so it
doesn't block — you keep browsing while it downloads, the row shows a spinner and
a live progress bar (how far the fetched chat has reached through the VOD), and
it flips to downloaded when it finishes. The same worker also infers watched
ranges from the freshly-downloaded chat (off the UI thread), so the row lands on
real coverage instead of flashing 0% until the VOD is opened. The watched column
is blank for undownloaded VODs (coverage only means something once the chat is on
disk).
Quitting aborts any in-flight download, so `q` confirms first (`ConfirmQuitScreen`)
when something is still downloading — otherwise it exits straight away.
"""

import threading
import time
import webbrowser
from collections import Counter
from functools import partial

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    OptionList,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerState

from vodscout import actions, fetcher, vodlist
from vodscout import analyzer as an
from vodscout import config as cfg
from vodscout import favorites as fav
from vodscout import watched as wt


def _coverage_bar(watched_seconds: int, duration_seconds: int, width: int = 5) -> str:
    """A tiny ▓░ progress bar + percentage, e.g. '▓▓▓░░  62%'."""
    if not duration_seconds:
        return f"{'░' * width}   0%"
    frac = max(0.0, min(1.0, watched_seconds / duration_seconds))
    filled = round(frac * width)
    bar = "▓" * filled + "░" * (width - filled)
    return f"{bar} {round(frac * 100):>3}%"


# Frames for the in-row "downloading" spinner — gives immediate, continuous
# motion the instant a download starts, before the percent has anything to show.
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _dl_indicator(spin: int, done: int, total: int | None) -> str:
    frame = _SPINNER[spin]
    if total:
        pct = round(100 * done / total)
        return f"{frame} {pct:>3}%"
    return frame


def _watched_seconds(vod_id: str, config: "cfg.Config") -> int:
    """Total watched time for a VOD, for the coverage bar. 0 if none/unreadable."""
    try:
        ranges = wt.load(vod_id, config.chat_dir).ranges
    except (FileNotFoundError, ValueError):
        return 0
    return sum(r.end_seconds - r.start_seconds for r in ranges)


def _infer_watched(vod_id: str, config: "cfg.Config") -> int:
    """Infer watched ranges from the user's own chat and persist them; return the
    number of ranges saved (0 if it did nothing).

    No-op — returns 0 — when there's no configured twitch_username, when a
    .watched.json already exists (never infer over existing data), or when the
    user never chatted in this VOD (an empty result leaves no file, so no false
    'has watched data' flag). Pure disk/compute with no Textual dependency, so
    it's safe to call off the UI thread — the background download does, right
    after the chat lands, so the list shows real coverage instead of a 0% that
    only fills in once the VOD is opened.
    """
    if not config.twitch_username:
        return 0
    try:
        if wt._watched_path(vod_id, config.chat_dir).exists():
            return 0
        suggested = wt.infer_from_chat(
            vod_id,
            config.twitch_username,
            config.chat_dir,
            config.gap_threshold_seconds,
        )
    except (FileNotFoundError, ValueError):
        return 0
    if not suggested:
        return 0
    actions.add_ranges(vod_id, config, suggested)
    return len(suggested)


def _match_emotes(items: list[tuple[str, int]], query: str) -> list[tuple[str, int]]:
    """Filter (emote, count) pairs by a case-insensitive substring `query`,
    preserving the input order (callers pass them most-used first).

    An empty/whitespace query matches everything. Mirrors how the analyzer
    resolves `--emote` (case-insensitive, partial), but keeps every match rather
    than collapsing to the single most-used one — the picker shows the field.
    """
    q = query.strip().lower()
    if not q:
        return list(items)
    return [(name, n) for name, n in items if q in name.lower()]


class VodListScreen(Screen):
    """The streamer's VOD list.

    Loads local downloads only on open (instant, no network); `r` refreshes from
    Twitch, merging in new VODs. Enter drills into a VOD window.
    """

    BINDINGS = [
        ("r", "refresh", "Refresh from Twitch"),
        ("d", "download", "Download chat"),
        ("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="vodlist", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#vodlist", DataTable)
        table.add_columns("date", "length", "title")
        # Fixed widths: the watched cell is blank until a VOD is downloaded, so
        # the column can't auto-size to the bar — pin it wide enough that the
        # download/coverage bar always fits (no truncation).
        table.add_column("watched", width=11)
        table.add_column("", width=6)  # marker: ⬇ downloaded · spinner+% downloading
        self._rows: dict[str, dict] = {}
        # vod_id -> (completed_seconds, total_seconds) progress for in-flight
        # background downloads. Workers are owned by this screen (it's the
        # always-mounted base), so they keep running while the user is off in a
        # VOD window.
        self._downloading: dict[str, tuple[int, int | None]] = {}
        # vod_id -> cancel flag, set on quit so a download aborts promptly (a
        # thread worker can't be force-killed, so it has to opt out itself).
        self._cancels: dict[str, threading.Event] = {}
        # Spinner animation for in-flight downloads: a paused interval that runs
        # only while something is downloading (resumed in start_download, paused
        # again when the last one finishes).
        self._spin = 0
        self._spinner_timer = self.set_interval(0.1, self._tick_spinner, pause=True)
        self._populate(offline=True)  # local-only on launch — no startup freeze
        table.focus()

    def _populate(self, offline: bool) -> None:
        table = self.query_one("#vodlist", DataTable)
        table.clear()
        self._rows = {}
        try:
            rows, login, note = vodlist.merged_vods(
                self.app.streamer, self.app.config, offline
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
            # Watched coverage only means something once the chat is on disk; an
            # undownloaded VOD shows nothing here (not a misleading "0% watched").
            cov = (
                _coverage_bar(v["watched_seconds"], v["duration_seconds"])
                if v["downloaded"]
                else ""
            )
            dl = "⬇" if v["downloaded"] else " "
            table.add_row(date, dur, v["title"] or "(no title)", cov, dl, key=v["id"])

        # Re-apply the live indicator for any download still running across a
        # rebuild (a refresh rebuilds the table, but the worker keeps going).
        for vod_id, (done, total) in self._downloading.items():
            self._set_row_status(vod_id, done, total)

        if note:
            self.notify(note, severity="warning")
        elif not rows:
            # Local-only with remote available: point them at the refresh key.
            if offline and not self.app.offline:
                self.notify("No local VODs — press r to fetch from Twitch.")
            else:
                self.notify("No VODs found.", severity="warning")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        vod = self._rows.get(event.row_key.value)
        if not vod:
            return
        if vod["downloaded"]:
            self.app.push_screen(VodScreen(vod))
            return
        # Undownloaded: there's nothing to show until the chat is fetched, so
        # offer to grab it instead of opening an empty window.
        if vod["id"] in self._downloading:
            self.notify(f"{vod['id']} is already downloading.")
            return
        if self.app.offline:
            self.notify(
                "Offline — relaunch without --offline to download.",
                severity="warning",
            )
            return

        def after(confirm: bool | None) -> None:
            if confirm:
                self.start_download(vod["id"])

        self.app.push_screen(ConfirmDownloadScreen(vod, self.app.streamer), after)

    def action_refresh(self) -> None:
        # r hits Twitch — unless the app was launched with --offline, which keeps
        # it local (r then just re-reads disk).
        self._populate(offline=self.app.offline)
        self.notify(
            "Reloaded local VODs." if self.app.offline else "Refreshed from Twitch."
        )

    def action_download(self) -> None:
        """Start a background download for the highlighted row."""
        table = self.query_one("#vodlist", DataTable)
        if table.row_count == 0:
            return
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        if row_key.value:
            self.start_download(row_key.value)

    def start_download(self, vod_id: str) -> None:
        """Kick off a non-blocking background fetch of one VOD's chat.

        The worker is owned by this (always-mounted) screen, so it survives the
        user drilling into a VOD window. Progress shows live in the row; the row
        flips to downloaded on success. Both front-end entry points (the list and
        the VOD window) funnel through here so there's one download path.
        """
        if self.app.offline:
            self.notify(
                "Offline — relaunch without --offline to download.",
                severity="warning",
            )
            return
        vod = self._rows.get(vod_id)
        if not vod or vod["downloaded"]:
            if vod:
                self.notify("Already downloaded.")
            return
        if vod_id in self._downloading:
            self.notify(f"{vod_id} is already downloading.")
            return

        self._downloading[vod_id] = (0, None)
        self._cancels[vod_id] = threading.Event()
        self._spinner_timer.resume()  # animate immediately, before any progress
        self._set_row_status(vod_id, 0, None)
        self.run_worker(
            partial(self._do_download, vod_id),
            name=vod_id,
            group="downloads",
            thread=True,
            exit_on_error=False,  # a failed fetch must not tear down the app
        )
        self.notify(f"Downloading {vod_id} in the background…")

    def _do_download(self, vod_id: str) -> int:
        """Worker body (runs off the UI thread). Reports progress (content offset
        vs. VOD duration) back via the throttled hook, fetches the chat, then
        infers watched ranges from it up front. Returns the inferred-range count
        (surfaced in the completion toast); the SUCCESS/ERROR result is handled in
        on_worker_state_changed."""
        last = 0.0
        cancel = self._cancels.get(vod_id)

        def on_progress(done: int, total: int | None) -> None:
            nonlocal last
            now = time.monotonic()
            if now - last >= 0.2:  # throttle cross-thread UI hops to ~5/s
                last = now
                self.app.call_from_thread(
                    self._on_download_progress, vod_id, done, total
                )

        fetcher.fetch_by_url(
            vod_id,
            self.app.config,
            on_progress=on_progress,
            should_cancel=cancel.is_set if cancel else None,
        )
        # Chat is on disk now. Infer watched ranges here, on the worker thread,
        # so the row flips straight to real coverage when it completes — no 0%
        # flash that only fills in once the VOD is opened. Skip if we're being
        # torn down (quit signals cancel after a fetch may have already landed).
        if cancel and cancel.is_set():
            return 0
        return _infer_watched(vod_id, self.app.config)

    def cancel_all_downloads(self) -> None:
        """Signal every in-flight download to abort. Called on app shutdown so a
        quit mid-download doesn't hang the process waiting on the worker thread
        (and the partial `.tmp` gets cleaned up by the fetch's own teardown)."""
        for event in self._cancels.values():
            event.set()

    def on_unmount(self) -> None:
        # This screen unmounts only when the app is quitting (it's the base
        # screen) — the reliable spot to release any in-flight downloads.
        self.cancel_all_downloads()

    def _on_download_progress(self, vod_id: str, done: int, total: int | None) -> None:
        if vod_id in self._downloading:  # ignore a late tick after completion
            self._downloading[vod_id] = (done, total)
            self._set_row_status(vod_id, done, total)

    def _set_row_status(self, vod_id: str, done: int, total: int | None) -> None:
        """Show live download progress on a row: a fill bar + percent (how far
        the fetched chat has reached through the VOD) in the otherwise-unused
        coverage cell, plus a spinning marker. Reuses the watched coverage bar's
        format, so it reads the same — the spinner (vs. ⬇) is what marks the row
        as an in-progress download rather than watched coverage, and it animates
        even while the percent sits at 0% during the initial connect."""
        table = self.query_one("#vodlist", DataTable)
        try:
            row = table.get_row_index(vod_id)
        except KeyError:
            return
        table.update_cell_at(Coordinate(row, 3), "")
        table.update_cell_at(Coordinate(row, 4), _dl_indicator(self._spin, done, total))

    def _tick_spinner(self) -> None:
        """Advance the download spinner one frame and repaint every in-flight
        row's marker. Runs only while something is downloading."""
        self._spin = (self._spin + 1) % len(_SPINNER)
        table = self.query_one("#vodlist", DataTable)
        for vod_id, (done, total) in self._downloading.items():
            try:
                row = table.get_row_index(vod_id)
            except KeyError:
                continue
            indicator = _dl_indicator(self._spin, done, total)
            table.update_cell_at(Coordinate(row, 4), indicator)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "downloads":
            return
        vod_id = event.worker.name
        if event.state is WorkerState.SUCCESS:
            self._finish_download(vod_id, ok=True, inferred=event.worker.result or 0)
        elif event.state is WorkerState.ERROR:
            err = event.worker.error
            if isinstance(err, FileExistsError):
                self._finish_download(vod_id, ok=True)  # already on disk
            elif isinstance(err, fetcher.DownloadCancelled):
                self._finish_download(vod_id, ok=False)  # quit/cancel — no toast
            else:
                self._finish_download(vod_id, ok=False, error=str(err))

    def _finish_download(
        self, vod_id: str, *, ok: bool, error: str | None = None, inferred: int = 0
    ) -> None:
        self._downloading.pop(vod_id, None)
        self._cancels.pop(vod_id, None)
        if not self._downloading:
            self._spinner_timer.pause()  # nothing left to animate
        if not ok:
            self._reset_row(vod_id)
            if error:  # a real failure; a bare cancel (quit) passes no error
                self.notify(f"Download failed for {vod_id}: {error}", severity="error")
            return
        self.refresh_row(vod_id)
        msg = f"Downloaded {vod_id}."
        if inferred:  # watched ranges inferred from your chat as part of the fetch
            msg += f" Inferred {inferred} watched range(s) from your chat."
        self.notify(msg)

    def refresh_row(self, vod_id: str) -> None:
        """Flip one row to downloaded in place — no full re-populate, so the
        cursor and any remote rows are preserved."""
        vod = self._rows.get(vod_id)
        if not vod:
            return
        vod["downloaded"] = True
        vod["watched_seconds"] = _watched_seconds(vod_id, self.app.config)
        self._set_row_cells(
            vod_id, _coverage_bar(vod["watched_seconds"], vod["duration_seconds"]), "⬇"
        )

    def _reset_row(self, vod_id: str) -> None:
        """Revert a row's transient download indicator back to its undownloaded
        look — blank watched cell, no marker (used when a download fails)."""
        if vod_id in self._rows:
            self._set_row_cells(vod_id, "", " ")

    def _set_row_cells(self, vod_id: str, coverage: str, marker: str) -> None:
        table = self.query_one("#vodlist", DataTable)
        try:
            row = table.get_row_index(vod_id)
        except KeyError:
            return
        table.update_cell_at(Coordinate(row, 3), coverage)
        table.update_cell_at(Coordinate(row, 4), marker)


class VodScreen(Screen):
    """One VOD: top moments (left) + emotes (right), with an All/Unwatched mode."""

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("w", "toggle_mode", "All/Unwatched"),
        ("e", "edit", "Edit watched"),
        ("i", "infer", "Infer watched"),
        ("f", "favorite", "★ emote"),
        ("/", "favorite_search", "★ search"),
        ("o", "overall", "Overall"),
        ("q", "app.quit", "Quit"),
    ]

    def __init__(self, vod: dict) -> None:
        super().__init__()
        self.vod = vod
        self.show_all = False  # Unwatched is the default view
        self.current_emote: str | None = None  # None = overall chat-volume view
        self.favorites: set[str] = set()  # loaded from the sidecar in on_mount
        self._raw_moments: list[an.Moment] = []  # all moments (watched-flagged)
        self._emote_counts: Counter = Counter()

    @property
    def _streamer(self) -> str:
        return self.app.streamer

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="vodheader")
        with Horizontal(id="panes"):
            yield DataTable(id="moments", cursor_type="row", zebra_stripes=True)
            yield DataTable(id="emotes", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#moments", DataTable).border_title = "Top moments"
        self.query_one(
            "#emotes", DataTable
        ).border_title = "Emotes  (f: ★ · /: search · tab: focus)"
        self.favorites = fav.load(self._streamer, self.app.config.chat_dir)
        self._auto_infer()
        self._refresh_header()
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

    def _recompute_coverage(self) -> None:
        """Refresh the row's watched_seconds after a watched-range change."""
        self.vod["watched_seconds"] = _watched_seconds(self.vod["id"], self.app.config)

    def _auto_infer(self) -> None:
        """On first open of a VOD with no watched file, infer watched ranges from
        the user's own chat and reflect them.

        Usually a no-op now — TUI downloads infer up front (see _infer_watched),
        so the file already exists by the time you open the VOD — but this still
        covers VODs fetched outside the TUI, or before a twitch_username was set.
        """
        count = _infer_watched(self.vod["id"], self.app.config)
        if count:
            self._recompute_coverage()
            self.notify(f"Auto-inferred {count} watched range(s) from your chat.")

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
        self.show_all = not self.show_all
        self._refresh_header()
        self._populate_moments()

    def action_overall(self) -> None:
        if self.current_emote is not None:
            self.current_emote = None
            self._load_moments()
            self._refresh_header()
            self._populate_moments()

    def action_edit(self) -> None:
        """Open the inline watched-range editor; refresh on save."""

        def after(saved: bool | None) -> None:
            if saved:
                self._recompute_coverage()
                self._refresh_header()
                self._load_moments()
                self._populate_moments()
                self.notify("Watched ranges saved.")

        self.app.push_screen(WatchedEditScreen(self.vod["id"], self.app.config), after)

    def action_infer(self) -> None:
        """Re-infer watched ranges from the user's chat and merge them in."""
        config = self.app.config
        if not config.twitch_username:
            self.notify("Set twitch_username in config to infer.", severity="warning")
            return
        try:
            suggested = wt.infer_from_chat(
                self.vod["id"],
                config.twitch_username,
                config.chat_dir,
                config.gap_threshold_seconds,
            )
        except (FileNotFoundError, ValueError) as e:
            self.notify(str(e), severity="error")
            return
        if not suggested:
            self.notify(
                f"No messages from {config.twitch_username!r} in this chat.",
                severity="warning",
            )
            return
        actions.add_ranges(self.vod["id"], config, suggested)
        self._recompute_coverage()
        self._refresh_header()
        self._load_moments()
        self._populate_moments()
        self.notify(f"Inferred {len(suggested)} range(s) (merged).")

    def action_favorite(self) -> None:
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
        self._toggle_favorite(name)

    def action_favorite_search(self) -> None:
        """Open the search-to-favorite picker (`/`): type to filter this VOD's
        emotes, Enter favorites the highlighted match — handy when the emote is
        buried in a long pane. Works regardless of which pane has focus."""
        if not self._emote_counts:
            self.notify("No emotes in this VOD to favorite.")
            return

        def after(name: str | None) -> None:
            if name:
                self._toggle_favorite(name, announce=True)

        self.app.push_screen(
            FavoriteEmotePickerScreen(self._emote_counts, self.favorites), after
        )

    def _toggle_favorite(self, name: str, announce: bool = False) -> None:
        """Add/remove `name` from this streamer's favorites, persist, repaint the
        pane. `announce` notifies which way it flipped — the picker closes over the
        pane, so unlike the inline `f` the result isn't visible without a word."""
        if name in self.favorites:
            self.favorites.discard(name)
            verb = "Unfavorited"
        else:
            self.favorites.add(name)
            verb = "Favorited"
        fav.save(self.favorites, self._streamer, self.app.config.chat_dir)
        self._populate_emotes()
        if announce:
            self.notify(f"{verb} {name}.")

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


class FavoriteEmotePickerScreen(ModalScreen[str | None]):
    """Search-to-favorite picker, opened with `/` from the VOD window.

    Type to filter the VOD's emotes live (case-insensitive substring); ↑↓ move
    the highlight, Enter favorites the highlighted emote, Esc cancels. Scoped to
    emotes present in *this* VOD's chat — the tool keeps no offline catalogue of
    every Twitch emote, and that's the same universe the pane already shows.
    Already-favorited emotes carry a ★. Dismisses with the chosen emote name (the
    caller toggles + persists it) or None on cancel.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("up", "cursor_up", "Up", priority=True),
    ]

    _NAME_WIDTH = 24  # column the count is padded out to; long emotes overflow it

    def __init__(self, emote_counts: Counter, favorites: set[str]) -> None:
        super().__init__()
        self._items = emote_counts.most_common()
        self.favorites = favorites

    def compose(self) -> ComposeResult:
        with Vertical(id="pickbox"):
            yield Static(
                "Favorite an emote — type to filter this VOD's emotes\n"
                "[dim]↑↓ move · enter ★ favorite · esc cancel[/dim]",
                id="pickhint",
            )
            yield Input(placeholder="filter emotes…", id="pickquery")
            yield OptionList(id="pickoptions")

    def on_mount(self) -> None:
        self.query_one("#pickquery", Input).focus()
        self._repopulate("")

    def _repopulate(self, query: str) -> None:
        options = self.query_one("#pickoptions", OptionList)
        options.clear_options()
        matches = _match_emotes(self._items, query)
        if not matches:
            options.add_option(Option(Text("(no match)", style="dim"), disabled=True))
            return
        width = min(max(len(name) for name, _ in matches), self._NAME_WIDTH)
        for name, n in matches:
            star = "★ " if name in self.favorites else "  "
            label = Text.assemble(star, name.ljust(width), "  ", (str(n), "dim"))
            options.add_option(Option(label, id=name))
        options.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        self._repopulate(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        options = self.query_one("#pickoptions", OptionList)
        if options.highlighted is None:
            return
        option = options.get_option_at_index(options.highlighted)
        if option.id is None:
            return  # the "(no match)" placeholder — keep the picker open
        self.dismiss(option.id)

    def action_cursor_down(self) -> None:
        self.query_one("#pickoptions", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#pickoptions", OptionList).action_cursor_up()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmDownloadScreen(ModalScreen[bool]):
    """Confirm-before-download dialog, shown when you open an undownloaded VOD.

    Requires an explicit `y` to start the (background) download; `n` or `esc`
    backs out. Enter is deliberately not bound — the same key that opened this
    dialog shouldn't also confirm it. Dismisses True on confirm, False on cancel.
    """

    BINDINGS = [
        Binding("y", "confirm", "Download", priority=True),
        Binding("n", "cancel", "Cancel", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(self, vod: dict, streamer: str) -> None:
        super().__init__()
        self.vod = vod
        self.streamer = streamer

    def compose(self) -> ComposeResult:
        v = self.vod
        date = (v["created_at"] or "")[:10] or "unknown date"
        dur = fetcher._format_duration(v["duration_seconds"])
        with Vertical(id="confirmbox"):
            yield Static(
                "[b]Download chat for this VOD?[/b]\n\n"
                f"{v['title'] or '(no title)'}\n"
                f"[dim]{self.streamer} · {date} · {dur}[/dim]\n\n"
                "[dim]Runs in the background — keep browsing.\n"
                "y download · esc/n cancel[/dim]",
                id="confirmtext",
            )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ConfirmQuitScreen(ModalScreen[bool]):
    """Confirm-before-quit dialog, shown only when a chat download is in flight.

    Quitting aborts in-flight downloads (the workers are signalled to cancel on
    shutdown and the partial `.tmp` is discarded), so an active download is worth
    a deliberate `y`. `n`/`esc` stays in the app and keeps downloading. Like
    ConfirmDownloadScreen, Enter is not bound. Dismisses True to quit.
    """

    BINDINGS = [
        Binding("y", "confirm", "Quit", priority=True),
        Binding("n", "cancel", "Stay", priority=True),
        Binding("escape", "cancel", "Stay", priority=True),
    ]

    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count

    def compose(self) -> ComposeResult:
        n = self.count
        subject = "1 chat download is" if n == 1 else f"{n} chat downloads are"
        them = "it" if n == 1 else "them"
        logs = "log" if n == 1 else "logs"
        with Vertical(id="confirmbox"):
            yield Static(
                "[b]Quit and stop downloading?[/b]\n\n"
                f"{subject} still running — quitting\n"
                f"cancels {them} and discards the partial {logs}.\n\n"
                "[dim]y quit · esc/n keep downloading[/dim]",
                id="confirmtext",
            )

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class WatchedEditScreen(ModalScreen[bool]):
    """Inline editor for a VOD's watched ranges, one `H:MM:SS-H:MM:SS` per line.

    Editing is authoritative — a full replace, not a merge: deleting a line drops
    that range, and clearing the box clears the ranges. Each line is parsed by
    `watched.parse_range`, so open-ended forms (e.g. `2:00:00-end`) still work; a
    bad line shows an error and keeps the editor open. Dismisses True on save,
    False on cancel.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+s", "save", "Save", priority=True),
    ]

    def __init__(self, vod_id: str, config: "cfg.Config") -> None:
        super().__init__()
        self.vod_id = vod_id
        self.config = config

    def compose(self) -> ComposeResult:
        ranges = wt.load(self.vod_id, self.config.chat_dir).ranges
        initial = "\n".join(
            f"{an._format_timestamp(r.start_seconds)}-"
            f"{an._format_timestamp(r.end_seconds)}"
            for r in ranges
        )
        with Vertical(id="editbox"):
            yield Static(
                "Edit watched ranges — one per line · H:MM:SS-H:MM:SS\n"
                "[dim]ctrl-s save · esc cancel[/dim]",
                id="edithint",
            )
            yield TextArea(initial, id="editarea")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_save(self) -> None:
        text = self.query_one("#editarea", TextArea).text
        parsed: list[wt.WatchedRange] = []
        for line in (ln.strip() for ln in text.splitlines()):
            if not line:
                continue
            try:
                parsed.append(
                    wt.parse_range(
                        line,
                        end_resolver=lambda: wt.vod_end_seconds(
                            self.vod_id, self.config.chat_dir
                        ),
                    )
                )
            except (ValueError, FileNotFoundError) as e:
                self.notify(f"{line!r}: {e}", severity="error")
                return
        if parsed:
            wt.save(wt.WatchedRanges(parsed, ""), self.vod_id, self.config.chat_dir)
        else:
            wt.clear(self.vod_id, self.config.chat_dir)
        self.dismiss(True)


class VodscoutApp(App):
    """Top-level Textual app. Holds the cross-screen state the screens read:
    the resolved config, the current streamer, and the offline flag."""

    CSS = """
    #vodlist { height: 1fr; }

    #vodheader { height: auto; padding: 1 2; background: $panel; }
    #panes { height: 1fr; }
    #moments { width: 2fr; border: round $primary; }
    #emotes { width: 1fr; border: round $primary; }

    ConfirmDownloadScreen, ConfirmQuitScreen { align: center middle; }
    #confirmbox {
        width: 60; height: auto; padding: 1 2;
        background: $surface; border: round $accent;
    }
    #confirmtext { height: auto; }

    WatchedEditScreen { align: center middle; }
    #editbox {
        width: 72; height: auto; padding: 1 2;
        background: $surface; border: round $accent;
    }
    #edithint { height: auto; padding-bottom: 1; }
    #editarea { height: 12; }

    FavoriteEmotePickerScreen { align: center middle; }
    #pickbox {
        width: 60; height: auto; padding: 1 2;
        background: $surface; border: round $accent;
    }
    #pickhint { height: auto; padding-bottom: 1; }
    #pickquery { margin-bottom: 1; }
    #pickoptions { height: 12; }
    """

    def __init__(
        self, config: "cfg.Config", streamer: str, offline: bool = False
    ) -> None:
        super().__init__()
        self.config = config
        self.streamer = streamer
        self.offline = offline

    def on_mount(self) -> None:
        self.title = "vodscout"
        self.sub_title = self.streamer
        self.push_screen(VodListScreen())

    def _vodlist_screen(self) -> "VodListScreen | None":
        """The always-mounted base list screen, which owns download state."""
        for screen in self.screen_stack:
            if isinstance(screen, VodListScreen):
                return screen
        return None

    async def action_quit(self) -> None:
        """Quit — but if any chat download is still running, confirm first, since
        quitting aborts it (the worker is cancelled on shutdown and its partial
        chat log discarded). Overriding the app's quit action covers every quit
        entry point (q on the list and the VOD window both bind to app.quit)."""
        base = self._vodlist_screen()
        if (
            base
            and base._downloading
            and not isinstance(self.screen, ConfirmQuitScreen)
        ):
            self.push_screen(
                ConfirmQuitScreen(len(base._downloading)), self._after_quit_confirm
            )
            return
        self.exit()

    def _after_quit_confirm(self, confirm: bool | None) -> None:
        if confirm:
            self.exit()


def run_shell(
    config: "cfg.Config", streamer: str | None = None, *, offline: bool = False
) -> None:
    """Entry point for `vodscout browse` and bare `vodscout`.

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
    VodscoutApp(config, streamer, offline).run()
