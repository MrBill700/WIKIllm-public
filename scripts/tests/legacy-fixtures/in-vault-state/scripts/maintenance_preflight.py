#!/usr/bin/env python3
"""Read-only safety gate for an unattended /wiki-maintenance pass.

Answers exactly one question: is this vault safe to run maintenance against
right now? It runs the read-only scanners (lint.py, check_stale.py,
check_raw.py, sync_from_template.py dry run), parses their summary lines,
and reports blockers / warnings / a numeric baseline. It never writes a file,
never decides what to fix, and never launches anything but those scanners.

The whole safety model rests on one distinction: "the scanner reported zero"
versus "the scanner did not run, or its output did not parse". Missing or
unparseable data is a BLOCKER, never a zero. Fail closed everywhere.

Blockers (ready = false):
  - root is not a vault (no wiki/ or scripts/)
  - a required scanner file is missing, exits non-zero, or its output does
    not match the shape this script expects
  - _meta/raw-hashes.json is missing, unreadable, not JSON, or has no
    non-empty "files" mapping (check_raw.py re-initializes and WRITES the
    baseline in every one of those states, not just when the file is absent,
    so it is not invoked -- and the scanner counts as not run)
  - _meta/open-loops.md is missing (no parking destination for findings)

It also resolves the vault's **edit-deny set** (gate 1: may this file be
edited at all) and reports it. That is a separate question from whether a fix
is provable, and it never blocks: it is data the pass applies before it ranks
or proves anything. See edit_deny() below.

Warnings (ready stays true):
  - sync_from_template.py reports DRIFTED files, or could not be run
  - a page the edit-deny set covers could not be read, or a parking
    destination the pass must write to is itself edit-denied
  - no claim-audit cadence stamp installed (check_stale D: 0 tracked, or
    stamps tracked but none names the claim audit) -- unless section D
    reports "claim audit: EXEMPT" (a recorded "claim audit: exempt -- <reason>"
    line), which is a decision, not an accident (regression R79)
  - other recurring items DUE that are not the claim audit (findings only;
    triggers.claim_audit.due comes from the claim-audit stamp alone -- R84)
  - no question tracker found (see question_tracker below)
  - raw/ has changes pending ingest
  - check_stale B3 reports INVALID terminal *(unverifiable:)* labels, or
    section E reports MISSING SCAFFOLD files: each detail line is carried
    here verbatim (regression R109). Neither is a reason to refuse a pass; both
    are a reason to say so out loud. Absent sections (an older check_stale)
    parse as before -- no new required key.

Run: python scripts/maintenance_preflight.py [--json] [--root PATH]

--json prints one JSON object (the contract the skill consumes); without it
a human summary of the same data prints. --root overrides the __file__-
derived vault root: the scanners derive their root from __file__ or cwd, so
a template copy run from a vault directory would otherwise scan the template's
empty wiki/ and pass having examined nothing (the regression R67 gap).

Exit status: 0 when ready, 1 when blocked. The JSON prints either way.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Scanners whose output the gate REQUIRES. Each runs with --summary from the
# vault root; a missing file, non-zero exit, or unparseable output blocks.
REQUIRED_SCANNERS = ["lint.py", "check_stale.py", "check_raw.py"]
SYNC_SCRIPT = "sync_from_template.py"
SCANNER_TIMEOUT = 600  # seconds; suggest_anchors' full anchor check is the slow part

PARK_TO = "_meta/open-loops.md"
# Question-tracker candidates, in preference order. Fleet reality (2026-09):
# open-loops.md exists in 9/9 vaults, open-questions.md in 1/9, for-reviewer.md
# in 1/9 -- so the tracker is discovered per vault, never assumed.
QUESTION_TRACKER_CANDIDATES = ["_meta/open-questions.md", "_meta/for-reviewer.md"]

# --- output-shape regexes -------------------------------------------------
# Anchored on the stable "=== <token> ===" prefix and the trailing numbers,
# never on the prose between (some headers carry a real em-dash in the
# source; subprocess output is decoded utf-8 with errors="replace").
LINT_RE = {
    "all_files": re.compile(r"^=== ALL FILES === (\d+)\s*$", re.M),
    "broken_links": re.compile(r"^=== BROKEN LINKS === (\d+) \((\d+) distinct targets\)", re.M),
    "cross_vault_links": re.compile(r"^=== CROSS-VAULT LINKS[^\n]*=== (\d+)\s*$", re.M),
    "orphans": re.compile(r"^=== ORPHANS[^\n]*=== (\d+)\s*$", re.M),
    "ghost_links": re.compile(r"^=== GHOST/PLACEHOLDER LINKS === (\d+)\s*$", re.M),
}
# OPTIONAL (regression R76): read when present, never required -- a vault whose
# lint.py predates the section must not be blocked by its absence.
LINT_RELDEPTH_RE = re.compile(r"^=== RELATIVE-DEPTH LINKS[^\n]*=== (\d+)\b", re.M)
# OPTIONAL (regression R151): links that resolve only via frontmatter aliases
# (grey in Obsidian). Absent on an older lint -> both keys are None (null in
# --json), never a blocker. Unlike relative_depth_links the keys are always
# present, so a report can tell "not measured" from "zero".
LINT_ALIAS_ONLY_RE = re.compile(
    r"^=== ALIAS-ONLY LINKS[^\n]*=== (\d+) \((\d+) distinct targets\)", re.M)
LOCATOR_RE = re.compile(
    r"^=== LOCATOR HYGIENE === coverage (\d+)/(\d+) \((\d+)%, target (\d+)%\)"
    r" \| broken heading anchors (\d+) \| suspect (\d+)", re.M)
LOCATOR_NA_RE = re.compile(
    r"^=== LOCATOR HYGIENE === no source-citing claims[^\n]*"
    r"\| broken heading anchors (\d+) \| suspect (\d+)", re.M)
STALE_RE = {
    "A": re.compile(r"^=== A\.[^\n]*=== (\d+) open / (\d+) overdue", re.M),
    "B": re.compile(r"^=== B\.[^\n]*=== (\d+) hits in (\d+) files", re.M),
    "B2": re.compile(r"^=== B2\.[^\n]*=== (\d+) hits\s*$", re.M),
    "C": re.compile(r"^=== C\.[^\n]*=== (\d+)\s*$", re.M),
    "D": re.compile(r"^=== D\.[^\n]*=== (\d+) tracked / (\d+) DUE", re.M),
    # OPTIONAL (regression R109): B3 and E arrived with regression R57/R66; an older
    # check_stale prints neither, and their absence must never block. B3 is
    # deliberately NOT anchored at end-of-line: regression R99 appends
    # ` / K UNLOGGED` after INVALID on the same header, and the prefix must
    # keep matching with or without that suffix.
    "B3": re.compile(r"^=== B3\.[^\n]*=== (\d+) valid / (\d+) INVALID", re.M),
    "E": re.compile(r"^=== E\.[^\n]*=== (\d+) required / (\d+) missing", re.M),
}
# The actionable detail lines under B3 / E, printed under --summary too. Both
# shapes are distinct from every neighbour: section F prints
# `  <page>  INVALID transcript_kind: ...` (no `:<line>`, no quoted slug) and
# sync_from_template prints a bare `MISSING : ` (no SCAFFOLD). The page path is
# `(.+?)`, never `\S+`: vault paths carry spaces (`wiki/Some Page.md` in a
# real vault) and the `  INVALID: '` token is anchor enough.
STALE_B3_DETAIL_RE = re.compile(r"^  (.+?):(\d+)  INVALID: '([^\n]*)'\s*$", re.M)
STALE_E_DETAIL_RE = re.compile(r"^  MISSING SCAFFOLD : (.+?)\s*$", re.M)
# check_stale's section D closing status line (regression R79). Absent on an older
# check_stale: treated as unknown, never as exempt.
STALE_CLAIM_AUDIT_RE = re.compile(r"^  claim audit: (installed|NOT INSTALLED|EXEMPT|CONFLICT)\b([^\n]*)", re.M)
# check_stale's section F header (regression R45). OPTIONAL: an older check_stale
# has no section F, and its absence must never block the gate.
STALE_TRANSCRIPT_RE = re.compile(
    r"^=== F\.[^\n]*=== (\d+) transcript pages / (\d+) missing / (\d+) invalid / (\d+) legacy", re.M)
RAW_FILES_RE = re.compile(r"^Files in raw/:\s+(\d+)\s*$", re.M)
RAW_SUMMARY_RE = re.compile(
    r"^Summary: (\d+) added, (\d+) modified, (\d+) removed, (\d+) unchanged", re.M)
RAW_CLEAN_RE = re.compile(r"^No changes since last baseline\.", re.M)
SYNC_INSYNC_RE = re.compile(r"^in sync : (\d+)\s*$", re.M)
SYNC_DRIFTED_RE = re.compile(r"^DRIFTED : (.+?)\s*$", re.M)
SYNC_MISSING_RE = re.compile(r"^MISSING : (.+?)\s*$", re.M)
SYNC_IS_TEMPLATE_RE = re.compile(r"^This IS the template", re.M)


def run_scanner(root: Path, name: str, args: list[str]) -> tuple[int | None, str, str]:
    """Run scripts/<name> from the vault root; (returncode, stdout, stderr).

    returncode None = the process could not be started or timed out (the
    failure text is in stderr). Output is read whole -- never piped through
    head/tail, which kills these scripts mid-print. Decoded as utf-8 with
    replacement so a stray em-dash can never turn a real result into a crash.
    """
    script = root / "scripts" / name
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # This gate is read-only, and these vaults live in Dropbox: a scanner that
    # imports a sibling module would otherwise drop scripts/__pycache__/*.pyc
    # into the vault and sync it to every device for nothing.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run([sys.executable, "-B", str(script), *args], cwd=str(root),
                              capture_output=True, timeout=SCANNER_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {SCANNER_TIMEOUT}s"
    except OSError as exc:
        return None, "", f"could not start: {exc}"
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    return proc.returncode, out, err


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:160]
    return ""


def parse_lint(out: str) -> tuple[dict, str | None]:
    """Counts from lint --summary, or (partial, reason) when a line is missing."""
    # Optional R151 keys first, so they are present even on a partial parse.
    counts: dict = {"alias_only_links": None, "alias_only_link_targets": None}
    for key, rx in LINT_RE.items():
        m = rx.search(out)
        if not m:
            return counts, f"lint output lacks the {key.replace('_', ' ').upper()} line"
        if key == "broken_links":
            counts["broken_links"] = int(m[1])
            counts["broken_link_targets"] = int(m[2])
        else:
            counts[key] = int(m[1])
    m = LINT_RELDEPTH_RE.search(out)
    if m:
        counts["relative_depth_links"] = int(m[1])
    m = LINT_ALIAS_ONLY_RE.search(out)
    counts["alias_only_links"] = int(m[1]) if m else None
    counts["alias_only_link_targets"] = int(m[2]) if m else None
    m = LOCATOR_RE.search(out)
    if m:
        counts.update(anchor_covered=int(m[1]), anchor_eligible=int(m[2]),
                      anchor_coverage_pct=int(m[3]), anchor_target_pct=int(m[4]),
                      broken_anchors=int(m[5]), suspect_anchors=int(m[6]))
        return counts, None
    m = LOCATOR_NA_RE.search(out)
    if m:
        # 0 eligible claims is a real measurement (this vault cites sources
        # another way), distinct from the "skipped" case below.
        counts.update(anchor_covered=0, anchor_eligible=0, anchor_coverage_pct=None,
                      anchor_target_pct=None, broken_anchors=int(m[1]),
                      suspect_anchors=int(m[2]))
        return counts, None
    if "=== LOCATOR HYGIENE === skipped" in out:
        return counts, "lint LOCATOR HYGIENE was skipped (anchor stack import failed)"
    return counts, "lint output lacks a parseable LOCATOR HYGIENE line"


def parse_stale(out: str) -> tuple[dict, str | None]:
    counts: dict = {}
    m = STALE_RE["A"].search(out)
    if not m:
        return counts, "check_stale output lacks the section A (open-loops) counts"
    counts["open_loops_open"], counts["open_loops_overdue"] = int(m[1]), int(m[2])
    m = STALE_RE["B"].search(out)
    if not m:
        return counts, "check_stale output lacks the section B (markers) counts"
    counts["markers_content_hits"], counts["markers_content_files"] = int(m[1]), int(m[2])
    m = STALE_RE["B2"].search(out)
    if not m:
        return counts, "check_stale output lacks the section B2 (tracker markers) count"
    counts["markers_tracker_hits"] = int(m[1])
    m = STALE_RE["C"].search(out)
    if not m:
        return counts, "check_stale output lacks the section C (central-page age) count"
    counts["central_pages_stale"] = int(m[1])
    m = STALE_RE["D"].search(out)
    if not m:
        return counts, "check_stale output lacks the section D (recurring items) counts"
    counts["recurring_tracked"], counts["recurring_due"] = int(m[1]), int(m[2])
    m = STALE_CLAIM_AUDIT_RE.search(out)
    if m:
        counts["claim_audit_status"] = m[1]
        # strip the leading dash and the trailing "  [file:line]" locator
        counts["claim_audit_status_detail"] = re.sub(r"\s*\[[^\]]+\]\s*$", "",
                                                    m[2].strip(" —-"))
    m = STALE_TRANSCRIPT_RE.search(out)
    if m:  # optional -- see STALE_TRANSCRIPT_RE
        (counts["transcript_pages"], counts["transcript_kind_missing"],
         counts["transcript_kind_invalid"], counts["transcript_kind_legacy"]) = (
            int(m[1]), int(m[2]), int(m[3]), int(m[4]))
    m = STALE_RE["B3"].search(out)
    if m:  # optional -- see STALE_RE
        counts["terminal_labels_valid"], counts["terminal_labels_invalid"] = int(m[1]), int(m[2])
    m = STALE_RE["E"].search(out)
    if m:  # optional -- see STALE_RE
        counts["scaffold_required"], counts["scaffold_missing"] = int(m[1]), int(m[2])
    return counts, None


def stale_detail_lines(out: str) -> dict:
    """The actionable B3 / E detail lines from check_stale --summary (regression R109).

    Kept out of the counts dict on purpose: the baseline is numbers, and these
    are the lines a pass must repeat verbatim (as warnings) so the gate can
    never again summarise `2 INVALID` into a report that looks clean.
    """
    return {
        "invalid_labels": [f"{m[1]}:{m[2]} '{m[3]}'" for m in STALE_B3_DETAIL_RE.finditer(out)],
        "missing_scaffold": [m[1] for m in STALE_E_DETAIL_RE.finditer(out)],
    }


def parse_raw(out: str) -> tuple[dict, str | None]:
    counts: dict = {}
    if "No baseline yet" in out:
        # Should be unreachable (the baseline is checked before invoking), but
        # if it ever prints, a file was written: report it, never bless it.
        return counts, "check_raw initialized a baseline (wrote _meta/raw-hashes.json)"
    m = RAW_FILES_RE.search(out)
    if not m:
        return counts, "check_raw output lacks the 'Files in raw/' line"
    counts["raw_files"] = int(m[1])
    m = RAW_SUMMARY_RE.search(out)
    if m:
        counts.update(raw_added=int(m[1]), raw_modified=int(m[2]),
                      raw_removed=int(m[3]), raw_unchanged=int(m[4]))
        return counts, None
    if RAW_CLEAN_RE.search(out):
        counts.update(raw_added=0, raw_modified=0, raw_removed=0,
                      raw_unchanged=counts["raw_files"])
        return counts, None
    return counts, "check_raw output has neither a Summary line nor 'No changes since last baseline'"


def parse_sync(out: str) -> tuple[dict, str | None]:
    if SYNC_IS_TEMPLATE_RE.search(out):
        return {"status": "is_template", "in_sync": None, "drifted": [], "missing": []}, None
    m = SYNC_INSYNC_RE.search(out)
    if not m:
        return {}, "sync output lacks the 'in sync : N' line"
    drifted = SYNC_DRIFTED_RE.findall(out)
    missing = SYNC_MISSING_RE.findall(out)
    status = "drifted" if (drifted or missing) else "in_sync"
    return {"status": status, "in_sync": int(m[1]), "drifted": drifted, "missing": missing}, None


def raw_baseline_problem(root: Path) -> str | None:
    """Why check_raw.py must NOT be invoked against this vault, or None.

    check_raw.py calls save_baseline whenever the loaded payload's "files"
    mapping is empty -- which covers a missing file, an empty {} payload, a
    payload without "files", and a vault bootstrapped with an empty raw/
    ({"files": {}}). A read-only gate has to reproduce that predicate, not
    merely test for the file's existence. Malformed JSON makes check_raw
    crash rather than write, but it is still not a usable baseline.
    """
    path = root / "_meta" / "raw-hashes.json"
    if not path.is_file():
        return "no _meta/raw-hashes.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return f"_meta/raw-hashes.json is unreadable ({exc.__class__.__name__})"
    except json.JSONDecodeError as exc:
        return f"_meta/raw-hashes.json is not valid JSON ({exc.msg} at line {exc.lineno})"
    if not isinstance(payload, dict):
        return "_meta/raw-hashes.json is not a JSON object"
    files = payload.get("files")
    if not isinstance(files, dict):
        return "_meta/raw-hashes.json has no 'files' mapping"
    if not files:
        return "_meta/raw-hashes.json 'files' mapping is empty"
    return None


def mentioned_in_claude_md(root: Path, rel: str) -> bool:
    """True when the vault's CLAUDE.md names the file (path or bare basename --
    a directory-tree listing names it as `open-questions.md` alone)."""
    claude = root / "CLAUDE.md"
    if not claude.is_file():
        return False
    try:
        text = claude.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return rel in text or Path(rel).name in text

# Section D of check_stale prints one row per "Last run / Next due" stamp and
# a TOTAL due count -- it does not say WHICH stamp is due, and the printed
# label is only the text before the first colon ("Quarterly"), so two rows
# can share it. The claim-audit trigger therefore has to find its own stamp:
# same file set and same stamp regexes as check_stale.recurring_due, but
# restricted to lines that name the claim audit. (regression R84: the trigger
# used to fire on ANY due recurring item -- a treadmill retest nearly
# launched a ~30-agent audit.)
STAMP_ROOTS = ("wiki", "_meta")
STAMP_SKIP_SUBSTR = ("raw-clips", "/extracted/")   # mirrors check_stale.SKIP_SUBSTR
NEXT_DUE_RE = re.compile(r"Next due:\s*~?(\d{4})-(\d{2})")
LAST_RUN_RE = re.compile(r"Last run:\s*(\d{4}-\d{2}-\d{2}|never)", re.I)
CLAIM_AUDIT_TOKEN_RE = re.compile(r"claim[ -]audit|audit_claims", re.I)
LIST_ITEM_RE = re.compile(r"^\s*(>\s*)*([-*+]|\d+[.)])\s")   # allows a callout/quote prefix
BLOCK_BOUNDARY_RE = re.compile(r"^\s*$|^\s*#")


def stamp_block(lines: list[str], i: int) -> str:
    """The list item a stamp line belongs to: from the nearest preceding
    list-marker line through line i, stopping at a blank/heading boundary.
    A stamp that is not inside a list item (table row, prose) is judged on
    its own line only. The template's own install block (_meta/open-loops.md)
    puts 'Quarterly: claim audit' three lines above its stamp, so matching
    the token against the stamp line alone misses the documented install path."""
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


def claim_audit_stamps(root: Path, today: dt.date | None = None) -> list[dict]:
    """Every 'Next due' stamp under wiki/ and _meta/ (log.md excluded) whose
    list item names the claim audit. Each entry: file, line, next_due
    (YYYY-MM), last_run, due (first of next_due month <= today). File set and
    read semantics mirror check_stale (plain utf-8; an unreadable file is
    skipped there too) so section D and this scan see the same stamps."""
    today = today or dt.date.today()
    out: list[dict] = []
    for r in STAMP_ROOTS:
        base = root / r
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.md")):
            rel = p.relative_to(root).as_posix()
            if rel.endswith("log.md") or any(sub in rel for sub in STAMP_SKIP_SUBSTR):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            lines = text.splitlines()
            for idx, line in enumerate(lines):
                n = idx + 1
                m = NEXT_DUE_RE.search(line)
                if not m or not CLAIM_AUDIT_TOKEN_RE.search(stamp_block(lines, idx)):
                    continue
                try:
                    due_date = dt.date(int(m[1]), int(m[2]), 1)
                except ValueError:
                    continue
                lr = LAST_RUN_RE.search(line)
                out.append({"file": rel, "line": n, "next_due": f"{m[1]}-{m[2]}",
                            "last_run": lr[1] if lr else None,
                            "due": due_date <= today})
    return out


# --- gate 1: may this file be edited at all (regression R81) ------------------
# Separate from `machine-verifiable`, which answers only "can this edit be
# proven". A file in the deny set is off limits however well a fix is proven:
# wiki/log.md is the vault's only durable record and these vaults are non-git
# Dropbox folders, so an edit to history is unrecoverable in the medium the
# history lives in. Evidence the separation is load-bearing (sample-vault-e,
# 2026-09-17): 166 wrong-depth relative links inside wiki/log.md, every one of
# them an exact span with a unique replacement and a scanner-verifiable
# postcondition -- and the correct answer was still to leave them latent.
EDIT_DENY_PATHS = ["wiki/log.md"]
EDIT_DENY_FLAG = "append_only"
EDIT_DENY_ROOTS = ("wiki", "_meta")
FM_HEAD_CHARS = 4096


def fm_flag_true(path: Path, key: str) -> bool | None:
    """True when `path`'s frontmatter carries `key: true`; None if unreadable.

    Mirrors _wikilib.fm_flag, deliberately inlined rather than imported: this
    gate imports no sibling module, so it still runs when the rest of the
    vault's scripts are broken, and importing one would drop
    scripts/__pycache__/*.pyc into a Dropbox vault (regression R83) -- which a
    script whose contract is "never writes a file" must not do.

    Reads a bounded head (wiki/log.md runs to hundreds of KB) but falls back
    to the whole file when the closing fence is not inside that head: a
    frontmatter split by the slice must never read as "flag absent", which
    would be a silent zero inside the gate that exists to prevent them.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            text = fh.read(FM_HEAD_CHARS)
            if text.startswith("---") and "\n---" not in text:
                text += fh.read()
    except OSError:
        return None
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    needle = key.lower() + ":true"
    return any(line.lower().replace(" ", "").startswith(needle)
               for line in text[:end].splitlines())


def edit_deny(root: Path) -> dict:
    """The files this vault forbids editing at all, and by which rule.

    Two rules, both read-only:

    PATH -- `wiki/log.md` whenever it exists. Not optional and not replaceable
    by the marker below: no vault carries a machine-readable flag today, and
    `wiki/` is not in sync_from_template's SYNC_SET, so no vault will ever
    receive one by sync. A marker-only deny set would protect nothing.

    FRONTMATTER -- any page under wiki/ or _meta/ carrying `append_only: true`,
    the same mechanism as `tracker_page`, so a vault designates its own history
    files without a template change.

    Unreadable pages FAIL CLOSED. A page whose frontmatter could not be read
    has an UNKNOWN `append_only` state, not a false one, so it is denied: it
    appears in `unreadable` AND in `paths`. The invariant callers may rely on
    is `paths == by_rule["path"] + by_rule["frontmatter"] + unreadable` --
    `paths` is the whole gate, and nothing is denied that is not in it.

    Never returns a bare empty set: `note` says in words when nothing resolved,
    because an empty section reads as protection that is not there -- the same
    `0 tracked`-reads-as-health failure as regression R79.
    """
    by_rule: dict = {"path": [], "frontmatter": []}
    unreadable: list[str] = []
    for rel in EDIT_DENY_PATHS:
        if (root / rel).is_file():
            by_rule["path"].append(rel)
    for r in EDIT_DENY_ROOTS:
        base = root / r
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.md")):
            rel = p.relative_to(root).as_posix()
            if rel in by_rule["path"]:
                continue
            flagged = fm_flag_true(p, EDIT_DENY_FLAG)
            if flagged is None:
                unreadable.append(rel)
            elif flagged:
                by_rule["frontmatter"].append(rel)
    paths = by_rule["path"] + by_rule["frontmatter"] + unreadable
    if paths:
        note = (f"{len(paths)} file(s) may not be edited by this pass "
                f"({len(by_rule['path'])} by path rule, "
                f"{len(by_rule['frontmatter'])} by `{EDIT_DENY_FLAG}: true` frontmatter, "
                f"{len(unreadable)} denied for being unreadable -- unknown is not false). "
                "A finding in one of them is parked with its count, however well proven.")
    else:
        note = ("none resolved -- this vault designates no append-only file "
                f"(no {', '.join(EDIT_DENY_PATHS)}, and no `{EDIT_DENY_FLAG}: true` "
                f"frontmatter under {', '.join(r + '/' for r in EDIT_DENY_ROOTS)}). "
                "Nothing is protected here; that is a measurement, not a guarantee.")
    return {"paths": paths, "by_rule": by_rule, "unreadable": unreadable, "note": note}


def preflight(root: Path) -> dict:
    """Assemble the report for `root`. Read-only; never raises on vault state."""
    root = root.resolve()
    blockers: list[str] = []
    warnings: list[str] = []
    scanners: dict = {}
    baseline: dict = {}
    stale_details: dict = {"invalid_labels": [], "missing_scaffold": []}
    template_sync: dict = {"status": "not_run", "in_sync": None, "drifted": [], "missing": []}

    if not (root / "wiki").is_dir() or not (root / "scripts").is_dir():
        blockers.append(f"{root} is not a vault (needs wiki/ and scripts/)")
        for name in REQUIRED_SCANNERS:
            scanners[name] = "not run: root is not a vault"
    else:
        # --- required scanners ---
        parsers = {"lint.py": parse_lint, "check_stale.py": parse_stale,
                   "check_raw.py": parse_raw}
        for name in REQUIRED_SCANNERS:
            if not (root / "scripts" / name).is_file():
                scanners[name] = "missing"
                blockers.append(f"required scanner scripts/{name} is missing")
                continue
            if name == "check_raw.py":
                # check_raw WRITES the baseline whenever it loads an empty
                # "files" mapping -- absent file, {}, or {"files": {}} from a
                # bootstrap with an empty raw/. A read-only gate must not
                # trigger that, so the scanner is not invoked at all.
                why = raw_baseline_problem(root)
                if why:
                    scanners[name] = f"not run: {why} (check_raw would rewrite the baseline)"
                    blockers.append(f"unusable raw/ baseline: {why} -- check_raw.py not run "
                                    "because it would (re)write _meta/raw-hashes.json; "
                                    "run `python scripts/check_raw.py --init` deliberately "
                                    "before maintenance")
                    continue
            rc, out, err = run_scanner(root, name, ["--summary"])
            if rc is None:
                scanners[name] = f"failed: {err}"
                blockers.append(f"scripts/{name} did not complete ({err})")
                continue
            if rc != 0:
                detail = _first_line(err) or _first_line(out) or "no output"
                scanners[name] = f"exit {rc}: {detail}"
                blockers.append(f"scripts/{name} exited {rc} ({detail})")
                continue
            counts, problem = parsers[name](out)
            if problem:
                scanners[name] = f"unparseable: {problem}"
                blockers.append(f"scripts/{name} output did not parse -- {problem}; "
                                "its counts are UNKNOWN, not zero")
                continue
            scanners[name] = "ok"
            baseline.update(counts)
            if name == "check_stale.py":
                stale_details = stale_detail_lines(out)

        # --- check_stale B3 / E detail lines (regression R109): warnings, never
        # blockers. Only when the section was printed at all (key present);
        # an older check_stale without it is silence, not a zero.
        n_inv = baseline.get("terminal_labels_invalid")
        if n_inv is not None:
            for detail in stale_details["invalid_labels"]:
                warnings.append(f"INVALID terminal label (check_stale B3): {detail} -- not in "
                                "the closed *(unverifiable: <slug>)* vocabulary; fix the slug "
                                "(marker hygiene, _meta/fleet-conventions.md)")
            if len(stale_details["invalid_labels"]) != n_inv:
                warnings.append(f"check_stale B3 counts {n_inv} INVALID terminal label(s) but "
                                f"{len(stale_details['invalid_labels'])} detail line(s) parsed "
                                "-- read the scanner output directly")
        n_gap = baseline.get("scaffold_missing")
        if n_gap is not None:
            for rel in stale_details["missing_scaffold"]:
                warnings.append(f"MISSING SCAFFOLD (check_stale E): {rel} -- instance-owned, "
                                "never synced; bootstrap it by hand from the template")
            if len(stale_details["missing_scaffold"]) != n_gap:
                warnings.append(f"check_stale E counts {n_gap} missing scaffold file(s) but "
                                f"{len(stale_details['missing_scaffold'])} detail line(s) "
                                "parsed -- read the scanner output directly")

        # --- template drift (advisory) ---
        if not (root / "scripts" / SYNC_SCRIPT).is_file():
            template_sync["status"] = "unavailable"
            warnings.append(f"scripts/{SYNC_SCRIPT} is missing -- template drift unknown")
        else:
            rc, out, err = run_scanner(root, SYNC_SCRIPT, [])
            if rc != 0:
                detail = (_first_line(err) if rc is None else
                          _first_line(err) or _first_line(out) or "no output")
                template_sync["status"] = "unknown"
                template_sync["error"] = detail
                warnings.append(f"template drift unknown: {SYNC_SCRIPT} "
                                f"{'did not complete' if rc is None else f'exited {rc}'} ({detail})")
            else:
                parsed, problem = parse_sync(out)
                if problem:
                    template_sync["status"] = "unknown"
                    template_sync["error"] = problem
                    warnings.append(f"template drift unknown: {problem}")
                else:
                    template_sync = parsed
                    if parsed["status"] == "drifted":
                        warnings.append("template drift: "
                                        f"{len(parsed['drifted'])} DRIFTED, "
                                        f"{len(parsed['missing'])} MISSING "
                                        "(see sync_from_template.py)")

    # --- parking destinations (never created here) ---
    park_to = None
    if (root / PARK_TO).is_file():
        park_to = str(root / PARK_TO)
    else:
        blockers.append(f"no parking destination: {PARK_TO} does not exist "
                        "(create it via the close-out protocol; this gate never creates files)")
    question_tracker = None
    for rel in QUESTION_TRACKER_CANDIDATES:
        if (root / rel).is_file() and mentioned_in_claude_md(root, rel):
            question_tracker = str(root / rel)
            break
    if question_tracker is None:
        warnings.append("no question tracker: none of "
                        f"{', '.join(QUESTION_TRACKER_CANDIDATES)} both exists and is "
                        "named in CLAUDE.md -- questions for the humans have no home here")

    # --- gate 1: the edit-deny set (advisory data, never a blocker) ---
    deny = edit_deny(root)
    if deny["unreadable"]:
        warnings.append(f"edit-deny gate could not read {len(deny['unreadable'])} page(s) "
                        f"({', '.join(deny['unreadable'][:3])}"
                        f"{' ...' if len(deny['unreadable']) > 3 else ''}) -- their "
                        f"`{EDIT_DENY_FLAG}` state is UNKNOWN, not false, so they are denied "
                        "(they are in edit_deny.paths); fix the read before running unattended")
    # The pass WRITES parked items and questions into these two, so a vault
    # that marks one append-only has denied its own parking destination.
    destinations = [(PARK_TO, "parking destination")]
    if question_tracker:
        destinations.append((Path(question_tracker).relative_to(root).as_posix(),
                             "question tracker"))
    marked = deny["by_rule"]["path"] + deny["by_rule"]["frontmatter"]
    for rel, label in destinations:
        if rel in marked:
            warnings.append(f"the {label} {rel} is itself in the edit-deny set -- this pass "
                            "must write to it; clear its `append_only: true` or nominate "
                            "another file before running unattended")
        elif rel in deny["unreadable"]:
            warnings.append(f"the {label} {rel} could not be read, so the edit-deny gate "
                            "denies it -- this pass must write to it; fix the read before "
                            "running unattended")

    # --- triggers ---
    # due_count is section D's TOTAL (every recurring stamp); `due` is decided
    # only by the stamp line(s) that name the claim audit. Other due items are
    # reported in recurring_due_other + a warning: findings, not a trigger.
    claim_audit: dict = {"due": False, "tracked": None, "due_count": None,
                         "stamp": None, "recurring_due_other": None, "exempt": None,
                         "note": None}
    if "recurring_tracked" in baseline:
        tracked, due_count = baseline["recurring_tracked"], baseline["recurring_due"]
        claim_audit.update(tracked=tracked, due_count=due_count)
        status = baseline.get("claim_audit_status")
        if status == "CONFLICT":
            warnings.append("claim audit: exemption line AND an installed stamp coexist "
                            f"({baseline.get('claim_audit_status_detail')}) -- resolve before "
                            "trusting due=false")
        if status == "EXEMPT":
            # A recorded decision, not an accident: no cadence warning. due
            # stays false; other due items are still findings.
            claim_audit["exempt"] = baseline.get("claim_audit_status_detail") or "exempt"
            claim_audit["note"] = f"claim audit exempt in this vault: {claim_audit['exempt']}"
            claim_audit["recurring_due_other"] = due_count
            # An exemption is a claim, not a measurement. The legitimate
            # criterion (no source-citing claims) is something lint already
            # counts -- so check it, or one line buys permanent silence.
            if baseline.get("anchor_eligible"):
                warnings.append(f"claim audit declared EXEMPT but lint counts "
                                f"{baseline['anchor_eligible']} source-citing claim(s) -- "
                                "the exemption's stated criterion does not hold; "
                                "install the cadence or justify the exemption")
            if due_count > 0:
                warnings.append(f"{due_count} recurring item(s) DUE in check_stale section D "
                                "(claim audit exempt here) -- report them as findings")
        elif tracked == 0:
            # No stamp installed is NOT "nothing due" -- it is "never scheduled".
            claim_audit["note"] = ("no 'Last run / Next due' cadence stamp installed -- "
                                   "the claim audit has never been scheduled in this vault")
            warnings.append("claim-audit cadence not installed (check_stale D: 0 tracked) "
                            "-- due=false means unscheduled, not up to date")
        else:
            stamps = claim_audit_stamps(root)
            due_stamps = [st for st in stamps if st["due"]]
            if not stamps:
                claim_audit["note"] = (f"{tracked} recurring stamp(s) tracked but none names "
                                       "the claim audit (no 'claim audit' / 'audit_claims' on "
                                       "a 'Next due' line) -- never scheduled here")
                warnings.append("claim-audit cadence not installed: check_stale D tracks "
                                f"{tracked} stamp(s), none is the claim audit -- due=false "
                                "means unscheduled, not up to date")
                other = due_count
            else:
                claim_audit["due"] = bool(due_stamps)
                # the row a launcher must re-read before launching: the earliest
                # due one, else the earliest scheduled
                pick = min(due_stamps or stamps, key=lambda st: (st["next_due"], st["file"], st["line"]))
                claim_audit["stamp"] = f"{pick['file']}:{pick['line']} next ~{pick['next_due']}"
                other = due_count - len(due_stamps)
            if other < 0:
                # Only possible when this scan counts due claim-audit stamps
                # that section D did not: the two scans read different content.
                # Unreachable by construction today (same file set, same read
                # semantics, same regexes) and therefore untested -- kept as a
                # fail-loud assertion, never hidden behind a clean 0.
                warnings.append(f"preflight found {len(due_stamps)} due claim-audit stamp(s) "
                                f"but check_stale section D counts only {due_count} due -- "
                                "the two scans disagree; verify the stamp before launching")
            claim_audit["recurring_due_other"] = other
            if other > 0:
                warnings.append(f"{claim_audit['recurring_due_other']} other recurring item(s) "
                                "DUE in check_stale section D (not the claim audit) -- "
                                "report them as findings, do not launch an audit for them")
    else:
        claim_audit["note"] = "check_stale did not report section D; due state unknown"
    if "raw_added" in baseline:
        pending = baseline["raw_added"] + baseline["raw_modified"] + baseline["raw_removed"]
        if pending:
            warnings.append(f"raw/ has {pending} change(s) pending ingest "
                            f"({baseline['raw_added']} added, {baseline['raw_modified']} "
                            f"modified, {baseline['raw_removed']} removed)")

    anchor_backfill: dict = {"due": False, "coverage_pct": None, "target_pct": None,
                             "eligible": None}
    if "anchor_eligible" in baseline:
        eligible, covered = baseline["anchor_eligible"], baseline["anchor_covered"]
        pct, target = baseline["anchor_coverage_pct"], baseline["anchor_target_pct"]
        anchor_backfill.update(coverage_pct=pct, target_pct=target, eligible=eligible)
        # Decide on the raw counts, not the printed percentage: lint rounds
        # with {pct:.0f}, so 119/200 prints as 60% and would read as on-target
        # while lint's own detail branch (float compare) calls it under.
        # covered/eligible < target/100  <=>  covered*100 < target*eligible.
        anchor_backfill["due"] = bool(eligible > 0 and target is not None
                                      and covered * 100 < target * eligible)

    return {
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "root": str(root),
        "scanners": scanners,
        "park_to": park_to,
        "question_tracker": question_tracker,
        "edit_deny": deny,
        "triggers": {"claim_audit": claim_audit, "anchor_backfill": anchor_backfill},
        "baseline": baseline,
        "template_sync": template_sync,
    }


def print_human(rep: dict) -> None:
    print(f"vault : {rep['root']}")
    print(f"READY : {'yes' if rep['ready'] else 'NO'}")
    print()
    print(f"=== BLOCKERS === {len(rep['blockers'])}")
    for b in rep["blockers"]:
        print(f"  ! {b}")
    print()
    print(f"=== WARNINGS === {len(rep['warnings'])}")
    for w in rep["warnings"]:
        print(f"  - {w}")
    print()
    print("=== SCANNERS ===")
    for name, status in rep["scanners"].items():
        print(f"  {name:<16} {status}")
    print()
    print("=== PARKING ===")
    print(f"  park_to          {rep['park_to'] or '(none -- blocker)'}")
    print(f"  question_tracker {rep['question_tracker'] or '(none)'}")
    print()
    ed = rep["edit_deny"]
    print(f"=== EDIT DENY === {len(ed['paths'])}")
    for rel in ed["by_rule"]["path"]:
        print(f"  {rel:<44} path rule")
    for rel in ed["by_rule"]["frontmatter"]:
        print(f"  {rel:<44} {EDIT_DENY_FLAG}: true")
    for rel in ed["unreadable"]:
        print(f"  {rel:<44} UNREADABLE -- denied (unknown is not false)")
    print(f"  note: {ed['note']}")
    print()
    ca, ab = rep["triggers"]["claim_audit"], rep["triggers"]["anchor_backfill"]
    print("=== TRIGGERS ===")
    print(f"  claim_audit      due={ca['due']}  tracked={ca['tracked']}  due_count={ca['due_count']}"
          f"  other_due={ca['recurring_due_other']}"
          + (f"  exempt={ca['exempt']!r}" if ca.get("exempt") else ""))
    if ca["stamp"]:
        print(f"                   stamp: {ca['stamp']}")
    if ca["note"]:
        print(f"                   note: {ca['note']}")
    print(f"  anchor_backfill  due={ab['due']}  coverage={ab['coverage_pct']}%  "
          f"target={ab['target_pct']}%  eligible={ab['eligible']}")
    print()
    ts = rep["template_sync"]
    print(f"=== TEMPLATE SYNC === {ts['status']}"
          + (f" (in sync {ts['in_sync']})" if ts.get("in_sync") is not None else "")
          + (f" -- {ts['error']}" if ts.get("error") else ""))
    for rel in ts.get("drifted", []):
        print(f"  DRIFTED : {rel}")
    for rel in ts.get("missing", []):
        print(f"  MISSING : {rel}")
    print()
    print("=== BASELINE ===")
    if not rep["baseline"]:
        print("  (empty -- no scanner produced parseable counts)")
    for k in sorted(rep["baseline"]):
        print(f"  {k:<24} {rep['baseline'][k]}")


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="Read-only readiness gate for an unattended wiki-maintenance pass.")
    ap.add_argument("--json", action="store_true",
                    help="Print one JSON object instead of the human summary.")
    ap.add_argument("--root", default=None,
                    help="Vault root to gate (default: the parent of this script's scripts/ dir).")
    args = ap.parse_args()
    root = Path(args.root) if args.root else ROOT
    rep = preflight(root)
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print_human(rep)
    return 0 if rep["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
