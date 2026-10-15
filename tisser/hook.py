"""Claude Code UserPromptSubmit hook: prints the brief as additionalContext.

settings.json:  "command": "py -3.13 -m tisser.hook"   (or python3 elsewhere)

Silent (prints nothing) when TISSER_OFF=1, in SDK and `claude -p` runs
(CLAUDE_CODE_ENTRYPOINT starts with "sdk"), when no bank exists, or when the
prompt is about a topic that should get no French. Never fails the prompt.
"""
from __future__ import annotations

import json
import os
import re
import sys

from .prompt import brief

SENSITIVE = re.compile(
    r"\b(doctor|hospital|symptom\w*|medic\w*|prescri\w*|dosage|overdose|diagnos\w*|"
    r"therap\w*|suicid\w*|self[- ]harm|emergency|911|lawyer|attorney|lawsuit|legal|court|"
    r"tax(es)?|irs|mortgage|bankrupt\w*)\b",
    re.IGNORECASE,
)


def should_weave(prompt: str, env=os.environ) -> bool:
    if env.get("TISSER_OFF") == "1":
        return False
    if env.get("TISSER_FORCE") == "1":
        return True
    if (env.get("CLAUDE_CODE_ENTRYPOINT") or "").startswith("sdk"):
        return False
    return not SENSITIVE.search(prompt or "")


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig", "replace")
        prompt = (json.loads(raw) if raw.strip() else {}).get("prompt", "")
        if not should_weave(prompt):
            return 0
        text = brief("chat")
        if text:
            out = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                          "additionalContext": text}}
            sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
