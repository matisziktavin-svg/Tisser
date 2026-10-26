"""The short instruction added to a reply's context: how much French, where it
must never go, and which bank items to work in this turn."""
from __future__ import annotations

import datetime as dt
import hashlib
import random
from pathlib import Path
from typing import Optional

from . import bank as B

PICK = 8          # items offered per turn
MAX_SHAKY = 4     # of which at most this many shaky ones
ASK_CHANCE = 0.10  # per reply: invite the user to answer in French (bank "ask_chance" overrides)

LEVELS = {
    1: "about 10% French: single words and short phrases inside English sentences",
    2: "about 20% French: words and phrases, plus now and then a short sentence fully in French",
    3: "about 35% French: roughly one sentence in three fully in French, the rest English "
       "with French words and phrases mixed in",
    4: "about 50% French: move freely between French and English sentences",
    5: "mostly French (about 75%); English only where precision really matters",
}

# Where the French may go, per surface. Everything else stays as it would be.
SURFACES = {
    "chat": "your conversational prose to the user",
    "discord": "your conversational prose to the user",
    "message": "the text of the message the user will read; JSON keys, structured fields "
               "and any files you write stay exactly as they would be",
}

RULES = (
    "Never in: code, commands, file paths, identifiers, error messages, file contents, "
    "commit messages, PR text, or anything the user will send to or share with someone "
    "else (emails, messages, documents, drafts). Keep technical terms in their usual form.\n"
    "Clarity first: warnings, errors, destructive actions, and step-by-step instructions "
    "the user must follow go in English.\n"
    "The mix holds in every message, short ones included: English stays the base language, so "
    "a one- or two-sentence status update is English with a few French words or one French "
    "clause, never wholly French. The 2-4 items are spread across the whole reply, not packed "
    "into each short update.\n"
    "No French at all when the topic is health, medication, legal, money or finance, "
    "safety, or an emergency.\n"
    "Write natural, idiomatic French (how a French speaker would say it, not word-for-word). "
    "No glosses or translations: the user asks when something is unclear. When they ask what "
    "something means or why it is written that way, explain briefly (meaning, and the grammar "
    "if relevant), then carry on at the same level."
)

FEEDBACK = (
    "When the user writes to you in French (a phrase or a whole message), answer what they said "
    "first, then add a short note: each mistake with the corrected form, and, where it helps, a "
    "more casual, natural way people would say it in everyday French today. Skip the note when "
    "there is nothing worth fixing. No praise filler."
)

ASK = (
    "This reply only: end it with one short line in French inviting the user to answer you in "
    "French (vary the wording). Skip it if the reply is a warning or error, or is about health, "
    "money, legal matters, safety or an emergency."
)


def _rotation_key(item_id: str, salt: str) -> str:
    return hashlib.sha1(f"{salt}:{item_id}".encode()).hexdigest()


def pick(bank: dict, now: Optional[dt.datetime] = None, n: int = PICK) -> list[dict]:
    """Shaky items first, then active ones; the order rotates every hour so
    long sessions don't hammer the same handful."""
    now = now or dt.datetime.now()
    salt = now.strftime("%Y-%m-%d-%H")
    rot = B.in_rotation(bank)
    shaky = sorted((i for i in rot if i["status"] == "shaky"),
                   key=lambda i: _rotation_key(i["id"], salt))[:MAX_SHAKY]
    active = sorted((i for i in rot if i["status"] == "active"),
                    key=lambda i: (len(i["exposures"]), _rotation_key(i["id"], salt)))
    return (shaky + active)[:n]


def _line(item: dict) -> str:
    return f"- {item['fr']} ({item['en']})" if item["kind"] == "grammar" else f"- {item['fr']}"


def build(bank: dict, surface: str = "chat", now: Optional[dt.datetime] = None,
          roll: Optional[float] = None) -> str:
    """roll: a number in [0, 1) for the reply-in-French invite (random when None)."""
    level = bank.get("level", B.DEFAULT_LEVEL)
    items = pick(bank, now)
    shaky = [i for i in items if i["status"] == "shaky"]
    active = [i for i in items if i["status"] == "active"]
    parts = [
        "[Tisser: French upkeep] The user is a conversational French speaker keeping their "
        f"French from slipping. Weave French into {SURFACES.get(surface, SURFACES['chat'])} at "
        f"level {level}/5: {LEVELS.get(level, LEVELS[B.DEFAULT_LEVEL])}.",
        RULES,
        FEEDBACK,
    ]
    chance = bank.get("ask_chance", ASK_CHANCE)
    if (random.random() if roll is None else roll) < chance:
        parts.append(ASK)
    if items:
        parts.append(
            "Work in 2-4 of these where they fit naturally (never force one; grammar items "
            "mean use that structure):")
        if shaky:
            parts.append("Recently asked about, use in clear context:\n"
                         + "\n".join(_line(i) for i in shaky))
        if active:
            parts.append("\n".join(_line(i) for i in active))
    return "\n".join(parts)


def brief(surface: str = "chat", base: Optional[Path] = None) -> str:
    """The block for a caller to add to its prompt; "" when no bank exists.
    Never raises."""
    try:
        bank = B.load(base)
        return build(bank, surface) if bank else ""
    except Exception:
        return ""
