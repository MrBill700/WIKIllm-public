#!/usr/bin/env python3
"""verify_capture.py -- footer-OCR geometry scanner for screen_capture.ps1 runs.

Replaces the manual "read first/middle/last frame of every run" pass that every
whole-book ebook reader ingest starts with (regression R49). For every page_NNN.png frame in
the run directories given, it crops the reader's footer strip (a band at the
bottom of the frame, derived from the image height, center-weighted), OCRs it
with Tesseract, parses `SECTION -- x / y` (en/em dashes folded to `--`, `x/y`
tolerated unspaced), and emits:

  1. a screen map (run | page | footer | section) as screen-map.md + screen-map.json
     in --out-dir;
  2. flags: non-contiguous x (GAP), y-total mismatch (Y-MISMATCH), duplicate
     frames (DUPLICATE: byte-identical sha256, or the same footer twice in a
     row -- two separate captures of one screen are rarely byte-identical),
     footer-less frames (FOOTERLESS: junk, e.g. the ebook reader library grid), frames
     whose footer band has text that does not parse (UNREADABLE), frames that
     cannot be read or OCR'd (BADIMAGE), and run-manifest.json mismatches
     (MANIFEST);
  3. section transitions, listed as information (not flags).

Run directories are checked as ONE stream in argument order, so a screen skipped
or duplicated at the boundary between two runs is caught. Pass them in capture
order (each run-manifest.json carries `started`).

ebook reader-footer readers only: a reader with no footer counter (e.g. archive.org
BookReader) reports every frame FOOTERLESS -- the tool is not meaningful there.

Usage:
  python scripts/verify_capture.py raw/book/ch1 raw/book/ch2 --out-dir C:/tmp/book-map

--out-dir is REQUIRED and may not be a run directory or anywhere beneath a vault's
raw/ tree, tested on the canonicalized path (.., junctions, symlinks and alternate
spellings resolve first): derived output never lands beside the frames or anywhere
in raw/ (regression R50 ruling, option 3; raw/-wide refusal ruled 2026-09-26).

Dependencies (lazy, optional for the rest of the toolset): Pillow, pytesseract,
and the Tesseract binary. A missing one prints a one-line remedy and exits 2.

Exit codes: 0 = clean, 1 = flags raised, 2 = usage error / missing dependency.
"""

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Footer band, as fractions of the frame. The ebook reader footer baseline sits at
# ~0.971 of the height (2040/2100 on a 3840x2100 frame, 2015/2075 on 3757x2075);
# the last body line ends near 0.94, so the band starts below it.
BAND_TOP = 0.955
BAND_XFRAC = 0.5          # center-weighted: the middle half of the width
UPSCALE = 2               # tesseract reads ~15px glyphs better at 2x

DASHES = {"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
          "\u2014": "-", "\u2015": "-", "\u2212": "-"}
FOOTER_RE = re.compile(r"^(?P<section>.*?\S)\s*-{1,3}\s*(?P<x>\d{1,4})\s*/\s*(?P<y>\d{1,4})\s*$")
PAGE_RE = re.compile(r"^page_(\d+)\.png$", re.I)
# Some screens (promos, a blank title verso) carry a bare counter with no
# section name -- parsed as the unnamed section, not treated as footer-less.
COUNTER_RE = re.compile(r"^(?P<x>\d{1,4})\s*/\s*(?P<y>\d{1,4})$")
SAME_SECTION_RATIO = 0.8

REMEDY = ("verify_capture.py: missing dependency {what} -- `pip install pillow pytesseract` "
          "and install the Tesseract binary (Windows: UB-Mannheim build), or pass --tesseract-cmd")


def fold(text: str) -> str:
    """Fold OCR dash variants to ASCII '-' and drop other non-ASCII."""
    for k, v in DASHES.items():
        text = text.replace(k, v)
    return text.encode("ascii", "ignore").decode("ascii")


def norm_section(s: str) -> str:
    """Comparison key for a section name: OCR confusions folded, spaces dropped."""
    s = s.upper().replace("|", "I").replace("L", "I").replace("1", "I")
    return re.sub(r"[^A-Z0-9]", "", s)


def same_section(a: str, b: str) -> bool:
    ka, kb = norm_section(a), norm_section(b)
    if ka == kb:
        return True
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= SAME_SECTION_RATIO


def parse_footer(text: str):
    """(section, x, y) from the LAST line of the band that parses, else None."""
    hit = None
    for line in fold(text).splitlines():
        line = line.strip()
        m = FOOTER_RE.match(line)
        if m:
            sec = m.group("section").strip(" -|")
            hit = (sec, int(m.group("x")), int(m.group("y")))
            continue
        m = COUNTER_RE.match(line)
        if m:
            hit = ("", int(m.group("x")), int(m.group("y")))
    return hit


def frames_of(run: Path):
    out = []
    for p in run.iterdir():
        m = PAGE_RE.match(p.name)
        if m and p.is_file():
            out.append((int(m.group(1)), p))
    return [p for _, p in sorted(out)]


def read_manifest(run: Path):
    mf = run / "run-manifest.json"
    if not mf.is_file():
        return None, "no run-manifest.json"
    try:
        return json.loads(mf.read_text(encoding="utf-8-sig")), None
    except (OSError, ValueError) as e:
        return None, f"run-manifest.json unreadable ({e})"


def check_manifest(run: Path, frames, flags, label):
    man, err = read_manifest(run)
    if err:
        flags.append(("MANIFEST", label, err))
        return man
    if not isinstance(man, dict):
        flags.append(("MANIFEST", label, f"run-manifest.json is not a JSON object ({type(man).__name__})"))
        return None
    nums = [int(PAGE_RE.match(p.name).group(1)) for p in frames]
    written = man.get("frames_written")
    if written != len(frames):
        flags.append(("MANIFEST", label,
                      f"frames_written={written} but {len(frames)} page_*.png files present"))
    if nums and nums != list(range(1, len(nums) + 1)):
        flags.append(("MANIFEST", label, "frame numbering is not page_001..N without holes"))
    if not frames:
        flags.append(("MANIFEST", label, "0 frames -- a failed run directory; remove or recapture"))
    if man.get("completed") is not True:
        why = man.get("abort_reason") or "no abort_reason recorded"
        flags.append(("MANIFEST", label,
                      f"completed={man.get('completed')} ({written}/{man.get('pages_requested')} frames; {why})"))
    elif written != man.get("pages_requested"):
        flags.append(("MANIFEST", label,
                      f"completed=true but frames_written={written} != pages_requested={man.get('pages_requested')}"))
    return man


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
        description="Footer-OCR geometry scanner for screen_capture.ps1 run directories "
                    "(screen map + contiguity / duplicate / junk-frame / manifest flags).")
    ap.add_argument("runs", nargs="+", metavar="RUN_DIR",
                    help="capture run directories (page_NNN.png + run-manifest.json), in capture order")
    ap.add_argument("--out-dir", required=True,
                    help="REQUIRED: where screen-map.md/.json go; never a run directory (regression R50)")
    ap.add_argument("--tesseract-cmd", default=None,
                    help="path to the tesseract binary when it is not on PATH")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    runs = [canon(r) for r in args.runs]
    for r in runs:
        if not r.is_dir():
            print(f"verify_capture.py: not a directory: {r}", file=sys.stderr)
            return 2
    out_dir = canon(args.out_dir)
    if any(out_dir == r or out_dir.is_relative_to(r) for r in runs):
        print("verify_capture.py: --out-dir may not be (or be inside) a run directory -- derived "
              "output never lands beside the frames (regression R50)", file=sys.stderr)
        return 2
    raw_root = out_dir_under_raw(args.out_dir, *args.runs)  # spelled paths: a raw/ junction keeps its name only in the unresolved spelling
    if raw_root is not None:
        print(f"verify_capture.py: --out-dir may not be anywhere under a vault raw/ tree "
              f"(resolves beneath {raw_root}) -- derived output never lands in raw/ (regression R50)",
              file=sys.stderr)
        return 2
    if out_dir.exists() and not out_dir.is_dir():
        print(f"verify_capture.py: --out-dir exists and is not a directory: {out_dir}", file=sys.stderr)
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

    flags, transitions, rows = [], [], []
    seen_hash = {}
    prev = None          # previous frame row that had a parsed footer
    prev_hash = None
    multi = len(runs) > 1
    # Label runs by folder name; fall back to the full path when two share a name.
    names = [r.name for r in runs]
    rlabels = [r.name if names.count(r.name) == 1 else r.as_posix() for r in runs]

    for run, rlabel in zip(runs, rlabels):
        frames = frames_of(run)
        man = check_manifest(run, frames, flags, rlabel)
        for f in frames:
            label = f"{rlabel}/{f.name}" if multi else f.name
            try:
                data = f.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                with Image.open(f) as im:
                    g = im.convert("L")
                    w, h = g.size
                    x0 = int(w * (0.5 - BAND_XFRAC / 2))
                    x1 = int(w * (0.5 + BAND_XFRAC / 2))
                    band = g.crop((x0, int(h * BAND_TOP), x1, h))
                    band = band.resize((band.width * UPSCALE, band.height * UPSCALE), Image.LANCZOS)
                    raw_text = pytesseract.image_to_string(band, config="--psm 6")
            except (OSError, ValueError, pytesseract.TesseractError) as e:
                # A half-written last frame is realistic (runs abort on focus loss).
                flags.append(("BADIMAGE", label, f"could not read/OCR the frame ({type(e).__name__}: {e})"))
                rows.append({"run": rlabel, "page": f.name, "sha256": None, "footer": None,
                             "section": None, "x": None, "y": None, "ocr": None})
                continue
            parsed = parse_footer(raw_text)
            row = {"run": rlabel, "page": f.name, "sha256": digest,
                   "footer": None, "section": None, "x": None, "y": None,
                   "ocr": fold(raw_text).strip()}
            if digest == prev_hash:
                flags.append(("DUPLICATE", label, "byte-identical to the previous frame"))
            elif digest in seen_hash:
                flags.append(("DUPLICATE", label, f"byte-identical to {seen_hash[digest]}"))
            seen_hash.setdefault(digest, label)
            prev_hash = digest

            if parsed is None:
                if re.search(r"\d+\s*/\s*\d+", fold(raw_text)):
                    flags.append(("UNREADABLE", label,
                                  f"footer band has a counter but did not parse: {row['ocr']!r}"))
                else:
                    flags.append(("FOOTERLESS", label,
                                  "no footer counter -- junk frame (library grid, reader chrome) or a non-ebook reader reader"))
                rows.append(row)
                continue

            sec, x, y = parsed
            row.update(footer=f"{sec or '(unnamed)'} -- {x}/{y}", section=sec, x=x, y=y)
            rows.append(row)
            if x > y or x < 1:
                flags.append(("GAP", label, f"counter {x}/{y} is out of range"))
            # The x/y counter is the continuity spine: it counts screens in the
            # reader's TOC chapter, while the name beside it is the nearest
            # heading and can change mid-chapter (Milo: "BACK INJURY ANATOMY
            # 101 -- 10/48" then "HOW TO SCREEN ... -- 11/48"). So a name change
            # is only an informational transition; flags come from the counter.
            if prev is None:
                if x != 1:
                    flags.append(("GAP", label, f"stream starts mid-chapter at {x}/{y}"))
                transitions.append((label, sec))
            else:
                px, py = prev["x"], prev["y"]
                same = same_section(prev["section"], sec)
                if not same:
                    transitions.append((label, sec))
                # A 1/1 screen repeated is a duplicate only when the names match
                # EXACTLY (after OCR folding): consecutive one-screen chapters
                # ("PART G", "PART H") are fuzzy-similar but distinct. Two bare
                # unnamed 1/1 screens (plates, promos) are distinct too; only a
                # byte-identical repeat of those is caught (above).
                exact = bool(norm_section(sec)) and norm_section(prev["section"]) == norm_section(sec)
                if (x, y) == (px, py) and (y != 1 or exact):
                    if digest != prev["sha256"]:   # byte-identical is already flagged above
                        flags.append(("DUPLICATE", label,
                                      f"same footer as {prev['_label']} ({x}/{y}) -- one screen captured twice"))
                elif x == 1 and px == py:
                    pass                           # previous chapter finished, next one starts
                elif y == py:
                    if x != px + 1:
                        flags.append(("GAP", label,
                                      f"{x}/{y} follows {px}/{py} -- screen(s) missing or out of order"))
                elif x == px + 1 and same and px != py:   # a finished chapter can't change total
                    flags.append(("Y-MISMATCH", label,
                                  f"{x}/{y} follows {px}/{py} -- chapter total changed (reflow or misread)"))
                else:
                    if px != py:
                        flags.append(("GAP", label,
                                      f"chapter changed after {prev['footer']} (chapter not finished)"))
                    if x != 1:
                        flags.append(("GAP", label, f"new chapter entered at {x}/{y}, not 1/{y}"))
            row["_label"] = label
            prev = row

        run_rows = [r for r in rows if r["run"] == rlabel]
        footed = [i for i, r in enumerate(run_rows) if r["x"] is not None]
        if frames and not footed:
            flags.append(("NOTE", rlabel, f"0 of {len(frames)} frames carry a footer counter -- "
                                          "a non-ebook reader reader (e.g. archive.org BookReader)? "
                                          "This tool cannot verify such runs"))
        # The truncated-run case (regression R49): an incomplete run can still have
        # reached the true book end. Say so instead of making a reader find out.
        if man is not None and man.get("completed") is not True and frames:
            if footed:
                last = run_rows[footed[-1]]
                trailing = len(run_rows) - footed[-1] - 1
                if last["x"] == last["y"] and trailing:
                    flags.append(("NOTE", rlabel,
                                  f"incomplete run, but the last footer {last['footer']!r} closes its chapter "
                                  f"and {trailing} footer-less frame(s) follow -- likely the book end; "
                                  "check those frames are junk before recapturing"))

    # A stream that stops mid-chapter under-captured: completed:true only proves
    # frames_written == the user-supplied -Pages, not that the chapter ended.
    if prev is not None and prev["x"] != prev["y"]:
        flags.append(("GAP", prev["_label"],
                      f"stream ends mid-chapter at {prev['footer']} -- later screens not captured"))

    for r in rows:
        r.pop("_label", None)

    real_flags = [f for f in flags if f[0] != "NOTE"]
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "tesseract": tess_ver,
        "runs": [str(r) for r in runs],
        "frames": rows,
        "flags": [{"kind": k, "where": w, "detail": d} for k, w, d in flags],
        "transitions": [{"where": w, "section": s} for w, s in transitions],
    }
    (out_dir / "screen-map.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    md = [f"# Screen map ({len(rows)} frames, {len(runs)} run(s))", "",
          f"Generated {report['generated']} by verify_capture.py (tesseract {tess_ver}); "
          "derived output, not source.", "",
          "| run | page | footer | section |", "|---|---|---|---|"]
    def cell(v):  # OCR reads roman I as '|' -- escape it so the table holds
        return (v or "").replace("|", "\\|")
    for r in rows:
        md.append(f"| {cell(r['run'])} | {r['page']} | {cell(r['footer']) or '(none)'} | {cell(r['section'])} |")
    md += ["", "## Flags", ""]
    md += [f"- **{k}** {w} -- {d}" for k, w, d in flags] or ["- none"]
    md += ["", "## Section transitions", ""]
    md += [f"- {w}: {s}" for w, s in transitions] or ["- none"]
    (out_dir / "screen-map.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"verify_capture: {len(rows)} frames in {len(runs)} run(s); "
          f"footers parsed {sum(1 for r in rows if r['x'] is not None)}; "
          f"sections {len(transitions)}; flags {len(real_flags)}")
    for k, w, d in flags:
        print(f"  {k:<10} {w}: {d}")
    print(f"screen map -> {out_dir / 'screen-map.md'} (+ screen-map.json)")
    return 1 if real_flags else 0


if __name__ == "__main__":
    sys.exit(main())
