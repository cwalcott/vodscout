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
        "query": f'query{{video(id:"{vod_id}"){{title,lengthSeconds,owner{{login}}}}}}',
        "variables": {},
    }
    with requests.Session() as session:
        session.headers["Client-ID"] = _META_CLIENT_ID
        data = _gql_post(session, payload)
    video = data["video"]
    if video is None:
        raise ValueError(f"VOD {vod_id!r} not found or is not accessible.")
    return video


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


def _iter_messages(session: requests.Session, vod_id: str):
    """Yield msg dicts for every chat message in the VOD."""
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
            emotes = [f["emote"]["emoteID"] for f in frags if f.get("emote")]
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
                    for msg in _iter_messages(session, vod_id):
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

    return out_path


def list_remote_vods(streamer: str, config: Config) -> list[dict]:
    """List recent VODs for a streamer via Twitch Helix API."""
    raise NotImplementedError


def fetch_by_streamer(streamer: str, config: Config, fetch_all: bool = False) -> None:
    """List/pick undownloaded VODs for a streamer and fetch selected ones."""
    raise NotImplementedError
