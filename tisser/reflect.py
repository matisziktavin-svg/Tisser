"""Nightly pass: read the day's conversations, update the bank.

1. Exposure (pure Python): a word or idiom counts as seen on a day when it
   appears in an assistant reply.
2. One model call reads the conversations that had French in them and reports
   what the user asked about or misunderstood, which bank grammar structures
   were used, and a few new items pitched just above today's comfort.
3. apply() (pure Python, unit-tested) turns that into status changes, then
   items move up the lifecycle, the rotation is refilled, and the overall
   level is nudged once a week at most.

CLI:  py -3.13 -m tisser reflect [--date YYYY-MM-DD] [--log-dir DIR --user NAME] [--dry-run]
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional

from . import bank as B
from .sources import Turn, claude_code, daily_log

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 60_000
ASSISTANT_CLIP, USER_CLIP = 1500, 1200
MAX_SUGGEST = 6
_FRENCH = re.compile(r"\b(le|la|les|des|du|est|pas|je|tu|vous|nous|que|qui|avec|pour|"
                     r"mais|donc|alors|c'est|j'ai|une|très|aussi|déjà|ça)\b", re.I)

SYSTEM = """You review a day of conversations between a user and an AI assistant. The assistant \
weaves French into its English replies to help the user, a conversational French speaker, keep \
their French from slipping. The user signals trouble by asking ("what does X mean?", "c'est quoi \
X ?", "why is it written like that?", "I had to look that up"), by misreading something, or by \
answering as if they misunderstood.

Return ONLY a JSON object, no prose:
{
  "asked": [{"kind": "word|idiom|grammar", "fr": "...", "en": "...", "evidence": "short quote"}],
  "grammar_used": ["<id>", ...],
  "suggest": [{"kind": "word|idiom|grammar", "fr": "...", "en": "...", "level": "B1|B2|C1", \
"forms": ["..."]}]
}

asked: every French word, expression or grammar point the user asked about or clearly struggled \
with. fr = dictionary form for words (infinitive, masculine singular), the expression for idioms, \
a short French label with a mini example for grammar. If the item is in the KNOWN list, copy its fr text exactly. en = short meaning or rule. Only real \
signals of not knowing: a question that is about the topic itself, not the French, does not count.
grammar_used: ids from the GRAMMAR list below whose structure the assistant actually used in \
French in these conversations.
suggest: up to 6 new items worth adding, a little above what the user handled comfortably today, \
favouring vocabulary for things they actually talk about and structures they avoid. Real French only: no English loanwords (hotfix, bug, deadline). forms = \
inflected forms likely in running text (empty for grammar). Skip anything already in the KNOWN list.
Empty lists are fine."""


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + " …"


def has_french(convs: list[list[Turn]]) -> list[list[Turn]]:
    return [c for c in convs
            if any(t.role == "assistant" and len(_FRENCH.findall(t.text)) >= 4 for t in c)]


def build_input(bank: dict, convs: list[list[Turn]], day: dt.date) -> str:
    blocks = []
    for n, conv in enumerate(convs, 1):
        lines = [f"=== conversation {n} ==="]
        for t in conv:
            clip = ASSISTANT_CLIP if t.role == "assistant" else USER_CLIP
            lines.append(f"[{t.role}] {_clip(t.text, clip)}")
        blocks.append("\n".join(lines))
    # Keep the most recent conversations when over budget.
    text, used = [], 0
    for b in reversed(blocks):
        if used + len(b) > MAX_INPUT_CHARS:
            break
        text.append(b)
        used += len(b)
    grammar = [i for i in B.in_rotation(bank) if i["kind"] == "grammar"]
    known = [i["fr"] for i in bank["items"]]
    return (
        f"Date: {day.isoformat()}. French level in use: {bank['level']}/5.\n\n"
        "GRAMMAR (id: structure):\n"
        + ("\n".join(f"{i['id']}: {i['fr']}" for i in grammar) or "(none)")
        + "\n\nKNOWN (already in the bank, don't suggest):\n" + "; ".join(known)
        + "\n\nCONVERSATIONS:\n\n" + "\n\n".join(reversed(text))
    )


def parse(text: str) -> dict:
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON object in model output")
    data = json.loads(text[a:b + 1])
    return {
        "asked": [x for x in data.get("asked") or [] if isinstance(x, dict)],
        "grammar_used": [x for x in data.get("grammar_used") or [] if isinstance(x, str)],
        "suggest": [x for x in data.get("suggest") or [] if isinstance(x, dict)],
    }


def apply(bank: dict, result: dict, day: dt.date, assistant_texts: Iterable[str]) -> dict:
    """Mutates `bank`; returns a summary for the history log."""
    texts = list(assistant_texts)
    iso = day.isoformat()
    seen = 0
    for item in bank["items"]:
        if item["status"] != "new" and seen_in(item, texts):
            B.record_exposure(item, day)
            seen += 1
    ids = {i["id"]: i for i in bank["items"]}
    for gid in result.get("grammar_used", []):
        if gid in ids and ids[gid]["status"] != "new":
            B.record_exposure(ids[gid], day)
            seen += 1

    asks = 0
    added = 0
    for a in result.get("asked", []):
        item = B.find(bank, a.get("fr", ""))
        if item is None:
            item = B.add_item(bank, a, status="shaky", source="asked", today=day)
            added += 1 if item else 0
        if item:
            B.record_ask(item, day)
            asks += 1

    for s in result.get("suggest", [])[:MAX_SUGGEST]:
        if B.add_item(bank, s, status="new", source="suggested", today=day):
            added += 1

    moves = B.promote(bank, day)
    activated = B.refill(bank, day)
    bank["history"] = [h for h in bank["history"] if h["date"] != iso]
    entry = {"date": iso, "exposures": seen, "asks": asks, "added": added,
             "promoted": sum(1 for m in moves if m[2] in ("solid", "active") and m[1] != "solid"),
             "activated": len(activated)}
    bank["history"].append(entry)
    new_level = B.adjust_level(bank, day)
    if new_level is not None:
        entry["level_change"], entry["level"] = True, new_level
    bank["history"] = bank["history"][-B.HISTORY_KEEP:]
    return entry


def seen_in(item: dict, texts: list[str]) -> bool:
    return B.seen_in(item, texts)


async def _call_model(user_msg: str, model: Optional[str], sdk_options: dict) -> str:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

    opts = ClaudeAgentOptions(system_prompt=SYSTEM, allowed_tools=[], max_turns=1,
                              **({"model": model} if model else {}), **sdk_options)
    out = []
    async for msg in query(prompt=user_msg, options=opts):
        if isinstance(msg, AssistantMessage):
            out.extend(b.text for b in msg.content if isinstance(b, TextBlock))
    return "".join(out)


async def run(day: Optional[dt.date] = None, *, base: Optional[Path] = None,
              log_dirs: Iterable[tuple[Path, str]] = (), model: Optional[str] = "sonnet",
              sdk_options: Optional[dict] = None, dry_run: bool = False,
              call: Optional[Callable[[str], Awaitable[str]]] = None,
              cc_root: Optional[Path] = None) -> Optional[dict]:
    """Run the nightly pass for `day` (default: today). Returns the history
    entry, or None when no bank exists."""
    day = day or dt.date.today()
    bank = B.load(base)
    if bank is None:
        logger.info("tisser: no bank at %s; run `tisser init` first", B.bank_path(base))
        return None
    convs = list(claude_code(day, cc_root))
    for folder, user in log_dirs:
        convs.extend(daily_log(Path(folder), day, user))
    assistant_texts = [t.text for c in convs for t in c if t.role == "assistant"]

    result = {"asked": [], "grammar_used": [], "suggest": []}
    french = has_french(convs)
    if french:
        msg = build_input(bank, french, day)
        call = call or (lambda m: _call_model(m, model, sdk_options or {}))
        try:
            result = parse(await call(msg))
        except Exception:
            logger.exception("tisser: model pass failed; applying exposure only")

    entry = apply(bank, result, day, assistant_texts)
    entry["asked_items"] = [a.get("fr") for a in result["asked"]]
    if not dry_run:
        B.save(bank, base)
        B.write_md(bank, base)
    return entry


def run_sync(*args, **kwargs):
    return asyncio.run(run(*args, **kwargs))
