"""Regression test for regression R72 -- check_raw flags agent renders in raw/.

An agent rendered page 314 of a scanned PDF with PyMuPDF and wrote
raw/karvonen-1957/p314.png beside the PDF; check_raw then reported it as an
ordinary ADDED source. check_raw.py now prints a `DERIVED RENDER?` WARNING
line for an ADDED image/text file beside a same-stem PDF/EPUB, or a pNNN
image in a directory holding exactly one PDF. It never blocks an accept.

Pins (end to end, through a copied check_raw.py in a throwaway vault, since
the script resolves PROJECT_ROOT from its own __file__):
  D1 the exact karvonen layout: p314.png flagged, beside karvonen-1957.pdf
  D2 PROVENANCE.md in that same folder is NOT flagged
  D3 same-stem text render (book.txt beside book.epub) flagged
  D4 screen-capture run (page_001.png + run-manifest.json, no PDF): not flagged
  D5 p1.png in a directory holding TWO PDFs: not flagged (ambiguous)
  D6 a same-stem image whose PDF is in a DIFFERENT directory: not flagged
  D7 --summary prints the warning too, and the ADDED filename line stays verbatim
  D8 --accept of a flagged file still exits 0 and writes it into the baseline
  D9 a file already in the baseline is never flagged (ADDED only)
  R1 RED control: the pre-R72 check_raw.py (legacy fixture pre-sync-sweep,
     label 4d11a71) prints no
     DERIVED RENDER? line for the same fixture -- the pins can go red.

Run:  python scripts/tests/test_raw_derived_flag.py      Exit 0 = pass.
"""

from __future__ import annotations

from legacy_fixture import legacy_result
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
PRE_FIX = "4d11a71"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")


def build(root: Path, script_bytes: bytes) -> None:
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "check_raw.py").write_bytes(script_bytes)
    (root / "_meta").mkdir()
    raw = root / "raw"
    # Baseline holds the pre-existing sources + one old image (D9).
    (raw / "karvonen-1957").mkdir(parents=True)
    (raw / "karvonen-1957" / "karvonen-1957.pdf").write_bytes(b"%PDF-1.4 scan\n")
    (raw / "old").mkdir()
    (raw / "old" / "manual.pdf").write_bytes(b"%PDF-1.4 old\n")
    (raw / "old" / "manual.png").write_bytes(b"old image, baselined\n")
    run(root, "--init")
    # Now the ADDED set.
    (raw / "karvonen-1957" / "p314.png").write_bytes(b"\x89PNG render\n")
    (raw / "karvonen-1957" / "PROVENANCE.md").write_text("# provenance\n", encoding="utf-8")
    (raw / "books").mkdir()
    (raw / "books" / "book.epub").write_bytes(b"PK epub\n")
    (raw / "books" / "book.txt").write_text("extracted text\n", encoding="utf-8")
    capture = raw / "capture-run-2026-09-01"
    capture.mkdir()
    (capture / "page_001.png").write_bytes(b"\x89PNG frame\n")
    (capture / "run-manifest.json").write_text('{"completed": true}\n', encoding="utf-8")
    two = raw / "two-pdfs"
    two.mkdir()
    (two / "a.pdf").write_bytes(b"%PDF a\n")
    (two / "b.pdf").write_bytes(b"%PDF b\n")
    (two / "p1.png").write_bytes(b"\x89PNG ambiguous\n")
    (raw / "elsewhere").mkdir()
    (raw / "elsewhere" / "karvonen-1957.png").write_bytes(b"\x89PNG other dir\n")


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", str(root / "scripts" / "check_raw.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=root)


def flagged(out: str) -> list[str]:
    return [ln.strip() for ln in out.splitlines() if "DERIVED RENDER?" in ln]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wikillm72-"))
    try:
        green = tmp / "green"
        build(green, (SCRIPTS / "check_raw.py").read_bytes())
        p = run(green)
        f = flagged(p.stdout)
        print(f"  (detail run flagged {len(f)}: {f})")
        check("D1 karvonen p314.png flagged beside its PDF",
              any(ln.startswith("DERIVED RENDER? karvonen-1957/p314.png (beside karvonen-1957/karvonen-1957.pdf)")
                  and "agent output does not belong in raw/; move to a temp dir or --accept deliberately" in ln
                  for ln in f), f"flags={len(f)}")
        check("D2 PROVENANCE.md not flagged", not any("PROVENANCE.md" in ln for ln in f), f"flags={len(f)}")
        check("D3 same-stem text render beside an EPUB flagged",
              any(ln.startswith("DERIVED RENDER? books/book.txt (beside books/book.epub)") for ln in f), f"flags={len(f)}")
        check("D4 screen-capture run frame not flagged", not any("capture-run" in ln for ln in f), f"flags={len(f)}")
        check("D5 pNNN image beside TWO PDFs not flagged", not any("two-pdfs" in ln for ln in f), f"flags={len(f)}")
        check("D6 same stem in a different directory not flagged",
              not any("elsewhere" in ln for ln in f), f"flags={len(f)}")
        check("D9 baselined image never flagged", not any("old/manual.png" in ln for ln in f), f"flags={len(f)}")
        check("D0 exactly two warnings, exit 0", len(f) == 2 and p.returncode == 0,
              f"rc={p.returncode}; n={len(f)}")

        s = run(green, "--summary")
        added_line = next((ln for ln in s.stdout.splitlines()
                           if ln.startswith("    ") and "karvonen-1957/p314.png" in ln
                           and "DERIVED" not in ln), "")
        check("D7 --summary warns too; ADDED name line stays verbatim",
              len(flagged(s.stdout)) == 2 and "DERIVED" not in added_line
              and added_line.strip().split(", ").count("karvonen-1957/p314.png") == 1,
              f"flags={len(flagged(s.stdout))}; added line={added_line.strip()!r}")

        a = run(green, "--accept", "karvonen-1957/p314.png")
        base = json.loads((green / "_meta" / "raw-hashes.json").read_text(encoding="utf-8"))["files"]
        check("D8 --accept of a flagged file: exit 0, baselined, warning still printed",
              a.returncode == 0 and "karvonen-1957/p314.png" in base and len(flagged(a.stdout)) == 2,
              f"rc={a.returncode}; in baseline={'karvonen-1957/p314.png' in base}; "
              f"flags on accept run={len(flagged(a.stdout))}")
        after = run(green)
        check("D8b once accepted it is no longer ADDED, so no longer flagged",
              not any("p314.png" in ln for ln in flagged(after.stdout)), repr(flagged(after.stdout)))

        old = legacy_result(f"{PRE_FIX}:scripts/check_raw.py")
        if old.returncode == 0:
            red = tmp / "red"
            build(red, old.stdout)
            r = run(red)
            check("R1 control: pre-R72 check_raw.py prints no DERIVED RENDER? line",
                  r.returncode == 0 and "karvonen-1957/p314.png" in r.stdout and not flagged(r.stdout),
                  f"rc={r.returncode}; flags={flagged(r.stdout)!r}")
        else:
            print(f"  [SKIP] R1 control: legacy fixture {PRE_FIX} unavailable ({old.stderr.decode(errors='replace').strip()})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
