"""Regression test for regression R7 -- raw/ scanners must recurse.

Red against the pre-fix scripts (5 of 7 assertions), green after.
Also pins the two constraints the naive rglob fix violates:
  A5 -- keys/pointers stay distinctive, never a bare "report.md"
  A6 -- a staged directory accepts as ONE unit, and only that unit
plus the layout-agnostic contract (regression R9):
  A8 -- any file at any depth under raw/ is visible to check_raw,
        so a /watch staging-layout change cannot re-open #7 silently

Run:  python scripts/tests/test_raw_recursion.py

Builds a throwaway fixture vault, copies the scripts under test into it
(they resolve PROJECT_ROOT from __file__, so this fully isolates them from
any real vault), plants one FLAT source and one NESTED source under
raw/watched/<slug>/report.md, and asserts the nested one is visible.

Usage:  python repro_wikillm7.py [--scripts-from DIR]

Exit 0 = all assertions pass (bug fixed).  Exit 1 = at least one red.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Default: the scripts/ dir this test lives under, so it follows the repo.
DEFAULT_SCRIPTS = Path(__file__).resolve().parents[1]
SLUG = "my-talk-2026-08-09"
NESTED_REL = f"watched/{SLUG}/report.md"
FLAT_NAME = "a-flat-source-document.pdf"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")


def build_fixture(root: Path, scripts_from: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    for s in ("check_raw.py", "audit_claims.py"):
        shutil.copy2(scripts_from / s, root / "scripts" / s)

    raw = root / "raw"
    (raw / "watched" / SLUG).mkdir(parents=True)
    (raw / FLAT_NAME).write_bytes(b"flat control source, not the bug\n")
    (raw / NESTED_REL).write_text(
        "# report\n\nA /watch report staged by the watch skill.\n", encoding="utf-8"
    )

    (root / "_meta").mkdir()

    src = root / "wiki" / "sources"
    src.mkdir(parents=True)
    # Sources page names BOTH raw pointers the way real pages do.
    (src / "my-talk.md").write_text(
        "---\ntitle: My Talk\nsources: [\"raw/watched/%s/report.md\"]\n---\n\n"
        "# My Talk\n\n## Locators\n\nSource file: `raw/%s`.\n"
        % (SLUG, NESTED_REL),
        encoding="utf-8",
    )
    (src / "flat-thing.md").write_text(
        "---\ntitle: Flat Thing\nsources: [\"raw/%s\"]\n---\n\n"
        "# Flat Thing\n\n## Locators\n\nSource file: `raw/%s`.\n" % (FLAT_NAME, FLAT_NAME),
        encoding="utf-8",
    )
    con = root / "wiki" / "concepts"
    con.mkdir(parents=True)
    (con / "a-concept.md").write_text(
        "---\ntitle: A Concept\n---\n\n# A Concept\n\n"
        "- A claim from the talk. See [[../sources/my-talk#Locators]].\n"
        "- A claim from the flat thing. See [[../sources/flat-thing#Locators]].\n",
        encoding="utf-8",
    )


def run(root: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / script), *args],
        capture_output=True, text=True, cwd=str(root),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripts-from", default=str(DEFAULT_SCRIPTS))
    ap.add_argument("--keep", action="store_true", help="keep the fixture dir for inspection")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="wikillm7-"))
    root = tmp / "vault"
    try:
        build_fixture(root, Path(args.scripts_from))
        print(f"fixture: {root}")
        print(f"scripts under test: {args.scripts_from}\n")

        # --- A1: check_raw --init must record the nested file in the baseline.
        p = run(root, "check_raw.py", "--init")
        baseline = json.loads((root / "_meta" / "raw-hashes.json").read_text(encoding="utf-8"))
        keys = sorted(baseline.get("files", baseline).keys()) if isinstance(baseline, dict) else []
        nested_keys = [k for k in keys if "report" in k or "watched" in k]
        check(
            "A1 check_raw --init records the nested report",
            bool(nested_keys),
            f"baseline keys={keys!r} -> nested={nested_keys!r} (init rc={p.returncode})",
        )

        # --- A2: after modifying the nested file, a plain run must report a change.
        (root / "raw" / NESTED_REL).write_text("# report\n\nEDITED.\n", encoding="utf-8")
        p = run(root, "check_raw.py")
        out = p.stdout
        check(
            "A2 check_raw detects a modification to the nested report",
            ("report.md" in out) or (SLUG in out),
            f"'0 modified' in output={'0 modified' in out}; mentions report/slug={'report.md' in out or SLUG in out}",
        )

        # --- A3: control -- the FLAT file must still work (guards against a fix regressing it).
        (root / "raw" / FLAT_NAME).write_bytes(b"flat control source, EDITED\n")
        p = run(root, "check_raw.py")
        check(
            "A3 control: check_raw still detects the flat file",
            FLAT_NAME in p.stdout,
            f"flat name present in output={FLAT_NAME in p.stdout}",
        )

        # --- A4: audit_claims must resolve a raw pointer for the nested source,
        #         and must NOT collapse it to a bare 'report.md'.
        p = run(root, "audit_claims.py", "--n", "10", "--no-history")
        out = p.stdout
        seg = ""
        for line in out.splitlines():
            if "sources/my-talk]]" in line:
                seg = line
                break
        resolved = seg and "(none named on page)" not in seg
        distinctive = SLUG in seg or "watched" in seg
        check(
            "A4 audit_claims resolves a raw pointer for the nested source",
            bool(resolved),
            f"line={seg.strip()!r}",
        )
        check(
            "A5 that pointer is distinctive, not a bare 'report.md'",
            bool(resolved and distinctive),
            "pointer must contain the slug or 'watched/' so two watch reports are distinguishable",
        )

        # --- A6: a watch report stages a whole DIRECTORY (report + hero frames).
        #     Accepting it must not require naming every frame individually.
        (root / "raw" / "watched" / SLUG / "frame_0001.jpg").write_bytes(b"\xff\xd8fake jpeg 1")
        (root / "raw" / "watched" / SLUG / "frame_0002.jpg").write_bytes(b"\xff\xd8fake jpeg 2")
        p = run(root, "check_raw.py", "--accept", f"watched/{SLUG}")
        out = p.stdout
        p2 = run(root, "check_raw.py")
        # Assert POSITIVELY on what was accepted -- a "nothing pending" check is
        # vacuously true on any build where the nested files were never visible
        # in the first place, which is exactly the bug under test.
        accepted_line = next((l for l in out.splitlines() if l.startswith("Accepted (")), "")
        n_accepted = 0
        if accepted_line:
            n_accepted = int(accepted_line.split("(")[1].split(")")[0])
        # report.md + 2 frames = 3 files, all under the one staged directory.
        enough = n_accepted >= 3
        # The unrelated flat file, modified in A3 and never accepted, must STILL
        # be pending -- prefix-accept has to be scoped, not a wildcard.
        flat_still_pending = FLAT_NAME in p2.stdout
        check(
            "A6 --accept accepts a staged directory by prefix, and only that",
            enough and flat_still_pending,
            f"accepted={n_accepted} (need >=3), unrelated flat still pending={flat_still_pending}; "
            f"line={accepted_line.strip()!r}",
        )

        # --- A7: control -- a bogus name must still be reported as skipped,
        #     i.e. prefix-accept must not silently swallow typos.
        p = run(root, "check_raw.py", "--accept", "no-such-file.pdf")
        check(
            "A7 control: a bogus --accept name is still reported as skipped",
            "skipped" in p.stdout,
            f"'skipped' present={'skipped' in p.stdout}",
        )

        # --- A8: the raw/ contract, skill-agnostic (regression R9). A1-A7 pin the
        #     /watch layout specifically, but that path is hardcoded here and
        #     nothing stops the skill changing its staging shape. So assert the
        #     weaker property that survives any layout change: ANY file at ANY
        #     depth under raw/, whatever its name, is visible to check_raw.
        contract_files = [
            "some-dir/notes.md",            # depth 2, nothing to do with /watch
            "a/b/c/deep-file.pdf",          # depth 3, binary-ish name
            "clips/2026/short.txt",         # depth 3, dated tree
        ]
        for rel in contract_files:
            f = root / "raw" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"contract fixture: " + rel.encode())
        p = run(root, "check_raw.py")
        missing = [rel for rel in contract_files if rel not in p.stdout]
        check(
            "A8 contract: any file at any depth under raw/ is visible",
            not missing,
            f"planted={contract_files!r}; not reported as pending={missing!r}",
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
