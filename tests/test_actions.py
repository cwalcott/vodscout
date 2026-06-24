import json

import pytest

from vodscout import actions
from vodscout.config import Config
from vodscout.watched import WatchedRange, WatchedRanges, save


def _write_log(chat_dir, streamer, vod_id, messages):
    streamer_dir = chat_dir / streamer
    streamer_dir.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(m) for m in messages)
    (streamer_dir / f"{vod_id}.txt").write_text(lines + "\n")


def _spiky_messages(emote=None):
    """30 quiet buckets then a 10x volume spike at bucket 30."""
    msgs = []
    for b in range(30):
        for _ in range(5):
            m = {"time": b * 60 + 1, "user": "u", "msg": "x"}
            if emote:
                m["emotes"] = [emote]
            msgs.append(m)
    for _ in range(50):
        m = {"time": 30 * 60 + 1, "user": "u", "msg": "x"}
        if emote:
            m["emotes"] = [emote]
        msgs.append(m)
    return msgs


@pytest.fixture
def config(tmp_path):
    return Config(chat_dir=tmp_path)


def test_analyze_overall_finds_spike(config):
    _write_log(config.chat_dir, "shroud", "111", _spiky_messages())
    result = actions.analyze("111", config)
    assert result.emote is None
    assert result.emote_matches == []
    assert len(result.moments) == 1
    assert result.moments[0].timestamp_seconds == 30 * 60


def test_analyze_emote_view(config):
    _write_log(config.chat_dir, "shroud", "111", _spiky_messages(emote="KEKW"))
    result = actions.analyze("111", config, emote="kekw")  # forgiving match
    assert result.emote == "KEKW"
    assert len(result.moments) == 1
    assert result.moments[0].count == 50


def test_analyze_emote_not_found_raises(config):
    _write_log(config.chat_dir, "shroud", "111", _spiky_messages(emote="KEKW"))
    with pytest.raises(actions.EmoteNotFound):
        actions.analyze("111", config, emote="NeverUsed")


def test_analyze_filters_watched_by_default(config):
    _write_log(config.chat_dir, "shroud", "111", _spiky_messages())
    # Mark the spike's bucket (30*60) as watched -> dropped by default.
    save(
        WatchedRanges([WatchedRange(0, 31 * 60, "manual")], ""), "111", config.chat_dir
    )
    assert actions.analyze("111", config).moments == []
    # include_watched opts the watched moment back in.
    assert len(actions.analyze("111", config, include_watched=True).moments) == 1


def test_analyze_missing_log_raises(config):
    with pytest.raises(FileNotFoundError):
        actions.analyze("999", config)


def test_emote_counts(config):
    msgs = [
        {"time": 1, "user": "u", "msg": "x", "emotes": ["A", "B"]},
        {"time": 2, "user": "u", "msg": "x", "emotes": ["A"]},
    ]
    _write_log(config.chat_dir, "shroud", "111", msgs)
    counts = actions.emote_counts("111", config)
    assert counts["A"] == 2
    assert counts["B"] == 1


def test_delete_vod_removes_log_and_sidecars(config):
    _write_log(config.chat_dir, "shroud", "111", _spiky_messages())
    streamer_dir = config.chat_dir / "shroud"
    (streamer_dir / "111.meta.json").write_text("{}")
    save(WatchedRanges([WatchedRange(0, 60, "manual")], ""), "111", config.chat_dir)

    removed = actions.delete_vod("111", config)

    assert len(removed) == 3
    assert not (streamer_dir / "111.txt").exists()
    assert not (streamer_dir / "111.meta.json").exists()
    assert not (streamer_dir / "111.watched.json").exists()


def test_delete_vod_only_removes_existing_sidecars(config):
    # Log with no sidecars -> only the .txt is removed.
    _write_log(config.chat_dir, "shroud", "111", _spiky_messages())
    removed = actions.delete_vod("111", config)
    assert len(removed) == 1


def test_delete_vod_missing_raises(config):
    with pytest.raises(FileNotFoundError):
        actions.delete_vod("999", config)


def test_add_ranges_merges_and_persists(config):
    _write_log(config.chat_dir, "shroud", "111", _spiky_messages())
    actions.add_ranges("111", config, [WatchedRange(0, 600, "manual")])
    # A second, overlapping range merges with the first on save.
    result = actions.add_ranges("111", config, [WatchedRange(300, 1200, "manual")])
    assert result.ranges == [WatchedRange(0, 1200, "manual")]
    # Persisted: a fresh load sees the merged range.
    from vodscout import watched

    assert watched.load("111", config.chat_dir).ranges == [
        WatchedRange(0, 1200, "manual")
    ]
