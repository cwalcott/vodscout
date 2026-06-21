import json

import pytest

from vodchat import watched
from vodchat.watched import WatchedRange, WatchedRanges


@pytest.fixture
def chat_dir(tmp_path):
    """A chat_dir with one streamer/VOD log so find_log can locate it."""
    streamer_dir = tmp_path / "shroud"
    streamer_dir.mkdir()
    (streamer_dir / "12345.txt").write_text("")
    return tmp_path


# ── load / save round-trip ────────────────────────────────────────────────────


def test_load_missing_returns_empty(chat_dir):
    w = watched.load("12345", chat_dir)
    assert w.ranges == []
    assert w.last_updated == ""


def test_save_then_load_round_trip(chat_dir):
    w = WatchedRanges(
        ranges=[WatchedRange(0, 6300, "manual")],
        last_updated="",
    )
    watched.save(w, "12345", chat_dir)

    loaded = watched.load("12345", chat_dir)
    assert len(loaded.ranges) == 1
    assert loaded.ranges[0] == WatchedRange(0, 6300, "manual")
    assert loaded.last_updated  # stamped on save


def test_save_writes_sibling_of_log(chat_dir):
    watched.save(WatchedRanges([WatchedRange(0, 60, "manual")], ""), "12345", chat_dir)
    out = chat_dir / "shroud" / "12345.watched.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["ranges"][0]["start_seconds"] == 0


# ── normalization on save ─────────────────────────────────────────────────────


def test_overlapping_ranges_merge(chat_dir):
    w = WatchedRanges(
        ranges=[
            WatchedRange(0, 1000, "manual"),
            WatchedRange(500, 1500, "manual"),
        ],
        last_updated="",
    )
    watched.save(w, "12345", chat_dir)
    loaded = watched.load("12345", chat_dir)
    assert loaded.ranges == [WatchedRange(0, 1500, "manual")]


def test_adjacent_ranges_merge(chat_dir):
    w = WatchedRanges(
        ranges=[WatchedRange(0, 1000, "manual"), WatchedRange(1000, 2000, "manual")],
        last_updated="",
    )
    watched.save(w, "12345", chat_dir)
    loaded = watched.load("12345", chat_dir)
    assert loaded.ranges == [WatchedRange(0, 2000, "manual")]


def test_disjoint_ranges_stay_separate_and_sorted(chat_dir):
    w = WatchedRanges(
        ranges=[
            WatchedRange(5000, 6000, "manual"),
            WatchedRange(0, 1000, "chat-inferred"),
        ],
        last_updated="",
    )
    watched.save(w, "12345", chat_dir)
    loaded = watched.load("12345", chat_dir)
    assert loaded.ranges == [
        WatchedRange(0, 1000, "chat-inferred"),
        WatchedRange(5000, 6000, "manual"),
    ]


def test_manual_wins_when_merging_mixed_sources(chat_dir):
    w = WatchedRanges(
        ranges=[
            WatchedRange(0, 1000, "chat-inferred"),
            WatchedRange(900, 2000, "manual"),
        ],
        last_updated="",
    )
    watched.save(w, "12345", chat_dir)
    loaded = watched.load("12345", chat_dir)
    assert loaded.ranges == [WatchedRange(0, 2000, "manual")]


# ── timestamp / range parsing ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "s,expected",
    [
        ("0", 0),
        ("90", 90),
        ("0:59", 59),
        ("1:30", 90),
        ("0:01:00", 60),
        ("1:23:45", 5025),
        ("  1:00:00  ", 3600),
    ],
)
def test_parse_timestamp(s, expected):
    assert watched._parse_timestamp(s) == expected


def test_parse_timestamp_too_many_parts():
    with pytest.raises(ValueError, match="Invalid timestamp"):
        watched._parse_timestamp("1:2:3:4")


def test_parse_range_colon_form():
    assert watched.parse_range("1:00:00-1:30:00") == WatchedRange(3600, 5400, "manual")


def test_parse_range_raw_seconds_form():
    # rsplit on last '-' keeps all-seconds form unambiguous
    assert watched.parse_range("60-120") == WatchedRange(60, 120, "manual")


def test_parse_range_rejects_non_increasing():
    with pytest.raises(ValueError, match="after start"):
        watched.parse_range("1:00:00-0:30:00")


def test_parse_range_requires_separator():
    with pytest.raises(ValueError, match="START-END"):
        watched.parse_range("3600")


def test_parse_range_open_end_keyword():
    r = watched.parse_range("2:45:00-end", end_resolver=lambda: 12000)
    assert r == WatchedRange(9900, 12000, "manual")


def test_parse_range_open_end_trailing_dash():
    r = watched.parse_range("2:45:00-", end_resolver=lambda: 12000)
    assert r == WatchedRange(9900, 12000, "manual")


def test_parse_range_open_start_empty():
    assert watched.parse_range("-1:00:00") == WatchedRange(0, 3600, "manual")


def test_parse_range_open_start_keyword():
    assert watched.parse_range("start-1:00:00") == WatchedRange(0, 3600, "manual")


def test_parse_range_open_end_without_resolver_errors():
    with pytest.raises(ValueError, match="VOD length"):
        watched.parse_range("2:45:00-end")


def test_parse_range_resolver_not_called_when_end_explicit():
    def boom():
        raise AssertionError("resolver should not be called")

    assert watched.parse_range("0:00-1:00:00", end_resolver=boom) == WatchedRange(
        0, 3600, "manual"
    )


# ── infer_from_chat (gap segmentation) ────────────────────────────────────────


def _write_log(chat_dir, lines):
    """lines: iterable of (time, user) — writes JSON-lines chat log."""
    import json as _json

    log = chat_dir / "shroud" / "12345.txt"
    log.write_text(
        "".join(
            _json.dumps({"time": t, "user": u, "msg": "x"}) + "\n" for t, u in lines
        )
    )


def test_infer_no_matching_user_returns_empty(chat_dir):
    _write_log(chat_dir, [(10, "someone_else")])
    assert watched.infer_from_chat("12345", "me", chat_dir) == []


def test_infer_single_message(chat_dir):
    _write_log(chat_dir, [(1000, "me")])
    ranges = watched.infer_from_chat("12345", "me", chat_dir, gap_threshold_seconds=600)
    assert ranges == [
        WatchedRange(
            1000 - watched.EDGE_PAD_SECONDS,
            1000 + watched.EDGE_PAD_SECONDS,
            "chat-inferred",
        )
    ]


def test_infer_clusters_split_on_gap(chat_dir):
    # Two sessions: 0..120 then a 700s gap (>600), then 900..1000
    _write_log(
        chat_dir,
        [(0, "me"), (60, "me"), (120, "me"), (900, "me"), (1000, "me")],
    )
    ranges = watched.infer_from_chat("12345", "me", chat_dir, gap_threshold_seconds=600)
    assert len(ranges) == 2
    # Interior edges (facing the break) are NOT padded — the full break stays
    # unwatched. Only the outer start/end get the EDGE_PAD cushion.
    assert ranges[0] == WatchedRange(0, 120, "chat-inferred")
    assert ranges[1] == WatchedRange(
        900, 1000 + watched.EDGE_PAD_SECONDS, "chat-inferred"
    )


def test_infer_short_gap_is_bridged(chat_dir):
    # A silence under the threshold doesn't carve a hole — one session.
    _write_log(chat_dir, [(0, "me"), (100, "me"), (200, "me")])
    ranges = watched.infer_from_chat("12345", "me", chat_dir, gap_threshold_seconds=120)
    assert len(ranges) == 1
    assert ranges[0] == WatchedRange(0, 200 + watched.EDGE_PAD_SECONDS, "chat-inferred")


def test_infer_gap_exactly_at_threshold_does_not_split(chat_dir):
    # Gap of exactly 600 is not > 600 → same cluster
    _write_log(chat_dir, [(1000, "me"), (1600, "me")])
    ranges = watched.infer_from_chat("12345", "me", chat_dir, gap_threshold_seconds=600)
    assert len(ranges) == 1


def test_infer_start_clamped_at_zero(chat_dir):
    _write_log(chat_dir, [(20, "me")])
    ranges = watched.infer_from_chat("12345", "me", chat_dir, gap_threshold_seconds=600)
    assert ranges[0].start_seconds == 0


def test_infer_username_case_insensitive(chat_dir):
    _write_log(chat_dir, [(100, "MyName")])
    assert watched.infer_from_chat("12345", "myname", chat_dir) != []
