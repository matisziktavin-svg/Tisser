"""The word bank: words, idioms and grammar structures, each with a status.

Lifecycle of an item:

    new ──activate──> active ──3 exposure days──> solid
                        ^                            │
                        │                            │ unseen 30+ days → review
          3 exposure    │                            v
          days since ─ shaky <──── Tavin asked about it (from any status)

"Exposure day" = a day the item showed up in an assistant reply. Everything in
here is pure Python over a dict so it can be unit-tested without a model.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

ACTIVE_TARGET = 10       # items in rotation (active + shaky)
MAX_ACTIVATE_PER_DAY = 4
EXPOSURES_TO_SETTLE = 3  # exposure days after `since` to move up a status
REVIEW_AFTER_DAYS = 30   # a solid item unseen this long comes back for review
MAX_REVIEWS_PER_DAY = 2
MIN_LEVEL, MAX_LEVEL, DEFAULT_LEVEL = 1, 5, 3
LEVEL_ORDER = {"B1": 0, "B2": 1, "C1": 2, "C2": 3}
KINDS = ("word", "idiom", "grammar")
HISTORY_KEEP = 90


def home() -> Path:
    return Path(os.environ.get("TISSER_HOME") or Path.home() / ".tisser")


def bank_path(base: Optional[Path] = None) -> Path:
    return (base or home()) / "bank.json"


def seed_path() -> Path:
    return Path(__file__).resolve().parent / "seed.json"


def norm(text: str) -> str:
    """Lowercase, unify apostrophes/quotes and whitespace. Accents are kept:
    'pêcher' and 'pécher' are different words."""
    text = unicodedata.normalize("NFC", text or "").lower()
    text = text.replace("’", "'").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def load(base: Optional[Path] = None) -> Optional[dict]:
    p = bank_path(base)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save(bank: dict, base: Optional[Path] = None) -> None:
    p = bank_path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".bank-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(bank, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _next_id(bank: dict, kind: str) -> str:
    prefix = kind[0]
    n = max((int(i["id"][1:]) for i in bank["items"] if i["id"][0] == prefix), default=0)
    return f"{prefix}{n + 1:04d}"


def new_bank(seed: list[dict], level: int = DEFAULT_LEVEL, today: Optional[dt.date] = None) -> dict:
    today = today or dt.date.today()
    bank = {"version": 1, "level": level, "level_changed": today.isoformat(),
            "items": [], "history": []}
    for s in seed:
        add_item(bank, s, status="new", source="seed", today=today)
    refill(bank, today, cap=ACTIVE_TARGET)
    return bank


def find(bank: dict, text: str) -> Optional[dict]:
    """Item whose fr (or one of its forms) equals `text`, ignoring case."""
    key = norm(text)
    if not key:
        return None
    for item in bank["items"]:
        if norm(item["fr"]) == key or key in (norm(f) for f in item.get("forms", [])):
            return item
    return None


def add_item(bank: dict, raw: dict, *, status: str, source: str,
             today: dt.date) -> Optional[dict]:
    """Add an item unless one with the same fr already exists. Returns the new
    item, or None for a duplicate or malformed entry."""
    kind = raw.get("kind")
    fr = (raw.get("fr") or "").strip()
    if kind not in KINDS or not fr or find(bank, fr):
        return None
    item = {
        "id": _next_id(bank, kind),
        "kind": kind,
        "fr": fr,
        "en": (raw.get("en") or "").strip(),
        "level": raw.get("level") if raw.get("level") in LEVEL_ORDER else "B2",
        "forms": [f for f in (raw.get("forms") or []) if isinstance(f, str) and f.strip()],
        "status": status,
        "since": today.isoformat(),
        "source": source,
        "exposures": [],
        "asked": [],
    }
    bank["items"].append(item)
    return item


def in_rotation(bank: dict) -> list[dict]:
    return [i for i in bank["items"] if i["status"] in ("active", "shaky")]


def refill(bank: dict, today: dt.date, cap: int = MAX_ACTIVATE_PER_DAY) -> list[dict]:
    """Activate `new` items (easiest level first, then bank order) until the
    rotation holds ACTIVE_TARGET items or `cap` were activated."""
    room = min(cap, ACTIVE_TARGET - len(in_rotation(bank)))
    if room <= 0:
        return []
    pool = [i for i in bank["items"] if i["status"] == "new"]
    pool.sort(key=lambda i: LEVEL_ORDER.get(i["level"], 1))  # stable: keeps bank order
    picked = pool[:room]
    for i in picked:
        i["status"], i["since"] = "active", today.isoformat()
    return picked


def _match_pattern(item: dict) -> Optional[re.Pattern]:
    if item["kind"] == "grammar":
        return None
    forms = {norm(f) for f in item.get("forms", [])}
    if " " in item["fr"].strip():
        # A bare verb form of a multi-word expression ("rend" for "se rendre
        # compte") matches too much unrelated text; keep only longer forms.
        forms = {f for f in forms if " " in f or len(f) >= 6}
    variants = {norm(item["fr"])} | forms
    variants = sorted((v for v in variants if v), key=len, reverse=True)
    if not variants:
        return None
    alt = "|".join(re.escape(v) for v in variants)
    # \w is Unicode-aware, so accented letters count as word characters.
    return re.compile(rf"(?<![\w]){alt}(?![\w])")


def seen_in(item: dict, texts: Iterable[str]) -> bool:
    pat = _match_pattern(item)
    return bool(pat) and any(pat.search(norm(t)) for t in texts)


def record_exposure(item: dict, day: dt.date) -> None:
    d = day.isoformat()
    if d not in item["exposures"]:
        item["exposures"].append(d)
        item["exposures"].sort()


def record_ask(item: dict, day: dt.date) -> None:
    d = day.isoformat()
    if d not in item["asked"]:
        item["asked"].append(d)
    item["status"], item["since"] = "shaky", d


def _days_after(item: dict, since: str) -> int:
    return sum(1 for d in item["exposures"] if d > since)


def promote(bank: dict, today: dt.date) -> list[tuple[str, str, str]]:
    """Move items along the lifecycle. Returns (id, from, to) for each move."""
    moves = []
    iso = today.isoformat()
    reviews = 0
    cutoff = (today - dt.timedelta(days=REVIEW_AFTER_DAYS)).isoformat()
    for i in bank["items"]:
        st = i["status"]
        if st in ("active", "shaky") and _days_after(i, i["since"]) >= EXPOSURES_TO_SETTLE:
            i["status"] = "solid" if st == "active" else "active"
            i["since"] = iso
            moves.append((i["id"], st, i["status"]))
        elif st == "solid" and reviews < MAX_REVIEWS_PER_DAY and i["since"] <= cutoff:
            last = i["exposures"][-1] if i["exposures"] else ""
            if last <= cutoff:
                i["status"], i["since"] = "active", iso
                moves.append((i["id"], "solid", "active"))
                reviews += 1
    return moves


def adjust_level(bank: dict, today: dt.date) -> Optional[int]:
    """Nudge overall density from the last 7 nights of history: no questions on
    at least 4 active nights → one level up; 8+ questions → one level down.
    At most one change a week. Returns the new level, or None."""
    changed = dt.date.fromisoformat(bank.get("level_changed", "1970-01-01"))
    if (today - changed).days < 7:
        return None
    week = [h for h in bank["history"]
            if dt.date.fromisoformat(h["date"]) > today - dt.timedelta(days=7)]
    active_nights = sum(1 for h in week if h.get("exposures", 0) > 0)
    asks = sum(h.get("asks", 0) for h in week)
    level = bank["level"]
    if asks == 0 and active_nights >= 4 and level < MAX_LEVEL:
        level += 1
    elif asks >= 8 and level > MIN_LEVEL:
        level -= 1
    else:
        return None
    bank["level"], bank["level_changed"] = level, today.isoformat()
    return level


def counts(bank: dict) -> dict:
    out = {"new": 0, "active": 0, "shaky": 0, "solid": 0}
    for i in bank["items"]:
        out[i["status"]] = out.get(i["status"], 0) + 1
    return out


def render_md(bank: dict) -> str:
    """Readable snapshot (bank.md) for browsing in a notes app."""
    c = counts(bank)
    lines = [
        "# Tisser — French bank",
        "",
        f"Level **{bank['level']}** / {MAX_LEVEL} · {c['solid']} solid · {c['active']} active · "
        f"{c['shaky']} shaky · {c['new']} waiting",
        "",
        "_Generated nightly from bank.json; edits here are overwritten._",
        "",
    ]

    def section(title: str, items: list[dict]) -> None:
        if not items:
            return
        lines.extend([f"## {title}", ""])
        for i in items:
            lines.append(f"- **{i['fr']}** ({i['kind']}, {i['level']}) — {i['en']}")
        lines.append("")

    by = lambda s: [i for i in bank["items"] if i["status"] == s]  # noqa: E731
    section("Shaky — you asked about these", by("shaky"))
    section("In rotation", by("active"))
    recent = sorted(by("solid"), key=lambda i: i["since"], reverse=True)[:30]
    section("Recently solid", recent)
    if bank["history"]:
        lines.extend(["## Recent nights", ""])
        for h in bank["history"][-10:][::-1]:
            lines.append(f"- {h['date']}: {h.get('exposures', 0)} seen, {h.get('asks', 0)} asked, "
                         f"{h.get('promoted', 0)} moved up, {h.get('added', 0)} added"
                         + (f", level → {h['level']}" if h.get("level_change") else ""))
        lines.append("")
    return "\n".join(lines)


def write_md(bank: dict, base: Optional[Path] = None) -> None:
    (base or home()).mkdir(parents=True, exist_ok=True)
    ((base or home()) / "bank.md").write_text(render_md(bank), encoding="utf-8")
