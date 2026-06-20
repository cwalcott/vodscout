import pytest

from vodchat.analyzer import (
    MIN_BASELINE,
    MIN_BASELINE_SAMPLES,
    Moment,
    _format_timestamp,
    _vod_link,
    detect_spikes,
    mark_watched,
)


def _moment(ts: int) -> Moment:
    return Moment(
        timestamp_seconds=ts,
        signals=["chat-rate"],
        magnitude=2.0,
        samples=[],
        watched=False,
    )


def _msgs(bucket_idx: int, count: int, bucket_seconds: int = 60) -> list[dict]:
    t = bucket_idx * bucket_seconds + 1
    return [{"time": t, "user": "u", "msg": f"m{i}"} for i in range(count)]


def _build(buckets: dict[int, int], bucket_seconds: int = 60) -> list[dict]:
    msgs = []
    for b, count in buckets.items():
        msgs.extend(_msgs(b, count, bucket_seconds))
    return msgs


# ── detect_spikes ────────────────────────────────────────────────────────────


def test_empty_messages():
    assert detect_spikes([], 60) == []


def test_flat_activity_no_spikes():
    # 35 steady buckets — nothing should exceed 3x of itself
    msgs = _build({i: 10 for i in range(35)})
    assert detect_spikes(msgs, 60) == []


def test_single_surge_produces_one_moment():
    # 30 buckets of 5 msgs establish a baseline of 5.0
    # bucket 30 has 50 msgs → 50 / 5 = 10x → spike
    msgs = _build({i: 5 for i in range(30)} | {30: 50})
    moments = detect_spikes(msgs, 60)
    assert len(moments) == 1
    m = moments[0]
    assert m.timestamp_seconds == 30 * 60
    assert m.magnitude == pytest.approx(10.0)
    assert m.signals == ["chat-rate"]
    assert m.watched is False


def test_adjacent_buckets_merge_into_one_moment():
    # 30 quiet buckets, then 3 consecutive spikes — merged into one moment
    base = {i: 5 for i in range(30)}
    surges = {30: 30, 31: 50, 32: 30}  # peak at bucket 31
    msgs = _build(base | surges)
    moments = detect_spikes(msgs, 60)
    assert len(moments) == 1
    m = moments[0]
    # Peak bucket is 31; baseline at 31 includes bucket 30 (30 msgs) in the window,
    # so baseline = (5*29 + 30) / 30 = 175/30 ≈ 5.83, not 5.0
    assert m.timestamp_seconds == 31 * 60
    expected_baseline = (5 * 29 + 30) / 30
    assert m.magnitude == pytest.approx(50 / expected_baseline, abs=0.01)


def test_two_separate_surges_produce_two_moments():
    base = {i: 5 for i in range(30)}
    surges = {30: 50, 60: 50}  # gap of 29 non-flagged buckets between them
    msgs = _build(base | {i: 5 for i in range(31, 60)} | surges)
    moments = detect_spikes(msgs, 60)
    assert len(moments) == 2


def test_near_zero_baseline_suppresses_spike():
    # Prior buckets have only 1 msg each → baseline = 1.0 < MIN_BASELINE (2.0)
    # bucket 10 has 100 msgs but should NOT be flagged
    msgs = _build({i: 1 for i in range(10)} | {10: 100})
    assert MIN_BASELINE > 1.0  # sanity-check the constant
    moments = detect_spikes(msgs, 60)
    assert moments == []


def test_insufficient_prior_buckets_suppresses_spike():
    # Only 2 prior buckets → < MIN_BASELINE_SAMPLES (3) → skip
    msgs = _build({0: 10, 1: 10, 2: 100})
    assert MIN_BASELINE_SAMPLES == 3
    moments = detect_spikes(msgs, 60)
    assert moments == []


def test_top_tokens_from_run():
    base = {i: 5 for i in range(30)}
    # Surge: 15 "KEKW" and 5 "lol" — KEKW should rank first
    surge_msgs = [
        {"time": 30 * 60 + 1, "user": "u", "msg": "KEKW"} for _ in range(15)
    ] + [{"time": 30 * 60 + 1, "user": "u", "msg": "lol"} for _ in range(5)]
    msgs = _build(base) + surge_msgs
    moments = detect_spikes(msgs, 60)
    assert len(moments) == 1
    assert len(moments[0].samples) <= 5
    assert moments[0].samples[0].startswith("KEKW")


# ── mark_watched ──────────────────────────────────────────────────────────────


def test_mark_watched_inside_range():
    moments = [_moment(100), _moment(5000)]
    mark_watched(moments, [(0, 1000)])
    assert moments[0].watched is True
    assert moments[1].watched is False


def test_mark_watched_boundaries_half_open():
    # start inclusive, end exclusive
    moments = [_moment(0), _moment(1000)]
    mark_watched(moments, [(0, 1000)])
    assert moments[0].watched is True
    assert moments[1].watched is False


def test_mark_watched_no_ranges_leaves_all_unwatched():
    moments = [_moment(100), _moment(200)]
    mark_watched(moments, [])
    assert all(not m.watched for m in moments)


def test_mark_watched_multiple_ranges():
    moments = [_moment(100), _moment(5000), _moment(9000)]
    mark_watched(moments, [(0, 1000), (8000, 10000)])
    assert [m.watched for m in moments] == [True, False, True]


# ── formatting helpers ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0:00:00"),
        (59, "0:00:59"),
        (60, "0:01:00"),
        (3600, "1:00:00"),
        (5025, "1:23:45"),
        (86399, "23:59:59"),
    ],
)
def test_format_timestamp(seconds, expected):
    assert _format_timestamp(seconds) == expected


@pytest.mark.parametrize(
    "seconds,expected_t",
    [
        (0, "0h00m00s"),
        (5025, "1h23m45s"),
        (3661, "1h01m01s"),
    ],
)
def test_vod_link(seconds, expected_t):
    link = _vod_link("123456", seconds)
    assert link == f"https://www.twitch.tv/videos/123456?t={expected_t}"
