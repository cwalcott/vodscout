import json

import pytest

from vodchat import fetcher
from vodchat.config import Config
from vodchat.fetcher import (
    _scan_third_party,
    _vod_id_from_url,
    cached_vods,
    downloaded_ids,
    parse_selection,
    remove_cached_meta,
    undownloaded_vods,
    write_remote_meta,
)


@pytest.mark.parametrize(
    "input,expected",
    [
        ("https://www.twitch.tv/videos/2334567890", "2334567890"),
        ("https://twitch.tv/videos/2334567890", "2334567890"),
        ("2334567890", "2334567890"),
        ("  2334567890  ", "2334567890"),
    ],
)
def test_vod_id_from_url_valid(input, expected):
    assert _vod_id_from_url(input) == expected


@pytest.mark.parametrize(
    "bad_input",
    [
        "not_a_vod",
        "https://twitch.tv/streamer_name",
        "",
        "abc123",
    ],
)
def test_vod_id_from_url_invalid(bad_input):
    with pytest.raises(ValueError, match="Cannot parse VOD ID"):
        _vod_id_from_url(bad_input)


@pytest.mark.parametrize(
    "text,count,expected",
    [
        ("", 5, []),
        ("all", 3, [0, 1, 2]),
        ("ALL", 3, [0, 1, 2]),
        ("1", 5, [0]),
        ("1,3,5", 5, [0, 2, 4]),
        ("1 3  5", 5, [0, 2, 4]),
        ("3, 1", 5, [0, 2]),  # sorted
        ("2,2,2", 5, [1]),  # deduped
    ],
)
def test_parse_selection_valid(text, count, expected):
    assert parse_selection(text, count) == expected


@pytest.mark.parametrize(
    "text,count",
    [
        ("0", 5),
        ("6", 5),
        ("abc", 5),
        ("1,x", 5),
    ],
)
def test_parse_selection_invalid(text, count):
    with pytest.raises(ValueError):
        parse_selection(text, count)


@pytest.mark.parametrize(
    "text,known,expected",
    [
        ("catJAM Pepega lol", {"catJAM", "Pepega"}, ["catJAM", "Pepega"]),
        ("catJAM catJAM catJAM", {"catJAM"}, ["catJAM", "catJAM", "catJAM"]),
        ("nothing here", {"catJAM"}, []),
        ("", {"catJAM"}, []),
        ("plain text only", set(), []),
        # whole-token match only — substrings don't count
        ("catJAMMER", {"catJAM"}, []),
    ],
)
def test_scan_third_party(text, known, expected):
    assert _scan_third_party(text, known) == expected


def _vod(vid):
    return {"id": vid, "title": "t", "created_at": "2026-06-20T00:00:00Z"}


def test_undownloaded_vods_filters_existing(tmp_path):
    config = Config(chat_dir=tmp_path)
    streamer_dir = tmp_path / "shroud"
    streamer_dir.mkdir()
    (streamer_dir / "111.txt").write_text("")  # already downloaded

    videos = [_vod("111"), _vod("222"), _vod("333")]
    result = undownloaded_vods(videos, "shroud", config)
    assert [v["id"] for v in result] == ["222", "333"]


def test_undownloaded_vods_no_local_dir(tmp_path):
    config = Config(chat_dir=tmp_path)
    videos = [_vod("111"), _vod("222")]
    result = undownloaded_vods(videos, "shroud", config)
    assert [v["id"] for v in result] == ["111", "222"]


def _remote_row(vid):
    return {
        "id": vid,
        "title": f"title {vid}",
        "user_login": "shroud",
        "created_at": "2026-06-20T00:00:00Z",
        "duration_seconds": 3600,
    }


def test_cached_vods_excludes_downloaded_and_reads_sidecar(tmp_path):
    config = Config(chat_dir=tmp_path)
    streamer_dir = tmp_path / "shroud"
    streamer_dir.mkdir()
    write_remote_meta(streamer_dir, _remote_row("111"))  # downloaded below
    write_remote_meta(streamer_dir, _remote_row("222"))  # cache-only
    (streamer_dir / "111.txt").write_text("")  # 111 is downloaded

    result = cached_vods("shroud", config)
    assert [v["id"] for v in result] == ["222"]
    assert result[0]["title"] == "title 222"
    assert result[0]["duration_seconds"] == 3600


def test_cached_vods_no_dir(tmp_path):
    assert cached_vods("nobody", Config(chat_dir=tmp_path)) == []


def test_cached_vods_skips_unreadable_sidecar(tmp_path):
    config = Config(chat_dir=tmp_path)
    streamer_dir = tmp_path / "shroud"
    streamer_dir.mkdir()
    (streamer_dir / "222.meta.json").write_text("{not valid json")
    write_remote_meta(streamer_dir, _remote_row("333"))

    assert [v["id"] for v in cached_vods("shroud", config)] == ["333"]


def test_remove_cached_meta(tmp_path):
    streamer_dir = tmp_path / "shroud"
    streamer_dir.mkdir()
    write_remote_meta(streamer_dir, _remote_row("222"))
    meta_path = streamer_dir / "222.meta.json"
    assert json.loads(meta_path.read_text())["id"] == "222"

    remove_cached_meta(streamer_dir, "222")
    assert not meta_path.exists()
    remove_cached_meta(streamer_dir, "222")  # idempotent — no error when missing


def test_fetch_by_url_on_progress_drives_hook_no_rich(tmp_path, monkeypatch):
    config = Config(chat_dir=tmp_path)
    monkeypatch.setattr(
        fetcher,
        "_video_metadata",
        lambda vid: {
            "title": "T",
            "lengthSeconds": 100,
            "publishedAt": "2026-06-22T00:00:00Z",
            "owner": {"login": "shroud", "id": "42"},
        },
    )
    monkeypatch.setattr(fetcher, "_third_party_emotes", lambda uid, sc=None: set())
    msgs = [
        {"time": 10, "user": "a", "msg": "hi"},
        {"time": 90, "user": "b", "msg": "yo"},
    ]
    monkeypatch.setattr(fetcher, "_iter_messages", lambda session, vid, tp: iter(msgs))

    seen: list[tuple[int, int | None]] = []
    out = fetcher.fetch_by_url(
        "123", config, on_progress=lambda d, t: seen.append((d, t))
    )

    assert out == tmp_path / "shroud" / "123.txt"
    assert out.read_text().splitlines() == [
        json.dumps(m, ensure_ascii=False) for m in msgs
    ]
    # Hook called once per message with (completed_seconds, total_seconds).
    assert seen == [(10, 100), (90, 100)]
    assert (tmp_path / "shroud" / "123.meta.json").exists()


def test_fetch_by_url_cancel_aborts_and_cleans_tmp(tmp_path, monkeypatch):
    config = Config(chat_dir=tmp_path)
    monkeypatch.setattr(
        fetcher,
        "_video_metadata",
        lambda vid: {
            "title": "T",
            "lengthSeconds": 100,
            "publishedAt": "",
            "owner": {"login": "shroud", "id": "42"},
        },
    )
    monkeypatch.setattr(fetcher, "_third_party_emotes", lambda uid, sc=None: set())
    msgs = [{"time": t, "user": "a", "msg": "hi"} for t in (10, 20, 30, 40)]
    monkeypatch.setattr(fetcher, "_iter_messages", lambda session, vid, tp: iter(msgs))

    n = {"calls": 0}

    def should_cancel():
        n["calls"] += 1
        return n["calls"] > 3  # let a couple messages write, then cancel

    with pytest.raises(fetcher.DownloadCancelled):
        fetcher.fetch_by_url("123", config, should_cancel=should_cancel)

    sdir = tmp_path / "shroud"
    assert not (sdir / "123.txt").exists()  # no partial log committed
    assert not (sdir / "123.tmp").exists()  # temp file cleaned up
    assert not (sdir / "123.meta.json").exists()  # meta only written on success


def test_fetch_by_url_no_messages_writes_nothing(tmp_path, monkeypatch):
    config = Config(chat_dir=tmp_path)
    monkeypatch.setattr(
        fetcher,
        "_video_metadata",
        lambda vid: {
            "title": "T",
            "lengthSeconds": 100,
            "publishedAt": "",
            "owner": {"login": "shroud", "id": "42"},
        },
    )
    monkeypatch.setattr(fetcher, "_third_party_emotes", lambda uid, sc=None: set())
    monkeypatch.setattr(fetcher, "_iter_messages", lambda session, vid, tp: iter([]))

    with pytest.raises(ValueError, match="No chat messages"):
        fetcher.fetch_by_url("123", config, on_progress=lambda d, t: None)
    # The tmp file is cleaned up and no log is left behind.
    assert not (tmp_path / "shroud" / "123.txt").exists()
    assert not (tmp_path / "shroud" / "123.tmp").exists()


def test_downloaded_ids(tmp_path):
    config = Config(chat_dir=tmp_path)
    assert downloaded_ids("shroud", config) == set()  # no dir yet

    streamer_dir = tmp_path / "shroud"
    streamer_dir.mkdir()
    (streamer_dir / "111.txt").write_text("")
    (streamer_dir / "222.txt").write_text("")
    (streamer_dir / "222.watched.json").write_text("{}")  # not a .txt log
    assert downloaded_ids("shroud", config) == {"111", "222"}
