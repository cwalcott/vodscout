import asyncio
import json

from textual.widgets import DataTable, Input

from vodscout import actions, analyzer, watched
from vodscout.config import Config
from vodscout.ui import VodscoutApp, VodScreen


def messages():
    return [
        {"time": t, "msg": msg, "user": "someone"}
        for t, msg in [
            (1, "ORANGE Orange"),
            (9, "orange"),
            (10, "Orange"),
            (11, "other"),
            (20, "orange"),
            (21, "orange"),
        ]
    ]


def test_literal_search_counts_messages_without_emote_metadata():
    windows = analyzer.frequency_windows(messages(), "OrAn")
    assert [(w.start_seconds, w.count) for w in windows] == [(0, 2), (10, 1), (20, 2)]
    assert analyzer.frequency_windows(messages(), ".*") == []
    assert analyzer.frequency_windows(messages(), "") == []
    assert [w.count for w in analyzer.frequency_windows(messages(), None)] == [2, 2, 2]


def test_scope_excludes_watched_messages_before_counting():
    windows = analyzer.frequency_windows(
        messages(), "orange", excluded_ranges=[(0, 9), (10, 20)]
    )
    assert [(w.start_seconds, w.end_seconds, w.count) for w in windows] == [
        (0, 10, 1),
        (20, 30, 2),
    ]


def test_search_reads_watched_and_preserves_history(tmp_path):
    config = make_chat(tmp_path)
    watched.save(
        watched.WatchedRanges([watched.WatchedRange(0, 10, "manual")], ""),
        "111",
        tmp_path,
    )
    assert [w.start_seconds for w in actions.search("111", config, "orange")] == [
        10,
        20,
    ]
    windows = actions.search("111", config, "orange", include_watched=True)
    assert [w.watched for w in windows] == [True, False, False]
    assert len(watched.load("111", tmp_path).ranges) == 1


def make_chat(tmp_path):
    root = tmp_path / "streamer"
    root.mkdir(exist_ok=True)
    (root / "111.txt").write_text("\n".join(map(json.dumps, messages())))
    (root / "favorites.json").write_text('{"emotes": ["Orange"]}')
    return Config(chat_dir=tmp_path)


def test_exact_emote_excludes_longer_names_and_counts_repeats_once():
    chat = [
        {"time": 1, "msg": "OOOO OOOO"},
        {"time": 2, "msg": "LMAOOOOOOO"},
        {"time": 3, "msg": "oooo"},
        {"time": 4, "msg": "native fragment", "emotes": ["OOOO"]},
    ]
    exact = analyzer.frequency_windows(chat, "OOOO", exact_emote=True)
    partial = analyzer.frequency_windows(chat, "oooo")
    assert exact[0].count == 3
    assert partial[0].count == 3
    assert analyzer.frequency_windows(chat[1:2], "OOOO", exact_emote=True) == []


def test_emote_first_ui_search_sort_and_mark(tmp_path):
    """One integration smoke test; frequency semantics are unit-tested above."""
    size = (80, 24)

    async def run():
        config = make_chat(tmp_path)
        app = VodscoutApp(config, "streamer", offline=True)
        async with app.run_test(size=size) as pilot:
            screen = VodScreen(
                {"id": "111", "title": "Test", "created_at": "", "duration_seconds": 30}
            )
            await app.push_screen(screen)
            await pilot.pause()  # Let mount and focus events settle.
            assert screen.query_one("#emotes", DataTable).has_focus
            assert not screen.query_one("#searchbar").display
            assert screen.sort_by_count
            assert not screen.query("#minimum, #searchstart, #searchend")
            # Saved emotes without provider metadata still match full tokens.
            await pilot.press("enter")
            assert screen.current_emote == "Orange"
            assert screen.exact_emote
            assert [w.count for w in screen._visible(screen._raw_moments)] == [2, 2, 1]
            screen.action_mark_watched()
            assert [w.start_seconds for w in screen._raw_moments] == [10, 20]
            screen.action_toggle_mode()
            assert len(screen._raw_moments) == 3
            assert screen._raw_moments[0].watched
            screen.action_sort()
            assert not screen.sort_by_count
            await pilot.press("/")
            assert screen.query_one("#searchbar").display
            query = screen.query_one("#searchquery", Input)
            await pilot.press(*"oran")
            assert query.value == "oran"
            assert query.region.right <= size[0]
            await pilot.press("enter")
            assert not screen.exact_emote
            assert len(screen._raw_moments) == 3
            assert not screen.query_one("#searchbar").display
            await pilot.press("/")
            await pilot.press("escape")
            assert not screen.query_one("#searchbar").display
            assert screen.query_one("#emotes", DataTable).has_focus
            screen.action_overall()
            assert screen.current_emote is None
            assert screen.sort_by_count
            assert sum(w.count for w in screen._raw_moments) == 6

    asyncio.run(run())
