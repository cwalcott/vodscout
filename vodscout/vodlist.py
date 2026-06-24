"""Shared VOD-list orchestration used by both front ends (cli.py and ui.py).

Merges a streamer's local downloads with Twitch's recent VODs into one ordered
list. This is front-end orchestration, not a leg: it composes the fetcher (and
a watched-file existence check) but holds no state and is imported by both the
command-line and interactive front ends so they show the same list.
"""

from vodscout import config as cfg
from vodscout import fetcher


def merged_vods(
    streamer: str, config: "cfg.Config", offline: bool
) -> tuple[list[dict], str, str | None]:
    """Merge local downloads (source of truth) with Twitch's recent VODs.

    Returns (rows newest-first, resolved login, note). Local downloads are never
    dropped; the remote check only adds new VODs and tops up metadata. A remote
    failure is reported via `note`, not raised — local rows still come back.

    Side effect on a successful remote fetch: every recent VOD's metadata is
    cached to a `.meta.json` sidecar (downloaded or not), and undownloaded cache
    entries that have aged off Twitch's recent list are pruned. That cache is
    what lets a later offline load show recent *undownloaded* VODs at startup
    without hitting the network. Downloaded VODs are never pruned.
    """
    streamer_dir = config.chat_dir / streamer
    rows: dict[str, dict] = {}
    for v in fetcher.local_vods(streamer, config):
        rows[v["id"]] = {
            **v,
            "downloaded": True,
            "watched": (streamer_dir / f"{v['id']}.watched.json").exists(),
        }
    # Recent undownloaded VODs cached on the last refresh — shown at startup
    # (offline) without a network call; live remote data tops these up below.
    for v in fetcher.cached_vods(streamer, config):
        rows.setdefault(v["id"], {**v, "downloaded": False, "watched": False})

    login = streamer
    note: str | None = None
    if not offline:
        try:
            remote = fetcher.list_remote_vods(streamer)
        except ValueError as e:  # streamer not found remotely
            note, remote = str(e), None
        except Exception as e:  # offline / network failure — local still shows
            note, remote = f"Couldn't reach Twitch ({e}).", None

        if remote is not None:
            remote_ids = {v["id"] for v in remote}
            for v in remote:
                login = v["user_login"]
                existing = rows.get(v["id"])
                if existing:
                    existing.update(
                        title=v["title"],
                        created_at=v["created_at"],
                        duration_seconds=v["duration_seconds"],
                    )
                else:
                    rows[v["id"]] = {**v, "downloaded": False, "watched": False}
                fetcher.write_remote_meta(streamer_dir, v)
            # Prune undownloaded cache entries no longer in Twitch's recent list,
            # so the list stays "your downloads + Twitch's recent VODs" and the
            # cache doesn't grow unbounded. Downloads are kept regardless.
            for vid, r in list(rows.items()):
                if not r["downloaded"] and vid not in remote_ids:
                    fetcher.remove_cached_meta(streamer_dir, vid)
                    del rows[vid]

    def sort_key(r: dict) -> tuple[str, int]:
        return (r["created_at"] or "", int(r["id"]) if r["id"].isdigit() else 0)

    return sorted(rows.values(), key=sort_key, reverse=True), login, note
