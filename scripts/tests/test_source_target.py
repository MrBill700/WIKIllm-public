#!/usr/bin/env python3
"""Regression test: cross-vault sources links must not enter the claim
population (is_source_target), be silently skipped by anchor validation
(check_anchors), or receive anchor suggestions from THIS vault's same-slug
page (suggest_anchors scan population).

The bug (fixed 2026-08-08): is_source_target substring-matched "sources/",
admitting cross-vault paths like [[../../Other/wiki/sources/x]] that
check_anchors' stricter extractor then skipped -- counted-but-unchecked
claims that inflated coverage, and, unanchored, could be auto-anchored
from the wrong vault's page.

Builds a throwaway two-vault fixture in a temp dir, copies the CURRENT
scripts into VaultA, and asserts on the population and validation results.
Run: python scripts/tests/test_source_target.py   (exit 0 = pass)
"""
import importlib
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def build_fixture(base: Path) -> Path:
    va = base / "VaultA"
    vb = base / "VaultB"
    (va / "scripts").mkdir(parents=True)
    (va / "wiki" / "sources").mkdir(parents=True)
    (va / "wiki" / "concepts").mkdir(parents=True)
    (va / "_meta").mkdir()
    (vb / "wiki" / "sources").mkdir(parents=True)
    for name in ("audit_claims.py", "suggest_anchors.py"):
        shutil.copy2(SCRIPTS / name, va / "scripts" / name)
    (va / "wiki" / "sources" / "shared.md").write_text(
        "# Shared\n\n## Local Heading A\n\nlocal body\n", encoding="utf-8")
    (vb / "wiki" / "sources" / "shared.md").write_text(
        "# Shared\n\n## Real Heading B\n\nreal body\n", encoding="utf-8")
    (va / "wiki" / "concepts" / "cite.md").write_text(
        "# Cite\n\n## Notes\n\n"
        "- control claim cites [[sources/shared#Local Heading A]] in-vault\n"
        "- wrong-depth in-vault citation [[../../sources/shared#Local Heading A]] stays a claim\n"
        "- cross-vault anchored [[../../../VaultB/wiki/sources/shared#Real Heading B]] here\n"
        "- cross-vault anchored [[../../../VaultB/wiki/sources/shared#Local Heading A]] here\n"
        "- cross-vault unanchored [[../../../VaultB/wiki/sources/shared]] with figure $2,500\n",
        encoding="utf-8")
    return va


def main() -> int:
    fails = []
    with tempfile.TemporaryDirectory() as td:
        va = build_fixture(Path(td))
        sys.path.insert(0, str(va / "scripts"))
        for m in ("audit_claims", "suggest_anchors"):
            sys.modules.pop(m, None)
        try:
            ac = importlib.import_module("audit_claims")
            sa = importlib.import_module("suggest_anchors")
            claims = list(ac.iter_claim_lines())
            problems = sa.check_anchors()

            # in-vault claims (plain and wrong-depth-../ shapes) stay counted
            if len(claims) != 2:
                fails.append(f"population is {len(claims)}, expected 2 in-vault claims "
                             f"(lines: {[c['line'][:60] for c in claims]})")
            # cross-vault lines are out of the population entirely
            if any("VaultB" in c["line"] for c in claims):
                fails.append("cross-vault sources link entered the claim population")
            # no false anchor problems on the in-vault claims
            if problems:
                fails.append(f"unexpected anchor problems: {problems}")
            # unanchored scan population must not contain the cross-vault line
            unanchored = [c for c in claims if not c["anchored"]]
            if unanchored:
                fails.append(f"unexpected unanchored claims (suggestion targets): "
                             f"{[c['line'][:60] for c in unanchored]}")
        finally:
            sys.path.remove(str(va / "scripts"))
            for m in ("audit_claims", "suggest_anchors"):
                sys.modules.pop(m, None)

    if fails:
        print("FAIL")
        for f in fails:
            print(f"  {f}")
        return 1
    print("PASS: cross-vault sources links excluded; in-vault claims (incl. "
          "wrong-depth ../) unaffected; no false anchor flags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
