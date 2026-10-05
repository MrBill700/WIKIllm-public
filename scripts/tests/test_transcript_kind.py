"""Regression test for check_stale.py section F -- transcript provenance (regression R45).

A wiki/sources/ page built from a transcript must carry `transcript_kind:` from
the closed vocabulary in _meta/fleet-conventions.md. Before R45 no script read
the field, so a transcript page written without it was invisible to every
scanner. One throwaway fixture vault pins:

  T1  a /watch page (frontmatter raw/watched/ + transcript_source) with no
      transcript_kind is MISSING, and the fix is derived via the compat map
  T2  bare legacy `captions` in transcript_source -> suggests auto-captions
      (a since-fixed /watch bug), never manual-captions
  T3  `captions (manual)` maps to manual-captions (prefix order: the manual
      row must not fall through to the bare-captions row)
  T4  a canonical transcript_kind (quoted, with a trailing # comment) passes
  T4b a column-0 block list (`transcript_kind:` then `- whisper-local`, valid
      YAML) is compliant, and a column-0 `- video` tag list triggers
  T4c an embellished label `whisper (groq, large-v3)` maps to whisper-remote
  V1  TRANSCRIPT_KINDS and every compat-map row match the synced
      _meta/fleet-conventions.md text (the code and the doc cannot drift)
  T5  transcript_kind holding a /watch label ("captions (auto)") is LEGACY
      LABEL -> migrate to auto-captions (migration, never grandfathering)
  T6  transcript_kind outside the vocabulary and outside the map is INVALID
  T7  a non-/watch page with >= 5 distinct timestamps written in backticks
      (`[05:14]`, one vault's style) is detected -- inline code is NOT
      stripped for this section
  T8  a paper page quoting 4 of another source's timestamps is NOT a
      transcript page (below TIMESTAMP_MIN)
  T9  a book page mentioning raw/watched/ only in its BODY is not flagged
  T10 a page tagged `video` with no other trigger is detected
  T11 timestamps inside a ``` fence do not count
  T12 pages outside wiki/sources/ are never scanned by section F
  T13 header counts: 11 transcript pages / 7 missing / 1 invalid / 1 legacy
  T14 --summary still prints every finding line (actionable)
  T15 exit code 0 with findings present (advisory contract)
  P1  maintenance_preflight.parse_stale reads the F counts when present
  P2  ... and output WITHOUT section F still parses with err=None and no F
      keys (the key is optional; an older check_stale must never block)

Run:  python scripts/tests/test_transcript_kind.py [--scripts-from DIR]
RED control: point --scripts-from at a directory holding the pre-R45
check_stale.py (and maintenance_preflight.py); the positive assertions go
red (T4/T8/T9/T11 are negative and pass there -- T13's exact count pins them).

Exit 0 = all assertions pass.  Exit 1 = at least one red.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

DEFAULT_SCRIPTS = Path(__file__).resolve().parents[1]
FENCE = "```"


def page(fm: str, body: str) -> str:
    return f"---\n{fm.strip()}\n---\n\n{body}\n"


def stamps(n: int, fmt: str = "[{:02d}:{:02d}]") -> str:
    return "\n".join(f"- {fmt.format(i, 10 + i)} \"words at that point\"" for i in range(n))


PAGES = {
    # an empty ledger, so section A prints its counts and parse_stale can run
    "_meta/open-loops.md": "# Open loops\n",
    # T1 -- /watch page, no transcript_kind, label in the compat map
    "wiki/sources/watch-auto.md": page(
        'title: Watch auto\nsource_file: raw/watched/foo-2026-07-01/report.md\n'
        'transcript_source: "captions (auto)"\ntags: [source]',
        "# Watch auto\n\n## Locators\n" + stamps(2)),
    # T2 -- bare legacy captions label
    "wiki/sources/watch-bare.md": page(
        "title: Watch bare\nsource_file: raw/watched/bar/report.md\ntranscript_source: captions",
        "# Watch bare"),
    # T3 -- manual captions label
    "wiki/sources/watch-manual.md": page(
        "title: Watch manual\nsource_file: raw/watched/baz/report.md\n"
        "transcript_source: 'captions (manual) en'",
        "# Watch manual"),
    # T4 -- compliant: quoted canonical value with a trailing comment
    "wiki/sources/watch-ok.md": page(
        'title: Watch ok\r\nsource_file: raw/watched/ok/report.md\r\n'
        'transcript_kind: "whisper-local"   # local run\r\n'
        'transcript_source: "whisper (local)"',
        "# Watch ok"),
    # T4b -- column-0 block lists (valid YAML): compliant kind; tag triggers
    "wiki/sources/col0-kind.md": page(
        "title: Col0 kind\nsource_file: raw/watched/c0/report.md\ntranscript_kind:\n- whisper-local",
        "# Col0 kind"),
    "wiki/sources/col0-tag.md": page(
        "title: Col0 tag\nsource_file: raw/talk.txt\ntags:\n- source\n- video",
        "# Col0 tag"),
    # T4c -- embellished remote-whisper label
    "wiki/sources/groq.md": page(
        'title: Groq\nsource_file: raw/watched/g/report.md\ntranscript_source: "whisper (groq, large-v3)"',
        "# Groq"),
    # T5 -- legacy label used as the kind
    "wiki/sources/legacy-kind.md": page(
        'title: Legacy kind\ntranscript_kind: "captions (auto)"',
        "# Legacy kind"),
    # T6 -- invalid kind
    "wiki/sources/invalid-kind.md": page(
        "title: Invalid kind\ntranscript_kind: youtube-transcript",
        "# Invalid kind"),
    # T7 -- non-/watch podcast page, locators written in backticks
    "wiki/sources/farm-talk.md": page(
        "title: Farm talk\nsource_file: raw/farm talk.txt",
        "# Farm talk\n\n" + "\n".join(f"- point `[0{i}:1{i}]` said" for i in range(6))),
    # T8 -- paper quoting another source's timestamps (4 < TIMESTAMP_MIN)
    "wiki/sources/paper.md": page(
        "title: Paper\nsource_file: raw/paper.pdf\ntags: [source, paper]",
        "# Paper\n\nThe video cites this at [15:47], [22:56], [24:29] and [24:44]."),
    # T9 -- book page mentioning raw/watched only in the body
    "wiki/sources/book.md": page(
        "title: Book\nsource_file: raw/book.pdf\ntags: [source, book]",
        "# Book\n\n- `raw/watched/plug-2026-07-13/report.md` -- how the book entered the vault"),
    # T10 -- tagged video, nothing else
    "wiki/sources/tagged-video.md": page(
        "title: Tagged video\nsource_file: raw/some transcript.txt\ntags:\n  - source\n  - video",
        "# Tagged video"),
    # T11 -- timestamps only inside a fence
    "wiki/sources/fenced.md": page(
        "title: Fenced\nsource_file: raw/fenced.pdf",
        f"# Fenced\n\n{FENCE}\n{stamps(8)}\n{FENCE}"),
    # T12 -- outside wiki/sources/: never scanned by F
    "wiki/concepts/concept-with-stamps.md": page(
        "title: Concept\ntranscript_kind: nonsense\ntags: [video]",
        "# Concept\n\n" + stamps(9)),
}

HEADER_RE = re.compile(
    r"^=== F\.[^\n]*=== (\d+) transcript pages / (\d+) missing / (\d+) invalid / (\d+) legacy", re.M)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")


def finding(out: str, rel: str) -> str:
    for line in out.splitlines():
        if line.strip().startswith(rel + " "):
            return line.strip()
    return ""


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([sys.executable, "-B", str(root / "scripts" / "check_stale.py"), *args],
                          capture_output=True, cwd=str(root), env=env)
    proc.stdout = proc.stdout.decode("utf-8", "replace")
    proc.stderr = proc.stderr.decode("utf-8", "replace")
    return proc


def build(root: Path, scripts_from: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(scripts_from / "check_stale.py", root / "scripts" / "check_stale.py")
    for rel, text in PAGES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripts-from", default=str(DEFAULT_SCRIPTS))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    scripts_from = Path(args.scripts_from)
    tmp = Path(tempfile.mkdtemp(prefix="wikillm-transcript-kind-"))
    root = tmp / "vault"
    try:
        build(root, scripts_from)
        print(f"fixture: {root}\nscript under test: {scripts_from}\n")
        proc = run(root)
        out = proc.stdout
        print(out[out.find("=== F."):] if "=== F." in out else "(no section F in output)")
        print("---- assertions ----")

        f = finding(out, "wiki/sources/watch-auto.md")
        check("T1 /watch page without transcript_kind is MISSING, fix from the compat map",
              "MISSING transcript_kind" in f and "transcript_kind: auto-captions" in f, f or "ABSENT")
        f = finding(out, "wiki/sources/watch-bare.md")
        check("T2 bare legacy `captions` -> auto-captions (ADR-0005), never manual",
              "transcript_kind: auto-captions" in f and "manual" not in f, f or "ABSENT")
        f = finding(out, "wiki/sources/watch-manual.md")
        check("T3 `captions (manual)` -> manual-captions (prefix order)",
              "transcript_kind: manual-captions" in f, f or "ABSENT")
        check("T4 canonical value (quoted, # comment) is compliant",
              not finding(out, "wiki/sources/watch-ok.md"), finding(out, "wiki/sources/watch-ok.md") or "no finding")
        f = finding(out, "wiki/sources/col0-tag.md")
        check("T4b column-0 block lists: kind list compliant, `- video` tag triggers",
              not finding(out, "wiki/sources/col0-kind.md") and "tag video" in f
              and "=== F." in out,
              f"col0-kind: {finding(out, 'wiki/sources/col0-kind.md') or 'no finding'}; col0-tag: {f or 'ABSENT'}")
        f = finding(out, "wiki/sources/groq.md")
        check("T4c embellished `whisper (groq, large-v3)` -> whisper-remote",
              "transcript_kind: whisper-remote" in f, f or "ABSENT")
        f = finding(out, "wiki/sources/legacy-kind.md")
        check("T5 /watch label used as the kind -> LEGACY LABEL -> migrate to auto-captions",
              "LEGACY LABEL" in f and "migrate to auto-captions" in f, f or "ABSENT")
        f = finding(out, "wiki/sources/invalid-kind.md")
        check("T6 value outside vocabulary and map -> INVALID",
              "INVALID transcript_kind" in f and "youtube-transcript" in f, f or "ABSENT")
        f = finding(out, "wiki/sources/farm-talk.md")
        check("T7 backtick-wrapped timestamps (>= 5) detect a non-/watch transcript page",
              "MISSING transcript_kind" in f and "6 timestamps" in f, f or "ABSENT")
        check("T8 a paper quoting 4 foreign timestamps is not a transcript page",
              not finding(out, "wiki/sources/paper.md"), finding(out, "wiki/sources/paper.md") or "no finding")
        check("T9 raw/watched/ mentioned only in the BODY is not a trigger",
              not finding(out, "wiki/sources/book.md"), finding(out, "wiki/sources/book.md") or "no finding")
        f = finding(out, "wiki/sources/tagged-video.md")
        check("T10 block-list tag `video` alone detects the page",
              "MISSING transcript_kind" in f and "tag video" in f, f or "ABSENT")
        check("T11 fenced timestamps do not count",
              not finding(out, "wiki/sources/fenced.md"), finding(out, "wiki/sources/fenced.md") or "no finding")
        check("T12 pages outside wiki/sources/ are not scanned by F",
              "concept-with-stamps" not in out[out.find("=== F."):] if "=== F." in out else False,
              "wiki/concepts/concept-with-stamps.md absent from section F")
        m = HEADER_RE.search(out)
        check("T13 header: 11 transcript pages / 7 missing / 1 invalid / 1 legacy",
              bool(m) and m.groups() == ("11", "7", "1", "1"), m.group(0) if m else "ABSENT")
        summ = run(root, "--summary").stdout
        check("T14 --summary keeps every finding line",
              all(finding(summ, r) for r in ("wiki/sources/watch-auto.md", "wiki/sources/legacy-kind.md",
                                              "wiki/sources/invalid-kind.md", "wiki/sources/farm-talk.md")),
              "MISSING / LEGACY / INVALID lines present under --summary")
        check("T15 exit code 0 with findings present", proc.returncode == 0, f"exit={proc.returncode}")

        sys.path.insert(0, str(scripts_from))
        try:
            import check_stale  # noqa: E402
            conv = (DEFAULT_SCRIPTS.parent / "_meta" / "fleet-conventions.md").read_text(encoding="utf-8")
            vm = re.search(r"^transcript_kind:\s*(.+)$", conv, re.M)
            doc_kinds = tuple(k.strip() for k in vm[1].split("|")) if vm else ()
            code_kinds = getattr(check_stale, "TRANSCRIPT_KINDS", None)
            compat = getattr(check_stale, "TRANSCRIPT_COMPAT", ())
            # every doc compat row's label(s) must map to its canonical kind in code
            rows = re.findall(r"^\| (`[^|]+) \| `([a-z-]+)` \|$", conv, re.M)
            row_ok = bool(rows) and all(
                check_stale.legacy_kind(lab.replace("<model>", "large-v3")) == kind
                for cell, kind in rows
                for lab in re.findall(r"`([^`]+)`", cell)
            ) if code_kinds else False
            check("V1 code vocabulary + compat map match _meta/fleet-conventions.md",
                  code_kinds is not None and set(code_kinds) == set(doc_kinds) and row_ok
                  and bool(compat),
                  f"doc kinds={doc_kinds} code={code_kinds} compat rows checked={len(rows)}")
            import maintenance_preflight  # noqa: E402
            counts, err = maintenance_preflight.parse_stale(out)
            check("P1 parse_stale reads the section F counts",
                  err is None and counts.get("transcript_pages") == 11
                  and counts.get("transcript_kind_missing") == 7
                  and counts.get("transcript_kind_invalid") == 1
                  and counts.get("transcript_kind_legacy") == 1,
                  f"err={err} F={[counts.get(k) for k in ('transcript_pages', 'transcript_kind_missing', 'transcript_kind_invalid', 'transcript_kind_legacy')]}")
            no_f = out[:out.find("=== F.")] if "=== F." in out else out
            counts2, err2 = maintenance_preflight.parse_stale(no_f)
            check("P2 output without section F parses, err None, no F keys (optional)",
                  err2 is None and not any(k.startswith("transcript") for k in counts2),
                  f"err={err2} keys={sorted(k for k in counts2 if k.startswith('transcript'))}")
        finally:
            sys.path.pop(0)

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
