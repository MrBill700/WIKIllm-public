"""Regression test for regression R54 -- check_raw's line-ending-rewrite hint.

Pins the whole hint contract:
  L1  CRLF->LF rewrite gets a detail `note:` (fact wording, no verdict)
  L2  --summary keeps filenames VERBATIM (copyable into --accept) and puts the
      annotation out-of-band on its own line
  L3  the reverse LF->CRLF direction also fires
  L4  a same-length content change gets NO note (equal-size early return)
  L5  a different-length content change gets NO note (hash mismatch)
  L6  non-text extensions never get the hint, even when NUL-free (.pdf gate --
      a CRLF-corrupted binary must not be blessed as "content unchanged")
  L7  NUL-containing content on an allowlisted extension degrades to no note
  L8  --accept-all still computes and prints the hint (last chance before the
      baseline hash -- the hint's only input -- is overwritten) AND accepts
  L9  a baseline entry missing 'size' (hand-edited raw-hashes.json) degrades
      to no hint instead of crashing the report
  L10 a mixed-ending rewrite is OUT OF SCOPE by design: no hint, plain MODIFIED

Run:  python scripts/tests/test_le_hint.py

Builds a throwaway fixture vault and copies check_raw.py into it (it resolves
PROJECT_ROOT from __file__, so this fully isolates it from any real vault).

Exit 0 = all assertions pass.  Exit 1 = at least one red.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_SCRIPTS = Path(__file__).resolve().parents[1]
CRLF_DOC = b"line one\r\nline two\r\nline three\r\n"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "check_raw.py"), *args],
        capture_output=True, text=True, cwd=str(root),
    )


def reset(root: Path) -> None:
    """Accept everything so the next case starts from a clean baseline."""
    run(root, "--accept-all")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripts-from", default=str(DEFAULT_SCRIPTS))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="wikillm54-"))
    root = tmp / "vault"
    try:
        (root / "scripts").mkdir(parents=True)
        shutil.copy2(Path(args.scripts_from) / "check_raw.py", root / "scripts" / "check_raw.py")
        (root / "_meta").mkdir()
        raw = root / "raw"
        raw.mkdir()
        (raw / "doc.md").write_bytes(CRLF_DOC)
        (raw / "fake.pdf").write_bytes(b"%PDF-1.4 pretend\r\nno nul bytes here\r\nend\r\n")
        (raw / "data.csv").write_bytes("a,b\r\n1,2\r\n".encode("utf-16"))  # NULs on an allowlisted ext
        run(root, "--init")
        print(f"fixture: {root}\nscript under test: {args.scripts_from}\n")

        # --- L1: CRLF -> LF rewrite -> detail note, fact wording only.
        (raw / "doc.md").write_bytes(CRLF_DOC.replace(b"\r\n", b"\n"))
        out = run(root).stdout
        check("L1 CRLF->LF gets the note (fact, not verdict)",
              "note:" in out and "line endings only" in out
              and "identify what rewrote" in out and "safe to re-accept" not in out,
              f"note present={'note:' in out}")

        # --- L2: --summary keeps names verbatim; annotation is out-of-band.
        out = run(root, "--summary").stdout
        verbatim = re.search(r"^    doc\.md$", out, re.M) is not None
        annotated = "line-endings-only rewrites" in out and "doc.md" in out.split("rewrites", 1)[-1]
        check("L2 --summary: verbatim filename + out-of-band annotation",
              verbatim and annotated, f"verbatim={verbatim} annotated={annotated}")
        reset(root)

        # --- L3: reverse direction.
        (raw / "doc.md").write_bytes(CRLF_DOC)
        out = run(root).stdout
        check("L3 LF->CRLF (reverse) also fires", "line endings only" in out, out.strip()[-160:])
        reset(root)

        # --- L4: same-length real change -> no note.
        (raw / "doc.md").write_bytes(b"line one\r\nlino two\r\nline three\r\n")
        out = run(root).stdout
        check("L4 same-length content change: MODIFIED, no note",
              "MODIFIED" in out and "line endings only" not in out, "equal-size early return")
        reset(root)

        # --- L5: different-length real change -> no note.
        (raw / "doc.md").write_bytes(b"line one\r\nCOMPLETELY DIFFERENT\r\nline three\r\n")
        out = run(root).stdout
        check("L5 diff-length content change: MODIFIED, no note",
              "MODIFIED" in out and "line endings only" not in out, "hash mismatch path")
        reset(root)

        # --- L6: extension gate -- NUL-free .pdf CRLF-rewritten must NOT be blessed.
        (raw / "fake.pdf").write_bytes(b"%PDF-1.4 pretend\r\nno nul bytes here\r\nend\r\n".replace(b"\r\n", b"\n"))
        out = run(root).stdout
        check("L6 .pdf rewrite: MODIFIED, NO hint (extension gate)",
              "MODIFIED" in out and "line endings only" not in out, "binaries never blessed")
        reset(root)

        # --- L7: NUL-containing allowlisted extension degrades quietly.
        (raw / "data.csv").write_bytes("a,b\n1,2\n".encode("utf-16"))
        out_p = run(root)
        check("L7 UTF-16 .csv: no note, no crash",
              "line endings only" not in out_p.stdout and "Traceback" not in out_p.stderr,
              "NUL degrade")
        reset(root)

        # --- L8: --accept-all still hints AND accepts.
        (raw / "doc.md").write_bytes(b"line one\r\nCOMPLETELY DIFFERENT\r\nline three\r\n".replace(b"\r\n", b"\n"))
        out = run(root, "--accept-all").stdout
        check("L8 --accept-all: hint fires and acceptance completes",
              "line endings only" in out and "changes accepted" in out,
              "last chance before the baseline hash is overwritten")

        # --- L9: baseline entry missing 'size' -> hint degrades, report survives.
        (raw / "doc.md").write_bytes(CRLF_DOC)
        hashes = root / "_meta" / "raw-hashes.json"
        payload = json.loads(hashes.read_text(encoding="utf-8"))
        del payload["files"]["doc.md"]["size"]
        hashes.write_text(json.dumps(payload), encoding="utf-8")
        out_p = run(root)
        check("L9 baseline missing 'size': no crash, MODIFIED still reported",
              out_p.returncode == 0 and "MODIFIED" in out_p.stdout and "Traceback" not in out_p.stderr,
              f"rc={out_p.returncode}")
        reset(root)

        # --- L10: mixed-ending rewrite is out of scope -- no hint, no claim.
        (raw / "doc.md").write_bytes(b"alpha\r\nbeta\ngamma\r\n")
        reset(root)
        (raw / "doc.md").write_bytes(b"alpha\nbeta\ngamma\n")  # mixed -> uniform LF
        out = run(root).stdout
        check("L10 mixed-ending baseline: MODIFIED, no hint (documented scope)",
              "MODIFIED" in out and "line endings only" not in out,
              "uniform candidates cannot reconstruct a mixed baseline")

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
