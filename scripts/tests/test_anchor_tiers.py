"""Regression test for regression R6 -- the two-tier anchor check.

`norm_heading()` folds ALL punctuation to spaces, which is more forgiving than
Obsidian actually is. That leniency produced a documented false pass: an anchor
writing a heading's em-dash as `--` passed `--check` and still landed at
top-of-page in the app (2026-08-08). Both spellings survive norm_heading, so the
checker could not see the one difference that mattered.

The fix compares in two tiers, and this test pins both empirically-grounded
cases against real observations rather than against a model of Obsidian:

  B1  EMPHASIS is clean.  Confirmed 2026-08-09 in the sample-vault-e vault: a
      citation into a heading carrying asterisk emphasis, written without the
      asterisks, lands ON the section. Must be strict-clean, NOT suspect.
  B2  EM-DASH SUBSTITUTION is suspect.  Confirmed 2026-08-08: heading carries
      an em-dash, link spells it `--`, lands at top-of-page. Must be reported
      as suspect -- this is the whole reason the tier exists.
  B3  A genuinely absent heading is still BROKEN, not merely suspect.
  B4  Suspects never leak into the broken list -- lint.py's headline count
      must not move when a suspect appears (8 vaults' close-out reads it).
  B5  lint.py surfaces the two as separate counts, end to end.

The em-dash is built with chr() rather than typed literally: this repo's
write-guard rejects smart punctuation in source files, and the codepoint also
makes it unambiguous which character the assertion is about.

Run:  python scripts/tests/test_anchor_tiers.py
Exit 0 = all assertions pass.  Exit 1 = at least one red.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_SCRIPTS = Path(__file__).resolve().parents[1]
EMDASH = chr(0x2014)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")


def build_fixture(root: Path, scripts_from: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    for s in ("check_raw.py", "audit_claims.py", "suggest_anchors.py", "lint.py",
              "_wikilib.py"):  # lint.py imports _wikilib (regression R80)
        shutil.copy2(scripts_from / s, root / "scripts" / s)
    (root / "_meta").mkdir()
    (root / "raw").mkdir()

    src = root / "wiki" / "sources"
    src.mkdir(parents=True)
    # One source page carrying all three target shapes.
    (src / "book.md").write_text(
        "---\ntitle: Book\n---\n\n"
        "# Book\n\n"
        "## What's *particularly* useful for the wiki\n\nEmphasis in the heading.\n\n"
        f"## 3. Real risk {EMDASH} outliving your money\n\nEm-dash in the heading.\n\n"
        "## E*Trade joint brokerage statement\n\nLone LITERAL asterisk, not emphasis.\n\n"
        "## A perfectly plain heading\n\nNothing special.\n",
        encoding="utf-8",
    )

    con = root / "wiki" / "concepts"
    con.mkdir(parents=True)
    (con / "claims.md").write_text(
        "---\ntitle: Claims\n---\n\n# Claims\n\n"
        # B1: emphasis omitted from the link -- resolves in Obsidian.
        "- Emphasis claim. See [[../sources/book#What's particularly useful for the wiki]].\n"
        # B2: em-dash spelled `--` -- does NOT resolve in Obsidian.
        "- Em-dash claim. See [[../sources/book#3. Real risk -- outliving your money]].\n"
        # B9: leading list marker's period dropped, every other character
        # verbatim (em-dash included) -- DOES resolve in Obsidian.
        f"- List-marker claim. See [[../sources/book#3 Real risk {EMDASH} outliving your money]].\n"
        # B3: no such heading at all.
        "- Absent claim. See [[../sources/book#No Such Heading Anywhere]].\n"
        # B6: the literal asterisk mangled two ways. NOT a marker omission --
        # a deleted/substituted character, the em-dash shape. Neither may be
        # reported clean. Deleted entirely -> nothing matches -> BROKEN.
        "- Etrade deleted. See [[../sources/book#ETrade joint brokerage statement]].\n"
        # Replaced by a space -> lenient matches, strict does not -> SUSPECT.
        "- Etrade spaced. See [[../sources/book#E Trade joint brokerage statement]].\n"
        # control: exact plain heading.
        "- Plain claim. See [[../sources/book#A perfectly plain heading]].\n",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripts-from", default=str(DEFAULT_SCRIPTS))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="wikillm6-"))
    root = tmp / "vault"
    try:
        build_fixture(root, Path(args.scripts_from))
        print(f"fixture: {root}")
        print(f"scripts under test: {args.scripts_from}\n")

        sys.path.insert(0, str(root / "scripts"))
        for mod in ("suggest_anchors", "audit_claims"):
            sys.modules.pop(mod, None)
        import suggest_anchors as sa

        problems, suspects = sa.check_anchors_full()
        blob_p, blob_s = " ".join(problems), " ".join(suspects)

        check(
            "B1 emphasis-only difference is clean (not suspect, not broken)",
            "particularly" not in blob_p and "particularly" not in blob_s,
            f"emphasis anchor absent from both lists; problems={len(problems)}, suspects={len(suspects)}",
        )
        check(
            "B2 em-dash written as '--' is reported SUSPECT",
            "Real risk" in blob_s,
            f"suspects={suspects!r}",
        )
        check(
            "B3 a genuinely absent heading is still BROKEN",
            "No Such Heading" in blob_p,
            f"problems={problems!r}",
        )
        check(
            "B4 suspects do not leak into the broken list",
            "Real risk" not in blob_p,
            f"the em-dash suspect must not appear among problems; broken count={len(problems)}",
        )

        # Before the paired-emphasis fix, EMPHASIS.sub("") deleted EVERY
        # asterisk, so both sides of `E*Trade` normalized to `etrade` and the
        # deleted-character anchor was reported CLEAN -- the exact em-dash
        # false pass, rebuilt one layer down.
        check(
            "B6 a LITERAL lone asterisk is not treated as emphasis",
            "ETrade joint" in blob_p and "E Trade joint" in blob_s,
            f"deleted '*' must be BROKEN and space-substituted '*' must be SUSPECT; "
            f"neither may be clean. problems={len(problems)}, suspects={len(suspects)}",
        )

        # B5: end-to-end through lint.py -- the headline broken count must not
        # absorb the suspect, and the suspect must still be visible.
        p = subprocess.run(
            [sys.executable, str(root / "scripts" / "lint.py")],
            capture_output=True, text=True, cwd=str(root),
        )
        line = next((l for l in p.stdout.splitlines() if "LOCATOR HYGIENE" in l), "")
        check(
            "B5 lint.py reports broken and suspect as separate counts",
            "broken heading anchors 2" in line and "suspect 2" in line,
            f"line={line.strip()!r}",
        )

        # B7: the --check CLI path. Codex review 2026-08-09 found it calling
        # the check_anchors() compatibility wrapper and printing "0 broken" on
        # a vault full of lenient-only matches -- the false pass reintroduced
        # in the one mode a session runs deliberately to catch it. B1-B6 all
        # exercise the library, so none of them saw it.
        p = subprocess.run(
            [sys.executable, str(root / "scripts" / "suggest_anchors.py"), "--check"],
            capture_output=True, text=True, cwd=str(root),
        )
        out = p.stdout
        check(
            "B7 --check surfaces suspects, not just broken",
            "SUSPECT" in out and "2 suspect" in out and "Real risk" in out,
            f"stdout head={out.strip().splitlines()[0] if out.strip() else out!r}",
        )
        # B9 records the 2026-08-09 click that settled regression R12: an anchor
        # dropping only the period after a leading list number, carrying the
        # em-dash and apostrophes verbatim, landed ON the section (lines 34-49
        # of sources/murray-simple-wealth.md highlighted). Before this, all 20
        # of the sample-vault-f vault's suspects looked alike; 16 of them were this.
        # It must be clean, and must NOT drag B2 clean with it -- B2 differs by
        # a substituted CHARACTER, which is the case that really breaks.
        check(
            "B9 a dropped leading-list-marker period is clean, em-dash still suspect",
            not any("3 Real risk" in s and "--" not in s for s in suspects)
            and "Real risk --" in blob_s,
            f"list-marker anchor must be clean while the em-dash one stays suspect; "
            f"suspects={len(suspects)}",
        )

        # B8 needs its OWN fixture: asserting rc==1 on the vault above would
        # only show that BROKEN anchors fail the check, which proves nothing
        # about suspects. A suspect-only vault is the case that discriminates.
        root2 = tmp / "vault-suspect-only"
        (root2 / "scripts").mkdir(parents=True)
        for s in ("check_raw.py", "audit_claims.py", "suggest_anchors.py", "lint.py",
                  "_wikilib.py"):  # lint.py imports _wikilib (regression R80)
            shutil.copy2(Path(args.scripts_from) / s, root2 / "scripts" / s)
        (root2 / "_meta").mkdir()
        (root2 / "raw").mkdir()
        (root2 / "wiki" / "sources").mkdir(parents=True)
        (root2 / "wiki" / "sources" / "book.md").write_text(
            "---\ntitle: Book\n---\n\n# Book\n\n"
            f"## 3. Real risk {EMDASH} outliving your money\n\nEm-dash in the heading.\n",
            encoding="utf-8",
        )
        (root2 / "wiki" / "concepts").mkdir(parents=True)
        (root2 / "wiki" / "concepts" / "claims.md").write_text(
            "---\ntitle: Claims\n---\n\n# Claims\n\n"
            "- Em-dash claim. See [[../sources/book#3. Real risk -- outliving your money]].\n",
            encoding="utf-8",
        )
        p2 = subprocess.run(
            [sys.executable, str(root2 / "scripts" / "suggest_anchors.py"), "--check"],
            capture_output=True, text=True, cwd=str(root2),
        )
        out2 = p2.stdout
        check(
            "B8 a suspect-only vault reports the suspect but exits 0",
            p2.returncode == 0 and "0 broken, 1 suspect" in out2,
            f"rc={p2.returncode} (want 0), head={out2.strip().splitlines()[0] if out2.strip() else out2!r}"
            " -- suspects are advisory and must not fail the check",
        )

        print()
        failed = [n for n, ok, _ in results if not ok]
        if failed:
            print(f"RED -- {len(failed)}/{len(results)} assertions failed: {failed}")
            return 1
        print(f"GREEN -- all {len(results)} assertions passed")
        return 0
    finally:
        if args.keep:
            print(f"\n(fixture kept at {root})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
