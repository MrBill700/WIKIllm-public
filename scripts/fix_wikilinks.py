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
target lint would file as BROKEN or GHOST -- a dotted slug (dotted-name mangling), a '/' or
':' slug ('/' or ':' name mangling), or a page named date / published (review
'lint-mangled-slug'). The modes are mutually
explicit: --alias-only never runs the backtick pass, the default mode never
rewrites alias links, and --manifest / --batch-size / --max-files /
--verify / --restore without --alias-only are usage errors (--max-files
also needs --apply and N >= 1).

Operational state (ADR-0005, regression R153) -- the manifest, the apply
logs and the page backups -- lives in a STATE DIRECTORY outside the vault and
outside any sync service: default %LOCALAPPDATA%\\WIKIllm\\alias-fix\\
<vault-folder-name>-<hash8>\\ (off Windows only, ~/.local/state/... when
LOCALAPPDATA is unset; on Windows an unset LOCALAPPDATA is refused, and so is
a Microsoft Store Python, whose %LOCALAPPDATA% writes are virtualized into a
private package folder -- on many Windows installs `python3` is the Store shim; run
`python -B`; hash8 =
sha256 of the normcased resolved vault path), holding manifest.json and
backup/<run_id>/. --state-dir PATH is the only override (a flag, never an env
variable); a state dir, its backup/ tree or --manifest inside the vault is
refused -- by file-system identity as well as text, so \\\\?\\, admin-share
and trailing-space spellings are caught (a component ending in a space or
a dot is refused outright). One state dir belongs to exactly one vault
path: its vault.json records it, and a --state-dir (or registered dir)
naming another vault is refused (untrusted). A
--state-dir is registered in the default dir's state-dirs.json, and the
unfinished-run check and --restore search the default dir, every registered
dir and legacy in-vault state (.alias-fix-backup/, _meta/alias-fix-manifest.json);
an unreachable registered dir fails --apply / --restore closed.
--forget-state-dir PATH drops a pointer only after proving that dir holds no
unfinished run. Every run prints STATE: <dir> (and REGISTERED IN: <registry>
for a non-default dir); a dry run writes NOTHING inside the vault. State is
keyed by path: same-folder-name state for another path only warns (settled)
or blocks --apply / --restore (unfinished); --rebind-state OLD_PATH attaches
a moved vault's state, only when OLD_PATH no longer exists. Settle or restore
runs before moving a vault. --migrate-legacy-state (dry run; with --apply:
copy -> verify -> prove discovery -> register -> delete) moves legacy (pre-ADR-0005)
in-vault state out; run it before the next --apply on such a vault. Run each
--apply alone and read its result before any next command.

Lock retry (regression R157, ADR-0005): vault pages stay in Dropbox, so every
page write, backup, state save and restore write/read retries ONLY Windows
lock errors (winerror 5 / 32 / 33) with backoff 0.1, 0.2, 0.4, 0.8, 1.6,
3.2 s; anything else fails at once. A locked READ through open() arrives as
PermissionError errno 13 with no winerror, so it is not retried -- but it is
never read as a human edit: apply stops the batch (exit 3), restore stops at
it and lists it and every later page it would have written transient
(restore-read-failed / restore-not-attempted, RESTORE INCOMPLETE), the unfinished-run
check counts the page live. Immediately before its first attempt
and before each retry a page write re-hashes its target (apply: still the
pre-apply bytes, restore: still what the apply wrote -> write; already the
intended output
-> counted as written; anything else -> a concurrent edit, not written); a
re-hash read refused by a lock never falls through to a write -- it waits
and re-reads on the same budget. An existing backup that is not
byte-identical to the original is a conflict. State reads (apply logs,
registry, manifest) retry a lock too.

Run lock (ADR-0006 / ADR-0007, regression R161): fix_wikilinks operations
that can mutate vault pages or shared operational state serialize through
one per-vault run lock -- one lock-participating writer per vault; external
editors are outside that guarantee. Backtick-mode --apply takes it too
(mutual exclusion and lock recovery only -- no manifest, backups or state;
it refuses, exit 2, where the lock is unreachable: LOCALAPPDATA unset or a
Microsoft Store Python); its dry run takes none. Every alias run that may write operational state (dry run, --apply,
--restore, --migrate-legacy-state --apply, --rebind-state,
--forget-state-dir, and --verify / the migrate dry run when given
--state-dir) holds run.lock in the vault's DEFAULT state dir, whatever
--state-dir it uses; plain --verify takes none. A held lock refuses at once
(exit 2, nothing written) and names its holder. --break-lock (standalone)
removes it only when the holder is provably gone; a lock whose holder
cannot be proven gone must be deleted by hand after confirming no run is
live. On Windows, --break-lock is safe alongside other fix_wikilinks runs:
it judges and deletes the same run.lock object through one exclusive
handle, so it cannot delete a live or replacement lock that it did not
judge stale (regression R170, ADR-0008). Off Windows, automatic --break-lock
refuses; remove run.lock manually only after confirming no fix_wikilinks
run is live.
An exhausted retry stops the batch cleanly (exit 3 with RESTORE) -- no file
is ever skipped for a later one; restore likewise stops at the first page it
cannot complete (every later page = restore-not-attempted, re-run finishes). Every --apply / --restore ends with
'LOCK RETRIES: N (max wait Xs)'. The journal keeps its write-ahead shape
(per batch: intent, after-write, committed saves; then a final save), so a
crash anywhere is exactly undoable: restore accounts for every logged page
once as RESTORED / ALREADY_ORIGINAL / REVIEW_REQUIRED. Read a restore's exit
code and all three counts (and any 'RESTORE INCOMPLETE' line), never
RESTORED alone.

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
    python scripts/fix_wikilinks.py --alias-only --root PATH --migrate-legacy-state [--apply]
    python scripts/fix_wikilinks.py --alias-only --root PATH --state-dir DIR    # non-default state dir
    python scripts/fix_wikilinks.py --alias-only --root PATH --forget-state-dir DIR
    python scripts/fix_wikilinks.py --alias-only --root PATH --rebind-state OLD_PATH
    python scripts/fix_wikilinks.py --alias-only --root PATH --break-lock  # clear a STALE run lock
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
# (the R151 case) and BROKEN (a dotted / '/' / ':' alias lint mangles
# -- grey in Obsidian all the same). "unknown" = no lint verdict supplied
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
# be filed by lint as BROKEN (dotted, '/' or ':' name mangling) or GHOST (date /
# published) instead of canonical (review h3-R3-I9-dotted-slug).
LINT_MANGLED_SLUG_REASON = ("lint-mangled-slug (dotted, '/' or ':' name, or ghost: lint would file the rewritten "
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
            # Competing semantics: never rewritten, a human decides (R151 I6).
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
            # as ALIAS-ONLY, or as BROKEN through dotted / '/' / ':' name mangling,
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
                # h3-R3-I9-dotted-slug): a dotted slug (splitext), a '/'
                # or ':' slug (basename) or a slug lint treats as a clipper
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
    BROKEN (dotted / '/' / ':' name mangling); a lint-canonical edit never reaches the edit list.
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


# ---------------------------------------------------------------------------
# Lock retry (regression R157, ADR-0005 R4). A synced folder (Dropbox) or an AV /
# indexer briefly holds a just-written file open; Windows then refuses the
# rename onto it (WinError 5 access denied, 32 sharing violation, 33 lock
# violation). Only those three codes are retried -- anything else fails at
# once -- with a fixed backoff (do not lengthen it: every lock measured
# cleared within 0.26 s). The hash rule a retry re-checks differs per write
# type: a page write (_write_page), a backup (_write_backup), a local state
# save (_write_json).
# ---------------------------------------------------------------------------

LOCK_WINERRORS = (5, 32, 33)
LOCK_BACKOFF = (0.1, 0.2, 0.4, 0.8, 1.6, 3.2)
# TEST-ONLY lock fault hook, honored only when --root is under the temp dir
# (inert elsewhere, with a WARNING): a JSON object {"<kind>:<name>": N} or
# {"<kind>:<name>": {"n": N, "code": 5, "skip": K}} -- the first K matching
# attempts pass, the next N raise an OSError with that winerror (default 5);
# "code": null raises PermissionError errno 13 with NO winerror -- the shape
# CPython's open() (read_bytes / write_bytes) gives a real Windows sharing
# violation, which the R4 predicate (winerror 5/32/33) never retries.
# kind: page (apply page write), backup (apply backup write), restore
# (restore page write), read (a vault page read), bread (a backup read by
# restore), bcheck (apply reading an existing backup), recheck / brecheck
# (the re-hash read of a page / backup between two write attempts), state (a
# state-file save; name = its file name), stateread (a state-file read:
# apply logs, registry, vault id, manifest), unlock (the run lock's delete:
# on Windows the exclusive compare-and-delete open used by BOTH release and
# --break-lock (ADR-0008); off Windows release's unlink; name = run.lock),
# dispose (Windows only: the delete disposition set through that exclusive
# handle AFTER the read and the judgment chose to delete; a fault here is a
# refused disposition, raised as LockDeleteRefused exactly like a real
# SetFileInformationByHandle failure -- never retried, never reopened, the
# file untouched (ADR-0008 Q4, option A); name = run.lock).
# name: the
# vault-relative path (or state file name), or * for every one.
LOCK_FAULT_ENV = "FIX_WIKILINKS_TEST_LOCKS"
_LOCKS: dict = {"retries": 0, "max_wait": 0.0, "faults": None}


class LockExhausted(OSError):
    """A lock error that outlasted every LOCK_BACKOFF retry. An OSError with
    no winerror, so no outer retry ever retries it again."""


def _is_lock(e: BaseException) -> bool:
    return isinstance(e, OSError) and getattr(e, "winerror", None) in LOCK_WINERRORS


def _lock_sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)


def arm_lock_faults(root: Path) -> None:
    """Reset the LOCK RETRIES counters and (temp vaults only) load the
    test-only lock fault hook."""
    import json
    import os
    _LOCKS.update(retries=0, max_wait=0.0, faults=None)
    spec = os.environ.get(LOCK_FAULT_ENV)
    if not spec:
        return
    if not _under_tempdir(root):
        print(f"WARNING: {LOCK_FAULT_ENV} ignored outside a temp vault")
        return
    faults = {}
    for key, v in json.loads(spec).items():
        v = v if isinstance(v, dict) else {"n": v}
        code = v.get("code", 5)
        faults[str(key)] = {"n": int(v.get("n", 0)), "code": None if code is None else int(code),
                            "skip": int(v.get("skip", 0))}
    _LOCKS["faults"] = faults


def _inject_lock(kind: str, name: str, path) -> None:
    f = _LOCKS["faults"]
    if not f:
        return
    for key in (f"{kind}:{name}", f"{kind}:*"):
        spec = f.get(key)
        if spec is None:
            continue
        if spec["skip"] > 0:
            spec["skip"] -= 1
            return
        if spec["n"] > 0:
            import errno
            spec["n"] -= 1
            if spec["code"] is None:
                # the shape CPython's open() raises for a real Windows
                # sharing violation: errno 13, winerror None
                raise PermissionError(errno.EACCES, f"[test] injected errno-only refusal ({key})",
                                      str(path))
            raise OSError(errno.EACCES, f"[test] injected lock ({key})", str(path), spec["code"])
        return


def lock_report() -> str:
    return f"LOCK RETRIES: {_LOCKS['retries']} (max wait {_LOCKS['max_wait']:.1f}s)"


class _Unverified:
    """A recheck whose own read of the target was refused by a lock: the
    target's current bytes are UNKNOWN, so the write must not be retried."""

    def __init__(self, err: OSError):
        self.err = err


def _with_lock_retry(op, desc: str, recheck=None):
    """op() under the lock retry. After each backoff sleep, recheck() (when
    given) decides from the target's CURRENT bytes: None = retry op; an
    _Unverified (the re-hash read itself hit a lock) = do NOT call op, sleep
    the next backoff and recheck again -- a write only ever follows a
    successful re-hash (R4a), on the same single budget; any other value =
    return it without retrying. A non-lock OSError propagates at once; a lock
    that outlasts every retry raises LockExhausted."""
    waited = 0.0
    call_op = True
    last: OSError | None = None
    for delay in LOCK_BACKOFF + (None,):
        if call_op:
            try:
                return op()
            except OSError as e:
                if not _is_lock(e):
                    raise
                last = e
        if delay is None:
            raise LockExhausted(f"{desc}: lock retries exhausted after {len(LOCK_BACKOFF)} "
                                f"retries ({waited:.1f}s): {last}") from last
        _lock_sleep(delay)
        waited += delay
        _LOCKS["retries"] += 1
        _LOCKS["max_wait"] = max(_LOCKS["max_wait"], waited)
        call_op = True
        if recheck is not None:
            verdict = recheck()
            if isinstance(verdict, _Unverified):
                call_op, last = False, verdict.err
                continue
            if verdict is not None:
                return verdict
    raise AssertionError("unreachable")


def _read_retry(path: Path, kind: str = "read", name: str | None = None) -> bytes:
    """read_bytes under the lock retry (reads are idempotent: no hash rule).
    Only a winerror 5/32/33 is retried, and CPython's open() does not raise
    one for a real Windows sharing violation (PermissionError errno 13, no
    winerror): such a read fails at once and every caller handles it as
    'cannot read', never as 'changed' (ADR-0005 consequences)."""
    def op():
        _inject_lock(kind, name if name is not None else path.name, path)
        return path.read_bytes()
    return _with_lock_retry(op, f"read {name or path}")


def _write_page(root: Path, rel: str, data: bytes, retry_if: set, kind: str = "page") -> str:
    """Write a vault page under the lock retry (R4a). The target is re-hashed
    IMMEDIATELY before the first attempt and before each retry: still one of
    `retry_if` (apply: the pre-apply hash; restore: a hash the apply wrote)
    -> write / retry; already exactly `data` -> 'present' before any attempt
    (nothing to write) or 'already' after a refused attempt (the write
    landed; counted as written, never rewritten); anything else -> 'changed'
    (a concurrent edit: do not write). The first-attempt check closes the
    gap an earlier lock backoff opens between the caller's own hash check
    and this write (a backup retry, earlier locked pages of a restore): a
    human save in that gap is never overwritten. A re-hash read that is
    itself refused by a lock never falls through to a write: the loop waits
    the next backoff and re-reads (one budget). Returns 'written' /
    'already' / 'present' / 'changed'; a page read locked past the retry
    raises LockExhausted."""
    path = root / rel

    def op():
        _inject_lock(kind, rel, path)
        _atomic_write(path, data)
        return "written"

    def verdict(now: bytes, landed: str):
        if now == data:
            return landed
        return None if _sha256(now) in retry_if else "changed"

    def recheck():
        # A lock on this read proves nothing about the page: never fall
        # through to a write (a human save may have landed during the
        # backoff) -- _Unverified makes the retry loop wait and re-read.
        # A read refused for any OTHER reason is not evidence of an edit
        # either: it raises (fails at once, R4) -- never 'changed'.
        try:
            _inject_lock("recheck", rel, path)
            now = path.read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return "changed"  # gone: not what the plan checked
        except OSError as e:
            if _is_lock(e):
                return _Unverified(e)
            raise
        return verdict(now, "already")

    try:
        now = _with_lock_retry(path.read_bytes, f"re-read {rel}")
    except (FileNotFoundError, NotADirectoryError):
        return "changed"  # gone: not what the plan checked
    # Any other failure (LockExhausted, or a refusal that is not a lock
    # code -- a real Windows sharing violation on open() is PermissionError
    # errno 13 with NO winerror) propagates: the caller stops the batch
    # (apply) or lists a transient restore-write-failed row (restore). It
    # was 'changed': a false human-edit claim, and apply carried on.
    first = verdict(now, "present")
    if first is not None:
        return first
    return _with_lock_retry(op, f"write {rel}", recheck)


class BackupConflict(OSError):
    """An existing backup that is not byte-identical to the page's original."""


def _write_backup(dest: Path, original: bytes, rel: str) -> None:
    """Create one page backup (R4b): absent -> written; byte-identical to the
    original -> ok as it is; anything else -> BackupConflict (never
    overwritten). Verified by read-back."""
    def status():
        try:
            b = _read_retry(dest, "bcheck", rel)
        except FileNotFoundError:
            return "absent"
        return "same" if b == original else "conflict"

    s = status()
    if s == "conflict":
        raise BackupConflict(f"backup conflict: {dest} exists and differs from the original of {rel}")
    if s == "absent":
        dest.parent.mkdir(parents=True, exist_ok=True)

        def op():
            _inject_lock("backup", rel, dest)
            _atomic_write(dest, original)
            return "same"

        def recheck():
            try:
                _inject_lock("brecheck", rel, dest)
                now = dest.read_bytes()
            except FileNotFoundError:
                return None
            except OSError as e:
                # locked read: the backup's bytes are unknown -- never rewrite
                # it blind; any other refusal fails at once (R4), never a
                # (false) conflict claim
                if _is_lock(e):
                    return _Unverified(e)
                raise
            return "same" if now == original else "conflict"

        if _with_lock_retry(op, f"backup {rel}", recheck) == "conflict":
            raise BackupConflict(f"backup conflict: {dest} changed while it was being written")
    if _read_retry(dest, "bcheck", rel) != original:
        raise OSError(f"backup verify failed: {dest} is not the original of {rel}")


def _copy_state_file(src: Path, dst: Path) -> None:
    """Copy one local state file (atomic write of dst) under the lock retry."""
    data = _read_retry(src, "stateread", src.name)

    def op():
        _inject_lock("state", dst.name, dst)
        _atomic_write(dst, data)
    _with_lock_retry(op, f"copy {src.name} -> {dst.name}")


def _write_json(path: Path, obj) -> None:
    """A local state save (R4c): atomic replace under the lock retry."""
    import json
    data = json.dumps(obj, indent=2, ensure_ascii=True).encode("ascii") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)

    def op():
        _inject_lock("state", path.name, path)
        _atomic_write(path, data)
    _with_lock_retry(op, f"save {path.name}")


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
        _, text = _decode(_read_retry(root / rel, "read", rel))
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
        return None, "lint.py printed no ALIAS-ONLY / BROKEN line (lint predates alias-only support?)"
    return {"alias_only": int(a.group(1)), "broken": int(b.group(1))}, ""


def restore_command(root: Path, rid: str, state_dir=None) -> str:
    cmd = (f'python "{Path(__file__).resolve()}" --alias-only --root "{root}" '
           f'--restore {rid}')
    if state_dir is not None:
        cmd += f' --state-dir "{state_dir}"'
    return cmd


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


# ---------------------------------------------------------------------------
# Operational state (ADR-0005, regression R153): the manifest, the apply logs and
# the page backups live in a STATE DIRECTORY outside the vault and outside any
# sync service. Layout of one state directory:
#   manifest.json               the dry run's plan
#   vault.json                  which vault path this directory belongs to
#   backup/<run_id>/            page backups + apply-log.json (+ rotated logs)
#   state-dirs.json             (default directory only) every --state-dir
#                               this vault has used: the index of record
# ---------------------------------------------------------------------------

STATE_TOOL = "alias-fix"
LEGACY_BACKUP = ".alias-fix-backup"
LEGACY_MANIFEST = "_meta/alias-fix-manifest.json"
REGISTRY_NAME = "state-dirs.json"
VAULT_ID_NAME = "vault.json"
STATE_SCHEMA = 1
# A run being copied by --migrate-legacy-state: never RUN_ID_RE, so no
# discovery reads it until it is verified and renamed.
STAGING_SUFFIX = ".migrating"
HASH8_RE = re.compile(r"[0-9a-f]{8}")


class StateError(Exception):
    """A state-directory refusal: exit 2, nothing written."""


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat()


def _msix_interpreter() -> str | None:
    """The interpreter path that marks this Python as a Microsoft Store
    (MSIX-packaged) build, or None. Such a process sees AppData\\Local through
    file-system virtualization: its writes land in a private package folder
    that no other interpreter reads. sys.base_prefix / sys.prefix catch a
    venv built from the Store Python (its sys.executable looks normal)."""
    import os
    for attr in ("executable", "_base_executable", "base_prefix", "prefix"):
        v = getattr(sys, attr, None)
        if not isinstance(v, str) or not v:
            continue
        n = os.path.normcase(v).replace("/", "\\")
        if "\\windowsapps\\" in n or "\\packages\\pythonsoftwarefoundation." in n:
            return v
    return None


def state_base() -> Path:
    """%LOCALAPPDATA%/WIKIllm/alias-fix; off Windows only, ~/.local/state/
    WIKIllm/alias-fix when LOCALAPPDATA is unset. On Windows an unset (or
    empty) LOCALAPPDATA is REFUSED (StateError): a silent fallback would move
    the index of record, hiding every run and registry under the real
    %LOCALAPPDATA% from discovery (ADR-0005 R1/R2). A Microsoft Store (MSIX)
    Python is REFUSED for the same reason: LOCALAPPDATA is set but its writes
    are virtualized into a private package folder other interpreters never
    see. Computed per call, never at import time."""
    import os
    lad = os.environ.get("LOCALAPPDATA")
    if not lad and os.name == "nt":
        raise StateError("LOCALAPPDATA is not set -- cannot locate the index-of-record state dir "
                         "(%LOCALAPPDATA%\\WIKIllm\\alias-fix); refusing rather than falling back "
                         "to another location where discovery would miss existing runs. Run from a "
                         "normal user session or set LOCALAPPDATA (--state-dir does not replace it: "
                         "the registry lives in the default dir)")
    if os.name == "nt":
        msix = _msix_interpreter()
        if msix:
            raise StateError(
                f"Microsoft Store Python ({msix}) redirects %LOCALAPPDATA% writes to a private "
                "package folder (...\\Packages\\PythonSoftwareFoundation...\\LocalCache\\Local), so "
                "runs made here would be invisible to any other interpreter (and theirs to this "
                "one) -- refusing; run with python.org / conda Python (`python -B`, not `python3`, "
                "which on many Windows installs is the Store shim). --state-dir does not replace it: the "
                "registry lives in the default dir")
    base = Path(lad) if lad else Path.home() / ".local" / "state"
    return Path(os.path.abspath(str(base))) / "WIKIllm" / STATE_TOOL


def _strip_extended(s: str) -> str:
    """Drop a Windows extended-length / device prefix (\\\\?\\C:\\... ->
    C:\\..., \\\\?\\UNC\\host\\share -> \\\\host\\share, \\\\.\\C:\\... -> C:\\...):
    Path.resolve() keeps it verbatim, so without this one directory has two
    text spellings (review: an in-vault state dir passed the text check,
    and one --state-dir registered twice)."""
    for pre, rep in (("\\\\?\\UNC\\", "\\\\"), ("//?/UNC/", "//"), ("\\\\?\\", ""), ("//?/", ""),
                     ("\\\\.\\", ""), ("//./", "")):
        if s[:len(pre)].upper() == pre.upper():
            rest = s[len(pre):]
            # only a drive path or a UNC share, never a device like \\.\PIPE
            if rep or (len(rest) >= 2 and rest[1] == ":"):
                return rep + rest
    return s


def _unsupported_form(raw) -> str | None:
    """Why a state path spelling is refused (Windows), else None: a path
    component ending in a space or a dot. Win32 silently strips those on
    create, so the directory written is not the one the text names (review:
    '<vault> \\state' passed the in-vault check and the dry run wrote into
    <vault>/state)."""
    import os
    if os.name != "nt":
        return None
    s = _strip_extended(str(raw))
    for part in s.replace("/", "\\").split("\\"):
        if part in ("", ".", "..") or part.endswith(":") or part == "?":
            continue
        if part != part.rstrip(" ."):
            return (f"unsupported path form: component {part!r} ends in a space or a dot (Windows "
                    f"strips it, so the path written would not be the path named)")
    return None


def _canon(path) -> Path:
    """The resolved absolute path in its plain spelling (no \\\\?\\ prefix)."""
    return Path(_strip_extended(str(Path(path).resolve())))


def _key_text(path) -> str:
    """os.path.normcase of the resolved absolute path (extended-length prefix
    stripped), '/' separators: the identity a vault's state is keyed by, and
    the path-equality test."""
    import os
    return os.path.normcase(str(_canon(path))).replace("\\", "/")


def vault_key(root) -> str:
    """<vault-folder-name>-<hash8>: hash8 = first 8 hex of sha256 of the
    normalized resolved vault path (ADR-0005)."""
    return f"{Path(root).resolve().name}-{_sha256(_key_text(root).encode('utf-8'))[:8]}"


def default_state_dir(root) -> Path:
    return state_base() / vault_key(root)


def _same_path(a, b) -> bool:
    """Same directory: equal key text, or -- two spellings resolve() does
    not merge (an admin-share UNC and its drive path, a junction) -- the
    same file-system object (os.path.samefile, when both exist)."""
    import os
    if _key_text(a) == _key_text(b):
        return True
    try:
        sa, sb = os.stat(str(a)), os.stat(str(b))
    except (OSError, ValueError):
        return False
    # samestat compares (st_dev, st_ino) only: a volume with no file IDs
    # (st_ino 0) would make every two paths on it "the same" -- two state
    # dirs merged, one dropped from discovery (fail open).
    if not sa.st_ino or not sb.st_ino:
        return False
    return os.path.samestat(sa, sb)


def _inside(p, root) -> bool:
    """True when `p` resolves to `root` or anywhere under it. Two tests, either
    one suffices: the resolved text, and file-system IDENTITY -- `p` or any
    existing ancestor of it IS the vault directory (st_dev + st_ino). The
    identity test catches every spelling the text misses (\\\\?\\ prefixes,
    \\\\localhost\\c$ admin shares, a trailing-space component Windows strips
    on create) BEFORE anything is created: the vault already exists (review:
    such a --state-dir wrote manifest, backups and the apply log into the
    vault)."""
    import os
    a, r = _key_text(p), _key_text(root)
    if a == r or a.startswith(r.rstrip("/") + "/"):
        return True
    try:
        rs = os.stat(str(root))
    except (OSError, ValueError):
        return False
    if not rs.st_ino:
        return False  # no file IDs on this volume: the text test is all we have
    q = Path(os.path.abspath(str(p)))
    for anc in (q, *q.parents):
        try:
            s = os.stat(str(anc))
        except (OSError, ValueError):
            continue
        if (s.st_dev, s.st_ino) == (rs.st_dev, rs.st_ino):
            return True
    return False


def _read_json_quiet(p: Path):
    """A small state JSON (registry, vault id) or None when absent or
    unreadable. The read retries a lock (R4): a transient sync / AV lock
    must not read as a corrupt registry."""
    import json
    try:
        return json.loads(_read_retry(p, "stateread", p.name).decode("utf-8"))
    except (OSError, ValueError):
        return None


def _run_dirs(base: Path, strict: bool = False) -> list[Path]:
    """Run directories (16-hex names) directly under one backup base. An
    absent base holds no run ([]). Any other listing error (an ACL-denied or
    transiently failing backup/) raises when `strict` -- discovery must then
    fail CLOSED ('cannot prove there is no unfinished run', ADR-0005 R2),
    never read it as empty; non-strict callers are counts and heuristics."""
    try:
        return sorted(p for p in base.iterdir() if p.is_dir() and RUN_ID_RE.fullmatch(p.name))
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError:
        if strict:
            raise
        return []


def _unlistable(base: Path) -> str | None:
    """None when `base` is absent or can be listed; else why it cannot be."""
    import os
    try:
        os.listdir(base)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as e:
        return f"{e.__class__.__name__}: {e}"
    return None


def _backup_in_vault(d: Path, root: Path) -> bool:
    """True when the backup/ tree under state dir `d` would lie inside the
    vault, or the vault inside it (state dir = the vault's parent and the
    vault folder named 'backup', ...): the page backups and apply logs must
    never land in the vault (ADR-0005 R1)."""
    b = d / "backup"
    return _inside(b, root) or _inside(root, b)


def _has_logs(run_dir: Path) -> bool:
    try:
        return any(run_dir.glob("apply-log*.json"))
    except OSError:
        return False


def legacy_scan(root: Path) -> dict:
    """legacy in-vault state: <vault>/.alias-fix-backup/<entry> (runs and
    set-aside <rid>-inspected dirs alike) and _meta/alias-fix-manifest.json.
    'total' is 0 only when neither exists."""
    base = root / LEGACY_BACKUP
    entries = []
    if base.is_dir():
        try:
            entries = sorted(base.iterdir())
        except OSError:
            entries = []
    manifest = root / LEGACY_MANIFEST
    has_manifest = manifest.is_file()
    runs = [e for e in entries if e.is_dir() and RUN_ID_RE.fullmatch(e.name)]
    return {"base": base, "base_exists": base.is_dir(), "entries": entries, "runs": runs,
            "manifest": manifest if has_manifest else None,
            "total": len(entries) + int(has_manifest) + int(base.is_dir() and not entries)}


class State:
    """Where this vault's operational state lives, and every place a run of
    it may be found (default dir, registered --state-dir dirs, legacy
    in-vault state). Construction reads only; register() / ensure_identity()
    are the writes, and none of them is ever inside the vault."""

    def __init__(self, root: Path, state_dir=None, manifest=None, paths_only=False):
        """paths_only=True (the pre-lock pass, ADR-0006): the refusal-only
        checks -- paths, and the identity check of an explicit --state-dir
        (its vault.json, plus this vault's own vault.json for the
        rebound_from grant it honors) -- but never the registry or run
        discovery; the run constructs State again under the run lock. The
        identity check stays here so a refused foreign --state-dir writes
        nothing anywhere, not even this vault's default dir (ADR-0005 I6)."""
        import os
        self.root = root
        self.default = default_state_dir(root)
        if _inside(self.default, root) or _backup_in_vault(self.default, root):
            raise StateError(f"the default state dir {self.default} resolves inside the vault {root} "
                             f"(LOCALAPPDATA points into it) -- pass --state-dir PATH outside the vault")
        # rebound_from first: the identity checks below accept a state dir
        # recording a root this vault's state was rebound from.
        vid = _read_json_quiet(self.default / VAULT_ID_NAME)
        rb = vid.get("rebound_from") if isinstance(vid, dict) else None
        self.rebound_from = {str(x) for x in rb} if isinstance(rb, list) else set()
        self.untrusted: list[str] = []     # why a run could be hidden from us
        if isinstance(vid, dict) and vid.get("key") not in (None, vault_key(root)):
            # The default dir is keyed by this path's hash, yet records
            # another vault: an interrupted --rebind-state (the old dir was
            # renamed here before the grant was written). Never raised --
            # --rebind-state must still run to repair it.
            self.untrusted.append(
                f"this vault's state dir {self.default} records vault {vid.get('root_display') or vid.get('root')} "
                f"(key {vid.get('key')}) -- an interrupted --rebind-state; re-run --rebind-state "
                f"\"{vid.get('root_display') or vid.get('root')}\" to finish it")
        self.explicit = state_dir is not None
        if self.explicit:
            bad = _unsupported_form(state_dir)
            if bad:
                raise StateError(f"--state-dir {state_dir}: {bad}")
        self.dir = _canon(state_dir) if self.explicit else self.default
        if _inside(self.dir, root):
            raise StateError(f"--state-dir {self.dir} resolves inside the vault {root} -- operational "
                             f"state must live outside the vault (ADR-0005)")
        if _backup_in_vault(self.dir, root):
            raise StateError(f"--state-dir {self.dir}: its backup tree {self.dir / 'backup'} resolves "
                             f"inside the vault {root} (or holds it) -- page backups and apply logs "
                             f"must live outside the vault (ADR-0005)")
        if self.dir.exists() and not self.dir.is_dir():
            raise StateError(f"--state-dir {self.dir} exists and is not a directory")
        if self.explicit and _same_path(self.dir, self.default):
            self.explicit = False  # naming the default dir is the default
            self.dir = self.default
        if self.explicit:
            # One state dir belongs to exactly one vault path (review: a byte
            # copy sharing a --state-dir got the same run_id, and its apply
            # rotated away the original's unfinished apply log).
            why = self.foreign(self.dir)
            if why:
                raise StateError(f"--state-dir {self.dir} {why} -- one state dir per vault: use a "
                                 f"different --state-dir, or --rebind-state OLD_PATH if that vault "
                                 f"no longer exists")
        if manifest is not None:
            bad = _unsupported_form(manifest)
            if bad:
                raise StateError(f"--manifest {manifest}: {bad}")
        self.manifest = (_canon(manifest) if manifest is not None
                         else self.dir / "manifest.json")
        if _inside(self.manifest, root):
            raise StateError(f"--manifest {self.manifest} resolves inside the vault {root} -- "
                             f"operational state must live outside the vault (ADR-0005)")
        self.registry = self.default / REGISTRY_NAME
        self.registry_error: str | None = None
        self.registered: list[dict] = []   # every state-dirs.json entry, as read
        self.reachable: list[Path] = []    # registered dirs trusted for discovery
        if paths_only:
            return
        if self.registry.exists():
            data = _read_json_quiet(self.registry)
            ok = (isinstance(data, dict) and isinstance(data.get("state_dirs"), list)
                  and all(isinstance(e, dict) and isinstance(e.get("path"), str)
                          for e in data["state_dirs"]))
            if not ok:
                self.registry_error = f"state registry {self.registry} is unreadable or malformed"
                self.untrusted.append(f"{self.registry_error} -- cannot prove there is no unfinished run")
            else:
                self.registered = list(data["state_dirs"])
        for e in self.registered:
            q = Path(e["path"])
            if _same_path(q, self.dir) or _same_path(q, self.default):
                continue
            bad = _unsupported_form(e["path"])
            if bad:
                self.untrusted.append(f"registered state dir {q}: {bad} -- not trusted; cannot prove "
                                      f"there is no unfinished run")
                continue
            if _inside(q, root) or _backup_in_vault(q, root):
                self.untrusted.append(f"registered state dir {q} is inside the vault -- not trusted; "
                                      f"cannot prove there is no unfinished run")
                continue
            try:
                os.listdir(q)
            except OSError:
                self.untrusted.append(f"registered state dir {q} is unreachable -- cannot prove there "
                                      f"is no unfinished run. If it is gone for good, create it empty "
                                      f"and run --forget-state-dir \"{q}\"")
                continue
            why = self.foreign(q)
            if why:
                self.untrusted.append(f"registered state dir {q} {why} -- not trusted; cannot prove "
                                      f"there is no unfinished run")
                continue
            self.reachable.append(q)
        # A reachable state dir whose backup/ cannot be listed hides its runs
        # just as well (R2: fail closed, never read it as empty).
        for b in self.bases():
            why = _unlistable(b)
            if why:
                self.untrusted.append(f"state backup dir {b} cannot be listed ({why}) -- cannot prove "
                                      f"there is no unfinished run")

    def foreign(self, d: Path) -> str | None:
        """None when state dir `d` may hold this vault's state: no vault.json
        yet, or one naming this vault (its key, its root, or a root this
        vault's state was rebound from). Else why not. A vault.json that
        exists but cannot be read is not proof either way: refused."""
        p = d / VAULT_ID_NAME
        if not p.exists():
            return None
        vid = _read_json_quiet(p)
        if not isinstance(vid, dict):
            return f"has an unreadable {VAULT_ID_NAME} (cannot tell which vault it belongs to)"
        if (vid.get("key") == vault_key(self.root) or vid.get("root") == _norm_root(self.root)
                or str(vid.get("root")) in self.rebound_from):
            return None
        return (f"belongs to vault {vid.get('root_display') or vid.get('root')} (key {vid.get('key')}), "
                f"not --root {self.root}")

    # --- discovery -------------------------------------------------------
    def bases(self, legacy: bool = True) -> list[Path]:
        """Backup bases (each holds <run_id>/ dirs), current dir first."""
        out: list[Path] = []
        cands = [self.dir / "backup", self.default / "backup"] + [q / "backup" for q in self.reachable]
        if legacy:
            cands.append(self.root / LEGACY_BACKUP)
        for c in cands:
            if not any(_same_path(c, o) for o in out):
                out.append(c)
        return out

    def find_run(self, rid: str, legacy: bool = True) -> list[Path]:
        """Every run directory named `rid` that holds an apply log."""
        return [b / rid for b in self.bases(legacy) if (b / rid).is_dir() and _has_logs(b / rid)]

    def is_legacy(self, run_dir: Path) -> bool:
        return _inside(run_dir, self.root)

    def restore_state_arg(self, run_dir: Path):
        """The --state-dir a printed RESTORE for `run_dir` needs (None: the
        default dir or legacy state, which discovery finds unaided)."""
        if self.is_legacy(run_dir):
            return None
        sd = run_dir.parent.parent
        return None if _same_path(sd, self.default) else sd

    def restore_cmd(self, rid: str, run_dir: Path) -> str:
        return restore_command(self.root, rid, self.restore_state_arg(run_dir))

    # --- writes (always outside the vault) --------------------------------
    def ensure_identity(self, d: Path) -> None:
        # Re-checked at the write boundary: nothing of the state is ever
        # created inside the vault, whatever spelling slipped past (R1).
        if _inside(d, self.root) or _backup_in_vault(d, self.root):
            raise OSError(f"refusing to write state inside the vault: {d}")
        p = d / VAULT_ID_NAME
        if not p.is_file():
            _write_json(p, {"schema_version": STATE_SCHEMA, "tool": STATE_TOOL,
                            "root": _norm_root(self.root), "root_display": str(self.root),
                            "key": vault_key(self.root), "created": _now_iso()})

    def register(self) -> None:
        """A run with --state-dir records it in the default dir's
        state-dirs.json (canonical absolute path + last_used)."""
        if not self.explicit:
            return
        if self.registry_error:
            raise StateError(f"cannot register --state-dir {self.dir}: {self.registry_error} -- "
                             f"repair or remove it first")
        # The dir exists (with its vault id) BEFORE its pointer is recorded:
        # a registered dir the tool never created would read as unreachable
        # and fail every later --apply / --restore closed. A dir that cannot
        # be created registers nothing.
        self.ensure_identity(self.dir)
        entries, found = [], False
        for e in self.registered:
            if _same_path(e["path"], self.dir):
                e = dict(e, path=str(self.dir), last_used=_now_iso())
                found = True
            entries.append(e)
        if not found:
            entries.append({"path": str(self.dir), "last_used": _now_iso()})
        _write_json(self.registry, {"schema_version": STATE_SCHEMA, "tool": STATE_TOOL,
                                    "vault_root": _norm_root(self.root), "state_dirs": entries})
        self.registered = entries
        self.ensure_identity(self.default)

    # --- identity (R7) ----------------------------------------------------
    def same_name(self) -> list[dict]:
        """State under the default root for ANOTHER key with this vault's
        folder name: a warning heuristic, never identity."""
        import os
        base = state_base()
        prefix = os.path.normcase(self.root.name) + "-"
        own = os.path.normcase(self.default.name)
        out = []
        try:
            sibs = sorted(base.iterdir()) if base.is_dir() else []
        except OSError:
            sibs = []
        for d in sibs:
            n = os.path.normcase(d.name)
            if (n == own or not n.startswith(prefix) or not HASH8_RE.fullmatch(n[len(prefix):])
                    or not d.is_dir()):
                continue
            # A key dir PROVABLY holding no state -- it lists, and holds only
            # run-lock artifacts (run.lock, run.lock.<id>.tmp) or nothing:
            # what a backtick-mode --apply leaves (ADR-0007). Not state;
            # sibling locks are sibling_locks()' business. Any other file
            # (an unexplained run.lock.* name included) falls through to the
            # fail-closed checks below (ADR-0008 Q5). Skipped ONLY on that
            # positive proof: a dir that cannot
            # be listed falls through to the fail-closed checks below (R168
            # re-review: an access-denied sibling read as 'no markers' let an
            # alias --apply past an unfinished run).
            try:
                names = os.listdir(d)
            except OSError:
                names = None
            if names is not None and all(
                    x == RUN_LOCK_NAME or (x.startswith(RUN_LOCK_NAME + ".") and x.endswith(".tmp"))
                    for x in names):
                continue
            vid = _read_json_quiet(d / VAULT_ID_NAME)
            where = vid.get("root_display") if isinstance(vid, dict) else None
            bases = [d / "backup"]
            settled, unfinished = 0, []
            # The sibling key's registered --state-dir dirs are part of its
            # discovery (R2), so they fail CLOSED here exactly as this
            # vault's own do: a present-but-unreadable registry, an
            # unreachable registered dir or one inside this vault cannot
            # prove its runs are settled -> counted unfinished (R7 block).
            regp = d / REGISTRY_NAME
            if regp.exists():
                reg = _read_json_quiet(regp)
                if not (isinstance(reg, dict) and isinstance(reg.get("state_dirs"), list)
                        and all(isinstance(e, dict) and isinstance(e.get("path"), str)
                                for e in reg["state_dirs"])):
                    unfinished.append(f"({regp} unreadable or malformed registry -- cannot prove "
                                      f"there is no unfinished run)")
                    reg = {"state_dirs": []}
                for e in reg["state_dirs"]:
                    q = Path(e["path"])
                    if _inside(q, self.root) or _backup_in_vault(q, self.root):
                        unfinished.append(f"({q} registered inside this vault -- not trusted; cannot "
                                          f"prove there is no unfinished run)")
                        continue
                    try:
                        os.listdir(q)
                    except OSError:
                        unfinished.append(f"({q} unreachable -- cannot prove there is no unfinished "
                                          f"run)")
                        continue
                    bases.append(q / "backup")
            for b in bases:
                try:
                    rds = _run_dirs(b, strict=True)
                except OSError:
                    unfinished.append(f"({b} cannot be listed -- cannot prove there is no "
                                      f"unfinished run)")
                    continue
                for rd in rds:
                    if not (rd / "apply-log.json").is_file():
                        continue
                    try:
                        lg = _read_apply_log(rd / "apply-log.json")
                    except ValueError:
                        unfinished.append(f"{rd.name} (unreadable log)")
                        continue
                    if lg.get("result") in SETTLED_RESULTS:
                        settled += 1
                    else:
                        unfinished.append(rd.name)
            out.append({"dir": d, "root": where, "settled": settled, "unfinished": unfinished})
        return out

    # --- reporting ---------------------------------------------------------
    def header(self) -> list[str]:
        """Prints STATE / REGISTERED IN and every warning; returns the
        reasons --apply / --restore must fail closed (empty = none)."""
        print(f"STATE: {self.dir}")
        if self.explicit:
            print(f"REGISTERED IN: {self.registry}")
        blockers = list(self.untrusted)
        for msg in self.untrusted:
            print(f"WARNING: {msg}")
        lg = legacy_scan(self.root)
        if lg["total"]:
            print(f"NOTE: legacy in-vault state (pre-ADR-0005 layout): {len(lg['entries'])} entr"
                  f"{'y' if len(lg['entries']) == 1 else 'ies'} in {lg['base']}"
                  + (f", manifest {lg['manifest']}" if lg["manifest"] else "")
                  + " -- run --migrate-legacy-state (then with --apply) before the next --apply "
                    "(ADR-0005).")
        for s in self.same_name():
            where = f" (vault {s['root']})" if s["root"] else ""
            if s["unfinished"]:
                print(f"WARNING: state for another vault path with the same folder name: {s['dir']}"
                      f"{where} has unfinished run(s) {', '.join(s['unfinished'])} -- --apply and "
                      f"--restore are blocked. If this vault is a COPY, settle or restore those runs "
                      f"at the original path; if it was MOVED and the old path no longer exists, "
                      f"attach them with --rebind-state OLD_PATH.")
                if any("cannot prove" in u for u in s["unfinished"]):
                    print("  (cannot prove there is no unfinished run there: reattach or repair what "
                          "is named above and re-run; if the old path is gone, --rebind-state "
                          "OLD_PATH then --forget-state-dir DIR for a dir that is gone for good.)")
                blockers.append(f"unfinished run(s) under same-folder-name state {s['dir']}")
            else:
                print(f"WARNING: state for another vault path with the same folder name: {s['dir']}"
                      f"{where} ({s['settled']} settled run(s)). State is keyed by path, so this "
                      f"vault does not inherit it; if this vault was moved from there and the old "
                      f"path no longer exists, --rebind-state OLD_PATH attaches it.")
        return blockers


def _backup_path(field, run_dir: Path, rid: str) -> Path | None:
    """The page backup an apply-log 'backup' field names, resolved against
    the directory the run ACTUALLY lives in, or None when it does not lie
    under that run's backup dir (then restore reports backup-missing).
    Accepted forms: 'backup/<rid>/<rel>' (state-dir relative, ADR-0005),
    '.alias-fix-backup/<rid>/<rel>' (legacy vault-relative; kept verbatim by
    --migrate-legacy-state) and an absolute path."""
    f = str(field).replace("\\", "/")
    p = Path(f)
    if p.is_absolute():
        cand = p
    else:
        parts = f.split("/")
        if len(parts) < 3 or parts[0] not in ("backup", LEGACY_BACKUP) or parts[1] != rid:
            return None
        cand = run_dir.joinpath(*parts[2:])
    try:
        cand = cand.resolve()
        cand.relative_to(run_dir.resolve())
    except (OSError, ValueError):
        return None
    return cand


# ---------------------------------------------------------------------------
# Run lock (ADR-0006 / ADR-0007, regression R161): one lock-participating
# fix_wikilinks writer per vault -- every alias state writer and the
# backtick-mode --apply (which borrows only mutual exclusion and recovery).
# External editors are outside the guarantee.
# run.lock lives in the vault's DEFAULT state dir whatever --state-dir a run
# uses (the lock protects the vault, not a state dir). A visible run.lock is
# always a complete holder record created in one atomic namespace operation:
# the record is written to a uniquely named temp file, flushed and fsynced,
# then os.link(tmp, run.lock) -- the link is the acquisition point. A holder
# is alive unless PROVABLY gone (pid absent, or alive with another process
# creation time); anything unprovable counts as alive (fail closed).
# Deleting it (ADR-0008, regression R170): on Windows every deletion -- release
# and --break-lock alike -- goes through ONE compare-and-delete primitive:
# CreateFileW(DELETE | GENERIC_READ, share mode 0) -> read the record through
# that handle -> the caller's predicate (release: the bytes are this run's
# own record; --break-lock: the holder is provably stale) -> only on true,
# FileDispositionInfo(DeleteFile) on that same handle -> CloseHandle. The
# condition is checked on the exact object deleted, and the name stays
# occupied until the handle closes, so a racing os.link fails (183) and no
# live or replacement lock can be deleted unjudged. On Windows, --break-lock
# is safe alongside other fix_wikilinks runs: it judges and deletes the same
# run.lock object through one exclusive handle, so it cannot delete a live
# or replacement lock that it did not judge stale (regression R170, ADR-0008).
# Every by-path run.lock read (the acquirer's holder read, sibling_locks)
# uses one shared-mode reader so a breaker's share-0 handle surfaces as
# WinError 32 and takes the bounded _with_lock_retry; on Windows release's
# ownership read is NOT a by-path read -- it is the read through its own
# exclusive compare-and-delete handle (ADR-0008 Q3/Q6, accepted deviation
# 1). Off Windows, automatic --break-lock refuses; remove run.lock manually
# only after confirming no fix_wikilinks run is live (acquire and release
# keep the by-path locking and I/O; only the platform-neutral diagnostics --
# 're-run', 'unreadable, not malformed' -- are shared, ADR-0008 Q2, accepted
# deviation 2).

RUN_LOCK_NAME = "run.lock"
RUN_LOCK_SCHEMA = 1
# TEST-ONLY run-lock hook, honored only when --root is under the temp dir
# (inert elsewhere, with a WARNING): a JSON object with any of
#   "gate": PATH  -- after the temp record is written, wait for PATH to exist
#                    before os.link (two contenders race on the link)
#   "hold": PATH  -- after acquiring, wait for PATH to exist before running
#   "nolink": N   -- os.link raises OSError winerror N (1 = the shape of a
#                    file system without hard links)
#   "after_sibling_check": PATH -- after the sibling-lock check, wait for
#                    PATH before acting on its result (both contenders check
#                    while both locks exist); writes PATH.at.<pid> on arrival
#   "break_judged": PATH -- --break-lock pauses after its judgment with the
#                    exclusive handle STILL OPEN (the only I/O ever done in
#                    that window, and only here): writes PATH.at.<pid> on
#                    arrival, then waits for PATH
#   "retry_marker": PATH -- each time a run.lock operation fails on a
#                    retryable lock error (5/32/33) -- the exclusive
#                    compare-and-delete open (kind excl) or the shared reader
#                    (kind read) -- write PATH.<kind>.<pid> (after the failed
#                    attempt's handle is closed), so tests wait on an
#                    observable barrier instead of sleeping
RUN_LOCK_TEST_ENV = "FIX_WIKILINKS_TEST_RUNLOCK"
_HARDLINK_UNSUPPORTED_WINERRORS = (1, 17, 50)  # INVALID_FUNCTION, NOT_SAME_DEVICE, NOT_SUPPORTED


class RunLockRefused(Exception):
    """The run lock could not be acquired: exit 2, nothing written."""


def _runlock_hook(root: Path) -> dict:
    import json
    import os
    import tempfile
    spec = os.environ.get(RUN_LOCK_TEST_ENV)
    if not spec:
        return {}
    # identity too: the sibling-check tests reach a temp vault through its
    # \\localhost\c$ spelling, which the text test does not recognize
    if not (_under_tempdir(root) or _inside(root, Path(tempfile.gettempdir()))):
        print(f"WARNING: {RUN_LOCK_TEST_ENV} ignored outside a temp vault")
        return {}
    return json.loads(spec)


def _wait_for(path: str) -> None:
    import time
    deadline = time.monotonic() + 120
    while not Path(path).exists():
        if time.monotonic() > deadline:
            raise RunLockRefused(f"[test] timed out waiting for {path}")
        time.sleep(0.02)


def _break_supported() -> bool:
    """True only on Windows (ADR-0008 Q2): the platform with the share-0 open
    and delete-by-handle an atomic --break-lock needs. EVERY Windows-only
    run.lock path (break, release, the shared reader) keys off this one seam,
    so a test that patches it False runs the off-Windows code for real."""
    import os
    return os.name == "nt"


_K32 = None
_GENERIC_READ = 0x80000000
_DELETE = 0x00010000
_FILE_SHARE_ALL = 0x1 | 0x2 | 0x4  # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
_OPEN_EXISTING = 3
_FILE_DISPOSITION_INFO = 4  # FILE_INFO_BY_HANDLE_CLASS FileDispositionInfo
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000  # open a symlink/junction itself, never its target


def _k32():
    """kernel32 with the run-lock declarations, loaded on first use (never
    at import time). HANDLE restypes so a 64-bit handle is never truncated."""
    global _K32
    if _K32 is None:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        k.ReadFile.restype = wintypes.BOOL
        k.ReadFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
        k.SetFileInformationByHandle.restype = wintypes.BOOL
        k.SetFileInformationByHandle.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                 wintypes.DWORD)
        k.CloseHandle.restype = wintypes.BOOL
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        _K32 = k
    return _K32


def _win_error(code: int, path) -> OSError:
    """ctypes.WinError(code) naming `path`: .winerror reaches _is_lock, and
    2 / 3 surface as FileNotFoundError (verified on 3.14)."""
    import ctypes
    e = ctypes.WinError(code)
    e.filename = str(path)
    return e


def _win_open(path, access: int, share: int):
    """CreateFileW(OPEN_EXISTING), no security attributes (a non-inheritable
    handle: a child spawned meanwhile can never keep the name alive). The
    last error is captured before anything else can overwrite it.
    FILE_FLAG_OPEN_REPARSE_POINT: a FILE reparse point as run.lock (a
    hand-planted file symlink) is opened as ITSELF -- its own empty data
    stream reads b'', is judged malformed / unprovable, and is never
    followed; a directory-shaped run.lock (a directory, directory symlink or
    junction) cannot be opened at all without FILE_FLAG_BACKUP_SEMANTICS
    (CreateFileW WinError 5), so it is retried under the R4 budget and fails
    closed as unreadable (LockExhausted), never followed or deleted. Either
    way a break can never judge or delete a file outside the state dir. Q1
    pins only the access mask and share mode; this flag is not a deviation
    from it. The
    state dir is %LOCALAPPDATA% (ADR-0005), never a cloud-sync folder, so no
    legitimate run.lock is a placeholder reparse point."""
    import ctypes
    from ctypes import wintypes
    h = _k32().CreateFileW(str(path), access, share, None, _OPEN_EXISTING,
                           _FILE_FLAG_OPEN_REPARSE_POINT, None)
    if h is None or h == wintypes.HANDLE(-1).value:
        raise _win_error(ctypes.get_last_error(), path)
    return h


def _win_read_all(h, path) -> bytes:
    """ReadFile through `h`, looped to EOF."""
    import ctypes
    from ctypes import wintypes
    k = _k32()
    buf = ctypes.create_string_buffer(65536)
    n = wintypes.DWORD()
    chunks = []
    while True:
        if not k.ReadFile(h, buf, len(buf), ctypes.byref(n), None):
            raise _win_error(ctypes.get_last_error(), path)
        if n.value == 0:
            return b"".join(chunks)
        chunks.append(buf.raw[:n.value])


def _run_lock_retry(attempt, desc: str, hook: dict | None, kind: str, tries: list | None = None):
    """attempt() under _with_lock_retry (5/32/33, the R4 budget). `tries`
    (when given) counts attempts. Test-only: the retry_marker hook is written
    after a retryable failure -- the attempt's own handle is already closed."""
    marker = (hook or {}).get("retry_marker")

    def op():
        if tries is not None:
            tries.append(1)
        try:
            return attempt()
        except OSError as e:
            if marker and _is_lock(e):
                import os
                Path(f"{marker}.{kind}.{os.getpid()}").write_text("retry")
            raise
    return _with_lock_retry(op, desc)


class LockDeleteRefused(OSError):
    """The run.lock was opened exclusively and judged, but setting its delete
    disposition through that handle failed (e.g. WinError 5 from a read-only
    attribute or an ACL). Built from one message string, so it carries NO
    .winerror: _is_lock() never retries it, and it is neither a
    FileNotFoundError nor a LockExhausted -- only the exclusive OPEN is
    retried (ADR-0008 Q4/Q6). The file is left untouched."""


def _lock_compare_delete(path: Path, decide, desc: str, hook: dict | None = None,
                         tries: list | None = None) -> bool:
    """THE Windows run.lock deletion (ADR-0008 Q1/Q6): exclusive share-0
    open with DELETE | GENERIC_READ -> read the record through that handle ->
    decide(raw) -> only on true, FileDispositionInfo(DeleteFile=TRUE) on the
    same handle; CloseHandle on every path (a raising predicate included).
    The name stays occupied until the close, so the record judged is exactly
    the object deleted. ONLY the exclusive open is retried on 5/32/33
    (another breaker judging, a shared reader, a delete pending) under
    _with_lock_retry; an open failure propagates as FileNotFoundError
    (gone), LockExhausted (retries spent on 5/32/33; it carries NO
    .winerror, so no outer retry ever retries it), or any other non-lock
    OSError at once. The read, the
    judgment and the disposition then run ONCE on that handle: a refused
    disposition closes the handle and raises LockDeleteRefused at once --
    no reopen, no re-read, no re-judge, file untouched (ADR-0008 Q4, option
    A: while this share-0 handle is held no other process can cause a
    sharing collision on the object, so the refusal is persistent, e.g.
    read-only / ACL). A stale verdict never survives across handles because
    there is never a second handle after the judgment. True = deleted,
    False = left untouched. Nothing is printed
    or logged while the handle is open (decide() is the caller's judgment;
    only the test-only break_judged hook does I/O there)."""
    import ctypes

    def open_exclusive():
        _inject_lock("unlock", RUN_LOCK_NAME, path)
        return _win_open(path, _DELETE | _GENERIC_READ, 0)
    h = _run_lock_retry(open_exclusive, desc, hook, "excl", tries)
    k = _k32()
    try:
        raw = _win_read_all(h, path)
        if not decide(raw):
            return False
        try:
            _inject_lock("dispose", RUN_LOCK_NAME, path)  # TEST-ONLY; no I/O
        except OSError as e:
            # shaped exactly like the real refusal below, so it takes the
            # same never-retried path
            code = getattr(e, "winerror", None)
            raise LockDeleteRefused(f"[WinError {code}] {e.strerror} (delete disposition refused): "
                                    f"{path}") from None
        flag = ctypes.c_ubyte(1)  # FILE_DISPOSITION_INFO.DeleteFile = TRUE
        if not k.SetFileInformationByHandle(h, _FILE_DISPOSITION_INFO, ctypes.byref(flag), 1):
            code = ctypes.get_last_error()  # before CloseHandle can overwrite it
            raise LockDeleteRefused(f"[WinError {code}] {ctypes.FormatError(code).strip()} "
                                    f"(delete disposition refused): {path}")
        return True
    finally:
        k.CloseHandle(h)


def _read_run_lock(path: Path, desc: str, hook: dict | None = None) -> bytes:
    """THE by-path run.lock reader (ADR-0008 Q3), for run.lock only: on
    Windows CreateFileW(GENERIC_READ, share read|write|delete), so a
    breaker's share-0 handle is a WinError 32 (and a pending delete a 5) that
    takes the bounded _with_lock_retry -- never CPython open()'s errno-13
    PermissionError, which R4 deliberately never retries. Callers on
    Windows: sibling_locks and the acquirer's holder read (release reads
    through its exclusive handle instead, accepted deviation 1). Off
    Windows: the plain by-path read (no winerror there, so no retry
    either), which off-Windows release also uses."""
    if not _break_supported():
        return _with_lock_retry(path.read_bytes, desc)

    def attempt():
        h = _win_open(path, _GENERIC_READ, _FILE_SHARE_ALL)
        try:
            return _win_read_all(h, path)
        finally:
            _k32().CloseHandle(h)
    return _run_lock_retry(attempt, desc, hook, "read")


def _proc_status(pid: int) -> tuple[str, int | None]:
    """('gone' | 'running' | 'unknown', creation time or None). 'gone' only
    when the OS proves no such process is running; a process that exists but
    whose creation time cannot be read is ('running', None)."""
    import os
    if not isinstance(pid, int) or pid <= 0:
        return "unknown", None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        k32.CloseHandle.restype = wintypes.BOOL
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            # ERROR_INVALID_PARAMETER: no process has this pid
            return ("gone", None) if ctypes.get_last_error() == 87 else ("unknown", None)
        try:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value != 259:  # STILL_ACTIVE
                return "gone", None  # exited; only an open handle keeps it listed
            ft = [wintypes.FILETIME() for _ in range(4)]
            if not k32.GetProcessTimes(h, *(ctypes.byref(f) for f in ft)):
                return "running", None
            return "running", (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "gone", None
    except PermissionError:
        return "running", None
    except OSError:
        return "unknown", None
    try:  # Linux: field 22 of /proc/<pid>/stat is the start time in clock ticks
        stat = Path(f"/proc/{pid}/stat").read_text()
        return "running", int(stat.rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return "running", None


def _hostname() -> str:
    import socket
    return socket.gethostname()


def _parse_lock(raw: bytes):
    """The holder record, or None when the bytes are not a well-formed one."""
    import json
    try:
        rec = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not (isinstance(rec, dict) and isinstance(rec.get("lock_id"), str) and rec["lock_id"]
            and isinstance(rec.get("pid"), int) and isinstance(rec.get("host"), str)
            and (rec.get("proc_created") is None or isinstance(rec.get("proc_created"), int))):
        return None
    return rec


def _holder_status(rec) -> tuple[str, str]:
    """('alive' | 'stale' | 'unprovable', why). Only 'stale' may be broken."""
    if rec is None:
        return "unprovable", "the lock file is malformed or unreadable -- its holder cannot be identified"
    if rec["host"] != _hostname():
        return "unprovable", f"it was taken on another host ({rec['host']})"
    status, created = _proc_status(rec["pid"])
    if status == "gone":
        return "stale", f"pid {rec['pid']} is not running"
    if status == "unknown":
        return "unprovable", f"cannot tell whether pid {rec['pid']} is running"
    if rec.get("proc_created") is None or created is None:
        return "unprovable", (f"pid {rec['pid']} is running and its creation time cannot be compared "
                              f"(it may be the holder)")
    if created != rec["proc_created"]:
        return "stale", f"pid {rec['pid']} now belongs to another process (creation time differs)"
    return "alive", f"pid {rec['pid']} is the holder and is still running"


def _describe_holder(rec) -> str:
    if rec is None:
        return "holder: unknown (malformed record)"
    return (f"holder: pid {rec['pid']} on {rec['host']}, mode {rec.get('mode')}, started "
            f"{rec.get('started')}, lock_id {rec['lock_id']}")


def _break_lock_cmd(root: Path) -> str:
    return f'python scripts/fix_wikilinks.py --alias-only --root "{root}" --break-lock'


def _after_break_hint(rec) -> str:
    """What to run after clearing a dead holder's lock: a backtick-mode
    holder keeps no record and no backups (ADR-0007)."""
    if rec and rec.get("mode") == "backtick-apply":
        return ("  then re-run the backtick dry run (no --alias-only) and read it: the dead "
                "backtick --apply keeps no record, and pages it already rewrote were not backed up")
    return "  then run a dry run first: it reports any unfinished run the holder left"


def _manual_recovery(path: Path) -> str:
    """Off Windows (ADR-0008 Q2) the only recovery for a stale lock: by hand.
    Names no break command -- off Windows that command always exits 2."""
    return (f"automatic stale-lock removal is unsupported on this platform (ADR-0008); delete "
            f"{path} by hand ONLY after independently confirming no fix_wikilinks run is live for "
            f"this vault")


def _held_message(root: Path, path: Path, raw, unread: str | None = None) -> str:
    head = [f"this vault's run lock is held -- one lock-participating fix_wikilinks writer per "
            f"vault (regression R161)", f"  LOCK: {path}"]
    if unread is not None:
        # The read itself failed (e.g. retries exhausted behind another
        # process's handle): the record was never seen, so it is NOT judged
        # malformed (ADR-0008 Q3). Platform-neutral wording: it applies off
        # Windows too (Q2, accepted deviation 2 -- diagnostics only).
        return "\n".join(head + [
            f"  holder: unknown -- the lock file could not be read ({unread})",
            "  holder status: not judged (unreadable, not malformed)",
            f"  re-run in a moment; if this repeats, find what holds {path} open -- do not delete it "
            f"while a fix_wikilinks run may be live for this vault"])
    rec = _parse_lock(raw) if raw is not None else None
    status, why = _holder_status(rec)
    lines = head + [f"  {_describe_holder(rec)}", f"  holder status: {status} ({why})"]
    if status == "stale":
        if _break_supported():
            lines.append(f"  the holder is gone; clear the lock with: {_break_lock_cmd(root)}")
        else:
            lines.append(f"  the holder is gone; {_manual_recovery(path)}")
        lines.append(_after_break_hint(rec))
    elif status == "unprovable":
        lines.append(f"  --break-lock will refuse; delete {path} by hand ONLY after confirming no "
                     f"fix_wikilinks run is live for this vault")
    else:
        lines.append("  wait for that run to finish and read its result first")
    return "\n".join(lines)


class RunLock:
    def __init__(self, path: Path, data: bytes, root: Path, hook: dict | None = None):
        self.path = path
        self.data = data
        self.root = root
        self.hook = hook or {}

    @classmethod
    def acquire(cls, root: Path, default_dir: Path, mode: str) -> "RunLock":
        import json
        import os
        import uuid
        hook = _runlock_hook(root)
        lock_id = uuid.uuid4().hex
        path = default_dir / RUN_LOCK_NAME
        tmp = default_dir / f"{RUN_LOCK_NAME}.{lock_id}.tmp"
        try:
            # Inside the refusal path (review: an uncreatable default dir
            # was a traceback, rc 1, instead of a clean exit 2).
            default_dir.mkdir(parents=True, exist_ok=True)
            pid = os.getpid()
            rec = {"schema_version": RUN_LOCK_SCHEMA, "tool": STATE_TOOL, "lock_id": lock_id,
                   "pid": pid, "proc_created": _proc_status(pid)[1], "host": _hostname(),
                   "mode": mode, "started": _now_iso(), "vault": str(root)}
            data = (json.dumps(rec, indent=2) + "\n").encode("utf-8")
            with open(tmp, "xb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            if hook.get("gate"):
                _wait_for(hook["gate"])

            def link():
                if hook.get("nolink"):
                    import errno
                    raise OSError(errno.EPERM, "[test] injected: hard links unsupported", str(path),
                                  int(hook["nolink"]))
                os.link(tmp, path)
            try:
                _with_lock_retry(link, f"{RUN_LOCK_NAME} acquire")
            except FileExistsError:
                try:
                    raw, unread = _read_run_lock(path, f"{RUN_LOCK_NAME} holder read", hook), None
                except FileNotFoundError:
                    # Linked against an existing lock, then found it gone: a
                    # release or --break-lock removed it in between. Refuse
                    # this invocation; never silently retry the acquisition
                    # (ADR-0006: no waiting; ADR-0008 Q3). Off Windows too
                    # (Q2, accepted deviation 2 -- diagnostics only).
                    raise RunLockRefused(f"the run lock was released or broken while this run was "
                                         f"inspecting it -- re-run\n  LOCK: {path}")
                except OSError as e:
                    raw, unread = None, f"{e.__class__.__name__}: {e}"
                raise RunLockRefused(_held_message(root, path, raw, unread))
            except OSError as e:
                import errno
                if (getattr(e, "winerror", None) in _HARDLINK_UNSUPPORTED_WINERRORS
                        or e.errno in (errno.EPERM, errno.EXDEV, getattr(errno, "ENOTSUP", -1),
                                       getattr(errno, "EOPNOTSUPP", -1), errno.ENOSYS)):
                    raise RunLockRefused(f"cannot take the run lock {path}: the state dir's file system "
                                         f"does not support hard links ({e}) -- refusing rather than "
                                         f"using a non-atomic lock (ADR-0006)")
                raise RunLockRefused(f"cannot take the run lock {path} ({e.__class__.__name__}: {e})")
        except OSError as e:
            raise RunLockRefused(f"cannot take the run lock in {default_dir} "
                                 f"({e.__class__.__name__}: {e})")
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass  # an orphan temp record is harmless: it is never a lock
        if hook.get("hold"):
            _wait_for(hook["hold"])
        return cls(path, data, root, hook)

    def release(self) -> None:
        """Delete run.lock only while it still holds exactly this run's record;
        a failed delete warns and leaves the exit code alone (the lock is then
        stale: this process is about to be gone). On Windows the ownership
        re-read IS the read through the exclusive compare-and-delete handle
        (ADR-0008 Q6; Q3's shared reader is not used here -- accepted
        deviation 1): the bytes compared are the object deleted. A refused
        delete disposition is never retried or reopened (Q4, option A)."""
        if not _break_supported():
            return self._release_by_path()
        try:
            deleted = _lock_compare_delete(self.path, lambda raw: raw == self.data,
                                           f"{RUN_LOCK_NAME} release", self.hook)
        except FileNotFoundError:
            print(f"WARNING: {self.path} vanished while this run held it -- nothing to release",
                  file=sys.stderr)
            return
        except LockDeleteRefused as e:
            # Opened exclusively and compared (this run's record), but the
            # delete disposition was refused: nothing was deleted.
            self._warn_left(f"opened and compared, but the delete was refused: {e}")
            return
        except OSError as e:
            # Most likely still this run's own lock, unopened behind another
            # process's handle past the retry budget: nothing was deleted.
            self._warn_left(f"cannot open it exclusively to confirm ownership and delete it: "
                            f"{e.__class__.__name__}: {e}")
            return
        if not deleted:
            print(f"WARNING: {self.path} no longer holds this run's record -- left untouched",
                  file=sys.stderr)

    def _release_by_path(self) -> None:
        """Off Windows (ADR-0008 Q2): the by-path read-compare-unlink."""
        import os
        try:
            raw = _read_run_lock(self.path, f"{RUN_LOCK_NAME} re-read", self.hook)
        except FileNotFoundError:
            print(f"WARNING: {self.path} vanished while this run held it -- nothing to release",
                  file=sys.stderr)
            return
        except OSError as e:
            # Unread: the same warning as a failed delete.
            self._warn_left(f"cannot re-read it to confirm ownership: {e.__class__.__name__}: {e}")
            return
        if raw != self.data:
            print(f"WARNING: {self.path} no longer holds this run's record -- left untouched",
                  file=sys.stderr)
            return

        def unlink():
            _inject_lock("unlock", RUN_LOCK_NAME, self.path)
            os.unlink(self.path)
        try:
            _with_lock_retry(unlink, f"{RUN_LOCK_NAME} release")
        except OSError as e:
            self._warn_left(f"{e.__class__.__name__}: {e}")

    def _warn_left(self, why: str) -> None:
        if _break_supported():
            then = f"once this process has exited: {_break_lock_cmd(self.root)}"
        else:
            then = f"once this process has exited: {_manual_recovery(self.path)}"
        print("WARNING: operation completed, but run.lock could not be removed\n"
              f"LOCK: {self.path}\n"
              "Next run will refuse until the stale lock is cleared.\n"
              f"  ({why}; {then})",
              file=sys.stderr)


def run_break_lock(root: Path) -> int:
    """--break-lock: remove this vault's run.lock ONLY when its holder is
    provably gone; standalone (it runs nothing else). Windows only (ADR-0008):
    the stale judgment and the deletion apply to the same file object through
    one exclusive handle (_lock_compare_delete), so it cannot delete a live or
    replacement lock it did not judge stale. Off Windows it refuses."""
    try:
        default = default_state_dir(root)
    except StateError as e:
        return _reject(str(e))
    path = default / RUN_LOCK_NAME
    print(f"STATE: {default}")
    if not _break_supported():
        return _reject(f"--break-lock refused: automatic stale-lock removal is unsupported on this "
                       f"platform (it needs the Windows exclusive-handle delete, ADR-0008) -- "
                       f"nothing broken\n  LOCK: {path}\n  delete it by hand ONLY after independently "
                       f"confirming no fix_wikilinks run is live for this vault")
    hook = _runlock_hook(root)
    seen: dict = {}

    def stale(raw: bytes) -> bool:
        # The judgment, inside the exclusive handle's window: no output here.
        # Called at most once per --break-lock (ADR-0008 Q4, option A).
        rec = _parse_lock(raw)
        status, why = _holder_status(rec)
        seen.update(rec=rec, status=status, why=why)
        if hook.get("break_judged"):  # TEST-ONLY pause, handle still open
            import os
            Path(f"{hook['break_judged']}.at.{os.getpid()}").write_text("judged")
            _wait_for(hook["break_judged"])
        return status == "stale"
    _hostname()  # warm the socket import OUTSIDE the exclusive-handle window (ADR-0008 Q1)
    tries: list = []
    try:
        _lock_compare_delete(path, stale, f"{RUN_LOCK_NAME} break", hook, tries)
    except FileNotFoundError:
        if len(tries) > 1:
            print(f"No run lock at {path} -- nothing to break (it was removed meanwhile).")
        else:
            print(f"No run lock at {path} -- nothing to break.")
        return 0
    except LockExhausted as e:
        return _reject(f"could not open the run lock exclusively; nothing broken\n  LOCK: {path}\n"
                       f"  ({e})")
    except LockDeleteRefused as e:
        # Never retried: the open succeeded, the judgment ran once; only the
        # disposition failed, so the lock is byte-unchanged. No reopen and
        # no re-judge (ADR-0008 Q4, option A; review finding J1).
        return _reject(f"judged stale through the exclusive handle, but the delete was refused "
                       f"({e}; read-only attribute or ACL?) -- not broken\n  LOCK: {path}\n"
                       f"  {_describe_holder(seen.get('rec'))}")
    except OSError as e:
        return _reject(f"cannot open or delete the run lock {path} ({e.__class__.__name__}: {e}) "
                       f"-- not broken")
    rec, status, why = seen["rec"], seen["status"], seen["why"]
    if status != "stale":
        return _reject(f"--break-lock refused: the holder is {status} ({why})\n  LOCK: {path}\n"
                       f"  {_describe_holder(rec)}\n  only a lock whose holder is provably gone is "
                       f"broken; " + ("wait for that run to finish" if status == "alive" else
                                      f"delete {path} by hand ONLY after confirming no fix_wikilinks "
                                      f"run is live for this vault"))
    # Judged stale through the exclusive handle and deleted through it: the
    # name was released at CloseHandle; no artifact is left (Q5).
    print(f"BROKE STALE LOCK: {_describe_holder(rec)} -- {why}")
    if rec and rec.get("mode") == "backtick-apply":
        print("Next: re-run the backtick dry run (no --alias-only) and read it; the dead backtick "
              "--apply keeps no record, and pages it already rewrote were not backed up.")
    else:
        print("Next: run a dry run first; it reports any unfinished run the dead holder left.")
    return 0


def sibling_locks(root: Path, default: Path, hook: dict | None = None) -> list[str]:
    """ADR-0006 sibling check, run AFTER this run holds its own lock: a
    run.lock under another state key with this vault's folder name whose
    holder is alive or unprovable. Windows lets one directory be reached
    under path spellings the ADR-0005 path hash keeps apart (C:\\x\\Vault vs
    \\\\localhost\\c$\\x\\Vault), so such a lock may be THIS vault's. It may
    also be a different vault with the same folder name: the refusal is
    deliberately conservative (safety over picking a winner), and nothing
    else of that key -- runs, registry, its lock -- is ever treated as this
    vault's. A stale sibling lock does not block. Each sibling run.lock is
    read through the shared-mode reader under the retry (ADR-0008 Q3): a
    breaker judging it is waited out, never reported as malformed."""
    import os
    try:
        base = state_base()
        sibs = sorted(base.iterdir()) if base.is_dir() else []
    except (StateError, OSError) as e:
        return [f"cannot list {STATE_TOOL} state keys to check for this vault under another path "
                f"spelling ({e})"]
    prefix = os.path.normcase(root.name) + "-"
    own = os.path.normcase(default.name)
    out = []
    for d in sibs:
        n = os.path.normcase(d.name)
        if n == own or not n.startswith(prefix) or not HASH8_RE.fullmatch(n[len(prefix):]):
            continue
        lock = d / RUN_LOCK_NAME
        try:
            raw = _read_run_lock(lock, f"sibling {RUN_LOCK_NAME} read", hook)
        except FileNotFoundError:
            continue
        except OSError as e:
            # Never seen (e.g. retries exhausted behind another process's
            # handle): conservative (it blocks), but NOT called malformed.
            out.append(f"{lock}: holder: unknown -- the lock file could not be read "
                       f"({e.__class__.__name__}: {e}); vault (unknown); holder status not judged "
                       f"(unreadable, not malformed) -- re-run in a moment; if this repeats, find "
                       f"what holds {lock} open")
            continue
        rec = _parse_lock(raw)
        status, why = _holder_status(rec)
        if status == "stale":
            continue
        vault = rec.get("vault") if rec else None
        hint = ("wait for that run to finish" if status == "alive" else
                f"--break-lock cannot clear it (it clears only this vault's own lock): delete {lock} "
                f"by hand ONLY after confirming no fix_wikilinks run is live for that vault")
        out.append(f"{lock}: {_describe_holder(rec)}; vault {vault or '(unknown)'}; holder status "
                   f"{status} ({why}) -- {hint}")
    return out


def _lock_mode(args) -> str | None:
    """The run-lock mode name, or None for a run that takes no lock: plain
    --verify and the plain migrate dry run are read-only; with --state-dir
    they may register it (a state-dirs.json write), so they lock too."""
    explicit = getattr(args, "state_dir", None) is not None
    if getattr(args, "forget_state_dir", None) is not None:
        return "forget-state-dir"
    if getattr(args, "rebind_state", None) is not None:
        return "rebind-state"
    if getattr(args, "migrate_legacy_state", False):
        return "migrate-apply" if args.apply else ("migrate-dry-run" if explicit else None)
    if args.restore is not None:
        return "restore"
    if args.verify:
        return "verify" if explicit else None
    return "apply" if args.apply else "dry-run"


def run_alias(args, root: Path) -> int:
    """--alias-only entry: every apply / restore (and migrate --apply) ends
    with its LOCK RETRIES line, whatever its exit path (ADR-0005 R4)."""
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    arm_lock_faults(root)
    mutating = bool(args.apply or args.restore is not None)
    try:
        return _run_alias(args, root)
    finally:
        if mutating:
            print(lock_report())
            sys.stdout.flush()


def _run_alias(args, root: Path) -> int:
    """Take the run lock (ADR-0006) around every state-writing alias run.
    ADR-0005's path validation runs first and may only REFUSE (nothing
    written anywhere, not even the lock); the run itself re-reads every bit
    of state under the lock."""
    if getattr(args, "break_lock", False):
        return run_break_lock(root)
    mode = _lock_mode(args)
    if mode is None:
        return _run_alias_locked(args, root)
    try:
        pre = State(root, getattr(args, "state_dir", None), args.manifest, paths_only=True)
    except StateError as e:
        return _reject(str(e))
    try:
        lock = RunLock.acquire(root, pre.default, mode)
    except RunLockRefused as e:
        return _reject(str(e))
    try:
        refusal = _sibling_refusal(root, pre.default)
        if refusal:
            return _reject(refusal)
        return _run_alias_locked(args, root)
    finally:
        lock.release()


def _sibling_refusal(root: Path, default: Path) -> str | None:
    """Run AFTER this run holds its own lock (ADR-0006 sibling check): the
    refusal text when a same-folder-name sibling key holds a live or
    unprovable run lock, else None."""
    hook = _runlock_hook(root)
    sibs = sibling_locks(root, default, hook)
    if hook.get("after_sibling_check"):
        import os
        Path(f"{hook['after_sibling_check']}.at.{os.getpid()}").write_text("checked")
        _wait_for(hook["after_sibling_check"])
    if not sibs:
        return None
    return ("another state key with this vault's folder name holds a live (or unprovable) run "
            "lock -- it may be THIS vault reached under another path spelling (e.g. "
            "\\\\localhost\\c$\\...), or a different vault with the same folder name; refusing "
            "either way (ADR-0006 sibling check: safety over picking a winner):\n  "
            + "\n  ".join(sibs)
            + "\n  this run's own lock is released")


def run_backtick_locked(args, root: Path) -> int:
    """Backtick mode (ADR-0007): --apply takes the SAME per-vault run lock as
    the alias writers -- mutual exclusion and lock recovery ONLY, never an
    alias-state transaction (no manifest, backups, apply log, registry or
    discovery; the default state dir only holds run.lock). The dry run writes
    nothing and stays lock-free. Uncontended output is unchanged (the pinned
    c376aaa parity); only refusals print anything new."""
    if not args.apply:
        return run_backtick(args, root)
    try:
        default = default_state_dir(root)
    except StateError as e:
        return _reject(f"backtick --apply needs the run lock (ADR-0007), which lives in the default "
                       f"state dir (backtick mode keeps no other state there; --state-dir is an "
                       f"--alias-only option): {e}")
    if _inside(default, root) or _backup_in_vault(default, root):
        return _reject(f"the default state dir {default} resolves inside the vault {root} "
                       f"(LOCALAPPDATA points into it) -- the run lock cannot live there")
    arm_lock_faults(root)
    try:
        lock = RunLock.acquire(root, default, "backtick-apply")
    except RunLockRefused as e:
        return _reject(str(e))
    try:
        refusal = _sibling_refusal(root, default)
        if refusal:
            return _reject(refusal)
        return run_backtick(args, root)
    finally:
        lock.release()


def _run_alias_locked(args, root: Path) -> int:
    import json
    import os
    wl = _wikilib()
    # Operational state (ADR-0005): resolve and validate the state dir before
    # anything else -- a refusal here writes nothing anywhere.
    try:
        st = State(root, getattr(args, "state_dir", None), args.manifest)
    except StateError as e:
        return _reject(str(e))
    if getattr(args, "forget_state_dir", None) is not None:
        return run_forget_state_dir(st, args.forget_state_dir)
    if getattr(args, "rebind_state", None) is not None:
        return run_rebind_state(st, args.rebind_state)
    try:
        st.register()
    except (StateError, OSError) as e:
        return _reject(f"cannot register --state-dir {st.dir}: {e}" if isinstance(e, OSError) else str(e))
    blockers = st.header()
    if getattr(args, "migrate_legacy_state", False):
        return run_migrate_legacy(st, args.apply, blockers)
    if blockers and (args.apply or args.restore is not None):
        for b in blockers:
            print(f"  fail-closed  {b}")
        return _reject(f"--{'apply' if args.apply else 'restore'} fails closed: cannot prove there "
                       f"is no unfinished run ({len(blockers)} reason(s) above)")
    if args.restore is not None:
        return run_restore(args.restore, root, st)
    manifest_path = st.manifest

    if args.verify:
        plan = build_plan(root, wl)
        if not print_summary(plan, "VERIFY"):
            return 5
        n = safe_rewrites(plan)
        print(f"Residual: review={len(plan['review'])}, broken={plan['counts']['broken']}")
        if st.untrusted:
            print(f"VERIFY FAIL: cannot prove there is no unfinished run ({len(st.untrusted)} "
                  f"reason(s) in the WARNING lines above).")
            return 1
        unf = unfinished_runs(root, st=st)
        if unf:
            # A failed apply's writes still in the vault are not a verified
            # state, whatever SAFE_REWRITES says (review h3-R3-DS-1).
            report_unfinished(root, unf, st)
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
        try:
            st.ensure_identity(st.dir)
            if _inside(manifest_path, root):  # the write boundary (R1)
                raise OSError(f"refusing to write the manifest inside the vault: {manifest_path}")
            rid = write_manifest(plan, root, manifest_path)
        except OSError as e:
            return _reject(f"could not save the manifest {manifest_path} ({e.__class__.__name__}: {e})")
        print(f"Manifest: {manifest_path} (run_id {rid})")
        # A failed earlier apply (another run_id) is reported here, where
        # the refusal's next step leads (review h3-R3-DS-1); --apply refuses.
        report_unfinished(root, unfinished_runs(root, st=st), st)
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
        manifest = json.loads(_read_retry(manifest_path, "stateread", manifest_path.name).decode("utf-8"))
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
    backup_root = st.dir / "backup" / rid
    # One run, one state location: logs of this run_id anywhere else (another
    # --state-dir, the default dir, legacy in-vault state) would split the
    # run, and --restore reads the apply logs of ONE location.
    elsewhere = [d for d in st.find_run(rid) if not _same_path(d, backup_root)]
    if elsewhere:
        legacy = any(st.is_legacy(d) for d in elsewhere)
        return _reject(f"run_id {rid} already has apply logs in {', '.join(str(d) for d in elsewhere)}"
                       + (" (legacy in-vault state) -- run --migrate-legacy-state --apply first"
                          if legacy else
                          " -- one run keeps one state location; re-run with --state-dir set to "
                          "that run's state dir"))
    # Fail-safe read (review t2-DS1-same-rid-corrupt-prev-silent-wedge): the
    # current log strictly; an unreadable rotated prev log is skipped with a
    # NOTE when the current log is settled (the vault never changes, so the
    # run_id never rotates and a hard reject would wedge every re-apply),
    # and otherwise still rejects -- with the set-aside remedy.
    try:
        prior_logs, bad_prev = _apply_logs_tolerant(backup_root)
    except ValueError as e:
        return _reject(str(e))
    # Belt and braces for 'one state dir per vault' (State.foreign): an
    # earlier log of this run_id rooted at another vault is never rotated
    # away by this apply -- it may be that vault's only restore evidence.
    _, root_err = restore_root_gate(st, backup_root, prior_logs)
    if root_err:
        return _reject(f"run_id {rid}: {root_err} -- {backup_root} holds another vault's apply of this "
                       f"run; one state dir per vault")
    if bad_prev:
        cur_settled = (bool(prior_logs) and prior_logs[-1][0].name == "apply-log.json"
                       and prior_logs[-1][1].get("result") in SETTLED_RESULTS)
        if not cur_settled:
            print(f"RESTORE: {st.restore_cmd(rid, backup_root)}")
            print(set_aside_remedy(backup_root))
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
            cur = _sha256(_read_retry(root / rel, "read", rel))
        except (FileNotFoundError, NotADirectoryError):
            continue  # gone: holds nothing an earlier apply wrote
        except OSError as e:
            # Fail closed (a lock past the retry, or any other refusal): an
            # unreadable file could be an earlier apply's write.
            return _reject(f"cannot check {rel} against the earlier apply of run_id {rid}: "
                           f"{e.__class__.__name__}: {e}")
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
        print(f"RESTORE: {st.restore_cmd(rid, backup_root)}")
        return 2
    # The same guard across run_ids (review h3-R3-DS-1): a failed apply's
    # files have changed, so the next dry run issues a NEW run_id and the
    # check above never sees the old one.
    unf = unfinished_runs(root, exclude=rid, st=st)
    if unf:
        report_unfinished(root, unf, st)
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
    try:
        if log_path.exists():  # an earlier apply of the same plan (restored or complete);
            # kept beside the new log -- run_restore reads every apply-log*.json.
            # COPIED, never moved: until the new log's first write-ahead save
            # atomically replaces apply-log.json, a current log must exist, or
            # a crash in between would leave --restore nothing to open (R5).
            stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S%f")
            _copy_state_file(log_path, log_path.with_name(f"apply-log.prev-{stamp}.json"))
        st.ensure_identity(st.dir)
    except OSError as e:
        return _reject(f"could not prepare the state dir {st.dir} ({e.__class__.__name__}: {e})")
    log = {"schema_version": APPLY_LOG_SCHEMA, "run_id": rid, "root": _norm_root(root),
           "tool_sha256": _tool_sha256(), "manifest": str(manifest_path),
           "state_dir": str(st.dir),
           "started": datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(),
           "restore_command": st.restore_cmd(rid, backup_root),
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
    # True once THIS apply's log has been durably saved. An apply-log.json
    # already on disk may be an EARLIER apply of the same run_id (copied to
    # prev, left in place): it is not evidence that this apply wrote anything.
    journaled = False
    earlier_log = log_path.is_file()

    def journal(label: str) -> str | None:
        """One apply-log save (lock retry inside); None, or why it failed."""
        nonlocal journaled
        try:
            _write_json(log_path, log)
            journaled = True
            return None
        except OSError as e:
            return f"apply log save failed at the {label} record ({e.__class__.__name__}: {e})"

    def stop(bi: int, batch: list[str], written: list[str], n_edits: int, headline: str) -> int:
        """Stop the batch loop cleanly (a failed postcondition, an exhausted
        lock retry, a backup conflict, a failed log save): nothing further is
        written, the files already written are listed, exit 3 with the
        RESTORE command. Never skip a file and carry on (ADR-0005 R4)."""
        later = ([r for r in batch if r not in written and r not in changed]
                 + [r for b in batches[bi:] for r in b])
        not_attempted = sum(len(plan["files"][r]["edits"]) for r in later)
        skipped = stale_only_links + sum(len(plan["files"][r]["edits"]) for r in changed)
        print(f"Batch {bi}/{len(batches)}: {len(written)} files, {n_edits} edits -- {headline}")
        if written:
            print(f"STOP. Batch {bi} files are written (originals in {backup_root}); "
                  f"{len(batches) - bi} remaining batches untouched.")
        else:
            print(f"STOP. Batch {bi} wrote no file; {len(batches) - bi} remaining batches untouched.")
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
        if not journaled:
            # The very first write-ahead save failed: no page was touched by
            # this apply. A RESTORE would only be refused -- or, with an
            # earlier apply's settled log still at apply-log.json, would undo
            # that earlier apply (e.g. a smoke batch meant to be kept).
            print("Nothing was written in the vault by this apply (its apply log could not be "
                  "saved), so there is nothing to restore -- fix the cause, then re-run --apply.")
            if earlier_log:
                print(f"An earlier apply of this run_id remains settled; its log {log_path} is "
                      f"unchanged and so is its own RESTORE.")
            return 3
        print(f"Apply log: {log_path}")
        print("To put back every file this apply wrote (only files still exactly as written; "
              "anything edited since is listed REVIEW_REQUIRED and left alone):")
        print(f"RESTORE: {st.restore_cmd(rid, backup_root)}")
        return 3

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
                                 # Relative to the state dir, never to the vault (ADR-0005).
                                 "backup": (Path("backup") / rid / rel).as_posix(),
                                 "links": len(plan["files"][rel]["edits"]), "status": "pending"}
        log["batches"].append({"batch": bi, "files": list(batch), "status": "writing"})
        err = journal(f"batch {bi} intent")
        if err:
            return stop(bi, batch, [], 0, f"APPLY LOG SAVE FAILED before any write of this batch: {err}")
        written: list[str] = []
        write_error = None
        for rel, data in todo:
            entry = log["files"][rel]
            # TOCTOU guard: the plan was built from bytes read before this
            # batch loop started. A human (or Dropbox) edit since then must
            # not be overwritten by the planned text -- re-hash immediately
            # before the write and leave a changed file alone.
            try:
                raw_now = _read_retry(root / rel, "read", rel)
            except (FileNotFoundError, NotADirectoryError):
                raw_now = None  # deleted since the plan: a concurrent edit
            except OSError as e:
                # A lock past the retry, or any other refusal -- a real
                # Windows sharing violation on open() is PermissionError
                # errno 13 with no winerror, so it is never retried (R4) --
                # stops the batch. Never 'changed-during-apply': nobody is
                # known to have edited the page, and the batch must not skip
                # it and carry on (ADR-0005 R4).
                write_error = f"read failed for {rel} ({e.__class__.__name__}: {e})"
                break
            if raw_now is None or _sha256(raw_now) != entry["pre_apply_sha256"]:
                changed.append(rel)
                entry["status"] = "changed-during-apply"
                print(f"  review  changed-during-apply  {rel}")
                continue
            # R4b: a backup that is absent is written; one byte-identical to
            # the original is kept; any other existing backup is a conflict.
            # Any failure stops the batch -- a page is never written without
            # its verified backup, and never skipped for a later one.
            try:
                _write_backup(backup_root / rel, raw_now, rel)
            except OSError as e:
                entry["status"] = "backup-failed"
                write_error = f"backup failed for {rel} ({e.__class__.__name__}: {e})"
                break
            if data != raw_now:
                try:
                    outcome = _write_page(root, rel, data, {entry["pre_apply_sha256"]})
                except OSError as e:  # e.g. a Dropbox / AV lock that outlasted the retry
                    entry["status"] = "write-failed"
                    write_error = f"write failed for {rel} ({e.__class__.__name__}: {e})"
                    break
                if outcome == "changed":
                    # R4a: edited after the TOCTOU read above -- during the
                    # backup's lock backoff, or between a refused write and
                    # its retry (_write_page re-hashes before every attempt).
                    changed.append(rel)
                    entry["status"] = "changed-during-apply"
                    print(f"  review  changed-during-apply  {rel}")
                    continue
                # 'already': the refused write had landed; 'present': the page
                # already held exactly the planned bytes -- written either way.
                entry["status"] = "written"
            else:
                entry["status"] = "unchanged"
            written.append(rel)
        n_edits = sum(len(plan["files"][r]["edits"]) for r in written)
        err = journal(f"batch {bi} after-write")
        if err:
            return stop(bi, batch, written, n_edits, "APPLY LOG SAVE FAILED after the batch's writes: "
                        + "; ".join(([write_error] if write_error else []) + [err])
                        + " (the write-ahead intent on disk covers every file of the batch)")
        before_alias = sum(plan["files"][r]["counts"]["alias-unique"]
                           + plan["files"][r]["counts"]["alias-unique-held"] for r in written)
        before_broken = sum(plan["files"][r]["counts"]["broken"] for r in written)
        before_canonical = sum(plan["files"][r]["counts"]["canonical"] for r in written)
        if not written and write_error is None:
            log["batches"][-1]["status"] = "empty"
            err = journal(f"batch {bi} empty")
            if err:
                return stop(bi, batch, written, 0, f"APPLY LOG SAVE FAILED: {err}")
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
                # A page the check cannot re-read is a postcondition failure,
                # never a skipped check: recount only counts links, it does
                # not assert table shape (ADR-0005 R4: an exhausted retry
                # stops the batch).
                try:
                    now_text = _decode(_read_retry(root / r, "read", r))[1]
                except (OSError, UnicodeDecodeError) as e:
                    failed.append(f"table check could not re-read {r} ({e.__class__.__name__}: {e})")
                    continue
                if _table_line_flags(now_text) != _table_line_flags(_decode(plan["new"][r][2])[1]):
                    reshaped.append(r)
            if reshaped:
                failed.append(f"table structure changed in {', '.join(reshaped)}")
        if failed:
            log["batches"][-1]["status"] = "failed"
            log["result"] = "postcondition-failed"
            err = journal(f"batch {bi} failed")
            if err:
                print(f"NOTE: {err} -- the write-ahead intent on disk still covers the batch.")
            return stop(bi, batch, written, n_edits, f"POSTCONDITION FAILED: {'; '.join(failed)}")
        log["batches"][-1]["status"] = "committed"
        err = journal(f"batch {bi} committed")
        if err:
            return stop(bi, batch, written, n_edits, f"postcondition OK, but APPLY LOG SAVE FAILED: {err}")
        committed.append(bi)
        applied_files += len(written)
        applied_links += n_edits
        for r in written:
            applied_edits += plan["files"][r]["edits"]
        print(f"Batch {bi}/{len(batches)}: {len(written)} files, {n_edits} edits -- postcondition OK "
              f"(alias-unique -{before_alias - after_alias}, canonical +{after['canonical'] - before_canonical}, "
              f"broken {before_broken} -> {after['broken']})")
    log["result"] = "partial" if deferred else "complete"
    changed_links = sum(len(plan["files"][r]["edits"]) for r in changed)
    skipped_links = stale_only_links + changed_links
    err = journal("final")
    if err:
        print(f"APPLY LOG SAVE FAILED: {err}")
        print(f"STOP. Every batch ({', '.join(str(b) for b in committed) or 'none'}) was written and "
              f"passed its postconditions, but the apply log does not record the run as finished, so "
              f"it counts as unfinished: restore it (RESTORE below), then re-run the dry run and --apply.")
        reconcile(planned_links, [("links_applied", applied_links)] + stale_terms
                  + [("links_skipped_stale", skipped_links)] + deferred_terms, log_path)
        print(f"Apply log: {log_path}")
        print(f"RESTORE: {st.restore_cmd(rid, backup_root)}")
        return 3
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
        print(f"Undo: {st.restore_cmd(rid, backup_root)}")
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
              f"under BROKEN, not ALIAS-ONLY: dotted names, '/' or ':' names)")
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


def unfinished_runs(root: Path, exclude: str | None = None, st: State | None = None,
                    bases: list[Path] | None = None) -> list[dict]:
    """Every run in any discovered state location (ADR-0005: the current
    state dir, the default dir, every registered --state-dir, and legacy
    <root>/.alias-fix-backup) -- or only under `bases` when given -- whose CURRENT apply log
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
    the set-aside remedy is printed too; "dir": the run directory. `exclude`:
    a run_id checked elsewhere (the manifest's own)."""
    out: list[dict] = []
    if bases is None:
        bases = (st if st is not None else State(root)).bases()
    dirs: list[Path] = []
    for b in bases:
        try:
            dirs += _run_dirs(b, strict=True)
        except OSError as e:
            # An un-listable backup base may hold an unfinished run: report
            # it (fail closed: --apply refuses, --verify fails, a dry run
            # warns) instead of reading it as empty (ADR-0005 R2).
            out.append({"run_id": f"(backup dir {b})", "result": "unlistable", "files": [],
                        "error": f"cannot list {b} ({e.__class__.__name__}: {e}) -- cannot prove "
                                 f"there is no unfinished run", "unlistable": True, "dir": b})
    completes = None  # [(started, files)] of every readable complete log, built on need
    for d in dirs:
        if d.name == exclude or not (d / "apply-log.json").is_file():
            continue
        try:
            lg = _read_apply_log(d / "apply-log.json")
        except ValueError as e:
            out.append({"run_id": d.name, "result": "unreadable log", "error": str(e), "files": [],
                        "dir": d})
            continue
        if lg.get("result") in SETTLED_RESULTS:
            continue
        live = []
        for rel, e in sorted(lg["files"].items()):
            post, pre = e.get("post_apply_sha256"), e.get("pre_apply_sha256")
            if not post or post == pre:
                continue
            try:
                cur = _sha256(_read_retry(root / rel, "read", rel))
            except (FileNotFoundError, NotADirectoryError):
                continue  # gone: holds none of the apply's writes
            except OSError:
                # Unreadable -- a lock past the retry, or a refusal with no
                # lock code (a real Windows sharing violation on open() is
                # errno 13, winerror None): cannot prove it is not live, so
                # fail CLOSED (it was skipped -- fail open -- for the latter).
                live.append(rel)
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
                        "restore_unsettled": tried, "dir": d})
    return out


def report_unfinished(root: Path, runs: list[dict], st: State | None = None) -> None:
    if st is None:
        st = State(root)
    for u in runs:
        bdir = u.get("dir") or root / LEGACY_BACKUP / u["run_id"]
        if u.get("unlistable"):
            print(f"WARNING: {u['error']}. Fix access to that directory and re-run.")
            continue
        if u.get("error"):
            # No RESTORE line: --restore refuses an unreadable log, so it is
            # not a remedy (review t1-DS1-b).
            print(f"WARNING: apply {u['run_id']} has an unreadable log; its files cannot be "
                  f"checked ({u['error']}).")
            print(f"  Inspect {bdir} (apply-log*.json and the original pages it holds) and put "
                  f"back by hand any page that still holds that apply's edits; then set the run "
                  f"aside by renaming that directory to {u['run_id']}-inspected (or moving it out "
                  f"of {bdir.parent}). This check skips it from then on.")
            continue
        print(f"WARNING: unfinished apply {u['run_id']} ({u['result']}) still has "
              f"{len(u['files'])} files as it wrote them -- restore it before any new apply:")
        for rel in u["files"]:
            print(f"  written-by-unfinished-apply  {rel}")
        print(f"RESTORE: {st.restore_cmd(u['run_id'], bdir)}")
        if u.get("restore_unsettled"):
            # RESTORE was already run and left REVIEW_REQUIRED rows: re-running
            # it will not settle the run (review t2-DS1-unsettleable-live-run).
            print(set_aside_remedy(bdir))


def _read_apply_log(p: Path) -> dict:
    """One apply log, validated; ValueError when unreadable. The read retries
    a lock (R4): a sub-second sync / AV lock must not read as an unreadable
    log (restore refusing, the set-aside remedy printed for a live run); a
    lock that outlasts the retry still fails closed as unreadable."""
    import json
    try:
        lg = json.loads(_read_retry(p, "stateread", p.name).decode("utf-8"))
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


# REVIEW_REQUIRED reasons of a restore that re-running RESTORE clears: a
# page or backup the OS kept locked past the lock retry.
TRANSIENT_REASONS = ("restore-write-failed", "restore-read-failed", "restore-not-attempted")
# A page an apply logged but never wrote (changed during the apply, or its
# backup failed): nothing to restore, so it never needs the set-aside remedy
# and never keeps a run from settling -- but it is still accounted for.
NEVER_WRITTEN_REASON = ("never-written (the apply logged this page but never wrote it, and it "
                        "no longer holds its original bytes: a later edit -- nothing to restore)")


def is_transient(reason) -> bool:
    return str(reason).startswith(TRANSIENT_REASONS)


def needs_set_aside(reasons) -> bool:
    """True when a restore's REVIEW_REQUIRED reasons include one that
    re-running RESTORE will not clear. A locked page
    ('restore-write-failed' / 'restore-read-failed') is transient -- re-run
    the same RESTORE -- so a lock-only restore never gets the set-aside
    remedy, nor does a never-written page (one predicate for run_restore and
    unfinished_runs; review t2-DS1-unsettleable)."""
    return any(not (is_transient(r) or str(r).startswith("never-written")) for r in reasons)


def set_aside_remedy(bdir: Path) -> str:
    """The manual way out when RESTORE cannot settle a run (review t2-DS1):
    renaming the run directory takes it out of RUN_ID_RE, so neither the
    unfinished-run scan nor the same-run_id gate reads it again."""
    rid = bdir.name
    return (f"SET ASIDE: RESTORE cannot settle run {rid}. Put the REVIEW_REQUIRED files right by "
            f"hand (or accept them as they are), then set the run aside by renaming {bdir} to "
            f"{rid}-inspected (or moving it out of {bdir.parent}); the unfinished-run check "
            f"stops guarding it from then on.")


def restore_plan(root: Path, rid: str, backup_root: Path, logs: list[tuple[Path, dict]],
                 bad_prev: list[str]) -> list[tuple]:
    """The non-writing half of --restore: one row per file of every apply of
    the run, (rel, verdict, why, backup bytes, post hashes) with verdict
    'already' (ALREADY_ORIGINAL), 'review' (REVIEW_REQUIRED) or 'restore'
    (the backup may be copied back), then one 'review' row per unreadable
    rotated log. For a 'restore' row, `why` carries the pre_apply_sha256 the
    written bytes must hash to. Every page an apply logged is accounted for
    exactly once (ADR-0005 R5) -- a page it logged but never wrote
    (changed-during-apply, backup-failed) is ALREADY_ORIGINAL while it still
    holds its original bytes, else a never-written REVIEW row. Page and
    backup reads retry a winerror 5/32/33 lock (R4); a lock that outlasts
    the retry -- or any read refusal but 'gone', e.g. the errno-13 /
    no-winerror shape a real Windows read lock has, which is never retried
    -- is a transient 'restore-read-failed' row. Reused by --migrate-legacy-state to
    prove the migrated copy restores exactly like the source."""
    # Every apply of this run_id, not only the latest (review h1-I8-1): a
    # re-apply rotates the earlier log to apply-log.prev-*.json, and the
    # files that apply wrote must still be restorable by this command.
    merged: dict[str, dict] = {}
    never: dict[str, set] = {}
    for _, lg in logs:
        for rel, e in lg["files"].items():
            # write-failed: os.replace is atomic, so the file is either the
            # original (ALREADY_ORIGINAL) or the planned bytes (restorable).
            if e.get("status") not in ("pending", "written", "unchanged", "write-failed"):
                # never written by that apply (changed-during-apply, backup-failed)
                never.setdefault(rel, set()).add(e["pre_apply_sha256"])
                continue
            m = merged.setdefault(rel, {"pre": set(), "post": set(), "backup": e["backup"]})
            m["pre"].add(e["pre_apply_sha256"])
            m["post"].add(e["post_apply_sha256"])
            m["backup"] = e["backup"]

    def current(rel):
        try:
            return _sha256(_read_retry(root / rel, "read", rel)), None
        except (FileNotFoundError, NotADirectoryError):
            return None, None  # gone: classified by the hash rules below
        except OSError as ex:
            # A lock past the retry, or any other refusal -- a real Windows
            # sharing violation on open() is PermissionError errno 13 with
            # NO winerror, never retried (R4). No hash was computed, so this
            # is a transient row (RESTORE INCOMPLETE, re-run), never the
            # 'changed-since-apply' human-edit claim with a SET ASIDE remedy
            # (the 11-missed-files mechanism, R5b).
            return None, f"restore-read-failed (page: {ex.__class__.__name__}: {ex})"

    rows: list[tuple] = []
    for rel in sorted(set(merged) | set(never)):
        if rel not in merged:
            cur, err = current(rel)
            if err:
                rows.append((rel, "review", err, None, None))
            elif cur in never[rel]:
                rows.append((rel, "already", "", None, None))
            else:
                rows.append((rel, "review", NEVER_WRITTEN_REASON, None, None))
            continue
        m = merged[rel]
        if len(m["pre"]) != 1:
            rows.append((rel, "review", "apply logs disagree on pre_apply_sha256", None, None))
            continue
        pre = next(iter(m["pre"]))
        cur, err = current(rel)
        if err:
            rows.append((rel, "review", err, None, None))
            continue
        if cur == pre:
            rows.append((rel, "already", "", None, None))
            continue
        if cur not in m["post"]:
            rows.append((rel, "review", "changed-since-apply (current hash is not what the apply wrote)",
                         None, None))
            continue
        # The backup must lie under THIS run's backup dir, whatever the log
        # field says (ADR-0005: the field is state-dir relative, or legacy).
        bpath = _backup_path(m["backup"], backup_root, rid)
        try:
            if bpath is None:
                raise OSError("backup path outside the run's backup dir")
            bdata = _read_retry(bpath, "bread", rel)
        except (FileNotFoundError, NotADirectoryError):
            rows.append((rel, "review", "backup-missing", None, None))
            continue
        except OSError as ex:
            if bpath is None:
                rows.append((rel, "review", "backup-missing", None, None))
                continue
            # locked past the retry, or refused without a lock code: transient
            rows.append((rel, "review", f"restore-read-failed (backup: {ex.__class__.__name__}: {ex})",
                         None, None))
            continue
        if _sha256(bdata) != pre:
            rows.append((rel, "review", "backup-corrupt (backup hash is not pre_apply_sha256)", None, None))
            continue
        rows.append((rel, "restore", pre, bdata, set(m["post"])))
    for name in bad_prev:
        rows.append((name, "review", "unreadable rotated apply log -- any file only it wrote must be "
                                     "checked by hand", None, None))
    return rows


# Written into a run dir by --migrate-legacy-state: the log roots the legacy
# in-vault location accepted (a vault moved after a legacy in-vault-state apply), bound to
# the vault the run was migrated for.
MIGRATED_NAME = "migrated-from-vault.json"


def restore_root_gate(st: State, backup_root: Path, logs: list[tuple[Path, dict]]):
    """(moved_from roots, error or None): may --restore use these logs with
    --root st.root? A log root other than --root is accepted when the run
    dir is legacy state inside --root (the folder moved WITH its state;
    review arb-M1), when this vault's state was rebound from that root
    (--rebind-state), or when --migrate-legacy-state carried that exact
    acceptance for THIS vault out of the legacy location. Anything else is
    refused. One gate for run_restore and the migration's proof."""
    inside = st.is_legacy(backup_root)
    carried: set[str] = set()
    mj = _read_json_quiet(backup_root / MIGRATED_NAME)
    # Bound to the vault it was migrated for -- or one this vault's state was
    # later rebound from (a second move after the migration).
    if (isinstance(mj, dict) and isinstance(mj.get("accepted_roots"), list)
            and (mj.get("vault_root") == _norm_root(st.root)
                 or str(mj.get("vault_root")) in st.rebound_from)):
        carried = {str(x) for x in mj["accepted_roots"]}
    moved_from: list[str] = []
    for lp_, lg in logs:
        r = lg.get("root")
        if r != _norm_root(st.root):
            if not inside and str(r) not in st.rebound_from and str(r) not in carried:
                return moved_from, (f"apply log {lp_.name} root {r} is not --root "
                                    f"{_norm_root(st.root)}")
            if str(r) not in moved_from:
                moved_from.append(str(r))
    return moved_from, None


def run_restore(rid: str, root: Path, st: State | None = None) -> int:
    """Concurrency-safe undo of one apply (I8): a backup is copied back only
    when the file's current hash equals the post_apply_sha256 the apply
    recorded AND the backup's own hash equals pre_apply_sha256. Anything else
    is REVIEW_REQUIRED and left untouched -- newer work is never overwritten.
    Covers every apply of the run: apply-log.json plus each rotated
    apply-log.prev-*.json (a current hash equal to ANY logged post hash).
    The run is found by discovery (ADR-0005): the current and default state
    dirs, every registered --state-dir, and legacy in-vault state."""
    import datetime
    if not RUN_ID_RE.fullmatch(rid or ""):
        return _reject(f"--restore takes a 16-hex-digit run_id, got {rid!r}")
    if st is None:
        st = State(root)
    found = st.find_run(rid)
    if not found:
        return _reject(f"no apply log for run_id {rid} in any state location ("
                       + ", ".join(str(b) for b in st.bases()) + ")")
    if len(found) > 1:
        legacy = any(st.is_legacy(d) for d in found)
        return _reject(f"run_id {rid} has apply logs in more than one location ("
                       + ", ".join(str(d) for d in found) + ")"
                       + (" -- finish --migrate-legacy-state --apply first" if legacy else
                          " -- keep one copy (set the other aside) and re-run"))
    backup_root = found[0]
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
    # (review arb-M1-moved-vault-wedge; legacy in-vault state), and a root
    # this vault's state was rebound from (--rebind-state, ADR-0005) is the
    # same vault under its old path: the per-file pre/post hash gates below
    # keep the restore safe, so it proceeds with a NOTE. Any other root is
    # refused.
    moved_from, root_err = restore_root_gate(st, backup_root, logs)
    if root_err:
        return _reject(root_err)
    rows = restore_plan(root, rid, backup_root, logs, bad_prev)
    print(f"=== ALIAS-ONLY LINK FIX (regression R151) -- RESTORE run_id {rid} ===")
    print(f"Run: {backup_root}")
    for old in moved_from:
        print(f"NOTE: vault moved from {old} (the apply log's root) to {_norm_root(root)}; "
              f"restoring by the per-file hash checks.")
    if len(logs) > 1:
        print(f"Apply logs read: {', '.join(p.name for p, _ in logs)}")
    restored, already, review = [], [], []
    # R4 (owner ruling 2): an exhausted retry stops the restore --
    # no page is skipped and continued past. The stop point is the first page
    # whose read (restore_plan) or write/verify (below) could not complete;
    # every later page this restore would have written is listed as a
    # transient 'restore-not-attempted' row, so it is still accounted for
    # exactly once, RESTORE INCOMPLETE fires and a re-run finishes the job.
    # Rows needing no write ('already', non-transient review) keep their
    # verdict: classifying them writes nothing.
    stopped_at = None
    for rel, verdict, why, bdata, posts in rows:
        if verdict == "already":
            already.append(rel)
            continue
        if stopped_at is not None and verdict == "restore":
            review.append((rel, f"restore-not-attempted (restore stopped at {stopped_at} after an "
                                f"exhausted retry)"))
            continue
        if verdict == "review":
            review.append((rel, why))
            if stopped_at is None and is_transient(why):
                stopped_at = rel
            continue
        target = root / rel
        try:
            # R4: restore_plan hashed every page up front, so the page is
            # re-hashed immediately before its first write attempt (and
            # before each lock retry): it must still hold what the apply
            # wrote (else it was edited since the plan: left alone) or
            # already be the original.
            outcome = _write_page(root, rel, bdata, posts, kind="restore")
            if outcome == "present":
                already.append(rel)  # original again before we wrote anything
                continue
            ok = outcome == "already" or (
                outcome == "written" and _sha256(_read_retry(target, "read", rel)) == why)
        except OSError as ex:
            # A page locked past the retry (Obsidian / Dropbox / AV): list it,
            # STOP (no later page is written), and still write the ledger
            # (review h2-R2-RESTORE-LOCK); RESTORE INCOMPLETE below says re-run.
            review.append((rel, f"restore-write-failed ({ex.__class__.__name__}: {ex})"))
            stopped_at = rel
            continue
        if outcome == "changed":
            review.append((rel, "changed-during-restore (edited after the restore plan was made or "
                                "while a locked write was retried; left alone)"))
            continue
        if not ok:
            review.append((rel, "restore-verify-failed"))
            continue
        restored.append(rel)
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
    transient = [r for r, w in review if is_transient(w)]
    if transient:
        # regression R157 / R5b: a restore that could not put every page back is
        # not done, whatever RESTORED says -- say so on its own line.
        print(f"RESTORE INCOMPLETE: {len(transient)} file(s) could not be put back (a file lock that "
              f"outlasted the retry) and may still hold this run's writes -- re-run the same command "
              f"once the lock clears:")
        print(f"RESTORE: {st.restore_cmd(rid, backup_root)}")
    if needs_set_aside(w for _, w in review):
        print(set_aside_remedy(backup_root))
    log.setdefault("restores", []).append({
        "at": datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(),
        "restored": restored, "already_original": already, "tmp_removed": swept,
        "review_required": [{"file": r, "reason": w} for r, w in review]})
    if all(w.startswith("never-written") for _, w in review):
        # Settled: no file of any apply of this run still holds its writes,
        # so unfinished_runs() stops guarding it (review t1-DS1-a). A later
        # apply's legitimate write to the same page must never be offered
        # to this run's RESTORE as "unfinished". A never-written page holds
        # none of this run's writes, so it does not keep the run open.
        log["result"] = "restored"
    try:
        _write_json(log_path, log)
    except OSError as ex:
        print(f"ERROR: the restore ledger could not be saved ({ex.__class__.__name__}: {ex}) -- the "
              f"files listed 'restored' above ARE restored; re-run the same command to record it:",
              file=sys.stderr)
        print(f"RESTORE: {st.restore_cmd(rid, backup_root)}")
        return 3
    return 0 if not review else 1


# ---------------------------------------------------------------------------
# State maintenance: --forget-state-dir, --rebind-state, --migrate-legacy-state
# ---------------------------------------------------------------------------

def run_forget_state_dir(st: State, path) -> int:
    """Remove one state-dirs.json pointer, only after proving the directory
    it names holds no unfinished run. Pointers are never pruned otherwise."""
    q = Path(path).resolve()
    print(f"STATE: {st.dir}")
    print(f"REGISTERED IN: {st.registry}")
    if st.registry_error:
        return _reject(st.registry_error)
    if not any(_same_path(e["path"], q) for e in st.registered):
        return _reject(f"{q} is not registered in {st.registry}")
    if _inside(q, st.root):
        return _reject(f"{q} is inside the vault -- not trusted as a state dir; remove the pointer "
                       f"by hand after checking it")
    if not q.is_dir():
        return _reject(f"cannot prove there is no unfinished run in {q}: it is unreachable. If that "
                       f"directory is gone for good, create it empty and re-run --forget-state-dir")
    why = _unlistable(q / "backup")
    if why:
        return _reject(f"cannot prove there is no unfinished run in {q}: its backup dir "
                       f"{q / 'backup'} cannot be listed ({why}) -- fix access and re-run")
    live = unfinished_runs(st.root, bases=[q / "backup"])
    if live:
        report_unfinished(st.root, live, st)
        return _reject(f"{q} holds {len(live)} unfinished run(s) -- restore or set them aside first")
    entries = [e for e in st.registered if not _same_path(e["path"], q)]
    _write_json(st.registry, {"schema_version": STATE_SCHEMA, "tool": STATE_TOOL,
                              "vault_root": _norm_root(st.root), "state_dirs": entries})
    print(f"FORGOT: {q} (no unfinished run there; {len(entries)} pointer(s) left)")
    return 0


def run_rebind_state(st: State, old_path) -> int:
    """Attach the state of a vault path that no longer exists to this vault's
    key (a MOVED vault). Refused while the old path exists: then this vault
    may be a copy, and nothing transfers."""
    import json
    import os
    old = Path(old_path).resolve()
    print(f"STATE: {st.default}")
    if old.exists():
        return _reject(f"--rebind-state: {old} still exists -- this vault may be a copy of it, so "
                       f"its state does not transfer. Settle or restore its runs there first")
    if _same_path(old, st.root):
        return _reject("--rebind-state OLD_PATH names this vault itself")
    old_dir = default_state_dir(old)
    new_dir = st.default
    old_vid = _read_json_quiet(old_dir / VAULT_ID_NAME)
    if not old_dir.is_dir():
        # The shape an interrupted rebind leaves (the pre-fix tool renamed
        # the whole old dir onto this key, THEN wrote the grant): the runs
        # are here, this key's vault.json still names the old vault and has
        # no grant for it. Finish it -- record the grant -- instead of
        # refusing 'no state' with the moved runs unrestorable.
        vid = _read_json_quiet(new_dir / VAULT_ID_NAME)
        stranded = (isinstance(vid, dict)
                    and (vid.get("key") == old_dir.name or vid.get("root") == _norm_root(old))
                    and _norm_root(old) not in [str(x) for x in (vid.get("rebound_from") or [])])
        if not stranded:
            return _reject(f"no state for {old} at {old_dir}")
        old_vid = vid
    # Plan every move first; any collision refuses before anything moves.
    oldkey = old_dir.name
    moves: list[tuple[Path, Path]] = []
    merge_registry: list[dict] = []

    def record_rebind() -> None:
        """This key's vault.json gains rebound_from (the grant --restore
        needs for apply logs rooted at the old path)."""
        vid = _read_json_quiet(new_dir / VAULT_ID_NAME)
        vid = vid if isinstance(vid, dict) else {}
        rb = [str(x) for x in (vid.get("rebound_from") or []) if isinstance(x, str)]
        for x in [_norm_root(old)] + ([str(y) for y in old_vid.get("rebound_from") or []]
                                      if isinstance(old_vid, dict) else []):
            if x not in rb:
                rb.append(x)
        vid.update({"schema_version": STATE_SCHEMA, "tool": STATE_TOOL, "root": _norm_root(st.root),
                    "root_display": str(st.root), "key": vault_key(st.root), "rebound_from": rb,
                    "rebound_at": _now_iso()})
        _write_json(new_dir / VAULT_ID_NAME, vid)

    # ONE path whether or not this key exists yet (review: the absent-key
    # branch renamed the whole old dir FIRST and wrote the grant after it,
    # so a failed grant save -- or a kill in between -- stranded the runs
    # unrestorable, with no REBIND INCOMPLETE). Every child of old_dir is
    # planned here: vault.json and the registry are consumed, backup/<rid>
    # and manifest.json move to named slots, anything else moves as
    # <name>.rebound-from-<oldkey> -- so after the moves old_dir holds only
    # what this function removes itself.
    for child in (sorted(old_dir.iterdir()) if old_dir.is_dir() else []):
        if child.name == VAULT_ID_NAME:
            continue
        if child.name == REGISTRY_NAME:
            reg = _read_json_quiet(child)
            if not (isinstance(reg, dict) and isinstance(reg.get("state_dirs"), list)):
                return _reject(f"unreadable {child} -- repair it before rebinding")
            # May merge nothing (the empty list --forget-state-dir leaves,
            # or only non-dict entries); the file is removed either way.
            merge_registry = [e for e in reg["state_dirs"] if isinstance(e, dict)]
            continue
        if child.name == "backup" and child.is_dir():
            for run in sorted(child.iterdir()):
                moves.append((run, new_dir / "backup" / run.name))
            continue
        if child.name == "manifest.json":
            moves.append((child, new_dir / f"manifest.rebound-from-{oldkey}.json"))
            continue
        moves.append((child, new_dir / f"{child.name}.rebound-from-{oldkey}"))
    clash = [str(d) for _, d in moves if d.exists()]
    if clash:
        return _reject(f"--rebind-state: {', '.join(clash)} already exist -- nothing moved")
    if merge_registry and st.registry_error:
        return _reject(st.registry_error)
    # The grant goes FIRST: a grant with nothing moved is harmless (the old
    # path is gone), moved runs without it are unrestorable here.
    done: list[tuple[Path, Path]] = []
    try:
        new_dir.mkdir(parents=True, exist_ok=True)
        record_rebind()
        for src, dst in moves:
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
            done.append((src, dst))
        if merge_registry:
            entries = list(st.registered)
            for e in merge_registry:
                if isinstance(e.get("path"), str) and not any(_same_path(e["path"], x["path"])
                                                              for x in entries):
                    entries.append(e)
            _write_json(st.registry, {"schema_version": STATE_SCHEMA, "tool": STATE_TOOL,
                                      "vault_root": _norm_root(st.root), "state_dirs": entries})
        if old_dir.is_dir():
            (old_dir / REGISTRY_NAME).unlink(missing_ok=True)
            (old_dir / VAULT_ID_NAME).unlink(missing_ok=True)
            leftover_backup = old_dir / "backup"
            if leftover_backup.is_dir() and not any(leftover_backup.iterdir()):
                leftover_backup.rmdir()
            old_dir.rmdir()
    except OSError as e:
        print(json.dumps({"moved": [[str(a), str(b)] for a, b in done]}, ensure_ascii=True))
        print(f"REBIND INCOMPLETE: {e.__class__.__name__}: {e}. {len(done)} of {len(moves)} "
              f"move(s) done (runs and manifests are moved, never deleted). Fix the cause, then re-run "
              f"--rebind-state {old} to finish.", file=sys.stderr)
        return 3
    runs = len(_run_dirs(new_dir / "backup"))
    print(f"REBOUND: state of {old} ({old_dir}) -> {new_dir}; {runs} run(s) now under this vault's "
          f"key. --restore accepts apply logs rooted at {_norm_root(old)}.")
    print(json.dumps({"moved": [[str(a), str(b)] for a, b in moves]}, ensure_ascii=True))
    return 0


def _tree(p: Path) -> dict:
    """{relative posix path: (sha256, size, mtime_ns)} for every file under
    `p` (or of `p` itself when it is a file), plus the set of dirs."""
    files: dict[str, tuple] = {}
    dirs: set[str] = set()
    if p.is_file():
        s = p.stat()
        files[""] = (_sha256(p.read_bytes()), s.st_size, s.st_mtime_ns)
        return {"files": files, "dirs": dirs}
    for f in sorted(p.rglob("*")):
        rel = f.relative_to(p).as_posix()
        if f.is_dir():
            dirs.add(rel)
        elif f.is_file():
            s = f.stat()
            files[rel] = (_sha256(f.read_bytes()), s.st_size, s.st_mtime_ns)
    return {"files": files, "dirs": dirs}


def _copy_entry(src: Path, dst: Path) -> None:
    import shutil
    if src.is_dir():
        shutil.copytree(src, dst, copy_function=shutil.copy2)
    else:
        shutil.copy2(src, dst)


def _remove_entry(p: Path) -> None:
    """Delete one legacy entry: its apply logs FIRST -- the rotated
    apply-log.prev-*.json, then apply-log.json LAST -- then the rest. An
    interrupted delete therefore leaves either a run with its current log
    (fewer prev logs than the verified copy) or a log-less remnant; both are
    a strict subset of the copy's log set, which run_migrate_legacy resumes
    as an interrupted delete. (apply-log.json first -- plain sorted order --
    could leave a prev-only remnant that discovery still counts as a second
    location of the run.)"""
    import shutil
    # Legacy state sits in the (synced) vault: each removal retries a lock.
    if p.is_dir():
        logs = sorted(p.glob("apply-log*.json"), key=lambda f: (f.name == "apply-log.json", f.name))
        for lg in logs:
            _with_lock_retry(lambda: lg.unlink(missing_ok=True), f"delete {lg}")
        _with_lock_retry(lambda: shutil.rmtree(p) if p.exists() else None, f"delete {p}")
    else:
        _with_lock_retry(lambda: p.unlink(missing_ok=True), f"delete {p}")


def _restore_view(root: Path, rid: str, run_dir: Path) -> tuple:
    """What --restore would do with the run at `run_dir`, write-free: the
    migration's discovery proof compares this for source and copy."""
    try:
        logs, bad = _apply_logs_tolerant(run_dir)
    except ValueError:
        return ("unreadable-current-log",)
    if not logs:
        return ("no-logs",)
    return tuple((row[0], row[1], row[2] if row[1] != "restore" else "restorable")
                 for row in restore_plan(root, rid, run_dir, logs, bad))


def _unfinished_view(root: Path, run_dir: Path, bases: list[Path]) -> tuple:
    """The unfinished-run check's verdict on the run at `run_dir`, computed
    over `bases` -- the same universe for source and copy, so the
    later-complete-apply suppression cannot differ between them."""
    return tuple((u["run_id"], u["result"], tuple(u["files"]), bool(u.get("error")))
                 for u in unfinished_runs(root, bases=bases)
                 if _same_path(u["dir"], run_dir))


def _carry_root_acceptance(st: State, run_dir: Path) -> None:
    """Legacy in-vault state accepted any log root (the folder moved WITH its
    state, review arb-M1). Once the run leaves the vault that proof is gone,
    so record the exact roots its logs carry, bound to THIS vault, for
    restore_root_gate -- otherwise a vault moved after a legacy in-vault-state apply would
    have its migrated run stranded (restore: 'root ... is not --root')."""
    try:
        logs = _apply_logs_tolerant(run_dir)[0]
    except ValueError:
        return  # unreadable current log: --restore refuses it wherever it lives
    roots = sorted({str(lg.get("root")) for _, lg in logs if lg.get("root") != _norm_root(st.root)})
    if roots:
        _write_json(run_dir / MIGRATED_NAME, {"schema_version": STATE_SCHEMA, "tool": STATE_TOOL,
                                              "vault_root": _norm_root(st.root),
                                              "accepted_roots": roots, "migrated_at": _now_iso()})


def run_migrate_legacy(st: State, apply: bool, blockers: list[str]) -> int:
    """--migrate-legacy-state (ADR-0005): move legacy in-vault state into the
    state dir. Dry run by default. With --apply, per legacy entry: copy to a
    staging name -> verify every file's hash + size + mtime -> rename into
    place -> (runs) prove restore discovery finds it there and classifies it
    exactly like the source -> register -> only then delete the in-vault copy.
    Any failure keeps that entry's source; the stale manifest is deleted only
    after every entry migrated."""
    import os
    root = st.root
    lg = legacy_scan(root)
    dest_base = st.dir / "backup"
    print(f"=== ALIAS-ONLY LINK FIX -- MIGRATE LEGACY STATE ({'APPLY' if apply else 'DRY RUN'}) ===")
    print(f"LEGACY STATE: {len(lg['runs'])} run(s), {len(lg['entries']) - len(lg['runs'])} other "
          f"entr{'y' if len(lg['entries']) - len(lg['runs']) == 1 else 'ies'} in {lg['base']}; "
          f"manifest {'present: ' + str(lg['manifest']) if lg['manifest'] else 'absent'}")
    if not lg["total"]:
        print("Nothing to migrate: the legacy scan is zero.")
        return 0
    def is_run(e: Path) -> bool:
        # A <run_id> dir is a RUN only while an apply log is there (or, after
        # an interrupted delete, in its verified copy): discovery reads
        # nothing else, so a log-less remnant (an interrupted rm) moves as a
        # plain entry instead of failing the discovery proof forever.
        return e in lg["runs"] and (_has_logs(e) or _has_logs(dest_base / e.name))

    for e in lg["entries"]:
        kind = "run" if is_run(e) else "entry"
        print(f"  {kind:<5}  {e.name}  -> {dest_base / e.name}")
    if not apply:
        print("Each would be: copied -> verified (hash + size + mtime of every file) -> proved "
              "(restore discovery from the new location) -> registered -> deleted from the vault. "
              "The stale manifest is deleted only after all of them migrate.")
        print("(Dry run -- nothing written. Re-run with --migrate-legacy-state --apply.)")
        return 0
    if blockers:
        for b in blockers:
            print(f"  fail-closed  {b}")
        return _reject(f"--migrate-legacy-state --apply fails closed ({len(blockers)} reason(s) above)")
    failed: list[str] = []
    for e in lg["entries"]:
        name = e.name
        dest = dest_base / name
        stage = dest_base / (name + STAGING_SUFFIX)
        run_entry = is_run(e)
        created_dest = False  # THIS invocation renamed an unproven copy into place
        phase = "copy"
        try:
            src_tree = _tree(e)
            resumed = False
            if dest.exists():
                # An earlier migration copied it (and was interrupted before
                # or during the delete): every source file must already be
                # there byte-identical, or this entry fails and is kept.
                dt = _tree(dest)
                if any(dt["files"].get(k, (None,))[:2] != v[:2] for k, v in src_tree["files"].items()):
                    raise RuntimeError(f"{dest} already exists and differs from the legacy copy")
                print(f"  resume    {name}: already copied and verified")
                resumed = True
            else:
                if stage.exists():
                    _remove_entry(stage)  # our own unverified staging copy
                dest_base.mkdir(parents=True, exist_ok=True)
                _copy_entry(e, stage)
                if _tree(stage) != src_tree:
                    raise RuntimeError(f"verification failed: the copy at {stage} differs from the "
                                       f"legacy source (hash / size / mtime / file set)")
                os.replace(stage, dest)
                created_dest = True
                if _tree(dest) != src_tree:
                    raise RuntimeError(f"verification failed after rename: {dest}")
                print(f"  copied    {name}: {len(src_tree['files'])} file(s) verified")
            phase = "prove"
            if run_entry:
                _carry_root_acceptance(st, dest)
                st2 = State(root, st.dir if st.explicit else None)
                where = st2.find_run(name, legacy=False)
                if len(where) != 1 or not _same_path(where[0], dest):
                    raise RuntimeError(f"discovery proof failed: run {name} found at "
                                       f"{[str(w) for w in where]} (want exactly {dest})")
                # The root gate --restore applies ABOVE the classification:
                # the migrated copy must pass it whenever the legacy copy did.
                try:
                    dlogs = _apply_logs_tolerant(dest)[0]
                except ValueError:
                    dlogs = None  # unreadable current log: restore refuses both copies alike
                if dlogs and restore_root_gate(st2, dest, dlogs)[1] is not None:
                    raise RuntimeError(f"discovery proof failed: --restore {name} would refuse the "
                                       f"migrated copy ({restore_root_gate(st2, dest, dlogs)[1]})")
                src_logs = {p.name for p in e.glob("apply-log*.json")}
                dst_logs = {p.name for p in dest.glob("apply-log*.json")}
                if resumed and src_logs < dst_logs:
                    # An earlier migration proved this copy and began the
                    # delete (apply logs go first, apply-log.json last): the
                    # source has lost logs its verified copy still holds, so
                    # it can no longer be classified like the copy -- and the
                    # copy (a byte-identical superset) is what discovery
                    # reads. Only the delete removes logs from a source, and
                    # it starts only after the proof passed.
                    print(f"  proved    {name}: discovery finds it at {dest} (classification "
                          f"proved before the interrupted delete; the legacy remnant holds "
                          f"{len(src_logs)} of the copy's {len(dst_logs)} apply log(s))")
                    phase = "register"
                    st.register()
                    phase = "delete"
                    _remove_entry(e)
                    print(f"  deleted   {name}: legacy remnant removed from the vault")
                    continue
                if _restore_view(root, name, dest) != _restore_view(root, name, e):
                    raise RuntimeError(f"discovery proof failed: --restore {name} would classify the "
                                       f"migrated copy differently from the legacy source")
                universe = [lg["base"], dest_base]
                if _unfinished_view(root, dest, universe) != _unfinished_view(root, e, universe):
                    raise RuntimeError(f"discovery proof failed: the unfinished-run check sees run "
                                       f"{name} differently at {dest}")
                print(f"  proved    {name}: discovery finds it at {dest}; restore classification "
                      f"and unfinished status match the source")
            phase = "register"
            st.register()
            phase = "delete"
            _remove_entry(e)
            print(f"  deleted   {name}: legacy copy removed from the vault")
        except (OSError, RuntimeError, StateError, ValueError) as ex:
            failed.append(name)
            try:
                if stage.exists():
                    _remove_entry(stage)
            except OSError:
                pass
            if phase == "delete":
                # Proved and registered: the verified copy holds everything.
                # Never remove it -- it may be the only copy with every log.
                print(f"  FAILED    {name}: {ex} -- the delete of the legacy copy was interrupted; "
                      f"the verified copy at {dest} holds everything. Re-run "
                      f"--migrate-legacy-state --apply to finish the delete")
                continue
            note = ""
            if created_dest:
                # An unproven copy renamed into place by THIS invocation: leave
                # it and --restore sees the run in two locations and refuses
                # (the unfinished legacy run could no longer be undone).
                try:
                    _remove_entry(dest)
                    note = f"; unproven copy at {dest} removed"
                except OSError as rx:
                    note = (f"; could NOT remove the unproven copy at {dest} ({rx}) -- --restore of "
                            f"this run is refused ('more than one location') until that copy is "
                            f"moved aside")
            print(f"  FAILED    {name}: {ex} -- legacy source kept{note}")
    if failed:
        print(f"MIGRATION INCOMPLETE: {len(failed)} of {len(lg['entries'])} entr"
              f"{'y' if len(lg['entries']) == 1 else 'ies'} failed ({', '.join(failed)}); their "
              f"legacy copies and the manifest are kept. Fix the cause and re-run.")
        return 1
    try:
        if lg["base"].is_dir() and not any(lg["base"].iterdir()):
            lg["base"].rmdir()
        if lg["manifest"] is not None:
            lg["manifest"].unlink()  # last: only after every entry migrated
    except OSError as ex:
        print(f"MIGRATION INCOMPLETE: could not remove {ex.filename}: {ex}")
        return 1
    after = legacy_scan(root)
    print(f"LEGACY STATE: {after['total']} after migration"
          + (" -- zero" if after["total"] == 0 else " -- NOT zero"))
    return 0 if after["total"] == 0 else 1


def main() -> int:
    # allow_abbrev=False: the new flags must be spelled in full. The pre-alias-only
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
                    help="--alias-only manifest path (default <state dir>/manifest.json; must be "
                         "outside the vault).")
    ap.add_argument("--state-dir", type=Path, default=None, metavar="PATH",
                    help="--alias-only: operational state directory (default "
                         "%%LOCALAPPDATA%%\\WIKIllm\\alias-fix\\<vault-folder-name>-<hash8>; must be "
                         "outside the vault; registered in the default dir's state-dirs.json).")
    ap.add_argument("--migrate-legacy-state", action="store_true",
                    help="--alias-only: move legacy (pre-ADR-0005) in-vault state (.alias-fix-backup/, "
                         "_meta/alias-fix-manifest.json) to the state dir; dry run unless --apply.")
    ap.add_argument("--forget-state-dir", type=Path, default=None, metavar="PATH",
                    help="--alias-only: remove a registered state dir pointer, only after proving "
                         "it holds no unfinished run.")
    ap.add_argument("--rebind-state", type=Path, default=None, metavar="OLD_PATH",
                    help="--alias-only: attach the state of a MOVED vault's old path (which must "
                         "no longer exist) to this vault.")
    ap.add_argument("--break-lock", action="store_true",
                    help="--alias-only: remove this vault's run.lock ONLY when its holder is provably "
                         "gone (pid not running, or reused); standalone -- runs nothing else. On "
                         "Windows, --break-lock is safe alongside other fix_wikilinks runs: it judges "
                         "and deletes the same run.lock object through one exclusive handle, so it "
                         "cannot delete a live or replacement lock that it did not judge stale "
                         "(regression R170, ADR-0008). Off Windows, automatic --break-lock refuses; "
                         "remove run.lock manually only after confirming no fix_wikilinks run is "
                         "live.")
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
                or args.restore is not None or args.max_files is not None
                or args.state_dir is not None or args.migrate_legacy_state
                or args.forget_state_dir is not None or args.rebind_state is not None
                or args.break_lock):
            ap.error("--verify, --manifest, --batch-size, --max-files, --restore, --state-dir, "
                     "--migrate-legacy-state, --forget-state-dir, --rebind-state and --break-lock "
                     "require --alias-only")
        return run_backtick_locked(args, root)
    if args.break_lock and (args.apply or args.verify or args.restore is not None
                            or args.manifest is not None or args.batch_size is not None
                            or args.max_files is not None or args.state_dir is not None
                            or args.migrate_legacy_state or args.forget_state_dir is not None
                            or args.rebind_state is not None):
        ap.error("--break-lock is standalone: it combines with no other mode or option "
                 "(the run lock lives in the default state dir whatever --state-dir a run uses)")
    if args.verify and args.apply:
        ap.error("--verify and --apply are mutually exclusive")
    if args.restore is not None and (args.apply or args.verify):
        ap.error("--restore is mutually exclusive with --apply and --verify")
    upkeep = [n for n, v in (("--migrate-legacy-state", args.migrate_legacy_state),
                             ("--forget-state-dir", args.forget_state_dir is not None),
                             ("--rebind-state", args.rebind_state is not None)) if v]
    if len(upkeep) > 1:
        ap.error(f"{' and '.join(upkeep)} are mutually exclusive")
    if upkeep and (args.verify or args.restore is not None or args.manifest is not None
                   or args.batch_size is not None or args.max_files is not None):
        ap.error(f"{upkeep[0]} does not combine with --verify, --restore, --manifest, --batch-size "
                 f"or --max-files")
    if (args.forget_state_dir is not None or args.rebind_state is not None) and args.apply:
        ap.error(f"{upkeep[0]} acts at once; it takes no --apply")
    if args.batch_size is not None and args.batch_size < 1:
        ap.error("--batch-size must be >= 1")
    if args.max_files is not None and not args.apply:
        ap.error("--max-files requires --apply (a smoke batch of an existing manifest)")
    if args.max_files is not None and args.max_files < 1:
        ap.error("--max-files must be >= 1")
    return run_alias(args, root)


if __name__ == "__main__":
    sys.exit(main())
