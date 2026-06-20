import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

BASELINE_BUCKETS = 30
MIN_BASELINE = 2.0
MIN_BASELINE_SAMPLES = 3


@dataclass
class Moment:
    timestamp_seconds: int
    signals: list[str]
    magnitude: float
    samples: list[str]
    watched: bool


def find_log(vod_id: str, chat_dir: Path) -> tuple[str, Path]:
    matches = list(chat_dir.glob(f"*/{vod_id}.txt"))
    if not matches:
        raise FileNotFoundError(
            f"No chat log found for VOD {vod_id!r}. "
            f"Fetch it first with: vodchat fetch --url <url>"
        )
    if len(matches) > 1:
        streamers = ", ".join(p.parent.name for p in sorted(matches))
        raise ValueError(f"VOD {vod_id!r} found under multiple streamers: {streamers}")
    path = matches[0]
    return path.parent.name, path


def load_messages(log_path: Path) -> list[dict]:
    messages = []
    with log_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                messages.append(json.loads(line))
    return messages


def detect_spikes(
    messages: list[dict],
    bucket_seconds: int,
) -> list[Moment]:
    if not messages:
        return []

    bucket_msgs: dict[int, list[str]] = {}
    for m in messages:
        b = m["time"] // bucket_seconds
        bucket_msgs.setdefault(b, []).append(m["msg"])

    max_bucket = max(bucket_msgs)
    counts = [len(bucket_msgs.get(i, [])) for i in range(max_bucket + 1)]

    flagged = [False] * len(counts)
    baselines: list[float] = [0.0] * len(counts)

    for i in range(len(counts)):
        start = max(0, i - BASELINE_BUCKETS)
        prior = counts[start:i]
        if len(prior) < MIN_BASELINE_SAMPLES:
            continue
        baseline = sum(prior) / len(prior)
        baselines[i] = baseline
        if baseline < MIN_BASELINE:
            continue
        if counts[i] > baseline:
            flagged[i] = True

    moments: list[Moment] = []
    i = 0
    while i < len(flagged):
        if not flagged[i]:
            i += 1
            continue
        j = i
        while j < len(flagged) and flagged[j]:
            j += 1
        # Run spans buckets i..j-1; find the peak
        peak = max(range(i, j), key=lambda k: counts[k])
        ts = peak * bucket_seconds
        magnitude = counts[peak] / baselines[peak]
        run_tokens: list[str] = []
        for k in range(i, j):
            for msg in bucket_msgs.get(k, []):
                run_tokens.extend(msg.split())
        samples = [f"{tok} ({n})" for tok, n in Counter(run_tokens).most_common(5)]
        moments.append(
            Moment(
                timestamp_seconds=ts,
                signals=["chat-rate"],
                magnitude=round(magnitude, 2),
                samples=samples,
                watched=False,
            )
        )
        i = j

    moments.sort(key=lambda m: m.magnitude, reverse=True)
    return moments


def report(
    moments: list[Moment], vod_id: str, top_n: int = 10, show_tokens: bool = True
) -> None:
    console = Console()

    if not moments:
        console.print("[yellow]No chat spikes found.[/yellow]")
        return

    ranked = sorted(moments, key=lambda m: m.magnitude, reverse=True)[:top_n]

    console.print(f"\nChat spikes — VOD {vod_id}\n")

    for rank, m in enumerate(ranked, 1):
        ts = _format_timestamp(m.timestamp_seconds)
        link = _vod_link(vod_id, m.timestamp_seconds)

        if show_tokens:
            line1 = f"[dim]{rank:2}[/dim]  {ts}  [bold]{m.magnitude:.1f}x[/bold]"
            if m.samples:
                line1 += "  " + "  ".join(m.samples)
            console.print(line1)
            console.print(f"    [cyan]{link}[/cyan]")
            console.print()
        else:
            console.print(
                f"[dim]{rank:2}[/dim]  {ts}  [bold]{m.magnitude:.1f}x[/bold]"
                f"  [cyan]{link}[/cyan]"
            )


def _format_timestamp(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def _vod_link(vod_id: str, seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"https://www.twitch.tv/videos/{vod_id}?t={h}h{m:02d}m{s:02d}s"
