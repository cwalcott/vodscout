import pytest

from vodchat.fetcher import _vod_id_from_url


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
