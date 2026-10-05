"""regression R157 / ADR-0005 R4: fix_wikilinks.py --alias-only retries ONLY the
three Windows lock codes (winerror 5 / 32 / 33) with the fixed backoff 0.1,
0.2, 0.4, 0.8, 1.6, 3.2 s, re-checks the target's hash before each retry by
write type, stops a batch cleanly when the budget runs out, and reports
'LOCK RETRIES: N (max wait Xs)' on every apply and restore.

R1  retry success: injected locks on a page write, a backup write and an
    apply-log save are absorbed -- rc 0, exact reconcile, the pages equal a
    lock-free reference apply, LOCK RETRIES counted; restore byte-exact
R1c each lock code on its own: winerror 32 and 33 injected (page, backup,
    state save, restore write) are retried like 5 (the default fault code)
R2 exhausted page write (7 locks): exit 3 with RESTORE, 6 retries / 6.3 s,
    the batch STOPS -- the file after the locked one is never attempted
    (no skip-and-continue); the printed RESTORE restores byte-exact
R3  exhausted apply-log saves: after a batch's writes -> exit 3, RESTORE
    accounts for every logged page; the very first write-ahead save ->
    exit 3, nothing written, no apply log, no RESTORE line
R4  concurrent human edit during a retry: the page keeps the human bytes
    (changed-during-apply), the rest applies; restore accounts for it as a
    never-written REVIEW row and still settles the run
R5  a refused write that had in fact landed: counted as written, never
    rewritten (one write call), postconditions and lint reconcile hold
R6  backup conflict: an existing backup that differs from the original
    stops the batch (exit 3) and is never overwritten; an existing backup
    byte-identical to the original is fine (rc 0)
R7  anything that is not a lock code fails at once: no winerror, winerror
    1224 -> exit 3 with LOCK RETRIES: 0 and no backoff wait
R8  restore retries page writes, page reads and backup reads (reads: the
    winerror 5/32/33 shape injected here -- a real Windows read lock has
    no winerror and is never retried, see R18 / R19); a restore
    lock that outlasts the retry is REVIEW_REQUIRED restore-write-failed
    with a RESTORE INCOMPLETE line (no SET ASIDE), and a re-run finishes
R9  the fault hook is inert outside a temp dir (WARNING, 0 retries), and
    the default backtick mode never prints a LOCK RETRIES line
R10 restore's mirror rules: a human edit during a restore retry is left
    alone (changed-during-restore); a refused restore write that had landed
    counts RESTORED with one write; an exhausted page read is transient
    (restore-read-failed + RESTORE INCOMPLETE, re-run finishes); the
    unfinished-run check lists a page it cannot read as live (fail closed)
R11 a human save during an EARLIER lock backoff (apply: the page's backup
    retry; restore: an earlier page's write retry) is never overwritten --
    the page is re-hashed immediately before its first write attempt
R12 R4(c) beyond the apply log: restore's ledger save (absorbed; exhausted
    -> exit 3 + RESTORE), state-dirs.json and manifest.json retry a lock
R13 a human save during a write's backoff whose re-hash read is then itself
    locked is never overwritten (apply, restore, backup); a read locked past
    the budget -> LockExhausted exit 3, the edit kept
R14 state reads retry a winerror lock (same caveat as R8): apply-log.json (dry run / --verify / restore
    see the live run; exhausted -> restore rc 2, nothing written), the
    registry and the manifest
R15 an exhausted lock on the table-shape postcondition's re-read of a
    written page stops the batch (exit 3, POSTCONDITION FAILED, RESTORE),
    never a silently skipped check with rc 0
R16 a re-apply (same run_id, after a smoke batch) whose first write-ahead
    save is exhausted says 'nothing to restore' with no RESTORE line (the
    earlier settled log is not this apply's); the re-run apply succeeds
R17 'present': a page that reaches exactly the intended bytes during an
    earlier lock backoff counts written (apply) / ALREADY_ORIGINAL (restore)
    with no write call and REVIEW 0 (kills a 'present -> changed' mutant)
R18 a read refused as PermissionError errno 13 with NO winerror (CPython's
    shape for a real Windows sharing violation; the hook's "code": null) is
    never retried and never a human edit: apply stops the batch (exit 3),
    a write / backup recheck fails at once, restore lists it transient
    (restore-read-failed + RESTORE INCOMPLETE, no SET ASIDE), the
    unfinished-run check counts it live and --apply refuses
R19 (Windows) a REAL lock -- CreateFileW share mode 0 on a page -- during
    --restore: transient restore-read-failed + RESTORE INCOMPLETE, and the
    printed RESTORE settles after release

Run: python -B scripts/tests/test_alias_lock_retry.py      Exit 0 = pass.
Temp vaults only; every --alias-only run passes --state-dir <vault>.state and
LOCALAPPDATA points at a temp dir, so the real %LOCALAPPDATA%/WIKIllm is
never touched.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.dont_write_bytecode = True
import test_fix_wikilinks_alias as A  # noqa: E402  (shared fixture + helpers)

SCRIPTS = A.SCRIPTS
LOCKS = "FIX_WIKILINKS_TEST_LOCKS"
LOCK_RE = re.compile(r"^LOCK RETRIES: (\d+) \(max wait (\d+\.\d)s\)$", re.M)


def locks(p):
    """(retries, max wait) from the one LOCK RETRIES line an apply/restore prints."""
    found = LOCK_RE.findall(p.stdout)
    assert len(found) == 1, ("want exactly one LOCK RETRIES line", p.stdout)
    return int(found[0][0]), float(found[0][1])


def run(root, *args, faults=None, **kw):
    env = dict(kw.pop("env_extra", None) or {})
    if faults is not None:
        env[LOCKS] = json.dumps(faults)
    return A.run(root, *args, env_extra=env, **kw)


WRAPPER = '''import os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, {scripts!r})
import tempfile
from pathlib import Path
import fix_wikilinks as fw
mode, arg = sys.argv[1], sys.argv[2]
vault = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
counter = vault.parent / (vault.name + ".writes")
def is_target(p):
    p = Path(p).resolve()
    return p.is_relative_to(vault) and p.relative_to(vault).as_posix() == arg
if mode == "human-edit":
    # a human saves the page while the tool sleeps between lock retries
    orig_sleep = fw._lock_sleep
    done = []
    def sleep(s):
        orig_sleep(s)
        if not done:
            done.append(1)
            with open(vault / arg, "ab") as fh:
                fh.write(b"HUMAN EDIT\\n")
    fw._lock_sleep = sleep
elif mode.startswith("human-edit-lockread:"):
    # a human saves <arg> (a vault page, or an absolute backup path) during
    # the FIRST lock backoff, and the saving app / syncer then holds it: the
    # next N reads of it (the tool's re-hash) are refused with winerror 32
    n_locked = int(mode.split(":")[1])
    target = (Path(arg) if Path(arg).is_absolute() else vault / arg).resolve()
    orig_sleep = fw._lock_sleep
    orig_rb = Path.read_bytes
    armed = []
    def sleep(s):
        orig_sleep(s)
        if not armed:
            armed.append(n_locked)
            with open(target, "ab") as fh:
                fh.write(b"HUMAN EDIT\\n")
    def read_bytes(self):
        if armed and armed[0] > 0 and Path(self).resolve() == target:
            armed[0] -= 1
            raise OSError(13, "[test] sharing violation after a human save", str(self), 32)
        return orig_rb(self)
    fw._lock_sleep = sleep
    Path.read_bytes = read_bytes
elif mode in ("landed", "nolock", "otherwin"):
    orig_aw = fw._atomic_write
    calls = []
    def aw(path, data):
        if not is_target(path):
            return orig_aw(path, data)
        calls.append(1)
        counter.write_text(str(len(calls)))
        if mode == "landed":
            # the rename lands, yet the OS reports a lock (a refused write
            # that had in fact gone through)
            orig_aw(path, data)
            if len(calls) == 1:
                raise OSError(13, "Access is denied", str(path), 5)
            return None
        if mode == "nolock":
            raise PermissionError(13, "simulated non-lock refusal", str(path))
        raise OSError(13, "user-mapped section open", str(path), 1224)
    fw._atomic_write = aw
elif mode.startswith("present:"):
    # during the FIRST lock backoff the page <arg> is set to exactly the
    # bytes in file <mode suffix> -- the bytes the tool is about to write --
    # and every tool write of <arg> is counted (it must make none)
    src = Path(mode.split(":", 1)[1])
    orig_sleep = fw._lock_sleep
    orig_aw = fw._atomic_write
    done, calls = [], []
    def sleep(s):
        orig_sleep(s)
        if not done:
            done.append(1)
            (vault / arg).write_bytes(src.read_bytes())
    def aw(path, data):
        if is_target(path):
            calls.append(1)
            counter.write_text(str(len(calls)))
        return orig_aw(path, data)
    fw._lock_sleep = sleep
    fw._atomic_write = aw
elif mode == "notemp":
    tempfile.gettempdir = lambda: arg
sys.argv = ["fix_wikilinks.py"] + sys.argv[3:]
sys.exit(fw.main())
'''


def run_wrapped(base, root, mode, arg, *args, faults=None):
    w = base / "lock-wrapper.py"
    w.write_text(WRAPPER.format(scripts=str(SCRIPTS)), encoding="utf-8")
    env = dict(os.environ)
    env.pop(A.FAULT_ENV, None)
    env.pop(LOCKS, None)
    if faults is not None:
        env[LOCKS] = json.dumps(faults)
    return subprocess.run([sys.executable, "-B", str(w), mode, arg, "--root", str(root), *args,
                           *A.state_args(root, args)], cwd=str(root), env=env, capture_output=True,
                          text=True, encoding="utf-8")


def pages(root):
    return {k: v for k, v in A.snapshot(root).items() if k.startswith(("wiki/", "_meta/"))}


def fresh(base, name):
    root = base / name
    before = A.build(root)
    p = run(root, "--alias-only")
    assert p.returncode == 0, p.stdout + p.stderr
    return root, before, A.manifest(root)["run_id"]


def restore(root, rid, faults=None):
    return run(root, "--alias-only", "--restore", rid, faults=faults)


def counts(p):
    return tuple(int(re.search(rf"^{k} = (\d+)$", p.stdout, re.M).group(1))
                 for k in ("RESTORED", "ALREADY_ORIGINAL", "REVIEW_REQUIRED"))


def printed_restore(out, cwd):
    line = [ln for ln in out.splitlines() if ln.startswith("RESTORE: ")][-1]
    import shlex
    parts = shlex.split(line[len("RESTORE: "):].replace("\\", "/"), posix=True)
    return subprocess.run([sys.executable, "-B", *parts[1:]], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8")


def r1_success(base, ref):
    root, before, rid = fresh(base, "r1")
    faults = {"page:wiki/crlf.md": 2, "backup:wiki/bom.md": 1,
              "state:apply-log.json": {"n": 1, "skip": 1}}
    p = run(root, "--alias-only", "--apply", faults=faults)
    assert p.returncode == 0, p.stdout + p.stderr
    assert locks(p) == (4, 0.3), locks(p)
    assert "RECONCILE: manifest_safe_rewrites 18 = links_applied 18 + links_skipped_stale 0" in p.stdout, p.stdout
    assert pages(root) == ref, "a retried apply differs from the lock-free reference"
    A.no_tool_state_in_vault(root)
    r = restore(root, rid)
    assert r.returncode == 0 and counts(r) == (3, 0, 0) and locks(r) == (0, 0.0), r.stdout
    assert all((root / k).read_bytes() == b for k, b in before.items())
    print("PASS R1 retry success: 2 page + 1 backup + 1 apply-log locks absorbed, LOCK RETRIES: 4 "
          "(max wait 0.3s), output == lock-free reference, restore byte-exact")


def r1_codes(base, ref):
    """Each of the three lock codes is retried on its own (a set narrowed to
    {5} must fail here): 32 on a page write and a state save, 33 on a backup
    write and a restore write."""
    root, before, rid = fresh(base, "r1c")
    faults = {"page:wiki/crlf.md": {"n": 1, "code": 32}, "backup:wiki/bom.md": {"n": 1, "code": 33},
              "state:apply-log.json": {"n": 1, "skip": 1, "code": 32}}
    p = run(root, "--alias-only", "--apply", faults=faults)
    assert p.returncode == 0, p.stdout + p.stderr
    assert locks(p) == (3, 0.1), locks(p)
    assert pages(root) == ref, "a code-32/33 retried apply differs from the lock-free reference"
    r = restore(root, rid, faults={"restore:wiki/crlf.md": {"n": 1, "code": 33},
                                   "restore:wiki/bom.md": {"n": 1, "code": 32}})
    assert r.returncode == 0 and counts(r) == (3, 0, 0) and locks(r) == (2, 0.1), r.stdout + r.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())
    print("PASS R1c winerror 32 and 33 each retried (page, backup, state save, restore write): rc 0, "
          "output == reference, restore byte-exact")


def r2_exhausted_page(base):
    root, before, rid = fresh(base, "r2")
    t0 = time.monotonic()
    p = run(root, "--alias-only", "--apply", faults={"page:wiki/concepts/links.md": 7})
    took = time.monotonic() - t0
    assert p.returncode == 3 and "Traceback" not in p.stderr, p.stdout + p.stderr
    assert locks(p) == (6, 6.3) and took >= 6.3, (locks(p), took)
    assert "write failed for wiki/concepts/links.md (LockExhausted" in p.stdout, p.stdout
    assert "lock retries exhausted after 6 retries (6.3s)" in p.stdout, p.stdout
    # the batch stopped: bom.md (before) written, links.md locked, crlf.md
    # (after) never attempted -- no skip-and-continue.
    assert (root / "wiki/bom.md").read_bytes() != before["wiki/bom.md"]
    assert (root / "wiki/concepts/links.md").read_bytes() == before["wiki/concepts/links.md"]
    assert (root / "wiki/crlf.md").read_bytes() == before["wiki/crlf.md"]
    assert ("RECONCILE: manifest_safe_rewrites 18 = links_applied 0 + links_written_failed_batch 1 "
            "+ links_not_attempted 17 + links_skipped_stale 0") in p.stdout, p.stdout
    log = json.loads((A.bdir(root, rid) / "apply-log.json").read_text(encoding="utf-8"))
    st = {k: v["status"] for k, v in log["files"].items()}
    assert st == {"wiki/bom.md": "written", "wiki/concepts/links.md": "write-failed",
                  "wiki/crlf.md": "pending"}, st
    r = printed_restore(p.stdout, root)
    assert r.returncode == 0 and counts(r) == (1, 2, 0), r.stdout + r.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())
    print(f"PASS R2 exhausted page write: exit 3 after 6 retries ({took:.1f}s), LOCK RETRIES: 6 (max "
          f"wait 6.3s), batch stopped (the next file never attempted), RESTORE 1 + ALREADY 2")


def r3_exhausted_journal(base):
    # (a) the after-write save of batch 1 outlasts the retry
    root, before, rid = fresh(base, "r3a")
    p = run(root, "--alias-only", "--apply", "--batch-size", "1",
            faults={"state:apply-log.json": {"n": 7, "skip": 1}})
    assert p.returncode == 3 and "Traceback" not in p.stderr, p.stdout + p.stderr
    assert "APPLY LOG SAVE FAILED after the batch's writes" in p.stdout, p.stdout
    assert locks(p)[0] == 6 and "RESTORE: " in p.stdout, p.stdout
    log = json.loads((A.bdir(root, rid) / "apply-log.json").read_text(encoding="utf-8"))
    assert list(log["files"]) == ["wiki/bom.md"] and log["files"]["wiki/bom.md"]["status"] == "pending"
    assert (root / "wiki/bom.md").read_bytes() != before["wiki/bom.md"]
    r = printed_restore(p.stdout, root)
    assert r.returncode == 0 and counts(r) == (1, 0, 0), r.stdout + r.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())
    # (b) the very first write-ahead save outlasts the retry: nothing written
    root, before, rid = fresh(base, "r3b")
    p = run(root, "--alias-only", "--apply", faults={"state:apply-log.json": 7})
    assert p.returncode == 3 and "Traceback" not in p.stderr, p.stdout + p.stderr
    assert "APPLY LOG SAVE FAILED before any write of this batch" in p.stdout, p.stdout
    assert "Nothing was written in the vault by this apply" in p.stdout, p.stdout
    assert "RESTORE: " not in p.stdout, p.stdout
    assert not (A.bdir(root, rid) / "apply-log.json").exists()
    assert all((root / k).read_bytes() == b for k, b in before.items())
    print("PASS R3 exhausted apply-log saves: after-write save -> exit 3, RESTORE accounts for the "
          "logged page; first intent save -> exit 3, nothing written, no RESTORE line")


def r4_human_edit(base, ref):
    root, before, rid = fresh(base, "r4")
    p = run_wrapped(base, root, "human-edit", "wiki/crlf.md", "--alias-only", "--apply",
                    faults={"page:wiki/crlf.md": 1})
    assert p.returncode == 0, p.stdout + p.stderr
    assert "changed-during-apply  wiki/crlf.md" in p.stdout and locks(p) == (1, 0.1), p.stdout
    assert (root / "wiki/crlf.md").read_bytes() == before["wiki/crlf.md"] + b"HUMAN EDIT\n"
    assert "RECONCILE: manifest_safe_rewrites 18 = links_applied 17 + links_skipped_stale 1" in p.stdout
    for rel in ("wiki/bom.md", "wiki/concepts/links.md"):
        assert (root / rel).read_bytes() == ref[rel], rel
    r = restore(root, rid)
    assert counts(r) == (2, 0, 1) and "never-written" in r.stdout, r.stdout
    assert "SET ASIDE" not in r.stdout and "RESTORE INCOMPLETE" not in r.stdout, r.stdout
    assert (root / "wiki/crlf.md").read_bytes() == before["wiki/crlf.md"] + b"HUMAN EDIT\n"
    log = json.loads((A.bdir(root, rid) / "apply-log.json").read_text(encoding="utf-8"))
    assert log["result"] == "restored", log["result"]
    d = run(root, "--alias-only")
    assert d.returncode == 0 and "WARNING: unfinished apply" not in d.stdout, d.stdout
    print("PASS R4 concurrent human edit during a retry: human bytes kept (changed-during-apply), "
          "17 links applied; restore RESTORED 2 + never-written REVIEW 1, run settled")


def r5_landed(base, ref):
    root, before, rid = fresh(base, "r5")
    p = run_wrapped(base, root, "landed", "wiki/crlf.md", "--alias-only", "--apply")
    assert p.returncode == 0, p.stdout + p.stderr
    assert locks(p) == (1, 0.1), p.stdout
    assert (root.parent / (root.name + ".writes")).read_text() == "1", "the landed write was rewritten"
    assert pages(root) == ref
    assert "RECONCILE: manifest_safe_rewrites 18 = links_applied 18 + links_skipped_stale 0" in p.stdout
    assert ": MISMATCH" not in p.stdout, p.stdout
    log = json.loads((A.bdir(root, rid) / "apply-log.json").read_text(encoding="utf-8"))
    assert log["files"]["wiki/crlf.md"]["status"] == "written" and log["result"] == "complete"
    print("PASS R5 a refused write that had landed: counted written, one write call (never rewritten), "
          "reconcile + lint OK")


def r6_backup_conflict(base, ref):
    root, before, rid = fresh(base, "r6")
    bk = A.bdir(root, rid) / "wiki/concepts/links.md"
    bk.parent.mkdir(parents=True, exist_ok=True)
    bk.write_bytes(b"NOT THE ORIGINAL\n")
    p = run(root, "--alias-only", "--apply")
    assert p.returncode == 3 and "Traceback" not in p.stderr, p.stdout + p.stderr
    assert "backup failed for wiki/concepts/links.md (BackupConflict" in p.stdout, p.stdout
    assert bk.read_bytes() == b"NOT THE ORIGINAL\n", "a conflicting backup was overwritten"
    assert (root / "wiki/concepts/links.md").read_bytes() == before["wiki/concepts/links.md"]
    assert (root / "wiki/crlf.md").read_bytes() == before["wiki/crlf.md"], "file after the stop written"
    r = printed_restore(p.stdout, root)
    assert r.returncode == 0 and counts(r) == (1, 2, 0), r.stdout + r.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())
    # an existing backup byte-identical to the original is not a conflict
    root, before, rid = fresh(base, "r6ok")
    bk = A.bdir(root, rid) / "wiki/bom.md"
    bk.parent.mkdir(parents=True, exist_ok=True)
    bk.write_bytes(before["wiki/bom.md"])
    p = run(root, "--alias-only", "--apply")
    assert p.returncode == 0, p.stdout + p.stderr
    assert pages(root) == ref and bk.read_bytes() == before["wiki/bom.md"]
    print("PASS R6 backup conflict stops the batch (exit 3), the differing backup is kept; an "
          "identical existing backup is accepted (rc 0)")


def r7_non_lock(base):
    for mode in ("nolock", "otherwin"):
        root, before, rid = fresh(base, "r7-" + mode)
        t0 = time.monotonic()
        p = run_wrapped(base, root, mode, "wiki/crlf.md", "--alias-only", "--apply")
        took = time.monotonic() - t0
        assert p.returncode == 3 and "Traceback" not in p.stderr, p.stdout + p.stderr
        assert "write failed for wiki/crlf.md" in p.stdout, p.stdout
        assert locks(p) == (0, 0.0), p.stdout
        assert (root.parent / (root.name + ".writes")).read_text() == "1", "a non-lock error was retried"
        assert took < 6, took
        r = printed_restore(p.stdout, root)
        assert r.returncode == 0 and all((root / k).read_bytes() == b for k, b in before.items())
    print("PASS R7 non-lock errors fail at once: no winerror and winerror 1224 -> exit 3, one write "
          "attempt, LOCK RETRIES: 0")


def r8_restore(base):
    root, before, rid = fresh(base, "r8")
    assert run(root, "--alias-only", "--apply").returncode == 0
    r = restore(root, rid, faults={"restore:wiki/crlf.md": 2, "bread:wiki/bom.md": 1,
                                   "read:wiki/concepts/links.md": 1})
    assert r.returncode == 0 and counts(r) == (3, 0, 0), r.stdout + r.stderr
    assert locks(r) == (4, 0.3), r.stdout
    assert all((root / k).read_bytes() == b for k, b in before.items())
    # exhausted: the page stays as the apply wrote it, the others restored
    root, before, rid = fresh(base, "r8x")
    assert run(root, "--alias-only", "--apply").returncode == 0
    applied = pages(root)
    r = restore(root, rid, faults={"restore:wiki/crlf.md": 7})
    assert r.returncode == 1 and counts(r) == (2, 0, 1), r.stdout + r.stderr
    assert "wiki/crlf.md  (restore-write-failed (LockExhausted" in r.stdout, r.stdout
    assert "RESTORE INCOMPLETE: 1 file(s)" in r.stdout and "SET ASIDE" not in r.stdout, r.stdout
    assert locks(r) == (6, 6.3), r.stdout
    assert (root / "wiki/crlf.md").read_bytes() == applied["wiki/crlf.md"]
    d = run(root, "--alias-only")
    assert f"WARNING: unfinished apply {rid}" not in d.stdout  # a complete apply is settled
    r2 = printed_restore(r.stdout, root)
    assert r2.returncode == 0 and counts(r2) == (1, 2, 0), r2.stdout + r2.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())
    # R4 stop (owner ruling 2): the FIRST page's write exhausts ->
    # restore stops; the two later pages are never written (one write
    # attempt budget spent on bom.md only), listed restore-not-attempted.
    root, before, rid = fresh(base, "r8s")
    assert run(root, "--alias-only", "--apply").returncode == 0
    applied = pages(root)
    r = restore(root, rid, faults={"restore:wiki/bom.md": 7})
    assert r.returncode == 1 and counts(r) == (0, 0, 3), r.stdout + r.stderr
    assert "wiki/bom.md  (restore-write-failed (LockExhausted" in r.stdout, r.stdout
    for rel in ("wiki/concepts/links.md", "wiki/crlf.md"):
        assert f"{rel}  (restore-not-attempted (restore stopped at wiki/bom.md" in r.stdout, r.stdout
    assert "RESTORE INCOMPLETE: 3 file(s)" in r.stdout and "SET ASIDE" not in r.stdout, r.stdout
    assert locks(r) == (6, 6.3), r.stdout
    assert all((root / k).read_bytes() == applied[k] for k in applied), "a page was written after the stop"
    last = json.loads(next((root.parent / "r8s.state").rglob("apply-log.json")).read_text(encoding="utf-8"))
    assert last.get("result") != "restored" and len(last["restores"][-1]["review_required"]) == 3, last
    r2 = printed_restore(r.stdout, root)
    assert r2.returncode == 0 and counts(r2) == (3, 0, 0), r2.stdout + r2.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())
    r3 = printed_restore(r.stdout, root)
    assert r3.returncode == 0 and counts(r3) == (0, 3, 0), r3.stdout + r3.stderr
    # mid-order stop: the MIDDLE page's write exhausts (winerror 32) -> the
    # page before it IS restored, the page after it is not attempted.
    root, before, rid = fresh(base, "r8m")
    assert run(root, "--alias-only", "--apply").returncode == 0
    applied = pages(root)
    r = restore(root, rid, faults={"restore:wiki/concepts/links.md": {"n": 7, "code": 32}})
    assert r.returncode == 1 and counts(r) == (1, 0, 2), r.stdout + r.stderr
    assert (root / "wiki/bom.md").read_bytes() == before["wiki/bom.md"], "page before the stop not restored"
    assert (root / "wiki/concepts/links.md").read_bytes() == applied["wiki/concepts/links.md"]
    assert (root / "wiki/crlf.md").read_bytes() == applied["wiki/crlf.md"], "page after the stop was written"
    assert "wiki/crlf.md  (restore-not-attempted (restore stopped at wiki/concepts/links.md" in r.stdout, r.stdout
    r2 = printed_restore(r.stdout, root)
    assert r2.returncode == 0 and counts(r2) == (2, 1, 0), r2.stdout + r2.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())
    r3 = printed_restore(r.stdout, root)
    assert r3.returncode == 0 and counts(r3) == (0, 3, 0), r3.stdout + r3.stderr
    print("PASS R8 restore retries page write / page read / backup read (LOCK RETRIES: 4); an "
          "exhausted restore write is REVIEW restore-write-failed + RESTORE INCOMPLETE (no SET ASIDE), "
          "the re-run finishes; an exhausted FIRST page STOPS the restore (later pages "
          "restore-not-attempted, unwritten; re-run 3/0/0, third 0/3/0)")


def r10_restore_rules(base):
    """The restore mirror of R4a: a human edit during a restore retry is left
    alone; a refused restore write that had landed counts RESTORED with one
    write; an exhausted page read is transient; the unfinished-run check
    fails CLOSED on a page it cannot read."""
    # (a) concurrent human edit while a locked restore write is retried
    root, before, rid = fresh(base, "r10a")
    assert run(root, "--alias-only", "--apply").returncode == 0
    applied = pages(root)
    r = run_wrapped(base, root, "human-edit", "wiki/crlf.md", "--alias-only", "--restore", rid,
                    faults={"restore:wiki/crlf.md": 1})
    assert r.returncode == 1 and counts(r) == (2, 0, 1), r.stdout + r.stderr
    assert "wiki/crlf.md  (changed-during-restore" in r.stdout and locks(r) == (1, 0.1), r.stdout
    assert (root / "wiki/crlf.md").read_bytes() == applied["wiki/crlf.md"] + b"HUMAN EDIT\n"
    # (b) a refused restore write that had in fact landed
    root, before, rid = fresh(base, "r10b")
    assert run(root, "--alias-only", "--apply").returncode == 0
    r = run_wrapped(base, root, "landed", "wiki/crlf.md", "--alias-only", "--restore", rid)
    assert r.returncode == 0 and counts(r) == (3, 0, 0) and locks(r) == (1, 0.1), r.stdout + r.stderr
    assert (root.parent / (root.name + ".writes")).read_text() == "1", "the landed restore was rewritten"
    assert all((root / k).read_bytes() == b for k, b in before.items())
    # (c) a page read locked past the retry: transient, re-run finishes
    root, before, rid = fresh(base, "r10c")
    assert run(root, "--alias-only", "--apply").returncode == 0
    r = restore(root, rid, faults={"read:wiki/crlf.md": 7})
    assert r.returncode == 1 and counts(r) == (2, 0, 1), r.stdout + r.stderr
    assert "wiki/crlf.md  (restore-read-failed" in r.stdout, r.stdout
    assert "RESTORE INCOMPLETE: 1 file(s)" in r.stdout and "SET ASIDE" not in r.stdout, r.stdout
    r2 = printed_restore(r.stdout, root)
    assert r2.returncode == 0 and counts(r2) == (1, 2, 0), r2.stdout + r2.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())
    # (d) the unfinished-run check cannot read a page of a failed apply:
    # listed as live (cannot prove otherwise), never silently skipped
    root, before, rid = fresh(base, "r10d")
    p = run(root, "--alias-only", "--apply", env_extra={A.FAULT_ENV: "wiki/crlf.md"})
    assert p.returncode == 3, p.stdout + p.stderr
    (root / "wiki/bom.md").write_bytes(before["wiki/bom.md"])  # put one page back by hand
    d = run(root, "--alias-only")
    assert f"WARNING: unfinished apply {rid} (postcondition-failed) still has 2 files" in d.stdout, d.stdout
    d = run(root, "--alias-only", faults={"read:wiki/bom.md": 7})
    assert f"WARNING: unfinished apply {rid} (postcondition-failed) still has 3 files" in d.stdout, d.stdout
    assert "written-by-unfinished-apply  wiki/bom.md" in d.stdout, d.stdout
    print("PASS R10 restore rules: human edit during a restore retry left alone (changed-during-"
          "restore); a landed restore write counts RESTORED with one write; an exhausted page read is "
          "restore-read-failed + RESTORE INCOMPLETE and a re-run finishes; the unfinished-run check "
          "lists an unreadable page as live (fail closed)")


def r11_gap_edits(base, ref):
    """A human save in the gap an EARLIER lock backoff opens between the
    tool's hash check and the write is never overwritten: (a) apply -- the
    page's backup write is retried and the page is saved during that sleep;
    (b) restore -- restore_plan hashed every page up front, an earlier page's
    restore write is retried, and a LATER page is saved during that sleep."""
    root, before, rid = fresh(base, "r11a")
    p = run_wrapped(base, root, "human-edit", "wiki/crlf.md", "--alias-only", "--apply",
                    faults={"backup:wiki/crlf.md": 1})
    assert p.returncode == 0, p.stdout + p.stderr
    assert "changed-during-apply  wiki/crlf.md" in p.stdout and locks(p) == (1, 0.1), p.stdout
    assert (root / "wiki/crlf.md").read_bytes() == before["wiki/crlf.md"] + b"HUMAN EDIT\n", \
        "the human edit made during the backup retry was overwritten"
    assert "RECONCILE: manifest_safe_rewrites 18 = links_applied 17 + links_skipped_stale 1" in p.stdout
    for rel in ("wiki/bom.md", "wiki/concepts/links.md"):
        assert (root / rel).read_bytes() == ref[rel], rel
    r = restore(root, rid)
    assert counts(r) == (2, 0, 1) and "never-written" in r.stdout, r.stdout
    assert (root / "wiki/crlf.md").read_bytes() == before["wiki/crlf.md"] + b"HUMAN EDIT\n"
    # (b) restore: lock on bom.md (first), the human saves crlf.md (last)
    root, before, rid = fresh(base, "r11b")
    assert run(root, "--alias-only", "--apply").returncode == 0
    applied = pages(root)
    r = run_wrapped(base, root, "human-edit", "wiki/crlf.md", "--alias-only", "--restore", rid,
                    faults={"restore:wiki/bom.md": 1})
    assert r.returncode == 1 and counts(r) == (2, 0, 1), r.stdout + r.stderr
    assert "wiki/crlf.md  (changed-during-restore" in r.stdout and locks(r) == (1, 0.1), r.stdout
    assert (root / "wiki/crlf.md").read_bytes() == applied["wiki/crlf.md"] + b"HUMAN EDIT\n", \
        "the human edit made during an earlier page's restore retry was overwritten"
    for rel in ("wiki/bom.md", "wiki/concepts/links.md"):
        assert (root / rel).read_bytes() == before[rel], rel
    print("PASS R11 a human save during an earlier lock backoff is never overwritten: apply (backup "
          "retry) -> changed-during-apply, restore (earlier page's retry) -> changed-during-restore; "
          "the edit survives both")


def r12_state_saves(base):
    """R4(c) beyond the apply log: restore's own ledger save, the registry
    (state-dirs.json) and the manifest each retry a lock."""
    # (a) restore ledger save: 2 locks absorbed
    root, before, rid = fresh(base, "r12a")
    assert run(root, "--alias-only", "--apply").returncode == 0
    r = restore(root, rid, faults={"state:apply-log.json": 2})
    assert r.returncode == 0 and counts(r) == (3, 0, 0) and locks(r) == (2, 0.3), r.stdout + r.stderr
    log = json.loads((A.bdir(root, rid) / "apply-log.json").read_text(encoding="utf-8"))
    assert len(log["restores"]) == 1 and log["result"] == "restored", log.get("restores")
    # (b) restore ledger save exhausted: exit 3 + RESTORE; the re-run records it
    root, before, rid = fresh(base, "r12b")
    assert run(root, "--alias-only", "--apply").returncode == 0
    r = restore(root, rid, faults={"state:apply-log.json": 7})
    assert r.returncode == 3 and "restore ledger could not be saved" in r.stderr, r.stdout + r.stderr
    assert "RESTORE: " in r.stdout and locks(r) == (6, 6.3), r.stdout
    assert all((root / k).read_bytes() == b for k, b in before.items())
    r2 = printed_restore(r.stdout, root)
    assert r2.returncode == 0 and counts(r2) == (0, 3, 0), r2.stdout + r2.stderr
    # (c) the registry save of a --state-dir run retries a lock
    root, before, rid = fresh(base, "r12c")
    reg = Path(os.environ["LOCALAPPDATA"]) / "WIKIllm" / "alias-fix"
    p = run(root, "--alias-only", "--apply", faults={"state:state-dirs.json": 1})
    assert p.returncode == 0 and locks(p) == (1, 0.1), p.stdout + p.stderr
    assert any(reg.glob("r12c-*/state-dirs.json")), sorted(reg.iterdir())
    # (d) the manifest save of a dry run retries a lock (no retry -> exit 2)
    root = base / "r12d"
    A.build(root)
    p = run(root, "--alias-only", faults={"state:manifest.json": 1})
    assert p.returncode == 0 and (A.state_of(root) / "manifest.json").is_file(), p.stdout + p.stderr
    print("PASS R12 state saves retry a lock: restore ledger (2 absorbed; 7 -> exit 3 + RESTORE, "
          "re-run ALREADY 3), state-dirs.json, manifest.json")


def r13_locked_recheck(base, ref):
    """R4a between two attempts of the SAME write: the write is refused, a
    human saves during the backoff, and the re-hash read is itself refused
    by a lock. A locked read proves nothing, so the tool must wait and
    re-read -- never write blind. (a) apply -> changed-during-apply;
    (b) restore -> changed-during-restore (REVIEW); (c) a backup -> the
    planted bytes are a BackupConflict, never rewritten; (d) the read stays
    locked past the budget -> LockExhausted exit 3, the human edit kept,
    also through the printed RESTORE."""
    page = "wiki/crlf.md"
    # (a) apply
    root, before, rid = fresh(base, "r13a")
    p = run_wrapped(base, root, "human-edit-lockread:1", page, "--alias-only", "--apply",
                    faults={f"page:{page}": 1})
    assert p.returncode == 0, p.stdout + p.stderr
    assert (root / page).read_bytes() == before[page] + b"HUMAN EDIT\n", \
        "a locked re-hash read let the retry overwrite the human edit (apply)"
    assert f"changed-during-apply  {page}" in p.stdout and locks(p) == (2, 0.3), p.stdout
    assert "RECONCILE: manifest_safe_rewrites 18 = links_applied 17 + links_skipped_stale 1" in p.stdout
    for rel in ("wiki/bom.md", "wiki/concepts/links.md"):
        assert (root / rel).read_bytes() == ref[rel], rel
    # (b) restore
    root, before, rid = fresh(base, "r13b")
    assert run(root, "--alias-only", "--apply").returncode == 0
    applied = pages(root)
    r = run_wrapped(base, root, "human-edit-lockread:1", page, "--alias-only", "--restore", rid,
                    faults={f"restore:{page}": 1})
    assert (root / page).read_bytes() == applied[page] + b"HUMAN EDIT\n", \
        "a locked re-hash read let the restore retry overwrite the human edit"
    assert r.returncode == 1 and counts(r) == (2, 0, 1), r.stdout + r.stderr
    assert f"{page}  (changed-during-restore" in r.stdout and locks(r) == (2, 0.3), r.stdout
    # (c) backup: a file appears at the backup path during the backup's
    # retry and the re-read is locked -> conflict, never rewritten
    root, before, rid = fresh(base, "r13c")
    bk = A.bdir(root, rid) / page
    p = run_wrapped(base, root, "human-edit-lockread:1", str(bk), "--alias-only", "--apply",
                    faults={f"backup:{page}": 1})
    assert bk.read_bytes() == b"HUMAN EDIT\n", "a locked re-read let the backup retry rewrite it blind"
    assert p.returncode == 3 and f"backup failed for {page} (BackupConflict" in p.stdout, p.stdout + p.stderr
    assert (root / page).read_bytes() == before[page]
    assert locks(p) == (2, 0.3), p.stdout
    # (d) the re-hash read stays locked past the budget: clean stop, exit 3
    root, before, rid = fresh(base, "r13d")
    p = run_wrapped(base, root, "human-edit-lockread:6", page, "--alias-only", "--apply",
                    faults={f"page:{page}": 1})
    assert (root / page).read_bytes() == before[page] + b"HUMAN EDIT\n", "human edit overwritten"
    assert p.returncode == 3 and "Traceback" not in p.stderr, p.stdout + p.stderr
    assert f"write failed for {page} (LockExhausted" in p.stdout and locks(p) == (6, 6.3), p.stdout
    assert "RESTORE: " in p.stdout, p.stdout
    r = printed_restore(p.stdout, root)
    assert (root / page).read_bytes() == before[page] + b"HUMAN EDIT\n", "restore overwrote the human edit"
    assert "Traceback" not in r.stderr, r.stdout + r.stderr
    for rel in ("wiki/bom.md", "wiki/concepts/links.md"):
        assert (root / rel).read_bytes() == before[rel], rel
    print("PASS R13 a locked re-hash read between two write attempts never writes blind: apply "
          "changed-during-apply, restore changed-during-restore, backup BackupConflict (planted bytes "
          "kept), a read locked past the budget -> LockExhausted exit 3; the human edit survives all "
          "four (and the printed RESTORE)")


def r14_state_reads(base):
    """R4 on state READS: the apply log, the registry and the manifest retry
    a lock. A transient lock on apply-log.json must not make restore refuse,
    nor make a dry run / --verify report a live run as an unreadable log
    with the set-aside remedy; a lock outlasting the retry still fails
    closed (restore rc 2, nothing written)."""
    log_lock = {"stateread:apply-log.json": 2}
    root, before, rid = fresh(base, "r14")
    p = run(root, "--alias-only", "--apply", env_extra={A.FAULT_ENV: "wiki/crlf.md"})
    assert p.returncode == 3, p.stdout + p.stderr
    written = pages(root)
    d = run(root, "--alias-only", faults=log_lock)
    assert d.returncode == 0 and f"WARNING: unfinished apply {rid}" in d.stdout, d.stdout + d.stderr
    assert "unreadable log" not in d.stdout and "set the run aside" not in d.stdout, d.stdout
    v = run(root, "--alias-only", "--verify", faults=log_lock)
    assert v.returncode == 1 and "still have writes in the vault" in v.stdout, v.stdout + v.stderr
    assert "unreadable apply log" not in v.stdout and "set the run aside" not in v.stdout, v.stdout
    x = restore(root, rid, faults={"stateread:apply-log.json": 7})
    assert x.returncode == 2 and "unreadable apply log" in x.stdout + x.stderr, x.stdout + x.stderr
    assert "lock retries exhausted" in x.stdout + x.stderr, x.stdout + x.stderr
    assert pages(root) == written, "an exhausted log read still wrote the vault"
    r = restore(root, rid, faults=log_lock)
    assert r.returncode == 0 and counts(r) == (3, 0, 0), r.stdout + r.stderr
    assert locks(r) == (2, 0.3), r.stdout
    assert all((root / k).read_bytes() == b for k, b in before.items())
    # the registry (state-dirs.json) and the manifest reads of an apply
    for name in ("state-dirs.json", "manifest.json"):
        root, before, rid = fresh(base, "r14-" + name.split(".")[0])
        p = run(root, "--alias-only", "--apply", faults={f"stateread:{name}": 1})
        assert p.returncode == 0 and "unreadable manifest" not in p.stdout + p.stderr, p.stdout + p.stderr
        assert "unreadable or malformed" not in p.stdout, p.stdout
        assert locks(p) == (1, 0.1), (name, p.stdout)
    print("PASS R14 state reads retry a lock: apply-log.json (dry run / --verify see the live run, no "
          "set-aside text; restore RESTORED 3 with LOCK RETRIES: 2; exhausted -> rc 2, nothing "
          "written), state-dirs.json, manifest.json")


def r15_table_check_read(base):
    """The table-shape postcondition's re-read of a written page (the THIRD
    'read' of wiki/bom.md: TOCTOU, recount, table check) outlasting the lock
    retry must stop the batch (exit 3, POSTCONDITION FAILED, RESTORE) -- it
    was swallowed ('recount above already read these bytes'), so the batch
    committed with the table check skipped and --apply exited 0."""
    root, before, rid = fresh(base, "r15")
    p = run(root, "--alias-only", "--apply", faults={"read:wiki/bom.md": {"n": 7, "skip": 2}})
    assert p.returncode == 3 and "Traceback" not in p.stderr, p.stdout + p.stderr
    assert "POSTCONDITION FAILED" in p.stdout and "table check could not re-read wiki/bom.md" in p.stdout \
        and "LockExhausted" in p.stdout, p.stdout
    assert locks(p) == (6, 6.3) and "RESTORE: " in p.stdout, p.stdout
    log = json.loads((A.bdir(root, rid) / "apply-log.json").read_text(encoding="utf-8"))
    assert [b["status"] for b in log["batches"]] == ["failed"], log["batches"]
    assert log["result"] == "postcondition-failed", log["result"]
    r = printed_restore(p.stdout, root)
    assert r.returncode == 0 and counts(r) == (3, 0, 0), r.stdout + r.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())
    r2 = restore(root, rid)
    assert r2.returncode == 0 and counts(r2) == (0, 3, 0), r2.stdout + r2.stderr
    print("PASS R15 an exhausted lock on the table-check re-read stops the batch: exit 3, POSTCONDITION "
          "FAILED 'table check could not re-read', batch 'failed', RESTORE 3/0/0 byte-exact, 2nd 0/3/0")


def r16_reapply_first_save(base):
    """A re-apply of the same run_id (the full run after a --max-files smoke
    batch) whose FIRST write-ahead save outlasts the retry wrote nothing, so
    it must say 'nothing to restore' and print NO RESTORE line: the earlier
    settled log still at apply-log.json made the old tool print one, and
    running it undid the smoke batch the user meant to keep."""
    root, before, rid = fresh(base, "r16")
    s = run(root, "--alias-only", "--apply", "--max-files", "1")
    assert s.returncode == 0, s.stdout + s.stderr
    smoke = pages(root)
    log_path = A.bdir(root, rid) / "apply-log.json"
    smoke_log = log_path.read_bytes()
    assert json.loads(smoke_log)["result"] == "partial"
    p = run(root, "--alias-only", "--apply", faults={"state:apply-log.json": 7})
    assert p.returncode == 3 and "Traceback" not in p.stderr, p.stdout + p.stderr
    assert "APPLY LOG SAVE FAILED before any write of this batch" in p.stdout, p.stdout
    assert "nothing to restore" in p.stdout and "RESTORE: " not in p.stdout, p.stdout
    assert "earlier apply of this run_id" in p.stdout, p.stdout
    assert pages(root) == smoke, "the failed re-apply changed the vault"
    assert log_path.read_bytes() == smoke_log, "the smoke batch's settled log changed"
    # 'fix the cause, then re-run --apply' really works
    f = run(root, "--alias-only", "--apply")
    assert f.returncode == 0 and "RECONCILE FAILED" not in f.stdout, f.stdout + f.stderr
    r = restore(root, rid)
    c = counts(r)
    assert r.returncode == 0 and c[2] == 0 and c[0] + c[1] == 3 and c[0] == 3, r.stdout + r.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())
    r2 = restore(root, rid)
    assert r2.returncode == 0 and counts(r2) == (0, 3, 0), r2.stdout + r2.stderr
    print("PASS R16 re-apply after a smoke batch, first write-ahead save exhausted: exit 3, 'nothing to "
          "restore', no RESTORE line, vault + settled smoke log unchanged; the re-run apply then "
          "succeeds and restore accounts 3/0/0, 2nd 0/3/0")


def r17_present(base, ref):
    """R4a 'already exactly the intended output -> counted as written, no
    rewrite' BEFORE the first write attempt ('present'): the page reaches
    the planned bytes during an earlier lock backoff. (a) apply: the page's
    backup write is retried and the page is set to the planned bytes in
    that sleep -> status written, no write call, exact reconcile + lint; (b)
    restore: an earlier page's restore write is retried and a later page
    goes back to its original in that sleep -> ALREADY_ORIGINAL, no write,
    REVIEW_REQUIRED 0 (a 'present -> changed' mutant makes both REVIEW)."""
    page = "wiki/crlf.md"
    root, before, rid = fresh(base, "r17a")
    want = base / "r17a-want"
    want.write_bytes(ref[page])
    p = run_wrapped(base, root, f"present:{want}", page, "--alias-only", "--apply",
                    faults={f"backup:{page}": 1})
    assert p.returncode == 0, p.stdout + p.stderr
    assert not (root.parent / (root.name + ".writes")).exists(), "the already-present page was rewritten"
    assert "changed-during-apply" not in p.stdout and locks(p) == (1, 0.1), p.stdout
    assert "RECONCILE: manifest_safe_rewrites 18 = links_applied 18 + links_skipped_stale 0" in p.stdout, p.stdout
    assert ": MISMATCH" not in p.stdout, p.stdout
    log = json.loads((A.bdir(root, rid) / "apply-log.json").read_text(encoding="utf-8"))
    assert log["files"][page]["status"] == "written" and log["result"] == "complete", log["files"][page]
    assert pages(root) == ref
    r = restore(root, rid)
    assert r.returncode == 0 and counts(r) == (3, 0, 0), r.stdout + r.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())
    # (b) restore
    root, before, rid = fresh(base, "r17b")
    assert run(root, "--alias-only", "--apply").returncode == 0
    orig = base / "r17b-orig"
    orig.write_bytes(before[page])
    r = run_wrapped(base, root, f"present:{orig}", page, "--alias-only", "--restore", rid,
                    faults={"restore:wiki/bom.md": 1})
    assert r.returncode == 0 and counts(r) == (2, 1, 0), r.stdout + r.stderr
    assert not (root.parent / (root.name + ".writes")).exists(), "the already-original page was rewritten"
    assert locks(r) == (1, 0.1), r.stdout
    assert all((root / k).read_bytes() == b for k, b in before.items())
    r2 = restore(root, rid)
    assert r2.returncode == 0 and counts(r2) == (0, 3, 0), r2.stdout + r2.stderr
    print("PASS R17 'present' (already the intended bytes before the first attempt): apply counts it "
          "written with no write call (reconcile 18 = 18, lint OK); restore counts it ALREADY_ORIGINAL "
          "with no write (2/1/0, REVIEW 0), 2nd restore 0/3/0")


def r18_errno_only(base):
    """A read refused WITHOUT a lock winerror -- the shape CPython's open()
    gives a real Windows sharing violation (PermissionError errno 13,
    winerror None; only os.replace carries winerror 5) -- is never retried
    (R4 retries winerror 5/32/33 only), and is never read as a human edit
    or as 'not live' either. (a) the hook's errno-only fault has exactly
    that shape; (b) apply's pre-write re-hash read -> the batch STOPS (exit
    3, RESTORE), never changed-during-apply + carry on with rc 0; (c) a
    page write's retry re-hash read, and (c2) a backup's, fail at once
    (exit 3), never a false changed / BackupConflict; (d) restore's page
    read and (e) backup read -> transient restore-read-failed + RESTORE
    INCOMPLETE, never 'changed-since-apply' / 'backup-missing' + SET ASIDE;
    a re-run settles; (f) the unfinished-run check lists the page live
    (fail closed) and --apply refuses."""
    for sub in R18_CASES:
        sub(base)
    print("PASS R18 errno-only refusals (PermissionError 13, winerror None -- CPython's shape for a real "
          "Windows sharing violation on open()): never retried (LOCK RETRIES 0), never a human edit: apply "
          "re-hash read / write recheck / backup recheck -> exit 3 + RESTORE (no changed-during-apply, no "
          "BackupConflict, the batch stops); restore page / backup read -> restore-read-failed + RESTORE "
          "INCOMPLETE (no SET ASIDE), re-run settles 1/2/0; the unfinished-run check lists it live and "
          "--apply refuses")


EO = {"n": 1, "code": None}  # one errno-only refusal


def r18a_hook_shape(base):
    import errno
    sys.path.insert(0, str(SCRIPTS))
    import fix_wikilinks as fw
    os.environ[LOCKS] = json.dumps({"read:x": {"n": 1, "code": None}})
    try:
        fw.arm_lock_faults(base)
        try:
            fw._inject_lock("read", "x", base / "x")
            raise AssertionError("the errno-only fault did not fire")
        except PermissionError as e:
            assert e.errno == errno.EACCES and getattr(e, "winerror", None) is None and not fw._is_lock(e), e
    finally:
        os.environ.pop(LOCKS, None)
        fw._LOCKS["faults"] = None


def r18b_apply_read(base):
    eo = EO
    root, before, rid = fresh(base, "r18b")
    p = run(root, "--alias-only", "--apply", faults={"read:wiki/concepts/links.md": eo})
    assert p.returncode == 3 and "Traceback" not in p.stderr, p.stdout + p.stderr
    assert "read failed for wiki/concepts/links.md (PermissionError" in p.stdout, p.stdout
    assert "changed-during-apply" not in p.stdout and locks(p) == (0, 0.0), p.stdout
    assert (root / "wiki/bom.md").read_bytes() != before["wiki/bom.md"]
    assert (root / "wiki/crlf.md").read_bytes() == before["wiki/crlf.md"], "the batch carried on past the read"
    r = printed_restore(p.stdout, root)
    assert r.returncode == 0 and counts(r) == (1, 2, 0), r.stdout + r.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())


def r18c_write_recheck(base):
    eo = EO
    root, before, rid = fresh(base, "r18c")
    p = run(root, "--alias-only", "--apply", faults={"page:wiki/crlf.md": 1, "recheck:wiki/crlf.md": eo})
    assert p.returncode == 3 and "Traceback" not in p.stderr, p.stdout + p.stderr
    assert "write failed for wiki/crlf.md (PermissionError" in p.stdout, p.stdout
    assert "changed-during-apply" not in p.stdout and locks(p) == (1, 0.1), p.stdout
    assert (root / "wiki/crlf.md").read_bytes() == before["wiki/crlf.md"]
    r = printed_restore(p.stdout, root)
    assert r.returncode == 0 and counts(r) == (2, 1, 0), r.stdout + r.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())


def r18c2_backup_recheck(base):
    eo = EO
    root, before, rid = fresh(base, "r18c2")
    p = run(root, "--alias-only", "--apply", faults={"backup:wiki/crlf.md": 1, "brecheck:wiki/crlf.md": eo})
    assert p.returncode == 3 and "backup failed for wiki/crlf.md (PermissionError" in p.stdout, p.stdout + p.stderr
    assert "BackupConflict" not in p.stdout, p.stdout
    r = printed_restore(p.stdout, root)
    assert r.returncode == 0 and all((root / k).read_bytes() == b for k, b in before.items()), r.stdout


def r18de_restore_reads(base):
    eo = EO
    # Restore order is wiki/bom.md, wiki/concepts/links.md, wiki/crlf.md.
    # (d) the LAST page's read fails: the two before it are restored.
    # (e) the FIRST page's backup read fails: restore STOPS there (R4, the owner
    # owner ruling 2) -- the two later pages are restore-not-attempted,
    # still hold the apply's bytes, and the re-run restores all three.
    for tag, fault, why, first, later in (
            ("d", "read:wiki/crlf.md", "wiki/crlf.md  (restore-read-failed (page: PermissionError",
             (2, 0, 1), ()),
            ("e", "bread:wiki/bom.md", "wiki/bom.md  (restore-read-failed (backup: PermissionError",
             (0, 0, 3), ("wiki/concepts/links.md", "wiki/crlf.md"))):
        root, before, rid = fresh(base, "r18" + tag)
        assert run(root, "--alias-only", "--apply").returncode == 0
        applied = pages(root)
        r = restore(root, rid, faults={fault: eo})
        assert r.returncode == 1 and counts(r) == first, (tag, r.stdout + r.stderr)
        assert why in r.stdout, (tag, r.stdout)
        n = first[2]
        assert f"RESTORE INCOMPLETE: {n} file(s)" in r.stdout and "SET ASIDE" not in r.stdout, (tag, r.stdout)
        assert "changed-since-apply" not in r.stdout and "backup-missing" not in r.stdout, (tag, r.stdout)
        assert locks(r) == (0, 0.0), r.stdout
        left = fault.split(":", 1)[1]
        for rel in (left,) + later:
            assert (root / rel).read_bytes() == applied[rel], (tag, rel, "written after the stop")
        for rel in later:
            assert f"{rel}  (restore-not-attempted (restore stopped at {left}" in r.stdout, (tag, r.stdout)
        r2 = printed_restore(r.stdout, root)
        assert r2.returncode == 0 and counts(r2) == (n, 3 - n, 0), (tag, r2.stdout + r2.stderr)
        assert all((root / k).read_bytes() == b for k, b in before.items())
        r3 = printed_restore(r.stdout, root)
        assert r3.returncode == 0 and counts(r3) == (0, 3, 0), (tag, r3.stdout + r3.stderr)


def r18f_unfinished(base):
    eo = EO
    root, before, rid = fresh(base, "r18f")
    p = run(root, "--alias-only", "--apply", env_extra={A.FAULT_ENV: "wiki/crlf.md"})
    assert p.returncode == 3, p.stdout + p.stderr
    (root / "wiki/bom.md").write_bytes(before["wiki/bom.md"])  # put one page back by hand
    d = run(root, "--alias-only")
    assert f"WARNING: unfinished apply {rid} (postcondition-failed) still has 2 files" in d.stdout, d.stdout
    d = run(root, "--alias-only", faults={"read:wiki/bom.md": eo})
    assert f"WARNING: unfinished apply {rid} (postcondition-failed) still has 3 files" in d.stdout, d.stdout
    assert "written-by-unfinished-apply  wiki/bom.md" in d.stdout, d.stdout
    ap = run(root, "--alias-only", "--apply", faults={"read:wiki/bom.md": eo})
    assert ap.returncode == 2 and "did not complete and still have writes" in ap.stderr, ap.stdout + ap.stderr


R18_CASES = (r18a_hook_shape, r18b_apply_read, r18c_write_recheck, r18c2_backup_recheck,
             r18de_restore_reads, r18f_unfinished)


def _hold_exclusive(path):
    """A REAL Windows sharing lock: CreateFileW with share mode 0 (what an
    editor / syncer / AV scanner holding the file open does). Returns a
    handle to close, or None off Windows."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    assert h and h != wintypes.HANDLE(-1).value, ctypes.get_last_error()
    return k32, h


def r19_real_lock(base):
    """The live path, not an injected shape: a page held open with share mode
    0 by another process during --restore. restore's read of it gets what
    CPython really raises (PermissionError errno 13, winerror None): the
    row is transient restore-read-failed with RESTORE INCOMPLETE and no SET
    ASIDE -- the pinned legacy in-vault-state tool said 'changed-since-apply', the
    mechanism of the 11-missed-files case (R5b) -- and once the lock is
    released the printed RESTORE settles the run."""
    if os.name != "nt":
        print("SKIP R19 (real Windows lock)")
        return
    root, before, rid = fresh(base, "r19")
    assert run(root, "--alias-only", "--apply").returncode == 0
    applied = pages(root)
    k32, h = _hold_exclusive(root / "wiki/crlf.md")
    try:
        try:
            (root / "wiki/crlf.md").read_bytes()
            raise AssertionError("the exclusive handle does not lock the page")
        except PermissionError as e:
            assert getattr(e, "winerror", None) is None, e  # the shape R4 never retries
        r = restore(root, rid)
    finally:
        k32.CloseHandle(h)
    assert r.returncode == 1 and counts(r) == (2, 0, 1), r.stdout + r.stderr
    assert "wiki/crlf.md  (restore-read-failed (page: PermissionError" in r.stdout, r.stdout
    assert "RESTORE INCOMPLETE: 1 file(s)" in r.stdout and "SET ASIDE" not in r.stdout, r.stdout
    assert (root / "wiki/crlf.md").read_bytes() == applied["wiki/crlf.md"]
    r2 = printed_restore(r.stdout, root)
    assert r2.returncode == 0 and counts(r2) == (1, 2, 0), r2.stdout + r2.stderr
    assert all((root / k).read_bytes() == b for k, b in before.items())
    print("PASS R19 a REAL Windows lock (CreateFileW share 0) on a page during --restore: errno 13 / "
          "winerror None -> restore-read-failed + RESTORE INCOMPLETE (no SET ASIDE, not a human edit); "
          "after release the printed RESTORE settles 1/2/0 byte-exact")


def r9_inert(base):
    root, before, rid = fresh(base, "r9")
    other = base / "not-temp"
    other.mkdir()
    p = run_wrapped(base, root, "notemp", str(other), "--alias-only", "--apply",
                    faults={"page:wiki/crlf.md": 7})
    assert p.returncode == 0, p.stdout + p.stderr
    assert "WARNING: FIX_WIKILINKS_TEST_LOCKS ignored outside a temp vault" in p.stdout, p.stdout
    assert locks(p) == (0, 0.0), p.stdout
    # default (backtick) mode never prints the line
    bt = base / "r9-bt"
    A.write_vault(bt, {"wiki/a.md": "x `[[B]]`\n", "wiki/b.md": "b\n"})
    for args in ((), ("--apply",)):
        q = run(bt, *args, faults={"page:*": 3})
        assert q.returncode == 0 and "LOCK RETRIES" not in q.stdout + q.stderr, q.stdout
    print("PASS R9 fault hook inert outside a temp dir (WARNING, 0 retries); backtick mode prints no "
          "LOCK RETRIES line")


def main():
    with tempfile.TemporaryDirectory(prefix="wikillm157-") as td:
        base = Path(td)
        A.isolate_state(base)
        real = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "AppData" / "Local" / "WIKIllm"
        before_real = real.exists()
        ref_root = base / "ref"
        A.build(ref_root)
        assert run(ref_root, "--alias-only").returncode == 0
        assert run(ref_root, "--alias-only", "--apply").returncode == 0
        ref = pages(ref_root)
        r1_success(base, ref)
        r1_codes(base, ref)
        r2_exhausted_page(base)
        r3_exhausted_journal(base)
        r4_human_edit(base, ref)
        r5_landed(base, ref)
        r6_backup_conflict(base, ref)
        r7_non_lock(base)
        r8_restore(base)
        r9_inert(base)
        r10_restore_rules(base)
        r11_gap_edits(base, ref)
        r12_state_saves(base)
        r13_locked_recheck(base, ref)
        r14_state_reads(base)
        r15_table_check_read(base)
        r16_reapply_first_save(base)
        r17_present(base, ref)
        r18_errno_only(base)
        r19_real_lock(base)
        assert real.exists() == before_real, "the real %LOCALAPPDATA%/WIKIllm changed"
    print("ALL PASS (R1-R19)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
