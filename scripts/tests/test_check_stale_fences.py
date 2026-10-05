"""Fence regressions for all seven check_stale scans (regression R141).

Run: python scripts/tests/test_check_stale_fences.py [--scripts-from DIR]
The optional directory supports running the same assertions against old code.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
parser.add_argument("--scripts-from", type=Path, default=Path(__file__).resolve().parents[1])
args = parser.parse_args()
spec = importlib.util.spec_from_file_location("check_stale", args.scripts_from / "check_stale.py")
stale = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stale)

TODAY = dt.date(2026, 9, 30)
PATH = "_meta/tasks.md"
STAMP = "- claim audit: Last run: never. Next due: ~2026-01"
EXEMPT = "claim audit: exempt -- no citations"
LABEL = "*(unverifiable: paywalled)*"
ENTRY = "## [2026-09-30] meta | downgrade | concepts/live | paywalled"
TIMESTAMPS = " ".join(f"`[0{i}:10]`" for i in range(5))


class FenceTests(unittest.TestCase):
    def assert_scans(self, prefix: list[str], closed: bool) -> None:
        """Run identical fence cases through every consumer, not just the helper."""
        payloads = ["TODO", LABEL, ENTRY, TIMESTAMPS,
                    "- [ ] example trigger: 2026-01-01", STAMP, EXEMPT]
        for payload in payloads:
            with self.subTest(payload=payload):
                # Put actionable content after every false closer and the real
                # closer. Only the final live line may survive a closed fence.
                lines = ["# Page"]
                for index, delimiter in enumerate(prefix):
                    content = payload
                    if index < len(prefix) - 1:
                        if payload == ENTRY:
                            content = ENTRY.replace("concepts/live", "concepts/hidden")
                        elif payload == TIMESTAMPS:
                            content = TIMESTAMPS.replace(":10", ":20")
                    lines.extend([delimiter, content])
                text = "\n".join(lines)
                lineno = len(lines)
                if payload == "TODO":
                    hits = stale.scan_markers(text)
                    self.assertEqual(hits, [(lineno, "needs-input", "TODO")] if closed else [])
                elif payload == LABEL:
                    self.assertEqual(stale.scan_unverifiable(text),
                                     [(lineno, "paywalled", True)] if closed else [])
                elif payload == ENTRY:
                    self.assertEqual(stale.downgrade_log_entries(text),
                                     {("concepts/live", "paywalled")} if closed else set())
                elif payload == TIMESTAMPS:
                    self.assertEqual(stale.transcript_triggers("", text),
                                     ["5 timestamps"] if closed else [])
                elif payload.startswith("- [ ]"):
                    self.assertEqual(stale.ledger_items(text),
                                     [(" ", dt.date(2026, 1, 1), payload[6:])] if closed else [])
                else:
                    texts = {PATH: text}
                    expected = [(PATH, "claim audit", dt.date(2026, 1, 1), "never")]
                    self.assertEqual(stale.recurring_due([PATH], texts),
                                     expected if closed and payload == STAMP else [])
                    status = stale.claim_audit_status([PATH], texts, TODAY)
                    if not closed:
                        self.assertIn("NOT INSTALLED", status)
                    else:
                        self.assertIn(f"{PATH}:{lineno}", status)
                        self.assertIn("installed" if payload == STAMP else "EXEMPT", status)

    def test_fence_matrix(self):
        for char, other in [("`", "~"), ("~", "`")]:
            cases = [
                ([char * 3, char * 3], True),
                (["  " + char * 4 + " markdown", char * 4 + " \t"], True),
                ([char * 4, char * 3, char * 4], True),
                ([char * 3, char * 5], True),
                ([char * 4, other * 5, char * 4], True),
                ([char * 4, char * 4 + " trailing text", char * 4], True),
                ([char * 3], False),
                ([char * 4, other * 4, char * 3], False),
            ]
            for delimiters, closed in cases:
                with self.subTest(delimiters=delimiters):
                    self.assert_scans(delimiters, closed)

    def test_unrelated_filters_and_frontmatter(self):
        text = "~~TODO~~\n- [x] TODO\n`*(unverifiable: invented)*`\n" + LABEL
        self.assertEqual(stale.scan_markers(text), [])
        self.assertEqual(stale.scan_unverifiable(text), [(4, "paywalled", True)])
        self.assertEqual(stale.transcript_triggers("tags: [video]", "~~~\n" + TIMESTAMPS),
                         ["tag video"])
        self.assertEqual(stale.recurring_due(["wiki/log.md"], {"wiki/log.md": STAMP}), [])
        self.assertIn("NOT INSTALLED", stale.claim_audit_status(
            ["_meta/fleet-conventions.md"], {"_meta/fleet-conventions.md": EXEMPT}, TODAY))
        # Backticks in a backtick info string make this inline code, not a fence.
        self.assertEqual(stale.scan_markers("```TODO```"), [(1, "needs-input", "```TODO```")])

    def test_stamp_context_cannot_cross_fence(self):
        text = "~~~\n- claim audit\n~~~\nLast run: never. Next due: ~2026-01"
        self.assertIn("NOT INSTALLED", stale.claim_audit_status([PATH], {PATH: text}, TODAY))
        text = "- claim audit\n  Last run: never. Next due: ~2026-01"
        self.assertIn(f"{PATH}:2", stale.claim_audit_status([PATH], {PATH: text}, TODAY))

    def test_adjacent_fence_types_and_crlf(self):
        text = "```\r\nTODO\r\n```\r\n~~~\r\nTODO\r\n~~~\r\nTODO"
        self.assertEqual(stale.scan_markers(text), [(7, "needs-input", "TODO")])

    def test_cli_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pages = {
                "wiki/concepts/live.md": "~~~\nTODO\n" + LABEL + "\n~~~\nTODO\n" + LABEL,
                "wiki/log.md": "~~~~\n" + ENTRY + "\n~~~\n" + ENTRY + "\n~~~~\n" + ENTRY,
                "_meta/open-loops.md": "~~~\n- [ ] hidden trigger: 2026-01-01\n" + STAMP
                    + "\n" + EXEMPT + "\n~~~\n" + STAMP,
                "wiki/sources/example.md": "~~~\n" + TIMESTAMPS + "\n~~~",
            }
            for path, text in pages.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            result = subprocess.run([sys.executable, str((args.scripts_from / "check_stale.py").resolve())],
                                    cwd=root, capture_output=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            for expected in ["0 open / 0 overdue", "1 hits in 1 files", "1 valid / 0 INVALID / 0 UNLOGGED",
                             "1 tracked / 1 DUE", "installed [_meta/open-loops.md:6]",
                             "0 transcript pages / 0 missing / 0 invalid / 0 legacy"]:
                self.assertIn(expected, result.stdout)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
