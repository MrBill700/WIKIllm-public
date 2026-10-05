#!/usr/bin/env python3
"""Regression test for verify_capture.py + ocr_sidecars.py (regression R49).

Draws synthetic ebook reader-like frames with Pillow (body line + centered footer
`SECTION -- x / y`), runs both scripts as subprocesses (so no scripts/__pycache__
appears), and pins:

  contiguous run -> exit 0 (em-dash + unspaced x/y footers parse)
  gap / y-total mismatch / duplicate frame / duplicate footer across runs /
  unfinished chapter / mid-chapter start / mid-chapter entry / footer-less frame /
  UNREADABLE footer / BADIMAGE frame / manifest mismatch / non-object manifest /
  0-frame failed run -> exit 1 + the flag; two distinct unnamed 1/1 screens -> clean
  --out-dir missing -> argparse exit 2 (both scripts); --out-dir == run dir, inside
  a run dir, or an existing file -> refused, exit 2
  missing pytesseract / missing tesseract binary -> exit 2, one-line remedy, no traceback
  ocr_sidecars writes <out>/page_NNN.txt with the provenance header, nothing beside the PNGs

Prints SKIP and exits 0 when Pillow/pytesseract/tesseract are absent (the
dependency-missing cases still run). Exit 0 = pass.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VC = HERE.parent / "verify_capture.py"
OS_ = HERE.parent / "ocr_sidecars.py"
FAILS = []


def check(cond, what):
    print(("PASS " if cond else "FAIL ") + what)
    if not cond:
        FAILS.append(what)


def run(script, *args, env=None):
    r = subprocess.run([sys.executable, "-B", str(script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=300)
    return r.returncode, r.stdout + r.stderr


def deps_ok():
    try:
        import pytesseract
        from PIL import Image  # noqa: F401
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def font(size):
    from PIL import ImageFont
    for cand in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/Library/Fonts/Arial.ttf"):
        if os.path.exists(cand):
            return ImageFont.truetype(cand, size)
    return ImageFont.load_default(size=size)


def frame(path, footer, body="The quick brown fox jumps over the lazy dog."):
    from PIL import Image, ImageDraw
    w, h = 1920, 1050
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    d.text((160, int(h * 0.90)), body, fill="black", font=font(22))
    if footer:
        f = font(20)
        tw = d.textlength(footer, font=f)
        d.text(((w - tw) / 2, int(h * 0.962)), footer, fill="black", font=f)
    im.save(path)


def make_run(root, name, footers, manifest="auto", bodies=None):
    rd = root / name
    rd.mkdir(parents=True)
    for i, ft in enumerate(footers, 1):
        if ft == "BAD":   # a half-written frame
            (rd / f"page_{i:03d}.png").write_bytes(b"not a png")
        elif ft == "COPY":
            shutil.copy(rd / f"page_{i - 1:03d}.png", rd / f"page_{i:03d}.png")
        else:
            frame(rd / f"page_{i:03d}.png", ft, (bodies or {}).get(i, f"Body text of screen {i} in {name}."))
    if manifest == "auto":
        manifest = {"pages_requested": len(footers), "frames_written": len(footers),
                    "completed": True, "abort_reason": None}
    if manifest is not None:
        # screen_capture.ps1 writes the manifest with a BOM (Out-File -Encoding utf8)
        (rd / "run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8-sig")
    return rd


def kinds(out):
    data = json.loads((out / "screen-map.json").read_text(encoding="utf-8"))
    return [(f["kind"], f["where"]) for f in data["flags"]], data


def main():
    tmp = Path(tempfile.mkdtemp(prefix="test_verify_capture_"))
    try:
        # --- dependency / usage cases: no tesseract needed ---------------------
        empty = tmp / "empty-run"
        empty.mkdir()
        for script in (VC, OS_):
            rc, out = run(script, empty)
            check(rc == 2 and "--out-dir" in out and "required" in out,
                  f"{script.name}: --out-dir missing -> argparse exit 2")
            rc, out = run(script, empty, "--out-dir", empty)
            check(rc == 2 and "may not be" in out, f"{script.name}: --out-dir == run dir refused (exit 2)")
            rc, out = run(script, empty, "--out-dir", empty / "map")
            check(rc == 2 and "may not be" in out and not (empty / "map").exists(),
                  f"{script.name}: --out-dir INSIDE the run dir refused (exit 2)")
            afile = tmp / "a-file.txt"
            afile.write_text("x")
            rc, out = run(script, empty, "--out-dir", afile)
            check(rc == 2 and "not a directory" in out and "Traceback" not in out,
                  f"{script.name}: --out-dir that is a file -> exit 2, no traceback")

        # --- raw/-wide refusal (ruled 2026-09-26): canonical path anywhere under a vault raw/
        vault = tmp / "vault"
        for d in ("scripts", "wiki", "_meta"):
            (vault / d).mkdir(parents=True)
        vrun = vault / "raw" / "book" / "ch1"
        vrun.mkdir(parents=True)
        for script in (VC, OS_):
            rc, out = run(script, vrun, "--out-dir", vault / "raw" / "book" / "ocr")
            check(rc == 2 and "under a vault raw/" in out and not (vault / "raw" / "book" / "ocr").exists(),
                  f"{script.name}: --out-dir SIBLING under raw/ (not inside the run dir) refused (exit 2)")
            rc, out = run(script, vrun, "--out-dir", vault / "raw" / "elsewhere" / "deep")
            check(rc == 2 and "under a vault raw/" in out,
                  f"{script.name}: --out-dir elsewhere under the same raw/ refused")
            rc, out = run(script, vrun, "--out-dir", vault / "wiki" / ".." / "raw" / "spelled" / ".." / "x")
            check(rc == 2 and "under a vault raw/" in out,
                  f"{script.name}: --out-dir spelled through .. into raw/ refused (canonicalized)")
            rc, out = run(script, vrun, "--out-dir", tmp / "outside" / "vault-map")
            check("under a vault raw/" not in out and "may not be" not in out and "Traceback" not in out,
                  f"{script.name}: --out-dir outside raw/ passes the guard")
            # a second vault's raw/ is refused too -- the rule keys on the out-dir's own ancestry
            other = tmp / "other-vault"
            (other / "_meta").mkdir(parents=True, exist_ok=True)
            rc, out = run(script, vrun, "--out-dir", other / "raw" / "dump")
            check(rc == 2 and "under a vault raw/" in out,
                  f"{script.name}: --out-dir under ANOTHER vault's raw/ refused")
        # junction/symlink spelling: a link outside raw/ that resolves into it
        link = tmp / "raw-link"
        made = False
        try:
            os.symlink(vault / "raw", link, target_is_directory=True)
            made = True
        except OSError:
            if os.name == "nt":
                r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(vault / "raw")],
                                   capture_output=True, text=True)
                made = r.returncode == 0
        if made:
            for script in (VC, OS_):
                rc, out = run(script, vrun, "--out-dir", link / "via-link")
                check(rc == 2 and "under a vault raw/" in out,
                      f"{script.name}: --out-dir through a junction/symlink into raw/ refused (realpath)")
        else:
            print("SKIP: could not create a symlink or junction here; link case not exercised")

        # raw/ that is ITSELF a junction/symlink to an outside tree (review finding on the raw/ path guard):
        # the canonical path has no ancestor named raw, so the guard must key on the spelled
        # ancestry too. The run dir is reached through the junction; the out-dir is spelled
        # both through it and directly at the external location.
        vault2 = tmp / "vault2"
        (vault2 / "_meta").mkdir(parents=True)
        ext = tmp / "ext-scans"
        (ext / "book" / "ch1").mkdir(parents=True)
        made2 = False
        try:
            os.symlink(ext, vault2 / "raw", target_is_directory=True)
            made2 = True
        except OSError:
            if os.name == "nt":
                r = subprocess.run(["cmd", "/c", "mklink", "/J", str(vault2 / "raw"), str(ext)],
                                   capture_output=True, text=True)
                made2 = r.returncode == 0
        if made2:
            vrun2 = vault2 / "raw" / "book" / "ch1"
            for script in (VC, OS_):
                rc, out = run(script, vrun2, "--out-dir", ext / "book" / "ocr")
                check(rc == 2 and "under a vault raw/" in out and not (ext / "book" / "ocr").exists(),
                      f"{script.name}: raw/ IS a junction; --out-dir spelled at the external target refused")
                rc, out = run(script, vrun2, "--out-dir", vault2 / "raw" / "elsewhere")
                check(rc == 2 and "under a vault raw/" in out,
                      f"{script.name}: raw/ IS a junction; --out-dir spelled through it refused")
                rc, out = run(script, ext / "book" / "ch1", "--out-dir", ext / "book" / "ocr2")
                check("under a vault raw/" not in out and "Traceback" not in out,
                      f"{script.name}: the same external tree reached WITHOUT any vault raw/ spelling is not refused")
        else:
            print("SKIP: could not create the raw/-as-junction fixture; that case not exercised")

        fake = tmp / "fakedeps"
        fake.mkdir()
        (fake / "pytesseract.py").write_text("raise ImportError('simulated: pytesseract absent')\n")
        env = dict(os.environ, PYTHONPATH=str(fake))
        for script in (VC, OS_):
            rc, out = run(script, empty, "--out-dir", tmp / "o-dep", env=env)
            check(rc == 2 and "missing dependency" in out and "Traceback" not in out,
                  f"{script.name}: missing pytesseract -> exit 2 + one-line remedy, no traceback")
            rc, out = run(script, empty, "--out-dir", tmp / "o-bin", "--tesseract-cmd", tmp / "no-such-tesseract.exe")
            check(rc == 2 and "Tesseract binary (not found" in out and "Traceback" not in out,
                  f"{script.name}: missing tesseract binary -> exit 2 + one-line remedy, no traceback")
        rc, out = run(VC, "--help")
        check(rc == 0 and "--out-dir" in out, "verify_capture.py --help works (help.py renders it)")

        if not deps_ok():
            print("SKIP: Pillow/pytesseract/tesseract not available -- OCR cases not run")
            return 1 if FAILS else 0

        EM = "\u2014"
        # --- green: contiguous, em-dash + unspaced counter ---------------------
        ok = make_run(tmp, "ok", [f"CHAPTER ONE {EM} 1 / 3", "CHAPTER ONE -- 2/3", f"CHAPTER ONE {EM} 3 / 3"])
        before = sorted(p.name for p in ok.iterdir())
        rc, out = run(VC, ok, "--out-dir", tmp / "o-ok")
        fl, data = kinds(tmp / "o-ok")
        xs = [(r["x"], r["y"]) for r in data["frames"]]
        check(rc == 0 and fl == [] and xs == [(1, 3), (2, 3), (3, 3)],
              f"contiguous run -> exit 0, no flags, counters 1..3/3 (got rc={rc} {xs} {fl})")
        check((tmp / "o-ok" / "screen-map.md").is_file(), "screen-map.md written to --out-dir")
        check(sorted(p.name for p in ok.iterdir()) == before, "verify_capture wrote nothing into the run dir")

        cases = [
            ("gap", ["PART A -- 1/4", "PART A -- 2/4", "PART A -- 4/4"], "GAP", "page_003.png"),
            ("ymis", ["PART B -- 1/3", "PART B -- 2/4", "PART B -- 3/4", "PART B -- 4/4"], "Y-MISMATCH", "page_002.png"),
            ("dup", ["PART C -- 1/2", "COPY", "PART C -- 2/2"], "DUPLICATE", "page_002.png"),
            ("ends", ["PART D -- 1/3", "PART D -- 2/3"], "GAP", "page_002.png"),
            ("unfinished", ["PART J -- 1/2", "PART K -- 1/1"], "GAP", "page_002.png"),
            ("midstart", ["PART L -- 2/2"], "GAP", "page_001.png"),
            ("midenter", ["PART M -- 1/1", "PART N -- 2/3", "PART N -- 3/3"], "GAP", "page_002.png"),
            ("unread", ["PART P -- 1/2", "SEE ITEM 2/2 BELOW"], "UNREADABLE", "page_002.png"),
            ("bad", ["PART Q -- 1/2", "BAD", "PART Q -- 2/2"], "BADIMAGE", "page_002.png"),
        ]
        for name, footers, want, where in cases:
            rd = make_run(tmp, name, footers)
            rc, out = run(VC, rd, "--out-dir", tmp / f"o-{name}")
            fl, _ = kinds(tmp / f"o-{name}")
            check(rc == 1 and (want, where) in fl, f"{name}: exit 1 + {want} at {where} (got rc={rc} {fl})")

        # footer-less junk frame after the true end, incomplete manifest -> FOOTERLESS + MANIFEST + NOTE
        rd = make_run(tmp, "junk", ["ALSO AVAILABLE -- 1/1", None],
                      manifest={"pages_requested": 5, "frames_written": 2, "completed": False,
                                "abort_reason": "focus lost at frame 3"})
        rc, out = run(VC, rd, "--out-dir", tmp / "o-junk")
        fl, _ = kinds(tmp / "o-junk")
        k = [x for x, _ in fl]
        check(rc == 1 and ("FOOTERLESS", "page_002.png") in fl and "MANIFEST" in k and "NOTE" in k,
              f"footer-less frame + incomplete run -> FOOTERLESS, MANIFEST, book-end NOTE (got {fl})")

        # two distinct bare 1/1 screens (plates, promos) are NOT duplicates
        rd = make_run(tmp, "plates", ["1 / 1", "1 / 1"])
        rc, out = run(VC, rd, "--out-dir", tmp / "o-plates")
        fl, _ = kinds(tmp / "o-plates")
        check(rc == 0 and fl == [], f"two distinct unnamed 1/1 screens -> no flags (got rc={rc} {fl})")

        # a manifest that is JSON but not an object -> MANIFEST flag, no crash
        rd = make_run(tmp, "nullman", ["PART R -- 1/1"], manifest=None)
        (rd / "run-manifest.json").write_text("null", encoding="utf-8")
        rc, out = run(VC, rd, "--out-dir", tmp / "o-nullman")
        check(rc == 1 and "not a JSON object" in out and "Traceback" not in out,
              "manifest `null` -> MANIFEST flag, no traceback")

        # manifest says 3 frames, 2 on disk
        rd = make_run(tmp, "mf", ["PART E -- 1/2", "PART E -- 2/2"],
                      manifest={"pages_requested": 3, "frames_written": 3, "completed": True})
        rc, out = run(VC, rd, "--out-dir", tmp / "o-mf")
        fl, _ = kinds(tmp / "o-mf")
        check(rc == 1 and ("MANIFEST", "mf") in fl, f"frames_written != files -> MANIFEST (got {fl})")

        # failed 0-frame run dir beside a good run
        zero = make_run(tmp, "zero", [], manifest={"pages_requested": 12, "frames_written": 0,
                                                   "completed": False, "abort_reason": "focus lost at frame 1"})
        rc, out = run(VC, zero, "--out-dir", tmp / "o-zero")
        check(rc == 1 and "0 frames" in out, "0-frame completed:false run -> MANIFEST flag")

        # boundary duplicate across two runs (a recaptured part divider), different bytes
        r1 = make_run(tmp, "run1", ["PART F -- 1/2", "PART F -- 2/2", "PART G -- 1/1"])
        r2 = make_run(tmp, "run2", ["PART G -- 1/1", "PART H -- 1/1"], bodies={1: "Recaptured divider."})
        rc, out = run(VC, r1, r2, "--out-dir", tmp / "o-x")
        fl, _ = kinds(tmp / "o-x")
        check(rc == 1 and fl == [("DUPLICATE", "run2/page_001.png")],
              f"same footer at a run boundary -> DUPLICATE across runs; distinct 1/1 'PART H' "
              f"after 'PART G' is NOT a duplicate (got {fl})")

        # --- ocr_sidecars -----------------------------------------------------
        before = sorted(p.name for p in ok.iterdir())
        rc, out = run(OS_, ok, "--out-dir", tmp / "o-ocr")
        txt = tmp / "o-ocr" / "page_001.txt"
        first = txt.read_text(encoding="utf-8").splitlines()[0] if txt.is_file() else ""
        check(rc == 0 and re.match(r"^# OCR \(tesseract [\w.]+\) of .*page_001\.png on \d{4}-\d{2}-\d{2}; "
                                   r"verification substrate, not source$", first) is not None,
              f"ocr_sidecars: page_001.txt first line is the provenance header ({first!r})")
        check(txt.is_file() and "CHAPTER ONE" in txt.read_text(encoding="utf-8"),
              "ocr_sidecars: sidecar body carries the page text")
        check(sorted(p.name for p in ok.iterdir()) == before, "ocr_sidecars wrote nothing beside the PNGs")
        rc, out = run(OS_, tmp / "bad", "--out-dir", tmp / "o-ocr-bad")
        check(rc == 1 and "BADIMAGE page_002.png" in out and "Traceback" not in out
              and (tmp / "o-ocr-bad" / "page_003.txt").is_file() and not (tmp / "o-ocr-bad" / "page_002.txt").exists(),
              "ocr_sidecars: unreadable frame -> BADIMAGE, exit 1, the other sidecars still written")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'FAILED' if FAILS else 'OK'}: {len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
