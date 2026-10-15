import asyncio
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path

from tisser import bank as B
from tisser import prompt as BR
from tisser import hook, reflect, sources

D0 = dt.date(2026, 10, 1)


def day(n: int) -> dt.date:
    return D0 + dt.timedelta(days=n)


SEED = [
    {"kind": "word", "fr": "tiroir", "en": "drawer", "level": "B1", "forms": ["tiroirs"]},
    {"kind": "idiom", "fr": "avoir du pain sur la planche", "en": "lots to do", "level": "B1",
     "forms": ["pain sur la planche"]},
    {"kind": "grammar", "fr": "subjonctif après bien que", "en": "bien que + subj.",
     "level": "B2", "forms": []},
    {"kind": "word", "fr": "épanoui", "en": "fulfilled", "level": "C1", "forms": ["épanouie"]},
    {"kind": "idiom", "fr": "se rendre compte", "en": "to realise", "level": "B2",
     "forms": ["rend compte", "rend"]},
]


def make(level=3, seed=SEED):
    return B.new_bank([dict(s) for s in seed], level=level, today=D0)


class BankTests(unittest.TestCase):
    def test_new_bank_activates_easiest_first(self):
        b = make()
        self.assertEqual(len(B.in_rotation(b)), len(SEED))  # fewer than ACTIVE_TARGET
        big = [{"kind": "word", "fr": f"mot{i}", "en": "x", "level": lv, "forms": []}
               for i, lv in enumerate(["C1"] * 5 + ["B1"] * 20)]
        b2 = make(seed=big)
        active = B.in_rotation(b2)
        self.assertEqual(len(active), B.ACTIVE_TARGET)
        self.assertTrue(all(i["level"] == "B1" for i in active))

    def test_duplicates_rejected_case_and_apostrophe_insensitive(self):
        b = make()
        self.assertIsNone(B.add_item(b, {"kind": "word", "fr": "Tiroir"}, status="new",
                                     source="x", today=D0))
        self.assertIsNotNone(B.find(b, "TIROIRS"))

    def test_matching_word_boundaries_and_accents(self):
        b = make()
        tiroir = B.find(b, "tiroir")
        self.assertTrue(B.seen_in(tiroir, ["Range-le dans le tiroir, stp."]))
        self.assertFalse(B.seen_in(tiroir, ["tiroirage"]))
        ep = B.find(b, "épanoui")
        self.assertFalse(B.seen_in(ep, ["epanoui"]))  # accents matter
        self.assertTrue(B.seen_in(ep, ["Elle est Épanouie."]))

    def test_short_form_of_expression_not_matched(self):
        b = make()
        item = B.find(b, "se rendre compte")
        self.assertFalse(B.seen_in(item, ["Il se rend au bureau."]))
        self.assertTrue(B.seen_in(item, ["Il se rend compte du problème."]))

    def test_grammar_never_string_matched(self):
        g = B.find(make(), "subjonctif après bien que")
        self.assertFalse(B.seen_in(g, ["subjonctif après bien que"]))

    def test_lifecycle_active_to_solid_then_review(self):
        b = make()
        t = B.find(b, "tiroir")
        for n in (1, 2, 3):
            B.record_exposure(t, day(n))
        B.promote(b, day(3))
        self.assertEqual(t["status"], "solid")
        B.promote(b, day(3 + B.REVIEW_AFTER_DAYS))
        self.assertEqual(t["status"], "active")

    def test_ask_makes_shaky_and_needs_fresh_exposures(self):
        b = make()
        t = B.find(b, "tiroir")
        B.record_exposure(t, day(1))
        B.record_ask(t, day(2))
        self.assertEqual(t["status"], "shaky")
        B.record_exposure(t, day(2))  # same day as the ask: doesn't count
        B.record_exposure(t, day(3))
        B.record_exposure(t, day(4))
        B.promote(b, day(4))
        self.assertEqual(t["status"], "shaky")
        B.record_exposure(t, day(5))
        B.promote(b, day(5))
        self.assertEqual(t["status"], "active")

    def test_level_up_after_quiet_week_and_down_after_many_asks(self):
        b = make()
        b["history"] = [{"date": day(n).isoformat(), "exposures": 3, "asks": 0} for n in range(4, 9)]
        self.assertEqual(B.adjust_level(b, day(9)), 4)
        self.assertIsNone(B.adjust_level(b, day(10)))  # once a week
        b["history"] = [{"date": day(n).isoformat(), "exposures": 3, "asks": 2} for n in range(12, 17)]
        self.assertEqual(B.adjust_level(b, day(17)), 3)

    def test_save_load_roundtrip_and_md(self):
        with tempfile.TemporaryDirectory() as d:
            b = make()
            B.save(b, Path(d))
            self.assertEqual(B.load(Path(d))["items"][0]["fr"], "tiroir")
            B.write_md(b, Path(d))
            self.assertIn("In rotation", (Path(d) / "bank.md").read_text(encoding="utf-8"))

    def test_real_seed_is_valid(self):
        seed = json.loads(B.seed_path().read_text(encoding="utf-8"))
        self.assertGreater(len(seed), 250)
        b = B.new_bank(seed, today=D0)
        self.assertEqual(len(b["items"]), len(seed), "seed has duplicate or malformed items")


class BriefTests(unittest.TestCase):
    def test_brief_content(self):
        b = make()
        B.record_ask(B.find(b, "tiroir"), D0)
        text = BR.build(b, "chat", dt.datetime(2026, 10, 1, 9))
        self.assertIn("level 3/5", text)
        self.assertIn("Recently asked about", text)
        self.assertIn("- tiroir", text)
        self.assertIn("subjonctif après bien que (bien que + subj.)", text)
        self.assertIn("No French at all", text)
        self.assertLess(len(text), 2600)

    def test_message_surface(self):
        self.assertIn("JSON keys", BR.build(make(), "message"))

    def test_pick_caps_and_rotates(self):
        seed = [{"kind": "word", "fr": f"mot{i}", "en": "x", "level": "B1", "forms": []}
                for i in range(30)]
        b = make(seed=seed)
        a = BR.pick(b, dt.datetime(2026, 10, 1, 9))
        c = BR.pick(b, dt.datetime(2026, 10, 1, 10))
        self.assertEqual(len(a), BR.PICK)
        self.assertNotEqual([i["id"] for i in a], [i["id"] for i in c])

    def test_brief_without_bank_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(BR.brief(base=Path(d)), "")


class HookTests(unittest.TestCase):
    def test_should_weave(self):
        self.assertTrue(hook.should_weave("fix the build", {}))
        self.assertTrue(hook.should_weave("fix", {"CLAUDE_CODE_ENTRYPOINT": "claude-vscode"}))
        self.assertFalse(hook.should_weave("fix", {"CLAUDE_CODE_ENTRYPOINT": "sdk-py"}))
        self.assertFalse(hook.should_weave("fix", {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}))
        self.assertFalse(hook.should_weave("fix", {"TISSER_OFF": "1"}))
        self.assertFalse(hook.should_weave("what dosage of ibuprofen?", {}))
        self.assertFalse(hook.should_weave("help with my taxes", {}))
        self.assertTrue(hook.should_weave("refactor the tax_rate column", {}))  # \b on tax_rate


class SourceTests(unittest.TestCase):
    def test_daily_log(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "2026-10-01.md").write_text(
                "# log\n\n**Mizzix (heartbeat) · 07:06**\nBonjour !\n\n**Tavin · 07:55**\n"
                "c'est quoi un tiroir ?\n\n**Crixizix · Magic_Card_Sorter · 08:00**\nUn drawer.\n",
                encoding="utf-8")
            conv = next(sources.daily_log(Path(d), D0, "Tavin"))
            self.assertEqual([t.role for t in conv], ["assistant", "user", "assistant"])
            self.assertEqual(conv[1].text, "c'est quoi un tiroir ?")

    def test_claude_code_skips_sdk_and_sidechains(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "proj"
            proj.mkdir()
            ts = "2026-10-01T15:00:00Z"
            rows = [
                {"type": "user", "timestamp": ts, "entrypoint": "claude-vscode",
                 "message": {"content": "<system-reminder>x</system-reminder>salut"}},
                {"type": "assistant", "timestamp": ts, "entrypoint": "claude-vscode",
                 "message": {"content": [{"type": "text", "text": "Le build passe."},
                                         {"type": "tool_use", "name": "Bash"}]}},
                {"type": "assistant", "timestamp": ts, "entrypoint": "sdk-py",
                 "message": {"content": [{"type": "text", "text": "bot"}]}},
                {"type": "assistant", "timestamp": ts, "isSidechain": True,
                 "message": {"content": [{"type": "text", "text": "sub"}]}},
            ]
            (proj / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            convs = list(sources.claude_code(D0, Path(d)))
            self.assertEqual([(t.role, t.text) for t in convs[0]],
                             [("user", "salut"), ("assistant", "Le build passe.")])


class ReflectTests(unittest.TestCase):
    def test_apply(self):
        b = make()
        result = {
            "asked": [{"kind": "word", "fr": "épanoui", "en": "fulfilled"},
                      {"kind": "word", "fr": "ruissellement", "en": "runoff"}],
            "grammar_used": [B.find(b, "subjonctif après bien que")["id"], "zzz"],
            "suggest": [{"kind": "idiom", "fr": "tant pis", "en": "too bad", "level": "B1"},
                        {"kind": "word", "fr": "tiroir", "en": "dup"}],
        }
        entry = reflect.apply(b, result, day(1), ["Le tiroir est plein."])
        self.assertEqual(B.find(b, "épanoui")["status"], "shaky")
        new = B.find(b, "ruissellement")
        self.assertEqual((new["status"], new["source"]), ("shaky", "asked"))
        self.assertEqual(B.find(b, "tant pis")["status"], "active")  # refilled into rotation
        self.assertEqual(B.find(b, "tiroir")["exposures"], [day(1).isoformat()])
        self.assertEqual(B.find(b, "subjonctif après bien que")["exposures"], [day(1).isoformat()])
        self.assertEqual((entry["asks"], entry["added"], entry["exposures"]), (2, 2, 2))

    def test_parse_tolerates_prose(self):
        r = reflect.parse('Sure:\n{"asked": [], "grammar_used": ["g0001"]}\n')
        self.assertEqual(r["grammar_used"], ["g0001"])
        self.assertEqual(r["suggest"], [])

    def test_run_end_to_end_with_fake_model(self):
        with tempfile.TemporaryDirectory() as d:
            base, logs, cc = Path(d) / "home", Path(d) / "logs", Path(d) / "cc"
            logs.mkdir()
            cc.mkdir()
            B.save(make(), base)
            (logs / "2026-10-02.md").write_text(
                "**Mizzix · 09:00**\nAlors, le tiroir est vide mais je pense que c'est pas grave, "
                "tu vois ?\n\n**Tavin · 09:01**\nc'est quoi épanoui ?\n", encoding="utf-8")
            seen = {}

            async def fake(msg):
                seen["msg"] = msg
                return json.dumps({"asked": [{"kind": "word", "fr": "épanoui", "en": "f"}]})

            entry = asyncio.run(reflect.run(day(1), base=base, log_dirs=[(logs, "Tavin")],
                                            call=fake, cc_root=cc))
            self.assertIn("c'est quoi épanoui", seen["msg"])
            self.assertEqual(entry["asks"], 1)
            saved = B.load(base)
            self.assertEqual(B.find(saved, "épanoui")["status"], "shaky")
            self.assertTrue((base / "bank.md").exists())

    def test_no_french_skips_model(self):
        with tempfile.TemporaryDirectory() as d:
            base, cc = Path(d) / "home", Path(d) / "cc"
            cc.mkdir()
            B.save(make(), base)

            async def boom(msg):
                raise AssertionError("model should not be called")

            entry = asyncio.run(reflect.run(day(1), base=base, call=boom, cc_root=cc))
            self.assertEqual(entry["asks"], 0)


if __name__ == "__main__":
    unittest.main()
