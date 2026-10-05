#!/usr/bin/env python
"""Vault wikilink fixer. Two modes.

DEFAULT mode -- one-shot fixup: strip backticks wrapping [[wikilinks]].

Obsidian doesn't parse wikilinks inside code spans (backticks). Earlier
in the project, every wikilink was written as `[[foo]]` (in backticks for
visual emphasis), which silently broke all graph connections.

This mode replaces `[[...]]` with [[...]] across .md files in:
- wiki/
- _meta/
- CLAUDE.md

--alias-only mode (regression R151) -- rewrite links whose target is only a
frontmatter ALIAS of a page. Obsidian does not resolve a bare [[alias]] link
(clicking one creates a new empty note), so such a link is grey in Obsidian
even though older lint versions counted it resolved. A link whose alias names
exactly one page is rewritten to [[slug|Alias]]; every other case (ambiguous
alias, an alias that is also some file's name -- 0-byte stub or not --,
unsupported link form, slug that cannot be spelled as a link) goes to a
review queue and is never rewritten. No guessing: only a destination proven
unique by exact (case-insensitive) name equality is automated; there is no
similarity or closest-name matching anywhere in this script. A #heading or
#^block fragment is split off before the alias lookup and re-attached
unchanged to the canonical slug. On a line inside a real GFM table block
the rewritten link's separator is always written '\\|' -- even when the
original link had a bare '|', which splits the cell in Obsidian and so
kept the link broken; outside tables an existing separator is kept as
written and a new one is a plain '|'.

Every alias-only dry run / --verify prints three counts on their own lines:
  SAFE_REWRITES = N    uniquely determined rewrites this tool will make
  REVIEW_REQUIRED = N  links a human must decide (the manifest's review queue)
  EXEMPT = N           alias-only links in excluded files (wiki/log.md,
                       append_only / auto_generated pages), broken out per
                       file as 'EXEMPT <file> = N (lint ALIAS-ONLY a, ...)'
REVIEW_REQUIRED and EXEMPT are legitimate end states, NOT failures: review
items need a human decision, and EXEMPT is known historical debt in files
that are never mutated. They must never be forced to zero, and neither takes
part in any exit code or reconciliation: --verify exits 0 iff SAFE_REWRITES
= 0. 'Nothing to
do' is printed only as 'Nothing to do: nothing safely auto-remediable
remains (review-only findings may remain)'.
Links inside frontmatter, fenced code (``` and ~~~; opener at any indent or
inside callouts, closed only at the same quote depth and <= 3 columns more
indent -- see mask_text), indented code blocks, raw HTML <pre>/<script>/
<style>/<textarea> blocks and inline code are never touched; the ones lint
still counts are listed for review ('in-code' / 'in-frontmatter'), as are
path-form alias links and alias links in scripts/*.md (lint-scanned, never
rewritten). NOT protected: other raw HTML blocks (<div> etc.) and inline HTML.
Workflow: dry run (writes a manifest) -> --apply (applies only files
unchanged since the dry run, in backed-up batches with postconditions, then
reconciles against lint.py) -> --verify (exit 0 iff no safe rewrite is
left). The first apply in a vault is a smoke batch: --apply --max-files N
writes only the first N applicable files in manifest order (apply-log
result 'partial', settled; RECONCILE adds links_deferred), then a human
clicks 2-3 rewritten links in Obsidian, then a fresh dry run and a full
--apply finish the rest. --restore RUN_ID puts back the originals of every apply of that run
(all apply-log*.json), but only for files still exactly as an apply wrote
them; --apply refuses (exit 2) while any earlier apply -- of this run or
of another run_id -- did not complete and still has writes in the vault,
until it is restored (the dry run prints a WARNING with its RESTORE command;
--verify exits 1). A link lint already resolves to a page
is never rewritten (review 'lint-canonical'), nor is a link whose rewritten
target lint would file as BROKEN or GHOST -- a dotted slug (regression R148), a '/' or
':' slug (regression R149), or a page named date / published (review
'lint-mangled-slug'). The modes are mutually
explicit: --alias-only never runs the backtick pass, the default mode never
rewrites alias links, and --manifest / --batch-size / --max-files /
--verify / --restore without --alias-only are usage errors (--max-files
also needs --apply and N >= 1).

Both modes skip wiki/log.md, append_only and auto_generated pages, reporting
each reason. Uses maintenance_preflight's edit-deny resolver (or an announced
standalone fallback).

Run:
    python scripts/fix_wikilinks.py            # dry-run, show counts only
    python scripts/fix_wikilinks.py --apply    # actually make the edits
    python scripts/fix_wikilinks.py --root PATH  # explicitly target another vault
    python scripts/fix_wikilinks.py --alias-only --root PATH            # plan + manifest
    python scripts/fix_wikilinks.py --alias-only --root PATH --apply --max-files 10  # smoke batch
    python scripts/fix_wikilinks.py --alias-only --root PATH --apply    # apply the manifest
    python scripts/fix_wikilinks.py --alias-only --root PATH --verify   # residual check
    python scripts/fix_wikilinks.py --alias-only --root PATH --restore RUN_ID  # undo an apply
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
try:
    from maintenance_preflight import edit_deny, fm_flag_true
except ImportError:
    edit_deny = None

    def fm_flag_true(path: Path, key: str) -> bool | None:
        # Standalone copy: retain the resolver's conservative flag semantics.
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        end = text.find("\n---", 3) if text.startswith("---") else -1
        if end == -1:
            return False
        return any(line.lower().replace(" ", "").startswith(key + ":true")
                   for line in text[:end].splitlines())

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PATTERN = re.compile(r"`(\[\[[^\]]+\]\])`")


def collect_md_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for t in (root / "wiki", root / "_meta", root / "CLAUDE.md"):
        if t.is_file() and t.suffix == ".md":
            files.append(t)
        elif t.is_dir():
            files.extend(p for p in t.rglob("*.md") if p.is_file())
    return sorted(files)


SKIP_REASONS = ("path", "append_only", "auto_generated", "unreadable", "edit_deny")


def resolve_exclusions(root: Path) -> dict:
    """Announce the edit-deny source and return {resolved path: reason}."""
    denied = edit_deny(root) if edit_deny is not None else None
    if denied is None:
        print("Edit-deny: standalone fallback (maintenance_preflight import unavailable).")
    else:
        print("Edit-deny: maintenance_preflight resolver + auto_generated protection.")
    # Path keys retain Windows case-insensitive identity (e.g. wiki/LOG.md).
    denied_reasons = {}
    if denied:
        for reason, paths in (("edit_deny", denied["paths"]),
                              ("append_only", denied["by_rule"]["frontmatter"]),
                              ("unreadable", denied["unreadable"]),
                              ("path", denied["by_rule"]["path"])):
            denied_reasons.update({(root / p).resolve(): reason for p in paths})
    denied_reasons[(root / "wiki/log.md").resolve()] = "path"
    return denied_reasons


def skip_reason(f: Path, denied_reasons: dict) -> str | None:
    reason = denied_reasons.get(f.resolve())
    if reason is None:
        for flag in ("append_only", "auto_generated"):
            value = fm_flag_true(f, flag)
            if value is None or value:
                reason = "unreadable" if value is None else flag
                break
    return reason


def run_backtick(args, root: Path) -> int:
    files = collect_md_files(root)
    denied_reasons = resolve_exclusions(root)
    skipped = dict.fromkeys(SKIP_REASONS, 0)
    total_replacements = 0
    files_changed = 0

    for f in files:
        rel = f.relative_to(root).as_posix()
        reason = skip_reason(f, denied_reasons)
        if reason is not None:
            skipped[reason] += 1
            continue
        text = f.read_text(encoding="utf-8")
        new_text, n = PATTERN.subn(r"\1", text)
        if n > 0:
            files_changed += 1
            total_replacements += n
            print(f"  {n:4d}  {rel}")
            if args.apply:
                f.write_text(new_text, encoding="utf-8")

    print()
    print(f"Skipped: {sum(skipped.values())} files (" +
          ", ".join(f"{reason}={count}" for reason, count in skipped.items()) + ").")
    if total_replacements == 0:
        print("No backtick-wrapped wikilinks found in eligible files. Nothing to do.")
        return 0

    print(f"Total: {total_replacements} replacements across {files_changed} files.")
    if args.apply:
        print("Applied.")
    else:
        print("(Dry run. Re-run with --apply to write changes.)")
    return 0


# ---------------------------------------------------------------------------
# --alias-only mode (regression R151)
# ---------------------------------------------------------------------------

# A "real" asset extension: 1-5 alphanumerics with at least one letter. A
# dotted note name ("... 9th ed., 2017)", "Mass selection vs. family
# selection", "Python 3.12") is NOT an attachment.
ASSET_EXT_RE = re.compile(r"\.(?=[A-Za-z0-9]{0,4}[A-Za-z])[A-Za-z0-9]{1,5}$")
# A backtick in a slug would pair with a later backtick in the paragraph and
# turn the rewritten link into a code span (regression R151 review 3-R3-3).
UNSAFE_SLUG_CHARS = set("[]|#^\\`\r\n")
LINT_HIDDEN_REASON = ("lint-hidden (lint's code-strip masks this link, e.g. it pairs a "
                      "mid-line ``` as a fence; Obsidian shows it)")
# Any indentation: _wikilib.strip_code (lint) masks a ``` fence wherever it
# sits, so a list-nested fence must be masked here too.
FENCE_OPEN_RE = re.compile(r"\s*(`{3,}|~{3,})")
# Leading blockquote / callout markers ("> ", "> > "), stripped before the
# fence and table tests so a fence or table inside a callout is recognized.
BQ_RE = re.compile(r"^(\s*>\s?)+")
# CommonMark type-1 raw HTML blocks: rendered literally, closed by the line
# holding the matching end tag.
HTML_BLOCK_OPEN_RE = re.compile(r" {0,3}<(pre|script|style|textarea)(?=[\s>]|$)", re.I)
ATX_HEADING_RE = re.compile(r" {0,3}#{1,6}(?:[ \t]|$)")
# A list item marker and the gap after it (group 3; empty = item at EOL).
LIST_ITEM_RE = re.compile(r"([ \t]*)([-*+]|\d{1,9}[.)])([ \t]+|$)")
# CommonMark thematic break (***, - - -, ___) and setext heading underline
# (=== / --- right under a paragraph line). Both end a paragraph.
THEMATIC_BREAK_RE = re.compile(r" {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
SETEXT_UNDERLINE_RE = re.compile(r" {0,3}(?:=+|-+)[ \t]*$")
MASK = "\x00"
# TEST-ONLY fault hook, honored only when --root is under the temp dir:
#   <rel>         append a broken link to that file's write (broken rises)
#   revert:<rel>  leave that file's original bytes in place (alias drop short)
# so the batch postconditions can be exercised. Never set in real use.
FAULT_ENV = "FIX_WIKILINKS_TEST_FAULT"
PATH_FORM_REASON = "path-form (a path Obsidian cannot follow to the alias holder)"
OUT_OF_SCOPE_REASON = "out-of-scope file (scripts/ is lint-scanned, never rewritten)"
# A link that exists only after lint's code-strip DELETES a span (e.g.
# [[Soil p`x`H]] -> [[Soil pH]]): lint counts it, nothing here can rewrite it
# (regression R151 review h2-I9-strip-join-invisible-alias).
STRIP_JOIN_REASON = "lint-only (code-strip join)"
TMP_SUFFIX = ".tmp-alias-fix"
LINK_CATEGORIES = ("canonical", "alias-unique", "alias-unique-held", "alias-ambiguous",
                   "stub-collision", "name-collision", "broken", "attachment")
# Review categories lint does NOT count as alias-only (it opens the file of
# that name), so they never enter the lint-parity sums.
COLLISION_CATEGORIES = ("stub-collision", "name-collision")
MANIFEST_SCHEMA = 2
APPLY_LOG_SCHEMA = 1
# Apply-log results that leave no writes of that apply to guard: it finished,
# a deliberate --max-files smoke batch finished (its remaining files are
# deferred, not failed), or a clean --restore put every file back (review
# t1-DS1-a).
SETTLED_RESULTS = ("complete", "partial", "restored")
# Results whose writes are legitimate (a finished apply, full or smoke).
WRITER_RESULTS = ("complete", "partial")
RUN_ID_RE = re.compile(r"[0-9a-f]{16}")
NOTHING_TO_DO = ("Nothing to do: nothing safely auto-remediable remains "
                 "(review-only findings may remain)")
# lint_kind recorded on a review item lint's code-strip masks: lint never
# sees the link, so it is in no lint section.
LINT_MASKED = "masked"
# lint verdicts under which an alias-unique link may be rewritten: ALIAS-ONLY
# (the R151 case) and BROKEN (a dotted / '/' / ':' alias lint mangles, R148 /
# R149 -- grey in Obsidian all the same). "unknown" = no lint verdict supplied
# (recount's postcondition re-plan). Anything else -- above all "canonical",
# a link lint already resolves to a page -- goes to review (arb-I9-1).
REWRITABLE_LINT_KINDS = ("alias-only", "broken", "unknown")
LINT_CANONICAL_REASON = ("lint-canonical (lint.py already resolves this link to a page; "
                         "rewriting would redirect it)")
# GFM table shape (review g1-C4-header-row-breaks-table / g1-R1-C4-display-
# inner-pipe / arb-M2): a rewrite must never make, unmake or reshape a table.
TABLE_HEADER_REASON = ("table-header-bare-pipe (escaping the separator on a table header row "
                       "would change its cell count and unmake the table)")
TABLE_DISPLAY_PIPE_REASON = "odd-form (display contains a bare pipe inside a table)"
TABLE_STRUCTURE_REASON = ("table-structure-changed (the file's planned rewrites would change "
                          "which lines are GFM table rows; whole file held)")
# The slug itself is a name lint mangles: the rewritten [[slug|Alias]] would
# be filed by lint as BROKEN (regression R148 dotted, R149 '/' ':') or GHOST (date /
# published) instead of canonical (review h3-R3-I9-dotted-slug).
LINT_MANGLED_SLUG_REASON = ("lint-mangled-slug (regression R148/R149/ghost: lint would file the rewritten "
                            "link as {kind})")


def _wikilib():
    """Imported lazily: the default mode must keep working from a bare
    scripts/ copy that has no _wikilib.py (test_fix_wikilinks_deny)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import _wikilib
    except (ImportError, SyntaxError) as e:
        sys.exit(f"--alias-only needs an importable scripts/_wikilib.py -- run "
                 f"python scripts/sync_from_template.py --apply. Import failed: {e}")
    if not hasattr(_wikilib, "lint_link_kind"):
        sys.exit("--alias-only needs a scripts/_wikilib.py with lint_link_kind (regression R151) -- "
                 "run python scripts/sync_from_template.py --apply.")
    return _wikilib


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _under_tempdir(root: Path) -> bool:
    """True when `root` lies under the system temp dir -- the only place the
    destructive test fault hook may act (a fleet vault never lives there)."""
    import os
    import tempfile
    tmp = os.path.normcase(str(Path(tempfile.gettempdir()).resolve()))
    r = os.path.normcase(str(Path(root).resolve()))
    try:
        return os.path.commonpath([tmp, r]) == tmp
    except ValueError:  # different drives on Windows
        return False


def _decode(raw: bytes) -> tuple[bool, str]:
    """(had_bom, text). Strict utf-8; raises UnicodeDecodeError."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return True, raw[3:].decode("utf-8")
    return False, raw.decode("utf-8")


def _encode(bom: bool, text: str) -> bytes:
    return (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")


def _lint_is_auto_generated(text: str) -> bool:
    # Mirrors lint.py's own copy exactly (see the NOTE there): the _meta/
    # exclusion must match lint's file set, not _wikilib.fm_flag's.
    if not text.startswith("---"):
        return False
    fm = text[:text.find("\n---", 3)]
    return any(l.lower().replace(" ", "").startswith("auto_generated:true")
               for l in fm.splitlines())


class Inventory:
    """What a link target can resolve to.

    Canonical names are VAULT-WIDE (every .md outside dot-directories,
    raw/ included): Obsidian resolves [[name]] against any file in the vault,
    so a name that is some file's basename is not grey and must never be
    redirected to an alias page. This is a deliberate superset of lint.py's
    inventory. Aliases come from lint's file set only (wiki/, _meta/ minus
    auto_generated, scripts/, CLAUDE.md).
    """

    def __init__(self, root: Path, wl):
        self.root = root
        self.md_by_base: dict[str, list[str]] = {}
        self.md_paths: set[str] = set()
        self.md_size: dict[str, int] = {}
        self.asset_names: set[str] = set()
        self.asset_paths: set[str] = set()
        import os
        for d, dirs, fs in os.walk(root):
            dirs[:] = sorted(x for x in dirs if not x.startswith("."))
            for f in fs:
                full = Path(d) / f
                rel = full.relative_to(root).as_posix()
                if f.lower().endswith(".md"):
                    self.md_by_base.setdefault(f[:-3].lower(), []).append(rel)
                    self.md_paths.add(rel[:-3].lower())
                    try:
                        self.md_size[rel] = full.stat().st_size
                    except OSError:
                        self.md_size[rel] = -1
                else:
                    self.asset_names.add(f.lower())
                    self.asset_paths.add(rel.lower())
        lint_files: list[str] = []
        for r in ("wiki", "_meta", "scripts"):
            base = root / r
            if base.is_dir():
                lint_files += sorted(p.relative_to(root).as_posix()
                                     for p in base.rglob("*.md")
                                     # lint's walk is case-sensitive: .MD is an asset
                                     if p.is_file() and p.name.endswith(".md"))
        if (root / "CLAUDE.md").is_file():
            lint_files.append("CLAUDE.md")
        self.alias_index: dict[str, set[str]] = {}
        for rel in lint_files:
            try:
                _, text = _decode((root / rel).read_bytes())
            except (OSError, UnicodeDecodeError):
                continue
            if rel.startswith("_meta/") and _lint_is_auto_generated(text):
                continue
            own = Path(rel).stem.lower()
            for alias in wl.frontmatter_aliases(text):
                a = alias.strip().lower()
                if a and a != own:
                    self.alias_index.setdefault(a, set()).add(rel)

    def canonical_matches(self, key: str, target: str, from_rel: str) -> list[str]:
        """Vault .md files the target names as Obsidian would link it."""
        import os
        if "/" not in key:
            return self.md_by_base.get(key, [])
        hits = [p + ".md" for p in self.md_paths
                if p == key or p.endswith("/" + key)]
        if hits:
            return sorted(hits)
        cand = os.path.normpath(os.path.join(str(self.root), os.path.dirname(from_rel), target))
        for c in (cand, cand + ".md"):
            if os.path.isfile(c) and c.lower().endswith(".md"):
                try:
                    return [Path(c).resolve().relative_to(self.root).as_posix()]
                except ValueError:
                    return ["<outside-vault>"]
        return []

    def is_existing_asset(self, t: str) -> bool:
        low = t.lower()
        base = low.rsplit("/", 1)[-1]
        if low.endswith(".md"):
            return False
        return low in self.asset_paths or low in self.asset_names or (
            "/" in low and base in self.asset_names)

    def slug(self, page_rel: str) -> str:
        base = Path(page_rel).name[:-3]
        if len(self.md_by_base.get(base.lower(), [])) == 1:
            return base
        return page_rel[:-3]


def _body_start(text: str, wl) -> int:
    """Offset of the first character after the frontmatter (0 if none)."""
    fm = wl.frontmatter(text)
    if not fm:
        return 0
    nl = text.find("\n", len(fm) + 1)
    return len(text) if nl == -1 else nl + 1


def _code_spans(seg: str, off: int) -> list[tuple[int, int]]:
    """Inline code spans in one paragraph `seg` (which starts at file offset
    `off`): a backtick run matched by the next equal-length run, newlines
    allowed in between (CommonMark code spans may cross lines within a
    paragraph). An unmatched run is literal text."""
    spans = []
    i = 0
    while i < len(seg):
        if seg[i] != "`":
            i += 1
            continue
        j = i
        while j < len(seg) and seg[j] == "`":
            j += 1
        n = j - i
        k = j
        found = -1
        while k < len(seg):
            if seg[k] == "`":
                r = k
                while r < len(seg) and seg[r] == "`":
                    r += 1
                if r - k == n:
                    found = r
                    break
                k = r
            else:
                k += 1
        if found == -1:
            i = j
        else:
            spans.append((off + i, off + found))
            i = found
    return spans


def _apply_mask(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    out = []
    last = 0
    for a, b in sorted(spans):
        if a < last:
            a = last
        if b <= a:
            continue
        out.append(text[last:a])
        out.append("".join(c if c in "\r\n" else MASK for c in text[a:b]))
        last = b
    out.append(text[last:])
    return "".join(out)


def _indent_width(s: str) -> int:
    """Columns of leading whitespace, tabs to the next multiple of 4."""
    n = 0
    for c in s:
        if c == " ":
            n += 1
        elif c == "\t":
            n += 4 - n % 4
        else:
            break
    return n


def mask_text(text: str, wl) -> str:
    """Length-preserving mask of the regions Obsidian renders as literal text,
    so a link inside them is never rewritten. Masked characters become NUL;
    newlines are kept so offsets and line numbers map 1:1 onto the file.

    Masked (line-based, CommonMark-shaped):
    - frontmatter;
    - fenced code (``` and ~~~). An opener may sit at any indent (a
      list-nested fence) and inside `>` blockquotes/callouts. The fence
      records its quote depth (count of `>` markers) and indent; it closes
      only on a line at the SAME quote depth, with the same fence character,
      a run at least as long as the opener's, at most 3 columns more indent
      than the opener, and nothing after the run. A line whose quote depth
      drops below a quoted fence's ends that fence (the quote ended), and
      the line is then evaluated afresh -- so a fence left open in a callout
      does not swallow, or get closed by, a later unquoted ``` line. A
      backtick run whose info string holds a backtick (```inline```) is
      inline code, not a fence;
    - indented code: a line indented 4+ columns (tab = 4) that does not
      continue an open paragraph (a blank line, an ATX `#` heading, a
      setext underline, a thematic break, a fence or HTML block, or the
      start of the body precede it), and the lines after it that stay at
      that indent (blank lines included). Tested before the fence opener,
      so a 4+-indented ``` after a blank line is indented code. Inside a
      list, the threshold is the last list item's content column + 4, so a
      list-continuation paragraph is not masked;
    - raw HTML blocks <pre>, <script>, <style>, <textarea> (CommonMark type
      1): from the opening line through the line holding the closing tag;
    - inline code: backtick runs matched within one paragraph, so a span
      that opens on one line and closes on the next is masked. A paragraph
      ends at a blank line, a heading, a setext underline, a thematic
      break, a fence / HTML block, and a list item start (a new item, or a
      non-empty bullet / '1.' item interrupting a paragraph), so a stray
      backtick in one block never pairs with one in the next.
    NOT masked: other raw HTML blocks (<div> and friends) and inline HTML.
    The list-indent rule is an approximation (the last item's content
    column, reset by an unindented non-item line, fence, HTML block or
    thematic break).

    plan_text backs this up: a link is rewritten only when lint_view also
    leaves it unmasked, so a block rule missing here cannot put an edit
    inside code that lint strips.

    Errs toward masking: a masked link is never rewritten (at worst it is
    listed for review), while an unmasked code sample would be corrupted."""
    spans: list[tuple[int, int]] = []
    body_start = _body_start(text, wl)
    if body_start:
        spans.append((0, body_start))
    pos = body_start
    # (char, run length, quote depth, indent, start offset) of an open fence
    fence: tuple[str, int, int, int, int] | None = None
    html_end: str | None = None  # closing tag of an open <pre>-type block
    icode: tuple[int, int] | None = None  # (quote depth, indent threshold)
    list_indent: int | None = None  # content column of the last list item
    para: list[tuple[int, int]] = []  # (start, end-without-newline) of paragraph lines

    def flush() -> None:
        if para:
            a, b = para[0][0], para[-1][1]
            spans.extend(_code_spans(text[a:b], a))
            para.clear()

    while pos < len(text):
        nl = text.find("\n", pos)
        end = len(text) if nl == -1 else nl + 1
        line = text[pos:end].rstrip("\r\n")
        line_span = (pos, pos + len(line))
        bq = BQ_RE.match(line)
        depth = bq.group(0).count(">") if bq else 0
        content = line[bq.end():] if bq else line
        ind = _indent_width(content)
        blank = not content.strip()
        m = FENCE_OPEN_RE.match(content)

        if fence is not None:
            if depth < fence[2]:
                # The quote holding the fence ended, and the fence with it.
                # Fall through: this line may itself open a new block.
                spans.append((fence[4], pos))
                fence = None
            else:
                if (depth == fence[2] and m and m.group(1)[0] == fence[0]
                        and len(m.group(1)) >= fence[1] and ind <= fence[3] + 3
                        and not content[m.end():].strip()):
                    spans.append((fence[4], pos + len(line)))
                    fence = None
                pos = end
                continue
        if html_end is not None:
            spans.append(line_span)
            if html_end in line.lower():
                html_end = None
            pos = end
            continue
        if icode is not None:
            if depth == icode[0] and (blank or ind >= icode[1]):
                spans.append(line_span)
                pos = end
                continue
            icode = None
        if blank:
            flush()
            pos = end
            continue
        # Indented code is tested BEFORE the fence opener: a ``` line indented
        # 4+ columns after a blank line is indented code, not a fence
        # (regression R151 review 3-R3-2).
        threshold = 4 if list_indent is None else list_indent + 4
        if not para and ind >= threshold:
            icode = (depth, threshold)
            spans.append(line_span)
            pos = end
            continue
        # A backtick fence's info string may not contain a backtick: such a
        # line (```inline```) is inline code, not a fence (CommonMark).
        if m and not (m.group(1)[0] == "`" and "`" in content[m.end():]):
            flush()
            if ind == 0 and depth == 0:
                list_indent = None  # an unindented fence ends the list
            fence = (m.group(1)[0], len(m.group(1)), depth, ind, pos)
            pos = end
            continue
        hm = HTML_BLOCK_OPEN_RE.match(content)
        if hm:
            flush()
            if ind == 0 and depth == 0:
                list_indent = None  # an unindented HTML block ends the list
            spans.append(line_span)
            close = "</" + hm.group(1).lower() + ">"
            if close not in content.lower():
                html_end = close
            pos = end
            continue
        if ATX_HEADING_RE.match(content):
            # A heading is one line and ends any paragraph, so an indented
            # line right after it is code; its own code spans are masked.
            flush()
            spans.extend(_code_spans(line, pos))
            if ind == 0:
                list_indent = None
            pos = end
            continue
        if (para and SETEXT_UNDERLINE_RE.match(content)) or THEMATIC_BREAK_RE.match(content):
            # A setext underline closes its paragraph (the heading); a
            # thematic break ends any paragraph and, unindented, the list.
            # Neither holds code spans, and inline code never pairs across
            # them (3-R3-1). Tested before LIST_ITEM_RE: '* * *' is a break.
            flush()
            if ind == 0:
                list_indent = None
            pos = end
            continue
        lm = LIST_ITEM_RE.match(content)
        if lm:
            # A list item starts a new block, so inline code never pairs
            # across items (3-R3-1). Outside a list, CommonMark lets only a
            # non-empty bullet or '1.' / '1)' item interrupt a paragraph; any
            # other such line is paragraph text (e.g. a wrapped '2026. The').
            rest = content[lm.end():]
            marker = lm.group(2)
            if (not para or list_indent is not None
                    or (rest.strip() and (marker in "-*+" or marker[:-1] == "1"))):
                flush()
            gap = len(lm.group(3))
            list_indent = (_indent_width(lm.group(1)) + len(lm.group(2))
                           + (gap if 1 <= gap <= 4 else 1))
        elif ind == 0:
            list_indent = None
        para.append(line_span)
        pos = end
    flush()
    if fence is not None:  # unclosed fence runs to EOF (CommonMark)
        spans.append((fence[4], len(text)))
    return _apply_mask(text, spans)


def lint_view(text: str) -> str:
    """Length-preserving twin of _wikilib.strip_code -- the same three
    patterns in the same order, masking instead of deleting -- so a link's
    offset tells whether lint.py counts it. Two uses in plan_text: (1) links
    the fixer masks but lint still sees (frontmatter, ~~~ fences, multi-line
    code spans, indented code, <pre> blocks, ``` fences lint pairs
    differently) are listed as review items; (2) a backstop on every
    rewrite -- a link lint masks is never rewritten (review 'lint-hidden'),
    whatever mask_text decided (regression R151 review ARB-1)."""
    for rx in LINT_CODE_PATTERNS:
        text = re.sub(rx, lambda m: MASK * len(m.group(0)), text)
    return text


# _wikilib.strip_code's three patterns, in its order.
LINT_CODE_PATTERNS = (r"```[\s\S]*?```", r"``[^\n]+?``", r"`[^`\n]+`")


def strip_code_map(text: str) -> tuple[str, list[int]]:
    """_wikilib.strip_code's deletion form plus, for every kept character,
    its offset in `text` -- so a link that exists only after the deletion
    joins two pieces ([[Soil p`x`H]] -> [[Soil pH]]) can be located."""
    idx = list(range(len(text)))
    for rx in LINT_CODE_PATTERNS:
        out: list[str] = []
        oidx: list[int] = []
        last = 0
        for m in re.finditer(rx, text):
            out.append(text[last:m.start()])
            oidx.extend(idx[last:m.start()])
            last = m.end()
        out.append(text[last:])
        oidx.extend(idx[last:])
        text, idx = "".join(out), oidx
    return text, idx


def parse_link(m, text: str):
    """Normalize a LINK_RE match: (target, anchor|None, pipe|None, display|None).
    LINK_RE's anchor group does not exclude a backslash, so on [[A#H\\|D]] it
    captures 'H\\' -- the escape belongs to the pipe, not the heading."""
    target = m.group(1)
    anchor = m.group(2)
    display = m.group(3)
    pipe = None
    if display is not None:
        escaped = text[m.start(3) - 2] == "\\"
        pipe = "\\|" if escaped else "|"
        if anchor is not None and escaped and anchor.endswith("\\"):
            anchor = anchor[:-1]
    return target, anchor, pipe, display


def classify(target: str, from_rel: str, inv: Inventory) -> tuple[str, list[str]]:
    t = target.strip()
    key = t.lower()
    if key.endswith(".md"):
        key = key[:-3]
    if inv.is_existing_asset(t):
        return "attachment", []
    matches = inv.canonical_matches(key, t, from_rel)
    if matches:
        pages = inv.alias_index.get(key, set())
        if pages - set(matches):
            # The name is BOTH some file's name (Obsidian opens that file)
            # AND another page's alias (the page the author may have meant).
            # Competing semantics: never rewritten, a human decides (regression R151 I6).
            # A 0-byte stub file is the R150 flavour of the same collision.
            cat = ("stub-collision" if any(inv.md_size.get(p, -1) == 0 for p in matches)
                   else "name-collision")
            return cat, sorted(set(matches) | pages)
        return "canonical", matches
    pages = sorted(inv.alias_index.get(key, set()))
    if pages and ("/" in key or ":" in key):
        # An alias spelled with '/' or ':' whose basename is a real file:
        # lint's resolve() takes os.path.basename (which on Windows also
        # splits a leading 'X:' drive) and opens THAT file, so the link is
        # canonical to lint while the alias names another page. Competing
        # semantics -> review, never rewritten (regression R151 review h1-I6-1).
        # ntpath splits both '/' and a drive prefix on any OS.
        import ntpath
        base_hits = sorted(set(inv.md_by_base.get(ntpath.basename(key), []))
                           | set(inv.md_by_base.get(key.rsplit("/", 1)[-1], [])))
        if base_hits:
            cat = ("stub-collision" if any(inv.md_size.get(p, -1) == 0 for p in base_hits)
                   else "name-collision")
            return cat, sorted(set(base_hits) | set(pages))
    if len(pages) == 1:
        return "alias-unique", pages
    if len(pages) > 1:
        return "alias-ambiguous", pages
    if "/" in key:
        # [[wrongdir/foo]] where some foo.md exists: lint resolves the
        # basename to it (resolve()'s basename-wins rule), so it is canonical
        # to lint and never an alias candidate (regression R151 review 3-R3-3).
        base_hits = inv.md_by_base.get(key.rsplit("/", 1)[-1])
        if base_hits:
            return "canonical", sorted(base_hits)
        # [[concepts/Alias One]], [[../nowhere/Holder Name]]: lint resolves the
        # basename through the alias index (alias-only); the path itself names
        # no page. Never rewritten -- which page was meant is a human call.
        pages = sorted(inv.alias_index.get(key.rsplit("/", 1)[-1], set()))
        if pages:
            return "alias-path-form", pages
    if ASSET_EXT_RE.search(t):
        return "attachment", []
    return "broken", []


# GFM table delimiter-row cell: optional ':' + one or more '-' + optional ':'.
DELIM_CELL_RE = re.compile(r"\s*:?-+:?\s*")
# CommonMark HTML block start conditions (spec 4.6), each of which ends a
# table (regression R151 review t1-R1-T1). Only these open an HTML block: an
# inline tag inside a row ('<b>x</b> | y') or an autolink ('<https://x>')
# does not, so neither ends a table nor stops a header row.
#   1 <script|pre|style|textarea   2 <!--   3 <?   4 <!LETTER   5 <![CDATA[
#   6 a block-level tag name (open or close)
#   7 a whole line that is ONE complete open or close tag (any other name)
HTML_TYPE6_NAMES = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|"
    "details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|"
    "h1|h2|h3|h4|h5|h6|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|"
    "noframes|ol|optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|"
    "thead|title|tr|track|ul")
HTML_START_1_6_RE = re.compile(
    r" {0,3}(?:<(?:script|pre|style|textarea)(?=[ \t>]|$)"
    r"|<!--|<\?|<![A-Za-z]|<!\[CDATA\["
    r"|</?(?:" + HTML_TYPE6_NAMES + r")(?=[ \t>]|/>|$))", re.I)
_HTML_ATTR = (r"(?:[ \t]+[A-Za-z_:][A-Za-z0-9_.:-]*"
              r"(?:[ \t]*=[ \t]*(?:[^ \t\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)")
HTML_START_7_RE = re.compile(
    r" {0,3}(?:<(?!(?:script|pre|style|textarea)(?=[ \t/>]))[A-Za-z][A-Za-z0-9-]*"
    + _HTML_ATTR + r"*[ \t]*/?>"
    r"|</(?!(?:script|pre|style|textarea)(?=[ \t>]))[A-Za-z][A-Za-z0-9-]*[ \t]*>)[ \t]*", re.I)


def _html_block_start(body: str) -> bool:
    return bool(HTML_START_1_6_RE.match(body) or HTML_START_7_RE.fullmatch(body))


def _split_pipes(s: str) -> list[str]:
    """Split on UNESCAPED pipes: a backslash escapes the next character, so
    '\\|' is literal but '\\\\|' is an escaped backslash then a separator
    (review t1-R1-T3). Code spans and [[...]] are NOT honoured: GFM (and
    Obsidian) split a row on every unescaped pipe (review t1-R1-T2)."""
    cells, cur, k = [], [], 0
    while k < len(s):
        ch = s[k]
        if ch == "\\" and k + 1 < len(s):
            cur.append(s[k:k + 2])
            k += 2
            continue
        if ch == "|":
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        k += 1
    cells.append("".join(cur))
    return cells


def _row_cells(body: str) -> list[str]:
    """Cells of a would-be GFM table row (blockquote markers already
    stripped), split the way GFM splits a row: on every unescaped pipe,
    inside inline code and [[...]] too (review t1-R1-T2); one leading and
    one unescaped trailing pipe are row borders."""
    s = body.strip()
    if s.startswith("|"):
        s = s[1:]
    cells = _split_pipes(s)
    if len(cells) > 1 and cells[-1] == "":
        cells.pop()  # an unescaped trailing border pipe
    return cells


def _is_delim_row(body: str) -> bool:
    s = body.strip()
    if "|" not in s:
        return False  # a bare '---' is a thematic break / setext underline
    return all(DELIM_CELL_RE.fullmatch(c) for c in _row_cells(s))


def _starts_other_block(body: str) -> bool:
    """True when `body` (quote markers stripped) opens a block that ends a
    GFM table (and so cannot be a table header or row)."""
    return bool(LIST_ITEM_RE.match(body) or ATX_HEADING_RE.match(body)
                or FENCE_OPEN_RE.match(body) or THEMATIC_BREAK_RE.match(body)
                or _html_block_start(body))


def _indent_cols(body: str) -> int:
    """Width of the leading whitespace, tabs to the next multiple of 4."""
    n = 0
    for ch in body:
        if ch == " ":
            n += 1
        elif ch == "\t":
            n += 4 - n % 4
        else:
            break
    return n


def _in_container(body: str, col: int, floor: int) -> str | None:
    """`body` as a row of a table whose rows sit at column `col`, with its
    indent stripped -- or None when the line is outside that table's
    container (indented less than `floor`) or is indented code there (4+
    columns past `col`) (review arb-M2-list-item-and-indent-not-checked).
    For a header that opens a list item, col = floor = the item's content
    column. For any other header, col = the header's own indent and floor =
    the content column of the innermost list item still open at the header
    (0 at top level): a table in a list continuation (4 spaces under a
    nested bullet or a '1.  item') ends at a row dedented below that column,
    since the dedent closes the item and GFM tables have no lazy
    continuation across a container boundary (review t2-R2-T1)."""
    if not body.strip():
        return body
    ind = _indent_cols(body)
    if ind < floor or ind - col >= 4:
        return None
    return body.lstrip(" \t")


def _table_line_flags(text: str) -> list[bool]:
    """Per line of `text` (split on '\\n'): True when a bare '|' written on
    that line could split a GFM table cell, i.e. the line is inside a real
    table block (GFM spec): a header row immediately followed by a delimiter
    row with the same cell count (cells of optional ':' + '-'s + optional ':'),
    then body rows until a blank line, a line dedented out of the table's
    container, or the start of another block (list item, heading, fence,
    thematic break, HTML block start per CommonMark types 1-7, or a
    blockquote-depth change).
    Outside such a block no line is table context -- a line that merely
    looks like a row never counts (final-verify defect, open-loops.md:45).
    Cells are split the GFM way, on every unescaped pipe: a '|' inside an
    inline code span or [[...]] never MAKES table context on its own, but
    inside a header row it does split cells (review t1-R1-T2).

    A pipe-less line after the rows is a lazy continuation ROW (GFM spec
    example 202), so it is table context (review t2-R2-T2, reversing
    t1-LAZY-1). The escaped form is the safe choice under either renderer
    reading: if the renderer follows GFM, a bare '|' there splits a cell
    and breaks the link; if it ends the table, [[slug\\|Display]] still
    resolves outside a table.

    Open list items are tracked while scanning (innermost item's content
    column; a non-blank line indented below it closes the item, a blank
    line keeps it open) and give a non-item header its `floor`, for the
    delimiter-row lookup and every body row (review t2-R2-T1).

    A table opening a list item ("- | a |" then "  |---|") needs its
    delimiter and body rows indented to the item's content column; any row
    4+ columns past the header's column (the item's content column, else
    the header's own indent) is indented code, never a delimiter or body
    row (review arb-M2). Blockquote/callout markers are
    stripped first, so a table inside a callout is recognized the same way.
    Fenced code needs no tracking here: links inside code are never
    rewritten, and a fence line ends a table."""
    return _table_lines(text)[0]


def _table_lines(text: str) -> tuple[list[bool], list[bool]]:
    """(_table_line_flags(text), header flags): the second list is True only
    on each table's HEADER row -- the row whose cell count must equal the
    delimiter row's, so a bare '|' there cannot be escaped without unmaking
    the table (review g1-C4-header-row-breaks-table)."""
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    parsed = []
    for ln in lines:
        m = BQ_RE.match(ln)
        depth = m.group(0).count(">") if m else 0
        parsed.append((depth, ln[m.end():] if m else ln))
    flags = [False] * len(lines)
    headers = [False] * len(lines)
    items: list[int] = []  # content columns of the open list items, innermost last
    items_depth = 0
    i = 0
    while i < len(lines):
        depth, body = parsed[i]
        if depth != items_depth:
            items, items_depth = [], depth
        if body.strip():
            ind = _indent_cols(body)
            while items and ind < items[-1]:
                items.pop()  # dedented below the item's content: it is closed
        # A table may open a list item ("- | a |" then "  |---|"): the header
        # is the text after the marker, the rows sit at its content column.
        lm = LIST_ITEM_RE.match(body)
        head = body[lm.end():] if lm else body
        col = len(body[:lm.end()].expandtabs(4)) if lm else _indent_cols(body)
        floor = col if lm else (items[-1] if items else 0)
        if lm:
            items.append(col)
        delim = (_in_container(parsed[i + 1][1], col, floor)
                 if i + 1 < len(lines) and parsed[i + 1][0] == depth else None)
        if (delim is not None and head.strip() and not _starts_other_block(head)
                and _is_delim_row(delim)
                and len(_row_cells(head)) == len(_row_cells(delim))):
            flags[i] = flags[i + 1] = True
            headers[i] = True
            j = i + 2
            while j < len(lines):
                d, b = parsed[j]
                row = _in_container(b, col, floor) if d == depth else None
                if row is None or not row.strip() or _starts_other_block(row):
                    break
                flags[j] = True
                j += 1
            i = j
            continue
        i += 1
    return flags, headers


def plan_text(rel: str, text: str, inv: Inventory, wl, hold: str | None = None,
              lk=None) -> dict:
    """Classify every live link in one file; build the safe edit list.

    `hold`: a reason that sends every alias-unique link to review instead of
    the edit list (read-only files). Links lint counts but this fixer masks
    (frontmatter, ~~~ fences, multi-line code spans, indented code, <pre>
    blocks, ``` fences lint pairs differently) are listed for review
    as 'in-frontmatter' / 'in-code' and never rewritten. The reverse case --
    a link live here that lint masks -- is never rewritten either: its
    review item carries lint_hidden=True (reason 'lint-hidden' unless `hold`
    overrides), and the returned "lint_hidden" tally counts such items lint
    would otherwise count as alias-only (alias-unique-held / ambiguous), so
    the EXEMPT tally and the lint parity equation leave them out.

    `lk`: target -> lint.py's verdict on that link in this file
    (_wikilib.lint_link_kind). Every edit and review item records it as
    "lint_kind" ("masked" when lint's code-strip hides the link), so a
    caller can predict lint's ALIAS-ONLY / BROKEN movement exactly."""
    if lk is None:
        def lk(_t):
            return "unknown"
    masked = mask_text(text, wl)
    lv = lint_view(text)
    lint_hidden = 0
    counts = dict.fromkeys(LINK_CATEGORIES, 0)
    edits: list[dict] = []
    review: list[dict] = []
    broken: dict[str, int] = {}
    replacements: list[tuple[int, int, str]] = []
    line_starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
    table_flags: list[bool] | None = None  # _table_line_flags(text), built on first need
    table_headers: list[bool] = []
    in_table = False
    edit_items: list[dict] = []  # each edit as a review item, for the table postcondition

    import bisect

    def locate(start: int, target: str) -> dict:
        li = bisect.bisect_right(line_starts, start) - 1
        return {"file": rel, "line": li + 1, "col": start - line_starts[li] + 1,
                "target": target.strip()}

    def held_category(pages: list[str]) -> str:
        return "alias-unique-held" if len(pages) == 1 else "alias-ambiguous"

    # Links lint sees that this fixer masks: review only, never rewritten.
    body_start = _body_start(text, wl)
    for m in wl.LINK_RE.finditer(lv):
        raw = m.group(0)
        if MASK in raw or "\n" in raw or "\r" in raw or MASK not in masked[m.start():m.end()]:
            continue
        target = parse_link(m, text)[0]
        cat, pages = classify(target, rel, inv)
        if cat not in ("alias-unique", "alias-ambiguous", "alias-path-form"):
            continue
        category = held_category(pages)
        counts[category] += 1
        review.append(dict(locate(m.start(), target), category=category, candidates=pages,
                           reason=hold or ("in-frontmatter" if m.start() < body_start else "in-code"),
                           lint_kind=lk(target.strip())))

    # Links lint sees ONLY in the deletion form of its code-strip (a deleted
    # span joins two pieces into a link): no span here can be rewritten, so
    # each one lint files as an alias candidate is a review item, keeping the
    # predicted ALIAS-ONLY count equal to lint's (review
    # h2-I9-strip-join-invisible-alias). A match is lint-only exactly when
    # its kept characters are not contiguous in the original (a span was
    # deleted inside it) -- matched by position, so a repeated alias in the
    # same file never misplaces the review item. No backtick, no deletion.
    import collections
    lint_text = wl.strip_code(text) if "`" in text else ""
    sidx = None
    seen = None
    if lint_text:
        mapped, sidx = strip_code_map(text)
        if mapped != lint_text:
            # The patterns drifted from _wikilib: fall back to a multiset
            # difference by target (every link lint_view shows unmasked is
            # also in the deletion form), located at 1:1.
            sidx = None
            seen = collections.Counter(parse_link(m, lv)[0].strip() for m in wl.LINK_RE.finditer(lv)
                                       if MASK not in m.group(0))
    for m in wl.LINK_RE.finditer(lint_text):
        t = m.group(1).strip()
        if sidx is not None:
            s, e = m.start(), m.end()
            if sidx[e - 1] - sidx[s] == e - s - 1:
                continue  # contiguous: the same link the scans above handle
        elif seen[t] > 0:
            seen[t] -= 1
            continue
        cat, pages = classify(t, rel, inv)
        kind = lk(t)
        if cat not in ("alias-unique", "alias-ambiguous", "alias-path-form") and kind != "alias-only":
            continue
        category = held_category(pages)
        counts[category] += 1
        review.append(dict(locate(sidx[m.start()] if sidx else 0, t), category=category,
                           candidates=pages, reason=hold or STRIP_JOIN_REASON, lint_kind=kind))

    for m in wl.LINK_RE.finditer(masked):
        raw = m.group(0)
        if MASK in raw or "\n" in raw or "\r" in raw:
            continue
        start = m.start()
        # '!' is an embed marker unless it is itself backslash-escaped
        # (\![[A]] is a literal '!' + an ordinary link; review 3-R3-4).
        embed = False
        if start > 0 and masked[start - 1] == "!":
            k = start - 2
            while k >= 0 and masked[k] == "\\":
                k -= 1
            embed = (start - 2 - k) % 2 == 0
        if embed:
            start -= 1
        target, anchor, pipe, display = parse_link(m, masked)
        cat, pages = classify(target, rel, inv)
        where = locate(start, target)
        li = where["line"] - 1
        line_no, col = where["line"], where["col"]
        if cat == "broken":
            counts["broken"] += 1
            broken[target.strip()] = broken.get(target.strip(), 0) + 1
            continue
        if cat in ("canonical", "attachment"):
            counts[cat] += 1
            continue
        # Backstop (ARB-1): a link lint's code-strip masks is never
        # rewritten, and is flagged so parity/excluded counts skip it.
        hidden = MASK in lv[m.start():m.end()]
        if cat == "alias-path-form":
            category = held_category(pages)
            counts[category] += 1
            item = dict(where, category=category, candidates=pages,
                        reason=hold or (LINT_HIDDEN_REASON if hidden else PATH_FORM_REASON),
                        lint_kind=LINT_MASKED if hidden else lk(target.strip()))
            if hidden:
                item["lint_hidden"] = True
                lint_hidden += 1
            review.append(item)
            continue
        if cat in ("alias-ambiguous",) + COLLISION_CATEGORIES:
            counts[cat] += 1
            item = dict(where, category=cat, candidates=pages,
                        lint_kind=LINT_MASKED if hidden else lk(target.strip()))
            if hold is not None:
                item["reason"] = hold
            elif hidden:
                item["reason"] = LINT_HIDDEN_REASON
            if hidden:
                item["lint_hidden"] = True
                if cat == "alias-ambiguous":
                    lint_hidden += 1
            review.append(item)
            continue
        # alias-unique: build the rewrite, or hold it for review.
        page = pages[0]
        slug = inv.slug(page)
        held = None
        if hold is not None:
            held = hold
        elif hidden:
            held = LINT_HIDDEN_REASON
        elif (anchor is not None and not anchor.strip()) or (display is not None and not display.strip()):
            held = "odd-form (empty anchor or display)"
        elif set(slug) & UNSAFE_SLUG_CHARS:
            held = "unsafe-slug (filename cannot be spelled as a link target)"
        elif lk(target.strip()) not in REWRITABLE_LINT_KINDS:
            # Backstop (arb-I9-1): lint already resolves this link to some
            # page (canonical) or files it elsewhere -- rewriting it would
            # redirect a link lint counts as healthy. Only links lint files
            # as ALIAS-ONLY, or as BROKEN through R148/R149 name mangling,
            # are rewritten.
            held = LINT_CANONICAL_REASON
        new = None
        if held is None:
            if table_flags is None:
                table_flags, table_headers = _table_lines(text)
            in_table = table_flags[li]
            if in_table and pipe == "|" and table_headers[li]:
                # A bare '|' on a HEADER row is a cell split the delimiter
                # row's cell count matches: escaping it would unmake the
                # whole table (review g1-C4-header-row-breaks-table).
                held = TABLE_HEADER_REASON
            elif in_table and display is not None and re.search(r"(?<!\\)\|", display):
                # [[A|x|y]] in a table: the display's own bare '|' still
                # splits the cell after the rewrite (review
                # g1-R1-C4-display-inner-pipe).
                held = TABLE_DISPLAY_PIPE_REASON
        if held is None:
            a_part = "" if anchor is None else "#" + anchor
            if in_table and pipe is not None:
                # Inside a real table the separator is ALWAYS '\|': a bare
                # '|' there splits the cell in Obsidian, so keeping it as
                # written would keep the link broken. Outside tables an
                # existing separator is kept as written.
                pipe = "\\|"
            if embed:
                inner = slug + a_part + ("" if display is None else pipe + display)
                exp_disp = display
            elif display is not None:
                inner = slug + a_part + pipe + display
                exp_disp = display
            else:
                disp = target.strip() if anchor is None else f"{target.strip()} > {anchor.strip()}"
                inner = slug + a_part + ("\\|" if in_table else "|") + disp
                exp_disp = disp
            new = ("!" if embed else "") + "[[" + inner + "]]"
            chk = wl.LINK_RE.fullmatch(new[1:] if embed else new)
            ok = chk is not None
            if ok:
                ct, ca, _, cd = parse_link(chk, new[1:] if embed else new)
                ok = (ct.strip() == slug and (ca or None) == (anchor or None)
                      and cd == exp_disp
                      and classify(slug, rel, inv)[0] == "canonical"
                      and classify(slug, rel, inv)[1] == [page])
            if not ok:
                held = "self-check failed (rewritten link would not parse back to the page)"
            else:
                # lint's verdict on the REWRITTEN target (review
                # h3-R3-I9-dotted-slug): a dotted slug (regression R148 splitext), a '/'
                # or ':' slug (regression R149) or a slug lint treats as a clipper
                # placeholder (date / published) would land under BROKEN or
                # GHOST, breaking the I9 reconciliation after the write.
                # "unknown" = no lint verdict supplied (recount's re-plan).
                slug_kind = lk(slug)
                if slug_kind not in ("canonical", "unknown"):
                    held = LINT_MANGLED_SLUG_REASON.format(kind=slug_kind)
        if held is not None:
            counts["alias-unique-held"] += 1
            item = dict(where, category="alias-unique-held", candidates=pages, reason=held,
                        lint_kind=LINT_MASKED if hidden else lk(target.strip()))
            if hidden:
                item["lint_hidden"] = True
                lint_hidden += 1
            review.append(item)
            continue
        counts["alias-unique"] += 1
        old = text[start:m.end()]
        edits.append({"line": line_no, "col": col, "old": old, "new": new, "category": "alias-unique",
                      "lint_kind": lk(target.strip())})
        edit_items.append(dict(where, category="alias-unique-held", candidates=pages,
                               reason=TABLE_STRUCTURE_REASON, lint_kind=lk(target.strip())))
        replacements.append((start, m.end(), new))

    new_text = text
    for a, b, s in reversed(replacements):
        new_text = new_text[:a] + s + new_text[b:]
    # Per-file postcondition (review arb-M2-table-structure-invariant): the
    # rewrites must leave every GFM table exactly where it was -- no table
    # made, unmade, lengthened or shortened. On any change the whole file is
    # held for review and nothing in it is rewritten.
    if replacements and _table_line_flags(new_text) != _table_line_flags(text):
        counts["alias-unique"] -= len(edits)
        counts["alias-unique-held"] += len(edits)
        review.extend(edit_items)
        edits, new_text = [], text
    return {"counts": counts, "edits": edits, "review": review, "broken": broken,
            "new_text": new_text, "lint_hidden": lint_hidden}


def _lint_kind_factory(root: Path, wl):
    """rel -> (target -> lint.py's verdict on that link in that file), over
    lint's own inventory (_wikilib.lint_inventory / lint_link_kind -- the
    code lint.py itself runs, not a copy)."""
    linv = wl.lint_inventory(str(root))

    def for_file(rel: str):
        def lk(target: str) -> str:
            return wl.lint_link_kind(target, rel, linv)[0]
        return lk
    return for_file


def _exempt_items(xp: dict) -> list[dict]:
    """Alias-only links in an excluded file that lint counts: every would-be
    edit plus the held / ambiguous review items lint's code-strip leaves
    visible. Collisions are left out (lint opens the file of that name)."""
    return list(xp["edits"]) + [r for r in xp["review"]
                                if r["category"] in ("alias-unique-held", "alias-ambiguous")
                                and not r.get("lint_hidden")]


def _lint_split(items: list[dict]) -> dict:
    out = {"links": len(items), "lint_alias_only": 0, "lint_broken": 0, "lint_other": 0}
    for it in items:
        k = it.get("lint_kind")
        if k == "alias-only":
            out["lint_alias_only"] += 1
        elif k == "broken":
            out["lint_broken"] += 1
        else:
            out["lint_other"] += 1
    return out


def build_plan(root: Path, wl, quiet: bool = False) -> dict:
    # Only what lint scans: its walk is case-sensitive, so a .MD page is an
    # asset to lint (rglob matches it case-insensitively on Windows).
    files = [f for f in collect_md_files(root) if f.name.endswith(".md")]
    if quiet:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            denied_reasons = resolve_exclusions(root)
    else:
        denied_reasons = resolve_exclusions(root)
    inv = Inventory(root, wl)
    lkf = _lint_kind_factory(root, wl)
    edit_deny_set = []
    for pth in denied_reasons:
        try:
            edit_deny_set.append(Path(pth).relative_to(root).as_posix())
        except ValueError:
            edit_deny_set.append(str(pth))
    plan = {"files": {}, "review": [], "broken": {}, "scanned": 0,
            "skipped": dict.fromkeys(SKIP_REASONS, 0), "exclusions": {},
            "counts": dict.fromkeys(LINK_CATEGORIES, 0), "exempt": {},
            "edit_deny": sorted(edit_deny_set), "new": {}, "inv": inv}
    for f in files:
        rel = f.relative_to(root).as_posix()
        reason = skip_reason(f, denied_reasons)
        raw = None
        if reason is None:
            try:
                raw = f.read_bytes()
                bom, text = _decode(raw)
            except (OSError, UnicodeDecodeError):
                reason = "unreadable"
            else:
                # Re-checked on the BOM-stripped text: a BOM hides the opening
                # `---` from a plain utf-8 read, and an append_only page must
                # never be planned (regression R151 review).
                for flag in ("append_only", "auto_generated"):
                    if wl.fm_flag(text, flag):
                        reason = flag
                        break
        if reason is not None:
            plan["skipped"][reason] += 1
            plan["exclusions"].setdefault(reason, []).append(rel)
            try:  # EXEMPT: alias-only links left grey in a never-mutated file
                _, xtext = _decode(f.read_bytes())
                # Only links lint counts: lint drops _meta/ auto_generated
                # pages from its file set entirely; collisions are canonical
                # to lint; lint_hidden links are masked by lint's code-strip.
                if not (rel.startswith("_meta/") and _lint_is_auto_generated(xtext)):
                    xp = plan_text(rel, xtext, inv, wl, lk=lkf(rel))
                    split = _lint_split(_exempt_items(xp))
                    if split["links"]:
                        plan["exempt"][rel] = split
            except (OSError, UnicodeDecodeError):
                pass
            continue
        plan["scanned"] += 1
        fp = plan_text(rel, text, inv, wl, lk=lkf(rel))
        for k, v in fp["counts"].items():
            plan["counts"][k] += v
        for k, v in fp["broken"].items():
            plan["broken"][k] = plan["broken"].get(k, 0) + v
        plan["review"] += fp["review"]
        if fp["edits"]:
            plan["files"][rel] = {"sha256": _sha256(raw), "edits": fp["edits"],
                                  "counts": fp["counts"]}
            plan["new"][rel] = (bom, fp["new_text"], raw)
    # scripts/*.md: lint scans them, this fixer never writes them. Their
    # alias-only links go to the review queue so lint's residue is listed.
    sdir = root / "scripts"
    if sdir.is_dir():
        for f in sorted(p for p in sdir.rglob("*.md")
                        if p.is_file() and p.name.endswith(".md")):
            rel = f.relative_to(root).as_posix()
            try:
                _, stext = _decode(f.read_bytes())
            except (OSError, UnicodeDecodeError):
                continue
            plan["review"] += plan_text(rel, stext, inv, wl, hold=OUT_OF_SCOPE_REASON,
                                        lk=lkf(rel))["review"]
    return plan


def safe_rewrites(plan: dict) -> int:
    return sum(len(v["edits"]) for v in plan["files"].values())


def exempt_total(plan: dict) -> int:
    return sum(e["links"] for e in plan["exempt"].values())


def lint_prediction(plan: dict) -> dict:
    """What lint.py's ALIAS-ONLY count should be right now, from this plan:
    every alias-candidate link lint files as alias-only (safe edits, review
    items, EXEMPT links). Collisions and lint-masked items carry another
    lint_kind and fall out on their own."""
    edits = [e for v in plan["files"].values() for e in v["edits"]]
    a = sum(1 for e in edits if e.get("lint_kind") == "alias-only")
    b = sum(1 for r in plan["review"] if r.get("lint_kind") == "alias-only")
    c = sum(e["lint_alias_only"] for e in plan["exempt"].values())
    sb = sum(1 for e in edits if e.get("lint_kind") == "broken")
    return {"safe": a, "review": b, "exempt": c, "total": a + b + c,
            "safe_lint_broken": sb, "safe_other": len(edits) - a - sb}


def check_safe_parity(plan: dict) -> bool:
    """arb-I9-1: every safe rewrite is one lint files as ALIAS-ONLY or as
    BROKEN (regression R148/R149); a lint-canonical edit never reaches the edit list.
    Printed, and checked explicitly (not an assert, which -O strips)."""
    lp = lint_prediction(plan)
    n = safe_rewrites(plan)
    print(f"SAFE PARITY: SAFE_REWRITES {n} = lint-alias-only {lp['safe']} + "
          f"lint-broken {lp['safe_lint_broken']}")
    if lp["safe_other"] or n != lp["safe"] + lp["safe_lint_broken"]:
        print(f"SAFE PARITY FAILED: {lp['safe_other']} safe rewrites lint files under neither "
              "ALIAS-ONLY nor BROKEN -- nothing may be applied from this plan.")
        return False
    return True


def run_id_for(plan: dict) -> str:
    import json
    core = {rel: {"sha256": v["sha256"], "edits": v["edits"]} for rel, v in plan["files"].items()}
    return _sha256(json.dumps(core, sort_keys=True).encode("utf-8"))[:16]


def print_counts(plan: dict) -> None:
    """The three I1 counts, each on its own line, then EXEMPT per file."""
    print(f"SAFE_REWRITES = {safe_rewrites(plan)}")
    print(f"REVIEW_REQUIRED = {len(plan['review'])}")
    print(f"EXEMPT = {exempt_total(plan)}")
    for rel, e in sorted(plan["exempt"].items()):
        other = f", other {e['lint_other']}" if e["lint_other"] else ""
        print(f"EXEMPT {rel} = {e['links']} (lint ALIAS-ONLY {e['lint_alias_only']}, "
              f"lint BROKEN {e['lint_broken']}{other})")
    print("(REVIEW_REQUIRED and EXEMPT are legitimate end states -- a human decision and "
          "never-mutated history -- not failures; never force them to zero.)")


def print_summary(plan: dict, label: str) -> bool:
    c = plan["counts"]
    edits = safe_rewrites(plan)
    print(f"=== ALIAS-ONLY LINK FIX (regression R151) -- {label} ===")
    print(f"Files scanned: {plan['scanned']}")
    print(f"Skipped: {sum(plan['skipped'].values())} files (" +
          ", ".join(f"{r}={n}" for r, n in plan["skipped"].items()) + ").")
    print("Links: " + ", ".join(f"{k}={c[k]}" for k in LINK_CATEGORIES) + ".")
    print(f"Broken targets: {len(plan['broken'])} distinct ({c['broken']} links), report-only.")
    print(f"Files with safe rewrites: {len(plan['files'])} ({edits} edits)")
    print(f"Review queue: {len(plan['review'])} items")
    for item in sorted(plan["review"], key=lambda r: (r["file"], r["line"], r["col"]))[:25]:
        extra = item.get("reason") or ", ".join(item["candidates"])
        print(f"  review  {item['category']:<17} {item['file']}:{item['line']}  "
              f"[[{item['target']}]]  ({extra})")
    if len(plan["review"]) > 25:
        print(f"  ... {len(plan['review']) - 25} more in the manifest")
    lp = lint_prediction(plan)
    print(f"Predicted lint.py ALIAS-ONLY now: {lp['total']} = safe {lp['safe']} + review "
          f"{lp['review']} + EXEMPT {lp['exempt']} (after --apply: review + EXEMPT)")
    ok = check_safe_parity(plan)
    print_counts(plan)
    return ok


def _norm_root(root) -> str:
    import os
    return os.path.normcase(os.path.normpath(os.path.abspath(str(root))))


def _tool_sha256() -> str:
    return _sha256(Path(__file__).resolve().read_bytes())


def _tool_git_commit() -> str | None:
    """HEAD of the git repo holding this script, when the script is tracked
    there (a template checkout) AND unmodified against HEAD; None in a vault
    (no git), for a modified working copy, or on any error.
    Informational -- tool_sha256 is the identity --apply checks."""
    import subprocess
    here = Path(__file__).resolve()
    try:
        kw = dict(capture_output=True, text=True, timeout=5)
        t = subprocess.run(["git", "-C", str(here.parent), "ls-files", "--error-unmatch", here.name], **kw)
        if t.returncode != 0:
            return None
        # A modified (or staged) working copy is NOT that commit's tool:
        # recording HEAD would be false provenance.
        d = subprocess.run(["git", "-C", str(here.parent), "diff", "--quiet", "HEAD", "--", here.name], **kw)
        if d.returncode != 0:
            return None
        h = subprocess.run(["git", "-C", str(here.parent), "rev-parse", "HEAD"], **kw)
        out = h.stdout.strip()
        return out if h.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", out) else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _atomic_write(path: Path, data: bytes) -> None:
    import os
    tmp = path.with_name(path.name + TMP_SUFFIX)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        # A failed write or replace (Dropbox / AV lock) must not leave a
        # stray tmp in wiki/ -- lint would index it as an asset name and
        # Dropbox would sync it (regression R151 review h2-R2-TMP-LEAK).
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _write_json(path: Path, obj) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=True).encode("ascii") + b"\n")


def write_manifest(plan: dict, root: Path, path: Path) -> str:
    import datetime
    rid = run_id_for(plan)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "mode": "alias-only",
        "run_id": rid,
        "generated": datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(),
        "root": _norm_root(root),
        "tool_sha256": _tool_sha256(),
        "tool_git_commit": _tool_git_commit(),
        # LINK edits (links, not files) and file count, separately (I4).
        "planned_links": safe_rewrites(plan),
        "planned_files": len(plan["files"]),
        "totals": {
            "files_scanned": plan["scanned"],
            "files_skipped": dict(plan["skipped"]),
            "links": dict(plan["counts"]),
            "safe_rewrites": safe_rewrites(plan),
            "review_required": len(plan["review"]),
            "exempt": exempt_total(plan),
            "broken_links": plan["counts"]["broken"],
            "predicted_lint_alias_only": lint_prediction(plan),
        },
        "exempt": {rel: dict(v) for rel, v in sorted(plan["exempt"].items())},
        "exclusions": {k: sorted(v) for k, v in sorted(plan["exclusions"].items())},
        "edit_deny": list(plan["edit_deny"]),
        "files": {rel: {"sha256": v["sha256"], "edits": v["edits"]}
                  for rel, v in sorted(plan["files"].items())},
        "review": sorted(plan["review"], key=lambda r: (r["file"], r["line"], r["col"])),
        "broken": dict(sorted(plan["broken"].items(), key=lambda kv: (-kv[1], kv[0]))),
    }
    _write_json(path, manifest)
    return rid


def recount(root: Path, rels: list[str], wl) -> dict:
    """Re-classify files from disk against a freshly built inventory."""
    inv = Inventory(root, wl)
    total = dict.fromkeys(LINK_CATEGORIES, 0)
    for rel in rels:
        _, text = _decode((root / rel).read_bytes())
        for k, v in plan_text(rel, text, inv, wl)["counts"].items():
            total[k] += v
    return total


LINT_BROKEN_RE = re.compile(r"^=== BROKEN LINKS === (\d+)", re.M)
LINT_ALIAS_RE = re.compile(r"^=== ALIAS-ONLY LINKS[^\n]*=== (\d+) ", re.M)


def run_lint(root: Path) -> tuple[dict | None, str]:
    """lint.py --summary (the copy beside this script) run from the vault
    root: ({"alias_only", "broken"}, "") or (None, why not measured)."""
    import os
    import subprocess
    lint = Path(__file__).resolve().parent / "lint.py"
    if not lint.is_file():
        return None, f"no lint.py beside {Path(__file__).name}"
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop(FAULT_ENV, None)
    try:
        p = subprocess.run([sys.executable, "-B", str(lint), "--summary"], cwd=str(root), env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=1800)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"lint.py could not run ({e.__class__.__name__}: {e})"
    if p.returncode != 0:
        # lint.py --summary exits 0 whatever it finds; nonzero is a crash,
        # never an "old lint" (regression R151 review h2-I9-lint-after-unmeasured-exit0).
        tail = " ".join((p.stderr or "").split())[-300:]
        return None, f"lint.py exited {p.returncode}: {tail or '(no stderr)'}"
    a, b =LINT_ALIAS_RE.search(p.stdout), LINT_BROKEN_RE.search(p.stdout)
    if not (a and b):
        return None, "lint.py printed no ALIAS-ONLY / BROKEN line (pre-R151 lint?)"
    return {"alias_only": int(a.group(1)), "broken": int(b.group(1))}, ""


def restore_command(root: Path, rid: str) -> str:
    return (f'python "{Path(__file__).resolve()}" --alias-only --root "{root}" '
            f'--restore {rid}')


def reconcile(planned_links: int, terms: list[tuple[str, int]], log_path=None) -> bool:
    """Print and check the I9 identity: every link the manifest planned is
    applied, or explicitly accounted for (stale / changed files -> review,
    a failed batch, batches not attempted). False = unexplained shortfall."""
    total = sum(n for _, n in terms)
    print(f"RECONCILE: manifest_safe_rewrites {planned_links} = "
          + " + ".join(f"{name} {n}" for name, n in terms))
    if planned_links == total:
        return True
    where = f" -- inspect {log_path}" if log_path else ""
    print(f"RECONCILE FAILED: {planned_links} != {total} (unexplained shortfall{where})")
    return False


def _reject(msg: str) -> int:
    print(f"ERROR: {msg} -- nothing written.", file=sys.stderr)
    return 2


def run_alias(args, root: Path) -> int:
    import json
    import os
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    wl = _wikilib()
    if args.restore is not None:
        return run_restore(args.restore, root)
    manifest_path = (args.manifest if args.manifest is not None
                     else root / "_meta" / "alias-fix-manifest.json").resolve()

    if args.verify:
        plan = build_plan(root, wl)
        if not print_summary(plan, "VERIFY"):
            return 5
        n = safe_rewrites(plan)
        print(f"Residual: review={len(plan['review'])}, broken={plan['counts']['broken']}")
        unf = unfinished_runs(root)
        if unf:
            # A failed apply's writes still in the vault are not a verified
            # state, whatever SAFE_REWRITES says (review h3-R3-DS-1).
            report_unfinished(root, unf)
            live = [u for u in unf if u["files"]]
            unreadable = [u for u in unf if not u["files"]]
            if live:
                print(f"VERIFY FAIL: {len(live)} unfinished apply run(s) still have writes in the "
                      f"vault -- restore them (RESTORE above).")
            if unreadable:
                print(f"VERIFY FAIL: {len(unreadable)} apply run(s) have an unreadable apply log -- "
                      f"their files cannot be checked; inspect and set them aside (see above).")
            return 1
        if n == 0:
            print("VERIFY OK: SAFE_REWRITES = 0 (exit 0 whatever REVIEW_REQUIRED / EXEMPT are).")
            print(NOTHING_TO_DO)
            return 0
        print(f"VERIFY FAIL: {n} safe rewrites pending across {len(plan['files'])} files.")
        return 1

    if not args.apply:
        plan = build_plan(root, wl)
        if not print_summary(plan, "DRY RUN"):
            print("No manifest written.")
            return 5
        rid = write_manifest(plan, root, manifest_path)
        print(f"Manifest: {manifest_path} (run_id {rid})")
        # A failed earlier apply (another run_id) is reported here, where
        # the refusal's next step leads (review h3-R3-DS-1); --apply refuses.
        report_unfinished(root, unfinished_runs(root))
        if not plan["files"]:
            print(NOTHING_TO_DO)
        else:
            print("(Dry run. Re-run with --apply to write the manifest's edits.)")
        return 0

    # --apply. Every rejection below happens before any write.
    if not manifest_path.is_file():
        print(f"ERROR: no manifest at {manifest_path} -- run the dry run (without --apply) first.",
              file=sys.stderr)
        return 2
    try:
        manifest = json.loads(manifest_path.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as e:
        return _reject(f"unreadable manifest {manifest_path}: {e}")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        return _reject(f"{manifest_path} is not an alias-only manifest")
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        return _reject(f"manifest schema_version {manifest.get('schema_version')!r} is not "
                       f"{MANIFEST_SCHEMA} -- re-run the dry run")
    if manifest.get("mode") != "alias-only":
        return _reject(f"manifest mode {manifest.get('mode')!r} is not 'alias-only'")
    if manifest.get("root") != _norm_root(root):
        return _reject(f"manifest root {manifest.get('root')} is not --root {_norm_root(root)}")
    if manifest.get("tool_sha256") != _tool_sha256():
        return _reject("manifest tool_sha256 does not match this fix_wikilinks.py (the script "
                       "changed since the dry run) -- re-run the dry run")
    mfiles = manifest["files"]
    rid = manifest.get("run_id", "")
    if not RUN_ID_RE.fullmatch(str(rid)):
        return _reject(f"manifest run_id {rid!r} is not 16 hex digits")
    try:
        m_links = {rel: len(v["edits"]) for rel, v in mfiles.items()}
    except (TypeError, KeyError):
        return _reject("manifest files entries lack edit lists")
    planned_links = manifest.get("planned_links")
    if manifest.get("planned_files") != len(mfiles):
        return _reject(f"manifest planned_files {manifest.get('planned_files')!r} != "
                       f"{len(mfiles)} files listed")
    plan = build_plan(root, wl)
    lp = lint_prediction(plan)
    if lp["safe_other"]:
        return _reject(f"SAFE PARITY FAILED: {lp['safe_other']} planned rewrites lint files under "
                       "neither ALIAS-ONLY nor BROKEN (arb-I9-1)")
    ready: list[str] = []
    stale: list[str] = []
    for rel, mv in sorted(mfiles.items()):
        cur = plan["files"].get(rel)
        if cur is None or cur["sha256"] != mv.get("sha256") or cur["edits"] != mv["edits"]:
            stale.append(rel)
        else:
            ready.append(rel)
    unplanned = sorted(set(plan["files"]) - set(mfiles))
    stale_links = sum(m_links[r] for r in stale)
    replan_links = sum(len(plan["files"][r]["edits"]) for r in ready)
    if not isinstance(planned_links, int) or planned_links - stale_links != replan_links:
        return _reject(f"manifest planned_links {planned_links!r} minus {stale_links} in stale "
                       f"files != {replan_links} links the re-plan finds in the other files")
    # An earlier apply of this same run_id (regression R151 review h1-I8-1 /
    # h1-R1-1 / arb-I8-2): a stale file whose bytes are exactly what that
    # apply wrote is this tool's own write, not a human change. While any
    # earlier apply of the run is unfinished (failed batch, crash) and such
    # files remain, refuse: re-applying would rotate its log away and the
    # printed RESTORE would no longer cover them.
    backup_root = root / ".alias-fix-backup" / rid
    # Fail-safe read (review t2-DS1-same-rid-corrupt-prev-silent-wedge): the
    # current log strictly; an unreadable rotated prev log is skipped with a
    # NOTE when the current log is settled (the vault never changes, so the
    # run_id never rotates and a hard reject would wedge every re-apply),
    # and otherwise still rejects -- with the set-aside remedy.
    try:
        prior_logs, bad_prev = _apply_logs_tolerant(backup_root)
    except ValueError as e:
        return _reject(str(e))
    if bad_prev:
        cur_settled = (bool(prior_logs) and prior_logs[-1][0].name == "apply-log.json"
                       and prior_logs[-1][1].get("result") in SETTLED_RESULTS)
        if not cur_settled:
            print(f"RESTORE: {restore_command(root, rid)}")
            print(set_aside_remedy(root, rid))
            return _reject(f"unreadable rotated apply log(s) {', '.join(bad_prev)} in {backup_root} "
                           f"and the current apply of run_id {rid} is not settled")
        for name in bad_prev:
            print(f"NOTE: unreadable rotated log {name} skipped for the earlier-apply check "
                  f"(run_id {rid} is settled).")
    prior_posts: dict[str, set] = {}
    for _, lg in prior_logs:
        for rel, e in lg["files"].items():
            if isinstance(e, dict) and e.get("post_apply_sha256"):
                prior_posts.setdefault(rel, set()).add(e["post_apply_sha256"])
    earlier: list[str] = []
    for rel in stale:
        try:
            cur = _sha256((root / rel).read_bytes())
        except OSError:
            continue
        if cur in prior_posts.get(rel, ()) and cur != mfiles[rel].get("sha256"):
            earlier.append(rel)
    stale_only = [r for r in stale if r not in earlier]
    earlier_links = sum(m_links[r] for r in earlier)
    stale_only_links = stale_links - earlier_links
    # Only the CURRENT log can be unfinished with its writes still live: a
    # log is rotated to apply-log.prev-* only after this gate passed, i.e.
    # once the unfinished apply's writes were restored (or edited away). A
    # rotated failed log whose post hashes equal a later COMPLETE apply's
    # (same plan) must not block a re-apply.
    unfinished = [p.name for p, lg in prior_logs[-1:]
                  if p.name == "apply-log.json" and lg.get("result") not in SETTLED_RESULTS]
    print(f"=== ALIAS-ONLY LINK FIX (regression R151) -- APPLY run_id {rid} ===")
    print(f"Ready: {len(ready)} files; changed since dry run (review queue, not written): "
          f"{len(stale_only)}; written by an earlier apply of this run: {len(earlier)}; "
          f"new since dry run (not in manifest, not written): {len(unplanned)}")
    for rel in stale_only:
        print(f"  review  stale-since-dry-run  {rel}  ({m_links[rel]} links -> REVIEW_REQUIRED)")
    for rel in earlier:
        # 'restore first' only where the apply is refused below (review
        # g1-C1-restore-first-wording); a settled earlier apply's files are
        # counted, never rewritten, and block nothing.
        label = ("restore first" if unfinished else
                 "applied by earlier apply of this run, settled -- counted as "
                 "links_previously_applied, not rewritten")
        print(f"  review  written-by-earlier-apply  {rel}  ({m_links[rel]} links; {label})")
    for rel in unplanned:
        print(f"  review  not-in-manifest      {rel}  ({len(plan['files'][rel]['edits'])} links)")
    new_links = sum(len(plan["files"][r]["edits"]) for r in unplanned)
    new_msg = (f"{new_links} safe rewrites in {len(unplanned)} files not in the manifest -- "
               f"re-run the dry run.")
    if earlier and unfinished:
        print(f"ERROR: an earlier apply of run_id {rid} did not complete ({', '.join(unfinished)}) "
              f"and {len(earlier)} files still hold what it wrote -- nothing written. Restore it "
              f"first, then re-run the dry run:", file=sys.stderr)
        print(f"RESTORE: {restore_command(root, rid)}")
        return 2
    # The same guard across run_ids (review h3-R3-DS-1): a failed apply's
    # files have changed, so the next dry run issues a NEW run_id and the
    # check above never sees the old one.
    unf = unfinished_runs(root, exclude=rid)
    if unf:
        report_unfinished(root, unf)
        live = [u for u in unf if u["files"]]
        unreadable = [u for u in unf if not u["files"]]
        if live:
            print(f"ERROR: {len(live)} earlier apply run(s) did not complete and still have writes "
                  f"in the vault -- nothing written. Restore them (RESTORE above), then re-run the "
                  f"dry run.", file=sys.stderr)
        if unreadable:
            print(f"ERROR: {len(unreadable)} earlier apply run(s) have an unreadable apply log -- "
                  f"nothing written. Inspect and set them aside (see above), then re-run the dry "
                  f"run.", file=sys.stderr)
        return 2
    stale_terms = ([("links_previously_applied", earlier_links)] if earlier_links else [])
    if not ready:
        ok = reconcile(planned_links, [("links_applied", 0)] + stale_terms
                       + [("links_skipped_stale", stale_only_links)])
        if new_links:
            # I1 (review h1-I1-1): the ruled sentence only when the re-plan
            # also finds SAFE_REWRITES = 0 -- a stale or empty manifest must
            # not mask safe rewrites in files it never listed.
            print(new_msg + " Nothing written.")
            return 6 if ok else 5
        if planned_links == 0:
            print(NOTHING_TO_DO)
        else:
            print("No manifest file is still applicable -- re-run the dry run.")
        return 0 if ok else 5

    # --max-files N (smoke batch): only the first N READY files, in the
    # manifest's sorted file order, are applied; the rest are deferred -- a
    # settled 'partial' run, not a failure. Stale files are already counted
    # in links_skipped_stale, so they are never also deferred.
    max_files = getattr(args, "max_files", None)
    deferred = ready[max_files:] if max_files is not None else []
    if max_files is not None:
        ready = ready[:max_files]
    deferred_links = sum(len(plan["files"][r]["edits"]) for r in deferred)
    deferred_terms = [("links_deferred", deferred_links)] if max_files is not None else []
    if max_files is not None:
        print(f"Smoke batch (--max-files {max_files}): applying {len(ready)} files; deferred "
              f"{len(deferred)} files ({deferred_links} links) for the full run.")
    lint_before, lint_why = run_lint(root)
    size = args.batch_size if args.batch_size is not None else 40
    batches = [ready[i:i + size] for i in range(0, len(ready), size)]
    log_path = backup_root / "apply-log.json"
    fault = os.environ.get(FAULT_ENV)
    if fault and not _under_tempdir(root):
        print(f"WARNING: {FAULT_ENV} ignored outside a temp vault")
        fault = None
    import datetime
    if log_path.exists():  # an earlier apply of the same plan (restored or complete);
        # kept beside the new log -- run_restore reads every apply-log*.json.
        stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S%f")
        os.replace(log_path, log_path.with_name(f"apply-log.prev-{stamp}.json"))
    log = {"schema_version": APPLY_LOG_SCHEMA, "run_id": rid, "root": _norm_root(root),
           "tool_sha256": _tool_sha256(), "manifest": str(manifest_path),
           "started": datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(),
           "restore_command": restore_command(root, rid),
           "batches": [], "files": {}, "result": "in-progress"}
    if max_files is not None:
        # In the literal's first flush, so a failed smoke batch (rc 3) logs
        # them too (review g2-R2-C1-failed-smoke-log).
        log["max_files"] = max_files
        log["deferred_files"] = list(deferred)
    applied_files = applied_links = 0
    applied_edits: list[dict] = []
    changed: list[str] = []
    committed: list[int] = []
    for bi, batch in enumerate(batches, 1):
        # Write-ahead: every batch file is logged (pre/post hash, backup)
        # BEFORE any write, so a crash mid-batch is still restorable.
        todo = []
        for rel in batch:
            bom, new_text, raw = plan["new"][rel]
            if fault and fault == rel:
                new_text += "\n[[__fault_injected_broken_link__]]\n"
            data = raw if fault == "revert:" + rel else _encode(bom, new_text)
            todo.append((rel, data))
            log["files"][rel] = {"batch": bi, "pre_apply_sha256": plan["files"][rel]["sha256"],
                                 "post_apply_sha256": _sha256(data),
                                 "backup": (Path(".alias-fix-backup") / rid / rel).as_posix(),
                                 "links": len(plan["files"][rel]["edits"]), "status": "pending"}
        log["batches"].append({"batch": bi, "files": list(batch), "status": "writing"})
        _write_json(log_path, log)
        written: list[str] = []
        write_error = None
        for rel, data in todo:
            entry = log["files"][rel]
            # TOCTOU guard: the plan was built from bytes read before this
            # batch loop started. A human (or Dropbox) edit since then must
            # not be overwritten by the planned text -- re-hash immediately
            # before the write and leave a changed file alone.
            try:
                raw_now = (root / rel).read_bytes()
            except OSError:
                raw_now = None
            if raw_now is None or _sha256(raw_now) != entry["pre_apply_sha256"]:
                changed.append(rel)
                entry["status"] = "changed-during-apply"
                print(f"  review  changed-during-apply  {rel}")
                continue
            dest = backup_root / rel
            try:
                if not dest.is_file() or _sha256(dest.read_bytes()) != entry["pre_apply_sha256"]:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    _atomic_write(dest, raw_now)
                ok_backup = _sha256(dest.read_bytes()) == entry["pre_apply_sha256"]
            except OSError:
                ok_backup = False
            if not ok_backup:
                changed.append(rel)
                entry["status"] = "backup-failed"
                print(f"  review  backup-failed         {rel}")
                continue
            if data != raw_now:
                try:
                    _atomic_write(root / rel, data)
                except OSError as e:  # e.g. a Dropbox / AV lock: stop, explain, offer restore
                    entry["status"] = "write-failed"
                    write_error = f"write failed for {rel} ({e.__class__.__name__}: {e})"
                    break
                entry["status"] = "written"
            else:
                entry["status"] = "unchanged"
            written.append(rel)
        _write_json(log_path, log)
        before_alias = sum(plan["files"][r]["counts"]["alias-unique"]
                           + plan["files"][r]["counts"]["alias-unique-held"] for r in written)
        before_broken = sum(plan["files"][r]["counts"]["broken"] for r in written)
        before_canonical = sum(plan["files"][r]["counts"]["canonical"] for r in written)
        n_edits = sum(len(plan["files"][r]["edits"]) for r in written)
        if not written and write_error is None:
            log["batches"][-1]["status"] = "empty"
            _write_json(log_path, log)
            print(f"Batch {bi}/{len(batches)}: 0 files written (all changed during apply)")
            continue
        failed = [write_error] if write_error else []
        try:
            after = recount(root, written, wl)
        except (OSError, UnicodeDecodeError) as e:
            after = None
            failed.append(f"recount could not re-read the batch ({e.__class__.__name__}: {e})")
        if after is not None:
            after_alias = after["alias-unique"] + after["alias-unique-held"]
            if before_alias - after_alias != n_edits:
                failed.append(f"alias-unique drop {before_alias - after_alias} != applied edits {n_edits}")
            if after["broken"] > before_broken:
                failed.append(f"broken count rose {before_broken} -> {after['broken']}")
            # Every edit must land as a LIVE canonical link: an alias drop
            # alone is also what a rewrite swallowed by a code span looks
            # like (regression R151 review 3-R3-3 / ARB-1).
            if after["canonical"] - before_canonical != n_edits:
                failed.append(f"canonical rise {after['canonical'] - before_canonical} "
                              f"!= applied edits {n_edits}")
            # Table shape on the WRITTEN bytes (review arb-M2): plan_text
            # holds any file whose rewrites would move a table line; this
            # re-asserts it on what actually landed.
            reshaped = []
            for r in written:
                try:
                    now_text = _decode((root / r).read_bytes())[1]
                except (OSError, UnicodeDecodeError):
                    continue  # recount above already read these bytes
                if _table_line_flags(now_text) != _table_line_flags(_decode(plan["new"][r][2])[1]):
                    reshaped.append(r)
            if reshaped:
                failed.append(f"table structure changed in {', '.join(reshaped)}")
        if failed:
            log["batches"][-1]["status"] = "failed"
            log["result"] = "postcondition-failed"
            _write_json(log_path, log)
            later = ([r for r in batch if r not in written and r not in changed]
                     + [r for b in batches[bi:] for r in b])
            not_attempted = sum(len(plan["files"][r]["edits"]) for r in later)
            skipped = stale_only_links + sum(len(plan["files"][r]["edits"]) for r in changed)
            print(f"Batch {bi}/{len(batches)}: {len(written)} files, {n_edits} edits -- "
                  f"POSTCONDITION FAILED: {'; '.join(failed)}")
            print(f"STOP. Batch {bi} files are written (originals in {backup_root}); "
                  f"{len(batches) - bi} remaining batches untouched.")
            if committed:
                print(f"Committed batches (postcondition OK, still written): "
                      f"{', '.join(str(b) for b in committed)} ({applied_files} files, "
                      f"{applied_links} links; file lists in {log_path})")
            else:
                print("Committed batches (postcondition OK): none")
            for rel in written:
                print(f"  batch file  {rel}")
            reconcile(planned_links, [("links_applied", applied_links),
                                      ("links_written_failed_batch", n_edits),
                                      ("links_not_attempted", not_attempted)] + stale_terms
                      + [("links_skipped_stale", skipped)] + deferred_terms, log_path)
            print(f"Apply log: {log_path}")
            print("To put back every file this apply wrote (only files still exactly as written; "
                  "anything edited since is listed REVIEW_REQUIRED and left alone):")
            print(f"RESTORE: {restore_command(root, rid)}")
            return 3
        log["batches"][-1]["status"] = "committed"
        _write_json(log_path, log)
        committed.append(bi)
        applied_files += len(written)
        applied_links += n_edits
        for r in written:
            applied_edits += plan["files"][r]["edits"]
        print(f"Batch {bi}/{len(batches)}: {len(written)} files, {n_edits} edits -- postcondition OK "
              f"(alias-unique -{before_alias - after_alias}, canonical +{after['canonical'] - before_canonical}, "
              f"broken {before_broken} -> {after['broken']})")
    log["result"] = "partial" if deferred else "complete"
    _write_json(log_path, log)
    changed_links = sum(len(plan["files"][r]["edits"]) for r in changed)
    skipped_links = stale_only_links + changed_links
    print(f"Applied: {applied_links} edits across {applied_files} files. "
          f"Skipped: {len(stale) + len(unplanned) + len(changed)} files "
          f"(changed/new since dry run, or changed during apply).")
    for rel in changed:
        print(f"  REVIEW_REQUIRED  changed-during-apply  {rel}  ({len(plan['files'][rel]['edits'])} links)")
    rc = 0
    if not reconcile(planned_links, [("links_applied", applied_links)] + stale_terms
                     + [("links_skipped_stale", skipped_links)] + deferred_terms, log_path):
        rc = 5
    if applied_files:
        print(f"Backups: {backup_root}")
        print(f"Apply log: {log_path}")
        print(f"Undo: {restore_command(root, rid)}")
    # Lint reconciliation (I9): lint's own ALIAS-ONLY / BROKEN movement must
    # equal the applied links lint filed under those sections. EXEMPT files
    # are never written, so they cannot move either count.
    kinds: dict[str, int] = {}
    for e in applied_edits:
        kinds[e.get("lint_kind", "unknown")] = kinds.get(e.get("lint_kind", "unknown"), 0) + 1
    p_ao, p_br = kinds.pop("alias-only", 0), kinds.pop("broken", 0)
    lint_after, why_after = run_lint(root) if lint_before is not None else (None, lint_why)
    if lint_before is None:
        # Unmeasurable before any write too (no lint.py, an old lint):
        # reported, not a failure of this apply.
        print(f"LINT RECONCILIATION: NOT MEASURED -- {lint_why}. "
              f"Predicted: ALIAS-ONLY -{p_ao}, BROKEN -{p_br}; check with lint.py --summary.")
    elif lint_after is None:
        # Measured before the writes, unmeasurable after them: I9 cannot be
        # checked exactly when something went wrong -- a failure, not a pass
        # (regression R151 review h2-I9-lint-after-unmeasured-exit0).
        print(f"LINT RECONCILIATION FAILED: NOT MEASURED after the writes -- {why_after}. "
              f"Measured before: ALIAS-ONLY {lint_before['alias_only']}, BROKEN "
              f"{lint_before['broken']}. Predicted: ALIAS-ONLY -{p_ao}, BROKEN -{p_br}; run "
              f"lint.py --summary and compare, or restore.")
        rc = rc or 4
    else:
        d_ao = lint_before["alias_only"] - lint_after["alias_only"]
        d_br = lint_before["broken"] - lint_after["broken"]
        ok_ao, ok_br = d_ao == p_ao, d_br == p_br
        print(f"LINT RECONCILIATION: links_applied {applied_links} = lint-alias-only {p_ao} + "
              f"lint-broken {p_br} + other {sum(kinds.values())}"
              + (f" {dict(sorted(kinds.items()))}" if kinds else ""))
        print(f"  ALIAS-ONLY {lint_before['alias_only']} -> {lint_after['alias_only']} "
              f"(drop {d_ao}) vs links_applied lint-alias-only {p_ao}: {'OK' if ok_ao else 'MISMATCH'}")
        print(f"  BROKEN {lint_before['broken']} -> {lint_after['broken']} (drop {d_br}) vs "
              f"links_applied lint-broken {p_br}: {'OK' if ok_br else 'MISMATCH'} (names lint files "
              f"under BROKEN, not ALIAS-ONLY: dotted names R148, '/' or ':' names R149)")
        if not (ok_ao and ok_br):
            print("  MISMATCH reason: lint scans the whole vault, so a file outside this apply "
                  "changed between the two lint runs, or lint.py/_wikilib disagree with the "
                  "prediction recorded in the manifest. Run lint.py (full) and compare with the "
                  "apply log.")
            rc = rc or 4
    if new_links:
        # Same situation, same exit as the ready-less path (review
        # h3-R3-I1-unplanned-exit0): a clean-looking apply must not leave
        # SAFE_REWRITES > 0 in files the manifest never listed unmentioned.
        print(new_msg)
        rc = rc or 6
    if earlier and applied_files:
        print(f"NOTE: this apply continues run_id {rid}; --restore {rid} now undoes the earlier "
              f"apply of this run and this apply together.")
    if max_files is not None:
        # The click-test hint only after a verified apply, and 'for the
        # rest' only when something is left (review g1-R1-C1-smoke-hint-ignores-rc).
        written_files = [r for r, e in log["files"].items() if e.get("status") == "written"]
        # 'Nothing left' only when no rewrite is pending anywhere: no deferred,
        # stale (incl. an earlier-apply file the re-plan still edits),
        # changed-during-apply or unplanned file (review g2-R2-C1-hint-nothing-left).
        left = (list(deferred) + stale_only + [r for r in earlier if r in plan["files"]]
                + changed + unplanned)
        if rc != 0:
            print(f"SMOKE: apply did not verify (rc {rc}) -- do not click-test or continue; act on "
                  f"the messages above (RESTORE if printed).")
        elif not written_files:
            print("SMOKE: 0 files written; re-run the dry run.")
        elif left:
            print(f"SMOKE: {len(written_files)} files applied; click 2-3 rewritten links in Obsidian, "
                  f"then re-run the dry run and --apply for the rest.")
        else:
            print(f"SMOKE: {len(written_files)} files applied (all ready files; nothing left to "
                  f"apply); click 2-3 rewritten links in Obsidian, then --verify.")
        for rel in written_files:
            print(f"  smoke-applied  {rel}")
    return rc


def _log_started(lg: dict):
    """An apply log's 'started' as an aware datetime, or None."""
    import datetime
    try:
        dt = datetime.datetime.fromisoformat(str(lg.get("started")))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else None


def unfinished_runs(root: Path, exclude: str | None = None) -> list[dict]:
    """Every run under <root>/.alias-fix-backup whose CURRENT apply log
    (apply-log.json) is not settled (result not in SETTLED_RESULTS: complete,
    partial, or restored clean) and still
    has files exactly as it wrote them (current hash == a post_apply_sha256
    that differs from the pre hash). A failed apply is otherwise invisible
    to the next dry run, which issues a NEW run_id (review h3-R3-DS-1).

    Only apply-log.json is read: a rotated apply-log.prev-*.json is never
    the unfinished one, and a corrupt one must not block other runs (review
    t1-DS1-b). A file whose bytes a LATER complete apply (any run_id) also
    records as its post hash is that apply's write, not this one's (review
    t1-DS1-a). An unreadable current log is reported with files=[] and an
    "error" (never skipped: its files could be the live ones). Each entry:
    {"run_id", "result", "files"[, "error"][, "restore_unsettled"]} --
    restore_unsettled: an earlier --restore left REVIEW_REQUIRED rows, so
    the set-aside remedy is printed too. `exclude`: a run_id checked
    elsewhere (the manifest's own)."""
    out: list[dict] = []
    base = root / ".alias-fix-backup"
    if not base.is_dir():
        return out
    dirs = sorted(p for p in base.iterdir() if p.is_dir() and RUN_ID_RE.fullmatch(p.name))
    completes = None  # [(started, files)] of every readable complete log, built on need
    for d in dirs:
        if d.name == exclude or not (d / "apply-log.json").is_file():
            continue
        try:
            lg = _read_apply_log(d / "apply-log.json")
        except ValueError as e:
            out.append({"run_id": d.name, "result": "unreadable log", "error": str(e), "files": []})
            continue
        if lg.get("result") in SETTLED_RESULTS:
            continue
        live = []
        for rel, e in sorted(lg["files"].items()):
            post, pre = e.get("post_apply_sha256"), e.get("pre_apply_sha256")
            if not post or post == pre:
                continue
            try:
                cur = _sha256((root / rel).read_bytes())
            except OSError:
                continue
            if cur == post:
                live.append(rel)
        started = _log_started(lg)
        if live and started is not None:
            if completes is None:
                completes = []
                for dd in dirs:
                    for p in sorted(dd.glob("apply-log*.json")):
                        try:
                            c = _read_apply_log(p)
                        except ValueError:
                            continue  # skipping only suppresses less: fail-safe
                        cs = _log_started(c)
                        if c.get("result") in WRITER_RESULTS and cs is not None:
                            completes.append((cs, c["files"]))
            live = [rel for rel in live
                    if not any(cs > started and isinstance(files.get(rel), dict)
                               and files[rel].get("post_apply_sha256")
                               == lg["files"][rel]["post_apply_sha256"]
                               for cs, files in completes)]
        if live:
            rs = lg.get("restores")
            tried = isinstance(rs, list) and any(
                isinstance(r, dict) and isinstance(r.get("review_required"), list)
                and needs_set_aside(x.get("reason", "") if isinstance(x, dict) else x
                                    for x in r["review_required"])
                for r in rs)
            out.append({"run_id": d.name, "result": lg.get("result"), "files": live,
                        "restore_unsettled": tried})
    return out


def report_unfinished(root: Path, runs: list[dict]) -> None:
    for u in runs:
        if u.get("error"):
            # No RESTORE line: --restore refuses an unreadable log, so it is
            # not a remedy (review t1-DS1-b).
            bdir = root / ".alias-fix-backup" / u["run_id"]
            print(f"WARNING: apply {u['run_id']} has an unreadable log; its files cannot be "
                  f"checked ({u['error']}).")
            print(f"  Inspect {bdir} (apply-log*.json and the original pages it holds) and put "
                  f"back by hand any page that still holds that apply's edits; then set the run "
                  f"aside by renaming that directory to {u['run_id']}-inspected (or moving it out "
                  f"of .alias-fix-backup). This check skips it from then on.")
            continue
        print(f"WARNING: unfinished apply {u['run_id']} ({u['result']}) still has "
              f"{len(u['files'])} files as it wrote them -- restore it before any new apply:")
        for rel in u["files"]:
            print(f"  written-by-unfinished-apply  {rel}")
        print(f"RESTORE: {restore_command(root, u['run_id'])}")
        if u.get("restore_unsettled"):
            # RESTORE was already run and left REVIEW_REQUIRED rows: re-running
            # it will not settle the run (review t2-DS1-unsettleable-live-run).
            print(set_aside_remedy(root, u["run_id"]))


def _read_apply_log(p: Path) -> dict:
    """One apply log, validated; ValueError when unreadable."""
    import json
    try:
        lg = json.loads(p.read_bytes().decode("utf-8"))
        if not isinstance(lg, dict) or not isinstance(lg.get("files"), dict):
            raise ValueError("no files map")
        for e in lg["files"].values():
            if not isinstance(e, dict) or not {"pre_apply_sha256", "post_apply_sha256",
                                               "backup"} <= set(e):
                raise ValueError("file entry lacks pre/post hash or backup")
    except (OSError, ValueError, TypeError) as e:
        raise ValueError(f"unreadable apply log {p}: {e}") from None
    return lg


def _apply_logs_tolerant(backup_root: Path) -> tuple[list[tuple[Path, dict]], list[str]]:
    """Every apply log of one run_id, oldest first: apply-log.prev-*.json
    (rotated by a re-apply; the stamp sorts chronologically) then
    apply-log.json, plus the names of the prev logs that could not be read.
    The CURRENT apply-log.json is read strictly (ValueError); an unreadable
    prev log is returned by name instead of raising (review t2-DS1), and a
    caller must never treat it as settled -- a skipped log is files a
    restore would silently leave behind, so restore lists it
    REVIEW_REQUIRED and the same-run_id gate skips it only for a settled
    run."""
    logs: list[tuple[Path, dict]] = []
    bad: list[str] = []
    for p in sorted(backup_root.glob("apply-log.prev-*.json")):
        try:
            logs.append((p, _read_apply_log(p)))
        except ValueError:
            bad.append(p.name)
    cur = backup_root / "apply-log.json"
    if cur.is_file():
        logs.append((cur, _read_apply_log(cur)))
    return logs, bad


def needs_set_aside(reasons) -> bool:
    """True when a restore's REVIEW_REQUIRED reasons include one that
    re-running RESTORE will not clear. A locked page
    ('restore-write-failed') is transient -- re-run the same RESTORE -- so
    a lock-only restore never gets the set-aside remedy (one predicate for
    run_restore and unfinished_runs; review t2-DS1-unsettleable)."""
    return any(not str(r).startswith("restore-write-failed") for r in reasons)


def set_aside_remedy(root: Path, rid: str) -> str:
    """The manual way out when RESTORE cannot settle a run (review t2-DS1):
    renaming the run directory takes it out of RUN_ID_RE, so neither the
    unfinished-run scan nor the same-run_id gate reads it again."""
    bdir = root / ".alias-fix-backup" / rid
    return (f"SET ASIDE: RESTORE cannot settle run {rid}. Put the REVIEW_REQUIRED files right by "
            f"hand (or accept them as they are), then set the run aside by renaming {bdir} to "
            f"{rid}-inspected (or moving it out of .alias-fix-backup); the unfinished-run check "
            f"stops guarding it from then on.")


def run_restore(rid: str, root: Path) -> int:
    """Concurrency-safe undo of one apply (I8): a backup is copied back only
    when the file's current hash equals the post_apply_sha256 the apply
    recorded AND the backup's own hash equals pre_apply_sha256. Anything else
    is REVIEW_REQUIRED and left untouched -- newer work is never overwritten.
    Covers every apply of the run: apply-log.json plus each rotated
    apply-log.prev-*.json (a current hash equal to ANY logged post hash)."""
    import datetime
    if not RUN_ID_RE.fullmatch(rid or ""):
        return _reject(f"--restore takes a 16-hex-digit run_id, got {rid!r}")
    backup_root = root / ".alias-fix-backup" / rid
    log_path = backup_root / "apply-log.json"
    if not log_path.is_file():
        return _reject(f"no apply log at {log_path}")
    # The current log is read strictly (the ledger is written back to it);
    # an unreadable rotated prev log no longer refuses the whole restore --
    # the readable logs are restored and each unreadable one is a
    # REVIEW_REQUIRED row, so the run stays unsettled (review
    # t2-DS1-printed-restore-refuses).
    try:
        logs, bad_prev = _apply_logs_tolerant(backup_root)
    except ValueError as e:
        return _reject(str(e))
    log = logs[-1][1]  # apply-log.json: the restore ledger is written here
    # A log root that differs from --root while the run directory sits
    # inside --root means the vault folder itself was moved or renamed
    # (review arb-M1-moved-vault-wedge): the per-file pre/post hash gates
    # below keep the restore safe, so it proceeds with a NOTE. A run
    # directory outside --root is still refused.
    try:
        backup_root.resolve().relative_to(root.resolve())
        inside = True
    except ValueError:
        inside = False
    moved_from: list[str] = []
    for lp_, lg in logs:
        if lg.get("root") != _norm_root(root):
            if not inside:
                return _reject(f"apply log {lp_.name} root {lg.get('root')} is not --root "
                               f"{_norm_root(root)}")
            if str(lg.get("root")) not in moved_from:
                moved_from.append(str(lg.get("root")))
    # Every apply of this run_id, not only the latest (review h1-I8-1): a
    # re-apply rotates the earlier log to apply-log.prev-*.json, and the
    # files that apply wrote must still be restorable by this command.
    merged: dict[str, dict] = {}
    for _, lg in logs:
        for rel, e in lg["files"].items():
            # write-failed: os.replace is atomic, so the file is either the
            # original (ALREADY_ORIGINAL) or the planned bytes (restorable).
            if e.get("status") not in ("pending", "written", "unchanged", "write-failed"):
                continue  # never written by that apply (changed-during-apply, backup-failed)
            m = merged.setdefault(rel, {"pre": set(), "post": set(), "backup": e["backup"]})
            m["pre"].add(e["pre_apply_sha256"])
            m["post"].add(e["post_apply_sha256"])
            m["backup"] = e["backup"]
    print(f"=== ALIAS-ONLY LINK FIX (regression R151) -- RESTORE run_id {rid} ===")
    for old in moved_from:
        print(f"NOTE: vault moved from {old} (the apply log's root) to {_norm_root(root)}; "
              f"restoring by the per-file hash checks.")
    if len(logs) > 1:
        print(f"Apply logs read: {', '.join(p.name for p, _ in logs)}")
    restored, already, review = [], [], []
    for rel, m in sorted(merged.items()):
        if len(m["pre"]) != 1:
            review.append((rel, "apply logs disagree on pre_apply_sha256"))
            continue
        e = {"pre_apply_sha256": next(iter(m["pre"])), "backup": m["backup"]}
        target = root / rel
        try:
            cur = _sha256(target.read_bytes())
        except OSError:
            cur = None
        if cur == e["pre_apply_sha256"]:
            already.append(rel)
            continue
        if cur not in m["post"]:
            review.append((rel, "changed-since-apply (current hash is not what the apply wrote)"))
            continue
        bpath = (root / e["backup"]).resolve()
        try:
            bpath.relative_to(backup_root.resolve())
            bdata = bpath.read_bytes()
        except (ValueError, OSError):
            review.append((rel, "backup-missing"))
            continue
        if _sha256(bdata) != e["pre_apply_sha256"]:
            review.append((rel, "backup-corrupt (backup hash is not pre_apply_sha256)"))
            continue
        try:
            _atomic_write(target, bdata)
            ok = _sha256(target.read_bytes()) == e["pre_apply_sha256"]
        except OSError as ex:
            # A locked page (Obsidian / Dropbox / AV): list it, keep going,
            # and still write the ledger (review h2-R2-RESTORE-LOCK).
            review.append((rel, f"restore-write-failed ({ex.__class__.__name__}: {ex})"))
            continue
        if not ok:
            review.append((rel, "restore-verify-failed"))
            continue
        restored.append(rel)
    for name in bad_prev:
        review.append((name, "unreadable rotated apply log -- any file only it wrote must be "
                             "checked by hand"))
    # Leftover tmp files from an apply whose write failed before this fix
    # (or was killed mid-replace): never part of the vault (h2-R2-TMP-LEAK).
    swept = []
    for rel in sorted({r for _, lg in logs for r in lg["files"]}):
        tmp = (root / rel).with_name(Path(rel).name + TMP_SUFFIX)
        try:
            if tmp.is_file():
                tmp.unlink()
                swept.append(tmp.relative_to(root).as_posix())
        except OSError:
            pass
    for t in swept:
        print(f"  removed leftover  {t}")
    for rel in restored:
        print(f"  restored          {rel}")
    for rel, why in review:
        print(f"  REVIEW_REQUIRED   {rel}  ({why})")
    print(f"RESTORED = {len(restored)}")
    print(f"ALREADY_ORIGINAL = {len(already)}")
    print(f"REVIEW_REQUIRED = {len(review)}")
    if needs_set_aside(w for _, w in review):
        print(set_aside_remedy(root, rid))
    log.setdefault("restores", []).append({
        "at": datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(),
        "restored": restored, "already_original": already, "tmp_removed": swept,
        "review_required": [{"file": r, "reason": w} for r, w in review]})
    if not review:
        # Settled: no file of any apply of this run still holds its writes,
        # so unfinished_runs() stops guarding it (review t1-DS1-a). A later
        # apply's legitimate write to the same page must never be offered
        # to this run's RESTORE as "unfinished".
        log["result"] = "restored"
    _write_json(log_path, log)
    return 0 if not review else 1


def main() -> int:
    # allow_abbrev=False: the new flags must be spelled in full. The pre-R151
    # parser (--apply, --root, --help only) accepted every unambiguous prefix;
    # those exact spellings stay as hidden aliases, so default mode keeps its
    # pre-PR command lines (--r PATH, --a) byte-identical (review
    # h2-I2-argparse-abbrev-divergence; I2 pins both).
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 allow_abbrev=False)
    ap.add_argument("--apply", action="store_true", help="Actually write the changes (default is dry-run).")
    ap.add_argument("--root", type=Path, default=PROJECT_ROOT, help="Vault root (default: script's parent vault).")
    for full, kw in (("--apply", dict(action="store_true", dest="apply")),
                     ("--root", dict(type=Path, dest="root")),
                     ("--help", dict(action="help"))):
        for n in range(3, len(full)):
            ap.add_argument(full[:n], help=argparse.SUPPRESS, default=argparse.SUPPRESS, **kw)
    ap.add_argument("--alias-only", action="store_true",
                    help="regression R151 mode: rewrite uniquely-resolvable alias-only links to [[slug|Alias]].")
    ap.add_argument("--manifest", type=Path, default=None,
                    help="--alias-only manifest path (default <root>/_meta/alias-fix-manifest.json).")
    ap.add_argument("--batch-size", type=int, default=None,
                    help="--alias-only --apply: files per batch (default 40).")
    ap.add_argument("--max-files", type=int, default=None, metavar="N",
                    help="--alias-only --apply: smoke batch -- apply only the first N applicable "
                         "files in manifest order (result 'partial'); then click-test, re-run the "
                         "dry run and --apply for the rest.")
    ap.add_argument("--verify", action="store_true",
                    help="--alias-only: re-plan without writing; exit 0 iff SAFE_REWRITES = 0.")
    ap.add_argument("--restore", metavar="RUN_ID", default=None,
                    help="--alias-only: put back the originals of apply RUN_ID (only files still "
                         "exactly as that apply wrote them).")
    args = ap.parse_args()

    root = args.root.resolve()
    if not (root / "wiki").is_dir():
        ap.error(f"not a vault (missing wiki/): {root}")
    if not args.alias_only:
        if (args.verify or args.manifest is not None or args.batch_size is not None
                or args.restore is not None or args.max_files is not None):
            ap.error("--verify, --manifest, --batch-size, --max-files and --restore require --alias-only")
        return run_backtick(args, root)
    if args.verify and args.apply:
        ap.error("--verify and --apply are mutually exclusive")
    if args.restore is not None and (args.apply or args.verify):
        ap.error("--restore is mutually exclusive with --apply and --verify")
    if args.batch_size is not None and args.batch_size < 1:
        ap.error("--batch-size must be >= 1")
    if args.max_files is not None and not args.apply:
        ap.error("--max-files requires --apply (a smoke batch of an existing manifest)")
    if args.max_files is not None and args.max_files < 1:
        ap.error("--max-files must be >= 1")
    return run_alias(args, root)


if __name__ == "__main__":
    sys.exit(main())
