"""SYNC-ENV canary (regression R157, ADR-0005 R6): fix_wikilinks.py --alias-only
end to end inside a REAL sync environment (a Dropbox folder), where every
earlier verification had not run.

    python -B scripts/tests/test_alias_state_sync_env.py --sync-dir <dir inside Dropbox>

Without --sync-dir it prints 'SKIP: real sync environment test requires
--sync-dir' and exits 0, so the normal suite never touches a sync folder.
It is an opt-in canary, NOT part of the normal suite totals.

With --sync-dir it creates ONE uniquely named child directory under <dir>
holding an ownership marker file, and refuses to operate on or clean up any
directory whose marker is missing or does not match. Then 3 independent runs,
each on a FRESH synthetic ~300-page vault (alias holders + many alias links,
several batches) inside that child:

    dry run -> full --apply -> --verify

asserting rc 0 each step and exact reconciliation (dry-run SAFE_REWRITES =
links_applied, lint's ALIAS-ONLY drop OK, VERIFY SAFE_REWRITES = 0). The
tool's operational state goes to a --state-dir in a temp dir OUTSIDE the sync
dir, and LOCALAPPDATA points at a temp dir too, so the real
%LOCALAPPDATA%/WIKIllm is never written. Each run asserts nothing
tool-owned exists inside the synthetic vault (no in-vault manifest, backup
dir or *.tmp-alias-fix; the vault's file set is unchanged) and that the page
backups landed in the state dir.

Per run it reports: run number, safe rewrite count, lock retry count, max
retry delay, final SAFE_REWRITES, cleanup ok. The last line is
'SYNC-ENV: PASS 3/3' or 'SYNC-ENV: FAIL k/3'. Zero lock retries is still a
PASS: contention is never manufactured (no fault hook is set).
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "fix_wikilinks.py"
MARKER = ".wikillm-sync-env-owner"
CHILD_PREFIX = "wikillm-sync-env-"
RUNS = 3
FAULT_ENVS = ("FIX_WIKILINKS_TEST_FAULT", "FIX_WIKILINKS_TEST_LOCKS")
LOCK_RE = re.compile(r"^LOCK RETRIES: (\d+) \(max wait (\d+\.\d)s\)$", re.M)


def synthetic_vault(root: Path, link_pages: int = 270, holders: int = 30,
                    links_per_page: int = 8) -> int:
    """A fresh synthetic vault: `holders` alias-holder pages and `link_pages`
    pages of bare [[alias]] links (each uniquely resolvable, so every one is
    a safe rewrite). Returns the expected SAFE_REWRITES. Deterministic."""
    (root / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "notes").mkdir(parents=True, exist_ok=True)
    (root / "_meta").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "log.md").write_bytes(b"# Log\n\n- created by the sync-env canary\n")
    for i in range(holders):
        (root / "wiki" / "concepts" / f"topic-{i:03d}.md").write_bytes(
            (f"---\ntitle: Topic {i}\naliases: [Topic {i} Alias]\n---\n# Topic {i}\n\n"
             f"Holder page {i}.\n").encode("utf-8"))
    for k in range(link_pages):
        lines = [f"# Note {k}", ""]
        for j in range(links_per_page):
            t = (k * 7 + j * 3) % holders
            lines.append(f"- point {j}: see [[Topic {t} Alias]] for detail.")
        (root / "wiki" / "notes" / f"note-{k:04d}.md").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return link_pages * links_per_page


def file_set(root: Path) -> set:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def inside(p: Path, parent: Path) -> bool:
    a = os.path.normcase(str(Path(p).resolve()))
    b = os.path.normcase(str(Path(parent).resolve()))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


def owned(child: Path, token: str) -> bool:
    """True only when `child` carries OUR marker with OUR token."""
    try:
        data = json.loads((child / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("token") == token


def rmtree_retry(p: Path) -> bool:
    """Remove a tree inside the sync dir; a sync client may hold a file open
    for a moment, so retry briefly. True when it is gone."""
    for delay in (0.1, 0.2, 0.4, 0.8, 1.6, 3.2, None):
        try:
            if p.exists():
                shutil.rmtree(p)
            return not p.exists()
        except OSError:
            if delay is None:
                return False
            time.sleep(delay)
    return not p.exists()


def run_tool(root: Path, state: Path, lad: Path, *args):
    env = dict(os.environ)
    for k in FAULT_ENVS:
        env.pop(k, None)  # never manufacture contention
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["LOCALAPPDATA"] = str(lad)
    argv = [sys.executable, "-B", str(SCRIPT), "--root", str(root), "--alias-only", *args,
            "--state-dir", str(state)]
    return subprocess.run(argv, cwd=str(root), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def one_run(n: int, child: Path, token: str, sync_dir: Path) -> tuple[bool, str]:
    """One fresh vault: dry run -> --apply -> --verify. (ok, report line)."""
    problems: list[str] = []
    safe = retries = final = None
    wait = None
    if not owned(child, token):
        return False, f"RUN {n}: REFUSED -- {child} lacks our ownership marker; not touched"
    vault = child / f"run{n}" / "SyncEnv Vault"
    tmp = Path(tempfile.mkdtemp(prefix=f"wikillm-syncenv-run{n}-"))
    state, lad = tmp / "state", tmp / "localappdata"
    try:
        if inside(tmp, sync_dir):
            raise AssertionError(f"temp dir {tmp} is inside the sync dir -- refusing (state must be outside)")
        expected = synthetic_vault(vault)
        before_files = file_set(vault)
        d = run_tool(vault, state, lad)
        out = d.stdout + d.stderr
        if d.returncode != 0:
            raise AssertionError(f"dry run rc {d.returncode}: {out[-2000:]}")
        m = re.search(r"^SAFE_REWRITES = (\d+)$", d.stdout, re.M)
        safe = int(m.group(1)) if m else None
        if safe != expected:
            problems.append(f"dry run SAFE_REWRITES {safe} != expected {expected}")
        if f"STATE: {state.resolve()}" not in d.stdout:
            problems.append("dry run did not print STATE: <the --state-dir>")
        a = run_tool(vault, state, lad, "--apply")
        out = a.stdout + a.stderr
        lk = LOCK_RE.findall(a.stdout)
        if len(lk) == 1:
            retries, wait = int(lk[0][0]), float(lk[0][1])
        else:
            problems.append("apply printed no single LOCK RETRIES line")
        if a.returncode != 0:
            raise AssertionError(f"apply rc {a.returncode}: {out[-3000:]}")
        rec = f"RECONCILE: manifest_safe_rewrites {safe} = links_applied {safe} + links_skipped_stale 0"
        if rec not in a.stdout:
            problems.append(f"apply reconcile is not exact (want '{rec}')")
        if not re.search(r"^  ALIAS-ONLY \d+ -> \d+ \(drop \d+\) vs links_applied lint-alias-only "
                         r"\d+: OK$", a.stdout, re.M) or "MISMATCH" in a.stdout:
            problems.append("lint reconciliation not OK")
        v = run_tool(vault, state, lad, "--verify")
        m = re.search(r"^SAFE_REWRITES = (\d+)$", v.stdout, re.M)
        final = int(m.group(1)) if m else None
        if v.returncode != 0 or final != 0 or "VERIFY OK: SAFE_REWRITES = 0" not in v.stdout:
            problems.append(f"verify rc {v.returncode}, SAFE_REWRITES {final}")
        # ADR-0005: nothing tool-owned inside the synthetic vault.
        if file_set(vault) != before_files:
            extra = sorted(file_set(vault) ^ before_files)[:5]
            problems.append(f"the vault's file set changed: {extra}")
        if (vault / ".alias-fix-backup").exists() or (vault / "_meta/alias-fix-manifest.json").exists():
            problems.append("legacy in-vault state was created")
        if any(vault.rglob("*.tmp-alias-fix")):
            problems.append("a *.tmp-alias-fix was left in the vault")
        backups = list((state / "backup").rglob("*.md")) if (state / "backup").is_dir() else []
        if len(backups) != len([p for p in before_files if p.startswith("wiki/notes/")]):
            problems.append(f"{len(backups)} page backups in the state dir, want one per rewritten page")
        if inside(state, sync_dir) or inside(lad, sync_dir):
            problems.append("operational state landed inside the sync dir")
    except AssertionError as e:
        problems.append(str(e))
    finally:
        # Clean up our run dir only after re-proving ownership.
        if owned(child, token):
            cleanup = rmtree_retry(child / f"run{n}")
        else:
            cleanup = False
            problems.append(f"marker lost -- {child} not cleaned up")
        shutil.rmtree(tmp, ignore_errors=True)
        cleanup = cleanup and not tmp.exists()
    if not cleanup:
        problems.append("cleanup failed")
    ok = not problems
    line = (f"RUN {n}: safe_rewrites={safe} lock_retries={retries} max_retry_delay="
            f"{'-' if wait is None else f'{wait:.1f}'}s final_SAFE_REWRITES={final} "
            f"cleanup={'ok' if cleanup else 'FAILED'} -> {'PASS' if ok else 'FAIL'}")
    if problems:
        line += "\n  " + "\n  ".join(problems)
    return ok, line


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sync-dir", type=Path, default=None,
                    help="an existing directory inside the sync service (e.g. a Dropbox folder)")
    args = ap.parse_args()
    if args.sync_dir is None:
        print("SKIP: real sync environment test requires --sync-dir")
        return 0
    sync_dir = args.sync_dir.resolve()
    if not sync_dir.is_dir():
        print(f"ERROR: --sync-dir {sync_dir} is not an existing directory -- nothing created")
        return 2
    token = uuid.uuid4().hex
    child = sync_dir / f"{CHILD_PREFIX}{token[:12]}"
    if child.exists():
        print(f"ERROR: {child} already exists -- refusing to reuse a directory we did not create")
        return 2
    child.mkdir()
    (child / MARKER).write_text(json.dumps({"token": token, "tool": "test_alias_state_sync_env",
                                            "pid": os.getpid(), "created": time.time()}),
                                encoding="utf-8")
    print(f"SYNC-ENV: sync dir {sync_dir}; owned child {child}")
    passed = 0
    for n in range(1, RUNS + 1):
        ok, line = one_run(n, child, token, sync_dir)
        print(line)
        passed += ok
    # Remove the child only if it is still ours and holds nothing but the marker.
    if owned(child, token) and {p.name for p in child.iterdir()} == {MARKER}:
        try:
            (child / MARKER).unlink()
            child.rmdir()
        except OSError as e:
            print(f"WARNING: could not remove {child}: {e}")
    else:
        print(f"WARNING: {child} left in place (not ours, or not empty) -- inspect it by hand")
    print(f"SYNC-ENV: {'PASS' if passed == RUNS else 'FAIL'} {passed}/{RUNS}")
    return 0 if passed == RUNS else 1


if __name__ == "__main__":
    sys.exit(main())
