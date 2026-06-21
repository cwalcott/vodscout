import pytest

from vodchat.config import Config
from vodchat.fetcher import (
    _scan_third_party,
    _vod_id_from_url,
    downloaded_ids,
    parse_selection,
    undownloaded_vods,
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


def test_downloaded_ids(tmp_path):
    config = Config(chat_dir=tmp_path)
    assert downloaded_ids("shroud", config) == set()  # no dir yet

    streamer_dir = tmp_path / "shroud"
    streamer_dir.mkdir()
    (streamer_dir / "111.txt").write_text("")
    (streamer_dir / "222.txt").write_text("")
    (streamer_dir / "222.watched.json").write_text("{}")  # not a .txt log
    assert downloaded_ids("shroud", config) == {"111", "222"}
