#!/usr/bin/env python
"""Detect changes to files in raw/.

Computes SHA-256 of every file in raw/, compares to the stored baseline
in _meta/raw-hashes.json, reports added / modified / removed / unchanged.

Default: report only — does NOT update the baseline. Re-running will keep
showing the same changes until you accept them.

Acceptance is intentionally split into two explicit modes (no implicit
"accept everything" shortcut, to avoid silent disasters when shell splats
expand empty):

  --accept FILE [FILE ...]   acknowledge only the named files. The common
                             case when ingesting a multi-source corpus one
                             source at a time. A name may also be a
                             DIRECTORY under raw/ (e.g. watched/<slug>),
                             which accepts everything staged inside it --
                             a /watch report plus its hero frames is one
                             logical unit, not nine.

  --accept-all               acknowledge every detected change at once.
                             Use only when every detected change has been
                             ingested into the wiki.

A MODIFIED text file that is byte-identical to its baseline after CRLF<->LF
normalization is annotated as a line-endings-only rewrite (hash-proven, not a
size heuristic; the detail report gets a `note:` line, --summary a separate
annotation line so filenames stay copyable into --accept). The file still
reports MODIFIED and still needs an explicit accept. The annotation silently
does NOT appear for: non-text extensions (binaries never get it), files over
20 MB, equal-size changes, mixed-ending rewrites, NUL-containing content,
transient read errors, or files changed again mid-scan -- so absence of the
note is NOT evidence of a real content change. (regression R54)

Usage:
    python scripts/check_raw.py                       # report changes
    python scripts/check_raw.py --summary             # counts + names (+ rewrite annotations; no size/mtime detail)
    python scripts/check_raw.py --accept-all          # accept ALL detected changes
    python scripts/check_raw.py --accept "foo.pdf"    # accept just one file
    python scripts/check_raw.py --accept a.pdf b.pdf  # accept multiple specific files
    python scripts/check_raw.py --init                # first-time baseline init
    python scripts/check_raw.py --duplicates          # report byte-identical files (read-only)

Prefer --summary over truncating the report with `| head` or
`| Select-Object -First N` — early-terminated pipes kill the interpreter and
report a false failure (exit 255 under PowerShell).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "raw"
HASHES_PATH = PROJECT_ROOT / "_meta" / "raw-hashes.json"


def file_hash(path: Path) -> str:
    """Compute SHA-256 hash of a file, streaming in 64KB chunks (safe for large files)."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# Only these extensions get the line-ending hint. An extension gate (not a NUL
# sniff) because real binaries can be NUL-free -- a sampled NUL-free PDF, once
# CRLF-corrupted on disk, would otherwise earn a "content unchanged" hint while
# being broken as a binary. Text formats only; unknown extensions get no hint.
LE_HINT_TEXT_EXTENSIONS = {
    ".md", ".txt", ".json", ".csv", ".tsv", ".yaml", ".yml", ".html", ".htm",
    ".xml", ".srt", ".vtt", ".py", ".ps1", ".js", ".css", ".ini", ".cfg",
    ".toml", ".log",
}
LE_HINT_SIZE_CAP = 20_000_000


def line_ending_rewrite_hint(path: Path, old_hash: str, old_size: int, new_meta: dict) -> str | None:
    """Return a hint when a MODIFIED text file is a pure CRLF<->LF rewrite of its baseline.

    Definitive within its scope, not a size heuristic: re-hash the current bytes
    with line endings converted and compare against the baseline hash. A match
    proves the content is byte-identical up to line endings (regression R54 -- an
    Edit pass normalized a staged /watch report's CRLF to LF; the resulting
    -262-byte MODIFIED cost a session a hand diagnosis before re-accept).

    The hint states the fact, not a verdict: raw/ is immutable by contract, so a
    pure rewrite still means SOMETHING wrote to the inbox -- worth identifying.

    Scope: text extensions only (LE_HINT_TEXT_EXTENSIONS), and only UNIFORM
    reconstruction candidates -- a mixed-ending baseline is never reconstructed,
    so mixed-ending rewrites get no hint (rare: 0 of 120 files in a live-fleet
    scan; they simply fall back to the plain MODIFIED report). Within that
    uniform-candidate scope equal sizes cannot match, so they return early, and
    the size delta's sign determines the only viable candidate.

    Degrades to None -- never crashes the report; the extra cost is bounded to
    re-reading modified, allowlisted text files under the cap -- when: the
    extension is not allowlisted, either size exceeds the 20 MB cap, sizes are
    equal, the file cannot be read (transient Dropbox/AV lock), it grew past
    the cap or changed at all since scan_raw() (the hint must describe the same
    bytes as the report lines beside it), it contains NUL bytes, or no candidate
    matches. Never affects the hash verdict or the MODIFIED status.
    """
    if path.suffix.lower() not in LE_HINT_TEXT_EXTENSIONS:
        return None
    new_size = new_meta["size"]
    if new_size == old_size:
        return None  # uniform candidates cannot hash-match at equal sizes (see docstring)
    if new_size > LE_HINT_SIZE_CAP or old_size > LE_HINT_SIZE_CAP:
        return None
    try:
        with path.open("rb") as f:
            data = f.read(LE_HINT_SIZE_CAP + 1)  # bounded: a file grown since the scan can't stall us
    except OSError:
        return None
    if len(data) > LE_HINT_SIZE_CAP:
        return None
    if hashlib.sha256(data).hexdigest() != new_meta["hash"]:
        return None  # changed again since scan_raw(); report and hint would disagree
    if b"\x00" in data:
        return None
    if new_size < old_size:
        # shrank -> baseline had CRLF; reconstruct it by expanding to uniform CRLF
        candidate = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    else:
        # grew -> baseline had LF; reconstruct it by collapsing to uniform LF
        candidate = data.replace(b"\r\n", b"\n")
    # The hash compare is the proof; the != and length checks are only cheap
    # skips that avoid hashing a candidate that cannot match.
    if candidate != data and len(candidate) == old_size and hashlib.sha256(candidate).hexdigest() == old_hash:
        return ("line endings only -- content byte-identical after CRLF<->LF normalization. "
                "raw/ is immutable by contract; identify what rewrote the file before re-accepting.")
    return None


def scan_raw() -> dict[str, dict]:
    """Return {relpath: {hash, size, mtime}} for every file under raw/.

    Recurses into subdirectories -- /watch stages reports at
    raw/watched/<slug>/report.md, and a non-recursive scan left those
    permanently invisible to change detection (regression R7).

    Keys are RAW-relative POSIX paths. For a file sitting directly in raw/
    that string is identical to its bare filename, so existing baselines
    carry over with no migration; only nested files gain a new-shaped key.
    Bare filenames would NOT be safe here: every /watch report is named
    report.md, so they would all collide on one key.

    Skips hidden files and anything under a hidden directory.
    """
    out = {}
    for p in sorted(RAW_DIR.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(RAW_DIR)
        if any(part.startswith(".") for part in rel.parts):
            continue
        stat = p.stat()
        out[rel.as_posix()] = {
            "hash": file_hash(p),
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }
    return out


def load_baseline() -> dict[str, dict]:
    """Read the baseline JSON. Returns empty dict on first run (no baseline file yet)."""
    if not HASHES_PATH.exists():
        return {}
    return json.loads(HASHES_PATH.read_text(encoding="utf-8"))


def save_baseline(state: dict) -> None:
    """Write the baseline JSON with a current UTC timestamp. Overwrites any existing baseline."""
    HASHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_baseline_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "files": state,
    }
    HASHES_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # --accept now REQUIRES at least one FILE argument (nargs="+"). Previously
    # this was nargs="*" which let bare --accept mean "accept everything" — but
    # that semantic collided with PowerShell splat behavior: `--accept @files`
    # with an empty $files array would expand to bare --accept and silently
    # accept every pending change. The new --accept-all flag is the explicit
    # opt-in for that intent.
    parser.add_argument(
        "--accept",
        nargs="+",
        action="append",
        default=None,
        metavar="FILE",
        help="Update baseline for the named files only. Repeatable: --accept A B C and --accept A --accept B both accept everything named. A name may be a file (path relative to raw/) or a directory under raw/, which accepts everything staged inside it. For 'accept all detected changes', use --accept-all instead.",
    )
    parser.add_argument(
        "--accept-all",
        action="store_true",
        help="Accept ALL detected changes (added + modified + removed). Use only when every detected change has been ingested into the wiki.",
    )
    parser.add_argument("--init", action="store_true", help="First-time baseline initialization. Writes a fresh baseline from current raw/ contents.")
    parser.add_argument("--summary", action="store_true", help="Compact report: counts + filenames only, no per-file size/mtime detail. Hash-proven line-ending-only rewrites are named on a separate annotation line (filenames in the main list stay verbatim for --accept).")
    parser.add_argument("--duplicates", action="store_true",
                        help="Report byte-identical files in raw/ (grouped by content hash, largest first, with a reclaimable-bytes total). Read-only: no deletion, no baseline change. Deciding which copy is canonical needs judgement -- see scripts/README.md.")
    args = parser.parse_args()
    if args.accept:
        # action="append" + nargs="+" yields one list per flag occurrence;
        # flatten so `--accept A --accept B` == `--accept A B`. Without
        # action="append", argparse silently kept only the LAST occurrence --
        # a multi-source session under-accepted while printing "Accepted"
        # and exiting 0 (regression R29, R33; observed twice on 2026-08-11).
        args.accept = [name for group in args.accept for name in group]

    if not RAW_DIR.exists():
        print(f"ERROR: raw/ does not exist at {RAW_DIR}", file=sys.stderr)
        return 1

    current = scan_raw()

    if args.duplicates:
        # regression R22: the scan already hashes everything, so duplicates are a
        # grouping over existing data. Name-pattern eyeballing is not enough:
        # the largest real-world dup pair (87 MB) had unrelated names.
        groups: dict[tuple, list[str]] = {}
        for name, meta in current.items():
            groups.setdefault((meta["hash"], meta["size"]), []).append(name)
        dup_groups = sorted(
            (v for v in groups.values() if len(v) > 1),
            key=lambda v: current[v[0]]["size"] * (len(v) - 1), reverse=True)
        if not dup_groups:
            print(f"No byte-identical duplicates among {len(current)} files in raw/.")
            return 0
        reclaimable = 0
        print(f"Byte-identical duplicate groups ({len(dup_groups)}), largest reclaimable first:\n")
        for names in dup_groups:
            size = current[names[0]]["size"]
            extra = size * (len(names) - 1)
            reclaimable += extra
            print(f"  {size:,} bytes x {len(names)} copies  (+{extra:,} reclaimable)")
            for n in sorted(names):
                print(f"    {n}")
            print()
        print(f"Total reclaimable: {reclaimable:,} bytes across {len(dup_groups)} group(s).")
        print("Report only -- nothing was deleted, the baseline is untouched. Which copy is")
        print("canonical needs judgement (keep the name your log/ledger already cites).")
        return 0
    baseline_payload = load_baseline()
    baseline = baseline_payload.get("files", {}) if baseline_payload else {}
    baseline_ts = baseline_payload.get("_baseline_updated", "(none)") if baseline_payload else "(none)"

    if not baseline:
        print("No baseline yet. Initializing.")
        save_baseline(current)
        print(f"Baseline written to {HASHES_PATH.relative_to(PROJECT_ROOT)} ({len(current)} files).")
        return 0

    print(f"Baseline last updated: {baseline_ts}")
    print(f"Files in raw/:        {len(current)}")
    print(f"Files in baseline:    {len(baseline)}")
    print()

    current_names = set(current.keys())
    baseline_names = set(baseline.keys())

    added = sorted(current_names - baseline_names)
    removed = sorted(baseline_names - current_names)
    modified: list[str] = []
    unchanged: list[str] = []
    for name in sorted(current_names & baseline_names):
        if current[name]["hash"] != baseline[name]["hash"]:
            modified.append(name)
        else:
            unchanged.append(name)

    any_change = bool(added or removed or modified)

    if added:
        print(f"+ ADDED ({len(added)}):")
        if args.summary:
            print(f"    {', '.join(added)}")
        else:
            for n in added:
                print(f"    + {n}  [{current[n]['size']:,} bytes, mtime {current[n]['mtime']}]")
        print()

    if removed:
        print(f"- REMOVED ({len(removed)}):")
        if args.summary:
            print(f"    {', '.join(removed)}")
        else:
            for n in removed:
                print(f"    - {n}  [was {baseline[n]['size']:,} bytes]")
        print()

    if modified:
        # regression R54: annotate pure line-ending rewrites so the next session
        # gets the diagnosis for free instead of hand-diffing before re-accept.
        # Computed on EVERY mode including --accept-all: acceptance overwrites
        # the baseline hash -- the hint's only input -- so the bulk-accept run
        # is the last chance to surface that something wrote to the immutable
        # inbox. (Cost is bounded by the exact pre-filters in the helper.)
        le_hints = {}
        for n in modified:
            old_size = baseline[n].get("size")  # hand-edited baselines may lack it; hint degrades, report survives
            if old_size is None:
                continue
            hint = line_ending_rewrite_hint(RAW_DIR / n, baseline[n]["hash"], old_size, current[n])
            if hint:
                le_hints[n] = hint
        print(f"~ MODIFIED ({len(modified)}):")
        if args.summary:
            # Filenames stay verbatim -- sessions copy them straight into
            # --accept, so annotations must live out-of-band on their own line.
            print(f"    {', '.join(modified)}")
            if le_hints:
                print(f"    line-endings-only rewrites (content unchanged -- but something rewrote raw/; see --help): {', '.join(sorted(le_hints))}")
        else:
            for n in modified:
                old = baseline[n]
                new = current[n]
                print(f"    ~ {n}")
                # Hand-edited baselines may lack size/mtime -- degrade to '?'
                # instead of killing the report with a KeyError.
                if old.get("size") is not None:
                    size_delta = new["size"] - old["size"]
                    sign = "+" if size_delta >= 0 else ""
                    print(f"        size:  {old['size']:,} -> {new['size']:,} ({sign}{size_delta:,} bytes)")
                else:
                    print(f"        size:  ? -> {new['size']:,} (baseline entry lacks size)")
                print(f"        mtime: {old.get('mtime', '?')} -> {new['mtime']}")
                if n in le_hints:
                    print(f"        note:  {le_hints[n]}")
        print()

    if not any_change:
        print("No changes since last baseline.")
    else:
        print(f"Summary: {len(added)} added, {len(modified)} modified, {len(removed)} removed, {len(unchanged)} unchanged")

    # Acceptance dispatch. Three mutually-exclusive modes:
    #   --init            : write fresh baseline from current state
    #   --accept-all      : accept everything pending (explicit, no shell-splat ambiguity)
    #   --accept FILE ... : selective accept by name (nargs="+" enforces at least one)
    # If none specified and there are pending changes, just remind the user to re-run.
    if args.init:
        save_baseline(current)
        print()
        print(f"Baseline updated -> {HASHES_PATH.relative_to(PROJECT_ROOT)}")
    elif args.accept_all:
        save_baseline(current)
        print()
        print(f"Baseline updated -> {HASHES_PATH.relative_to(PROJECT_ROOT)} (all {len(added) + len(modified) + len(removed)} changes accepted)")
    elif args.accept:
        accepted, skipped = selective_accept(args.accept, current, baseline, added, modified, removed)
        if accepted:
            updated_payload = {
                "_baseline_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") + f" (selective: {len(accepted)} file{'s' if len(accepted) != 1 else ''})",
                "files": baseline,
            }
            HASHES_PATH.parent.mkdir(parents=True, exist_ok=True)
            HASHES_PATH.write_text(json.dumps(updated_payload, indent=2, sort_keys=True), encoding="utf-8")
            print()
            print(f"Baseline updated -> {HASHES_PATH.relative_to(PROJECT_ROOT)}")
            print(f"Accepted ({len(accepted)}): {', '.join(accepted)}")
        if skipped:
            print()
            for name, reason in skipped:
                print(f"  ! skipped {name!r}: {reason}")
        pending_added = len([n for n in added if n not in accepted])
        pending_modified = len([n for n in modified if n not in accepted])
        pending_removed = len([n for n in removed if n not in accepted])
        if pending_added or pending_modified or pending_removed:
            print()
            print(f"Still pending: {pending_added} added, {pending_modified} modified, {pending_removed} removed.")
    elif any_change:
        print()
        print("(Baseline NOT updated. Re-run with --accept FILE [...] or --accept-all once the changes have been ingested into the wiki.)")

    return 0


def selective_accept(
    names: list[str],
    current: dict[str, dict],
    baseline: dict[str, dict],
    added: list[str],
    modified: list[str],
    removed: list[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Update baseline in-place for the named files only.

    Returns (accepted_names, [(skipped_name, reason), ...]). Mutates `baseline`.
    """
    added_set = set(added)
    modified_set = set(modified)
    removed_set = set(removed)
    accepted: list[str] = []
    skipped: list[tuple[str, str]] = []
    for raw_name in names:
        name = raw_name.replace("\\", "/").rstrip("/")
        # Case 0: directory-prefix accept. A staged directory (a /watch report
        # plus its hero frames) is ONE logical unit -- naming the directory
        # accepts everything under it, rather than forcing the caller to list
        # every frame. Only fires when the name is not itself a tracked file,
        # so it can never shadow a real filename.
        if name not in current and name not in baseline:
            prefix = name + "/"
            under = sorted(k for k in (added_set | modified_set | removed_set) if k.startswith(prefix))
            if under:
                for k in under:
                    if k in added_set or k in modified_set:
                        baseline[k] = current[k]
                    elif k in removed_set:
                        del baseline[k]
                    accepted.append(k)
                continue
        # Four possible cases for each named file, handled in order:
        if name in added_set or name in modified_set:
            # Case 1: file is new or has changed → write current hash into baseline.
            baseline[name] = current[name]
            accepted.append(name)
        elif name in removed_set:
            # Case 2: file existed in baseline but no longer in raw/ → drop the entry.
            del baseline[name]
            accepted.append(name)
        elif name in current and name in baseline:
            # Case 3: file exists in both and hashes match → user mentioned a file that
            # has no pending change. Likely a typo or a stale list; warn and continue.
            skipped.append((name, "unchanged (already at baseline hash)"))
        elif name not in current and name not in baseline:
            # Case 4: file is in neither. Definitely a typo (or someone deleted it
            # already). Warn so the user can correct the command.
            skipped.append((name, "not found in raw/ or baseline"))
        else:
            # Shouldn't be reachable given the three sets cover all change types,
            # but kept as a safety net for unanticipated states.
            skipped.append((name, "no detected change"))
    return accepted, skipped


if __name__ == "__main__":
    sys.exit(main())
