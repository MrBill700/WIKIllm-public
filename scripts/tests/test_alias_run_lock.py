"""ADR-0006 / regression R161: fix_wikilinks.py --alias-only holds a per-vault run
lock (run.lock in the vault's DEFAULT state dir) around every alias-state
writer. Backtick-mode --apply joins the same lock under ADR-0007 / regression R168;
its cases live in test_backtick_run_lock.py. ADR-0008 / regression R170: on
Windows every run.lock deletion (release and --break-lock) judges and deletes
the same file object through one exclusive handle; off Windows --break-lock
refuses (the --break-lock steps of K1, K4 and K6, K3's stale refusal and
break, K8's no-lock no-op and K11-K15 are Windows-only: K11-K15 skip
elsewhere, the others would fail there; so do test_backtick_run_lock.py B3
and test_alias_journal.py's crash-recovery break).

K1  a live holder (held open by the temp-only hold hook): every lock-taking
    mode refuses at once (exit 2, 'run lock is held', holder status alive)
    -- dry run, --apply, --restore, --migrate-legacy-state [--apply],
    --rebind-state, --forget-state-dir, --verify --state-dir -- with the
    vault hash tree, the state dir tree and run.lock unchanged and no temp
    record left; plain --verify runs (no lock text); --break-lock refuses
    an alive holder; the holder then finishes rc 0 and releases the lock
K2  two --apply contenders race on os.link (gate hook: both temp records
    written first): exactly one wins, the loser exits 2 with the vault
    untouched by it and its own temp record removed; one apply log, pages
    = one apply's result, no run.lock left
K3  stale locks: a dead pid, and a live pid with another creation time
    (pid reuse), are reported STALE with the --break-lock command;
    --break-lock removes them (BROKE STALE LOCK), leaving the default state
    dir EMPTY (no artifact of any kind, ADR-0008 Q5), and the next run works
K4  unprovable holders are never broken: creation time missing, a foreign
    host, a malformed / empty lock file -- run refused, --break-lock exit 2,
    the lock file byte-identical afterwards; an orphan temp record is
    never treated as a lock; an unprovable SIBLING lock (same folder name,
    other key) refuses with hand-delete guidance and --break-lock leaves
    it; a stale sibling lock does not block
K5  hard links unsupported (hook: winerror 1) -> exit 2, no fallback, no
    run.lock, no temp record, nothing in the vault
K6  release (fault hook 'unlock' = the exclusive compare-and-delete open,
    ADR-0008 Q6): a transient lock on that open is retried (lock gone); an
    exhausted open keeps the run's exit code, prints the three WARNING
    lines and leaves a lock --break-lock then clears; a run.lock replaced
    under the holder is left untouched with a WARNING; a run that refuses
    after acquiring (--apply with no manifest) still releases
K7  --verify with --state-dir: refused while a writer holds the lock even
    when that dir is already registered; two concurrent --verify runs
    registering different new state dirs: exactly one proceeds, the
    registry then holds exactly that dir (the loser's re-run adds its own);
    a --state-dir whose vault.json names another vault is refused by the
    refusal-only pre-lock identity check, the held lock untouched
K9  (Windows, admin share reachable) sibling check: C:\\...\\Vault and
    \\\\localhost\\c$\\...\\Vault hash to two keys; a contender under one
    spelling refuses while the other holds (dry run and --apply, its own
    lock released, nothing written); two simultaneous contenders both
    refuse and release; a DIFFERENT vault with the same folder name is
    refused only while its run is live (named in the message), its lock
    is never broken by this vault's --break-lock, and once it settles this
    vault runs with only the existing same-name WARNING (runs not adopted)
K10 an uncreatable default state dir: clean exit 2, no traceback (review #3)
K11 (ADR-0008 Q7, regression R170) the break race, at the invariant: B1 runs
    in-process with _holder_status wrapped; INSIDE its stale judgment a
    real --break-lock B2 and a real --apply C (gate + hold hooks) are
    launched and the test waits on observable barriers (new code: the
    retry markers of B2's exclusive open and C's holder read; old code: B2
    exited and run.lock holds C's record), then the real verdict returns.
    B1 must act only on the record it judged (rc 0, BROKE naming the dead
    pid -- on 8ec44d0 it moves C's live lock aside: 'B1 acted on a record
    it did not judge'); C is never displaced; B2 reports 'removed
    meanwhile', C 'released or broken while ... inspecting it -- re-run';
    C's re-run is the sole holder, a D --apply and a B3 --break-lock both
    refuse against it, and after C releases no lock is left. B1 runs with
    every by-path removal of run.lock (os.unlink / remove / rename /
    replace) raising, so its delete must go through the judging handle --
    red against a close-then-unlink mutant; a direct _lock_compare_delete
    check pins true -> deleted, false -> byte-unchanged under the same guard
K12 (ADR-0008, real subprocesses, break_judged hook: a breaker paused after
    its judgment with the exclusive handle open) an acquirer reads the
    alive holder accurately once the refused break closes; a live holder's
    release during a refused break succeeds and leaves no lock; the sibling
    check during a break (stale and alive sibling) reports no false
    malformed / unprovable; an acquirer during a stale break gets the
    accurate 're-run' refusal, never 'malformed'; exclusive-open
    exhaustion: break (fault hook) rc 2 with the lock untouched, release
    (a real breaker holding past the budget) keeps the run's rc + the
    WARNING; off Windows (the _break_supported seam patched, never os.name)
    --break-lock exits 2 with the bytes unchanged, naming the path and the
    manual guidance, while acquire + release still work in-process, and a
    stale held refusal and an exhausted-release WARNING name no break
    command, only the by-hand recovery; K12f (in-process, share-0 handles
    held on this vault's run.lock and a same-folder-name sibling's past the
    retry budget, _lock_sleep stubbed) the acquirer's refusal says 'holder
    status: not judged (unreadable, not malformed)' and the sibling entry
    'not judged (unreadable, not malformed)', never 'malformed record' /
    'holder status: unprovable' (ADR-0008 Q3 note)
K13 (ADR-0008 Q4/Q6: only the exclusive open is retried) a read-only stale
    run.lock: --break-lock opens and judges it, the delete disposition is
    refused (WinError 5) -> rc 2 at once (< 4 s, never the 6.3 s retry budget) with the
    delete-refused text, the lock byte-unchanged, exactly one exclusive
    open / read / judgment; release names the refused delete; with the
    attribute cleared the same break empties the dir
K14 a file-symlinked run.lock (target: a stale record outside the state dir) is
    opened as itself (FILE_FLAG_OPEN_REPARSE_POINT): --break-lock refuses
    it as unprovable, the target and the link untouched; an acquirer gets
    the unprovable hand-delete message, never 're-run' (SKIP when this
    account cannot create symlinks)
K15 (ADR-0008 Q4 option A, review finding J1) ONE injected disposition
    failure (test-only fault kind 'dispose') on a stale lock: --break-lock
    (subprocess, env hook) rc 2 with the delete-refused text, never 'could
    not open ... exclusively', run.lock byte-unchanged; in-process, counted
    by wrappers: exactly one exclusive open, one read, one judgment, no
    retry sleep (no reopen / re-read / re-judge), refused in < 4 s (never the
    6.3 s retry budget); then the
    same break without the fault succeeds. Release with the same fault: one
    open + one read, no judgment, no sleep, the delete-refused WARNING (not
    'cannot open it exclusively'), the lock kept; as a subprocess run it
    keeps the run's exit code with the three WARNING lines. Red on 8ec44d0
    (the old break ignores the kind and breaks the lock: rc 0)
K8 placement + usage: a --state-dir run's lock is in the DEFAULT dir, never
    in --state-dir; --break-lock combined with any mode or option, or
    without --alias-only, is a usage error; --break-lock with no lock is a
    no-op (rc 0)

Run: python -B scripts/tests/test_alias_run_lock.py      Exit 0 = pass.
Every run uses temp dirs; LOCALAPPDATA is pointed at a temp dir, so the real
%LOCALAPPDATA%/WIKIllm is never touched.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "fix_wikilinks.py"
HOOK = "FIX_WIKILINKS_TEST_RUNLOCK"
LOCKS = "FIX_WIKILINKS_TEST_LOCKS"
SOIL = "---\ntitle: Soil pH\naliases: [Soil pH]\n---\n# Soil pH\n"
FILES = {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": "a [[Soil pH]]\n",
         "wiki/b.md": "b [[Soil pH]]\n", "wiki/c.md": "c [[Soil pH]]\n"}
HELD = "run lock is held"


def argv(root, *args, state=None):
    a = [sys.executable, "-B", str(SCRIPT), "--root", str(root), "--alias-only", *args]
    return a + (["--state-dir", str(state)] if state is not None else [])


def env(hook=None, locks=None):
    e = dict(os.environ)
    e.pop(HOOK, None)
    e.pop(LOCKS, None)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    if hook:
        e[HOOK] = json.dumps(hook)
    if locks:
        e[LOCKS] = json.dumps(locks)
    return e


def run(root, *args, state=None, hook=None, locks=None):
    return subprocess.run(argv(root, *args, state=state), cwd=str(root), env=env(hook, locks),
                          capture_output=True, text=True, encoding="utf-8")


def start(root, *args, state=None, hook=None):
    return subprocess.Popen(argv(root, *args, state=state), cwd=str(root), env=env(hook),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8")


def finish(p, timeout=120):
    o, e = p.communicate(timeout=timeout)
    return p.returncode, o + e


def out(p):
    return p.stdout + p.stderr


def write_vault(root, files=FILES):
    for rel, content in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content.encode("utf-8"))


def tree(d):
    t = {}
    if not Path(d).exists():
        return t
    for p in sorted(Path(d).rglob("*")):
        rel = p.relative_to(d).as_posix()
        t[rel] = "<dir>" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
    return t


def default_dir(lad, root):
    """Independent of the tool: <LOCALAPPDATA>/WIKIllm/alias-fix/<name>-<hash8>."""
    key = os.path.normcase(str(Path(root).resolve())).replace("\\", "/")
    h8 = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return Path(os.path.abspath(str(lad))) / "WIKIllm" / "alias-fix" / f"{Path(root).resolve().name}-{h8}"


def temps(d):
    return sorted(p.name for p in Path(d).glob("run.lock.*.tmp")) if Path(d).exists() else []


def wait_until(cond, what, timeout=60):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > timeout:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.02)


def check(cond, msg, text=""):
    if not cond:
        raise AssertionError(f"{msg}\n--- output ---\n{text}")


def fresh(base, name):
    root = base / name
    write_vault(root)
    return root


def hold_writer(base, root, *args, state=None):
    """A live holder: a run that acquires the lock and waits for a release file."""
    rel = base / f"release-{root.name}-{time.monotonic_ns()}"
    p = start(root, *args, state=state, hook={"hold": str(rel)})
    return p, rel


def craft_lock(d, rec_or_bytes):
    d.mkdir(parents=True, exist_ok=True)
    data = rec_or_bytes if isinstance(rec_or_bytes, bytes) else json.dumps(rec_or_bytes).encode()
    (d / "run.lock").write_bytes(data)
    return data


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    pid = p.pid
    del p
    return pid


def rec(pid, created, host=None):
    return {"schema_version": 1, "tool": "alias-fix", "lock_id": "f" * 32, "pid": pid,
            "proc_created": created, "host": host or socket.gethostname(), "mode": "apply",
            "started": "2026-09-30T00:00:00+00:00", "vault": "x"}


# ---------------------------------------------------------------------------

def k1_live_holder(base, lad):
    root = fresh(base, "k1")
    sd = base / "k1-state"
    other = base / "k1-other-state"
    assert run(root).returncode == 0                       # manifest
    assert run(root, state=sd).returncode == 0             # registers sd
    d = default_dir(lad, root)
    p, rel = hold_writer(base, root, "--apply")
    try:
        wait_until(lambda: (d / "run.lock").exists(), "holder's run.lock")
        lock_bytes = (d / "run.lock").read_bytes()
        held = json.loads(lock_bytes)
        check(held["pid"] == p.pid and len(held["lock_id"]) == 32 and held["mode"] == "apply"
              and isinstance(held["proc_created"], int), "holder record", lock_bytes.decode())
        vt, dt, st = tree(root), tree(d), tree(sd)
        modes = [((), None), (("--apply",), None), (("--restore", "0" * 16), None),
                 (("--migrate-legacy-state",), sd), (("--migrate-legacy-state", "--apply"), None),
                 (("--rebind-state", str(base / "k1-gone")), None),
                 (("--forget-state-dir", str(sd)), None), (("--verify",), sd),
                 (("--verify",), other), ((), sd)]
        for args, state in modes:
            r = run(root, *args, state=state)
            o = out(r)
            check(r.returncode == 2 and HELD in o and "holder status: alive" in o
                  and "nothing written" in o, f"K1 {args} state={state} refused", o)
            check(tree(root) == vt and tree(d) == dt and tree(sd) == st and not other.exists(),
                  f"K1 {args}: something was written", o)
            check((d / "run.lock").read_bytes() == lock_bytes and not temps(d), f"K1 {args}: lock/temps", o)
        r = run(root, "--verify")
        check(r.returncode in (0, 1) and HELD not in out(r), "K1 plain --verify is lock-free", out(r))
        r = run(root, "--break-lock")
        check(r.returncode == 2 and "holder is alive" in out(r), "K1 --break-lock refuses alive", out(r))
        check((d / "run.lock").read_bytes() == lock_bytes, "K1 lock intact after refused break")
    finally:
        rel.write_text("go")
    rc, o = finish(p)
    check(rc == 0, "K1 holder finishes rc 0", o)
    check(not (d / "run.lock").exists() and not temps(d), "K1 lock released", o)
    print("K1 ok -- live holder refuses 10 modes, plain --verify runs, break refused, released")


def k2_link_race(base, lad):
    root = fresh(base, "k2")
    assert run(root).returncode == 0
    d = default_dir(lad, root)
    before = tree(root)
    gate, hold = base / "k2-gate", base / "k2-hold"
    hook = {"gate": str(gate), "hold": str(hold)}
    a = start(root, "--apply", hook=hook)
    b = start(root, "--apply", hook=hook)
    try:
        wait_until(lambda: len(temps(d)) == 2, "two complete temp records")
        for t in temps(d):
            r = json.loads((d / t).read_text(encoding="utf-8"))
            check(r["pid"] in (a.pid, b.pid), "temp record is a complete holder record", t)
        gate.write_text("go")
        wait_until(lambda: a.poll() is not None or b.poll() is not None, "the loser to exit")
        loser = a if a.poll() is not None else b
        winner = b if loser is a else a
        rc, o = finish(loser)
        check(rc == 2 and HELD in o and "nothing written" in o, "K2 loser refused", o)
        check(tree(root) == before, "K2 loser left the vault untouched", o)
        check(winner.poll() is None and (d / "run.lock").exists(), "K2 winner still holds")
        check(json.loads((d / "run.lock").read_text())["pid"] == winner.pid, "K2 lock names winner")
        wait_until(lambda: len(temps(d)) == 0, "both temp records removed", 10)
    finally:
        hold.write_text("go")
    rc, o = finish(winner)
    check(rc == 0, "K2 winner applied", o)
    logs = list((d / "backup").rglob("apply-log.json"))
    check(len(logs) == 1 and not list((d / "backup").rglob("apply-log.prev*")), "K2 one apply log",
          str(logs))
    for rel in ("wiki/a.md", "wiki/b.md", "wiki/c.md"):
        check((root / rel).read_text(encoding="utf-8").endswith("[[soil-ph|Soil pH]]\n"),
              f"K2 {rel} rewritten once", (root / rel).read_text(encoding="utf-8"))
    check(not (d / "run.lock").exists() and not temps(d), "K2 no lock left")
    print("K2 ok -- os.link race: exactly one of two --apply contenders won, loser untouched")


def k3_stale(base, lad):
    for name, record in (("k3-dead", rec(dead_pid(), 1)), ("k3-reused", rec(os.getpid(), 1))):
        root = fresh(base, name)
        d = default_dir(lad, root)
        craft_lock(d, record)
        r = run(root)
        o = out(r)
        check(r.returncode == 2 and "holder status: stale" in o and "--break-lock" in o,
              f"{name}: refused as STALE with the break command", o)
        r = run(root, "--break-lock")
        check(r.returncode == 0 and "BROKE STALE LOCK" in out(r) and not (d / "run.lock").exists(),
              f"{name}: broken", out(r))
        check(sorted(os.listdir(d)) == [], f"{name}: the default dir is EMPTY after the break",
              f"{sorted(os.listdir(d))}\n{out(r)}")
        r = run(root)
        check(r.returncode == 0 and not (d / "run.lock").exists(), f"{name}: next run works", out(r))
    print("K3 ok -- dead pid and reused pid are stale; --break-lock clears; next run works")


def k4_unprovable(base, lad):
    cases = (("k4-nocreated", rec(os.getpid(), None)),
             ("k4-foreign", rec(dead_pid(), 1, host="some-other-host")),
             ("k4-empty", b""), ("k4-garbage", b"{not json"))
    for name, record in cases:
        root = fresh(base, name)
        d = default_dir(lad, root)
        data = craft_lock(d, record)
        vt = tree(root)
        r = run(root)
        check(r.returncode == 2 and "holder status: unprovable" in out(r)
              and "by hand ONLY after confirming" in out(r), f"{name}: run refused", out(r))
        r = run(root, "--break-lock")
        check(r.returncode == 2 and "unprovable" in out(r), f"{name}: break refused", out(r))
        check((d / "run.lock").read_bytes() == data and tree(root) == vt, f"{name}: lock untouched")
    root = fresh(base, "k4-orphan")
    d = default_dir(lad, root)
    d.mkdir(parents=True)
    (d / "run.lock.deadbeef.tmp").write_bytes(json.dumps(rec(os.getpid(), 1)).encode())
    r = run(root)
    check(r.returncode == 0 and (d / "run.lock.deadbeef.tmp").exists() and not (d / "run.lock").exists(),
          "K4 an orphan temp record is not a lock", out(r))
    # re-review N2: an unprovable SIBLING lock (same folder name, other key)
    # refuses with hand-delete guidance, not 'wait'; --break-lock leaves it
    root = fresh(base / "k4-sib", "farm")
    sib = default_dir(lad, root).parent / "farm-0badc0de"
    sib.mkdir(parents=True)
    (sib / "run.lock").write_bytes(b"garbage")
    r = run(root)
    o = out(r)
    check(r.returncode == 2 and str(sib / "run.lock") in o and "by hand ONLY" in o
          and "--break-lock cannot clear it" in o, "K4 unprovable sibling: hand-delete guidance", o)
    r = run(root, "--break-lock")
    check(r.returncode == 0 and (sib / "run.lock").read_bytes() == b"garbage", "K4 sibling untouched", out(r))
    (sib / "run.lock").write_bytes(json.dumps(rec(dead_pid(), 1)).encode())
    r = run(root)
    check(r.returncode == 0, "K4 a STALE sibling lock does not block", out(r))
    print("K4 ok -- unprovable holders (no creation time, foreign host, empty, garbage) never broken")


def k5_no_hardlinks(base, lad):
    root = fresh(base, "k5")
    d = default_dir(lad, root)
    vt = tree(root)
    r = run(root, hook={"nolink": 1})
    check(r.returncode == 2 and "does not support hard links" in out(r), "K5 refused", out(r))
    check(not (d / "run.lock").exists() and not temps(d) and not (d / "manifest.json").exists()
          and tree(root) == vt, "K5 nothing written, no fallback lock", out(r))
    print("K5 ok -- no hard links: exit 2, no fallback")


def k6_release(base, lad):
    root = fresh(base, "k6")
    d = default_dir(lad, root)
    # 'unlock' faults the exclusive compare-and-delete open (ADR-0008 Q6)
    r = run(root, locks={"unlock:run.lock": 2})
    check(r.returncode == 0 and not (d / "run.lock").exists() and "WARNING" not in out(r),
          "K6 transient release lock retried", out(r))
    base_rc = run(root).returncode
    r = run(root, locks={"unlock:run.lock": 7})
    o = out(r)
    check(r.returncode == base_rc == 0, "K6 exhausted release keeps the run's exit code", o)
    check("WARNING: operation completed, but run.lock could not be removed" in o
          and f"LOCK: {d / 'run.lock'}" in o
          and "Next run will refuse until the stale lock is cleared." in o, "K6 warning lines", o)
    check((d / "run.lock").exists(), "K6 lock left behind")
    r = run(root)
    check(r.returncode == 2 and "holder status: stale" in out(r), "K6 leftover is stale", out(r))
    r = run(root, "--break-lock")
    check(r.returncode == 0 and not (d / "run.lock").exists(), "K6 leftover broken", out(r))
    # replaced under the holder
    p, rel = hold_writer(base, root)
    wait_until(lambda: (d / "run.lock").exists(), "holder")
    foreign = json.dumps(rec(os.getpid(), 1)).encode()
    (d / "run.lock").write_bytes(foreign)
    rel.write_text("go")
    rc, o = finish(p)
    check(rc == 0 and "no longer holds this run's record -- left untouched" in o, "K6 mismatch warns", o)
    check((d / "run.lock").read_bytes() == foreign, "K6 replaced lock untouched")
    (d / "run.lock").unlink()
    # refusal after acquiring still releases
    root2 = fresh(base, "k6-nomanifest")
    d2 = default_dir(lad, root2)
    r = run(root2, "--apply")
    check(r.returncode != 0 and not (d2 / "run.lock").exists() and not temps(d2),
          "K6 a refused --apply still releases", out(r))
    print("K6 ok -- release retries, exhausted release warns + keeps rc, mismatch untouched")


def k7_verify_state_dir(base, lad):
    root = fresh(base, "k7")
    d = default_dir(lad, root)
    sd = base / "k7-state"
    assert run(root, state=sd).returncode == 0
    z = fresh(base, "k7-z")
    sz = base / "k7-z-state"
    assert run(z, state=sz).returncode == 0     # sz's vault.json names vault z
    p, rel = hold_writer(base, root)
    try:
        wait_until(lambda: (d / "run.lock").exists(), "holder")
        r = run(root, "--verify", state=sd)
        check(r.returncode == 2 and HELD in out(r), "K7 --verify with registered --state-dir refused",
              out(r))
        # A --state-dir whose vault.json names another vault is refused by
        # the refusal-only pre-lock identity check (ADR-0006; keeps
        # ADR-0005 I6's nothing-written), never touching the held lock.
        held = (d / "run.lock").read_bytes()
        r = run(root, "--verify", state=sz)
        check(r.returncode == 2 and "one state dir per vault" in out(r) and HELD not in out(r)
              and (d / "run.lock").read_bytes() == held, "K7 foreign --state-dir refused pre-lock", out(r))
    finally:
        rel.write_text("go")
    assert finish(p)[0] == 0
    s1, s2 = base / "k7-new-1", base / "k7-new-2"
    gate, hold = base / "k7-gate", base / "k7-hold"
    hook = {"gate": str(gate), "hold": str(hold)}
    a = start(root, "--verify", state=s1, hook=hook)
    b = start(root, "--verify", state=s2, hook=hook)
    try:
        wait_until(lambda: len(temps(d)) == 2, "two verify contenders")
        gate.write_text("go")
        wait_until(lambda: a.poll() is not None or b.poll() is not None, "verify loser")
    finally:
        hold.write_text("go")
    loser = a if a.poll() is not None else b
    winner = b if loser is a else a
    win_dir, lose_dir = (s2, s1) if loser is a else (s1, s2)
    rc, o = finish(loser)
    check(rc == 2 and HELD in o and not lose_dir.exists(), "K7 losing verify refused, dir not created", o)
    rcw, ow = finish(winner)
    check(rcw in (0, 1) and HELD not in ow, "K7 winning verify ran", ow)

    def registered():
        reg = json.loads((d / "state-dirs.json").read_text(encoding="utf-8"))
        return {os.path.normcase(str(Path(e["path"]))) for e in reg["state_dirs"]}
    n = lambda p: os.path.normcase(str(p.resolve()))
    check(n(win_dir) in registered() and n(lose_dir) not in registered(), "K7 registry = winner only",
          str(registered()))
    r = run(root, "--verify", state=lose_dir)
    check(r.returncode in (0, 1) and n(lose_dir) in registered() and n(win_dir) in registered(),
          "K7 loser's re-run registers its own dir", out(r))
    print("K7 ok -- --verify --state-dir locks; concurrent registrations serialized")


def k8_placement_usage(base, lad):
    root = fresh(base, "k8")
    d = default_dir(lad, root)
    sd = base / "k8-state"
    p, rel = hold_writer(base, root, state=sd)
    try:
        wait_until(lambda: (d / "run.lock").exists(), "holder in the default dir")
        check(not (sd / "run.lock").exists(), "K8 no lock in --state-dir")
    finally:
        rel.write_text("go")
    assert finish(p)[0] == 0
    for extra in (["--apply"], ["--verify"], ["--restore", "x"], ["--state-dir", str(sd)],
                  ["--migrate-legacy-state"], ["--forget-state-dir", str(sd)], ["--max-files", "1"],
                  ["--rebind-state", str(sd)], ["--batch-size", "1"], ["--manifest", str(sd / "m.json")]):
        r = run(root, "--break-lock", *extra)
        check(r.returncode == 2 and "standalone" in out(r), f"K8 --break-lock {extra} usage error", out(r))
    r = subprocess.run([sys.executable, "-B", str(SCRIPT), "--root", str(root), "--break-lock"],
                       capture_output=True, text=True, encoding="utf-8", env=env())
    check(r.returncode == 2 and "require --alias-only" in out(r), "K8 needs --alias-only", out(r))
    r = run(root, "--break-lock")
    check(r.returncode == 0 and "nothing to break" in out(r) and "removed meanwhile" not in out(r),
          "K8 no lock = no-op", out(r))
    print("K8 ok -- lock in the default dir; --break-lock standalone; no-op when absent")


SIBLING = "another state key with this vault's folder name"


def share_spelling(p):
    """\\\\localhost\\c$\\... for a C:\\... path: one directory, a second path
    spelling ADR-0005's path hash keys apart. None when unreachable."""
    if os.name != "nt":
        return None
    s = str(Path(p).resolve())
    if len(s) < 3 or s[1:3] != ":\\":
        return None
    q = Path(f"\\\\localhost\\{s[0].lower()}$\\{s[3:]}")
    try:
        return q if q.is_dir() else None
    except OSError:
        return None


def k9_sibling_spelling(base, lad):
    root = fresh(base / "k9", "Vault")
    share = share_spelling(root)
    if share is None:
        print("K9 SKIP -- \\\\localhost\\c$ admin share unreachable (not Windows or shares off)")
        return
    d, ds = default_dir(lad, root), default_dir(lad, share)
    check(d != ds, f"K9 precondition: two keys for one dir ({d.name} vs {ds.name})")
    assert run(root).returncode == 0
    assert run(share).returncode == 0
    # 1+3: C:\ holder vs \\localhost\c$ contender -- cannot both write; the
    # refusing contender releases its own lock
    pristine = tree(root / "wiki")
    p, rel = hold_writer(base, root, "--apply")
    try:
        wait_until(lambda: (d / "run.lock").exists(), "C: holder")
        for args in ((), ("--apply",)):
            r = run(share, *args)
            o = out(r)
            check(r.returncode == 2 and SIBLING in o and str(d / "run.lock") in o
                  and "holder status alive" in o, f"K9 share {args or 'dry run'} refused by sibling", o)
            check(not (ds / "run.lock").exists() and not temps(ds), "K9 contender released its lock", o)
            check(tree(root / "wiki") == pristine, "K9 contender wrote nothing", o)
    finally:
        rel.write_text("go")
    rc, o = finish(p)
    check(rc == 0 and not (d / "run.lock").exists(), "K9 holder applied and released", o)
    # 2: simultaneous alternate-spelling contenders may both refuse
    hold = base / "k9-hold"
    a = start(root, hook={"hold": str(hold)})
    b = start(share, hook={"hold": str(hold)})
    try:
        wait_until(lambda: (d / "run.lock").exists() and (ds / "run.lock").exists(), "both own locks")
    finally:
        hold.write_text("go")
    # Both acquired their own locks before either checked. Who checks first
    # is timing: both may refuse, or the first refuses + releases and the
    # second then proceeds. Never both proceed.
    res = [finish(a), finish(b)]
    proceeded = [rc for rc, _ in res if rc == 0]
    for rc, o in res:
        check("ignored outside a temp vault" not in o, "K9 the test hook reached both spellings", o)
        check(rc == 0 or (rc == 2 and SIBLING in o), "K9 simultaneous: a refusal is a sibling refusal", o)
    check(len(proceeded) <= 1, "K9 simultaneous: never both proceed", "\n=====\n".join(o for _, o in res))
    print(f"   K9 simultaneous outcome: {len(res) - len(proceeded)} refused, {len(proceeded)} proceeded")
    check(not (d / "run.lock").exists() and not (ds / "run.lock").exists(), "K9 natural race: released")
    # deterministic both-refuse: both check while both locks exist
    hold2, chk = base / "k9-hold2", base / "k9-checked"
    hk = {"hold": str(hold2), "after_sibling_check": str(chk)}
    a = start(root, hook=hk)
    b = start(share, hook=hk)
    try:
        wait_until(lambda: (d / "run.lock").exists() and (ds / "run.lock").exists(), "both own locks")
        hold2.write_text("go")
        wait_until(lambda: len(list(base.glob("k9-checked.at.*"))) == 2,
                   "both contenders past their sibling check")
    finally:
        hold2.write_text("go")
        chk.write_text("go")
    for name, (rc, o) in (("C:", finish(a)), ("share", finish(b))):
        check(rc == 2 and SIBLING in o, f"K9 barrier: {name} contender refused (both refuse)", o)
    check(not (d / "run.lock").exists() and not (ds / "run.lock").exists() and not temps(d)
          and not temps(ds), "K9 both released")
    # 4: a genuinely different vault with the same folder name
    x, y = fresh(base / "k9x", "Farm"), fresh(base / "k9y", "Farm")
    dx, dy = default_dir(lad, x), default_dir(lad, y)
    assert run(x).returncode == 0
    p, rel = hold_writer(base, x, "--apply")
    try:
        wait_until(lambda: (dx / "run.lock").exists(), "x holder")
        xlock = (dx / "run.lock").read_bytes()
        r = run(y)
        o = out(r)
        check(r.returncode == 2 and SIBLING in o and "different vault with the same folder name" in o
              and str(x.resolve()) in o, "K9 same-name different vault: conservative refusal names it", o)
        r = run(y, "--break-lock")
        check(r.returncode == 0 and "nothing to break" in out(r) and "removed meanwhile" not in out(r)
              and (dx / "run.lock").read_bytes() == xlock,
              "K9 y's --break-lock never touches x's lock", out(r))
    finally:
        rel.write_text("go")
    assert finish(p)[0] == 0
    r = run(y)
    o = out(r)
    check(r.returncode == 0 and "WARNING: state for another vault path with the same folder name" in o
          and SIBLING not in o and not list(dy.glob("backup/*")),
          "K9 after x settles: y runs, existing same-name WARNING only, x's runs not adopted", o)
    print("K9 ok -- C: vs \\\\localhost\\c$ serialized (both-refuse when simultaneous, own lock "
          "released); same-name different vault refused only while live, never merged")


def k10_uncreatable_default(base, lad2):
    """Review #3: an uncreatable default state dir is a clean exit 2."""
    lad = base / "k10-lad"
    (lad / "WIKIllm").mkdir(parents=True)
    (lad / "WIKIllm" / "alias-fix").write_text("a FILE where the state base dir goes")
    root = fresh(base, "k10")
    old = os.environ["LOCALAPPDATA"]
    os.environ["LOCALAPPDATA"] = str(lad)
    try:
        for args in ((), ("--apply",)):
            r = run(root, *args)
            check(r.returncode == 2 and "Traceback" not in out(r) and "nothing written" in out(r),
                  f"K10 {args or 'dry run'}: clean exit 2", out(r))
    finally:
        os.environ["LOCALAPPDATA"] = old
    print("K10 ok -- uncreatable default state dir: exit 2, no traceback")


RERUN = "released or broken while this run was inspecting it -- re-run"


def import_tool():
    """The tool in-process (no bytecode: sys.dont_write_bytecode is set)."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import fix_wikilinks as fw
    return fw


def captured(fn, *a):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = fn(*a)
    return rc, buf.getvalue()


def lock_pid(d):
    """The pid in d/run.lock, or None (absent, unreadable, not a record)."""
    try:
        return json.loads((Path(d) / "run.lock").read_bytes())["pid"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def marker_path(mark, kind, p):
    """The retry_marker hook's file: written when p's run.lock operation of
    `kind` (excl = exclusive compare-and-delete open, read = shared reader)
    failed on a retryable lock error."""
    return Path(f"{mark}.{kind}.{p.pid}")


def arrived(stem):
    """A break_judged / after_sibling_check arrival file (STEM.at.<pid>)."""
    return bool(list(Path(stem).parent.glob(Path(stem).name + ".at.*")))


def settle(procs, files):
    """Release every gate / hold / pause file, then reap every child (kill
    one that will not exit), so a failed assertion is never buried under a
    TemporaryDirectory cleanup error from a live child in the temp vault."""
    for f in files:
        try:
            Path(f).write_text("go")
        except OSError:
            pass
    for p in procs:
        if p.poll() is None:
            try:
                p.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                p.kill()
                p.communicate()


REMOVERS = ("unlink", "remove", "rename", "replace")


def by_path_guard(describe):
    """A context in which os.unlink / os.remove / os.rename / os.replace
    (pathlib's Path.unlink / rename / replace route through these -- checked
    on 3.14) raise AssertionError for a source named run.lock: a Windows
    run.lock deletion must go through the handle that judged it (ADR-0008
    Q1/Q6), never by path. describe() adds the state at the moment of the
    call to the message."""
    import contextlib

    @contextlib.contextmanager
    def cm():
        real = {n: getattr(os, n) for n in REMOVERS}

        def guard(n):
            def f(src, *a, **k):
                if os.path.basename(os.fspath(src)) == "run.lock":
                    raise AssertionError(f"by-path removal of run.lock (os.{n}) -- the deletion must "
                                         f"go through the judging handle; {describe()}")
                return real[n](src, *a, **k)
            return f
        for n in REMOVERS:
            setattr(os, n, guard(n))
        try:
            yield
        finally:
            for n in REMOVERS:
                setattr(os, n, real[n])
    return cm()


def k11_break_race(base, lad):
    """ADR-0008 Q7 (regression R170): the race forced at the one point the old
    and new --break-lock share -- the stale judgment. Old code: B2 breaks
    the stale lock, C acquires, and B1 (which judged the STALE record) then
    moves C's live lock aside. New code: B2 and C contend against B1's
    share-0 handle; B1 deletes only the object it judged. B1 runs with every
    by-path removal of run.lock (os.unlink / remove / rename / replace)
    turned into an AssertionError, so the delete must have gone through the
    judging handle: red against a close-then-unlink mutant (judge through
    the handle, CloseHandle, then unlink by path), which the race alone
    does not catch. A direct _lock_compare_delete check under the same
    guard pins both predicate outcomes (true: deleted; false: byte-unchanged)."""
    if os.name != "nt":
        print("K11 SKIP -- the atomic --break-lock is Windows-only (ADR-0008 Q2)")
        return
    fw = import_tool()
    root = fresh(base, "k11")
    assert run(root).returncode == 0                       # the manifest C's --apply needs
    d = default_dir(lad, root)
    dead = dead_pid()
    craft_lock(d, rec(dead, 1))
    pristine = tree(root)
    mark = base / "k11-retry"
    gate, hold, hold2 = base / "k11-gate", base / "k11-hold", base / "k11-hold2"
    procs, seen = {}, {}
    real = fw._holder_status

    def marked(kind, name):
        return name in procs and marker_path(mark, kind, procs[name]).exists()

    def exited(name):
        return name in procs and procs[name].poll() is not None

    def state():
        return (f"B2 exclusive-open retry marker={marked('excl', 'B2')}, B2 exited={exited('B2')}, "
                f"C holder-read retry marker={marked('read', 'C')}, C exited={exited('C')}, "
                f"run.lock pid={lock_pid(d)} (C pid={procs['C'].pid if 'C' in procs else None}), "
                f"temps={temps(d)}")

    def barrier(cond, what, timeout):
        t0 = time.monotonic()
        while not cond():
            if time.monotonic() - t0 > timeout:
                raise AssertionError(f"K11 barrier not reached within {timeout}s: {what}\n  {state()}")
            time.sleep(0.01)

    def race():
        # C first, parked at the gate with its temp record written, so that
        # once B2 is retrying (its 6.3 s budget running) only C's link and
        # holder read remain before the verdict returns.
        procs["C"] = start(root, "--apply", hook={"gate": str(gate), "hold": str(hold),
                                                  "retry_marker": str(mark)})
        barrier(lambda: len(temps(d)) == 1, "C wrote its temp record and waits at the gate", 60)
        procs["B2"] = start(root, "--break-lock", hook={"retry_marker": str(mark)})
        barrier(lambda: marked("excl", "B2") or exited("B2"),
                "B2 contending (exclusive-open retry marker) or B2 exited", 60)
        gate.write_text("go")
        barrier(lambda: (marked("excl", "B2") and marked("read", "C"))
                or (exited("B2") and lock_pid(d) == procs["C"].pid),
                "new code: B2's exclusive-open AND C's holder-read retry markers; old code: B2 "
                "exited AND run.lock holds C's record", 3)
        seen["c_held"] = lock_pid(d) == procs["C"].pid
        if seen["c_held"]:
            seen["c_bytes"] = (d / "run.lock").read_bytes()

    def judged(r):
        verdict = real(r)
        if "judged" not in seen:
            seen["judged"] = r
            race()
        return verdict

    def at_removal():
        c, pid = procs.get("C"), lock_pid(d)
        if c is not None and pid == c.pid:
            return (f"B1 acted on a record it did not judge: it judged pid {dead}'s stale lock, and "
                    f"run.lock held C's live record (pid {pid}) when it was removed by path "
                    f"(regression R170)\n  {state()}")
        return f"run.lock pid at that moment: {pid}\n  {state()}"

    fw._holder_status = judged
    try:
        with by_path_guard(at_removal):
            rc, o = captured(fw.run_break_lock, root.resolve())
        fw._holder_status = real
        check(isinstance(seen.get("judged"), dict) and seen["judged"]["pid"] == dead,
              "K11 B1 judged the crafted stale record", o)
        if rc != 0 and "changed while it was being broken" in o:
            raise AssertionError(
                f"K11 B1 acted on a record it did not judge: it judged pid {dead}'s stale lock, then "
                f"moved the live replacement lock of C (pid {procs['C'].pid}) aside (regression R170)\n"
                f"--- B1 output ---\n{o}\n  {state()}")
        check(rc == 0 and "BROKE STALE LOCK" in o and f"pid {dead} on" in o,
              "K11 B1 acted only on the record it judged (rc 0, BROKE naming the dead pid)", o)
        if seen.get("c_held"):
            check(lock_pid(d) == procs["C"].pid and (d / "run.lock").read_bytes() == seen["c_bytes"],
                  "K11 C's live lock was displaced", state())
        rcb, ob = finish(procs["B2"], 60)
        check(rcb == 0 and "removed meanwhile" in ob, "K11 B2: exit 0, 'removed meanwhile'", ob)
        rcc, oc = finish(procs["C"], 60)
        check(rcc == 2 and RERUN in oc and "malformed" not in oc,
              "K11 C's first attempt: the accurate re-run refusal", oc)
        check(tree(root) == pristine and not (d / "run.lock").exists() and not temps(d),
              "K11 after the race: nothing written, nothing held", state())
        # C re-runs: the sole holder; D and B3 refuse against it
        procs["C2"] = start(root, "--apply", hook={"hold": str(hold2)})
        wait_until(lambda: lock_pid(d) == procs["C2"].pid, "C's re-run to hold the lock")
        c_bytes = (d / "run.lock").read_bytes()
        check(sorted(x.name for x in d.glob("run.lock*")) == ["run.lock"],
              "K11 C's re-run is the sole holder", str(sorted(os.listdir(d))))
        r = run(root, "--apply")
        check(r.returncode == 2 and HELD in out(r) and "holder status: alive" in out(r),
              "K11 D --apply refused against C (held, alive)", out(r))
        r = run(root, "--break-lock")
        check(r.returncode == 2 and "holder is alive" in out(r), "K11 B3 --break-lock refused (alive)",
              out(r))
        check((d / "run.lock").read_bytes() == c_bytes, "K11 C's lock untouched by D and B3")
        hold2.write_text("go")
        rc2, o2 = finish(procs["C2"])
        check(rc2 == 0 and "could not be removed" not in o2 and not (d / "run.lock").exists()
              and not temps(d), "K11 C released: rc 0, no lock left", o2)
        # the primitive itself, under the same guard: deleted only via the handle
        direct = base / "k11-direct"
        direct.mkdir()
        lk = direct / "run.lock"
        lk.write_bytes(b"judge me")
        got = []
        with by_path_guard(lambda: "direct _lock_compare_delete check"):
            kept = fw._lock_compare_delete(lk, lambda raw: (got.append(raw), False)[1], "test", {})
            check(kept is False and lk.read_bytes() == b"judge me" and got == [b"judge me"],
                  "K11 direct: predicate false -> False, byte-unchanged", str(got))
            gone = fw._lock_compare_delete(lk, lambda raw: True, "test", {})
            check(gone is True and not os.path.lexists(lk) and os.listdir(direct) == [],
                  "K11 direct: predicate true -> True, deleted through the handle (no by-path call)")
    finally:
        fw._holder_status = real
        settle(list(procs.values()), (gate, hold, hold2))
    print("K11 ok -- break race: B1 deleted only the stale record it judged, through its handle "
          "(no by-path removal); B2 'removed meanwhile', C 're-run'; C's re-run sole holder, D + B3 "
          "refused, released clean; the primitive deletes only via the handle")


def k12_handle_window(base, lad):
    """ADR-0008 Q3/Q4/Q6 + Q2 seam: everything that meets a breaker's open
    exclusive handle (break_judged hook pauses it after judging)."""
    if os.name != "nt":
        print("K12 SKIP -- the atomic --break-lock is Windows-only (ADR-0008 Q2)")
        return
    mark = base / "k12-retry"
    procs, files = [], []

    def go(name):
        f = base / name
        files.append(f)
        return f

    def spawn(root, *args, hook=None):
        p = start(root, *args, hook=hook)
        procs.append(p)
        return p

    def paused_breaker(root, stem):
        j = go(stem)
        b = spawn(root, "--break-lock", hook={"break_judged": str(j)})
        wait_until(lambda: arrived(j), f"{stem}: breaker paused after judging (handle open)", 60)
        return b, j

    try:
        # (a) an acquirer, then a live holder's release, each meeting a REFUSED break
        root = fresh(base, "k12a")
        d = default_dir(lad, root)
        hold = go("k12a-hold")
        h = spawn(root, hook={"hold": str(hold), "retry_marker": str(mark)})
        wait_until(lambda: lock_pid(d) == h.pid, "K12a holder")
        h_bytes = (d / "run.lock").read_bytes()
        b, j = paused_breaker(root, "k12a-j1")
        a = spawn(root, hook={"retry_marker": str(mark)})
        wait_until(lambda: marker_path(mark, "read", a).exists(),
                   "K12a acquirer's holder read retrying behind the breaker", 60)
        j.write_text("go")
        rc, o = finish(b, 60)
        check(rc == 2 and "holder is alive" in o, "K12a breaker refused (alive)", o)
        rc, o = finish(a, 60)
        check(rc == 2 and HELD in o and "holder status: alive" in o and "malformed" not in o
              and "holder status: unprovable" not in o, "K12a acquirer: accurate held-alive refusal", o)
        check((d / "run.lock").read_bytes() == h_bytes, "K12a holder's lock untouched")
        b, j = paused_breaker(root, "k12a-j2")
        hold.write_text("go")
        wait_until(lambda: marker_path(mark, "excl", h).exists(),
                   "K12a holder's release retrying behind the breaker", 60)
        j.write_text("go")
        rc, o = finish(b, 60)
        check(rc == 2 and "holder is alive" in o, "K12a second breaker refused (alive)", o)
        rc, o = finish(h, 60)
        check(rc == 0 and "could not be removed" not in o and "WARNING" not in o
              and not (d / "run.lock").exists() and not temps(d),
              "K12a live holder's release during a refused break: rc 0, no lock left", o)
        print("K12a ok -- acquirer reads the alive holder after the refused break; the holder's "
              "release during a refused break succeeds, no lock left")

        # (b) the sibling check during a break: stale sibling (broken), alive sibling (refused)
        x, y = fresh(base / "k12x", "Farm"), fresh(base / "k12y", "Farm")
        dx = default_dir(lad, x)
        craft_lock(dx, rec(dead_pid(), 1))
        b, j = paused_breaker(x, "k12b-j1")
        yp = spawn(y, hook={"retry_marker": str(mark)})
        wait_until(lambda: marker_path(mark, "read", yp).exists(),
                   "K12b sibling read retrying behind x's breaker", 60)
        j.write_text("go")
        rc, o = finish(b, 60)
        check(rc == 0 and "BROKE STALE LOCK" in o, "K12b x's stale lock broken", o)
        rc, o = finish(yp, 60)
        check(rc == 0 and SIBLING not in o and "malformed" not in o and "holder status unprovable" not in o,
              "K12b y during the stale sibling's break: no sibling refusal, no false malformed", o)
        xh = go("k12b-xhold")
        hx = spawn(x, hook={"hold": str(xh)})
        wait_until(lambda: lock_pid(dx) == hx.pid, "K12b x holder")
        b, j = paused_breaker(x, "k12b-j2")
        yp = spawn(y, hook={"retry_marker": str(mark)})
        wait_until(lambda: marker_path(mark, "read", yp).exists(),
                   "K12b sibling read retrying behind x's refused breaker", 60)
        j.write_text("go")
        rc, o = finish(b, 60)
        check(rc == 2 and "holder is alive" in o, "K12b x's breaker refused (alive)", o)
        rc, o = finish(yp, 60)
        check(rc == 2 and SIBLING in o and "holder status alive" in o and "malformed" not in o
              and "holder status unprovable" not in o, "K12b y: accurate alive-sibling refusal", o)
        xh.write_text("go")
        rc, o = finish(hx, 60)
        check(rc == 0 and not (dx / "run.lock").exists(), "K12b x holder released", o)
        print("K12b ok -- sibling check during a break: stale -> no block, alive -> accurate "
              "refusal; never malformed/unprovable")

        # (c) an acquirer during a STALE break: the accurate re-run refusal
        root = fresh(base, "k12c")
        d = default_dir(lad, root)
        dead = dead_pid()
        craft_lock(d, rec(dead, 1))
        b, j = paused_breaker(root, "k12c-j")
        a = spawn(root, hook={"retry_marker": str(mark)})
        wait_until(lambda: marker_path(mark, "read", a).exists(),
                   "K12c acquirer's holder read retrying behind the breaker", 60)
        j.write_text("go")
        rc, o = finish(b, 60)
        check(rc == 0 and "BROKE STALE LOCK" in o and f"pid {dead} on" in o, "K12c stale lock broken", o)
        rc, o = finish(a, 60)
        check(rc == 2 and RERUN in o and "malformed" not in o and "holder status: unprovable" not in o,
              "K12c acquirer during the break: 're-run', never 'malformed'", o)
        check(sorted(os.listdir(d)) == [], "K12c nothing left", str(sorted(os.listdir(d))))
        r = run(root)
        check(r.returncode == 0, "K12c the re-run works", out(r))
        print("K12c ok -- acquirer during a stale break: accurate re-run refusal, re-run works")

        # (d) exclusive-open exhaustion: break (fault hook) and release (a real breaker)
        root = fresh(base, "k12d")
        d = default_dir(lad, root)
        data = craft_lock(d, rec(dead_pid(), 1))
        r = run(root, "--break-lock", locks={"unlock:run.lock": 7})
        check(r.returncode == 2 and "could not open the run lock exclusively; nothing broken" in out(r)
              and (d / "run.lock").read_bytes() == data,
              "K12d exhausted break: rc 2, lock untouched", out(r))
        (d / "run.lock").unlink()
        base_rc = run(root).returncode
        hold = go("k12d-hold")
        h = spawn(root, hook={"hold": str(hold)})
        wait_until(lambda: lock_pid(d) == h.pid, "K12d holder")
        h_bytes = (d / "run.lock").read_bytes()
        b, j = paused_breaker(root, "k12d-j")
        hold.write_text("go")
        rc, o = finish(h, 120)        # its release exhausts behind the paused breaker
        check(rc == base_rc == 0, "K12d exhausted release keeps the run's exit code", o)
        check("WARNING: operation completed, but run.lock could not be removed" in o
              and f"LOCK: {d / 'run.lock'}" in o
              and "Next run will refuse until the stale lock is cleared." in o,
              "K12d exhausted release: the three WARNING lines", o)
        j.write_text("go")
        rc, o = finish(b, 60)
        check(rc == 2 and "holder is alive" in o, "K12d breaker judged the then-live holder alive", o)
        check((d / "run.lock").read_bytes() == h_bytes, "K12d the holder's record left in place")
        r = run(root, "--break-lock")
        check(r.returncode == 0 and "BROKE STALE LOCK" in out(r) and not (d / "run.lock").exists(),
              "K12d leftover then broken", out(r))
        print("K12d ok -- exhausted exclusive open: break rc 2 untouched; release keeps rc + WARNING")

        # (e) off Windows, through the _break_supported seam (never os.name), in-process
        fw = import_tool()
        real = fw._break_supported
        fw._break_supported = lambda: False
        try:
            root = fresh(base, "k12e")
            d = default_dir(lad, root)
            data = craft_lock(d, rec(dead_pid(), 1))
            rc, o = captured(fw.run_break_lock, root.resolve())
            check(rc == 2 and "unsupported on this platform" in o and str(d / "run.lock") in o
                  and "by hand ONLY after" in o and (d / "run.lock").read_bytes() == data,
                  "K12e off-Windows --break-lock: rc 2, bytes unchanged, path + guidance", o)
            root2 = fresh(base, "k12e2")
            d2 = default_dir(lad, root2)
            rc, o = captured(fw.run_break_lock, root2.resolve())
            check(rc == 2 and "unsupported on this platform" in o and not (d2 / "run.lock").exists(),
                  "K12e off-Windows --break-lock refuses even with no lock", o)
            lk = fw.RunLock.acquire(root2.resolve(), d2, "dry-run")
            check((d2 / "run.lock").read_bytes() == lk.data, "K12e off-Windows acquire")
            rc, o = captured(lambda: lk.release() or 0)
            check(not (d2 / "run.lock").exists() and "WARNING" not in o, "K12e off-Windows release", o)
            lk = fw.RunLock.acquire(root2.resolve(), d2, "dry-run")
            foreign = json.dumps(rec(os.getpid(), 1)).encode()
            (d2 / "run.lock").write_bytes(foreign)
            rc, o = captured(lambda: lk.release() or 0)
            check("no longer holds this run's record -- left untouched" in o
                  and (d2 / "run.lock").read_bytes() == foreign, "K12e off-Windows release mismatch", o)
            (d2 / "run.lock").unlink()
            # off Windows the stale guidance names no break command (it
            # always exits 2 there): the held refusal and the exhausted
            # release WARNING both point at the manual recovery
            root3 = fresh(base, "k12e3")
            d3 = default_dir(lad, root3)
            craft_lock(d3, rec(dead_pid(), 1))
            try:
                fw.RunLock.acquire(root3.resolve(), d3, "dry-run")
                o = None
            except fw.RunLockRefused as e:
                o = str(e)
            check(o is not None and "holder status: stale" in o and "--break-lock" not in o
                  and "by hand ONLY after independently confirming" in o
                  and "dry run first" in o, "K12e off-Windows stale held refusal: manual guidance only",
                  str(o))
            (d3 / "run.lock").unlink()
            lk = fw.RunLock.acquire(root3.resolve(), d3, "dry-run")
            real_sleep = fw._lock_sleep
            fw._lock_sleep = lambda s: None
            fw._LOCKS["faults"] = {"unlock:run.lock": {"n": 7, "code": 5, "skip": 0}}
            try:
                rc, o = captured(lambda: lk.release() or 0)
            finally:
                fw._LOCKS["faults"] = None
                fw._lock_sleep = real_sleep
            check("WARNING: operation completed, but run.lock could not be removed" in o
                  and "--break-lock" not in o and "by hand ONLY after independently confirming" in o
                  and (d3 / "run.lock").read_bytes() == lk.data,
                  "K12e off-Windows exhausted release WARNING: manual guidance only", o)
            (d3 / "run.lock").unlink()
        finally:
            fw._break_supported = real
        print("K12e ok -- off Windows (seam): --break-lock rc 2 untouched with path + guidance; "
              "acquire + release work; stale refusal + exhausted-release WARNING name no break command")

        # (f) retries EXHAUSTED behind a share-0 handle (ADR-0008 Q3 note): the
        # acquirer's holder read and the sibling read fail unread -> 'not
        # judged (unreadable, not malformed)', never 'malformed record' /
        # 'unprovable'. In-process, both handles held past the retry budget.
        root = fresh(base / "k12f", "K12f")
        rr = root.resolve()
        d = default_dir(lad, root)
        own = craft_lock(d, rec(os.getpid(), fw._proc_status(os.getpid())[1]))
        other8 = "11111111" if d.name.endswith("-00000000") else "00000000"
        sd = d.parent / f"{rr.name}-{other8}"   # another key, same folder name
        sib = craft_lock(sd, rec(os.getpid(), fw._proc_status(os.getpid())[1]))
        real_sleep = fw._lock_sleep
        h = hs = None
        try:
            fw._lock_sleep = lambda s: None
            h = fw._win_open(d / "run.lock", fw._DELETE | fw._GENERIC_READ, 0)
            hs = fw._win_open(sd / "run.lock", fw._DELETE | fw._GENERIC_READ, 0)
            try:
                fw.RunLock.acquire(rr, d, "dry-run")
                o = None
            except fw.RunLockRefused as e:
                o = str(e)
            check(o is not None and HELD in o
                  and "holder status: not judged (unreadable, not malformed)" in o
                  and "malformed record" not in o and "holder status: unprovable" not in o,
                  "K12f acquirer, holder read exhausted: 'not judged (unreadable, not malformed)'",
                  str(o))
            s = fw.sibling_locks(rr, d)
            check(len(s) == 1 and str(sd / "run.lock") in s[0]
                  and "not judged (unreadable, not malformed)" in s[0] and "malformed record" not in s[0],
                  "K12f sibling read exhausted: 'not judged (unreadable, not malformed)'", str(s))
        finally:
            fw._lock_sleep = real_sleep
            for x in (h, hs):
                if x is not None:
                    fw._k32().CloseHandle(x)
        check((d / "run.lock").read_bytes() == own and (sd / "run.lock").read_bytes() == sib
              and not temps(d), "K12f both locks byte-unchanged, no temp record left")
        (d / "run.lock").unlink()
        (sd / "run.lock").unlink()
        print("K12f ok -- exhausted holder / sibling read behind a share-0 handle: 'not judged "
              "(unreadable, not malformed)', never malformed / unprovable")
    finally:
        settle(procs, files)
    print("K12 ok -- the exclusive-handle window: accurate readers, clean release, exhaustion, seam, "
          "unread not malformed")


def k13_readonly_lock(base, lad):
    """ADR-0008 Q4/Q6: only the exclusive OPEN is retried. A read-only stale
    run.lock opens, is judged stale, and its delete disposition is refused
    (WinError 5): --break-lock fails at once (no 6.3 s retry budget) with
    the delete-refused text, the lock byte-unchanged; release names the
    refused delete. With the attribute cleared the same break succeeds."""
    if os.name != "nt":
        print("K13 SKIP -- the atomic --break-lock is Windows-only (ADR-0008 Q2)")
        return
    import stat
    fw = import_tool()
    root = fresh(base, "k13")
    d = default_dir(lad, root)
    dead = dead_pid()
    data = craft_lock(d, rec(dead, 1))
    lk = d / "run.lock"
    os.chmod(lk, stat.S_IREAD)
    try:
        t0 = time.monotonic()
        with counted(fw) as n:
            rc, o = captured(fw.run_break_lock, root.resolve())
        secs = time.monotonic() - t0
        check(n == {"open": 1, "read": 1, "judge": 1, "sleep": 0},
              f"K13 one open, one read, one judgment, no retry sleep: {n}", o)
        check(rc == 2 and "judged stale through the exclusive handle, but the delete was refused" in o
              and "WinError 5" in o and "not broken" in o and f"pid {dead} on" in o
              and "could not open the run lock exclusively" not in o and "retries exhausted" not in o
              and "BROKE" not in o, "K13 read-only stale lock: delete refused, rc 2", o)
        check(secs < 4.0, f"K13 refused at once ({secs:.2f}s), not after the 6.3 s open retry budget", o)
        check(lk.read_bytes() == data, "K13 lock byte-unchanged after the refused delete", o)
    finally:
        if os.path.lexists(lk):
            os.chmod(lk, stat.S_IREAD | stat.S_IWRITE)
    rc, o = captured(fw.run_break_lock, root.resolve())
    check(rc == 0 and "BROKE STALE LOCK" in o and sorted(os.listdir(d)) == [],
          "K13 attribute cleared: the same break succeeds, the dir is empty", o)
    # release: opened and compared (this run's record), delete refused
    lkr = fw.RunLock.acquire(root.resolve(), d, "dry-run")
    os.chmod(lk, stat.S_IREAD)
    try:
        t0 = time.monotonic()
        rc, o = captured(lambda: lkr.release() or 0)
        secs = time.monotonic() - t0
        check("WARNING: operation completed, but run.lock could not be removed" in o
              and "opened and compared, but the delete was refused" in o and "WinError 5" in o
              and "cannot open it exclusively" not in o and secs < 4.0
              and lk.read_bytes() == lkr.data,
              f"K13 read-only release: the refused delete named at once ({secs:.2f}s, not after the "
              f"6.3 s open retry budget), lock kept", o)
    finally:
        if os.path.lexists(lk):
            os.chmod(lk, stat.S_IREAD | stat.S_IWRITE)
    lk.unlink()
    print("K13 ok -- read-only stale lock: delete refused at once (rc 2, untouched), release names "
          "it; cleared attribute -> broken, dir empty")


def counted(fw):
    """A context counting, in-process, the steps of one run.lock operation:
    open = every CreateFileW (_win_open: the exclusive open AND any shared
    read), read = every ReadFile loop (_win_read_all), judge = every holder
    judgment (_holder_status), sleep = every retry backoff (_lock_sleep,
    still really sleeping). Yields the dict of counts."""
    import contextlib
    names = {"open": "_win_open", "read": "_win_read_all", "judge": "_holder_status",
             "sleep": "_lock_sleep"}

    @contextlib.contextmanager
    def cm():
        n = {k: 0 for k in names}
        real = {k: getattr(fw, v) for k, v in names.items()}

        def wrap(k):
            def f(*a, **kw):
                n[k] += 1
                return real[k](*a, **kw)
            return f
        for k, v in names.items():
            setattr(fw, v, wrap(k))
        try:
            yield n
        finally:
            for k, v in names.items():
                setattr(fw, v, real[k])
    return cm()


def k15_dispose_refused(base, lad):
    """ADR-0008 Q4, option A (review finding J1, the owner 2026-10-01): only the
    exclusive OPEN is retried. Once the share-0 handle is held the record is
    read once, judged once and the disposition attempted once; a refused
    disposition closes the handle and fails -- no reopen, no re-read, no
    re-judge, run.lock untouched. One disposition failure is injected
    through the test-only 'dispose' fault kind (temp vaults only)."""
    if os.name != "nt":
        print("K15 SKIP -- the atomic --break-lock is Windows-only (ADR-0008 Q2)")
        return
    refused = "judged stale through the exclusive handle, but the delete was refused"
    one = {"dispose:run.lock": 1}
    root = fresh(base, "k15")
    d = default_dir(lad, root)
    lk = d / "run.lock"
    dead = dead_pid()
    data = craft_lock(d, rec(dead, 1))

    def locks_in(dd):
        return sorted(x.name for x in Path(dd).glob("run.lock*"))
    # (1) the real CLI, fault through the env hook (red on 8ec44d0: no such
    # kind there, so the old break breaks the lock and exits 0)
    r = run(root, "--break-lock", locks=one)
    o = out(r)
    check(r.returncode == 2 and refused in o and "not broken" in o and f"pid {dead} on" in o
          and "could not open the run lock exclusively" not in o and "retries exhausted" not in o
          and "BROKE" not in o,
          "K15 --break-lock, one refused disposition: rc 2 with the delete-refused text", o)
    check(lk.read_bytes() == data and locks_in(d) == ["run.lock"],
          "K15 --break-lock: run.lock byte-unchanged, nothing else left", o)
    # (2) in-process: count every open / read / judgment / retry sleep
    fw = import_tool()
    spec = {"n": 1, "code": 5, "skip": 0}
    fw._LOCKS.update(retries=0, max_wait=0.0, faults={"dispose:run.lock": spec})
    try:
        t0 = time.monotonic()
        with counted(fw) as n:
            rc, o = captured(fw.run_break_lock, root.resolve())
        secs = time.monotonic() - t0
    finally:
        fw._LOCKS["faults"] = None
    check(spec["n"] == 0, "K15 the injected disposition fault was reached", str(spec))
    check(n == {"open": 1, "read": 1, "judge": 1, "sleep": 0} and fw._LOCKS["retries"] == 0,
          f"K15 break: exactly one exclusive open, one read, one judgment, no retry (no reopen / "
          f"re-read / re-judge): {n}, retries {fw._LOCKS['retries']}", o)
    check(rc == 2 and refused in o and "WinError 5" in o and "not broken" in o
          and "could not open the run lock exclusively" not in o and "BROKE" not in o,
          "K15 break in-process: rc 2, delete refused (not an exclusive-open failure)", o)
    check(secs < 4.0, f"K15 break refused promptly ({secs:.2f}s), not after the 6.3 s open retry budget",
          o)
    check(lk.read_bytes() == data and locks_in(d) == ["run.lock"],
          "K15 break in-process: run.lock byte-unchanged", o)
    rc, o = captured(fw.run_break_lock, root.resolve())
    check(rc == 0 and "BROKE STALE LOCK" in o and locks_in(d) == [],
          "K15 without the fault the same break succeeds", o)
    # (3) release, in-process: the same single-shot rule, no judgment
    lkr = fw.RunLock.acquire(root.resolve(), d, "dry-run")
    spec = {"n": 1, "code": 5, "skip": 0}
    fw._LOCKS.update(retries=0, max_wait=0.0, faults={"dispose:run.lock": spec})
    try:
        t0 = time.monotonic()
        with counted(fw) as n:
            rc, o = captured(lambda: lkr.release() or 0)
        secs = time.monotonic() - t0
    finally:
        fw._LOCKS["faults"] = None
    check(spec["n"] == 0 and n == {"open": 1, "read": 1, "judge": 0, "sleep": 0}
          and fw._LOCKS["retries"] == 0,
          f"K15 release: one exclusive open, one read, no retry: {n}, retries {fw._LOCKS['retries']}", o)
    check("WARNING: operation completed, but run.lock could not be removed" in o
          and "opened and compared, but the delete was refused" in o and "WinError 5" in o
          and "cannot open it exclusively" not in o and secs < 4.0
          and lk.read_bytes() == lkr.data,
          f"K15 release: delete-refused WARNING at once ({secs:.2f}s, not after the 6.3 s open retry "
          f"budget), lock kept", o)
    lk.unlink()
    # (4) release as a real run: the run's exit code is kept
    base_rc = run(root).returncode
    check(not lk.exists(), "K15 a plain run releases (precondition)")
    r = run(root, locks=one)
    o = out(r)
    check(r.returncode == base_rc == 0, "K15 refused release keeps the run's exit code", o)
    check("WARNING: operation completed, but run.lock could not be removed" in o
          and f"LOCK: {lk}" in o and "Next run will refuse until the stale lock is cleared." in o
          and "opened and compared, but the delete was refused" in o
          and "cannot open it exclusively" not in o,
          "K15 refused release: the three WARNING lines naming the refused delete", o)
    check(lk.exists() and lock_pid(d) is not None, "K15 refused release left the run's lock", o)
    r = run(root, "--break-lock")
    check(r.returncode == 0 and "BROKE STALE LOCK" in out(r) and locks_in(d) == [],
          "K15 the leftover is then broken", out(r))
    print("K15 ok -- one refused disposition: break rc 2 at once, one open/read/judgment, no "
          "reopen, lock untouched; release keeps rc + delete-refused WARNING, lock kept")


def k14_reparse_lock(base, lad):
    """A FILE reparse-point run.lock (a hand-planted file symlink to a stale record
    outside the state dir) is opened as ITSELF (FILE_FLAG_OPEN_REPARSE_POINT):
    it reads empty, is unprovable, and is never followed or deleted --
    --break-lock refuses with hand-delete guidance (the target and the link
    untouched) and an acquirer gets the unprovable held message, not
    're-run'."""
    if os.name != "nt":
        print("K14 SKIP -- the atomic --break-lock is Windows-only (ADR-0008 Q2)")
        return
    root = fresh(base, "k14")
    d = default_dir(lad, root)
    d.mkdir(parents=True, exist_ok=True)
    target = base / "k14-outside-target.lock"
    data = json.dumps(rec(dead_pid(), 1)).encode()
    target.write_bytes(data)
    lk = d / "run.lock"
    try:
        os.symlink(target, lk)
    except OSError as e:
        print(f"K14 SKIP -- cannot create a symlink here ({e.__class__.__name__}: {e})")
        return
    try:
        r = run(root, "--break-lock")
        o = out(r)
        check(r.returncode == 2 and "unprovable" in o and "by hand ONLY" in o and "BROKE" not in o,
              "K14 --break-lock on a symlinked run.lock refuses (unprovable)", o)
        check(target.read_bytes() == data and os.path.islink(lk),
              "K14 the target is byte-unchanged and the link still present", o)
        r = run(root)
        o = out(r)
        check(r.returncode == 2 and "holder status: unprovable" in o
              and "by hand ONLY after confirming" in o and RERUN not in o,
              "K14 acquirer: unprovable held message with hand-delete guidance, not 're-run'", o)
        check(target.read_bytes() == data and os.path.islink(lk), "K14 untouched after the acquirer", o)
    finally:
        if os.path.lexists(lk):
            os.unlink(lk)  # the link itself; the target stays
    print("K14 ok -- symlinked run.lock: never followed or deleted; break + acquirer refuse with "
          "hand-delete guidance")


def main():
    with tempfile.TemporaryDirectory(prefix="wikillm161-") as td:
        base = Path(td)
        lad = base / "localappdata"
        os.environ["LOCALAPPDATA"] = str(lad)  # never the real %LOCALAPPDATA%
        for case in (k1_live_holder, k2_link_race, k3_stale, k4_unprovable, k5_no_hardlinks,
                     k6_release, k7_verify_state_dir, k8_placement_usage, k9_sibling_spelling,
                     k10_uncreatable_default, k11_break_race, k12_handle_window,
                     k13_readonly_lock, k14_reparse_lock, k15_dispose_refused):
            case(base, lad)
    print("OK -- ADR-0006/0008 run-lock matrix passed (K1-K15)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
