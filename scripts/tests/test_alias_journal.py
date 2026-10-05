"""regression R157 / ADR-0005 R5 + R5b: the apply journal's crash accounting, and
the 11-missed-files case.

R5 crash matrix. The journal keeps its write-ahead shape -- per batch an
intent save, an after-write save and a committed save, plus a final save.
A synthetic vault of 10 rewritable pages is applied in batches of 4 (4 + 4 +
2: saves 1-10, pages 1-10) and the process is hard-killed (os._exit, no
cleanup) at every journal transition: immediately before and after each of
the 10 saves (intent / after-write / committed of every batch, and the
final save), and after the first, a middle and the last page of every batch
-- including 'page successfully written -> the next journal save crashes'
(before-save 2 / 5 / 8). After each crash (once --break-lock has cleared the
killed run's stale run lock, asserted, ADR-0006):
  * no page differs from the original unless a durable write-ahead intent
    names it (no write without a journal), and no *.tmp-alias-fix is left;
  * the first --restore accounts for every page of the on-disk journal
    exactly once: REVIEW_REQUIRED 0 and RESTORED + ALREADY_ORIGINAL == all
    logged pages, RESTORED == the pages that differ; the vault is byte-exact;
  * a second --restore reports RESTORED 0, ALREADY_ORIGINAL N+M, REVIEW 0;
  * a crash before the first save leaves no journal and no write.
A restore that is itself killed after 1 / 3 pages is finished by the next
restore with the same accounting.

R5b the 11-missed-files case (a cloud-synced copy of one large vault):
the pinned legacy in-vault-state tool (1c48b84 -- byte-identical, modulo CRLF, to the
recorded tool_sha256 in that run's apply log) crashed in batch 8 of 8 on a
WinError 5 apply-log save (7 committed batches + batch 8 'writing' with 28
files, 308 files); the first --restore showed 'RESTORED = 297' (the session
grepped only ^RESTORED|^ERROR); the next --apply refused 'still has 11
files as it wrote them'; a second restore gave RESTORED 11 /
ALREADY_ORIGINAL 297 / REVIEW_REQUIRED 0, and the log then held restores: 2.
  * REPRODUCED with the pinned tool: a transient lock on 11 of the first
    restore's own vault operations -- the page rename (WinError 5), the
    page read or the backup read (backups sat in Dropbox too; a read is
    refused in the shape CPython's open() really raises for a sharing
    violation: PermissionError errno 13, winerror None -- probed with
    CreateFileW share 0) -- makes those 11 rows REVIEW_REQUIRED (exit 1, no
    traceback, ledger saved; a locked page read is labelled
    'changed-since-apply', a false human-edit claim), and every observed
    fact follows exactly, including the grep-visible output and restores: 2.
  * ELIMINATED: restore crashing on its own ledger save (the leading
    hypothesis) gives the grep-visible 'RESTORED = 308', a second restore of
    0 / 308 and restores: 1; a stale on-disk journal (batch 8's 28 pages were
    'pending' or 'written' -- both are restore rows); rotated prev logs (a
    fresh run had none); status filtering (only never-written statuses were
    skipped, and none occurred).
  * FIXED: the current tool retries every winerror 5/32/33 lock (LOCK
    RETRIES) and restores all 308 on the first run; the apply-log lock that
    crashed the apply is absorbed (rc 0) or, when it outlasts the retry,
    stops cleanly (exit 3, no traceback, RESTORE printed). A read refused
    in the real errno-13 / winerror-None shape is NOT retried (R4 retries
    winerror 5/32/33 only -- the owner kept that, owner ruling 1), but it is
    never a human edit any more: restore STOPS at the first refused page (R4,
    decision 2) -- the pages before it restored, the 11 refused pages
    'restore-read-failed', every other later page 'restore-not-attempted' and
    unwritten, a loud RESTORE INCOMPLETE, no SET ASIDE -- and the printed
    RESTORE finishes (third restore 0 / 308 / 0).

Run: python -B scripts/tests/test_alias_journal.py      Exit 0 = pass.
Temp vaults only; --state-dir <vault>.state and LOCALAPPDATA in a temp dir.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.dont_write_bytecode = True
import test_fix_wikilinks_alias as A  # noqa: E402
import test_alias_state as T  # noqa: E402  (the pinned legacy in-vault-state tool)
from test_alias_state_sync_env import synthetic_vault  # noqa: E402

SCRIPTS = A.SCRIPTS
LOCKS = "FIX_WIKILINKS_TEST_LOCKS"
CRASH_RC = 77

CRASH = '''import os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, {scripts!r})
from pathlib import Path
import fix_wikilinks as fw
point, k = sys.argv[1], int(sys.argv[2])
n = {{"save": 0, "page": 0, "restore": 0}}
def die():
    sys.stdout.flush()
    os._exit({rc})
orig_wj = fw._write_json
def wj(path, obj):
    p = Path(path)
    if p.name == "apply-log.json" and p.parent.parent.name == "backup":
        n["save"] += 1
        if point == "before-save" and n["save"] == k:
            die()
        orig_wj(path, obj)
        if point == "after-save" and n["save"] == k:
            die()
        return None
    return orig_wj(path, obj)
fw._write_json = wj
orig_wp = fw._write_page
def wp(root, rel, data, retry_if, kind="page"):
    out = orig_wp(root, rel, data, retry_if, kind)
    n[kind] += 1
    if point == "after-" + kind and n[kind] == k:
        die()
    return out
fw._write_page = wp
sys.argv = ["fix_wikilinks.py"] + sys.argv[3:]
sys.exit(fw.main())
'''


def crash_run(base, root, point, k, *args):
    w = base / "crash-wrapper.py"
    w.write_text(CRASH.format(scripts=str(SCRIPTS), rc=CRASH_RC), encoding="utf-8")
    env = dict(os.environ)
    env.pop(A.FAULT_ENV, None)
    env.pop(LOCKS, None)
    p = subprocess.run([sys.executable, "-B", str(w), point, str(k), "--root", str(root), *args,
                        *A.state_args(root, args)], cwd=str(root), env=env, capture_output=True,
                       text=True, encoding="utf-8")
    if p.returncode == CRASH_RC:
        # A hard-killed run leaves its run lock behind (ADR-0006): the next
        # alias run must refuse it as STALE, and --break-lock -- the operator's
        # recovery step -- must clear it before the restore this matrix tests.
        b = subprocess.run([sys.executable, "-B", str(SCRIPTS / "fix_wikilinks.py"), "--root",
                            str(root), "--alias-only", "--break-lock"], cwd=str(root), env=env,
                           capture_output=True, text=True, encoding="utf-8")
        assert b.returncode == 0 and "BROKE STALE LOCK" in b.stdout, (point, k, b.stdout + b.stderr)
    return p


def counts(p):
    got = []
    for key in ("RESTORED", "ALREADY_ORIGINAL", "REVIEW_REQUIRED"):
        m = re.search(rf"^{key} = (\d+)$", p.stdout, re.M)
        assert m, (key, p.stdout + p.stderr)
        got.append(int(m.group(1)))
    return tuple(got)


def pages(root):
    """Every vault page (legacy in-vault backups under .alias-fix-backup excluded)."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*.md")
            if not any(part.startswith(".") for part in p.relative_to(root).parts)}


def tmp_left(root):
    return sorted(p.name for p in root.rglob("*.tmp-alias-fix"))


def rid_of(root):
    return A.manifest(root)["run_id"]


def check_restores(root, pristine, logged, touched, label, rc_first=0):
    """First restore: every logged page exactly once; second: RESTORED 0."""
    r1 = A.run(root, "--alias-only", "--restore", rid_of(root))
    assert r1.returncode == rc_first, (label, r1.stdout + r1.stderr)
    assert counts(r1) == (len(touched), len(logged) - len(touched), 0), (label, counts(r1), len(touched),
                                                                          len(logged))
    assert pages(root) == pristine, (label, "vault not byte-exact after restore")
    assert tmp_left(root) == [], (label, tmp_left(root))
    r2 = A.run(root, "--alias-only", "--restore", rid_of(root))
    assert r2.returncode == 0 and counts(r2) == (0, len(logged), 0), (label, r2.stdout)
    d = A.run(root, "--alias-only")
    assert d.returncode == 0 and "WARNING: unfinished apply" not in d.stdout, (label, d.stdout)
    return counts(r1)


SAVE_NAMES = {1: "b1 intent", 2: "b1 after-write", 3: "b1 committed", 4: "b2 intent",
              5: "b2 after-write", 6: "b2 committed", 7: "b3 intent", 8: "b3 after-write",
              9: "b3 committed", 10: "final"}


def matrix(base):
    cases = [("before-save", k) for k in range(1, 11)] + [("after-save", k) for k in range(1, 11)]
    # first / middle / last page of each batch (4 + 4 + 2)
    cases += [("after-page", k) for k in (1, 2, 4, 5, 6, 8, 9, 10)]
    report = []
    for point, k in cases:
        label = f"{point} {k}" + (f" ({SAVE_NAMES[k]})" if "save" in point else "")
        root = base / f"m-{point}-{k}"
        synthetic_vault(root, link_pages=10, holders=3, links_per_page=2)
        pristine = pages(root)
        assert A.run(root, "--alias-only").returncode == 0
        rid = rid_of(root)
        p = crash_run(base, root, point, k, "--alias-only", "--apply", "--batch-size", "4")
        assert p.returncode == CRASH_RC, (label, "crash point not reached", p.stdout + p.stderr)
        log_path = A.bdir(root, rid) / "apply-log.json"
        touched = {rel for rel, b in pages(root).items() if pristine.get(rel) != b}
        assert tmp_left(root) == [], (label, tmp_left(root))
        if not log_path.exists():
            # only a crash before the very first write-ahead save
            assert (point, k) == ("before-save", 1), label
            assert not touched, (label, "a page was written with no journal", touched)
            r = A.run(root, "--alias-only", "--restore", rid)
            assert r.returncode == 2 and "no apply log" in r.stderr, (label, r.stdout + r.stderr)
            report.append(f"{label}: no journal, no write")
            continue
        logged = set(json.loads(log_path.read_text(encoding="utf-8"))["files"])
        assert touched <= logged, (label, "written without a durable intent", sorted(touched - logged))
        c = check_restores(root, pristine, logged, touched, label)
        report.append(f"{label}: logged {len(logged)}, RESTORED {c[0]} + ALREADY_ORIGINAL {c[1]}, "
                      f"REVIEW 0; second restore 0/{len(logged)}/0")
    # the "page written -> next journal save crashes" transitions, named
    for k in (2, 5, 8):
        assert any(r.startswith(f"before-save {k} ") for r in report)
    # a restore killed part-way is finished by the next restore
    for apply_point, rk in ((("before-save", 6), 3), (("after-save", 10), 1)):
        label = f"restore killed after page {rk} (apply {apply_point[0]} {apply_point[1]})"
        root = base / f"rk-{apply_point[0]}-{apply_point[1]}-{rk}"
        synthetic_vault(root, link_pages=10, holders=3, links_per_page=2)
        pristine = pages(root)
        assert A.run(root, "--alias-only").returncode == 0
        rid = rid_of(root)
        p = crash_run(base, root, apply_point[0], apply_point[1], "--alias-only", "--apply",
                      "--batch-size", "4")
        assert p.returncode == CRASH_RC, (label, p.stdout + p.stderr)
        logged = set(json.loads((A.bdir(root, rid) / "apply-log.json").read_text(encoding="utf-8"))["files"])
        touched0 = {rel for rel, b in pages(root).items() if pristine.get(rel) != b}
        q = crash_run(base, root, "after-restore", rk, "--alias-only", "--restore", rid)
        assert q.returncode == CRASH_RC, (label, q.stdout + q.stderr)
        touched = {rel for rel, b in pages(root).items() if pristine.get(rel) != b}
        assert len(touched) == len(touched0) - rk, (label, len(touched0), len(touched))
        c = check_restores(root, pristine, logged, touched, label)
        report.append(f"{label}: RESTORED {c[0]} + ALREADY_ORIGINAL {c[1]} == logged {len(logged)}")
    # A re-apply of the same manifest after a settled smoke batch keeps the
    # earlier log as apply-log.prev-*; a crash right after that, before or
    # after the new log's first save, must leave the smoke writes restorable.
    for point, k in (("before-save", 1), ("after-save", 1)):
        label = f"re-apply after a smoke batch, {point} {k}"
        root = base / f"rot-{point}"
        synthetic_vault(root, link_pages=10, holders=3, links_per_page=2)
        pristine = pages(root)
        assert A.run(root, "--alias-only").returncode == 0
        rid = rid_of(root)
        s = A.run(root, "--alias-only", "--apply", "--max-files", "1")
        assert s.returncode == 0, (label, s.stdout + s.stderr)
        p = crash_run(base, root, point, k, "--alias-only", "--apply", "--batch-size", "4")
        assert p.returncode == CRASH_RC, (label, p.stdout + p.stderr)
        logged = set()
        for lp in A.bdir(root, rid).glob("apply-log*.json"):
            logged |= set(json.loads(lp.read_text(encoding="utf-8"))["files"])
        touched = {rel for rel, b in pages(root).items() if pristine.get(rel) != b}
        assert len(touched) == 1 and touched <= logged, (label, touched)
        c = check_restores(root, pristine, logged, touched, label)
        report.append(f"{label}: logged {len(logged)}, RESTORED {c[0]} + ALREADY_ORIGINAL {c[1]}, REVIEW 0")
    for line in report:
        print(f"  {line}")
    print(f"PASS R5 crash matrix: {len(cases)} apply crash points + 2 restore crash points + 2 "
          f"log-rotation crash points; every logged page accounted exactly once, REVIEW 0, second "
          f"restore RESTORED 0")


# --- R5b ------------------------------------------------------------------

LEGACY = '''import errno, os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, {scripts!r})
from pathlib import Path
import fix_wikilinks as fw
mode, arg = sys.argv[1], sys.argv[2]
vault = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
bk = vault / ".alias-fix-backup"
names = set(arg.split(",")) if arg != "-" else set()
hit = set()
def winerror5(p):
    return OSError(errno.EACCES, "Access is denied", str(p), 5)
def rel_in(p, base):
    p = Path(p).resolve()
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        return None
if mode == "apply-crash":
    # the observed crash: os.replace of apply-log.json -> WinError 5 on save #<arg>
    orig = fw._write_json
    count = [0]
    def wj(path, obj):
        if Path(path).name == "apply-log.json":
            count[0] += 1
            if count[0] == int(arg):
                raise winerror5(path)
        return orig(path, obj)
    fw._write_json = wj
elif mode == "restore-replace":
    orig_rep = os.replace
    def rep(src, dst, *a, **k):
        rel = rel_in(dst, vault)
        if rel in names and rel not in hit:
            hit.add(rel)
            raise winerror5(dst)
        return orig_rep(src, dst, *a, **k)
    os.replace = rep
elif mode in ("restore-read", "restore-bread"):
    orig_rb = Path.read_bytes
    def rb(self):
        if mode == "restore-read":
            rel = None if rel_in(self, bk) is not None else rel_in(self, vault)
        else:
            r = rel_in(self, bk)
            rel = r.split("/", 1)[1] if r and "/" in r else None
        if rel in names and rel not in hit:
            hit.add(rel)
            # the shape CPython's open() gives a REAL Windows sharing
            # violation: errno 13, winerror None (os.replace alone carries 5)
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return orig_rb(self)
    Path.read_bytes = rb
elif mode == "restore-ledger":
    orig = fw._write_json
    def wj(path, obj):
        if Path(path).name == "apply-log.json":
            raise winerror5(path)
        return orig(path, obj)
    fw._write_json = wj
sys.argv = ["fix_wikilinks.py"] + sys.argv[3:]
sys.exit(fw.main())
'''

LARGE_FILES = 308          # files with safe rewrites in the large-vault run
LARGE_BATCHES = 8          # batch size 40: 7 x 40 + 28
MISSED = 11


def legacy(base, tool, root, mode, arg, *args):
    w = base / "legacy-wrapper.py"
    w.write_text(LEGACY.format(scripts=str(tool.parent)), encoding="utf-8")
    env = dict(os.environ)
    env.pop(A.FAULT_ENV, None)
    env.pop(LOCKS, None)
    return subprocess.run([sys.executable, "-B", str(w), mode, arg, "--root", str(root), "--alias-only",
                           *args], cwd=str(root), env=env, capture_output=True, text=True,
                          encoding="utf-8")


def visible(p):
    """What the 2026-09-30 session saw of a restore: grep -E '^(RESTORED|ERROR)'."""
    return [ln for ln in (p.stdout + p.stderr).splitlines() if re.match(r"^(RESTORED|ERROR)", ln)]


def legacy_crashed(base, tool, name):
    root = base / name
    synthetic_vault(root, link_pages=LARGE_FILES, holders=30, links_per_page=3)
    pristine = pages(root)
    d = legacy(base, tool, root, "none", "-")
    assert d.returncode == 0, d.stdout + d.stderr
    rid = json.loads((root / "_meta/alias-fix-manifest.json").read_text(encoding="utf-8"))["run_id"]
    # save 3 * 8 = 24 is batch 8's committed save (the observed traceback line)
    a = legacy(base, tool, root, "apply-crash", str(3 * LARGE_BATCHES), "--apply")
    assert a.returncode == 1 and "PermissionError: [WinError 5] Access is denied" in a.stderr, \
        a.stdout + a.stderr
    log = json.loads((root / ".alias-fix-backup" / rid / "apply-log.json").read_text(encoding="utf-8"))
    shape = [(b["status"], len(b["files"])) for b in log["batches"]]
    assert shape == [("committed", 40)] * 7 + [("writing", 28)], shape
    assert len(log["files"]) == LARGE_FILES and log["result"] == "in-progress"
    assert not list((root / ".alias-fix-backup" / rid).glob("apply-log.prev-*.json"))
    written = sorted(rel for rel, b in pages(root).items() if pristine[rel] != b)
    assert len(written) == LARGE_FILES, len(written)
    return root, pristine, rid, written


def r5b(base):
    tool = T.legacy_tool(base)
    lines = []
    # --- the observed sequence, one variant per locked restore operation ---
    for mode in ("restore-replace", "restore-read", "restore-bread"):
        root, pristine, rid, written = legacy_crashed(base, tool, "large-" + mode)
        missed = written[5::28][:MISSED]
        assert len(missed) == MISSED
        r1 = legacy(base, tool, root, mode, ",".join(missed), "--restore", rid)
        assert "Traceback" not in r1.stderr and r1.returncode == 1, (mode, r1.stdout + r1.stderr)
        assert counts(r1) == (LARGE_FILES - MISSED, 0, MISSED), (mode, counts(r1))
        assert visible(r1) == [f"RESTORED = {LARGE_FILES - MISSED}"], (mode, visible(r1))
        why = {"restore-replace": "restore-write-failed (PermissionError: [WinError 5]",
               "restore-read": "changed-since-apply", "restore-bread": "backup-missing"}[mode]
        assert r1.stdout.count(why) == MISSED, (mode, r1.stdout)
        d = legacy(base, tool, root, "none", "-")
        rid2 = json.loads((root / "_meta/alias-fix-manifest.json").read_text(encoding="utf-8"))["run_id"]
        assert rid2 != rid, "the vault is not back at its original bytes, so the run_id changes"
        a = legacy(base, tool, root, "none", "-", "--apply")
        assert a.returncode == 2, (mode, a.stdout + a.stderr)
        assert (f"unfinished apply {rid} (in-progress) still has {MISSED} files as it wrote them"
                in a.stdout), (mode, a.stdout)
        r2 = legacy(base, tool, root, "none", "-", "--restore", rid)
        assert r2.returncode == 0 and counts(r2) == (MISSED, LARGE_FILES - MISSED, 0), (mode, r2.stdout)
        log = json.loads((root / ".alias-fix-backup" / rid / "apply-log.json").read_text(encoding="utf-8"))
        assert len(log["restores"]) == 2 and log["result"] == "restored", (mode, log.get("restores"))
        assert pages(root) == pristine
        lines.append(f"pinned tool, lock on restore {mode[8:]}: visible {visible(r1)}; apply refused "
                     f"'still has {MISSED} files'; second restore {counts(r2)}; restores 2 -- REPRODUCED")
    # --- the leading hypothesis: restore crashed on its own ledger save ---
    root, pristine, rid, written = legacy_crashed(base, tool, "large-ledger")
    r1 = legacy(base, tool, root, "restore-ledger", "-", "--restore", rid)
    assert r1.returncode == 1 and "Traceback" in r1.stderr, r1.stdout + r1.stderr
    assert visible(r1) == [f"RESTORED = {LARGE_FILES}"], visible(r1)
    d = legacy(base, tool, root, "none", "-")
    rid2 = json.loads((root / "_meta/alias-fix-manifest.json").read_text(encoding="utf-8"))["run_id"]
    assert rid2 == rid, "a fully restored vault re-plans to the same run_id"
    r2 = legacy(base, tool, root, "none", "-", "--restore", rid)
    assert counts(r2) == (0, LARGE_FILES, 0), counts(r2)
    log = json.loads((root / ".alias-fix-backup" / rid / "apply-log.json").read_text(encoding="utf-8"))
    assert len(log["restores"]) == 1, log.get("restores")
    lines.append(f"pinned tool, crash on the restore ledger save: visible {visible(r1)}; second restore "
                 f"{counts(r2)}; restores 1; same run_id -- does NOT match (ELIMINATED)")
    # --- the current tool under the same faults ---
    root = base / "large-new"
    synthetic_vault(root, link_pages=LARGE_FILES, holders=30, links_per_page=3)
    pristine = pages(root)
    assert A.run(root, "--alias-only").returncode == 0
    rid = rid_of(root)
    faults = {"state:apply-log.json": {"n": 7, "skip": 3 * LARGE_BATCHES - 1}}
    a = A.run(root, "--alias-only", "--apply", env_extra={LOCKS: json.dumps(faults)})
    assert a.returncode == 3 and "Traceback" not in a.stderr, a.stdout + a.stderr
    assert "postcondition OK, but APPLY LOG SAVE FAILED" in a.stdout and "RESTORE: " in a.stdout, a.stdout
    written = sorted(rel for rel, b in pages(root).items() if pristine[rel] != b)
    assert len(written) == LARGE_FILES
    sets = [written[i::28][:MISSED] for i in (3, 11, 19)]
    assert len({x for s in sets for x in s}) == 3 * MISSED
    rf = {}
    for kind, n, s in (("restore", 2, sets[0]), ("read", 1, sets[1]), ("bread", 1, sets[2])):
        rf.update({f"{kind}:{rel}": n for rel in s})
    r1 = A.run(root, "--alias-only", "--restore", rid, env_extra={LOCKS: json.dumps(rf)})
    assert r1.returncode == 0 and counts(r1) == (LARGE_FILES, 0, 0), r1.stdout + r1.stderr
    lk = re.search(r"^LOCK RETRIES: (\d+) ", r1.stdout, re.M)
    assert lk and int(lk.group(1)) == 2 * MISSED + MISSED + MISSED, r1.stdout[-500:]
    assert pages(root) == pristine
    r2 = A.run(root, "--alias-only", "--restore", rid)
    assert counts(r2) == (0, LARGE_FILES, 0), r2.stdout
    # the REAL read-lock shape (errno 13, winerror None) is not a lock code,
    # so R4 does not retry it -- but the current tool never calls it a human
    # edit: 11 transient restore-read-failed rows, a loud RESTORE INCOMPLETE,
    # no SET ASIDE, and the printed RESTORE finishes the other 11
    root = base / "large-new-errno"
    synthetic_vault(root, link_pages=LARGE_FILES, holders=30, links_per_page=3)
    pristine = pages(root)
    assert A.run(root, "--alias-only").returncode == 0
    rid = rid_of(root)
    assert A.run(root, "--alias-only", "--apply").returncode == 0
    written = sorted(rel for rel, b in pages(root).items() if pristine[rel] != b)
    assert len(written) == LARGE_FILES
    missed = written[5::28][:MISSED]
    rf = {f"read:{rel}": {"n": 1, "code": None} for rel in missed}
    r1 = A.run(root, "--alias-only", "--restore", rid, env_extra={LOCKS: json.dumps(rf)})
    assert r1.returncode == 1 and "Traceback" not in r1.stderr, r1.stdout[-1500:] + r1.stderr
    # R4 stop (owner ruling 2): restore stops at the first refused
    # page (index `stop` in restore order); the pages before it are restored,
    # it and every later page stay as the apply wrote them -- the other
    # refused pages keep restore-read-failed, the rest are restore-not-attempted.
    stop = written.index(missed[0])
    assert counts(r1) == (stop, 0, LARGE_FILES - stop), counts(r1)
    assert r1.stdout.count("(restore-read-failed (page: PermissionError") == MISSED, r1.stdout[-2000:]
    assert r1.stdout.count(f"(restore-not-attempted (restore stopped at {missed[0]} ") == \
        LARGE_FILES - stop - MISSED, r1.stdout[-2000:]
    now = pages(root)
    assert all(now[rel] != pristine[rel] for rel in written[stop:]), "a page was written after the stop"
    assert f"RESTORE INCOMPLETE: {LARGE_FILES - stop} file(s)" in r1.stdout and "SET ASIDE" not in r1.stdout, \
        r1.stdout[-2000:]
    assert "changed-since-apply" not in r1.stdout, r1.stdout[-2000:]
    assert any(ln.startswith("RESTORE: ") and f"--restore {rid}" in ln for ln in r1.stdout.splitlines())
    r2 = A.run(root, "--alias-only", "--restore", rid)
    assert r2.returncode == 0 and counts(r2) == (LARGE_FILES - stop, stop, 0), r2.stdout[-1500:]
    assert pages(root) == pristine
    r3 = A.run(root, "--alias-only", "--restore", rid)
    assert counts(r3) == (0, LARGE_FILES, 0), r3.stdout[-800:]
    lines.append(f"current tool, {MISSED} errno-13 (real-shape) page-read refusals on the first restore: "
                 f"{counts(r1)} (stops at the first refused page) with RESTORE INCOMPLETE {LARGE_FILES - stop} and no SET ASIDE (was "
                 f"'changed-since-apply' + SET ASIDE); second restore {counts(r2)}; third {counts(r3)}")
    # the apply-log lock that crashed the large-vault apply is absorbed when it clears
    root = base / "large-new-ok"
    synthetic_vault(root, link_pages=LARGE_FILES, holders=30, links_per_page=3)
    assert A.run(root, "--alias-only").returncode == 0
    faults = {"state:apply-log.json": {"n": 3, "skip": 3 * LARGE_BATCHES - 1}}
    a = A.run(root, "--alias-only", "--apply", env_extra={LOCKS: json.dumps(faults)})
    assert a.returncode == 0 and "LOCK RETRIES: 3 (max wait 0.7s)" in a.stdout, a.stdout[-1500:]
    lines.append(f"current tool: apply-log lock outlasting the retry -> exit 3 clean with RESTORE; "
                 f"restore under {3 * MISSED} locked page-write/page-read/backup-read ops -> "
                 f"RESTORED {LARGE_FILES} first time (LOCK RETRIES {4 * MISSED}); a clearing apply-log "
                 f"lock -> rc 0 -- FIXED")
    for ln in lines:
        print(f"  {ln}")
    print("PASS R5b 11-missed-files case: cause = transient locks on the first restore's own Dropbox "
          "file operations (a WinError 5 rename or an errno-13 read; REVIEW_REQUIRED 11, exit 1, hidden "
          "by the grep); the ledger-crash hypothesis is eliminated; the current tool restores all 308 "
          "first time under winerror locks, and under errno-13 read refusals says RESTORE INCOMPLETE "
          "11 (no SET ASIDE) and the printed RESTORE finishes")


def main():
    with tempfile.TemporaryDirectory(prefix="wikillm157j-") as td:
        base = Path(td)
        A.isolate_state(base)
        real = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "AppData" / "Local" / "WIKIllm"
        before_real = real.exists()
        matrix(base)
        r5b(base)
        assert real.exists() == before_real, "the real %LOCALAPPDATA%/WIKIllm changed"
    print("ALL PASS (R5 crash matrix, R5b)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
