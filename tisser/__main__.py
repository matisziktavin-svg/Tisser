"""CLI: python -m tisser <init|status|brief|reflect|known|ask>"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from . import bank as B
from .prompt import brief


def _load_or_die() -> dict:
    bank = B.load()
    if bank is None:
        sys.exit(f"No bank at {B.bank_path()}. Run: python -m tisser init")
    return bank


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tisser")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="create the bank from the seed list")
    p.add_argument("--level", type=int, default=B.DEFAULT_LEVEL, choices=range(1, 6))
    p.add_argument("--force", action="store_true", help="overwrite an existing bank")
    sub.add_parser("status", help="level and counts")
    p = sub.add_parser("brief", help="print the brief a reply would get")
    p.add_argument("--surface", default="chat")
    p = sub.add_parser("level", help="set the level (1-5)")
    p.add_argument("n", type=int, choices=range(1, 6))
    p = sub.add_parser("reflect", help="run the nightly pass")
    p.add_argument("--date", type=dt.date.fromisoformat)
    p.add_argument("--log-dir", action="append", default=[],
                   help="folder of YYYY-MM-DD.md logs (repeatable)")
    p.add_argument("--user", default="", help="the user's name in --log-dir headers")
    p.add_argument("--model", default="sonnet")
    p.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "init":
        if B.load() and not a.force:
            sys.exit(f"Bank already exists at {B.bank_path()} (use --force to start over).")
        seed = json.loads(B.seed_path().read_text(encoding="utf-8"))
        bank = B.new_bank(seed, level=a.level)
        B.save(bank)
        B.write_md(bank)
        print(f"Created {B.bank_path()} with {len(seed)} items at level {a.level}.")
    elif a.cmd == "status":
        bank = _load_or_die()
        print(f"{B.bank_path()}\nlevel {bank['level']}/5 · {B.counts(bank)}")
    elif a.cmd == "brief":
        print(brief(a.surface) or "(no bank)")
    elif a.cmd == "level":
        bank = _load_or_die()
        bank["level"], bank["level_changed"] = a.n, dt.date.today().isoformat()
        B.save(bank)
        B.write_md(bank)
        print(f"level {a.n}/5")
    elif a.cmd == "reflect":
        from .reflect import run_sync
        dirs = [(Path(d), a.user) for d in a.log_dir]
        entry = run_sync(a.date, log_dirs=dirs, model=a.model, dry_run=a.dry_run)
        print(json.dumps(entry, ensure_ascii=False, indent=1) if entry else "(no bank)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
