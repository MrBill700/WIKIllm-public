#!/usr/bin/env python3
"""ocr_sidecars.py -- full-page Tesseract text for a screen_capture.ps1 run, as a verification substrate.

For every page_NNN.png in a capture run directory, writes <out-dir>/page_NNN.txt
holding Tesseract's full-page OCR (regression R50). The first line of each file is a
provenance header:

  # OCR (tesseract <ver>) of <relpath> on <date>; verification substrate, not source

Use the sidecars for mechanical quote checks (the normalizer-diff pattern PDF
sources already get) and for grepping sampled claims. They are NOT the source:
reader agents still read the PNGs, and table/figure values still need eyes on
the image -- OCR is a routing heuristic, eyes certify.

Usage:
  python scripts/ocr_sidecars.py raw/book/ch1 --out-dir C:/tmp/book-ocr/ch1

--out-dir is REQUIRED and may not be the run directory or anywhere beneath a
vault's raw/ tree, tested on the canonicalized path (.., junctions, symlinks and
alternate spellings resolve first): derived output never lands beside the frames
or anywhere in raw/ (regression R50 ruling, option 3; raw/-wide refusal ruled 2026-09-26 --
raw/ holds human/capture-script material; agent-generated renders and OCR go
elsewhere unless deliberately directed). Existing page_NNN.txt files in
--out-dir are overwritten.

Dependencies (lazy, optional for the rest of the toolset): Pillow, pytesseract,
and the Tesseract binary. A missing one prints a one-line remedy and exits 2.

Exit codes: 0 = sidecars written, 1 = no page_NNN.png frames found or a frame
could not be read (BADIMAGE; the others are still written),
2 = usage error / missing dependency.
"""

import argparse
import os
import datetime
import re
import sys
from pathlib import Path

PAGE_RE = re.compile(r"^page_(\d+)\.png$", re.I)

REMEDY = ("ocr_sidecars.py: missing dependency {what} -- `pip install pillow pytesseract` "
          "and install the Tesseract binary (Windows: UB-Mannheim build), or pass --tesseract-cmd")


def vault_relpath(p: Path) -> str:
    """Path relative to the enclosing vault (first ancestor holding _meta/), else to cwd, else absolute."""
    for anc in p.parents:
        if (anc / "_meta").is_dir():
            return p.relative_to(anc).as_posix()
    try:
        return p.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return p.as_posix()


VAULT_MARKERS = ("scripts", "wiki", "_meta", "CLAUDE.md")


def canon(p):
    """Canonical absolute path: realpath collapses .., symlinks and Windows junctions in the
    existing prefix, so no spelling of a location under raw/ can slip past the guard."""
    return Path(os.path.realpath(str(Path(p).expanduser())))


def _is_vault_raw(d):
    """A directory named raw whose parent looks like a vault root."""
    return d.name.lower() == "raw" and any((d.parent / m).exists() for m in VAULT_MARKERS)


def raw_roots(*paths):
    """Every vault raw/ tree implied by the given paths (the out-dir as spelled, the run dirs
    as spelled, the cwd), as CANONICAL directories. Ancestry is walked on both the unresolved
    spelling (os.path.abspath) and the canonical one: a vault whose raw/ is itself a junction
    or symlink keeps the name only in the unresolved spelling, so walking the canonical path
    alone would erase the very ancestor the rule keys on. Each root found is canonicalized
    before it is returned, so the containment test compares like with like."""
    roots = set()
    for p in paths:
        for q in (Path(os.path.abspath(str(Path(p).expanduser()))), canon(p)):
            for d in (q, *q.parents):
                if _is_vault_raw(d):
                    roots.add(canon(d))
    return roots


def out_dir_under_raw(out_dir, *anchors):
    """Return the offending (canonical) raw/ root if the canonical out_dir sits anywhere
    beneath a vault raw/ tree, else None. Roots come from the out-dir's own ancestry (both
    spellings), the run dirs' ancestry (so a run reached through a raw/ junction still names
    its vault raw/), and the cwd's (a vault-root cwd contributes its raw/)."""
    out = canon(out_dir)
    for root in raw_roots(out_dir, Path.cwd(), *anchors):
        if out == root or out.is_relative_to(root):
            return root
    return None


def main():
    ap = argparse.ArgumentParser(
        description="Full-page Tesseract OCR of a capture run into page_NNN.txt sidecars "
                    "(verification substrate, not source).")
    ap.add_argument("run", metavar="RUN_DIR", help="capture run directory holding page_NNN.png frames")
    ap.add_argument("--out-dir", required=True,
                    help="REQUIRED: where page_NNN.txt go; never the run directory (regression R50)")
    ap.add_argument("--tesseract-cmd", default=None,
                    help="path to the tesseract binary when it is not on PATH")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    run = canon(args.run)
    if not run.is_dir():
        print(f"ocr_sidecars.py: not a directory: {run}", file=sys.stderr)
        return 2
    out_dir = canon(args.out_dir)
    if out_dir == run or out_dir.is_relative_to(run):
        print("ocr_sidecars.py: --out-dir may not be (or be inside) the run directory -- derived "
              "output never lands beside the frames (regression R50)", file=sys.stderr)
        return 2
    raw_root = out_dir_under_raw(args.out_dir, args.run)  # spelled paths: a raw/ junction keeps its name only in the unresolved spelling
    if raw_root is not None:
        print(f"ocr_sidecars.py: --out-dir may not be anywhere under a vault raw/ tree "
              f"(resolves beneath {raw_root}) -- derived output never lands in raw/ (regression R50)",
              file=sys.stderr)
        return 2
    if out_dir.exists() and not out_dir.is_dir():
        print(f"ocr_sidecars.py: --out-dir exists and is not a directory: {out_dir}", file=sys.stderr)
        return 2

    # Lazy optional dependencies (regression R49 ruling): only needed to actually run.
    try:
        from PIL import Image
    except ImportError:
        print(REMEDY.format(what="Pillow"), file=sys.stderr)
        return 2
    try:
        import pytesseract
    except ImportError:
        print(REMEDY.format(what="pytesseract"), file=sys.stderr)
        return 2
    if args.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = args.tesseract_cmd
    try:
        tess_ver = str(pytesseract.get_tesseract_version()).split()[0]
    except Exception:
        print(REMEDY.format(what="Tesseract binary (not found or not runnable)"), file=sys.stderr)
        return 2

    frames = sorted(((int(m.group(1)), p) for p in run.iterdir()
                     if p.is_file() and (m := PAGE_RE.match(p.name))))
    if not frames:
        print(f"ocr_sidecars.py: no page_NNN.png frames in {run}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    chars, written, failed = 0, 0, []
    for _, f in frames:
        try:
            with Image.open(f) as im:
                text = pytesseract.image_to_string(im)
        except (OSError, ValueError, pytesseract.TesseractError) as e:
            # A half-written last frame is realistic (runs abort on focus loss).
            failed.append(f"{f.name}: {type(e).__name__}: {e}")
            (out_dir / (f.stem + ".txt")).unlink(missing_ok=True)   # no stale sidecar from an earlier run
            continue
        header = (f"# OCR (tesseract {tess_ver}) of {vault_relpath(f)} on {today}; "
                  "verification substrate, not source")
        (out_dir / (f.stem + ".txt")).write_text(header + "\n" + text, encoding="utf-8")
        chars += len(text)
        written += 1
    print(f"ocr_sidecars: {written} sidecar(s), {chars} chars -> {out_dir} (tesseract {tess_ver})")
    for msg in failed:
        print(f"  BADIMAGE {msg} -- no sidecar written")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
