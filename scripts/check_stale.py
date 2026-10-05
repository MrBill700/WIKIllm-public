#!/usr/bin/env python3
"""Staleness detector for the LLM wiki — the mechanical half of an LLM lint pass.

lint.py finds broken links. This finds content that has likely gone *stale*
since it was written — the drift that crept in when a decision was made but its
ripple across downstream pages was deferred and then forgotten.

Six sections:

  A. Open-loops ledger    — lists the deferred-propagation items in
     _meta/open-loops.md and flags any whose `trigger:` date has passed
     (OVERDUE). This is the ledger the session close-out protocol writes to.
     A loop is CLOSED by flipping its marker to [x]: this section reads the
     marker and nothing else, so a dated CLOSED/RESOLVED note left on a [ ] or
     [~] line still counts as open and overdue. Whenever anything is OVERDUE
     the section prints one reminder saying so (regression R74).

  B. Time-sensitive markers — phrases that are *meant to flip* once a decision /
     ingest / answer lands: "pending ingest", "NOT yet done", "TODO", "needs
     input" (also "needs <name>'s input/answer/call/sign-off"), ==highlight==
     (the wiki's unverified-claim convention), "placeholder", "TBD". Grouped by
     file with line numbers. Markers on *tracker pages* (pages that are
     SUPPOSED to hold open items — a research-agenda or open-questions page)
     are reported separately — open items are EXPECTED there, so they're
     low-signal. Declare a tracker page by putting `tracker_page: true` in its
     frontmatter (preferred — survives script syncs), or list it in
     TRACKER_PAGES below. Pages carrying `auto_generated: true` are skipped:
     their markers are engine-emitted, not human drift.
     B3 validates the TERMINAL counterpart of those markers: the inline label
     `*(unverifiable: <slug>)*`, whose reason is a CLOSED vocabulary
     (source-gone | paywalled | provenance-lost -- _meta/fleet-conventions.md,
     regression R46). No MARKERS regex matches the label, by design, so without
     this check an invented slug leaves the drift channel silently. Valid
     labels are counted so the population stays visible; anything outside the
     vocabulary is reported file:line, in --summary too (regression R57). Only
     text shaped like the LABEL is validated -- prose that merely uses the
     word in brackets is not a label and is never flagged.
     Every VALID label is then cross-checked against wiki/log.md for the
     downgrade entry the convention requires (regression R99): a heading
     `## [YYYY-MM-DD] meta | downgrade | <page path> | <slug>` naming the
     label's page (path match, never line numbers) and its slug. A label with
     no such entry is reported UNLOGGED file:line, in --summary too. Hygiene,
     not validity: the label still counts as valid and the exit code is
     unchanged -- the entry is the only durable record of WHY the claim can
     never be re-verified, and without it the story is gone.

  C. Central-page age      — pages tagged `central` whose frontmatter `updated:`
     date is older than --days (default 45), as a gentle "re-read me" list.

  D. Recurring items due   — any wiki/_meta line carrying a
     "Last run: ... Next due: ~YYYY-MM" stamp (the quarterly claim audit, a
     graph-curation pass, an annual review, ...). Flagged DUE once that month
     arrives. Because this script runs at every session close-out, a due
     recurring task nags every session until it's run and re-stamped — the
     reminder mechanism that doesn't rely on human memory.
     The section always ends with a "claim audit:" status line: installed
     (file:line + next due), NOT INSTALLED (no stamp names the claim audit —
     "0 tracked" is not health, regression R79), or EXEMPT when a wiki/_meta line
     reads "claim audit: exempt -- <reason>" (a deliberate, recorded
     decision that the audit does not apply to this vault's citation style).

  E. Instance scaffold     -- the required instance-owned files every vault is
     supposed to carry (CLAUDE.md, scripts/README.md, _meta/llm-wiki.md, ...).
     They are deliberately NOT in sync_from_template.py's SYNC_SET, so a vault
     bootstrapped from an older template silently lacks whatever was added
     later and nothing ever says so (regression R65). This section only
     REPORTS the gap -- instance-owned stays instance-owned; a session
     bootstraps the file by hand from the template. Reported in --summary too.

  F. Transcript provenance -- every wiki/sources/ page that is transcript-
     derived must carry `transcript_kind:` from the closed vocabulary in
     _meta/fleet-conventions.md (regression R45). A page counts as transcript-
     derived if its FRONTMATTER names raw/watched/ or carries
     transcript_source / transcript_kind, if it is tagged video / podcast /
     lecture / talk / webinar, or if its body holds >= TIMESTAMP_MIN distinct
     [mm:ss] timestamps. Reports MISSING (with the fix derived from
     transcript_source via the compat map), INVALID (outside the vocabulary)
     and LEGACY LABEL (a /watch label such as bare `captions` used as the
     kind -> migrate to the canonical value; migration, never grandfathering).
     Reported in --summary too.

It never auto-fixes and never fails a build — every line is a *candidate for
review*. The script can't tell a legitimately-open item from a stale one; a
human (or the session) decides. Run at session close-out (alongside lint.py)
and at the annual review.

Run: python scripts/check_stale.py [--days N] [--summary]

--summary prints section counts plus only the actionable lines (OVERDUE ledger
items, DUE recurring items, INVALID terminal labels, MISSING SCAFFOLD files,
and section F transcript-provenance findings).
Use it at close-out instead of truncating with
`| head` / `| Select-Object -First N` — early-terminated pipes kill the
interpreter and report a false failure (exit 255 under PowerShell).
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import re
import sys

ROOTS = ["wiki", "_meta"]
LEDGER = "_meta/open-loops.md"
# Pages that are SUPPOSED to hold open items (a research-agenda page, an
# open-questions page, etc.). Their marker hits get reported separately as
# low-signal so they don't drown out real drift. PREFERRED: put
# `tracker_page: true` in the page's frontmatter instead of editing this set —
# frontmatter survives template syncs; this set is a legacy fallback.
TRACKER_PAGES: set[str] = set()
SKIP_SUBSTR = ("raw-clips", "/extracted/")  # source clips + transient caches: not maintained wiki prose

DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# Each marker is meant to be temporary — it should be removed/flipped once its
# triggering event happens. Their lingering presence is the staleness signal.
MARKERS = [
    ("pending-ingest", re.compile(r"pending(?:\s+tier[\s-]*\w+)?\s+ingest|pending\s+tier", re.I)),
    ("deferred", re.compile(
        r"not\s+yet\s+(?:done|executed|built|modeled|swept|woven|propagat)"
        r"|flagged for a follow-?up|future\s+(?:terminology\s+)?pass"
        r"|deferred to a (?:dedicated|follow-?up)", re.I)),
    # "needs input" but also "needs the reviewer's input" / "needs the owner's call" —
    # up to two words (a name) may sit between "needs" and the noun.
    ("needs-input", re.compile(
        r"needs?\s+(?:[\w'-]+\s+){0,2}(?:input|answer|sign-?off|call|a decision|review|confirmation)"
        r"|awaiting\s+(?:input|answer|review|sign-?off|decision)"
        r"|\bTODO\b|\bFIXME\b|weighs? in on", re.I)),
    ("unverified(==)", re.compile(r"==[^=\n]{2,}==")),
    ("placeholder/TBD", re.compile(r"\bplaceholder\b|\bTBD\b|to be determined", re.I)),
]

# --- The terminal counterpart of the MARKERS above (regression R46) ---
# `*(unverifiable: <slug>)*` retires a permanently-unverifiable claim out of the
# temporary-marker channel. The reason is a CLOSED vocabulary; nothing enforced
# it until this section, so an invented slug used to be indistinguishable from a
# valid one -- and, because no MARKERS regex matches the label, invisible.
UNVERIFIABLE_SLUGS = ("source-gone", "paywalled", "provenance-lost")
# The colon is OPTIONAL on purpose: `*(unverifiable)*` is a near-miss that no
# other regex here catches either, so it must be reported, not skipped. The
# asterisks are CAPTURED rather than required, because B3 validates labels and
# must never police prose: an ordinary sentence -- "the figure is disputed
# (unverifiable without the parish register)" -- uses the same word in the same
# brackets. See LABEL SHAPE in scan_unverifiable().
UNVERIFIABLE_RE = re.compile(r"(\*?)\(unverifiable\b\s*:?\s*([^)\n]*)\)(\*?)", re.I)
# `<reason>` / `<slug>` is the convention text's own placeholder. It is not a
# violation ANYWHERE -- a vault page or ledger entry quoting the form must not
# be flagged in every vault (the file skip below only holds while the docs stay
# put).
PLACEHOLDER_REASON_RE = re.compile(r"^<[^>]*>$")
# A label written inside `backticks` is being QUOTED, exactly like one inside a
# ``` fence -- the live form is never code-formatted. Stripping inline code
# before matching is what lets any page document the convention.
INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")
# The downgrade entry a valid label must have in wiki/log.md (regression R99;
# shape defined in _meta/fleet-conventions.md, marker hygiene). A HEADING, so a
# session cannot satisfy it by mentioning the slug in passing, and the date is
# a real one, so the convention's own `[YYYY-MM-DD]` placeholder line -- which
# a fleet-sync note quotes verbatim (sample-vault-b) -- can never match. Path and
# slug are matched after normalization (see downgrade_log_entries); line
# numbers are never part of the key because they shift with every edit.
# Case-insensitive (`Meta | Downgrade` is the same entry); the heading ends at
# the slug -- trailing prose on the heading line fails closed (UNLOGGED).
DOWNGRADE_LOG_PATH = "wiki/log.md"
DOWNGRADE_LOG_RE = re.compile(
    r"^##\s*\[\d{4}-\d{2}-\d{2}\]\s*meta\s*\|\s*downgrade\s*\|\s*([^|\n]+?)\s*\|\s*([^|\s]+)\s*$",
    re.IGNORECASE)

# Instance-owned files a vault is required to carry (regression R66). Deliberately
# NOT in sync_from_template.py's SYNC_SET -- instances own their content, so the
# only way a gap gets noticed is a report like this one. Kept REQUIRED-only: the
# conditional layers (retrieval-layer.md, wiki/data/, positions-register.md) are
# absent from several vaults BY DESIGN, and nagging for them at every close-out
# is exactly the noise section B2 exists to avoid.
REQUIRED_SCAFFOLD = (
    "CLAUDE.md",
    "scripts/README.md",
    "_meta/llm-wiki.md",
    "_meta/obsidian-syntax.md",
    "_meta/open-loops.md",
    "wiki/index.md",
    "wiki/log.md",
)

# --- Section F: transcript provenance (regression R45) ---
# The closed vocabulary and the /watch compat map both live in
# _meta/fleet-conventions.md ("Transcript-derived sources"); keep these in step.
TRANSCRIPT_KINDS = ("auto-captions", "whisper-local", "whisper-remote",
                    "manual-captions", "official", "human-verified")
# Ordered, most specific first (see legacy_kind). Bare `captions` is handled
# as an exact match there, never as a prefix.
# Prefixes stop before the closing paren so an embellished label --
# `whisper (groq, large-v3)` -- still maps (the convention: match as prefixes).
TRANSCRIPT_COMPAT = (
    ("captions (manual", "manual-captions"),
    ("captions (auto", "auto-captions"),
    ("whisper (local", "whisper-local"),
    ("local-whisper", "whisper-local"),
    ("whisper (groq", "whisper-remote"),
    ("whisper (openai", "whisper-remote"),
)
TRANSCRIPT_TAGS = {"video", "podcast", "lecture", "talk", "webinar"}
# A bracketed recording timestamp -- [mm:ss] or [h:mm:ss] -- the Locators form.
TIMESTAMP_RE = re.compile(r"\[(?:\d{1,2}:)?\d{1,2}:[0-5]\d\]")
# Distinct timestamps needed before a page with no other trigger counts as a
# transcript page. Measured 2026-09-26 across the fleet: the smallest real
# non-/watch transcript page carried 7, the largest non-transcript page 4 (a
# paper page quoting another source's video timestamps).
TIMESTAMP_MIN = 5
SOURCES_DIR = "wiki/sources/"


def md_files(roots: list[str]) -> list[str]:
    out: list[str] = []
    for root in roots:
        for d, _, fs in os.walk(root):
            for f in fs:
                if f.endswith(".md"):
                    p = os.path.join(d, f).replace("\\", "/")
                    if not any(s in p for s in SKIP_SUBSTR):
                        out.append(p)
    return out


def read(p: str) -> str:
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return ""


def frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[:end] if end != -1 else ""


def parse_updated(fm: str) -> dt.date | None:
    for line in fm.splitlines():
        if line.lower().startswith("updated:"):
            m = DATE_RE.search(line)
            if m:
                try:
                    return dt.date(int(m[1]), int(m[2]), int(m[3]))
                except ValueError:
                    return None
    return None


def is_central(fm: str) -> bool:
    for line in fm.splitlines():
        if line.lower().startswith("tags:") and "central" in line.lower():
            return True
    return False


def fm_flag(fm: str, key: str) -> bool:
    """True if frontmatter carries `<key>: true` (whitespace-insensitive)."""
    want = f"{key}:true"
    for line in fm.splitlines():
        if line.lower().replace(" ", "").startswith(want):
            return True
    return False


def is_auto_generated(fm: str) -> bool:
    """Pages regenerated by a script (auto_generated: true) carry engine-emitted
    markers (confidence stamps, 'placeholder', etc.) that are NOT human drift —
    skip them like log.md."""
    return fm_flag(fm, "auto_generated")


def is_tracker(fm: str) -> bool:
    """Pages that declare themselves open-item trackers (tracker_page: true)."""
    return fm_flag(fm, "tracker_page")


def lines_outside_fences(text: str) -> list[str]:
    """Blank fence delimiters and content, retaining every original line slot.

    A fence starts with >= 3 backticks or tildes after leading whitespace.
    It closes only with the same character, at least the opening run length,
    and whitespace after the run. An unclosed fence hides the remaining text.
    Keep the scanners' existing leading-whitespace tolerance; this is not a
    Markdown parser for list/quote nesting or indented code blocks.
    """
    out: list[str] = []
    fence_char = ""
    fence_length = 0
    for line in text.splitlines():
        m = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence_char:
            if (m and m[1][0] == fence_char and len(m[1]) >= fence_length
                    and not m[2].strip()):
                fence_char = ""
            out.append("")
        elif m and (m[1][0] == "~" or "`" not in m[2]):
            fence_char, fence_length = m[1][0], len(m[1])
            out.append("")
        else:
            out.append(line)
    return out


def scan_markers(text: str) -> list[tuple[int, str, str]]:
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines_outside_fences(text), 1):
        # Skip lines already marked resolved: strikethrough (~~...~~, the wiki's
        # "superseded claim" convention) or a checked box ([x]). These re-quote
        # an old marker for the record and are not live drift.
        if "~~" in line or re.match(r"\s*-?\s*\[x\]", line, re.I):
            continue
        for label, rx in MARKERS:
            if rx.search(line):
                s = line.strip()
                hits.append((i, label, s if len(s) <= 110 else s[:107] + "..."))
    return hits


def scan_unverifiable(text: str) -> list[tuple[int, str, bool]]:
    """Terminal `*(unverifiable: <slug>)*` labels on one page.

    Returns (lineno, reason_as_written, is_valid). Same line filters as
    scan_markers -- fenced code, ~~struck~~ and checked [x] lines are exempt,
    because those re-quote a label for the record rather than applying one;
    `inline code` is stripped for the same reason. Placeholder reasons
    (`<reason>`, `<slug>`) count as neither valid nor invalid and are dropped:
    they are documentation, not a live label.

    LABEL SHAPE. A parenthetical counts as a label only if it LOOKS like one --
    italic-wrapped as the convention writes it (`*(unverifiable: x)*`, which
    also catches the reason-less near-miss `*(unverifiable)*`), or carrying a
    whitespace-free slug-shaped reason (`(unverifiable: could-not-find)`, the
    italics forgotten). Anything else is ordinary prose that happens to use the
    word inside brackets -- "disputed (unverifiable without the parish
    register)" -- and is left alone. Policing prose would hand every vault a
    permanent close-out nag with no correct fix but rewording the sentence."""
    hits: list[tuple[int, str, bool]] = []
    for i, line in enumerate(lines_outside_fences(text), 1):
        if "~~" in line or re.match(r"\s*-?\s*\[x\]", line, re.I):
            continue
        for m in UNVERIFIABLE_RE.finditer(INLINE_CODE_RE.sub(" ", line)):
            reason = m[2].strip()
            italic = bool(m[1]) and bool(m[3])
            if not italic and (not reason or re.search(r"\s", reason)):
                continue  # prose, not a label -- see LABEL SHAPE above
            if PLACEHOLDER_REASON_RE.match(reason):
                continue
            hits.append((i, reason, reason.lower() in UNVERIFIABLE_SLUGS))
    return hits


def page_key(path: str) -> str:
    """The identity a downgrade log entry names a page by: forward slashes,
    no `[[wikilink]]` brackets, no leading `./`, no `wiki/` prefix, no `.md`,
    case-folded (the vaults live on case-insensitive filesystems, so a
    case-only mismatch is not a different page)."""
    k = path.strip().replace("\\", "/").strip("`")
    if k.startswith("[[") and k.endswith("]]"):
        k = k[2:-2].strip()
    while k.startswith("./"):
        k = k[2:]
    k = k.lstrip("/")
    if k.startswith("wiki/"):
        k = k[5:]
    if k.lower().endswith(".md"):
        k = k[:-3]
    return k.casefold()


def downgrade_log_entries(log_text: str) -> set[tuple[str, str]]:
    """(page_key, slug) for every downgrade heading in wiki/log.md. Headings
    inside a ``` or ~~~ fence are skipped (a quoted example; the fence closes
    only on its own opener character), and either field being a
    `<placeholder>` is documentation, not an entry."""
    out: set[tuple[str, str]] = set()
    for line in lines_outside_fences(log_text):
        m = DOWNGRADE_LOG_RE.match(line.rstrip("\r"))
        if not m:
            continue
        path, slug = m[1].strip(), m[2].strip().strip("`*")
        if PLACEHOLDER_REASON_RE.match(path) or PLACEHOLDER_REASON_RE.match(slug):
            continue
        out.add((page_key(path), slug.lower()))
    return out


def unlogged_labels(labeled: list[tuple[str, int, str]],
                    log_text: str) -> list[tuple[str, int, str]]:
    """The valid labels (page, lineno, slug) with no matching downgrade entry
    in `log_text`. Matched on page + slug only -- never on the line number."""
    logged = downgrade_log_entries(log_text)
    return sorted((p, n, s) for p, n, s in labeled if (page_key(p), s.lower()) not in logged)


def _fm_values(fm: str, key: str) -> list[str] | None:
    """Raw values of one frontmatter key: None when the key is absent, [] when
    present but empty. Handles `key: v`, `key: [a, b]` and a block list of
    `- v` lines. Each value is normalized: a trailing ` # comment` (the
    convention's own YAML example carries one), quotes, CR and case dropped."""
    lines = fm.splitlines()
    for i, line in enumerate(lines):
        m = re.match(rf"^{re.escape(key)}\s*:(.*)$", line.rstrip("\r"))
        if not m:
            continue
        raw = re.sub(r"\s+#.*$", "", m[1]).strip()
        if raw.startswith("[") and raw.endswith("]"):
            vals = raw[1:-1].split(",")
        elif raw:
            vals = [raw]
        else:
            vals = []
            for nxt in lines[i + 1:]:
                # column-0 `- v` is valid YAML too, so the indent is optional
                bm = re.match(r"^\s*-\s+(.*)$", nxt.rstrip("\r"))
                if not bm:
                    break
                vals.append(re.sub(r"\s+#.*$", "", bm[1]))
        return [v.strip().strip("\"'").strip().lower() for v in vals if v.strip().strip("\"'").strip()]
    return None


def legacy_kind(label: str) -> str | None:
    """The canonical transcript_kind a /watch `transcript_source` label maps to
    (the compat map in _meta/fleet-conventions.md), or None if unmappable.
    Prefix match, most specific first: `captions (manual)` must never fall
    through to the bare-`captions` row, and bare `captions` is exact-only."""
    for prefix, kind in TRANSCRIPT_COMPAT:
        if label.startswith(prefix):
            return kind
    return "auto-captions" if label == "captions" else None


def transcript_triggers(fm: str, text: str) -> list[str]:
    """Why a wiki/sources/ page counts as transcript-derived ([] = it does
    not). Frontmatter only for the path/label triggers -- a book page that
    mentions `raw/watched/` in its BODY (how the book entered the vault) is
    not a transcript page. Timestamps are counted in the body outside fences;
    inline code is KEPT, because some vaults write locators as `[05:14]`."""
    why: list[str] = []
    if _fm_values(fm, "transcript_kind") is not None:
        why.append("transcript_kind")
    if "raw/watched/" in fm:
        why.append("raw/watched")
    if _fm_values(fm, "transcript_source") is not None:
        why.append("transcript_source")
    tags = _fm_values(fm, "tags") or []
    hit = sorted({t for t in tags if t in TRANSCRIPT_TAGS})
    if hit:
        why.append("tag " + "/".join(hit))
    stamps: set[str] = set()
    for line in lines_outside_fences(text[len(fm):]):
        stamps.update(TIMESTAMP_RE.findall(line))
    if len(stamps) >= TIMESTAMP_MIN:
        why.append(f"{len(stamps)} timestamps")
    return why


def transcript_findings(fm: str) -> list[tuple[str, str]]:
    """(kind, detail) problems with a transcript page's transcript_kind:
    MISSING / INVALID / LEGACY. [] = compliant."""
    kinds = _fm_values(fm, "transcript_kind")
    if not kinds:
        srcs = _fm_values(fm, "transcript_source") or []
        if srcs:
            fix = legacy_kind(srcs[0])
            if fix:
                return [("MISSING", f"add `transcript_kind: {fix}` (from transcript_source "
                                    f"'{srcs[0]}')")]
            return [("MISSING", f"transcript_source '{srcs[0]}' is not in the compat map -- "
                                "derive the kind from the artifact; unknown provenance = "
                                "auto-captions")]
        return [("MISSING", "derive the kind from the transcript artifact itself; unknown "
                            "provenance = auto-captions")]
    out: list[tuple[str, str]] = []
    for k in kinds:
        if k in TRANSCRIPT_KINDS:
            continue
        fix = legacy_kind(k)
        if fix:
            out.append(("LEGACY", f"'{k}' -> migrate to {fix}"))
        else:
            out.append(("INVALID", f"'{k}' is not a transcript_kind"))
    return out


def missing_scaffold(required: tuple[str, ...] = REQUIRED_SCAFFOLD) -> list[str]:
    """Required instance-owned files absent from the vault root (cwd-relative,
    like every other path this script reads). Report only -- never create."""
    return [p for p in required if not os.path.exists(p)]


def ledger_items(text: str) -> list[tuple[str, dt.date | None, str]]:
    items: list[tuple[str, dt.date | None, str]] = []
    for line in lines_outside_fences(text):
        m = re.match(r"\s*-\s*\[([ xX~])\]\s+(.*)", line)
        if not m:
            continue
        status = m[1].lower()
        body = m[2].strip()
        trig = None
        tm = re.search(r"trigger:\s*(\d{4}-\d{2}-\d{2})", body, re.I)
        if tm:
            d = DATE_RE.search(tm[1])
            if d:
                try:
                    trig = dt.date(int(d[1]), int(d[2]), int(d[3]))
                except ValueError:
                    trig = None
        items.append((status, trig, body))
    return items


CLAIM_AUDIT_TOKEN_RE = re.compile(r"claim[ -]audit|audit_claims", re.I)
# A declaration, not a mention: the line must START with it (list marker /
# quote / bold allowed) and carry a real reason, so documentation that quotes
# the form in prose does not exempt the vault.
CLAIM_AUDIT_EXEMPT_RE = re.compile(
    r"^\s*(?:[-*+>]\s*)*\**(?:claim[ -]audit|audit_claims):\s*exempt\**\s*(?:--|—|-|:|\()\s*(.+)",
    re.I)
# Fleet-synced / pattern docs are never a vault's own declaration: a line in
# one of these would exempt every vault at once.
EXEMPT_SKIP_FILES = {"_meta/fleet-conventions.md", "_meta/book-scanning.md",
                     "_meta/llm-wiki.md", "_meta/obsidian-syntax.md",
                     "_meta/retrieval-layer.md"}
# Section B3 skips the same files for a DIFFERENT reason: they spell the
# terminal label out by definition, so a hit in one of them is documentation,
# not a live downgrade. Given its own name rather than borrowed silently -- if
# EXEMPT_SKIP_FILES is ever re-scoped for the claim-audit check alone, B3 keeps
# a list of its own instead of quietly losing its guard (regression R57).
LABEL_SKIP_FILES = set(EXEMPT_SKIP_FILES)
LIST_ITEM_RE = re.compile(r"^\s*(>\s*)*([-*+]|\d+[.)])\s")
BLOCK_BOUNDARY_RE = re.compile(r"^\s*$|^\s*#")


def stamp_block(lines: list[str], i: int) -> str:
    """The list item a stamp line belongs to (same rule as
    maintenance_preflight.stamp_block): back to the list marker, stopping at a
    blank/heading; a stamp outside a list item is judged on its own line."""
    j = i
    while j > 0:
        if LIST_ITEM_RE.match(lines[j]):
            return "\n".join(lines[j:i + 1])
        if BLOCK_BOUNDARY_RE.match(lines[j - 1]):
            break
        j -= 1
    if LIST_ITEM_RE.match(lines[j]):
        return "\n".join(lines[j:i + 1])
    return lines[i]


def claim_audit_status(files: list[str], texts: dict[str, str], today: dt.date) -> str:
    """One line for the end of section D: is the claim audit scheduled here?
    'installed' names the stamp; 'NOT INSTALLED' when no stamp's list item
    names the claim audit; 'EXEMPT -- <reason>' when a line records the
    exemption. An exemption line AND a stamp is a contradiction: reported."""
    stamps: list[tuple[str, int, dt.date]] = []
    exempt: list[tuple[str, int, str]] = []
    ignored: list[tuple[str, int]] = []
    for p in files:
        if p.endswith("log.md"):
            continue
        lines = lines_outside_fences(texts[p])
        for i, line in enumerate(lines):
            ex = CLAIM_AUDIT_EXEMPT_RE.search(line) if p not in EXEMPT_SKIP_FILES else None
            if ex:
                reason = ex[1].strip(" -*`").rstrip(".)")
                # the template's placeholder is not a reason
                if reason and not reason.lower().startswith("<reason>"):
                    exempt.append((p, i + 1, reason))
                else:
                    ignored.append((p, i + 1))
            m = re.search(r"Next due:\s*~?(\d{4})-(\d{2})", line)
            if not m or not CLAIM_AUDIT_TOKEN_RE.search(stamp_block(lines, i)):
                continue
            try:
                stamps.append((p, i + 1, dt.date(int(m[1]), int(m[2]), 1)))
            except ValueError:
                continue
    exempt.sort()
    if stamps and exempt:
        p, n, _ = exempt[0]
        sp, sn, _ = min(stamps, key=lambda t: (t[2], t[0], t[1]))
        return (f"  claim audit: CONFLICT — exempt at {p}:{n} but a stamp is installed at "
                f"{sp}:{sn}; remove one")
    if stamps:
        p, n, due = min(stamps, key=lambda t: (t[2], t[0], t[1]))
        flag = "  <== DUE" if due <= today else ""
        return f"  claim audit: installed [{p}:{n}]  next ~{due.strftime('%Y-%m')}{flag}"
    if exempt:
        p, n, reason = exempt[0]
        return f"  claim audit: EXEMPT — {reason}  [{p}:{n}]"
    if ignored:
        p, n = ignored[0]
        return (f"  claim audit: NOT INSTALLED — exempt line at {p}:{n} IGNORED: replace the "
                "'<reason>' placeholder with the actual reason")
    return ("  claim audit: NOT INSTALLED — no 'Last run / Next due' stamp names the claim "
            "audit; it has never been scheduled here (install block: _meta/open-loops.md, "
            "or record a line 'claim audit: exempt -- <why>' with a real reason)")


def recurring_due(files: list[str], texts: dict[str, str]) -> list[tuple[str, str, dt.date, str | None]]:
    """All 'Next due: ~YYYY-MM' stamps across maintained pages (log.md excluded).
    Returns (file, label, due_first_of_month, last_run_str). Any page can declare
    a recurring task by carrying the stamp — by convention they live on a
    decision-calendar / recurring-items page."""
    out: list[tuple[str, str, dt.date, str | None]] = []
    for p in files:
        if p.endswith("log.md"):
            continue
        for line in lines_outside_fences(texts[p]):
            m = re.search(r"Next due:\s*~?(\d{4})-(\d{2})", line)
            if not m:
                continue
            try:
                due = dt.date(int(m[1]), int(m[2]), 1)
            except ValueError:
                continue
            lr = re.search(r"Last run:\s*(\d{4}-\d{2}-\d{2}|never)", line, re.I)
            label = re.sub(r"[*`>#-]", "", line.split(":", 1)[0]).strip()[:60]
            out.append((p, label or "(unlabeled recurring item)", due, lr[1] if lr else None))
    return out


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Wiki staleness detector (advisory; never fails).")
    ap.add_argument("--days", type=int, default=45,
                    help="Flag central-tagged pages whose `updated:` is older than this (default 45).")
    ap.add_argument("--summary", action="store_true",
                    help="Counts + actionable lines only (OVERDUE / DUE); no per-file marker listings.")
    args = ap.parse_args()

    today = dt.date.today()
    files = md_files(ROOTS)
    texts = {p: read(p) for p in files}
    fms = {p: frontmatter(texts[p]) for p in files}

    # --- A. Open-loops ledger ---
    if os.path.exists(LEDGER):
        items = ledger_items(read(LEDGER))
        open_items = [it for it in items if it[0] in (" ", "~")]
        overdue_items = [(s, t, b) for s, t, b in open_items if t and t < today]
        print(f"=== A. OPEN-LOOPS LEDGER === {len(open_items)} open / {len(overdue_items)} overdue  ({LEDGER})")
        shown = overdue_items if args.summary else open_items
        for status, trig, body in shown:
            flag = "  <== OVERDUE" if (trig and trig < today) else ""
            tg = f" [trigger {trig.isoformat()}]" if trig else " [no trigger date]"
            print(f"    [{status}]{tg}{flag}")
            print(f"        {body[:104]}")
        if overdue_items:
            # regression R74: closing a loop in prose does not close it here.
            print("    (a loop is closed by flipping its marker to [x] -- this section reads the marker")
            print("     and nothing else, so a dated CLOSED note on a [ ] or [~] line is still open)")
    else:
        print("=== A. OPEN-LOOPS LEDGER ===")
        print(f"  (no ledger at {LEDGER} — create it; see the CLAUDE.md session close-out protocol)")
    print()

    # --- B. Time-sensitive markers ---
    # log.md is an append-only historical record — it will always contain every
    # marker phrase (describing past states), so scanning it is pure noise.
    trackers = TRACKER_PAGES | {p for p in files if is_tracker(fms[p])}
    auto_gen = {p for p in files if is_auto_generated(fms[p])}
    # The template's pattern/reference docs DESCRIBE the marker conventions —
    # scanning them is self-referential noise in every instance.
    pattern_docs = {"_meta/llm-wiki.md", "_meta/obsidian-syntax.md", "_meta/retrieval-layer.md"}
    marker_skip = trackers | auto_gen | pattern_docs | {LEDGER, "wiki/log.md"}
    content_hits = {p: h for p in files if p not in marker_skip
                    for h in [scan_markers(texts[p])] if h}
    tracker_hits = {p: scan_markers(texts[p]) for p in files if p in trackers}
    tracker_hits = {p: h for p, h in tracker_hits.items() if h}

    total = sum(len(v) for v in content_hits.values())
    print(f"=== B. TIME-SENSITIVE MARKERS — content pages === {total} hits in {len(content_hits)} files")
    if not args.summary:
        print("    (each is meant to flip once its decision/ingest/answer lands — confirm each is still true)")
        for p in sorted(content_hits):
            print(f"  {p}")
            for lineno, label, snippet in content_hits[p]:
                print(f"    {lineno:>4} [{label}] {snippet}")
    print()

    tracker_total = sum(len(v) for v in tracker_hits.values())
    print(f"=== B2. MARKERS on tracker pages (open items EXPECTED here — low signal) === {tracker_total} hits")
    if not args.summary:
        for p in sorted(tracker_hits):
            print(f"  {p}  x{len(tracker_hits[p])}")
    print()

    # --- B3. Terminal *(unverifiable: <slug>)* labels ---
    # The convention docs spell the label out, and log.md records past
    # downgrades verbatim; neither is a live label, so both are skipped. Every
    # other page IS validated -- an invented slug is wrong on a tracker or
    # auto-generated page too, unlike an "expected open item" marker.
    # basename, not endswith: a `changelog.md` / `backlog.md` page is ordinary
    # content and must still be validated (the two older call sites above use
    # the looser endswith idiom; not touched here).
    label_skip = LABEL_SKIP_FILES | {p for p in files
                                     if os.path.basename(p) == "log.md"}
    labeled: list[tuple[str, int, str]] = []
    invalid: list[tuple[str, int, str]] = []
    for p in files:
        if p in label_skip:
            continue
        for lineno, reason, ok in scan_unverifiable(texts[p]):
            if ok:
                labeled.append((p, lineno, reason))
            else:
                invalid.append((p, lineno, reason))
    valid_n = len(labeled)
    invalid.sort()
    # regression R99: every valid label needs its downgrade entry in wiki/log.md
    # (the story behind the slug lives there and nowhere else). Read directly,
    # not via `texts`: log.md is in `files` today, but this must keep working
    # if it is ever skipped from the scan, and a missing log.md reads as "".
    unlogged = unlogged_labels(labeled, read(DOWNGRADE_LOG_PATH))
    # UNLOGGED is appended AFTER INVALID so a parser matching the older
    # `N valid / M INVALID` prefix (maintenance_preflight, regression R109) keeps
    # matching.
    print(f"=== B3. TERMINAL *(unverifiable:)* LABELS === {valid_n} valid / {len(invalid)} INVALID"
          f" / {len(unlogged)} UNLOGGED")
    # Invalid slugs print even under --summary: they are actionable, like
    # OVERDUE and DUE, and this is the only channel that will ever mention them.
    for p, lineno, reason in invalid:
        shown = reason if reason else "(no reason given)"
        print(f"  {p}:{lineno}  INVALID: '{shown}'")
    if invalid:
        print(f"    (closed vocabulary: {' | '.join(UNVERIFIABLE_SLUGS)} -- see the marker-hygiene")
        print("     section of _meta/fleet-conventions.md; the story goes in the log.md entry)")
    # Unlogged labels are hygiene, not validity -- printed under --summary too,
    # never a change to the exit code (advisory, like INVALID).
    for p, lineno, slug in unlogged:
        print(f"  {p}:{lineno}  UNLOGGED: '{slug}' -- no `meta | downgrade` entry in {DOWNGRADE_LOG_PATH}")
    if unlogged:
        print(f"    (a logged downgrade is a {DOWNGRADE_LOG_PATH} heading")
        print("     `## [YYYY-MM-DD] meta | downgrade | <page path> | <slug>` naming the page and the")
        print("     slug, with the claim and the concrete reason in prose below it -- see the")
        print("     marker-hygiene section of _meta/fleet-conventions.md)")
    print()

    # --- C. Central-page age ---
    aged: list[tuple[int | None, str]] = []
    for p in files:
        fm = fms[p]
        if not fm or not is_central(fm):
            continue
        u = parse_updated(fm)
        if u is None:
            aged.append((None, p))
        elif (today - u).days > args.days:
            aged.append(((today - u).days, p))
    aged.sort(key=lambda t: (0 if t[0] is None else 1, -(t[0] or 0)))
    print(f"=== C. CENTRAL PAGES not updated in > {args.days} days === {len(aged)}")
    if not args.summary:
        for age, p in aged:
            print(f"  {'no-date' if age is None else str(age) + 'd':>8}  {p}")
    print()

    # --- D. Recurring items due ---
    rec = recurring_due(files, texts)
    due_now = [r for r in rec if r[2] <= today]
    print(f"=== D. RECURRING ITEMS ('Next due:' stamps) === {len(rec)} tracked / {len(due_now)} DUE")
    for p, label, due, last in sorted(rec, key=lambda t: t[2]):
        if args.summary and due > today:
            continue
        flag = "  <== DUE — run it this session or re-stamp the date" if due <= today else ""
        print(f"  next ~{due.strftime('%Y-%m')}  (last run: {last or '?'})  {label}  [{p}]{flag}")
    print(claim_audit_status(files, texts, today))
    print()

    # --- E. Instance scaffold ---
    gaps = missing_scaffold()
    print(f"=== E. INSTANCE SCAFFOLD === {len(REQUIRED_SCAFFOLD)} required / {len(gaps)} missing")
    # Printed under --summary too: a missing scaffold file is actionable, and
    # the token is deliberately NOT sync_from_template's bare "MISSING : " so a
    # close-out log never conflates the two.
    for p in gaps:
        print(f"  MISSING SCAFFOLD : {p}")
    if gaps:
        print("    (instance-owned -- sync_from_template.py never ships these. Bootstrap each by")
        print("     hand from the template, then adapt it to this vault's topic.)")
    print()

    # --- F. Transcript provenance (regression R45) ---
    tpages: dict[str, list[str]] = {}
    tfound: list[tuple[str, str, str, list[str]]] = []
    for p in sorted(files):
        if not p.startswith(SOURCES_DIR):
            continue
        why = transcript_triggers(fms[p], texts[p])
        if not why:
            continue
        tpages[p] = why
        for kind, detail in transcript_findings(fms[p]):
            tfound.append((p, kind, detail, why))
    n_by = {k: sum(1 for _, kk, _, _ in tfound if kk == k) for k in ("MISSING", "INVALID", "LEGACY")}
    print(f"=== F. TRANSCRIPT PROVENANCE === {len(tpages)} transcript pages / "
          f"{n_by['MISSING']} missing / {n_by['INVALID']} invalid / {n_by['LEGACY']} legacy")
    # Actionable, so printed under --summary too (like B3 INVALID and E gaps).
    for p, kind, detail, why in tfound:
        label = {"MISSING": "MISSING transcript_kind", "INVALID": "INVALID transcript_kind",
                 "LEGACY": "LEGACY LABEL"}[kind]
        print(f"  {p}  {label}: {detail}  [{', '.join(why)}]")
    if tfound:
        print(f"    (closed vocabulary: {' | '.join(TRANSCRIPT_KINDS)}; legacy /watch labels")
        print("     are MIGRATED, never grandfathered -- see 'Transcript-derived sources' in")
        print("     _meta/fleet-conventions.md. transcript_source keeps its verbatim label.)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
