#!/usr/bin/env python3
"""Extract every quoted span from a /watch-derived wiki page and verify it against each available reference transcript, one labelled verdict per reference.

Why not a single OK/MISS: the pages declare `transcript_kind:` /
`transcript_source:`, so WHICH reference a span matches is the finding. A span
present in the auto track but absent from Whisper is a caption-garble
candidate -- the page was faithful to what it was given and the text may still
be wrong. Binary matching collapses that into the same bucket as author drift.

Two extraction traps this is built against, both already hit in the fleet:
  - `> [!quote]` callouts are often INDENTED under a list item, so a `^>`
    anchor misses them.
  - frontmatter `description:`, the `**Citation:**` line and the H1 title
    carry quoted strings that were never speech.

This audits QUOTED SPANS ONLY. A clean run means "the words inside quotation
marks are real" -- it does NOT mean the page's unquoted figures are right. On a
page whose load-bearing content is tickers, levels and percentages, that is
half the instrument.

Which pages: every wiki/sources/ page that declares `transcript_kind:` or
`transcript_source:` in its frontmatter AND names exactly one existing
`raw/watched/<dir>`. Pinned by what the page itself says, never fuzzy-matched
(a scored matcher once paired a page with the wrong report at score 1).

References come from `gather_transcripts.py`, by default in `<vault>/.transcripts/`
(`--refs DIR` overrides). When a page's video has nothing there, the transcript
embedded in its report.md is used as the `contemp` reference (FALLBACK). When
NO page has ANY reference, the run refuses to tally: it prints
`NO REFERENCES LOADED` first and exits 2 -- an unfed verifier otherwise reports
100% MISS, which reads as a fabrication disaster (regression R75). Spans on a page
with no reference are labelled NO-REFS, never MISS.

The /watch skill -- an optional, separately installed third-party Claude Code
skill, NOT bundled with this template, expected at
~/.claude/skills/watch/scripts -- supplies the VTT parser (parse_vtt).
Absent, VTT tracks are skipped with a WARNING; contemp and whisper.json still
load.

Usage:
    python scripts/verify_quotes.py --fixture              # self-test -- run this first
    python scripts/verify_quotes.py --summary              # tallies only
    python scripts/verify_quotes.py --packet               # + one packet per page
    python scripts/verify_quotes.py --page SLUG            # one page
    python scripts/verify_quotes.py --refs DIR --summary   # refs somewhere else

Exit: 0 every page had a reference; 1 some page had none (NO-REFS spans) or
a page was SKIPPED; 2 no references loaded at all, or a bad --page.
"""
import argparse
import difflib
import html
import json
import re
import sys
from pathlib import Path

# Importing a sibling below; keep scripts/__pycache__ out of the synced vault
# (regression R83). Must stay ABOVE the import.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from gather_transcripts import (  # noqa: E402
        DEFAULT_REFS, SKILL, caption_kind, contemporaneous)
except ImportError as e:  # a half-finished sync: guard at the importer
    raise SystemExit("verify_quotes.py needs its sibling scripts/"
                     "gather_transcripts.py (%s) -- re-run "
                     "sync_from_template.py --apply" % e)

DQ = chr(34)
LQ, RQ = chr(0x201C), chr(0x201D)
EN, EM = chr(0x2013), chr(0x2014)
ELLIPSIS = chr(0x2026)

NO_REFS_BANNER = ("NO REFERENCES LOADED -- run gather_transcripts.py first; "
                  "MISS counts are meaningless")

# Must end on a non-dot: "...see raw/watched/foo." is prose punctuation, and
# Windows is_dir() would accept "foo." as a second, distinct dir.
WATCHED_RE = re.compile(r"raw/watched/([A-Za-z0-9._-]*[A-Za-z0-9_-])")
QUOTE_RE = re.compile("[" + DQ + LQ + "]([^" + DQ + LQ + RQ + "]+)["
                      + DQ + RQ + "]")
# The `~` and the en/em dash are not decoration: locators read like
# `[~04:57-05:16]` with an en dash, and a stricter pattern reported every span
# on such a page as unanchored.
TS_RE = re.compile(r"\[~?(\d{1,2}:\d{2}(?::\d{2})?)"
                   r"(?:\s*[-" + EN + EM + r"]\s*~?(\d{1,2}:\d{2}(?::\d{2})?))?\]")
CALLOUT_RE = re.compile(r"^\s*>\s*\[!quote\]", re.IGNORECASE)

NUMBERS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100",
}

_PARSE_VTT = None      # resolved lazily; False = the /watch skill is absent


def parse_vtt_or_none():
    """The /watch skill's parse_vtt, or None when the skill is not installed."""
    global _PARSE_VTT
    if _PARSE_VTT is None:
        try:
            if str(SKILL) not in sys.path:
                sys.path.insert(0, str(SKILL))
            from transcribe import parse_vtt  # noqa: E402
            _PARSE_VTT = parse_vtt
        except Exception:
            _PARSE_VTT = False
    return _PARSE_VTT or None


def norm(text):
    """Lowercase word list, punctuation dropped, spelled numbers digitised."""
    # Entities first. YouTube VTT carries `S&amp;P` and `&#39;`; a page
    # carries `S&P` and a straight apostrophe. Un-unescaped, the two sides
    # normalise differently and the span reads as absent from the very track
    # it was copied out of -- a fabricated MISS (found 2026-08-10, sample-vault-f).
    text = html.unescape(text)
    text = (text.lower()
            .replace(chr(0x2019), "'").replace(chr(0x2018), "'")
            .replace(chr(0xa0), " ").replace(chr(0x2011), "-"))
    words = re.sub(r"[^a-z0-9' ]+", " ", text).split()
    return [NUMBERS.get(w, w) for w in words]


def stitch(segments):
    """Join cue texts, dropping the rolling overlap YouTube auto-subs repeat.

    parse_vtt's _dedupe only collapses exact repeats and prefix extensions, so
    a sliding 2-line window still leaves `line2` duplicated. Without this a
    quote spanning three caption lines is never contiguous in the reference and
    reads as drift.
    """
    out = []
    for seg in segments:
        words = norm(seg["text"])
        overlap = 0
        for k in range(min(len(words), len(out), 30), 0, -1):
            if out[-k:] == words[:k]:
                overlap = k
                break
        out.extend(words[overlap:])
    return out


def stitch_lines(txt):
    # The report's transcript section is one cue per line and carries the same
    # rolling duplication as a VTT, so it is stitched the same way.
    return stitch([{"text": ln} for ln in txt.splitlines() if ln.strip()])


def load_refs(slug, refs_dir, watched, warnings):
    """({kind: word list}, fell_back) for one video.

    `contemp` falls back to the transcript embedded in raw/watched/<slug>/
    report.md when gather_transcripts.py has not written one.
    """
    d = refs_dir / slug
    refs, fallback = {}, False
    contemp = d / "contemporaneous.txt"
    if contemp.exists():
        txt = contemp.read_text(encoding="utf-8", errors="replace")
        if txt.strip():
            refs["contemp"] = stitch_lines(txt)
    if "contemp" not in refs:
        rep = watched / slug / "report.md"
        if rep.exists():
            txt = contemporaneous(rep.read_text(encoding="utf-8",
                                                errors="replace"))
            if txt.strip():
                refs["contemp"] = stitch_lines(txt)
                fallback = True
    vtts = sorted(d.glob("video*.vtt")) if d.exists() else []
    if vtts:
        parse_vtt = parse_vtt_or_none()
        if parse_vtt is None:
            warnings.add("WARNING: the /watch skill is absent (%s) -- VTT "
                         "caption tracks were SKIPPED; verdicts use contemp "
                         "and whisper only" % SKILL)
        else:
            for vtt in vtts:
                kind = caption_kind(vtt)
                try:
                    words = stitch(parse_vtt(str(vtt)))
                except Exception as e:
                    warnings.add("WARNING: %s is unreadable (%s) -- ignored"
                                 % (vtt, e.__class__.__name__))
                    continue
                if not words:
                    # An empty witness is not a witness: kept, it would turn
                    # every span into a MISS / GARBLE-CANDIDATE against it.
                    warnings.add("WARNING: %s parsed to 0 words -- ignored"
                                 % vtt)
                    continue
                prev = refs.get(kind)
                if prev is None or len(words) > len(prev):
                    refs[kind] = words
    wj = d / "whisper.json"
    if wj.exists():
        try:
            words = stitch(json.loads(wj.read_text(
                encoding="utf-8", errors="replace"))["segments"])
        except (ValueError, KeyError, TypeError) as e:
            warnings.add("WARNING: %s is unreadable (%s) -- ignored"
                         % (wj, e.__class__.__name__))
            words = []
        else:
            if not words:
                warnings.add("WARNING: %s has 0 words -- ignored" % wj)
        if words:
            refs["whisper"] = words
    return refs, fallback


def frontmatter_end(lines):
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return i + 1
    return 0


def watch_pages(vault, watched):
    """(pages, skipped): page slug -> raw/watched dir, read off each page."""
    pages, skipped = {}, []
    src = vault / "wiki" / "sources"
    if not src.exists():
        return pages, skipped
    for p in sorted(src.glob("*.md")):
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        fm = lines[:frontmatter_end(lines)]
        if not any(ln.startswith(("transcript_kind:", "transcript_source:"))
                   for ln in fm):
            continue
        named = sorted({m.group(1) for ln in lines
                        for m in WATCHED_RE.finditer(ln)
                        if (watched / m.group(1)).is_dir()})
        if len(named) == 1:
            pages[p.stem] = named[0]
        else:
            skipped.append((p.stem, named))
    return pages, skipped


def extract(page_path=None, text=None):
    """Every quoted span on the page, with the timestamp on its line."""
    if text is None:
        text = page_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    # Skip YAML frontmatter: `description:` and `title:` quote source text that
    # was never spoken.
    start = frontmatter_end(lines)

    spans = []
    carried = ""          # a callout's timestamp may live on its TITLE line
    for i in range(start, len(lines)):
        line = lines[i]
        if line.startswith("**Citation:**"):
            continue
        # A level-1 heading is a citation in heading position (typically the
        # video's own title, from YouTube's title field), not speech. Left in,
        # every page opens with a permanent NEAR-MISS or MISS that is not a
        # finding. `## ` and deeper can and do carry real quoted claims.
        if line.startswith("# "):
            continue
        ts = TS_RE.search(line)
        stamp = ts.group(0) if ts else ""
        # A `> [!quote] The mechanism [~04:57-05:16]` title carries the
        # timestamp; the quoted words are on the NEXT `>` line.
        if CALLOUT_RE.match(line):
            carried = stamp
        elif line.lstrip().startswith(">"):
            stamp = stamp or carried
        elif line.strip():
            carried = ""
        found = [m.group(1).strip() for m in QUOTE_RE.finditer(line)]
        if found:
            for q in found:
                spans.append({"text": q, "ts": stamp, "line": i + 1,
                              "callout": bool(CALLOUT_RE.match(line))})
        elif CALLOUT_RE.match(line):
            # A quote callout whose body carries no quotation marks: the whole
            # body IS the verbatim span.
            body = re.sub(r"^\s*>\s*\[!quote\][-+]?", "", line)
            body = TS_RE.sub("", body).strip()
            if len(body.split()) >= 3:
                spans.append({"text": body, "ts": stamp, "line": i + 1,
                              "callout": True})
    return spans


def find(fragment, ref):
    """(hit, ratio, window) for a normalised fragment in a normalised ref.

    Returns the best-matching window TEXT as well as the score, so an
    adjudicator can see whether a 0.87 is "wanna" vs "want to" or a different
    sentence entirely without hunting through the whole transcript.
    """
    n = len(fragment)
    if not n or not ref:
        return False, 0.0, ""
    for i in range(len(ref) - n + 1):
        if ref[i:i + n] == fragment:
            lo, hi = max(0, i - 4), min(len(ref), i + n + 4)
            return True, 1.0, " ".join(ref[lo:hi])
    best, best_i = 0.0, 0
    joined = " ".join(fragment)
    step = max(1, n // 6)
    for i in range(0, max(1, len(ref) - n + 1), step):
        window = " ".join(ref[i:i + n + 2])
        r = difflib.SequenceMatcher(None, joined, window).ratio()
        if r > best:
            best, best_i = r, i
    lo, hi = max(0, best_i - 3), min(len(ref), best_i + n + 5)
    return False, round(best, 3), " ".join(ref[lo:hi])


def check(span, refs):
    """Per-reference presence. Ellipsis means the author elided: every
    fragment must land, independently."""
    parts = [p for p in re.split(r"\.\.\.|" + ELLIPSIS, span["text"])
             if len(norm(p)) >= 2]
    if not parts:
        parts = [span["text"]]
    result = {}
    for name, ref in refs.items():
        hits, ratios, windows = [], [], []
        for p in parts:
            hit, ratio, window = find(norm(p), ref)
            hits.append(hit)
            ratios.append(ratio)
            windows.append(window)
        worst = ratios.index(min(ratios))
        result[name] = {"hit": all(hits), "ratio": min(ratios),
                        "window": windows[worst]}
    return result


def verdict(res, words=None):
    """Label by WHICH reference matched, not merely whether one did."""
    if not res:
        # Nothing to compare against is not evidence of absence.
        return "NO-REFS"

    def ok(k):
        return res.get(k, {}).get("hit", False)

    def near(k):
        return res.get(k, {}).get("ratio", 0) >= 0.90

    have_w = "whisper" in res
    if ok("manual"):
        return "VERIFIED-MANUAL"
    if ok("whisper") and (ok("auto") or ok("contemp")):
        return "VERIFIED-BOTH"
    if (ok("auto") or ok("contemp")) and have_w and not ok("whisper"):
        return "GARBLE-CANDIDATE" if not near("whisper") else "VERIFIED-NEAR"
    if ok("auto") or ok("contemp"):
        return "VERIFIED-CAPTIONS-ONLY"
    if ok("whisper"):
        return "PROVENANCE-ODD"
    if any(near(k) for k in res):
        return "NEAR-MISS"
    return "MISS"


# Synthetic self-test page (no vault page is assumed to exist). Exercises every
# extraction trap the fleet has hit: frontmatter quotes, the H1 title, the
# Citation line, an INDENTED quote callout whose title carries an en-dash stamp
# range, a callout with no quote marks, a table cell, three spans on one line.
FIXTURE_STAMP = "[~04:57" + EN + "05:16]"
FIXTURE_PAGE = "\n".join([
    "---",
    'title: "Never spoken frontmatter title"',
    'description: "never spoken description"',
    "transcript_kind: auto-captions",
    "---",
    '# Channel -- "Never spoken video title" (2026-01-01)',
    "",
    '**Citation:** Channel, YouTube -- "never spoken citation title".',
    "",
    "- A list item with an indented callout:",
    "  > [!quote] The mechanism " + FIXTURE_STAMP,
    '  > "the fatigue is not the point"',
    "",
    "> [!quote] Keep the easy days genuinely easy [12:01]",
    "",
    "| claim | quote |",
    "|---|---|",
    '| window | "Window for incremental selling may be closing" [01:02] |',
    "",
    'Locators [00:32]: "only happened two previous times", then '
    '"dated May 14th", then "May 15th".',
])
FIXTURE_MUST_FIND = [
    ("the fatigue is not the point", FIXTURE_STAMP),
    ("Keep the easy days genuinely easy", "[12:01]"),
    ("Window for incremental selling may be closing", "[01:02]"),
    ("only happened two previous times", "[00:32]"),
    ("dated May 14th", "[00:32]"),
    ("May 15th", "[00:32]"),
]
FIXTURE_MUST_SKIP = ["Never spoken frontmatter title", "never spoken description",
                     "Never spoken video title", "never spoken citation title"]


def run_fixture():
    spans = extract(text=FIXTURE_PAGE)
    for s in spans:
        print("  line %-4d %-16s %s" % (s["line"], s["ts"].encode(
            "ascii", "replace").decode(), s["text"][:70]))
    bad = []
    for text, ts in FIXTURE_MUST_FIND:
        hit = [s for s in spans if s["text"] == text]
        if not hit:
            bad.append("MISSED: %s" % text)
        elif hit[0]["ts"] != ts:
            bad.append("WRONG STAMP: %s -> %s" % (text, ascii(hit[0]["ts"])))
    for text in FIXTURE_MUST_SKIP:
        if any(text in s["text"] for s in spans):
            bad.append("NOT SKIPPED: %s" % text)
    if len(spans) != len(FIXTURE_MUST_FIND):
        bad.append("span count %d, want %d"
                   % (len(spans), len(FIXTURE_MUST_FIND)))
    print("\nextracted %d spans; %d expected"
          % (len(spans), len(FIXTURE_MUST_FIND)))
    for b in bad:
        print("  " + b)
    print("-> %s" % ("PASS" if not bad else "FAIL"))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixture", action="store_true",
                    help="self-test the span extractor on a built-in page")
    ap.add_argument("--root", type=Path, default=None,
                    help="vault root (default: the directory above scripts/)")
    ap.add_argument("--page", help="one page slug only")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--packet", action="store_true",
                    help="write one adjudication packet per page")
    ap.add_argument("--refs", type=Path, default=None,
                    help="where gather_transcripts.py put its output "
                         "(default: <vault>/%s/)" % DEFAULT_REFS)
    args = ap.parse_args(argv)
    try:  # page text / transcripts may not fit a cp1252 console
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    if args.fixture:
        return run_fixture()

    vault = (args.root or Path(__file__).resolve().parents[1]).resolve()
    watched = vault / "raw" / "watched"
    out_dir = args.refs or (vault / DEFAULT_REFS)

    pages, skipped = watch_pages(vault, watched)
    skip_lines = ["SKIPPED %s: declares a transcript but names %s"
                  % (page, ("%d raw/watched dirs (%s) -- ambiguous" % (
                      len(named), ", ".join(named))) if named
                      else "no existing raw/watched/<dir>")
                  for page, named in skipped]
    if args.page:
        if args.page not in pages:
            hit = [ln for ln, (p, _) in zip(skip_lines, skipped)
                   if p == args.page]
            print("ERROR: " + (hit[0] if hit else
                  "%s is not a /watch page here (needs transcript_kind: or "
                  "transcript_source: frontmatter and exactly one existing "
                  "raw/watched/<dir> named on the page)" % args.page))
            return 2
        pages, skip_lines = {args.page: pages[args.page]}, []
    if not pages:
        for ln in skip_lines:
            print(ln)
        print("no verifiable /watch pages in this vault%s"
              % (" -- %d SKIPPED above" % len(skip_lines) if skip_lines
                 else " -- nothing to verify"))
        return 1 if skip_lines else 0

    # Load every reference BEFORE printing anything, so a starved run says so
    # on its first line instead of under a wall of MISS rows.
    warnings = set()
    loaded = {page: load_refs(slug, out_dir, watched, warnings)
              for page, slug in pages.items()}
    starved = [p for p, (refs, _) in loaded.items() if not refs]
    fell_back = [p for p, (_, fb) in loaded.items() if fb]
    if len(starved) == len(pages):
        print(NO_REFS_BANNER)
        print("  refs dir: %s (%s)" % (out_dir, "exists" if out_dir.exists()
                                       else "does not exist"))
        print("  %d /watch page(s) found; none loaded a usable reference "
              "(gathered, or a ## Transcript section in report.md)" % len(pages))
        for ln in sorted(warnings) + skip_lines:
            print("  " + ln)
        return 2
    if starved:
        print("WARNING: %d of %d page(s) have NO references -- their spans are "
              "NO-REFS, not MISS: %s" % (len(starved), len(pages),
                                         ", ".join(starved)))
    if fell_back:
        print("NOTE: %d page(s) use the contemp FALLBACK (report.md transcript;"
              " nothing gathered in %s) -- run gather_transcripts.py for "
              "caption and whisper witnesses" % (len(fell_back), out_dir))
    for ln in sorted(warnings) + skip_lines:
        print(ln)

    rows = []
    packets = out_dir / "packets"
    if args.packet:
        packets.mkdir(parents=True, exist_ok=True)
    for page, slug in pages.items():
        path = vault / "wiki" / "sources" / (page + ".md")
        spans = extract(path)
        refs, fb = loaded[page]
        declared = ""
        plines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for ln in plines[:frontmatter_end(plines)]:
            if ln.startswith("transcript_kind:"):
                declared = ln.split(":", 1)[1].strip()
            elif ln.startswith("transcript_source:") and not declared:
                declared = ln.split(":", 1)[1].strip()
        refdesc = ", ".join("%s:%dw" % (k, len(v))
                            for k, v in sorted(refs.items())) or "NONE"
        if fb:
            refdesc += " (contemp = FALLBACK from report.md)"
        if not args.summary:
            print("\n=== %s  (%d spans; refs: %s)" % (page, len(spans), refdesc))
        # Dump each stitched reference in full: the best-matching window is
        # useless for a paraphrase, so an adjudicator needs the whole text.
        if refs:
            (out_dir / slug).mkdir(parents=True, exist_ok=True)
            for k, v in refs.items():
                (out_dir / slug / ("ref_%s.txt" % k)).write_text(
                    " ".join(v), encoding="utf-8")
        out = ["# Adjudication packet -- %s" % page, "",
               "- page: `wiki/sources/%s.md`" % page,
               "- declared transcript: %s" % (declared or "(none)"),
               "- references available: %s" % refdesc,
               "- full stitched references, searchable, one file each: `%s`"
               % str(out_dir / slug / "ref_<kind>.txt").replace("\\", "/"), ""]
        for idx, s in enumerate(spans, 1):
            res = check(s, refs)
            v = verdict(res, s["text"])
            rows.append({"page": page, "slug": slug, "line": s["line"],
                         "ts": s["ts"], "text": s["text"], "verdict": v,
                         "anchored": bool(s["ts"]),
                         "refs": {k: val["hit"] for k, val in res.items()},
                         "ratios": {k: val["ratio"] for k, val in res.items()}})
            if not args.summary:
                marks = " ".join(
                    ("%s+" % k[0]) if val["hit"] else ("%s%.2f" % (k[0], val["ratio"]))
                    for k, val in sorted(res.items()))
                print("  %-22s %-12s %-28s %s"
                      % (v, s["ts"], marks, s["text"][:64]))
            nw = len(norm(s["text"]))
            out += ["## Span %d -- line %d %s -- %d words -- machine verdict `%s`"
                    % (idx, s["line"], s["ts"] or "(NO TIMESTAMP)", nw, v), "",
                    "PAGE ASSERTS: " + DQ + s["text"] + DQ, ""]
            for k in ("manual", "auto", "contemp", "whisper"):
                if k not in res:
                    continue
                val = res[k]
                out.append("- **%s**: %s -- `%s`"
                           % (k, "EXACT" if val["hit"]
                              else "best %.2f" % val["ratio"],
                              val["window"][:400]))
            out.append("")
        if args.packet:
            (packets / (page + ".md")).write_text("\n".join(out),
                                                  encoding="utf-8")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "quote_verdicts.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8")
    tally = {}
    for r in rows:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print("\n--- %d spans across %d pages" % (len(rows), len(pages)))
    for k in sorted(tally, key=lambda x: -tally[x]):
        print("  %-24s %d" % (k, tally[k]))
    return 1 if (starved or skip_lines) else 0


def __getattr__(name):
    # Compat: an instance-owned verify_figures.py (sample-vault-f) does
    # `from verify_quotes import PAGES`, which the vault-specific copy exported
    # as a hand-pinned map. Derived now, for the vault this script sits in.
    if name == "PAGES":
        vault = Path(__file__).resolve().parents[1]
        return watch_pages(vault, vault / "raw" / "watched")[0]
    raise AttributeError(name)


if __name__ == "__main__":
    raise SystemExit(main())
