import json

from vodchat import fetcher, vodlist
from vodchat.config import Config


def _remote(
    vid, *, title="t", login="shroud", created="2026-06-20T00:00:00Z", dur=3600
):
    return {
        "id": vid,
        "title": title,
        "user_login": login,
        "created_at": created,
        "duration_seconds": dur,
    }


def _streamer_dir(tmp_path, streamer="shroud"):
    d = tmp_path / streamer
    d.mkdir(parents=True, exist_ok=True)
    return d


def _patch_remote(monkeypatch, videos):
    monkeypatch.setattr(fetcher, "list_remote_vods", lambda streamer: videos)


def _meta(streamer_dir, vid):
    path = streamer_dir / f"{vid}.meta.json"
    return json.loads(path.read_text()) if path.exists() else None


def test_remote_refresh_caches_all_recent_vods(tmp_path, monkeypatch):
    config = Config(chat_dir=tmp_path)
    sdir = _streamer_dir(tmp_path)
    (sdir / "111.txt").write_text("")  # one already downloaded
    _patch_remote(monkeypatch, [_remote("111"), _remote("222"), _remote("333")])

    rows, login, note = vodlist.merged_vods("shroud", config, offline=False)

    assert note is None
    assert login == "shroud"
    assert [r["id"] for r in rows] == ["333", "222", "111"]  # newest-first by id
    # A sidecar is now cached for every recent VOD, downloaded or not.
    assert _meta(sdir, "222")["title"] == "t"
    assert _meta(sdir, "333") is not None
    assert _meta(sdir, "111") is not None  # downloaded VOD's sidecar refreshed too


def test_cached_undownloaded_vods_show_at_startup_offline(tmp_path, monkeypatch):
    config = Config(chat_dir=tmp_path)
    sdir = _streamer_dir(tmp_path)
    (sdir / "111.txt").write_text("")  # downloaded
    # Seed the cache as a prior remote refresh would have.
    _patch_remote(monkeypatch, [_remote("111"), _remote("222")])
    vodlist.merged_vods("shroud", config, offline=False)

    # Now an offline (startup) load — no network — still surfaces the cached 222.
    rows, _, note = vodlist.merged_vods("shroud", config, offline=True)

    assert note is None
    by_id = {r["id"]: r for r in rows}
    assert by_id["111"]["downloaded"] is True
    assert by_id["222"]["downloaded"] is False  # cached, not downloaded
    assert by_id["222"]["title"] == "t"


def test_prune_drops_undownloaded_cache_no_longer_recent(tmp_path, monkeypatch):
    config = Config(chat_dir=tmp_path)
    sdir = _streamer_dir(tmp_path)
    _patch_remote(monkeypatch, [_remote("111"), _remote("222")])
    vodlist.merged_vods("shroud", config, offline=False)
    assert _meta(sdir, "111") is not None and _meta(sdir, "222") is not None

    # 111 ages off Twitch's recent list; a fresh refresh should prune its cache.
    _patch_remote(monkeypatch, [_remote("222"), _remote("333")])
    rows, _, _ = vodlist.merged_vods("shroud", config, offline=False)

    assert [r["id"] for r in rows] == ["333", "222"]
    assert _meta(sdir, "111") is None  # pruned sidecar
    assert _meta(sdir, "333") is not None


def test_prune_never_drops_downloaded_aged_off_vod(tmp_path, monkeypatch):
    config = Config(chat_dir=tmp_path)
    sdir = _streamer_dir(tmp_path)
    (sdir / "111.txt").write_text("")  # downloaded, will age off Twitch
    fetcher.write_remote_meta(sdir, _remote("111"))
    _patch_remote(monkeypatch, [_remote("222"), _remote("333")])

    rows, _, _ = vodlist.merged_vods("shroud", config, offline=False)

    by_id = {r["id"]: r for r in rows}
    assert by_id["111"]["downloaded"] is True  # kept despite aging off remote
    assert _meta(sdir, "111") is not None  # downloaded sidecar never pruned


def test_remote_failure_keeps_cache_and_local(tmp_path, monkeypatch):
    config = Config(chat_dir=tmp_path)
    sdir = _streamer_dir(tmp_path)
    (sdir / "111.txt").write_text("")  # downloaded
    fetcher.write_remote_meta(sdir, _remote("222"))  # cached undownloaded

    def boom(streamer):
        raise RuntimeError("network down")

    monkeypatch.setattr(fetcher, "list_remote_vods", boom)
    rows, _, note = vodlist.merged_vods("shroud", config, offline=False)

    assert "Couldn't reach Twitch" in note
    assert {r["id"] for r in rows} == {"111", "222"}  # nothing pruned on failure
    assert _meta(sdir, "222") is not None
