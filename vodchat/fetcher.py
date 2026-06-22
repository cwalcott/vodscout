import json
import os
import re
import time
from pathlib import Path

import requests
from rich.progress import BarColumn, Progress, TaskProgressColumn, TimeRemainingColumn

from .config import Config

_GQL_URL = "https://gql.twitch.tv/gql"
_CHAT_CLIENT_ID = "kd1unb4b3q4t58fwlpcbzcbnm76a8fp"
_META_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_CHAT_HASH = "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"

# Streamer-name VOD discovery — same public GQL endpoint used for chat,
# no Twitch API credentials required.
_VOD_LIST_LIMIT = 10  # recent archives to list per streamer

# Third-party emote providers. BTTV/FFZ/7TV emotes aren't in Twitch's emote
# system, so GQL delivers them as plain text — we recognize them by name
# against the channel's (global + channel-specific) sets, fetched once per VOD.
_BTTV_GLOBAL = "https://api.betterttv.net/3/cached/emotes/global"
_BTTV_USER = "https://api.betterttv.net/3/cached/users/twitch/{id}"
_FFZ_GLOBAL = "https://api.frankerfacez.com/v1/set/global"
_FFZ_ROOM = "https://api.frankerfacez.com/v1/room/id/{id}"
_SEVENTV_GLOBAL = "https://7tv.io/v3/emote-sets/global"
_SEVENTV_USER = "https://7tv.io/v3/users/twitch/{id}"


def _vod_id_from_url(url_or_id: str) -> str:
    match = re.search(r"/videos/(\d+)", url_or_id)
    if match:
        return match.group(1)
    if re.fullmatch(r"\d+", url_or_id.strip()):
        return url_or_id.strip()
    raise ValueError(f"Cannot parse VOD ID from: {url_or_id!r}")


def _gql_post(session: requests.Session, payload: dict) -> dict:
    """POST to GQL and return body['data'], raising ValueError on GQL-level errors."""
    resp = session.post(_GQL_URL, json=payload, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    data = body.get("data")
    if data is None:
        errors = body.get("errors") or []
        msg = errors[0].get("message", "unknown error") if errors else "empty response"
        raise ValueError(f"Twitch GQL error: {msg}")
    return data


def _video_metadata(vod_id: str) -> dict:
    payload = {
        "query": (
            f'query{{video(id:"{vod_id}")'
            f"{{title,lengthSeconds,publishedAt,owner{{id,login}}}}}}"
        ),
        "variables": {},
    }
    with requests.Session() as session:
        session.headers["Client-ID"] = _META_CLIENT_ID
        data = _gql_post(session, payload)
    video = data["video"]
    if video is None:
        raise ValueError(f"VOD {vod_id!r} not found or is not accessible.")
    return video


def _get_json(url: str):
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _bttv_codes(data) -> set[str]:
    # Global is a bare list; the user endpoint nests channel/shared emotes.
    if isinstance(data, list):
        emotes = data
    else:
        emotes = (data.get("channelEmotes") or []) + (data.get("sharedEmotes") or [])
    return {e["code"] for e in emotes if e.get("code")}


def _ffz_names(data) -> set[str]:
    names: set[str] = set()
    for emote_set in (data.get("sets") or {}).values():
        for e in emote_set.get("emoticons") or []:
            if e.get("name"):
                names.add(e["name"])
    return names


def _seventv_names(data) -> set[str]:
    # Global is an emote-set object; the user endpoint wraps it in emote_set.
    emote_set = data.get("emote_set") if "emote_set" in data else data
    emotes = (emote_set or {}).get("emotes") or []
    return {e["name"] for e in emotes if e.get("name")}


def _third_party_emotes(user_id: str) -> set[str]:
    """Names of all BTTV/FFZ/7TV emotes available in the channel (+ globals).

    Each source is fetched independently and best-effort: a provider being
    down — or, very commonly, not having this channel registered (the
    channel endpoint 404s) — must not drop the other sources, including that
    provider's globals, nor break the chat download.
    """
    sources = [
        (_BTTV_GLOBAL, _bttv_codes),
        (_BTTV_USER.format(id=user_id), _bttv_codes),
        (_FFZ_GLOBAL, _ffz_names),
        (_FFZ_ROOM.format(id=user_id), _ffz_names),
        (_SEVENTV_GLOBAL, _seventv_names),
        (_SEVENTV_USER.format(id=user_id), _seventv_names),
    ]
    names: set[str] = set()
    for url, extract in sources:
        try:
            names |= extract(_get_json(url))
        except Exception:
            pass
    return names


def _scan_third_party(text: str, known: set[str]) -> list[str]:
    """Whitespace-split tokens of `text` that are known third-party emotes."""
    return [tok for tok in text.split() if tok in known]


def _chat_payload(vod_id: str, *, offset: int = 0, cursor: str | None = None) -> dict:
    variables: dict = {"videoID": vod_id}
    if cursor is not None:
        variables["cursor"] = cursor
    else:
        variables["contentOffsetSeconds"] = offset
    return {
        "operationName": "VideoCommentsByOffsetOrCursor",
        "variables": variables,
        "extensions": {"persistedQuery": {"version": 1, "sha256Hash": _CHAT_HASH}},
    }


def _iter_messages(
    session: requests.Session, vod_id: str, third_party: set[str] | None = None
):
    """Yield msg dicts for every chat message in the VOD."""
    third_party = third_party or set()
    cursor: str | None = None
    null_streak = 0

    while True:
        data = _gql_post(session, _chat_payload(vod_id, cursor=cursor))
        comments = data["video"]["comments"]
        edges = comments.get("edges") or []

        if not edges:
            null_streak += 1
            if null_streak >= 3:
                break
            time.sleep(0.5 * null_streak)
            continue

        null_streak = 0

        for edge in edges:
            node = edge["node"]
            if node.get("commenter") is None:
                continue  # deleted account
            frags = node["message"]["fragments"]
            text = "".join(f["text"] for f in frags if f.get("text"))
            # Store emote names (the fragment text), not IDs: per-emote spike
            # detection, the `emotes` exploration command, and future favorites
            # are all name-facing. Names join directly with what the user reads
            # in the report; IDs would need a separate persisted lookup table.
            emotes = [f["text"] for f in frags if f.get("emote")]
            # Third-party emotes arrive as plain text — match them by name.
            emotes += _scan_third_party(text, third_party)
            msg: dict = {
                "time": node["contentOffsetSeconds"],
                "user": node["commenter"]["login"],
                "msg": text,
            }
            if emotes:
                msg["emotes"] = emotes
            yield msg

        if not comments.get("pageInfo", {}).get("hasNextPage"):
            break
        cursor = edges[-1]["cursor"]


def fetch_by_url(url: str, config: Config) -> Path:
    """Download chat for a VOD URL/ID. Returns path to the saved chat log."""
    vod_id = _vod_id_from_url(url)
    meta = _video_metadata(vod_id)
    streamer = meta["owner"]["login"]
    duration = meta.get("lengthSeconds") or None

    owner_id = meta["owner"].get("id")
    third_party = _third_party_emotes(owner_id) if owner_id else set()

    out_dir = config.chat_dir / streamer
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{vod_id}.txt"

    if out_path.exists():
        raise FileExistsError(f"Chat log already exists: {out_path}")

    tmp_path = out_path.with_suffix(".tmp")
    count = 0
    try:
        with Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task(f"[cyan]Fetching {vod_id}", total=duration)
            with requests.Session() as session:
                session.headers["Client-ID"] = _CHAT_CLIENT_ID
                with tmp_path.open("w") as f:
                    for msg in _iter_messages(session, vod_id, third_party):
                        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                        count += 1
                        if duration:
                            progress.update(task, completed=msg["time"])

        if count == 0:
            raise ValueError(f"No chat messages found for VOD {vod_id!r}.")

        os.replace(tmp_path, out_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    write_meta(out_dir, vod_id, meta)
    return out_path


_VIDEOS_QUERY = """
query($login: String!, $first: Int!) {
  user(login: $login) {
    login
    videos(first: $first, type: ARCHIVE, sort: TIME) {
      edges { node { id title lengthSeconds publishedAt } }
    }
  }
}
"""


def _format_duration(seconds: int | None) -> str:
    h, rem = divmod(int(seconds or 0), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def list_remote_vods(streamer: str) -> list[dict]:
    """List recent archived VODs for a streamer via Twitch's GQL endpoint.

    Each dict carries id, title, user_login, created_at, duration_seconds. No
    Twitch API credentials needed — this is the same public GQL endpoint (and
    public web Client-ID) the chat download already uses.
    """
    payload = {
        "query": _VIDEOS_QUERY,
        "variables": {"login": streamer, "first": _VOD_LIST_LIMIT},
    }
    with requests.Session() as session:
        session.headers["Client-ID"] = _META_CLIENT_ID
        data = _gql_post(session, payload)

    user = data.get("user")
    if user is None:
        raise ValueError(f"Streamer {streamer!r} not found.")

    videos = []
    for edge in user["videos"]["edges"]:
        node = edge["node"]
        videos.append(
            {
                "id": node["id"],
                "title": node["title"],
                "user_login": user["login"],
                "created_at": node["publishedAt"],
                "duration_seconds": node["lengthSeconds"] or 0,
            }
        )
    return videos


def _meta_path(vod_id: str, streamer_dir: Path) -> Path:
    return streamer_dir / f"{vod_id}.meta.json"


_META_SUFFIX = ".meta.json"


def _write_meta_file(streamer_dir: Path, data: dict) -> None:
    """Write a `{id,title,created_at,duration_seconds}` sidecar. Best-effort:
    a write failure must never fail the fetch/refresh that triggered it."""
    try:
        _meta_path(data["id"], streamer_dir).write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
    except OSError:
        pass


def write_meta(streamer_dir: Path, vod_id: str, meta: dict) -> None:
    """Persist a downloaded VOD's metadata sidecar next to its chat log.

    `meta` is the GQL video object (title, lengthSeconds, publishedAt). Stored
    so `list` can show a rich, offline view of downloaded VODs without a
    network call. Best-effort: a sidecar write failure must not fail a fetch.
    """
    _write_meta_file(
        streamer_dir,
        {
            "id": vod_id,
            "title": meta.get("title", ""),
            "created_at": meta.get("publishedAt", ""),
            "duration_seconds": meta.get("lengthSeconds") or 0,
        },
    )


def write_remote_meta(streamer_dir: Path, video: dict) -> None:
    """Cache a recent-VOD sidecar from a `list_remote_vods` row (already
    normalized to id/title/created_at/duration_seconds).

    Written on every Twitch refresh for *all* recent VODs, including ones not
    yet downloaded — the sidecar is then the only on-disk trace of an
    undownloaded VOD, which is what lets the list show recent VODs at startup
    with no network call. Best-effort, same as `write_meta`.
    """
    _write_meta_file(
        streamer_dir,
        {
            "id": video["id"],
            "title": video.get("title", ""),
            "created_at": video.get("created_at", ""),
            "duration_seconds": video.get("duration_seconds", 0),
        },
    )


def remove_cached_meta(streamer_dir: Path, vod_id: str) -> None:
    """Delete an undownloaded VOD's cached sidecar (best-effort).

    Used to prune cache entries that have aged off Twitch's recent list. Only
    ever called for VODs with no chat log — a downloaded VOD's sidecar is real
    metadata, not cache, and is never removed this way.
    """
    try:
        _meta_path(vod_id, streamer_dir).unlink(missing_ok=True)
    except OSError:
        pass


def local_vods(streamer: str, config: Config) -> list[dict]:
    """Metadata for every downloaded VOD of a streamer, read from sidecars.

    One dict per downloaded chat log (id, title, created_at, duration_seconds);
    fields fall back to empty/0 when a sidecar is missing. No network. This is
    the source of truth for `list` — downloads are never dropped just because a
    VOD has aged off (or been removed from) Twitch's recent list.
    """
    streamer_dir = config.chat_dir / streamer
    if not streamer_dir.is_dir():
        return []

    vods = []
    for log in streamer_dir.glob("*.txt"):
        vod_id = log.stem
        meta_path = _meta_path(vod_id, streamer_dir)
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except (OSError, json.JSONDecodeError):
                meta = {}
        vods.append(
            {
                "id": vod_id,
                "title": meta.get("title", ""),
                "created_at": meta.get("created_at", ""),
                "duration_seconds": meta.get("duration_seconds", 0),
            }
        )
    return vods


def cached_vods(streamer: str, config: Config) -> list[dict]:
    """Metadata for recent VODs cached but NOT downloaded (sidecar, no chat log).

    These are the recent-VOD entries persisted by `write_remote_meta` on the
    last Twitch refresh, so the list can show them offline at startup. A VOD
    whose chat log is on disk is a *download* (see `local_vods`), not a cache
    entry, and is excluded here. Same dict shape as `local_vods`.
    """
    streamer_dir = config.chat_dir / streamer
    if not streamer_dir.is_dir():
        return []

    vods = []
    for meta_path in streamer_dir.glob(f"*{_META_SUFFIX}"):
        vod_id = meta_path.name[: -len(_META_SUFFIX)]
        if not vod_id or (streamer_dir / f"{vod_id}.txt").exists():
            continue  # downloaded — handled by local_vods
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        vods.append(
            {
                "id": vod_id,
                "title": meta.get("title", ""),
                "created_at": meta.get("created_at", ""),
                "duration_seconds": meta.get("duration_seconds", 0),
            }
        )
    return vods


def downloaded_ids(streamer: str, config: Config) -> set[str]:
    """VOD IDs already on disk for a streamer (empty if the dir doesn't exist)."""
    streamer_dir = config.chat_dir / streamer
    if not streamer_dir.is_dir():
        return set()
    return {p.stem for p in streamer_dir.glob("*.txt")}


def undownloaded_vods(videos: list[dict], streamer: str, config: Config) -> list[dict]:
    """Filter `videos` to those whose chat log isn't already on disk."""
    have = downloaded_ids(streamer, config)
    return [v for v in videos if v["id"] not in have]


def parse_selection(text: str, count: int) -> list[int]:
    """Parse a numbered-pick string into sorted 0-based indices.

    Accepts "all", or a comma/space-separated list of 1-based numbers
    (e.g. "1,3 5"). Blank selects nothing. Raises ValueError on a number
    outside 1..count or a non-numeric token.
    """
    text = text.strip()
    if not text:
        return []
    if text.lower() == "all":
        return list(range(count))

    indices: set[int] = set()
    for token in text.replace(",", " ").split():
        if not token.isdigit():
            raise ValueError(f"Not a number: {token!r}")
        n = int(token)
        if not 1 <= n <= count:
            raise ValueError(f"Out of range (1–{count}): {n}")
        indices.add(n - 1)
    return sorted(indices)
