import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

BASELINE_BUCKETS = 30
MIN_BASELINE = 2.0
MIN_BASELINE_SAMPLES = 3
TOP_EMOTES = 5  # emotes shown as context per moment in the overall report


@dataclass
class Moment:
    timestamp_seconds: int
    magnitude: float
    watched: bool = False
    # Overall view: top emotes used in the spike window, as (name, count).
    top_emotes: list[tuple[str, int]] = field(default_factory=list)
    # Per-emote view: raw uses of the chosen emote in the peak bucket.
    count: int | None = None


@dataclass
class _Run:
    """A contiguous run of flagged buckets, with its peak."""

    start: int  # inclusive
    end: int  # exclusive
    peak: int
    magnitude: float


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


def count_emotes(messages: list[dict]) -> Counter:
    """Total usage count per emote name across the given messages."""
    counts: Counter = Counter()
    for m in messages:
        for emote in m.get("emotes") or ():
            counts[emote] += 1
    return counts


def _find_runs(counts: list[float]) -> list[_Run]:
    """Flag buckets above their trailing baseline; merge adjacent flags.

    Shared by the overall chat-volume detector and the per-emote detector.
    """
    flagged = [False] * len(counts)
    baselines = [0.0] * len(counts)

    for i in range(len(counts)):
        prior = counts[max(0, i - BASELINE_BUCKETS) : i]
        if len(prior) < MIN_BASELINE_SAMPLES:
            continue
        baseline = sum(prior) / len(prior)
        baselines[i] = baseline
        if baseline < MIN_BASELINE:
            continue
        if counts[i] > baseline:
            flagged[i] = True

    runs: list[_Run] = []
    i = 0
    while i < len(flagged):
        if not flagged[i]:
            i += 1
            continue
        j = i
        while j < len(flagged) and flagged[j]:
            j += 1
        peak = max(range(i, j), key=lambda k: counts[k])
        runs.append(_Run(i, j, peak, counts[peak] / baselines[peak]))
        i = j
    return runs


def detect_spikes(
    messages: list[dict], bucket_seconds: int, top_emotes: int = TOP_EMOTES
) -> list[Moment]:
    """Overall view: moments where chat volume spiked above its recent normal,
    each annotated with the emotes most used in that window."""
    if not messages:
        return []

    bucket_count: dict[int, int] = {}
    bucket_emotes: dict[int, list[str]] = {}
    for m in messages:
        b = m["time"] // bucket_seconds
        bucket_count[b] = bucket_count.get(b, 0) + 1
        emotes = m.get("emotes")
        if emotes:
            bucket_emotes.setdefault(b, []).extend(emotes)

    max_bucket = max(bucket_count)
    counts = [bucket_count.get(i, 0) for i in range(max_bucket + 1)]

    moments: list[Moment] = []
    for run in _find_runs(counts):
        window: list[str] = []
        for k in range(run.start, run.end):
            window.extend(bucket_emotes.get(k, []))
        moments.append(
            Moment(
                timestamp_seconds=run.peak * bucket_seconds,
                magnitude=round(run.magnitude, 2),
                top_emotes=Counter(window).most_common(top_emotes),
            )
        )

    moments.sort(key=lambda m: m.magnitude, reverse=True)
    return moments


def detect_emote_spikes(
    messages: list[dict], bucket_seconds: int, emote: str
) -> list[Moment]:
    """Per-emote view: moments where one chosen emote spiked above its own
    normal rate. No usage threshold — the caller picked the emote on purpose."""
    if not messages:
        return []

    per_bucket: dict[int, int] = {}
    for m in messages:
        emotes = m.get("emotes")
        if emotes and emote in emotes:
            b = m["time"] // bucket_seconds
            per_bucket[b] = per_bucket.get(b, 0) + emotes.count(emote)

    if not per_bucket:
        return []

    max_bucket = max(m["time"] // bucket_seconds for m in messages)
    counts = [per_bucket.get(i, 0) for i in range(max_bucket + 1)]

    moments: list[Moment] = []
    for run in _find_runs(counts):
        moments.append(
            Moment(
                timestamp_seconds=run.peak * bucket_seconds,
                magnitude=round(run.magnitude, 2),
                count=int(counts[run.peak]),
            )
        )

    moments.sort(key=lambda m: m.magnitude, reverse=True)
    return moments


def mark_watched(moments: list[Moment], watched_ranges: list[tuple[int, int]]) -> None:
    """Set Moment.watched for moments whose timestamp falls in any range.

    Takes plain (start, end) tuples rather than the watched module's types —
    the analyzer reads watched data through the on-disk file (loaded by the
    caller), keeping the three legs decoupled.
    """
    for m in moments:
        m.watched = any(
            start <= m.timestamp_seconds < end for start, end in watched_ranges
        )


def report(
    moments: list[Moment], vod_id: str, top_n: int = 10, emote: str | None = None
) -> None:
    console = Console()
    title = f"Top {emote} moments" if emote else "Top moments"

    if not moments:
        what = f"{emote} spikes" if emote else "spikes"
        console.print(f"[yellow]No {what} found.[/yellow]")
        return

    ranked = sorted(moments, key=lambda m: m.magnitude, reverse=True)[:top_n]

    console.print(f"\n{title} — VOD {vod_id}\n")

    for rank, m in enumerate(ranked, 1):
        ts = _format_timestamp(m.timestamp_seconds)
        link = _vod_link(vod_id, m.timestamp_seconds)
        mark = "  [dim]\\[watched][/dim]" if m.watched else ""
        style = "dim" if m.watched else None

        if emote:
            detail = f"[magenta]{m.count} uses[/magenta]"
        else:
            detail = "  ".join(f"{e} [dim]({n})[/dim]" for e, n in m.top_emotes)

        head = (
            f"[dim]{rank:2}[/dim]  {ts}  [bold]{m.magnitude:.1f}×[/bold]  "
            f"{detail}{mark}"
        )
        console.print(head, style=style)
        console.print(f"    [cyan]{link}[/cyan]", style=style)
        console.print()


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
