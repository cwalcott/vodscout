import json

import pytest

from vodchat import actions
from vodchat.config import Config
from vodchat.watched import WatchedRange, WatchedRanges, save


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
