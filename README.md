# Tisser

*Tisser* is French for "to weave". Tisser keeps your French from slipping by weaving it into the AI replies you already read. It's built for people who already speak some French and want to keep it, not for learning from scratch.

> Le build passe maintenant. The cache was stale, donc je l'ai vidé, et bien qu'il soit tard, the deploy is back on track.

## How it works

- **A word bank.** About 330 starting items at B1 to C1 level: words, idioms, and grammar structures (subjonctif triggers, *dont*/*lequel*, pronoun order, *faire* causatif, …). Each item is `new`, `active`, `shaky` or `solid`.
- **A brief on every reply.** A short instruction of about 300 tokens says how much French to use (level 1–5) and offers 8 items from the rotation to work in. It never adds translations: when something isn't clear, you ask, and that question is the signal.
- **Practice the other way.** When you write in French, the reply corrects your mistakes and suggests a more natural, casual way to say it. About one reply in ten (set `ask_chance` in the bank, 0 to turn it off) ends by inviting you to answer in French.
- **A nightly pass.** It reads the day's conversations:
  - A word or idiom that appears in a reply counts as an exposure for that day.
  - One model call looks for what you asked about or misread ("c'est quoi *ruissellement* ?", "why *soit* and not *est*?"). Those items become **shaky** and come back more often.
  - After 3 exposure days with no questions, an item moves up a status. Solid items unseen for 30 days come back for review.
  - The model suggests a few new items pitched just above what you handled comfortably that day.
  - The overall level goes up after a quiet week and down after a week with a lot of questions.
- **Never** in code, commands, file contents, commit messages, or drafts meant for other people, and not at all for health, legal, money, or safety topics.

Your bank lives in `TISSER_HOME`, which defaults to `~/.tisser`. It holds `bank.json` and a readable `bank.md`. Nothing leaves your machine except the nightly model call.

## Install

```sh
pip install -e .            # add [reflect] for the nightly pass: pip install -e ".[reflect]"
python -m tisser init       # creates the bank; --level 1-5 (default 3)
```

### Claude Code

Add a `UserPromptSubmit` hook to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [{ "type": "command", "command": "python -m tisser.hook", "timeout": 10 }] }
    ]
  }
}
```

On Windows, use `py -3.13 -m tisser.hook` (or whichever Python you installed into).

The hook stays silent in these cases:
- SDK and `claude -p` runs (`CLAUDE_CODE_ENTRYPOINT=sdk-*`)
- When `TISSER_OFF=1` is set
- When the prompt looks like a health, legal, or money question

Set `TISSER_FORCE=1` to weave anyway.

### Your own bot or app

```python
import tisser
prompt_parts.append(tisser.brief("chat"))   # or "message" for JSON-producing prompts
```

`brief()` never raises. It returns `""` when there is no bank.

### Nightly pass

```sh
python -m tisser reflect                              # Claude Code transcripts for today
python -m tisser reflect --log-dir ~/logs --user Sam  # plus YYYY-MM-DD.md logs with **Name · HH:MM** headers
```

Schedule it once a day, just before midnight. It skips the model call on days with no French in them.

## Commands

| Command | |
|---|---|
| `python -m tisser status` | level and counts |
| `python -m tisser brief` | print the brief a reply gets right now |
| `python -m tisser level N` | set the level by hand |
| `python -m tisser reflect [--date D] [--dry-run]` | run the nightly pass |

## Develop

```sh
python -m unittest discover -s tests
```

## License

MIT
