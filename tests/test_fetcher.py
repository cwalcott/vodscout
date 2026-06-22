import json

import pytest

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


def test_downloaded_ids(tmp_path):
    config = Config(chat_dir=tmp_path)
    assert downloaded_ids("shroud", config) == set()  # no dir yet

    streamer_dir = tmp_path / "shroud"
    streamer_dir.mkdir()
    (streamer_dir / "111.txt").write_text("")
    (streamer_dir / "222.txt").write_text("")
    (streamer_dir / "222.watched.json").write_text("{}")  # not a .txt log
    assert downloaded_ids("shroud", config) == {"111", "222"}
