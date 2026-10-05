#!/usr/bin/env python3
"""Regression test for regression R94 -- the mechanical apply modes must leave an
on-disk record of WHICH pages they anchored.

The anchor-backfill workflow runs `suggest_anchors.py --apply-auto` and then a
bare re-scan one command later. --apply-auto wrote the pre-edit tier list to
_meta/anchor-suggestions.json; the re-scan overwrote it with the POST-AUTO
list, from which every entry that had just landed was gone (a scan keeps only
unanchored claims). Nothing on disk or in the workflow result then named the
pages the AUTO tier touched, so the maintenance pass's disk reconciliation
could only bound them by a count. The apply modes now write
_meta/anchor-auto-applied-latest.json, which the re-scan does not touch.

  A1  --apply-auto writes the record: one entry per anchor that LANDED, tier
      AUTO, count == the "=== APPLIED ===" line; the HIGH_REVIEW claim it did
      not apply is absent
  A2  apply_entries' `landed` excludes SKIPs and carries one entry per anchor
      written -- both citations of a two-source line, the stale entry neither
      (the record must never claim a page the run did not edit)
  A3  the bare re-scan the workflow runs next leaves the record intact while
      _meta/anchor-suggestions.json loses the applied entries -- the exact
      regression
  A4  a later --apply-auto that applies nothing REWRITES the record with
      entries: [] -- in a non-git Dropbox vault a stale list would make the
      reconciliation looser than the count bound it replaces
  A5  --apply-all records the HIGH_REVIEW tier too, tagged HIGH_REVIEW
      (recording only AUTO would reproduce the defect one tier over)
  A6  --apply FILE does NOT write the record: the decisions pass runs later in
      the same workflow and would otherwise clobber the Scan step's list
  A7  `generated` is a second-resolution TIMESTAMP, not a date: it is how the
      reconciliation tells this run's record from a leftover, and a date
      cannot do that for a pass that crosses midnight

Builds throwaway vaults with the CURRENT scripts copied in (suggest_anchors
derives ROOT from __file__). Run: python scripts/tests/test_anchor_auto_record.py
Exit 0 = pass.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
RECORD = "_meta/anchor-auto-applied-latest.json"
fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        fails.append(msg)


# One claim that tiers AUTO (unique figures + rare terms in exactly one
# section) and one that tiers HIGH_REVIEW (no figure, a verbatim Locators
# section winning decisively). Both anchors are plain ASCII, so neither is a
# fallback and neither can be refused as unlinkable.
HANDBOOK = ("---\ntitle: Handbook\n---\n\n# Handbook\n\n"
            "## Crate ceiling measurements\n\n"
            "Loaded crates averaged 42.5 kg with a ceiling of 58 kg across fourteen "
            "orchard harvest pallet trials.\n\n"
            "## Storage humidity notes\n\n"
            "Humidity below thirty percent cracked the lids during winter transport.\n")
INTERVIEW = ("---\ntitle: Interview\n---\n\n# Interview\n\n"
             "## Locators\n\n"
             "> The orchard foreman insisted that lidless stacking wrecked the seedlings.\n"
             "> Stacking without lids exposes orchard seedlings to wind scald overnight, "
             "ruining a third of the flats, the foreman said.\n"
             "> Wind scald ruined a third of the seedling flats that season.\n"
             "> The foreman refused lidless stacking afterwards.\n\n"
             "## Scheduling remarks\n\n"
             "Deliveries leave the packing shed before dawn.\n")
AUTO_LINE = ("- Loaded crates averaged 42.5 kg with a ceiling of 58 kg across fourteen "
             "orchard harvest pallet trials [[sources/handbook]].")
HIGH_LINE = ("- Stacking without lids exposes orchard seedlings to wind scald overnight, "
             "ruining a third of the flats, the foreman said [[sources/interview]].")
CRATES = f"---\ntitle: Crates\n---\n\n# Crates\n\n{AUTO_LINE}\n{HIGH_LINE}\n"


def build(root: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    for s in ("audit_claims.py", "suggest_anchors.py"):
        shutil.copy2(SCRIPTS / s, root / "scripts" / s)
    (root / "_meta").mkdir()
    src = root / "wiki" / "sources"
    src.mkdir(parents=True)
    (src / "handbook.md").write_text(HANDBOOK, encoding="utf-8")
    (src / "interview.md").write_text(INTERVIEW, encoding="utf-8")
    (root / "wiki" / "concepts").mkdir()
    (root / "wiki" / "concepts" / "crates.md").write_text(CRATES, encoding="utf-8")


def run(root: Path, *argv: str) -> str:
    r = subprocess.run([sys.executable, str(root / "scripts" / "suggest_anchors.py"),
                        "--no-history", *argv],
                       capture_output=True, text=True, errors="replace", cwd=str(root))
    return r.stdout + r.stderr


def record(root: Path) -> dict:
    return json.loads((root / RECORD).read_text(encoding="utf-8"))


def applied_count(out: str):
    m = re.search(r"=== APPLIED === (\d+)", out)
    return int(m.group(1)) if m else None


def case_a1_a3_a4(tmp: Path) -> None:
    root = tmp / "auto"
    build(root)
    started = datetime.datetime.now().replace(microsecond=0)
    out = run(root, "--apply-auto")
    check((root / RECORD).exists(), f"A1: record not written\n{out}")
    if not (root / RECORD).exists():
        return
    rec = record(root)
    check(rec["mode"] == "--apply-auto" and rec["applied"] == 1 and len(rec["entries"]) == 1,
          f"A1: mode/applied/entries {rec['mode']!r} {rec['applied']} {len(rec['entries'])}\n{out}")
    check(rec["applied"] == applied_count(out),
          f"A1: record count {rec['applied']} != APPLIED line {applied_count(out)}\n{out}")
    e = rec["entries"][0]
    check(e == {"rel": "wiki/concepts/crates.md", "lineno": 7, "slug": "handbook",
                "anchor": "Crate ceiling measurements", "tier": "AUTO"},
          f"A1: entry shape {e}")
    check(all(x["slug"] != "interview" for x in rec["entries"]),
          f"A1: an unapplied HIGH_REVIEW entry is in the record: {rec['entries']}")
    check(RECORD.replace("/", "\\") in out or RECORD in out,
          f"A1: the record path is not printed\n{out}")
    # A7 -- `generated` is a full timestamp, not a date. The reconciliation
    # decides "this run's record or a leftover?", a date cannot answer that
    # for an unattended pass that crosses midnight, and the only other answer
    # is an mtime a Dropbox re-download can rewrite.
    check(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", rec.get("generated", "")) is not None,
          f"A7: generated is not a second-resolution timestamp: {rec.get('generated')!r}")
    check(datetime.datetime.fromisoformat(rec["generated"]) >= started,
          f"A7: generated {rec['generated']!r} predates the run that wrote it")

    # A3 -- the workflow's very next command
    before = (root / RECORD).read_bytes()
    out2 = run(root)
    after = (root / RECORD).read_bytes()
    check(before == after, f"A3: the re-scan rewrote the record\n{out2}")
    sugg = json.loads((root / "_meta" / "anchor-suggestions.json").read_text(encoding="utf-8"))
    check(all(x["slug"] != "handbook" for x in sugg["entries"]),
          "A3: the re-scan still lists the anchored entry -- fixture no longer reproduces "
          "the overwrite this record exists for")
    check(json.loads(before.decode("utf-8"))["entries"][0]["slug"] == "handbook",
          "A3: the record lost the entry the suggestions file dropped")

    # A4 -- nothing left to apply: the record is rewritten empty, not left stale
    out3 = run(root, "--apply-auto")
    rec3 = record(root)
    check(rec3["entries"] == [] and rec3["applied"] == 0,
          f"A4: stale record kept: {rec3}\n{out3}")


def case_a2(tmp: Path) -> None:
    """apply_entries' landed list, in-process: SKIPs out, every written anchor in."""
    root = tmp / "landed"
    build(root)
    page = root / "wiki" / "concepts" / "crates.md"
    page.write_text("---\ntitle: Two\n---\n\n# Two\n\n"
                    "- Both cited here [[sources/handbook]] and [[sources/interview]].\n"
                    "- Edited since the scan [[sources/handbook]].\n", encoding="utf-8")
    sys.path.insert(0, str(root / "scripts"))
    for mod in ("suggest_anchors", "audit_claims"):
        sys.modules.pop(mod, None)
    try:
        import suggest_anchors as sa
        applied, skipped, _fallbacks, landed = sa.apply_entries([
            {"rel": "wiki/concepts/crates.md", "lineno": 7, "slug": "handbook",
             "line": "- Both cited here [[sources/handbook]] and [[sources/interview]].",
             "anchor": "Crate ceiling measurements", "tier": "AUTO"},
            {"rel": "wiki/concepts/crates.md", "lineno": 7, "slug": "interview",
             "line": "- Both cited here [[sources/handbook]] and [[sources/interview]].",
             "anchor": "Locators", "tier": "HIGH_REVIEW"},
            {"rel": "wiki/concepts/crates.md", "lineno": 8, "slug": "handbook",
             "line": "- A line this page no longer carries",
             "anchor": "Storage humidity notes", "tier": "AUTO"},
        ])
        check(applied == 2 and len(skipped) == 1, f"A2: applied={applied} skipped={skipped}")
        check(len(landed) == applied, f"A2: len(landed)={len(landed)} != applied={applied}")
        check([(x["slug"], x["lineno"], x["tier"]) for x in landed]
              == [("handbook", 7, "AUTO"), ("interview", 7, "HIGH_REVIEW")],
              f"A2: landed {landed}")
    finally:
        sys.path.remove(str(root / "scripts"))
        for mod in ("suggest_anchors", "audit_claims"):
            sys.modules.pop(mod, None)


def case_a5(tmp: Path) -> None:
    root = tmp / "all"
    build(root)
    out = run(root, "--apply-all")
    check((root / RECORD).exists(), f"A5: record not written\n{out}")
    if not (root / RECORD).exists():
        return
    rec = record(root)
    check(rec["mode"] == "--apply-all", f"A5: mode {rec['mode']!r}")
    check(sorted((x["slug"], x["tier"]) for x in rec["entries"])
          == [("handbook", "AUTO"), ("interview", "HIGH_REVIEW")],
          f"A5: entries {rec['entries']}\n{out}")
    check(rec["applied"] == applied_count(out),
          f"A5: record count {rec['applied']} != APPLIED line {applied_count(out)}\n{out}")


def case_a6(tmp: Path) -> None:
    root = tmp / "decisions"
    build(root)
    run(root, "--apply-auto")
    before = (root / RECORD).read_bytes()
    dec = root / "_meta" / "anchor-decisions-latest.json"
    dec.write_text(json.dumps({"entries": [
        {"rel": "wiki/concepts/crates.md", "lineno": 8, "slug": "interview",
         "line": HIGH_LINE, "anchor": "Locators"},
    ]}), encoding="utf-8")
    out = run(root, "--apply", str(dec))
    check(applied_count(out) == 1, f"A6: the decisions apply did not land\n{out}")
    check((root / RECORD).read_bytes() == before,
          f"A6: --apply clobbered the Scan step's record\n{out}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wikillm94-"))
    try:
        case_a1_a3_a4(tmp)
        case_a2(tmp)
        case_a5(tmp)
        case_a6(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print("FAIL\n  " + "\n  ".join(fails))
        return 1
    print("PASS: --apply-auto records the pages it anchored (count == the APPLIED line, SKIPs and "
          "unapplied tiers excluded); the workflow's re-scan leaves the record while the suggestions "
          "file drops those entries; an empty run rewrites the record rather than leaving it stale; "
          "--apply-all records HIGH_REVIEW too; --apply does not touch it; and `generated` is a "
          "second-resolution timestamp the reconciliation can key staleness on")
    return 0


if __name__ == "__main__":
    sys.exit(main())
