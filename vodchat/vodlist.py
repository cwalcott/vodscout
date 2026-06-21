"""Shared VOD-list orchestration used by both front ends (cli.py and ui.py).

Merges a streamer's local downloads with Twitch's recent VODs into one ordered
list. This is front-end orchestration, not a leg: it composes the fetcher (and
a watched-file existence check) but holds no state and is imported by both the
command-line and interactive front ends so they show the same list.
"""

from vodchat import config as cfg
from vodchat import fetcher


def merged_vods(
    streamer: str, config: "cfg.Config", offline: bool
) -> tuple[list[dict], str, str | None]:
    """Merge local downloads (source of truth) with Twitch's recent VODs.

    Returns (rows newest-first, resolved login, note). Local downloads are never
    dropped; the remote check only adds new VODs and tops up metadata. A remote
    failure is reported via `note`, not raised — local rows still come back.
    """
    streamer_dir = config.chat_dir / streamer
    rows: dict[str, dict] = {}
    for v in fetcher.local_vods(streamer, config):
        rows[v["id"]] = {
            **v,
            "downloaded": True,
            "watched": (streamer_dir / f"{v['id']}.watched.json").exists(),
        }

    login = streamer
    note: str | None = None
    if not offline:
        try:
            for v in fetcher.list_remote_vods(streamer):
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
        except ValueError as e:  # streamer not found remotely
            note = str(e)
        except Exception as e:  # offline / network failure — local still shows
            note = f"Couldn't reach Twitch ({e})."

    def sort_key(r: dict) -> tuple[str, int]:
        return (r["created_at"] or "", int(r["id"]) if r["id"].isdigit() else 0)

    return sorted(rows.values(), key=sort_key, reverse=True), login, note
