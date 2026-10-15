"""Read a day's conversations: Claude Code transcripts, plus optional folders of
markdown daily logs (one YYYY-MM-DD.md per day with `**Name · HH:MM**` headers).

Each source yields conversations: lists of Turn(role, text) in order, where
role is "user" or "assistant".
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


@dataclass
class Turn:
    role: str
    text: str


_TAG_BLOCK = re.compile(r"<(system-reminder|command-[\w-]+|local-command-[\w-]+|"
                        r"ide_[\w-]+|task-notification)[^>]*>.*?</\1>", re.DOTALL)


def _clean(text: str) -> str:
    return _TAG_BLOCK.sub("", text or "").strip()


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _local_date(ts: str) -> Optional[dt.date]:
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()
    except (ValueError, AttributeError):
        return None


def claude_code(day: dt.date, root: Optional[Path] = None) -> Iterator[list[Turn]]:
    """Interactive Claude Code sessions for `day` (local time). SDK and
    `claude -p` runs (entrypoint sdk-*) and subagent sidechains are skipped."""
    root = root or Path.home() / ".claude" / "projects"
    if not root.exists():
        return
    start = dt.datetime.combine(day, dt.time()).timestamp()
    for f in root.glob("*/*.jsonl"):
        try:
            if f.stat().st_mtime < start:
                continue
            turns: list[Turn] = []
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"user"' not in line and '"assistant"' not in line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (e.get("entrypoint") or "").startswith("sdk") or e.get("isSidechain") \
                            or e.get("isMeta") or e.get("type") not in ("user", "assistant"):
                        continue
                    if _local_date(e.get("timestamp", "")) != day:
                        continue
                    text = _clean(_text_of((e.get("message") or {}).get("content")))
                    if text:
                        turns.append(Turn(e["type"], text))
            if turns:
                yield turns
        except OSError:
            continue


_HEADER = re.compile(r"^\*\*(?P<name>[^*]+?)(?: \([^)]*\))? · \d{1,2}:\d{2}\*\*\s*$", re.M)


def daily_log(folder: Path, day: dt.date, user_name: str) -> Iterator[list[Turn]]:
    """One conversation from `<folder>/<day>.md`; turns by `user_name` are the
    user's, every other speaker is the assistant."""
    f = Path(folder) / f"{day.isoformat()}.md"
    if not f.exists():
        return
    text = f.read_text(encoding="utf-8", errors="replace")
    heads = list(_HEADER.finditer(text))
    turns = []
    for h, nxt in zip(heads, heads[1:] + [None]):
        body = text[h.end(): nxt.start() if nxt else len(text)].strip()
        if body:
            role = "user" if h["name"].strip().lower() == user_name.lower() else "assistant"
            turns.append(Turn(role, body))
    if turns:
        yield turns
