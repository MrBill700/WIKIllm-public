#!/usr/bin/env python3
"""
suggest_anchors.py -- anchor-backfill assistant (the mechanical half of raising
locator coverage)

Companion to audit_claims.py, which reports locator coverage against a target
(default 60%) and empirically finds that citation drift clusters on claims with
NO #heading anchor. Raising coverage is the cheapest defence against drift --
but anchoring hundreds of claims by hand means re-reading every sources page,
which is exactly the cost this stack exists to avoid. This script does the
reading mechanically and leaves only genuine judgment to a session.

For every UNANCHORED source-citing claim line (the same population
audit_claims.py samples -- its extractor is imported, so coverage moves 1:1
with this tool's work), it parses the cited sources/ page into sections and
scores each section against the claim line:

  - exact figure matches dominate: normalized figures -- dollar amounts,
    percentages, and unit-bearing or decimal numbers ("$2,500/yr", "16.8%",
    "5.28 h", "HRV 49 ms", "RHR 47") -- appearing in both the claim and
    exactly ONE section are the strongest possible signal;
  - rare-term overlap breaks ties: a content word that appears in only one
    section is nearly as good; a word appearing everywhere is worth ~nothing;
  - VERBATIM SECTIONS WIN TIES. A section whose body is mostly the source
    talking -- a `> [!quote]` block, a "Locators" section, the page-and-line
    evidence layer CLAUDE.md's locator rule asks for -- is multiplied by
    VERBATIM_BONUS. Backtesting found the scorer's largest error class was
    preferring the WIKI-VOICE synthesis section ("Bearing on positions", "Key
    takeaways") whose phrasing the claim was written FROM, over the quote the
    claim can actually be verified against. Dominance is the test, not
    presence: a synthesis section that quotes a line or two is still wiki
    voice.

Three corrections learned from field runs, each of which was a whole class of
bad picks:

  - GENERIC HEADINGS ARE NOT LOCATORS. "Links", "Citations", "Status", "See
    also", "Wiki edits prompted by this source", a heading that merely
    restates the page title ("Source: <title>") -- an anchor pointing there
    tells a future session nothing it did not already know from the bare link,
    and the bookkeeping ones describe what the WIKI did, not what the SOURCE
    says. Those sections are excluded from scoring outright (they remain valid
    for --check, since pre-existing anchors may already point at them). The
    link-inventory half of the test IS audit_claims.SKIP_HEADINGS, imported,
    so the two scripts cannot drift apart.
  - ONE LINE, SEVERAL CITATIONS, ONE WINNER. A line citing three sources used
    to score its FULL text against each of them, so all three anchors
    converged on whichever section happened to match the *other* citations'
    text. When scoring for source X, every wikilink span that is not
    sources/X is masked out first -- its display text included. Surrounding
    prose is kept.
  - THE PAGE IS PARSED THE WAY OBSIDIAN READS IT. Headings inside fenced code
    blocks are not headings (a ```markdown block quoting a template would
    otherwise donate sections that do not exist, and --check, reading the same
    structure, would validate an anchor Obsidian cannot resolve). H1 counts as
    a real heading for --check, but only wins anchors on pages that have no
    deeper headings -- elsewhere the lone H1 is the restated page title.

Each claim lands in a tier:

  AUTO        a UNIQUE figure match, above AUTO_SCORE_MIN, beating the
              runner-up by AUTO_MARGIN -- safe to apply mechanically
              (--apply-auto). The bar is deliberately high because a WRONG
              anchor is worse than no anchor: it manufactures false confidence
              at the exact spot the audit trusts most.
  HIGH_REVIEW no unique figure, but a VERBATIM section wins decisively
              (HIGH_SCORE_MIN / HIGH_MARGIN). The anchor field is pre-filled,
              so a session can skim these as a batch and apply the lot with
              --apply-all instead of retyping headings.
  REVIEW      plausible candidates exist but the choice needs judgment. The
              suggestions file carries the top candidates per claim, grouped so
              a session (or a fleet of cheap subagents, one per sources page)
              can decide in batches and feed decisions back via --apply.
  NOMATCH     no section scored. These are interesting: the support for the
              claim may not exist on the sources page at all -- treat as drift
              candidates for the next claim audit, do NOT force an anchor. The
              scan separates the STRUCTURAL CEILING out of this bucket: claims
              citing a page with no anchorable section at all (no headings, or
              only bookkeeping ones) can never be anchored by any amount of
              judgment; the fix is to give that sources page substantive
              sections, not to re-read the claim.

Both mechanical tiers were tuned on a 341-claim backtest against hand-verified
anchors (sample-vault-c vault, 2026-07-29): AUTO 12/12 and HIGH_REVIEW 32/32
correct, at the cost of covering 13% of the population mechanically. Precision
is bought with the margin rule, not the score floor: the residual errors are
confident picks of a plausible-but-wrong section, so thresholds alone never
got past ~0.73.

Headings containing link-breaking characters ([ ] | # ^ ` and the backslash)
cannot be linked. PAIRED asterisk emphasis is NOT link-breaking: it is stripped
by anchor_text() before the anchor is written and Obsidian resolves the
stripped form, so `## What's *particularly* useful ...` is anchorable. A lone
unpaired asterisk (E*Trade) is literal text and is preserved on both sides.
Rather than silently disqualifying the winner, the anchor FALLS BACK to
the nearest linkable ancestor heading -- a coarser locator, but a real one,
marked "fallback": true so the choice stays auditable. A fallback anchor points
at the winner's PARENT, so the evidence that won the scoring is not in the
linked section: fallbacks are therefore REVIEW-only, never AUTO/HIGH_REVIEW,
and a human accepts them (via --apply) or does better.

Anchor VALIDATION is built in (--check) because lint.py deliberately strips
anchors before resolving links: it verifies every existing [[sources/...#...]]
anchor resolves to a real heading on the target page. Matching runs in TWO
TIERS -- strict_heading() (paired emphasis stripped, whitespace collapsed,
casefolded: Obsidian's rule as far as observed) and the lenient norm_heading()
(all punctuation folded to spaces). A strict match is clean; a match needing
only the lenient tier is reported SUSPECT and counted separately, never as
broken, because leniency is a superset that hides real breaks -- an anchor
spelling a heading's em-dash as "--" passed the old single-tier check and still
landed at top-of-page (2026-08-08). Block anchors (#^id) are validated against
the target page's actual block ids. It runs automatically after any apply, so a
bad write is caught in the same breath it happens.

Modes (mutually exclusive; default is a scan):
  (scan)            score everything, write _meta/anchor-suggestions.json,
                    print tier counts + the per-source breakdown
  --summary         scan, but print counts only
  --apply-auto      apply the AUTO tier from a fresh scan, then --check
  --apply-all       apply AUTO + HIGH_REVIEW from a fresh scan, then --check
  --apply FILE      apply a decisions file (same JSON shape; every entry with
                    a non-empty "anchor" is applied), then --check
  --check           validate existing anchors only

The scan-driven apply modes write _meta/anchor-suggestions.json BEFORE editing,
so the record of what was chosen (including which choices were fallbacks)
survives the run that made the edits; --apply FILE is already driven by such a
record. Fallback counts are printed by every mode that writes one.

They also write _meta/anchor-auto-applied-latest.json AFTER editing: the pages
and lines that actually took an anchor. The suggestions file cannot serve that
purpose, because the next scan lists only UNANCHORED claims and drops every
entry that just landed -- so a caller (the anchor-backfill workflow re-scans
one command later) had no way to tell which pages the run touched, regression R94.
It is rewritten unconditionally, entries: [] included, so it is never stale.

--root PATH points every path at another vault, so a TEMPLATE or WORKTREE copy
can be run against real content instead of silently scanning the template's
empty scaffold (regression R67). The apply modes WRITE at --root: scan first.

Applying edits citing pages IN PLACE, one line at a time, and refuses any line
whose recorded text no longer matches (the file moved on; the lineno now points
at a different claim, and if that claim cites the same source the anchor would
be silently mispaired -- an error --check cannot see, because only the PAIRING
is wrong). Line counts never change, so line numbers stay valid across a whole
batch. Reads and writes are byte-transparent (newline=""), so a one-line edit
does not rewrite an LF-authored page as CRLF. Table rows keep their
escaped-pipe convention intact.

Advisory posture, stdlib only, exit 0 unless --check finds broken anchors
(exit 1 so a close-out step can gate on it).
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

# Vaults are cloud-synced folders: the sibling import below must not leave
# scripts/__pycache__/*.pyc behind for Dropbox to push to every device
# (regression R83). Set before the import, or it is a no-op.
sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_claims as ac  # noqa: E402
from audit_claims import (  # noqa: E402
    SKIP_HEADINGS, is_source_target, iter_claim_lines, slug_of,
)
try:
    from audit_claims import record_coverage  # noqa: E402
except ImportError:      # older audit_claims on a stale instance
    record_coverage = None

# The PATH-valued names stay on the audit_claims MODULE and are read as
# ac.ROOT / ac.SOURCES at CALL time. A from-import snapshots the value at
# import time, so --root (audit_claims.set_root, regression R67) would move
# audit_claims' paths and leave this script's pointing at its own repo --
# scanning one vault while writing _meta/ into another. Functions and regexes
# are safe to from-import: they read those globals when called.
META = ac.ROOT / "_meta"
SUGGESTIONS_FILE = META / "anchor-suggestions.json"
# regression R94: which pages the mechanical apply modes actually anchored --
# see write_auto_applied().
AUTO_APPLIED_FILE = META / "anchor-auto-applied-latest.json"


def set_root(path):
    """--root: move audit_claims' paths, then re-derive the ones owned here.

    The AttributeError guard is the same discipline as the record_coverage
    import above, and the reason sync_from_template.py's SYNC_SET comment
    gives: cross-script imports are guarded at the IMPORTER, not by sync
    ordering. A vault carrying this file beside a pre-regression R67
    audit_claims.py would otherwise die on a raw traceback; main() turns the
    ValueError into the same exit-2 message a rootless directory gets."""
    global META, SUGGESTIONS_FILE, AUTO_APPLIED_FILE
    try:
        root = ac.set_root(path)
    except AttributeError:
        raise ValueError(
            "--root needs a scripts/audit_claims.py from regression R67 or later; "
            "this vault's copy is older -- run python scripts/sync_from_template.py --apply")
    META = root / "_meta"
    SUGGESTIONS_FILE = META / "anchor-suggestions.json"
    AUTO_APPLIED_FILE = META / "anchor-auto-applied-latest.json"
    return root

# --------------------------------------------------------------- tunables ---
# The two mechanical bars. Deliberately separate module constants: they are the
# numbers a precision backtest re-tunes, and nothing else about the tiering
# should have to move when they do. Raise either to trade recall for precision.
#
# Set from a 341-claim backtest against hand-verified anchors (the sample-vault-c
# vault, 2026-07-29): AUTO 12/12 correct, HIGH_REVIEW 32/32. Both tiers demand a
# clean margin over the runner-up as well as a score floor, because the residual
# errors are not low-confidence noise -- they are confident picks of a
# plausible-but-wrong section, and only the margin separates those.
AUTO_SCORE_MIN = 25.0   # AUTO also requires a UNIQUE figure match (see tier_claim)
AUTO_MARGIN = 1.5
HIGH_SCORE_MIN = 28.0   # HIGH_REVIEW also requires a VERBATIM winner (see below)
HIGH_MARGIN = 1.75      # top must beat the runner-up by this factor (or runner-up == 0)

# The verbatim-evidence preference. CLAUDE.md defines a locator as "a verbatim
# quote (> [!quote]) and/or a chapter/page/heading reference", so a section
# built out of the SOURCE'S OWN WORDS is a better anchor than a section of
# wiki-voice prose that happens to reuse the claim's vocabulary. Without this,
# the scorer's single largest error class was picking the synthesis section
# ("Bearing on positions", "Key takeaways") whose phrasing the claim was
# written FROM, over the quote block the claim is actually verifiable against.
#
# DOMINANCE, not presence, is the test: a synthesis section that quotes a line
# or two still reads as wiki voice, and in the 341-claim backtest those were
# exactly the confident wrong picks. A section counts as verbatim when its body
# is MOSTLY the source talking (or when it is explicitly the page's locator
# section).
VERBATIM_BONUS = 1.6            # score multiplier for a matching verbatim section
VERBATIM_MIN_QUOTE_LINES = 3    # floor, so a two-line aside does not qualify
VERBATIM_MIN_QUOTE_SHARE = 0.4  # share of the section's non-blank body lines

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE = re.compile(r"^\s*(?:```|~~~)")
# An Obsidian block-id target: end-of-line " ^id" (or ^id alone on a line),
# id = letters/digits/dashes. The leading whitespace requirement is real --
# "foo^2" in prose is not a block id.
BLOCK_ID = re.compile(r"(?:^|\s)\^([A-Za-z0-9-]+)\s*$")
DOLLAR = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s?([KkMm])?")
PCT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
# A bare number, optionally unit-suffixed, that is not part of a date/time/range
# and not already claimed by DOLLAR/PCT. The lookarounds do that work: the
# lookbehind rejects a digit run reached through $ . , : / or -, and the two
# lookaheads stop the engine from backtracking to a PREFIX of a longer number
# ("16" out of "16.8%") or from double-counting a percentage.
NUMBER = re.compile(
    r"(?<![\w$.,:/-])(\d[\d,]*(?:\.\d+)?)(?![\d.])(?!\s?%)\s?([A-Za-z][A-Za-z/]{0,5})?")
WORD = re.compile(r"[a-z][a-z\-']{4,}")
LOCATOR_HEADING = re.compile(r"(?i)^(locators?|verbatim|quotes?)\b")
# Characters that break a [[page#anchor]] link. The backslash is in the set
# because check_anchors' extractor stops at one (it has to: `\|` is the
# in-table pipe escape), so an anchor containing one can never validate --
# writing it would mean writing a value this script's own --check rejects.
#
# `*` is deliberately NOT here: asterisk emphasis is stripped by anchor_text()
# before an anchor is written, so it never reaches the link, and Obsidian
# resolves the stripped form (confirmed 2026-08-09). Judge linkability on
# anchor_text(heading), not on the raw heading. The backtick stays, since
# whether Obsidian strips code spans in heading matching is untested.
UNLINKABLE = re.compile(r"[\[\]|#^`\\]")
LINK_SPAN = re.compile(r"\[\[[^\]]*\]\]")

# Units that make a bare number a figure rather than a counter ("49 ms",
# "5.28 h"). Device/clinical/training vocabulary, deliberately broad: a false
# positive only adds a weak term, a false negative loses the load-bearing
# number entirely (which is how the AUTO tier's one field miss happened).
UNITS = frozenset(
    "h hr hrs hour hours min mins minute minutes s sec secs ms bpm kg kgs lb "
    "lbs g mg mcg km mi m cm mm kcal cal mmhg spm rpm w watts wk wks week "
    "weeks d day days yr yrs year years x fold rm met mets ml l oz ft ppm "
    "reps sets steps floors".split()
)

# Headings that add no locator specificity: anchoring at one says nothing the
# bare [[sources/x]] link did not already say. Excluded from scoring (but NOT
# from headings_norm -- --check must still validate anchors already pointing
# at them) and ineligible as fallback parents.
#
# The link-inventory half of this test is DELEGATED to audit_claims.SKIP_HEADINGS
# (see also / related / sources / inputs / history / wiki edits prompted): that
# script already refuses to treat lines under those headings as claims, and two
# hand-maintained lists would drift. What is listed here is the rest -- the
# house-bookkeeping sections the fleet's sources pages actually grow, which
# describe what the WIKI did rather than what the SOURCE says.
GENERIC_HEADINGS = frozenset({
    "links", "citation", "citations", "status", "source", "sources",
    "connections", "connections into the wiki", "cross references",
    "source quality notes", "provenance", "state after ingest",
    "pages updated by this ingest", "further reading", "see also", "related",
})
GENERIC_PREFIX = "source:"   # "Source: <page title restated>"

STOP = frozenset(
    "about above added after again along among annual around because before "
    "being below between claim claims could every field first found later "
    "might other pages roughly second should since source sources their there "
    "these things those three through under until updated verified where which "
    "while whose within would years".split()
)


def _fmt(val: float) -> str:
    return f"{val:.0f}" if val == int(val) else f"{val}"


def norm_figures(text: str) -> set:
    """Normalized figure tokens.

    '$2,500/yr' -> 'd2500' (suffixed forms expand: '$1.5M' -> 'd1500000');
    '16.8%' -> 'p16.8'; and -- because most wikis in this fleet quantify in
    something other than money -- bare numbers too: '5.28 h' / 'HRV 49 ms' /
    'RHR 47' -> 'n5.28' / 'n49' / 'n47'.

    The bare-number class is deliberately filtered. A number counts only if it
    carries a decimal point, or a unit from UNITS, or is >= 10 and not a
    calendar year: below that it is list furniture ("3 sets", "1 |") and 4-digit
    years are citation furniture. Date and time components (2026-07-20, 08:28)
    are excluded by the regex lookbehind, so a date contributes nothing."""
    out = set()
    for m in DOLLAR.finditer(text):
        num = m.group(1).replace(",", "")
        try:
            val = float(num)
        except ValueError:
            continue
        if m.group(2):
            val *= 1000 if m.group(2).lower() == "k" else 1_000_000
        out.add("d" + _fmt(val))
    for m in PCT.finditer(text):
        try:    # numeric, NOT rstrip("0") -- that collapsed 10% and 100% onto 1%
            out.add("p" + _fmt(float(m.group(1))))
        except ValueError:
            continue
    for m in NUMBER.finditer(text):
        raw = m.group(1).replace(",", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        unit = (m.group(2) or "").lower().rstrip("/")
        if not ("." in raw or unit in UNITS
                or (val >= 10 and not (1900 <= val <= 2099 and val == int(val)))):
            continue
        out.add("n" + _fmt(val))
    return out


def norm_heading(h: str) -> str:
    """Obsidian-style skeleton: heading links written by Obsidian drop
    punctuation (colons, periods, apostrophes) and markdown emphasis, so a
    strict string compare flags dozens of anchors that resolve fine in the
    app. Compare on casefolded alphanumerics + spaces only; em/en dashes and
    hyphens count as spaces. Word-level drift still mismatches."""
    h = re.sub(r"[^\w\s]", " ", h, flags=re.UNICODE)   # punctuation -> space ("Bernstein's" -> "bernstein s")
    return re.sub(r"\s+", " ", h).strip().casefold()


# PAIRED asterisk emphasis, which Obsidian strips from a heading before
# matching an anchor against it -- so a link omitting the markers still
# resolves. Empirically confirmed 2026-08-09: a citation into
# `## What's *particularly* useful for the sample project`, written
# without the asterisks, lands ON the section in the app.
#
# PAIRED is the load-bearing word. Deleting every `*` unconditionally would
# rebuild the very false pass this check exists to remove: the sample-vault-f vault
# has sources-page headings like `## A*B product sheet`, where
# the asterisk is literal text, not a marker. A blanket strip rewrites both
# sides to `AB product sheet`, so the checker calls it clean while Obsidian -- which
# renders a lone asterisk literally -- lands at top-of-page. A deleted
# CHARACTER is the em-dash failure; an omitted MARKER is the safe case.
#
# DELIBERATELY asterisks only. Underscores, tildes and backticks are plausibly
# stripped too, but that is untested, and assuming it would rebuild the same
# false pass one layer down and harder to see. An untested marker mismatching
# now reports SUSPECT, which is the safe direction. Widen this only against an
# observation, and cite it here.
EMPHASIS = re.compile(r"\*\*\*(.+?)\*\*\*|\*\*(.+?)\*\*|\*([^*]+?)\*")


def _unemphasize(text: str) -> str:
    """Drop paired asterisk markers, keep their inner text. An unpaired
    asterisk is literal and survives untouched."""
    return EMPHASIS.sub(lambda m: next(g for g in m.groups() if g is not None), text)


def anchor_text(h: str) -> str:
    """The heading as it should be WRITTEN inside [[page#...]].

    Only PAIRED asterisk emphasis is removed -- see EMPHASIS. Everything else
    is carried verbatim, including a lone literal asterisk (`E*Trade`), because
    a substituted character is what breaks resolution (the 2026-08-08 em-dash
    finding), not an omitted marker."""
    return re.sub(r"\s+", " ", _unemphasize(h)).strip()


# A leading ordered-list marker's PUNCTUATION. Obsidian ignores it when
# matching: click-tested 2026-08-09 in the sample-vault-f vault, an anchor written
# `#3 Real risk isn't volatility - it's outliving your money` resolved to the
# heading `### 3. Real risk isn't volatility - it's outliving your money` and
# highlighted the section (lines 34-49 of sources/murray-simple-wealth.md).
#
# The NUMBER is kept -- the anchor that resolved still carried its `3`, so only
# the trailing `.` is dropped. Deliberately narrow: this transform encodes the
# one thing observed, not a theory that Obsidian ignores periods generally. A
# period elsewhere in a heading still counts, and a mismatch there reports
# SUSPECT, which is the safe direction.
LIST_MARKER = re.compile(r"^(\d+)[.)](\s)")


def strict_heading(h: str) -> str:
    """Obsidian's ACTUAL matching rule, as far as it has been tested: strip
    paired asterisk emphasis and a leading list marker's punctuation, collapse
    whitespace, casefold -- and change nothing else.

    This exists because `norm_heading()` above is deliberately lenient (it
    folds ALL punctuation to spaces), and that leniency is a known source of
    false passes: on 2026-08-08 an anchor writing a heading's em-dash as `--`
    passed `--check` and still landed at top-of-page in Obsidian. Both
    spellings survive norm_heading, so the checker could not see the
    difference that mattered.

    So anchors are compared in two tiers. A strict match is genuinely fine. A
    match that needs the lenient tier is SUSPECT -- reported separately, never
    folded into the broken count, because leniency is a superset and some of
    what it forgives does resolve in the app."""
    h = LIST_MARKER.sub(r"\1\2", _unemphasize(h))
    return re.sub(r"\s+", " ", h).strip().casefold()


def is_generic_heading(h: str) -> bool:
    """A heading that restates the obvious. See GENERIC_HEADINGS.

    The link-inventory / history test is audit_claims.SKIP_HEADINGS itself, so
    the two scripts cannot disagree about what is bookkeeping and what is a
    claim."""
    return (bool(SKIP_HEADINGS.match("## " + h.strip()))
            or norm_heading(h) in GENERIC_HEADINGS
            or h.strip().casefold().startswith(GENERIC_PREFIX))


def link_target(span: str) -> str:
    """Target of a raw '[[...]]' span: display text and #anchor stripped."""
    inner = span[2:-2]
    inner = re.split(r"\\?\|", inner, maxsplit=1)[0]
    return inner.split("#", 1)[0].strip()


def mask_other_links(line: str, slug: str) -> str:
    """Blank out every wikilink span (target AND display text) that is not a
    link to sources/<slug>, keeping the surrounding prose and the line's
    length. Without this, a line citing several sources scores its full text
    against each of them and every citation converges on the same section --
    the second-largest source of wrong picks in the field run."""
    def repl(m):
        target = link_target(m.group(0))
        if is_source_target(target) and slug_of(target) == slug:
            return m.group(0)
        return " " * len(m.group(0))
    return LINK_SPAN.sub(repl, line)


class Section:
    """One section of a sources page (its heading plus the body under it)."""

    __slots__ = ("heading", "level", "parent", "figures", "terms", "generic",
                 "eligible", "verbatim")

    def __init__(self, heading, level, parent, figures, terms, generic, verbatim):
        self.heading = heading
        self.level = level
        self.parent = parent        # index into SourcePage.sections, or -1
        self.figures = figures
        self.terms = terms
        self.generic = generic
        self.verbatim = verbatim    # carries the source's own words (see VERBATIM_BONUS)
        self.eligible = False       # may win an anchor; set in SourcePage


class SourcePage:
    """Sections of a sources/ page, with heading levels and ancestry.

    Two parsing rules that make this agree with Obsidian rather than with a
    naive line scan:
      - fenced code blocks are NOT parsed for headings. A ``` block quoting a
        markdown template would otherwise contribute sections that do not exist
        on the rendered page -- and --check, reading the same structure, would
        happily validate an anchor Obsidian cannot resolve.
      - H1 is a real heading (Obsidian links to it), so it always lands in
        headings_norm and --check never false-flags an H1 anchor. It is only
        ELIGIBLE to win a new anchor on pages that have no deeper headings: on
        a normal page the single H1 is the restated page title, which is no
        locator at all."""

    def __init__(self, slug: str):
        self.slug = slug
        self.exists = False
        self.sections = []          # [Section] -- ALL sections, generic included
        self.scorable = []          # indices of sections eligible to win an anchor
        self.headings_norm = set()  # ALL headings (any level), lenient, for --check
        self.headings_strict = set()  # same, under strict_heading (see the two tiers)
        self.block_ids = set()      # ^block-id targets (casefolded), for --check
        path = ac.SOURCES / f"{slug}.md"
        if not path.exists():
            return
        self.exists = True
        stack = []                  # [(level, index)] of open ancestors
        cur = None                  # (heading, level, parent, body_lines)
        in_fence = False
        for line in path.open(encoding="utf-8", newline="").read().splitlines():
            if FENCE.match(line):
                in_fence = not in_fence
                if cur is not None:
                    cur[3].append(line)
                continue
            if not in_fence:
                # A ^block-id inside a fence is literal text, not a target,
                # so this collection mirrors the heading rule above.
                bm = BLOCK_ID.search(line)
                if bm:
                    self.block_ids.add(bm.group(1).casefold())
            m = None if in_fence else HEADING.match(line)
            if m:
                idx = self._close(cur)
                if idx is not None:
                    stack.append((cur[1], idx))
                level, head = len(m.group(1)), m.group(2)
                self.headings_norm.add(norm_heading(head))
                self.headings_strict.add(strict_heading(head))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                cur = (head, level, stack[-1][1] if stack else -1, [])
            elif cur is not None:
                cur[3].append(line)
        self._close(cur)
        min_level = 2 if any(s.level >= 2 for s in self.sections) else 1
        for i, s in enumerate(self.sections):
            s.eligible = not s.generic and s.level >= min_level
            if s.eligible:
                self.scorable.append(i)

    def _close(self, cur):
        if cur is None:
            return None
        head, level, parent, body = cur
        text = head + "\n" + "\n".join(body)
        low = text.casefold()
        terms = {w for w in WORD.findall(low) if w not in STOP}
        filled = [b for b in body if b.strip()]
        quoted = sum(1 for b in filled if b.lstrip().startswith(">"))
        verbatim = bool(LOCATOR_HEADING.match(head.strip())) or (
            quoted >= VERBATIM_MIN_QUOTE_LINES
            and quoted >= VERBATIM_MIN_QUOTE_SHARE * len(filled))
        self.sections.append(Section(head, level, parent, norm_figures(text),
                                     terms, is_generic_heading(head), verbatim))
        return len(self.sections) - 1

    def nomatch_reason(self):
        """Why nothing here can win an anchor (None if something can)."""
        if self.scorable:
            return None
        return "no-sections" if not self.sections else "generic-only"

    def ancestors(self, i):
        """Indices of section i's enclosing headings, nearest first."""
        out, p = [], self.sections[i].parent
        while p >= 0:
            out.append(p)
            p = self.sections[p].parent
        return out

    def resolve_anchor(self, i):
        """-> (heading_to_link, used_fallback). A heading with link-breaking
        characters cannot be written as an anchor; rather than dropping the
        match, walk up to the nearest linkable ancestor that is itself eligible
        (non-generic, and a real section level for this page). A coarser locator
        still beats none, but it is REVIEW-only -- see tier_claim.
        (None, False) if nothing qualifies.

        Asterisk emphasis is NOT link-breaking: the shared source-page shape
        ships headings like `## What's *particularly* useful for ...`, and
        Obsidian resolves an anchor that omits the asterisks (confirmed
        2026-08-09). Treating `*` as unlinkable made every such heading
        permanently unanchorable -- roughly seven per vault -- and pushed
        real claims onto coarser ancestor locators for no reason. So the
        anchor is written emphasis-stripped, and linkability is judged on the
        text actually written."""
        sec = self.sections[i]
        cand = anchor_text(sec.heading)
        if not UNLINKABLE.search(cand):
            return cand, False
        for a in self.ancestors(i):
            anc = self.sections[a]
            anc_cand = anchor_text(anc.heading)
            if anc.eligible and not UNLINKABLE.search(anc_cand):
                return anc_cand, True
        return None, False

    def score(self, line: str):
        """[(score, unique_figure_count, section_index)] best-first.

        A figure found in exactly ONE section is worth far more than one found
        in several; likewise a term. A section that is mostly the source's own
        words is multiplied by VERBATIM_BONUS -- of two sections that match the
        claim, the one carrying the quote is the better locator. Generic
        sections take no part: not as candidates, and not in the document
        frequencies that decide how rare a term is."""
        line = mask_other_links(line, self.slug)
        c_figs = norm_figures(line)
        c_terms = {w for w in WORD.findall(line.casefold()) if w not in STOP}
        # document frequency of each term / figure across scorable sections
        df_term, df_fig = {}, {}
        for i in self.scorable:
            sec = self.sections[i]
            for t in c_terms & sec.terms:
                df_term[t] = df_term.get(t, 0) + 1
            for f in c_figs & sec.figures:
                df_fig[f] = df_fig.get(f, 0) + 1
        ranked = []
        for i in self.scorable:
            sec = self.sections[i]
            fig_hits = c_figs & sec.figures
            uniq_figs = {f for f in fig_hits if df_fig.get(f) == 1}
            s = 4.0 * len(uniq_figs) + 1.5 * (len(fig_hits) - len(uniq_figs))
            for t in c_terms & sec.terms:
                df = df_term[t]
                s += 2.0 if df == 1 else (1.0 if df == 2 else 0.2)
            if s > 0:
                if sec.verbatim:
                    s *= VERBATIM_BONUS
                ranked.append((s, len(uniq_figs), i))
        ranked.sort(key=lambda r: r[0], reverse=True)
        return ranked


def tier_claim(page: SourcePage, line: str):
    """-> dict(tier, anchor, candidates, fallback, reason) for one unanchored
    claim. `reason` is set only for NOMATCH, to separate the structural ceiling
    (nothing to anchor to) from a genuine failure to match."""
    if not page.exists:
        return {"tier": "NOMATCH", "anchor": None, "candidates": [],
                "fallback": False, "reason": "missing-page"}
    if not page.scorable:
        return {"tier": "NOMATCH", "anchor": None, "candidates": [],
                "fallback": False, "reason": page.nomatch_reason()}
    ranked = page.score(line)
    if not ranked:
        return {"tier": "NOMATCH", "anchor": None, "candidates": [],
                "fallback": False, "reason": "no-match"}
    cands = [{"heading": page.sections[i].heading, "score": round(s, 1),
              "unique_figures": uf, "verbatim": page.sections[i].verbatim}
             for s, uf, i in ranked[:3]]
    best_s, best_uf, best_i = ranked[0]
    second_s = ranked[1][0] if len(ranked) > 1 else 0.0
    anchor, fallback = page.resolve_anchor(best_i)
    if fallback:
        cands[0]["fallback_to"] = anchor
    # A fallback anchor points at the winner's PARENT, so the evidence that won
    # is not in the section being linked. Mechanical tiers refuse it (in the
    # backtest, 2 of 3 fallback picks were wrong); it stays visible as a REVIEW
    # candidate with "fallback_to", for a human to accept or better.
    mechanical = anchor is not None and not fallback
    if (mechanical and best_uf >= 1 and best_s >= AUTO_SCORE_MIN
            and (second_s == 0 or best_s >= AUTO_MARGIN * second_s)):
        return {"tier": "AUTO", "anchor": anchor, "candidates": cands,
                "fallback": False, "reason": None}
    if (mechanical and page.sections[best_i].verbatim
            and best_s >= HIGH_SCORE_MIN
            and (second_s == 0 or best_s >= HIGH_MARGIN * second_s)):
        return {"tier": "HIGH_REVIEW", "anchor": anchor, "candidates": cands,
                "fallback": False, "reason": None}
    return {"tier": "REVIEW", "anchor": None, "candidates": cands,
            "fallback": fallback, "reason": None}


def scan():
    """-> (entries, meta). meta carries the NOMATCH breakdown and the
    structural-ceiling roll-up (pages that CANNOT be anchored as they stand)."""
    claims = [c for c in iter_claim_lines() if not c["anchored"]]
    pages, entries = {}, []
    for c in claims:
        slug = c["slug"]
        page = pages.get(slug)
        if page is None:
            page = pages[slug] = SourcePage(slug)
        r = tier_claim(page, c["line"])
        e = {
            "rel": c["rel"].replace("\\", "/"), "lineno": c["lineno"],
            "slug": slug, "tier": r["tier"], "anchor": r["anchor"],
            "candidates": r["candidates"],
            "line": c["line"][:200],
        }
        if r["fallback"]:
            e["fallback"] = True
        if r["reason"]:
            e["nomatch_reason"] = r["reason"]
        entries.append(e)

    breakdown = {"missing-page": 0, "no-sections": 0, "generic-only": 0, "no-match": 0}
    ceiling_slugs, ceiling_claims = set(), 0
    for e in entries:
        why = e.get("nomatch_reason")
        if not why:
            continue
        breakdown[why] = breakdown.get(why, 0) + 1
        if why in ("no-sections", "generic-only"):
            ceiling_slugs.add(e["slug"])
            ceiling_claims += 1
    meta = {
        "nomatch_breakdown": breakdown,
        "structural_ceiling": {
            "claims": ceiling_claims,
            "pages": len(ceiling_slugs),
            "slugs": sorted(ceiling_slugs),
        },
    }
    return entries, meta


# ---------------------------------------------------------------- apply ----

def link_pattern(slug: str) -> re.Pattern:
    """An UNANCHORED wikilink to sources/<slug>, any relative prefix, optional
    (possibly pipe-escaped) display text."""
    return re.compile(
        r"\[\[((?:\.\./)*sources/" + re.escape(slug) + r")(\\?\|[^\]]*)?\]\]"
    )


def apply_entries(entries):
    """Insert anchors in place. Returns (applied, skipped_msgs, fallbacks, landed).

    `landed` names WHAT actually reached disk -- one
    {rel, lineno, slug, anchor, tier} per anchor written, SKIPs excluded. The
    counts cannot identify a page, and a caller that has to report which pages
    a run touched (regression R94) has no other source for it: the next scan lists
    only UNANCHORED claims, so an anchor that just landed disappears from the
    suggestions file that named it. len(landed) == applied by construction.

    Two refusals keep a batch honest:
      - the recorded line text must still match. A decisions file is written by
        one run and applied by a later one; if the citing page gained or lost a
        line in between, lineno now points at a DIFFERENT claim -- and if that
        claim happens to cite the same source (routine), the anchor would be
        written onto the wrong claim, with --check green-lighting it because the
        heading is real. Only the pairing is wrong, which is undetectable after
        the fact. So a moved line is skipped, not guessed at.
      - an anchor carrying link-breaking characters is never written.

    I/O is byte-transparent: files are read and written with newline="" so a
    one-line edit does not rewrite every other line's terminator (which would
    turn an LF-authored page into a CRLF one on Windows -- a whole-file diff,
    and a whole-file re-sync for the Dropbox instances)."""
    applied, skipped, fallbacks, landed = 0, [], 0, []
    by_file = {}
    for e in entries:
        if e.get("anchor"):
            by_file.setdefault(e["rel"], []).append(e)
    for rel, es in sorted(by_file.items()):
        path = ac.ROOT / rel
        if not path.exists():
            skipped.append(f"{rel}: file not found")
            continue
        lines = path.open(encoding="utf-8", newline="").read().splitlines(keepends=True)
        dirty = False
        # The stale-line guard compares against the line AS THIS BATCH FOUND IT,
        # not as the batch has since rewritten it: a line citing two sources
        # gets two entries, and the first anchor landing must not make the
        # second read as "changed since the scan" (regression R91). Only an edit
        # that happened OUTSIDE this apply is stale.
        as_found: dict[int, str] = {}
        # Per-file, and merged into `landed` only AFTER this file's write
        # returns: the record must name pages that reached DISK, and a page
        # locked by Obsidian or mid-Dropbox-sync raises at write time with
        # every in-memory edit already made. (`applied` keeps its pre-existing
        # in-loop increment; that counter is printed by a run that got far
        # enough to print, while the record is read by a later reconciliation.)
        pending: list[dict] = []
        for e in sorted(es, key=lambda x: x["lineno"]):
            i = e["lineno"] - 1
            if i >= len(lines):
                skipped.append(f"{rel}:{e['lineno']}: line number out of range")
                continue
            if UNLINKABLE.search(e["anchor"]):
                skipped.append(f"{rel}:{e['lineno']}: heading not cleanly linkable: {e['anchor']!r}")
                continue
            recorded = e.get("line")
            original = as_found.setdefault(i, lines[i])
            if recorded and original.strip()[:len(recorded)] != recorded:
                skipped.append(f"{rel}:{e['lineno']}: line changed since the scan "
                               f"(stale entry -- re-scan before applying)")
                continue
            pat = link_pattern(e["slug"])
            new, n = pat.subn(
                lambda m: "[[" + m.group(1) + "#" + e["anchor"] + (m.group(2) or "") + "]]",
                lines[i], count=1)
            if n == 0:
                skipped.append(f"{rel}:{e['lineno']}: no unanchored [[sources/{e['slug']}]] link on that line (stale entry?)")
                continue
            lines[i] = new
            dirty = True
            applied += 1
            # Only the reconciliation fields: rel/lineno/slug/anchor/tier. The
            # scan entry also carries `candidates` and 200 chars of `line`,
            # which would bloat a file that sits in a Dropbox-synced vault and
            # answers no question the reconciliation asks.
            pending.append({"rel": rel, "lineno": e["lineno"], "slug": e["slug"],
                            "anchor": e["anchor"], "tier": e.get("tier")})
            if e.get("fallback"):
                fallbacks += 1
        if dirty:
            with path.open("w", encoding="utf-8", newline="") as fh:
                fh.write("".join(lines))
            landed.extend(pending)
    return applied, skipped, fallbacks, landed


# ---------------------------------------------------------------- check ----

def check_anchors_full():
    """Validate every anchored sources-link in the claim population.

    Returns (problems, suspects) -- two DISTINCT severities:

      problems  the anchor names no heading on the page even under the
                lenient `norm_heading()`. Certainly broken.
      suspects  it matches only under the lenient tier, not under
                `strict_heading()`. The checker vouches for it; Obsidian may
                not. This is the false-pass class the em-dash lesson found
                the hard way (2026-08-08), and it was invisible before.

    Suspects are deliberately NOT problems. Leniency is a superset and some of
    what it forgives genuinely resolves -- markdown emphasis, confirmed
    2026-08-09 -- so promoting them to broken would turn every vault's close-out
    red on a category that is partly noise. `check_anchors()` below keeps the
    old single-list contract for exactly that reason."""
    problems, suspects = [], []
    pages = {}
    anchor_re = None
    for c in iter_claim_lines():
        if not c["anchored"]:
            continue
        if anchor_re is None:
            anchor_re = re.compile(r"\[\[(?:\.\./)*sources/([^\]|#\\]+)#([^\]|\\]+)")
        for m in anchor_re.finditer(c["line"]):
            slug, anchor = m.group(1).strip(), m.group(2).strip()
            if slug != c["slug"]:
                continue
            page = pages.setdefault(slug, SourcePage(slug))
            if not page.exists:
                problems.append(f"{c['rel']}:{c['lineno']}: sources/{slug}.md not found")
            elif anchor.startswith("^"):
                # Block-id citations are the PRESCRIBED form when the target
                # heading carries link-breaking characters, so they must be
                # drift-checked like heading anchors or coverage silently
                # absorbs unverifiable locators (regression R4).
                if anchor[1:].casefold() not in page.block_ids:
                    problems.append(
                        f"{c['rel']}:{c['lineno']}: block anchor '{anchor}' not found on sources/{slug}.md")
            elif strict_heading(anchor) in page.headings_strict:
                continue                       # clean under Obsidian's own rule
            elif norm_heading(anchor) in page.headings_norm:
                near = next((h for h in page.sections
                             if norm_heading(h.heading) == norm_heading(anchor)), None)
                suspects.append(
                    f"{c['rel']}:{c['lineno']}: anchor #'{anchor}' matches sources/{slug}.md only "
                    f"after lenient normalization"
                    + (f" -- heading is '{near.heading}'" if near else ""))
            else:
                problems.append(
                    f"{c['rel']}:{c['lineno']}: anchor #'{anchor}' not a heading on sources/{slug}.md")
    return problems, suspects


def check_anchors():
    """Broken anchors only. Stable single-list contract for lint.py -- callers
    wanting the SUSPECT tier call check_anchors_full()."""
    return check_anchors_full()[0]


def run_check(prefix="=== ANCHOR CHECK ==="):
    problems, suspects = check_anchors_full()
    print(f"{prefix} {len(problems)} broken, {len(suspects)} suspect")
    for p in problems:
        print("BROKEN: " + p)
    for s in suspects:
        print("SUSPECT: " + s)
    return problems


def refresh_coverage(no_history):
    """Recompute vault-wide locator coverage from disk and record the
    day's datapoint. Called after every apply, unconditionally --
    the edits are already on disk whether or not --check found
    unrelated broken anchors."""
    if no_history or record_coverage is None:
        print("Re-run audit_claims.py for the new coverage number.")
        return
    population = list(iter_claim_lines())   # fresh, unfiltered re-count
    total = len(population)
    anchored = sum(1 for c in population if c["anchored"])
    hist = record_coverage(anchored, total)
    if total:
        print(f"  coverage now: {anchored}/{total} ({100*anchored/total:.0f}%) recorded in _meta/locator-coverage.json")
    if hist and len(hist) > 1:
        trail = "  ".join(f"{h['date']}:{h['pct']:.0f}%" for h in hist[-4:])
        print(f"  trend: {trail}")


# ----------------------------------------------------------------- main ----

TIERS = ("AUTO", "HIGH_REVIEW", "REVIEW", "NOMATCH")


def write_suggestions(entries, counts, meta):
    """Persist the scan. Written by the apply modes too, BEFORE they edit: a
    run that mutates the vault must leave the record of what it chose (and
    which choices were fallbacks) on disk, not just on the console."""
    META.mkdir(exist_ok=True)
    SUGGESTIONS_FILE.write_text(json.dumps({
        "generated": datetime.date.today().isoformat(),
        "counts": counts,
        "nomatch_breakdown": meta["nomatch_breakdown"],
        "structural_ceiling": meta["structural_ceiling"],
        "entries": entries,
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def write_auto_applied(landed, mode):
    """Record WHICH pages a mechanical apply mode just anchored (regression R94).

    _meta/anchor-suggestions.json cannot answer that question: the
    anchor-backfill workflow re-scans immediately after --apply-auto, and a
    scan lists only UNANCHORED claims, so the second write drops every entry
    that just landed. Nothing else on disk names those pages, and the
    maintenance pass's reconciliation has to decide whether a modified wiki
    page was the run's doing or an unapproved edit. The scan never touches
    this file, so the record survives the re-scan.

    Written UNCONDITIONALLY by --apply-auto / --apply-all, entries: []
    included. The file lives in a non-git, Dropbox-synced vault, where a
    leftover list from an earlier run would make that reconciliation LOOSER
    than the count bound it replaces -- the wrong direction to fail.

    NOT written by --apply: the decisions pass has its own record (the
    decisions JSON it was handed), and writing here would clobber the Scan
    step's list later in the same run.

    `generated` carries a full TIMESTAMP here, unlike the date-only field in
    write_suggestions: the reconciliation has to decide whether this record is
    this run's or a leftover, a date cannot answer that for an unattended pass
    that crosses midnight, and mtime -- the only other answer -- is exactly the
    attribute a Dropbox re-download rewrites.
    """
    META.mkdir(exist_ok=True)
    AUTO_APPLIED_FILE.write_text(json.dumps({
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "applied": len(landed),
        "entries": landed,
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def fallback_note(n):
    return (f"  of which {n} fallback anchor(s): the winning heading was not linkable, "
            "so the anchor points at its parent section (coarser -- the figure that "
            "justified the pick is deeper in)." if n else "")


def main():
    ap = argparse.ArgumentParser(description="Suggest, apply, and validate #heading anchors on sources-citing claims.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--summary", action="store_true", help="scan; counts only")
    mode.add_argument("--apply-auto", action="store_true", help="apply the AUTO tier from a fresh scan")
    mode.add_argument("--apply-all", action="store_true", help="apply the AUTO + HIGH_REVIEW tiers from a fresh scan")
    mode.add_argument("--apply", metavar="FILE", help="apply a decisions JSON (entries with non-empty 'anchor')")
    mode.add_argument("--check", action="store_true", help="validate existing anchors only")
    ap.add_argument("--no-history", action="store_true",
                    help="do not record the post-apply coverage datapoint in _meta/locator-coverage.json")
    ap.add_argument("--root", default=None, metavar="PATH",
                    help="work on the vault at PATH instead of this script's own repo "
                         "(a template/worktree copy can then be run against a real vault; "
                         "the apply modes WRITE there, so verify with a scan first)")
    args = ap.parse_args()

    try:  # Windows consoles default to cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    if args.root:
        try:
            set_root(args.root)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"(working on {ac.ROOT})")

    if args.check:
        # Must go through run_check(), same as the post-apply paths: --check is
        # the mode a session runs DELIBERATELY to validate anchors, so it is the
        # last place that may hide the SUSPECT tier. Calling the check_anchors()
        # compatibility wrapper here printed "0 broken" on a vault full of
        # lenient-only matches -- the em-dash false pass, reintroduced in the
        # one command meant to catch it.
        #
        # Exit code still keys on `problems` alone. A suspect is advisory (some
        # resolve fine in Obsidian), and making it non-zero would fold the two
        # severities back together in the CI-facing signal.
        problems = run_check()
        return 1 if problems else 0

    if args.apply:
        data = json.loads(Path(args.apply).read_text(encoding="utf-8"))
        entries = data["entries"] if isinstance(data, dict) else data
        applied, skipped, fallbacks, _landed = apply_entries(entries)
        for s in skipped:
            print("SKIP: " + s)
        print(f"=== APPLIED === {applied} anchor(s) from {args.apply}; {len(skipped)} skipped")
        if fallbacks:
            print(fallback_note(fallbacks))
        problems = run_check()
        refresh_coverage(args.no_history)
        return 1 if problems else 0

    entries, meta = scan()
    counts = {t: 0 for t in TIERS}
    for e in entries:
        counts[e["tier"]] += 1

    if args.apply_auto or args.apply_all:
        write_suggestions(entries, counts, meta)   # the record, before the edits
        auto = [e for e in entries if e["tier"] == "AUTO"]
        a_applied, skipped, fallbacks, landed = apply_entries(auto)
        h_applied = 0
        if args.apply_all:
            high = [e for e in entries if e["tier"] == "HIGH_REVIEW"]
            h_applied, h_skipped, h_fallbacks, h_landed = apply_entries(high)
            skipped = skipped + h_skipped
            fallbacks += h_fallbacks
            # BOTH tiers go in the record: --apply-all edits HIGH_REVIEW pages
            # too, and recording only AUTO would reproduce regression R94 one tier
            # over.
            landed = landed + h_landed
        write_auto_applied(landed, "--apply-all" if args.apply_all else "--apply-auto")
        for s in skipped:
            print("SKIP: " + s)
        if args.apply_all:
            print(f"=== APPLIED === {a_applied + h_applied} anchor(s): "
                  f"{a_applied} AUTO + {h_applied} HIGH_REVIEW; {len(skipped)} skipped")
        else:
            print(f"=== APPLIED === {a_applied} AUTO anchor(s); {len(skipped)} skipped")
        if fallbacks:
            print(fallback_note(fallbacks))
        print(f"  full record of what was chosen: {SUGGESTIONS_FILE.relative_to(ac.ROOT)}")
        print(f"  pages this run anchored: {AUTO_APPLIED_FILE.relative_to(ac.ROOT)}")
        problems = run_check()
        refresh_coverage(args.no_history)
        if counts["REVIEW"] or counts["NOMATCH"]:
            print("Re-scan for the remaining tiers.")
        return 1 if problems else 0

    write_suggestions(entries, counts, meta)

    total = len(entries) or 1
    fallbacks = sum(1 for e in entries if e.get("fallback"))
    print(f"=== ANCHOR SUGGESTIONS ===  unanchored claims: {len(entries)}")
    print(f"  AUTO        {counts['AUTO']:4d}  ({100*counts['AUTO']//total}%)  safe to --apply-auto")
    print(f"  HIGH_REVIEW {counts['HIGH_REVIEW']:4d}  ({100*counts['HIGH_REVIEW']//total}%)  one clear winner, anchor pre-filled -- skim, then --apply-all")
    print(f"  REVIEW      {counts['REVIEW']:4d}  ({100*counts['REVIEW']//total}%)  candidates in {SUGGESTIONS_FILE.relative_to(ac.ROOT)}")
    print(f"  NOMATCH     {counts['NOMATCH']:4d}  ({100*counts['NOMATCH']//total}%)  no section matched -- drift candidates, do not force")
    ceiling = meta["structural_ceiling"]
    if ceiling["claims"]:
        slugs = ceiling["slugs"]
        shown = ", ".join(slugs[:8]) + (f", ... (+{len(slugs) - 8} more)" if len(slugs) > 8 else "")
        nb0 = meta["nomatch_breakdown"]
        why = (f"{nb0['no-sections']} with no headings at all"
               + (f", {nb0['generic-only']} with only bookkeeping headings"
                  if nb0.get("generic-only") else ""))
        print(f"  structural ceiling: {ceiling['claims']} claim(s) cite {ceiling['pages']} source page(s) "
              f"with no anchorable section ({why}): {shown}")
        print("    -> unfixable claim-side. Give those sources pages substantive sections, then re-scan.")
    nb = meta["nomatch_breakdown"]
    if nb["missing-page"]:
        print(f"  missing sources pages: {nb['missing-page']} claim(s) cite a page that does not exist (lint.py territory)")
    if fallbacks:
        print(f"  fallback anchors: {fallbacks} (winning heading not linkable -- anchored to its parent section)")
    if args.summary:
        return 0
    by_slug = {}
    for e in entries:
        d = by_slug.setdefault(e["slug"], {t: 0 for t in TIERS})
        d[e["tier"]] += 1
    print("\n  per sources page (AUTO/HIGH/REVIEW/NOMATCH), review-heaviest first:")
    ordered = sorted(by_slug.items(),
                     key=lambda kv: kv[1]["REVIEW"] + kv[1]["HIGH_REVIEW"], reverse=True)
    for slug, d in ordered[:25]:
        print(f"    {d['AUTO']:3d} /{d['HIGH_REVIEW']:4d} /{d['REVIEW']:4d} /{d['NOMATCH']:4d}   {slug}")
    if len(by_slug) > 25:
        print(f"    ... and {len(by_slug) - 25} more sources pages (full detail in the JSON)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
