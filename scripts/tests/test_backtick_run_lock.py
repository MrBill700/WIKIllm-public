"""ADR-0007 / regression R168: backtick-mode fix_wikilinks.py --apply takes the SAME
per-vault run lock as the --alias-only writers (ADR-0006) -- mutual exclusion
and lock recovery only, never an alias-state transaction.

B1  cross-mode, alias holds: a backtick --apply refuses (exit 2, 'run lock is
    held', holder alive, mode apply), the vault byte-unchanged; the backtick
    DRY RUN still runs (rc 0, no lock taken)
B2  cross-mode, backtick holds (hold hook): an alias dry run / --apply and a
    second backtick --apply refuse, naming mode backtick-apply; the holder
    then applies and releases
B3  a hard-killed backtick --apply leaves a STALE lock: the next backtick
    --apply refuses naming '--alias-only ... --break-lock'; that command
    clears it; the next backtick --apply works
B4  lock unreachable -> backtick --apply refuses, nothing written anywhere:
    a Microsoft Store Python (WindowsApps sys.executable, forced by a
    wrapper) and an unset LOCALAPPDATA (Windows); the dry run is unchanged
    under both
B5  (Windows, admin share reachable) sibling check: a backtick --apply via
    \\\\localhost\\c$ refuses while an alias run holds via C:\\; its own lock
    is released and nothing is written
B6  no alias-state transaction: an uncontended backtick --apply leaves only
    the default state dir with nothing in it (no manifest, backup, vault.json,
    registry, run.lock or temp record) and prints no lock text
B7  that lock-only key dir is not 'state': an alias run on another vault
    with the same folder name gets no same-name WARNING from it, while a
    real alias-state sibling still warns (review finding 1); a key dir
    holding only an unexplained run.lock.broken-* is NOT skipped (same-name
    WARNING, rc 0 -- ADR-0008 Q5 removed the '.broken-' tolerance)
B8  (Windows icacls) the lock-only skip needs positive proof: a same-name
    sibling key dir that cannot be listed still makes an alias --apply fail
    closed (re-review N1)
B3 also pins the backtick-holder guidance (re-run the backtick dry run;
    pages it already rewrote were not backed up) in the stale refusal and
    the --break-lock next step
Run: python -B scripts/tests/test_backtick_run_lock.py      Exit 0 = pass.
LOCALAPPDATA is pointed at a temp dir; the real %LOCALAPPDATA%/WIKIllm is
never touched.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "fix_wikilinks.py"
HOOK = "FIX_WIKILINKS_TEST_RUNLOCK"
HELD = "run lock is held"
SIBLING = "another state key with this vault's folder name"
SOIL = "---\ntitle: Soil pH\naliases: [Soil pH]\n---\n# Soil pH\n"
FILES = {"wiki/concepts/soil-ph.md": SOIL,
         "wiki/tick.md": "see `[[soil-ph]]` here\n",       # backtick-mode target
         "wiki/a.md": "a [[Soil pH]]\n"}                    # alias-mode target
MSIX_EXE = (r"C:\Users\u\AppData\Local\Microsoft\WindowsApps"
            r"\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe")
WRAP = '''import sys
sys.dont_write_bytecode = True
sys.path.insert(0, {scripts!r})
import fix_wikilinks as fw
sys.executable = sys._base_executable = {fake!r}
sys.argv = ["fix_wikilinks.py"] + sys.argv[1:]
sys.exit(fw.main())
'''


def env(hook=None, drop=()):
    e = dict(os.environ)
    for k in (HOOK, "FIX_WIKILINKS_TEST_LOCKS", "FIX_WIKILINKS_TEST_FAULT", *drop):
        e.pop(k, None)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    if hook:
        e[HOOK] = json.dumps(hook)
    return e


def argv(root, *a, alias=False):
    return [sys.executable, "-B", str(SCRIPT), "--root", str(root)] + (["--alias-only"] if alias else []) + list(a)


def run(root, *a, alias=False, hook=None, drop=()):
    return subprocess.run(argv(root, *a, alias=alias), cwd=str(root), env=env(hook, drop),
                          capture_output=True, text=True, encoding="utf-8")


def start(root, *a, alias=False, hook=None):
    return subprocess.Popen(argv(root, *a, alias=alias), cwd=str(root), env=env(hook),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")


def finish(p):
    o, e = p.communicate(timeout=120)
    return p.returncode, o + e


def out(p):
    return p.stdout + p.stderr


def tree(d):
    d = Path(d)
    if not d.exists():
        return {}
    return {p.relative_to(d).as_posix(): ("<dir>" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(d.rglob("*"))}


def fresh(parent, name):
    root = Path(parent) / name
    for rel, c in FILES.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(c.encode("utf-8"))
    return root


def ddir(lad, root):
    key = os.path.normcase(str(Path(root).resolve())).replace("\\", "/")
    h8 = hashlib.sha256(key.encode()).hexdigest()[:8]
    return Path(os.path.abspath(str(lad))) / "WIKIllm" / "alias-fix" / f"{Path(root).resolve().name}-{h8}"


def wait_until(cond, what, t=60):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > t:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.02)


def check(c, msg, text=""):
    if not c:
        raise AssertionError(f"{msg}\n--- output ---\n{text}")


def ticked(root):
    return (root / "wiki/tick.md").read_text(encoding="utf-8") == "see [[soil-ph]] here\n"


def b1_alias_holds(base, lad):
    root = fresh(base, "b1")
    d = ddir(lad, root)
    assert run(root, alias=True).returncode == 0
    rel = base / "b1-release"
    p = start(root, "--apply", alias=True, hook={"hold": str(rel)})
    try:
        wait_until(lambda: (d / "run.lock").exists(), "alias holder")
        vt = tree(root)
        r = run(root, "--apply")
        o = out(r)
        check(r.returncode == 2 and HELD in o and "holder status: alive" in o and "mode apply" in o
              and "nothing written" in o, "B1 backtick --apply refused while alias holds", o)
        check(tree(root) == vt and not ticked(root), "B1 nothing written", o)
        r = run(root)
        check(r.returncode == 0 and HELD not in out(r) and "Dry run" in out(r), "B1 backtick dry run runs", out(r))
    finally:
        rel.write_text("go")
    rc, o = finish(p)
    check(rc == 0 and not (d / "run.lock").exists(), "B1 alias holder finished, released", o)
    r = run(root, "--apply")
    check(r.returncode == 0 and ticked(root), "B1 backtick --apply works after release", out(r))
    print("B1 ok -- backtick --apply refused while an alias writer holds; its dry run unaffected")


def b2_backtick_holds(base, lad):
    root = fresh(base, "b2")
    d = ddir(lad, root)
    assert run(root, alias=True).returncode == 0
    rel = base / "b2-release"
    p = start(root, "--apply", hook={"hold": str(rel)})
    try:
        wait_until(lambda: (d / "run.lock").exists(), "backtick holder")
        rec = json.loads((d / "run.lock").read_text(encoding="utf-8"))
        check(rec["mode"] == "backtick-apply" and rec["pid"] == p.pid, "B2 holder record", str(rec))
        vt, dt = tree(root), tree(d)
        for a, alias in (((), True), (("--apply",), True), (("--apply",), False)):
            r = run(root, *a, alias=alias)
            o = out(r)
            check(r.returncode == 2 and HELD in o and "mode backtick-apply" in o,
                  f"B2 {'alias' if alias else 'backtick'} {a or 'dry run'} refused", o)
            check(tree(root) == vt and tree(d) == dt, f"B2 {a}: nothing written", o)
    finally:
        rel.write_text("go")
    rc, o = finish(p)
    check(rc == 0 and ticked(root) and not (d / "run.lock").exists(), "B2 holder applied + released", o)
    print("B2 ok -- alias dry run / --apply and a second backtick --apply refused while backtick holds")


def b3_crash_recovery(base, lad):
    root = fresh(base, "b3")
    d = ddir(lad, root)
    rel = base / "b3-never"
    p = start(root, "--apply", hook={"hold": str(rel)})
    wait_until(lambda: (d / "run.lock").exists(), "backtick holder")
    p.kill()  # hard kill: no finally
    p.communicate()
    check((d / "run.lock").exists() and not ticked(root), "B3 killed holder left its lock, wrote nothing")
    r = run(root, "--apply")
    o = out(r)
    check(r.returncode == 2 and "holder status: stale" in o and "--alias-only --root" in o
          and "--break-lock" in o, "B3 stale refusal names --alias-only ... --break-lock", o)
    check("one lock-participating fix_wikilinks writer per vault" in o
          and "re-run the backtick dry run" in o and "were not backed up" in o
          and "reports any unfinished run" not in o, "B3 backtick-holder guidance, not alias guidance", o)
    r = run(root, "--break-lock", alias=True)
    check(r.returncode == 0 and "BROKE STALE LOCK" in out(r) and not (d / "run.lock").exists()
          and "Next: re-run the backtick dry run" in out(r),
          "B3 --alias-only --break-lock clears a backtick lock, backtick next step", out(r))
    r = run(root, "--apply")
    check(r.returncode == 0 and ticked(root), "B3 next backtick --apply works", out(r))
    print("B3 ok -- crashed backtick --apply: stale refusal -> --alias-only --break-lock -> apply")


def b4_unreachable(base, lad):
    if os.name != "nt":
        print("B4 SKIP -- Windows-only (Store Python / unset LOCALAPPDATA refusals)")
        return
    root = fresh(base, "b4")
    d = ddir(lad, root)
    w = base / "b4-store-wrapper.py"
    w.write_text(WRAP.format(scripts=str(SCRIPTS), fake=MSIX_EXE), encoding="utf-8")
    vt, lt = tree(root), tree(lad)
    r = subprocess.run([sys.executable, "-B", str(w), "--root", str(root), "--apply"], cwd=str(root),
                       env=env(), capture_output=True, text=True, encoding="utf-8")
    o = out(r)
    check(r.returncode == 2 and "Microsoft Store Python" in o and "python -B" in o and "nothing written" in o,
          "B4 Store Python: backtick --apply refused", o)
    check(tree(root) == vt and tree(lad) == lt, "B4 Store Python: nothing written anywhere", o)
    r = subprocess.run([sys.executable, "-B", str(w), "--root", str(root)], cwd=str(root), env=env(),
                       capture_output=True, text=True, encoding="utf-8")
    check(r.returncode == 0 and "Dry run" in out(r) and tree(root) == vt, "B4 Store Python: dry run unchanged", out(r))
    r = run(root, "--apply", drop=("LOCALAPPDATA",))
    o = out(r)
    check(r.returncode == 2 and "LOCALAPPDATA is not set" in o and tree(root) == vt and tree(lad) == lt,
          "B4 unset LOCALAPPDATA: backtick --apply refused, nothing written", o)
    r = run(root, drop=("LOCALAPPDATA",))
    check(r.returncode == 0 and "Dry run" in out(r), "B4 unset LOCALAPPDATA: dry run unchanged", out(r))
    check(not d.exists(), "B4 no default dir created")
    print("B4 ok -- Store Python and unset LOCALAPPDATA: backtick --apply exit 2, nothing written; dry run unchanged")


def share_spelling(p):
    if os.name != "nt":
        return None
    s = str(Path(p).resolve())
    if s[1:3] != ":\\":
        return None
    q = Path(f"\\\\localhost\\{s[0].lower()}$\\{s[3:]}")
    try:
        return q if q.is_dir() else None
    except OSError:
        return None


def b5_sibling(base, lad):
    root = fresh(base / "b5", "Vault")
    share = share_spelling(root)
    if share is None:
        print("B5 SKIP -- \\\\localhost\\c$ admin share unreachable")
        return
    d, ds = ddir(lad, root), ddir(lad, share)
    check(d != ds, "B5 precondition: two keys for one dir")
    assert run(root, alias=True).returncode == 0
    rel = base / "b5-release"
    p = start(root, "--apply", alias=True, hook={"hold": str(rel)})
    try:
        wait_until(lambda: (d / "run.lock").exists(), "alias holder via C:")
        vt = tree(root)
        r = run(share, "--apply")
        o = out(r)
        check(r.returncode == 2 and SIBLING in o, "B5 backtick --apply via share refused by sibling", o)
        check(not (ds / "run.lock").exists() and tree(root) == vt, "B5 own lock released, nothing written", o)
    finally:
        rel.write_text("go")
    assert finish(p)[0] == 0
    print("B5 ok -- backtick --apply via \\\\localhost\\c$ refused while an alias run holds via C:")


def b6_no_transaction(base, lad):
    root = fresh(base, "b6")
    d = ddir(lad, root)
    r = run(root, "--apply")
    o = out(r)
    check(r.returncode == 0 and ticked(root) and "lock" not in o.lower(), "B6 uncontended apply, no lock text", o)
    check(d.is_dir() and list(d.iterdir()) == [], "B6 default dir holds nothing (no manifest/backup/state/lock)",
          str(list(d.iterdir()) if d.exists() else "absent"))
    print("B6 ok -- uncontended backtick --apply: no lock text, no alias-state artifacts, lock released")


def b7_lock_only_dir_not_state(base, lad):
    """Review finding 1: the empty key dir a backtick --apply leaves is not
    'state' -- an alias run on another vault with the same folder name gets
    no same-name WARNING from it; a real alias-state sibling still warns."""
    a, b = fresh(base / "b7a", "Farm"), fresh(base / "b7b", "Farm")
    warn = "state for another vault path with the same folder name"
    r = run(b, alias=True)
    check(r.returncode == 0 and warn not in out(r), "B7 baseline: no same-name warning", out(r))
    assert run(a, "--apply").returncode == 0
    check(ddir(lad, a).is_dir(), "B7 precondition: the backtick run left its (empty) key dir")
    r = run(b, alias=True)
    check(r.returncode == 0 and warn not in out(r), "B7 lock-only key dir is not same-name state", out(r))
    # ADR-0008 Q5 (r4 review F1): the '.broken-' tolerance is gone -- a key
    # dir holding ONLY an unexplained run.lock.broken-* is no longer skipped
    # as lock-only; it falls through to the same-name checks (rc unchanged).
    bk = ddir(lad, a) / "run.lock.broken-0123abcd"
    bk.write_text("left by an older --break-lock")
    check(os.listdir(ddir(lad, a)) == [bk.name], "B7 precondition: the key dir holds ONLY run.lock.broken-*",
          str(os.listdir(ddir(lad, a))))
    try:
        r = run(b, alias=True)
        check(r.returncode == 0 and warn in out(r),
              "B7 a run.lock.broken-* only key dir is not skipped (same-name WARNING, rc 0)", out(r))
    finally:
        bk.unlink()
    assert run(a, alias=True).returncode == 0   # now A has real alias state (manifest, vault.json)
    r = run(b, alias=True)
    check(r.returncode == 0 and warn in out(r), "B7 a real alias-state sibling still warns", out(r))
    print("B7 ok -- a backtick run's lock-only key dir is not same-name state; a run.lock.broken-* "
          "only dir and real state still warn")


def b8_unlistable_sibling_blocks(base, lad):
    """Re-review N1: the lock-only skip needs POSITIVE proof. A same-folder-name
    sibling key dir that cannot be listed is never read as lock-only: an alias
    --apply still fails closed ('cannot prove there is no unfinished run')."""
    import shutil as _sh
    if os.name != "nt" or not _sh.which("icacls"):
        print("B8 SKIP -- needs Windows icacls")
        return
    a, b = fresh(base / "b8a", "Farm"), fresh(base / "b8b", "Farm")
    assert run(a, alias=True).returncode == 0          # A: real alias state (vault.json, manifest)
    assert run(a, "--apply", alias=True).returncode == 0   # ... and a run under backup/
    assert run(b, alias=True).returncode == 0          # B: its own manifest
    da = ddir(lad, a)
    who = subprocess.run(["whoami"], capture_output=True, text=True).stdout.strip()
    # Inherited READ deny (list / read data / read attributes / read EA): even
    # exists() on A's vault.json / backup reads False, so a marker-absence
    # test sees 'no state' (the re-review probe's effect). NEVER deny F here:
    # %TEMP% dirs inherit an OWNER RIGHTS ACE, so a full deny also strips the
    # owner's right to edit the ACL and /remove:d cannot undo it (observed
    # 2026-10-01: the dir needs an elevated takeown to recover).
    deny = subprocess.run(["icacls", str(da), "/deny", f"{who}:(OI)(CI)(RD,RA,REA)"],
                          capture_output=True, text=True)
    try:
        try:
            os.listdir(da)
            denied = False
        except PermissionError:
            denied = True
        if denied and (da / "vault.json").exists():
            print("B8 SKIP -- the read deny did not make exists() False on this machine")
            return
        if deny.returncode != 0 or not denied:
            print(f"B8 SKIP -- could not deny listing {da}: {deny.stdout.strip()} {deny.stderr.strip()}")
            return
        vt = tree(b)
        r = run(b, "--apply", alias=True)
        o = out(r)
        check(r.returncode == 2 and "cannot prove there is no unfinished run" in o and tree(b) == vt,
              "B8 unlistable same-name sibling still blocks alias --apply (fail closed)", o)
    finally:
        undo = subprocess.run(["icacls", str(da), "/remove:d", who], capture_output=True, text=True)
        try:
            os.listdir(da)
        except OSError as e:
            raise AssertionError(f"B8 could not undo its deny on {da} (icacls rc {undo.returncode}: "
                                 f"{undo.stdout.strip()} {undo.stderr.strip()}; {e}) -- fix its ACL by hand")
    r = run(b, "--apply", alias=True)
    check(r.returncode == 0, "B8 once listable (settled sibling): alias --apply proceeds", out(r))
    print("B8 ok -- an unlistable same-name sibling is never read as lock-only; alias --apply fails closed")


def main():
    with tempfile.TemporaryDirectory(prefix="wikillm168-", ignore_cleanup_errors=True) as td:
        base = Path(td)
        lad = base / "localappdata"
        os.environ["LOCALAPPDATA"] = str(lad)  # never the real %LOCALAPPDATA%
        for case in (b1_alias_holds, b2_backtick_holds, b3_crash_recovery, b4_unreachable,
                     b5_sibling, b6_no_transaction, b7_lock_only_dir_not_state,
                     b8_unlistable_sibling_blocks):
            case(base, lad)
    print("OK -- ADR-0007 backtick run-lock matrix passed (B1-B8)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
