#!/usr/bin/env python3
"""Regression test: verify_quotes.py never reports a starved run as 100% MISS
(regression R75), falls back to the report.md transcript, and still classifies.

Pinned, on a throwaway vault built in a temp dir with the CURRENT
gather_transcripts.py + verify_quotes.py copied into its scripts/:
  1. NO REFERENCES: no gathered refs and no `## Transcript` in report.md ->
     exit 2, first output line is the NO REFERENCES LOADED banner, no tally.
  2. FALLBACK: no gathered refs, report.md has a transcript -> the contemp
     reference loads from report.md (NOTE ... FALLBACK line), exit 0.
  3. CLASSIFICATION on that fallback: one VERIFIED-CAPTIONS-ONLY, one
     NEAR-MISS, one MISS, each on a known span.
  4. PARTIAL: a second page whose video has no reference -> its spans are
     NO-REFS (never MISS), WARNING line, exit 1.
  5. GATHERED refs are preferred: gather_transcripts.py --no-download writes
     contemporaneous.txt, and verify_quotes then loads it without FALLBACK.
  6. Default --refs is <vault>/.transcripts/ -- nothing is written under raw/.
  7. /watch skill absent (HOME pointed at an empty dir) with a VTT present ->
     a WARNING line, not a traceback; contemp still verifies.
  8. yt-dlp absent (empty PATH) -> gather prints a WARNING, still writes contemp.
  9. An ambiguous page (two raw/watched dirs) is SKIPPED loudly, exit 1.
 10. --fixture self-test passes.
 11. Every declaring page skipped -> SKIPPED lines + exit 1, not a clean 0.
 12. "raw/watched/<dir>." in prose is the same dir, not a second one.
 13. Empty / malformed whisper.json -> WARNING, ignored (no GARBLE-CANDIDATE,
     no traceback).
 14. A starved run whose cause is the missing /watch skill says so under the
     banner.
 15. `from verify_quotes import PAGES` (sample-vault-f's instance verify_figures.py)
     still works, now derived.
RED controls: two single-guard mutants (banner guard removed; NO-REFS verdict
removed) must each FAIL exactly their own check (1, resp. 4) -- proving the
assertions can see the bug they guard against.

Run: python scripts/tests/test_verify_quotes_refs.py   (exit 0 = pass)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
BANNER = ("NO REFERENCES LOADED -- run gather_transcripts.py first; "
          "MISS counts are meaningless")

TRANSCRIPT = """## Transcript

[00:01] welcome back to the channel today we talk about easy running
[00:05] the most important thing is to keep the easy days genuinely easy
[00:09] because the fatigue is not the point of the session
[00:13] and then we will cover strides at the end

## Frames
"""

PAGE_A = """---
title: "Video A"
transcript_kind: auto-captions
---
# Coach -- "Video A title never spoken" (2026-01-01)

Source: raw/watched/vid-a/report.md -- staged at raw/watched/vid-a.

- Exact: "keep the easy days genuinely easy" [00:05]
- Near: "the most important thing is to keep the easy days genuinely easy because the fatigue is not the point of the sessions" [00:09]
- Absent: "sprint every single day until you collapse" [00:20]
"""

PAGE_B = """---
title: "Video B"
transcript_source: captions (auto)
---
Source: raw/watched/vid-b/report.md

- "a claim from a video with no transcript anywhere" [00:03]
"""

PAGE_AMBIG = """---
transcript_kind: auto-captions
---
Mentions raw/watched/vid-a and raw/watched/vid-b.

- "keep the easy days genuinely easy"
"""

PAGE_PLAIN = """---
title: "Not a watch page"
---
Cites raw/watched/vid-a but declares no transcript.

- "sprint every single day until you collapse"
"""

REPORT_HEAD = "---\nsource: https://example.invalid/watch?v=x\n---\n\n# Report\n\n"


def build(base, with_transcript=True, with_b=False, ambiguous=False):
    v = base / "vault"
    (v / "scripts").mkdir(parents=True)
    (v / "wiki" / "sources").mkdir(parents=True)
    for name in ("gather_transcripts.py", "verify_quotes.py"):
        shutil.copy2(SCRIPTS / name, v / "scripts" / name)
    a = v / "raw" / "watched" / "vid-a"
    a.mkdir(parents=True)
    (a / "report.md").write_text(
        REPORT_HEAD + (TRANSCRIPT if with_transcript else "## Frames\n"),
        encoding="utf-8")
    (v / "wiki" / "sources" / "page-a.md").write_text(PAGE_A, encoding="utf-8")
    (v / "wiki" / "sources" / "plain.md").write_text(PAGE_PLAIN, encoding="utf-8")
    if with_b or ambiguous:
        b = v / "raw" / "watched" / "vid-b"
        b.mkdir(parents=True)
        (b / "report.md").write_text(REPORT_HEAD + "## Frames\n", encoding="utf-8")
    if with_b:
        (v / "wiki" / "sources" / "page-b.md").write_text(PAGE_B, encoding="utf-8")
    if ambiguous:
        (v / "wiki" / "sources" / "ambig.md").write_text(PAGE_AMBIG,
                                                         encoding="utf-8")
    return v


def run(v, script, *args, env=None):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    # Inherited bytecode suppression would make check 6's __pycache__ assertion
    # pass without testing the in-script guard.
    e.pop("PYTHONDONTWRITEBYTECODE", None)
    e.pop("PYTHONPYCACHEPREFIX", None)
    if env:
        e.update(env)
    r = subprocess.run([sys.executable, str(v / "scripts" / script), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=e, cwd=str(v))
    return r.returncode, r.stdout + r.stderr


def verdicts(refs_dir):
    rows = json.loads((refs_dir / "quote_verdicts.json").read_text(encoding="utf-8"))
    return {(r["page"], r["text"]): r["verdict"] for r in rows}


def check_starved(base, fails, label=""):
    """Checks 1 and 4 -- the two a starved-run regression would break."""
    v = build(base / ("s1" + label), with_transcript=False)
    rc, out = run(v, "verify_quotes.py", "--refs", str(base / ("e1" + label)),
                  "--summary")
    first = out.strip().splitlines()[0] if out.strip() else ""
    if rc != 2 or first != BANNER or "MISS" in out.replace(BANNER, ""):
        fails.append("1 NO REFERENCES: rc=%s first=%r" % (rc, first))
    v = build(base / ("s4" + label), with_b=True)
    refs = base / ("e4" + label)
    rc, out = run(v, "verify_quotes.py", "--refs", str(refs), "--summary")
    got = verdicts(refs) if (refs / "quote_verdicts.json").exists() else {}
    vb = got.get(("page-b", "a claim from a video with no transcript anywhere"))
    if rc != 1 or vb != "NO-REFS" or "have NO references" not in out:
        fails.append("4 PARTIAL: rc=%s page-b verdict=%s" % (rc, vb))


def main():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)

        check_starved(base, fails)
        print("pinned 1: no refs -> exit 2 + banner on line 1, no tally")
        print("pinned 4: page without refs -> NO-REFS (not MISS), exit 1")

        # 2 + 3: fallback + classification
        v = build(base / "s2")
        refs = base / "e2"
        rc, out = run(v, "verify_quotes.py", "--refs", str(refs), "--summary")
        if rc != 0 or "FALLBACK" not in out:
            fails.append("2 FALLBACK: rc=%s out=%s" % (rc, out[-300:]))
        got = verdicts(refs)
        want = {
            ("page-a", "keep the easy days genuinely easy"): "VERIFIED-CAPTIONS-ONLY",
            ("page-a", "the most important thing is to keep the easy days genuinely easy "
             "because the fatigue is not the point of the sessions"): "NEAR-MISS",
            ("page-a", "sprint every single day until you collapse"): "MISS",
        }
        for k, w in want.items():
            if got.get(k) != w:
                fails.append("3 CLASSIFY %s: got %s want %s" % (k[1], got.get(k), w))
        if len(got) != 3:
            fails.append("3 span count %d want 3 (H1 title / plain page leaked?)"
                         % len(got))
        print("pinned 2: report.md transcript used as contemp FALLBACK, exit 0")
        print("pinned 3: VERIFIED-CAPTIONS-ONLY / NEAR-MISS / MISS on known spans")

        # 5: gathered refs preferred; 8: yt-dlp absent is loud
        v = build(base / "s5")
        refs = base / "e5"
        rc, out = run(v, "gather_transcripts.py", "--out", str(refs),
                      env={"PATH": ""})
        if rc != 0 or "yt-dlp not found" not in out:
            fails.append("8 YT-DLP ABSENT: rc=%s out=%s" % (rc, out[-300:]))
        if not (refs / "vid-a" / "contemporaneous.txt").exists():
            fails.append("5 gather did not write contemporaneous.txt")
        (v / "raw" / "watched" / "vid-a" / "report.md").write_text(
            REPORT_HEAD + "## Frames\n", encoding="utf-8")
        rc, out = run(v, "verify_quotes.py", "--refs", str(refs), "--summary")
        if rc != 0 or "FALLBACK" in out:
            fails.append("5 GATHERED: rc=%s out=%s" % (rc, out[-300:]))
        elif verdicts(refs).get(("page-a", "keep the easy days genuinely easy")) \
                != "VERIFIED-CAPTIONS-ONLY":
            fails.append("5 GATHERED: exact span not verified from gathered ref")
        print("pinned 5: gathered contemporaneous.txt loads without FALLBACK")
        print("pinned 8: yt-dlp absent -> WARNING, contemp still written")

        # 6: default refs dir; nothing under raw/
        v = build(base / "s6")
        before = sorted(str(p) for p in (v / "raw").rglob("*"))
        rc, out = run(v, "verify_quotes.py", "--summary")
        after = sorted(str(p) for p in (v / "raw").rglob("*"))
        if rc != 0 or not (v / ".transcripts" / "quote_verdicts.json").exists():
            fails.append("6 DEFAULT REFS: rc=%s, .transcripts/ not written" % rc)
        if before != after:
            fails.append("6 raw/ changed: %s" % sorted(set(after) - set(before)))
        if (v / "scripts" / "__pycache__").exists():
            fails.append("6 scripts/__pycache__ written (sibling import unguarded)")
        print("pinned 6: default refs = <vault>/.transcripts/, raw/ untouched, "
              "no __pycache__")

        # 7: /watch skill absent with a VTT present
        v = build(base / "s7")
        refs = base / "e7"
        (refs / "vid-a").mkdir(parents=True)
        (refs / "vid-a" / "video.en.vtt").write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n", encoding="utf-8")
        home = base / "emptyhome"
        home.mkdir()
        rc, out = run(v, "verify_quotes.py", "--refs", str(refs), "--summary",
                      env={"USERPROFILE": str(home), "HOME": str(home)})
        if rc != 0 or "/watch skill is absent" not in out or "Traceback" in out:
            fails.append("7 SKILL ABSENT: rc=%s out=%s" % (rc, out[-300:]))
        print("pinned 7: /watch skill absent -> WARNING, VTT skipped, no traceback")

        # 9: ambiguous page skipped
        v = build(base / "s9", ambiguous=True)
        rc, out = run(v, "verify_quotes.py", "--refs", str(base / "e9"),
                      "--summary")
        if rc != 1 or "SKIPPED ambig" not in out or "ambiguous" not in out:
            fails.append("9 AMBIGUOUS: rc=%s out=%s" % (rc, out[-300:]))
        print("pinned 9: a page naming two raw/watched dirs is SKIPPED loudly, "
              "exit 1")

        # 11: every declaring page skipped -> not a clean "nothing to verify"
        v = build(base / "s11", ambiguous=True)
        (v / "wiki" / "sources" / "page-a.md").unlink()
        rc, out = run(v, "verify_quotes.py", "--refs", str(base / "e11"),
                      "--summary")
        if rc != 1 or "SKIPPED ambig" not in out:
            fails.append("11 ALL SKIPPED: rc=%s out=%s" % (rc, out[-300:]))
        print("pinned 11: all pages skipped -> SKIPPED lines + exit 1, not 0")

        # 12: prose "raw/watched/vid-a." (PAGE_A ends a sentence on it) is the
        # same dir, not a second one -- check 3 above would go ambiguous too.
        if "raw/watched/vid-a." not in PAGE_A:
            fails.append("12 fixture lost its trailing-period sentence")
        print("pinned 12: trailing period after raw/watched/<dir> is punctuation")

        # 13: empty / malformed whisper.json is ignored loudly, never a witness
        for label, body in (("empty", '{"segments": []}'), ("bad", "{not json")):
            v = build(base / ("s13" + label), with_transcript=False)
            refs = base / ("e13" + label)
            (refs / "vid-a").mkdir(parents=True)
            (refs / "vid-a" / "contemporaneous.txt").write_text(
                TRANSCRIPT, encoding="utf-8")
            (refs / "vid-a" / "whisper.json").write_text(body, encoding="utf-8")
            rc, out = run(v, "verify_quotes.py", "--refs", str(refs), "--summary")
            vv = verdicts(refs).get(("page-a", "keep the easy days genuinely easy")) \
                if (refs / "quote_verdicts.json").exists() else None
            if rc != 0 or "Traceback" in out or "whisper.json" not in out \
                    or vv != "VERIFIED-CAPTIONS-ONLY":
                fails.append("13 WHISPER %s: rc=%s verdict=%s out=%s"
                             % (label, rc, vv, out[-300:]))
        print("pinned 13: empty/malformed whisper.json -> WARNING, ignored, "
              "no GARBLE-CANDIDATE, no traceback")

        # 14: starved + skill absent -> the banner names the real cause
        v = build(base / "s14", with_transcript=False)
        refs = base / "e14"
        (refs / "vid-a").mkdir(parents=True)
        (refs / "vid-a" / "video.en.vtt").write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n", encoding="utf-8")
        rc, out = run(v, "verify_quotes.py", "--refs", str(refs), "--summary",
                      env={"USERPROFILE": str(home), "HOME": str(home)})
        first = out.strip().splitlines()[0] if out.strip() else ""
        if rc != 2 or first != BANNER or "/watch skill is absent" not in out:
            fails.append("14 BANNER CAUSE: rc=%s out=%s" % (rc, out[-300:]))
        print("pinned 14: starved banner carries the skill-absent warning")

        # 15: compat export for instance-owned verify_figures.py
        v = build(base / "s15")
        r = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys; sys.path.insert(0, 'scripts'); "
             "from verify_quotes import PAGES; print(sorted(PAGES.items()))"],
            capture_output=True, text=True, cwd=str(v))
        if "('page-a', 'vid-a')" not in r.stdout or "plain" in r.stdout:
            fails.append("15 PAGES compat: %s %s" % (r.stdout, r.stderr[-300:]))
        print("pinned 15: `from verify_quotes import PAGES` still works (derived)")

        # 10: built-in self-test
        rc, out = run(v, "verify_quotes.py", "--fixture")
        if rc != 0 or "-> PASS" not in out:
            fails.append("10 FIXTURE: rc=%s out=%s" % (rc, out[-300:]))
        print("pinned 10: --fixture self-test passes")

        # RED controls: strip ONE guard at a time; exactly its own check must
        # fail (banner guard -> check 1; NO-REFS verdict -> check 4).
        src = (SCRIPTS / "verify_quotes.py").read_text(encoding="utf-8")
        for name, old, new, want in (
                ("banner", "if len(starved) == len(pages):", "if False:", "1 "),
                ("no-refs", "    if not res:\n", "    if False:\n", "4 ")):
            if src.count(old) != 1:
                fails.append("RED %s: mutation anchor not found" % name)
                continue
            mutant_scripts = base / ("red-" + name) / "scripts"
            mutant_scripts.mkdir(parents=True)
            shutil.copy2(SCRIPTS / "gather_transcripts.py", mutant_scripts)
            (mutant_scripts / "verify_quotes.py").write_text(
                src.replace(old, new), encoding="utf-8")
            red_fails = []
            saved = globals()["SCRIPTS"]
            globals()["SCRIPTS"] = mutant_scripts
            try:
                check_starved(base, red_fails, label="-red-" + name)
            finally:
                globals()["SCRIPTS"] = saved
            if len(red_fails) != 1 or not red_fails[0].startswith(want):
                fails.append("RED %s: mutant tripped %s, want only check %s"
                             % (name, red_fails, want.strip()))
            else:
                print("RED control (%s guard stripped): check %s fails, as it "
                      "must" % (name, want.strip()))

    if fails:
        print("\nFAIL")
        for f in fails:
            print("  " + f)
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
