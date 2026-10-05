#!/usr/bin/env python3
"""
audit_claims.py -- claim-audit sampler (the mechanical half of a claim-faithfulness pass)

The fourth layer of the mechanical-lint stack:
  lint.py          -> is the graph broken?
  check_stale.py   -> is the content stale?
  audit_claims.py  -> are the citations FAITHFUL?  (this script samples; an LLM session judges)

Why this exists: the drift class no other scanner catches is a wiki page
asserting something its cited source doesn't quite say -- a figure credited to
the wrong author, a nuance flattened into a stronger claim, a label the source
never used. Links resolve, nothing is stale, but the claim has drifted from
its source. Reading comprehension can't be scripted; SAMPLING can. This script
picks N citation-bearing claim lines and prints a verification queue for the
session to judge against the sources/ page and the underlying raw/ document.

SAMPLING IS RISK-WEIGHTED BY DEFAULT (--mode risk), not uniform. Uniform random
is close to useless at realistic cadences: a 2026 audit of one instance found a
population of 335 claims against a default n=10, i.e. an ~8-year cycle for a
single pass. Worse, the defects were not uniformly distributed -- in a
page-by-page re-read of a 15-page derived corpus, EVERY page overstated its
sources, and the failures clustered hard on claims with NO locator. So the
sampler ranks by risk and spends most of the budget there:

  +3  no #heading anchor on the sources link  (the dominant empirical signal)
  +2  the line states a figure (%, dose, ratio, range, threshold)
  +2  the page carries a term from _meta/audit-priority.txt (per-vault stakes)
  +1  the page is tagged `central` (a hub other pages cite)
  +1  the line uses bold emphasis (author marked it load-bearing)

70% of the sample comes from the highest-risk end (deterministic), 30% from a
seeded random draw over the remainder so coverage still rotates. --mode random
restores the old uniform behaviour.

LOCATOR COVERAGE is reported against a TARGET (--target, default 60%) and every
run appends a dated datapoint to _meta/locator-coverage.json (suggest_anchors.py's
apply modes record one too, via the shared record_coverage() function, so the
trend reflects mechanical anchor work as well), so the number is tracked rather
than merely observed. Coverage is the leading indicator: claims
carrying a page-referenced locator survive verification; claims without one are
where the defects live. Raising coverage is the cheapest way to make the corpus
auditable at all.

Deterministic + advisory only: the default seed is the current quarter, and
the JUDGED-CLAIMS LEDGER (_meta/claim-audit-ledger.json, regression R52) removes
claims already judged FAITHFUL this quarter from the pool, so --n means NEW
claims and a same-quarter re-run serves the next-highest-risk unjudged claims
instead of re-serving the same queue. A run is reproduced by seed + ledger
snapshot (or --include-judged, which ignores the ledger). DRIFTED / UNSUPPORTED
/ STALE claims stay in the pool until their text changes. Each queue entry
prints an id= token; record verdicts with --record-verdict ID@SLUG=VERDICT (an
in-session read) or --record-run (a workflow run). Always exits 0 (the
exceptions: an unusable --root, --record-run, or --record-verdict input exits 2).
Stdlib only. Writes: the coverage datapoint (unless --no-history), and --record-run
appends a workflow run record to _meta/claim-audit-ledger.json (regression R70).

--root audits the vault at PATH instead of this script's own repo. Without it
the paths below are derived from __file__, so a TEMPLATE or WORKTREE copy run
from a vault's cwd audits the template's empty scaffold and reports "No
citation-bearing claim lines found" -- exit 0, looks clean, exercised nothing
(regression R67). Pass --no-history with --root unless the run is meant to move the
target vault's coverage trend.

Quarterly workflow (stamp it "Last run: ... Next due: ~YYYY-MM" on a recurring-
items page so check_stale.py section D nags when due):
  1. Run the script; work the queue in-session (sources page -> locator -> raw/).
  2. Judge each claim FAITHFUL / DRIFTED / UNSUPPORTED / STALE; fix immediately.
  3. Log the line and apply the cadence rule exactly as the NEXT STEPS block
     printed at the end of a run says (next_steps() below is the single source,
     regression R63); update the recurring-items stamp.
  4. Validation: occasionally run a seeded-error drill:
     one session plants 2-3 subtle distortions and force-includes them via
     --include; a later session judges blind; recall <100% means the audit
     protocol needs sharpening.
"""

import argparse
import collections
import datetime
import hashlib
import json
import math
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
SOURCES = WIKI / "sources"
RAW = ROOT / "raw"
META = ROOT / "_meta"
PRIORITY_FILE = META / "audit-priority.txt"
COVERAGE_FILE = META / "locator-coverage.json"
LEDGER_FILE = META / "claim-audit-ledger.json"


def set_root(path):
    """Re-point every vault-relative path at `path`; returns the new ROOT.

    Rebinding the module globals is what --root needs: every consumer
    (iter_claim_lines, raw_pointers, record_coverage, load_priority_terms,
    and suggest_anchors.py, which reads them as attributes of this module)
    touches these names INSIDE a function, so one rebind before the first
    scan moves all of them. Threading a root parameter through every
    signature would move the same bytes and change eight call shapes.

    Fails loud on a directory with no wiki/: the silent empty-population run
    is exactly the failure --root exists to kill, so refusing to guess is the
    whole point (regression R67).
    """
    global ROOT, WIKI, SOURCES, RAW, META, PRIORITY_FILE, COVERAGE_FILE, LEDGER_FILE
    root = Path(path).expanduser().resolve()   # relative_to(ROOT) needs absolute
    if not (root / "wiki").is_dir():
        raise ValueError(f"--root {root}: no wiki/ directory there -- not a vault root")
    ROOT = root
    WIKI = ROOT / "wiki"
    SOURCES = WIKI / "sources"
    RAW = ROOT / "raw"
    META = ROOT / "_meta"
    PRIORITY_FILE = META / "audit-priority.txt"
    COVERAGE_FILE = META / "locator-coverage.json"
    LEDGER_FILE = META / "claim-audit-ledger.json"
    return ROOT


# A line "states a figure" if it carries a number with units, a percentage, a
# decimal, or a numeric range -- the falsifiable, dangerous-if-wrong kind of
# claim. A bare 4-digit year (citation furniture) does not count on its own.
FIGURE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|g/kg|mg|kg|lb|km|mi|m/s|bpm|ms|kcal|mmHg|mm|cm|"
    r"spm|min|hr?s?|hours?|days?|weeks?|months?|years?|x|-fold|RM|MET)"
    r"|\d+\.\d+"
    r"|\d+\s*(?:-|to|–)\s*\d+"
    r"|(?:HR|OR|RR|ES|CI|p)\s*[=<>]\s*\d",
    re.I,
)


def load_priority_terms() -> list:
    """Per-vault high-stakes terms, one per line (# comments allowed).

    Deliberately NOT hardcoded: what counts as high-stakes is instance-specific
    -- a clinical figure in a health wiki, a dosage in a farm wiki, a load
    rating in an engineering wiki. Absent file = no term weighting."""
    if not PRIORITY_FILE.exists():
        return []
    try:
        return [ln.strip().lower() for ln in PRIORITY_FILE.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return []

# Lines under these headings are link inventory / history, not claims.
SKIP_HEADINGS = re.compile(
    r"^#{1,6}\s*(see also|related|sources?|inputs|history|wiki edits prompted)", re.I
)
WIKILINK = re.compile(r"\[\[([^\]|#]+)(#[^\]|]*)?(\|[^\]]*)?\]\]")

SKIP_FILES = {"log.md", "index.md"}


# The one definition of "a sources link": an optional ../ run, then
# sources/<slug>, nothing else -- the same shape check_anchors' extractor
# accepts. The old substring test ("sources/" in target) also admitted
# cross-vault paths (../../Other Vault/wiki/sources/x), which entered the
# claim population but could never be validated: check_anchors' stricter
# regex silently skipped them (counted-but-unchecked, inflating coverage),
# and suggest_anchors resolved their slug against THIS vault's sources/,
# so an unanchored one could be suggested -- or auto-applied -- an anchor
# from the wrong vault's same-slug page.
SOURCE_TARGET = re.compile(r"(?:\.\./)*sources/[^/]+$")


def is_source_target(target: str) -> bool:
    return bool(SOURCE_TARGET.match(target.replace("\\", "/").strip()))


def slug_of(target: str) -> str:
    return target.replace("\\", "/").rstrip("/").split("/")[-1].strip()


def iter_claim_lines(priority_terms=()):
    """Yield a claim dict for every source-citing line on a wiki content page
    (sources/ pages excluded -- they're the layer being cited, audited via the
    raw/ pointer instead)."""
    for path in sorted(WIKI.rglob("*.md")):
        rel = path.relative_to(ROOT)
        if path.name in SKIP_FILES or SOURCES in path.parents:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = text.splitlines()
        # frontmatter span
        fm_end = 0
        if lines and lines[0].strip() == "---":
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    fm_end = i
                    break
        fm = "\n".join(lines[:fm_end])
        # auto-generated pages are script-written; their text isn't claims
        if "auto_generated: true" in fm:
            continue
        page_central = bool(re.search(r"(?m)^tags:.*\bcentral\b", fm)
                            or re.search(r"(?m)^\s*-\s*central\s*$", fm))
        low = text.lower()
        page_priority = any(t in low for t in priority_terms)
        skipping = False
        for i, line in enumerate(lines):
            if i <= fm_end:
                continue
            if line.lstrip().startswith("#"):
                skipping = bool(SKIP_HEADINGS.match(line.strip()))
                continue
            if skipping or line.lstrip().startswith("~~"):
                continue
            for m in WIKILINK.finditer(line):
                target, anchor = m.group(1), m.group(2)
                if is_source_target(target):
                    yield {
                        "rel": str(rel), "lineno": i + 1, "line": line.strip(),
                        "key": claim_key(str(rel), line),
                        "slug": slug_of(target),
                        "anchored": bool(anchor and len(anchor) > 1),
                        "central": page_central,
                        "priority": page_priority,
                        "figure": bool(FIGURE.search(line)),
                        "bold": "**" in line,
                    }


def claim_key(rel: str, line: str) -> str:
    """The identity of a claim line in the judged-claims ledger (regression R52):
    sha256 over the repo-relative path (forward slashes, so a Windows and a
    POSIX machine sharing one Dropbox vault agree) and the line text with
    whitespace runs collapsed -- whitespace ONLY, deliberately boring. Any other
    edit to the line, an anchor insertion included, is a new identity and so an
    unjudged claim. Shortened to 16 hex chars (64 bits) so it prints as a
    readable id= token in the queue."""
    norm_rel = str(rel).replace("\\", "/")
    norm_line = " ".join(str(line).split())
    return hashlib.sha256(f"{norm_rel}\n{norm_line}".encode("utf-8")).hexdigest()[:16]


def period_of(day) -> str:
    """The ledger's skip window: the calendar quarter, e.g. 2026-Q3 -- the
    same token as the default seed. Always the calendar quarter, never a
    --seed override (a seed is a tie-break lever and can be any token)."""
    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


def is_skipped(row, slug: str, period: str, include_judged: bool, line_slugs=()) -> bool:
    """THE skip rule, derived at sample time from stored facts (no stored
    boolean -- it would drift from the verdict / re-arm rules):
    skip iff the row was judged in THIS period AND its verdict was FAITHFUL
    AND --include-judged is absent. DRIFTED / UNSUPPORTED / STALE never skip
    (a non-faithful claim stays hot until its text changes), and a period
    roll lifts every skip."""
    if include_judged or not isinstance(row, dict):
        return False
    sources = row.get("sources")
    source = sources.get(slug) if isinstance(sources, dict) else None
    # A v1 flat row remains authoritative only where attribution is certain.
    # On a multi-source line it suppresses nothing: guessing would recreate R132.
    if source is None and not isinstance(sources, dict) and set(line_slugs) == {slug}:
        source = row
    return (isinstance(source, dict)
            and source.get("last_judged_period") == period
            and source.get("verdict") == "FAITHFUL")


def risk_score(c: dict) -> int:
    """Higher = more worth an auditor's time. See the module docstring for why
    these weights: the no-locator signal dominates because that is empirically
    where drift was found."""
    return ((0 if c["anchored"] else 3)
            + (2 if c["figure"] else 0)
            + (2 if c["priority"] else 0)
            + (1 if c["central"] else 0)
            + (1 if c["bold"] else 0))


def risk_reasons(c: dict) -> str:
    r = []
    if not c["anchored"]:
        r.append("no-locator")
    if c["figure"]:
        r.append("figure")
    if c["priority"]:
        r.append("priority-term")
    if c["central"]:
        r.append("central")
    if c["bold"]:
        r.append("bold")
    return ",".join(r) or "-"


def raw_pointers(slug: str, raw_names: list) -> list:
    """Best-effort raw/ file pointers: which actual raw/ filenames (or their
    distinctive stems) appear in the sources page text. Robust to spaces in
    filenames; short stems are skipped (they match prose by accident)."""
    page = SOURCES / f"{slug}.md"
    if not page.exists():
        return ["(sources page NOT FOUND -- lint.py should have caught this)"]
    try:
        text = page.read_text(encoding="utf-8")
    except OSError:
        return []
    exact = [n for n in raw_names if n in text]
    rest = [n for n in raw_names if n not in exact]
    # Fallbacks, only when the page never names a raw path outright. A nested
    # file's distinctive token is its PARENT DIRECTORY, not its stem ("report"
    # is 6 chars and would never clear the stem guard below). Gated on `not
    # exact` so a page that does cite the report path is not also handed every
    # hero frame sitting beside it.
    bases = []
    if not exact:
        bases = [n for n in rest if "/" in n and Path(n).parent.name and Path(n).parent.name in text]
        rest = [n for n in rest if n not in bases]
    stems = [n for n in rest if len(Path(n).stem) >= 15 and Path(n).stem in text]
    return (exact + bases + stems)[:3]


def record_coverage(anchored, total, today=None):
    """Append/replace today's locator-coverage datapoint in
    _meta/locator-coverage.json and return the updated history list.

    Invariant this function installs: the day's datapoint reflects the
    day's END state. Same-date entries are replaced, not appended, so the
    last writer of the day wins. Sole writer of the file -- audit_claims
    runs and suggest_anchors apply paths both call this; never reimplement
    the rounding / cap / JSON format at a call site.

    Returns None (and never raises) when total == 0 (freshly bootstrapped
    wiki, nothing to record) or when the write fails -- a history failure
    must never change the caller's exit code.
    """
    if total <= 0:
        return None
    if today is None:
        today = datetime.date.today()
    hist = []
    if COVERAGE_FILE.exists():
        try:
            hist = json.loads(COVERAGE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            hist = []
    stamp = today.isoformat()
    hist = [h for h in hist if h.get("date") != stamp]
    hist.append({"date": stamp, "anchored": anchored, "total": total,
                 "pct": round(100 * anchored / total, 1)})
    hist = hist[-40:]
    try:
        META.mkdir(exist_ok=True)
        COVERAGE_FILE.write_text(json.dumps(hist, indent=1) + "\n", encoding="utf-8")
    except OSError:
        return None
    return hist


# _meta/claim-audit-ledger.json -- per-vault audit STATE (never template-synced):
#   {"version": 1, "runs": [<run record>, ...], "claims": {<claim_key>: <row>, ...}}
# `claims` is the judged-claims ledger of regression R52/R132: one row per line identity
# (claim_key), with per-cited-slug facts under `sources`. The top-level verdict is
# the worst current per-slug verdict for display only; eligibility is DERIVED per
# slug by is_skipped(), never stored. Rows whose key no
# longer resolves to a live claim line are pruned (and counted) on every write.
# `runs` is the escalation telemetry of regression R70: one record per claim-audit
# workflow run and nothing else (in-session --record-verdict writes claims only), exactly the workflow's returned `run_record` plus a `run` id and
# the `recorded` date. Observe-only -- nothing reads it to tune anything; it is
# the comparable per-run data the escalation gate is to be judged on later.
LEDGER_VERSION = 1
LEDGER_RUNS_CAP = 100
RUN_RECORD_KEYS = ("seed", "n", "hits", "split", "unconfirmed", "escalated", "escalation_reasons", "rows")


def load_ledger() -> dict:
    """The ledger as a dict with a `runs` list; an absent file is an empty ledger.
    A present-but-unreadable file RAISES (ValueError) -- rewriting it from empty
    would silently destroy the history it exists to keep."""
    if not LEDGER_FILE.exists():
        return {"version": LEDGER_VERSION, "runs": [], "claims": {}}
    try:
        data = json.loads(LEDGER_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{LEDGER_FILE} is unreadable ({exc}) -- fix or move it aside; refusing to overwrite it") from exc
    if (not isinstance(data, dict) or not isinstance(data.get("runs", []), list)
            or not all(isinstance(r, dict) for r in data.get("runs", []))):
        raise ValueError(f"{LEDGER_FILE} is not a ledger object with a 'runs' list of objects -- refusing to overwrite it")
    data.setdefault("version", LEDGER_VERSION)
    data.setdefault("runs", [])
    if not isinstance(data.setdefault("claims", {}), dict):
        raise ValueError(f"{LEDGER_FILE} has a 'claims' value that is not an object -- refusing to overwrite it")
    return data


def save_ledger(data: dict) -> None:
    META.mkdir(exist_ok=True)
    tmp = LEDGER_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
        tmp.replace(LEDGER_FILE)
    finally:  # a failed replace (e.g. a sync-client lock) must not strand the temp file in _meta/
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def next_run_id(ledger: dict, stamp: str) -> str:
    """YYYY-MM-DD.k with k = highest same-date suffix + 1 (a count would
    re-issue an id after a hand-delete or a cap trim)."""
    suffixes = [str(r.get("run", ""))[len(stamp) + 1:] for r in ledger["runs"]
                if str(r.get("run", "")).startswith(stamp + ".")]
    return f"{stamp}.{1 + max([int(x) for x in suffixes if x.isdigit()], default=0)}"


def _run_record_problem(rec):
    """None if `rec` is a usable run record, else a one-line reason. Types are
    checked BEFORE any write, so bad input can never half-land in the ledger."""
    if not isinstance(rec, dict):
        return "not a JSON object"
    missing = [k for k in RUN_RECORD_KEYS if k not in rec]
    if missing:
        return f"missing: {', '.join(missing)}"
    if rec.get("kind") != "workflow":
        return "kind is not 'workflow' (pass the workflow's run_record, not an older whole result)"
    for k in ("n", "hits", "split", "unconfirmed", "escalated"):
        if not isinstance(rec[k], int) or isinstance(rec[k], bool):
            return f"{k} is not an integer"
    if not isinstance(rec["escalation_reasons"], dict) or not all(
            isinstance(v, int) and not isinstance(v, bool) for v in rec["escalation_reasons"].values()):
        return "escalation_reasons is not an object of integer counts"
    if not isinstance(rec["rows"], list) or not all(isinstance(r, dict) for r in rec["rows"]):
        return "rows is not a list of objects"
    return None


def record_run(path, today=None) -> int:
    """--record-run: append a claim-audit workflow run record to the ledger.

    Accepts the workflow's `run_record` object, or the whole returned result
    (its `run_record` field is used). Exit 0 on success, 2 on unusable input or
    an unreadable ledger -- never a partial write."""
    if today is None:
        today = datetime.date.today()
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: --record-run {path}: cannot read JSON ({exc})", file=sys.stderr)
        return 2
    if isinstance(rec, dict) and isinstance(rec.get("run_record"), dict):
        rec = rec["run_record"]
    problem = _run_record_problem(rec)
    if problem:
        print(f"ERROR: --record-run {path}: not a claim-audit run record ({problem}) "
              "-- pass the workflow's run_record (or its whole result)", file=sys.stderr)
        return 2
    try:
        ledger = load_ledger()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    body = {k: v for k, v in rec.items() if k not in ("run", "recorded")}
    # A retry after a timeout must not double the telemetry: an identical record is already on file.
    # Trade-off, accepted: two genuinely separate runs with byte-identical results (same seed, every
    # row identical) also collapse to one entry -- a workflow has no randomness to carry a nonce.
    for r in ledger["runs"]:
        if {k: v for k, v in r.items() if k not in ("run", "recorded")} == body:
            # Telemetry is not doubled, but the verdicts are still (idempotently) applied:
            # a run recorded before the judged-claims ledger existed gets its verdicts now.
            # Period from the run's OWN recorded date, never today: a September run
            # replayed in October was judged in Q3, not Q4.
            # A replay only BACK-FILLS: it writes claims no row exists for yet and never
            # replaces an existing row (any row there is this run's own or a later judgement).
            try:
                run_day = datetime.date.fromisoformat(str(r.get("recorded")))
            except ValueError:
                print(f"already recorded as run {r.get('run')} -- its recorded date is unreadable, verdicts not re-applied")
                return 0
            written, unresolved, pruned = apply_verdicts(ledger, rec["rows"], str(r.get("run")), period_of(run_day), backfill=True)
            try:
                save_ledger(ledger)
            except OSError as exc:
                print(f"ERROR: could not write {LEDGER_FILE}: {exc}", file=sys.stderr)
                return 2
            print(f"already recorded as run {r.get('run')} -- telemetry not duplicated")
            print_verdict_summary(written, unresolved, pruned)
            return 0
    stamp = today.isoformat()
    entry = dict(body)
    entry["run"] = next_run_id(ledger, stamp)     # set AFTER the input: the ledger owns these two fields
    entry["recorded"] = stamp
    ledger["runs"].append(entry)
    ledger["runs"] = ledger["runs"][-LEDGER_RUNS_CAP:]
    written, unresolved, pruned = apply_verdicts(ledger, rec["rows"], entry["run"], period_of(today))
    try:
        save_ledger(ledger)
    except OSError as exc:
        print(f"ERROR: could not write {LEDGER_FILE}: {exc}", file=sys.stderr)
        return 2
    reasons = rec.get("escalation_reasons") or {}
    rs = ", ".join(f"{a}={b}" for a, b in reasons.items()) or "none"
    print(f"recorded run {entry['run']} -> {LEDGER_FILE}  "
          f"(escalated {rec['escalated']}/{rec['n']}; reasons: {rs}; runs on file: {len(ledger['runs'])})")
    print_verdict_summary(written, unresolved, pruned)
    return 0


def _row_key(row, live_keys: dict, line_cache: dict):
    """The ledger key for one judged row: the queue's printed id= key when the
    row carries one (the identity that was actually SERVED), else the key of
    the line at file:line as it reads now. None when neither resolves to a
    live claim line."""
    k = row.get("key")
    f, ln = row.get("file"), row.get("line")
    if isinstance(k, str) and k:
        if k not in live_keys:
            return None
        # A key carried beside a file must be THAT file's claim: a miscopied or swapped
        # key would otherwise land a verdict on a different claim (a FAITHFUL there would
        # silently skip a claim nobody read). Line numbers are not compared -- a fix can
        # shift them. In-session rows (--record-verdict) carry no file.
        if isinstance(f, str) and f.replace("\\", "/") != live_keys[k]:
            return None
        return k
    if not isinstance(f, str) or not isinstance(ln, int) or isinstance(ln, bool):
        return None
    rel = f.replace("\\", "/")
    if rel not in line_cache:
        try:
            line_cache[rel] = (ROOT / rel).read_text(encoding="utf-8").splitlines()
        except OSError:
            line_cache[rel] = []
    lines = line_cache[rel]
    if not 1 <= ln <= len(lines):
        return None
    k = claim_key(rel, lines[ln - 1].strip())
    return k if k in live_keys else None


# Severity order for one claim line judged more than once in a run (the queue
# prints one entry per sources link, so a two-citation line appears twice):
# any non-FAITHFUL verdict beats FAITHFUL, so the line stays armed.
_VERDICT_RANK = {"FAITHFUL": 0, "UNCONFIRMED": 1, "STALE": 2, "UNSUPPORTED": 3, "DRIFTED": 4}


def _run_order(run_id):
    """(date, k) for a workflow run id YYYY-MM-DD.k, (date, None) for
    in-session:YYYY-MM-DD, ("", None) for anything else."""
    s = str(run_id or "")
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.(\d+)", s)
    if m:
        return (m.group(1), int(m.group(2)))
    m = re.fullmatch(r"in-session:(\d{4}-\d{2}-\d{2})", s)
    if m:
        return (m.group(1), None)
    return ("", None)


def _is_newer(old_run, new_run) -> bool:
    """True when old_run is STRICTLY newer than new_run. Dates decide; on the
    same date only two workflow ids are ordered (by k) -- an in-session record
    and a workflow run on one day are treated as concurrent, so the later write wins."""
    (od, ok), (nd, nk) = _run_order(old_run), _run_order(new_run)
    if od != nd:
        return od > nd
    return ok is not None and nk is not None and ok > nk


def apply_verdicts(ledger: dict, rows, run_id: str, period: str, backfill: bool = False):
    """Write per-slug judgments into ledger['claims'] and prune dead line keys.

    The line key remains the identity; each cited slug carries its own verdict,
    run, and period. UNCONFIRMED is stored because it is a real eligibility fact:
    that slug stays hot while a different FAITHFUL slug stays suppressed. The
    top-level verdict is the worst per-slug verdict for display only. Legacy
    multi-source rows retain a line-level ordering watermark, never coverage.
    Invalid workflow verdicts veto FAITHFUL for their pair and are unresolved.
    Returns (written, unresolved, pruned)."""
    live_claims = list(iter_claim_lines())
    live = {c["key"]: c["rel"].replace("\\", "/") for c in live_claims}
    live_slugs = {}
    for c in live_claims:
        live_slugs.setdefault(c["key"], set()).add(c["slug"])
    claims = ledger.setdefault("claims", {})
    # Lazy v1 migration. A flat judgment can be attributed without guessing only
    # when the live line cites one slug; multi-source flat rows remain aggregate
    # history and suppress nothing until each slug is judged under v2.
    for k, old in list(claims.items()):
        if (k in live_slugs and len(live_slugs[k]) == 1 and isinstance(old, dict)
                and not isinstance(old.get("sources"), dict) and old.get("verdict") in _VERDICT_RANK):
            slug = next(iter(live_slugs[k]))
            old["sources"] = {slug: {name: old.get(name) for name in
                                     ("verdict", "last_judged_run", "last_judged_period")}}
    best, vetoed, cache, unresolved = {}, set(), {}, 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        v = row.get("final_verdict")
        k = _row_key(row, live, cache)
        if k is None:
            unresolved += 1
            continue
        slug = row.get("slug")
        if slug is not None and (not isinstance(slug, str) or slug not in live_slugs[k]):
            unresolved += 1
            continue
        if slug is None:
            # Backward compatibility for old workflow rows and ID=VERDICT:
            # a one-source line is unambiguous; a multi-source line is not.
            if len(live_slugs[k]) != 1:
                unresolved += 1
                continue
            slug = next(iter(live_slugs[k]))
        pair = (k, slug)
        if not isinstance(v, str) or v not in _VERDICT_RANK:
            unresolved += 1
            vetoed.add(pair)
            continue
        if pair not in best or _VERDICT_RANK[v] > _VERDICT_RANK[best[pair]]:
            best[pair] = v
    best = {pair: v for pair, v in best.items() if not (v == "FAITHFUL" and pair in vetoed)}
    written = 0
    touched = set()
    for (k, slug), v in list(best.items()):
        # Never let an older run overwrite a newer judgement (a replayed September
        # FAITHFUL must not bury an October DRIFTED).
        old = claims.get(k)
        if not isinstance(old, dict):
            old = {}
        sources = old.get("sources") if isinstance(old.get("sources"), dict) else {}
        watermark = old.get("legacy_watermark")
        if not isinstance(old.get("sources"), dict) and len(live_slugs[k]) > 1 and "last_judged_run" in old:
            watermark = {name: old.get(name) for name in
                         ("verdict", "last_judged_run", "last_judged_period")}
        if isinstance(watermark, dict):
            watermark_run = watermark.get("last_judged_run")
            # A replay must be provably newer than the legacy judgment. A fresh
            # same-day in-session/workflow judgment still uses last-write-wins,
            # as _is_newer() defines; an equal or older workflow id cannot pass.
            if ((watermark_run == run_id and _run_order(run_id)[1] is not None)
                    or _is_newer(watermark_run, run_id)
                    or (backfill and not _is_newer(run_id, watermark_run))):
                continue
        old_source = sources.get(slug)
        if isinstance(old_source, dict) and (backfill or _is_newer(old_source.get("last_judged_run"), run_id)):
            continue
        sources[slug] = {"verdict": v, "last_judged_run": run_id, "last_judged_period": period}
        claims[k] = {"relpath": live[k], "sources": sources}
        if isinstance(watermark, dict):
            # Retain chronology even after only one sibling gains coverage.
            claims[k]["legacy_watermark"] = watermark
        written += 1
        touched.add(k)
    # Aggregate is informational. Eligibility never consults these fields.
    for k in touched:
        row = claims[k]
        current = [v for s, v in row["sources"].items() if s in live_slugs[k] and isinstance(v, dict)]
        if current:
            worst = max(current, key=lambda x: _VERDICT_RANK.get(x.get("verdict"), 99))
            row.update({"verdict": worst.get("verdict"),
                        "last_judged_run": worst.get("last_judged_run"),
                        "last_judged_period": worst.get("last_judged_period")})
    # Prune only what is provably gone: a file that exists but could not be read this
    # time (a sync-client lock) keeps its rows rather than losing its judgements.
    dead = []
    for k, row in claims.items():
        if k in live:
            continue
        p = ROOT / str(row.get("relpath", ""))
        if p.is_file():
            try:
                p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        dead.append(k)
    for k in dead:
        del claims[k]
    return written, unresolved, len(dead)


def print_verdict_summary(written, unresolved, pruned):
    print(f"judged-claims ledger: {written} claim verdict(s) written, {pruned} key(s) pruned "
          f"(line edited or deleted since judged)"
          + (f", {unresolved} judged row(s) UNRESOLVED -- invalid/missing verdict or no unambiguous live "
             "claim/source target; invalid judgments not recorded" if unresolved else ""))


_RECORD_VERDICT = re.compile(r"^([0-9a-f]{16})(?:@([^=]+))?=(FAITHFUL|DRIFTED|UNSUPPORTED|STALE)$")


def record_verdicts(pairs, today=None) -> int:
    """--record-verdict ID@SLUG=VERDICT (repeatable): the in-session path -- a
    session that judged the printed queue by hand (the /ingest close-out
    audit) records its verdicts by the queue's id= tokens. Writes claims rows
    only, with last_judged_run = "in-session:<date>": `runs` stays the
    workflow's escalation telemetry (regression R70) -- close-outs appending there
    would push the few quarterly records out of its cap. Exit 2 on a malformed pair."""
    if today is None:
        today = datetime.date.today()
    parsed = []
    for p in pairs:
        m = _RECORD_VERDICT.match(p.strip())
        if not m:
            print(f"ERROR: --record-verdict {p!r}: expected ID[@SLUG]=VERDICT, ID the 16-hex id= token from the "
                  "queue, VERDICT one of FAITHFUL / DRIFTED / UNSUPPORTED / STALE", file=sys.stderr)
            return 2
        parsed.append((m.group(1), m.group(2), m.group(3)))
    live_slugs = {}
    for claim in iter_claim_lines():
        live_slugs.setdefault(claim["key"], set()).add(claim["slug"])
    rows = []
    for key, supplied_slug, verdict in parsed:
        slugs = live_slugs.get(key)
        if not slugs:
            print(f"ERROR: --record-verdict {key}: unknown or stale claim id; no live claim line has that id", file=sys.stderr)
            return 2
        if supplied_slug is not None:
            if supplied_slug not in slugs:
                print(f"ERROR: --record-verdict {key}@{supplied_slug}: slug is not currently cited by that claim "
                      f"(live: {', '.join(sorted(slugs))})", file=sys.stderr)
                return 2
            slug = supplied_slug
        elif len(slugs) == 1:
            slug = next(iter(slugs))
        else:
            print(f"ERROR: --record-verdict {key}: slug omitted but the claim cites multiple sources "
                  f"({', '.join(sorted(slugs))}); use ID@SLUG=VERDICT", file=sys.stderr)
            return 2
        rows.append({"key": key, "slug": slug, "final_verdict": verdict})
    try:
        ledger = load_ledger()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    run_id = f"in-session:{today.isoformat()}"
    written, unresolved, pruned = apply_verdicts(ledger, rows, run_id, period_of(today))
    try:
        save_ledger(ledger)
    except OSError as exc:
        print(f"ERROR: could not write {LEDGER_FILE}: {exc}", file=sys.stderr)
        return 2
    print(f"recorded {run_id} verdicts -> {LEDGER_FILE}")
    print_verdict_summary(written, unresolved, pruned)
    return 0


def parse_include_spec(spec: str):
    """Parse one --include spec into (file_suffix, lineno_or_None).

    `FILE:LINE` names one claim line; `FILE:` (trailing colon, no line)
    names every claim line in that file. Anything else is rejected with a
    ValueError naming the spec (regression R97): a bare `FILE` with no colon or
    a `:LINE` with no path used to leave the path part EMPTY, and an empty
    path suffix matches every page; a non-numeric line part used to be
    read as "no line filter". The claim-audit workflow's queueCommand()
    rejects the same shapes on its side.
    """
    text = str(spec).strip()
    path, sep, ln = text.rpartition(":")
    path = path.replace("\\", "/").strip()
    if not sep or not path:
        raise ValueError(f"--include {spec!r}: empty path part -- pass FILE:LINE "
                         "(e.g. wiki/concepts/foo.md:42) or FILE: for every claim line in that file; "
                         "nothing was sampled")
    if ln and not ln.isdigit():
        raise ValueError(f"--include {spec!r}: line part {ln!r} is not a number -- pass FILE:LINE "
                         "or FILE: for every claim line in that file; nothing was sampled")
    return path, (int(ln) if ln else None)


def claim_identity(c) -> tuple:
    """The identity two population entries share when they are the same claim-
    source entry: (line key, cited slug, line number). Used for dedupe against
    the sample instead of dict equality (regression R97)."""
    return (c["key"], c["slug"], c["lineno"])


def include_matches(c, path: str, lineno) -> bool:
    """True when the spec names this entry: the path part is a whole-page path
    or a suffix on a `/` boundary (`foo.md`, `concepts/foo.md`), never a bare
    string suffix (`s.md:` must not force every page ending in s.md)."""
    rel = c["rel"].replace("\\", "/")
    return ((rel == path or rel.endswith("/" + path))
            and (lineno is None or c["lineno"] == lineno))


def main():
    ap = argparse.ArgumentParser(description="Sample citation-bearing claims for an LLM faithfulness audit.")
    ap.add_argument("--n", type=int, default=10, help="sample size (default 10)")
    ap.add_argument("--seed", default=None,
                    help="sampling seed (default: current quarter, e.g. 2026-Q2 -- reproducible within a quarter)")
    ap.add_argument("--source", default=None,
                    help="restrict the sample to claims citing this source slug")
    ap.add_argument("--include", action="append", default=[],
                    metavar="FILE:LINE", help="force-include every claim-source entry on that line, one per "
                    "cited source (repeatable; for seeded-error drills). FILE: (no line) forces every claim "
                    "line in the file; a spec with an empty path or a non-numeric line exits 2 (regression R97)")
    ap.add_argument("--mode", choices=("risk", "random"), default="risk",
                    help="risk (default): 70%% of the sample from the highest-risk claims, 30%% seeded-random. "
                         "random: uniform, the pre-2026 behaviour")
    ap.add_argument("--target", type=int, default=60,
                    help="locator-coverage target %% (default 60); advisory, never changes exit code")
    ap.add_argument("--no-history", action="store_true",
                    help="do not append this run's coverage datapoint to _meta/locator-coverage.json")
    ap.add_argument("--root", default=None, metavar="PATH",
                    help="audit the vault at PATH instead of this script's own repo "
                         "(a template/worktree copy can then be run against a real vault; "
                         "pair with --no-history to leave the target's trend alone)")
    ap.add_argument("--record-run", default=None, metavar="JSON",
                    help="append a claim-audit workflow run record (its run_record, or the whole "
                         "result) to _meta/claim-audit-ledger.json and exit; no sampling (regression R70). "
                         "Also writes each judged row's verdict into the ledger's claims map (regression R52)")
    ap.add_argument("--include-judged", action="store_true",
                    help="do not skip claims the judged-claims ledger shows FAITHFUL this quarter "
                         "(the quarterly deep audit passes this; regression R52)")
    ap.add_argument("--record-verdict", action="append", default=[], metavar="ID[@SLUG]=VERDICT",
                    help="record an in-session verdict for a queue entry by its id= token and source slug (repeatable; "
                         "VERDICT = FAITHFUL/DRIFTED/UNSUPPORTED/STALE) into the judged-claims ledger and exit")
    args = ap.parse_args()

    try:  # Windows consoles default to cp1252; claim text is UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    # Malformed --include specs are rejected up front (regression R97), before
    # any --root, sampling or ledger read: a spec with an empty path part
    # used to match EVERY population entry (endswith("") is always True).
    include_specs = []
    for spec in args.include:
        try:
            include_specs.append(parse_include_spec(spec))
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    if args.root:
        try:
            set_root(args.root)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"(auditing {ROOT})")

    if args.record_run and args.record_verdict:
        print("ERROR: --record-run and --record-verdict are separate paths; pass one", file=sys.stderr)
        return 2
    if args.record_run:
        return record_run(args.record_run)
    if args.record_verdict:
        return record_verdicts(args.record_verdict)

    today = datetime.date.today()
    seed = args.seed or f"{today.year}-Q{(today.month - 1) // 3 + 1}"

    priority_terms = load_priority_terms()
    population = list(iter_claim_lines(priority_terms))
    line_slugs = {}
    for c in population:
        line_slugs.setdefault(c["key"], set()).add(c["slug"])
    if args.source:
        population = [c for c in population if c["slug"] == args.source]
    if not population:
        print("No citation-bearing claim lines found for the given filters.")
        print()
        next_steps()
        return 0

    # Judged-claims ledger (regression R52): --n means NEW claims this run, so claims
    # judged FAITHFUL this quarter leave the candidate pool before sampling (the
    # highest-risk UNJUDGED claim is always next). Read-only here; pruning happens
    # on the record paths. An unreadable ledger skips nothing and says so.
    period = period_of(today)
    ledger_note = None
    try:
        judged = load_ledger().get("claims", {})
    except ValueError as exc:
        judged = {}
        ledger_note = f"WARNING: {exc} -- nothing skipped this run"
    candidates = [c for c in population if not is_skipped(
        judged.get(c["key"]), c["slug"], period, args.include_judged, line_slugs[c["key"]])]
    skipped_pairs = {(c["key"], c["slug"]) for c in population} - {(c["key"], c["slug"]) for c in candidates}

    total = len(population)
    anchored = sum(1 for c in population if c["anchored"])
    pct = 100 * anchored / total
    by_dir = {}
    for c in population:
        p = c["rel"].replace("\\", "/")
        d = p.rsplit("/", 2)[-2] if "/" in p else "wiki"
        by_dir[d] = by_dir.get(d, 0) + 1

    rng = random.Random(seed)
    if args.mode == "random":
        sample = rng.sample(candidates, min(args.n, len(candidates)))
    else:
        # Stratified: most of the budget on the highest-risk end (deterministic,
        # ties broken by the seed so it is stable within a quarter), the rest
        # random over the remainder so coverage still rotates between runs.
        n_risk = min(math.ceil(args.n * 0.7), len(candidates))
        shuffled = candidates[:]
        rng.shuffle(shuffled)
        ranked = sorted(shuffled, key=risk_score, reverse=True)
        sample = ranked[:n_risk]
        rest = ranked[n_risk:]
        n_rand = min(args.n - n_risk, len(rest))
        if n_rand > 0:
            sample = sample + rng.sample(rest, n_rand)

    # Forced entries (regression R97): EVERY population entry matching a spec is
    # forced -- one entry per wikilink match, the queue's contract everywhere
    # else -- not just the first (the old `break` dropped the second citation
    # of a two-source line, and a first match already in the sample stopped a
    # not-yet-sampled second one from being forced at all). Dedupe against the
    # sample per ENTRY by (key, slug, lineno) identity, as a MULTISET: a line
    # citing the same source twice holds two entries of one identity, and if
    # the sampler already drew one of them the other is still forced (the
    # count of sampled + forced per identity never exceeds the population's).
    # A population entry is forced at most once even when two specs overlap
    # (FILE: and FILE:LINE). Forced entries are drawn from the population, not
    # the candidates, so they bypass the judged-claims ledger skip as before.
    forced = []
    pop_count = collections.Counter(claim_identity(c) for c in population)
    held = collections.Counter(claim_identity(c) for c in sample)
    forced_objs = set()
    for spec, (f, ln) in zip(args.include, include_specs):
        hits = 0
        for c in population:
            if not include_matches(c, f, ln):
                continue
            hits += 1
            ident = claim_identity(c)
            if id(c) in forced_objs or held[ident] >= pop_count[ident]:
                continue
            forced_objs.add(id(c))
            held[ident] += 1
            forced.append(c)
        if hits == 0:
            print(f"WARNING: --include {spec!r} matched no claim line (nothing forced for it)", file=sys.stderr)
    sample = forced + sample

    print(f"=== CLAIM-AUDIT SAMPLE ===  seed={seed}  n={len(sample)}  population={total}  mode={args.mode}")
    if args.mode == "risk":
        print(f"  risk weights: no-locator +3, figure +2, priority-term +2, central +1, bold +1"
              + (f"   ({len(priority_terms)} priority terms loaded)" if priority_terms
                 else "   (no _meta/audit-priority.txt -- term weighting off)"))
    print()
    print("=== LOCATOR COVERAGE (vault-wide, all source-citing claim lines) ===")
    verdict = "AT/ABOVE TARGET" if pct >= args.target else f"BELOW TARGET by {args.target - pct:.0f} pts"
    print(f"  {anchored}/{total} ({pct:.0f}%) carry a #heading anchor on the sources link"
          f"   [target {args.target}% -- {verdict}]")
    if pct < args.target:
        need = math.ceil((args.target / 100) * total) - anchored
        print(f"  -> {need} more anchored claim(s) would reach the target."
              " Anchors are the cheapest defence against drift: an unanchored")
        print("     claim cannot be checked without re-reading the whole source, so in practice it never is.")
    print("  by directory: " + ", ".join(f"{k}={v}" for k, v in sorted(by_dir.items())))
    if not args.no_history:
        hist = record_coverage(anchored, total, today)
        if hist and len(hist) > 1:
            trail = "  ".join(f"{h['date']}:{h['pct']:.0f}%" for h in hist[-4:])
            print(f"  trend: {trail}   (_meta/locator-coverage.json)")
    print()
    print("=== VERIFICATION QUEUE (judge each claim against the sources page, then raw/) ===")
    raw_names = sorted(
        p.relative_to(RAW).as_posix()
        for p in RAW.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(RAW).parts)
    ) if RAW.exists() else []
    by_src = {}
    for c in sample:
        by_src.setdefault(c["slug"], []).append(c)
    for slug in sorted(by_src):
        ptrs = raw_pointers(slug, raw_names)
        print(f"\n  [[sources/{slug}]]" + (f"   raw: {'; '.join(ptrs)}" if ptrs else "   raw: (none named on page)"))
        for c in sorted(by_src[slug], key=risk_score, reverse=True):
            loc = "anchored" if c["anchored"] else "NO LOCATOR"
            txt = c["line"] if len(c["line"]) <= 160 else c["line"][:157] + "..."
            print(f"    {c['rel']}:{c['lineno']}  [{loc}]  risk={risk_score(c)} ({risk_reasons(c)})  id={c['key']}")
            print(f"      {txt}")
    print()
    next_steps(skipped=len(skipped_pairs), include_judged=args.include_judged, period=period, note=ledger_note)
    return 0


def next_steps(skipped=None, include_judged=False, period=None, note=None):
    """The single source for the log-line shape and the cadence rule (regression R63).
    CLAUDE.md, scripts/README.md and the module docstring point here; do not restate it."""
    print("=== NEXT STEPS (LLM session, not this script) ===")
    print("  For each claim: open the sources page, follow its locator (or the raw/ file) and judge:")
    print("    FAITHFUL / DRIFTED (source says something subtly different) / UNSUPPORTED / STALE.")
    print("  Record results in wiki/log.md as exactly this heading shape:")
    print("    ## [date] lint | claim audit (seed, n, hits; split S, unconfirmed U; escalated E/N (reasons: k1=n1, k2=n2))")
    print("  hits = non-FAITHFUL confirmed by two readers on the same finding (the claim-audit workflow:")
    print("  the arbiter upholds a lens that raised it); split = drift resting on one reader;")
    print("  unconfirmed = no adjudicated result. Split and unconfirmed rows are never applied.")
    print("  escalated E/N = claims the workflow sent to the Opus arbiter, with per-reason claim counts")
    print("  (the workflow returns this whole parenthesis as log_line; E=0 -> 'reasons: none').")
    print("  A single in-session read has split 0, unconfirmed 0; escalated n/a (no panel ran).")
    print("  Workflow runs also persist the telemetry: --record-run <run_record json> (regression R70).")
    print("  Update the recurring-items 'Last run / Next due' stamp (check_stale.py section D watches it).")
    print("  Cadence rule: track hits/n per quarter. 0 hits x 3 quarters -> stretch the cadence, but only")
    print("  if split and unconfirmed were also 0 in all three; >=2 hits in a quarter -> raise --n.")
    print("  This block is the single source for the log line and the cadence rule (regression R63).")
    if skipped is not None:
        print("  Judged-claims ledger (_meta/claim-audit-ledger.json, regression R52):"
              + (f" --include-judged: nothing skipped ({period})." if include_judged else
                 f" skipped {skipped} claim-source judgment(s) FAITHFUL in {period} (--include-judged re-reads them)."))
        print("  Record verdicts so the next run moves on: a workflow run via --record-run <run_record json>;")
        print("  an in-session read via --record-verdict <id>@<slug>=<VERDICT> per judged entry (id= on each queue line).")
        if note:
            print(f"  {note}")


if __name__ == "__main__":
    sys.exit(main())
