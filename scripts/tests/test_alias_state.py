"""ADR-0005 / regression R153: fix_wikilinks.py --alias-only keeps its operational
state (manifest, apply logs, page backups) in a state directory OUTSIDE the
vault. Pins the frozen requirements R1 (location), R2 (discovery, registry,
fail-closed, --forget-state-dir, STATE lines, dry run writes nothing in the
vault), R3 (--migrate-legacy-state) and R7 (identity, --rebind-state).

L1  default dir = %LOCALAPPDATA%/WIKIllm/alias-fix/<name>-<hash8>, hash8 pinned
    by an independent computation; STATE line; backups + log there, the log's
    backup field state-dir relative; a dry run / --verify / migrate dry run
    leave the vault's hash tree (files AND dirs) unchanged
L2  refusals, nothing written anywhere: --state-dir inside the vault,
    --manifest inside the vault, LOCALAPPDATA pointing into the vault
L3  a tampered backup field pointing outside its run dir -> backup-missing,
    the page left as the apply wrote it
L4  a --state-dir whose backup/ tree lands inside the vault (vault folder
    named 'backup', state dir = its parent) is refused, nothing written; a
    registered pointer to one is not trusted
L5  Windows: unset / empty LOCALAPPDATA refused (no ~/.local/state
    fallback), with or without --state-dir; nothing written anywhere
L6  Windows: a Microsoft Store (MSIX) Python -- a WindowsApps sys.executable
    or a Store base_prefix, forced by a wrapper -- refused for every mode,
    with or without --state-dir; nothing written anywhere
D1--state-dir registers in <default>/state-dirs.json (REGISTERED IN line);
    a run WITHOUT the flag discovers its unfinished run, prints a RESTORE
    carrying --state-dir, refuses --apply, and that RESTORE restores
D2  unreachable registered dir: dry run warns 'cannot prove', --apply and
    --restore fail closed (2), --verify 1; in-vault pointer and a corrupt
    registry fail closed too; a corrupt registry refuses a new --state-dir;
    with legacy state planted, migrate --apply fails closed (D5 too)
D3  --forget-state-dir: refused unregistered / unreachable / unfinished,
    removes a settled pointer
D4  one run, one location: --apply of a run_id with logs elsewhere and
    --restore of a run_id found twice are refused
D5  (Windows icacls) a registered dir whose backup/ cannot be listed: dry
    run warns 'cannot prove', --apply / --restore 2, --verify 1,
    --forget-state-dir refused; the scan reports it, never empty
M1 legacy state made by the pinned legacy in-vault-state tool (a complete run, a failed
    run, a <rid>-inspected set-aside, the manifest): migrate dry run writes
    nothing; a verification failure keeps that source AND the manifest;
    the re-run migrates the rest, deletes the manifest last, legacy scan 0;
    the failed run restores byte-exact from the new location
M2  an interrupted delete (logs gone, rest left) resumes on re-run
M3  a legacy run of a vault MOVED after its apply migrates and still
    restores (carried root acceptance, bound to this vault)
M4  a log-less legacy <run_id> remnant migrates as a plain entry (first
    run, legacy scan 0) instead of failing its discovery proof forever
M5  the discovery-proof gate: each proof check failing (find / classify /
    unfinished) and an mtime-only copy mismatch keep source + manifest and
    remove the unproven copy (run listed once, RESTORE works); a resumed
    copy is kept
M6  interrupted delete of a two-log legacy run (apply-log.json + prev): the
    prev-only remnant resumes to legacy scan 0 and the printed RESTORE
    works; prev logs are deleted before apply-log.json (order pinned)
I1 same-folder-name state: settled -> warning only (apply allowed);
    unfinished -> --apply / --restore blocked; --rebind-state refused while
    OLD_PATH exists, allowed after the move, and the old run then restores
    byte-exact with 'NOTE: vault moved from'
I2  --rebind-state onto a path with no state yet renames the whole old key
    dir (registry included); a run in its registered --state-dir restores
I3  same-name via the other key's registered --state-dir: an unfinished run
    there blocks; unreachable dir / corrupt sibling registry / sibling
    pointer into this vault / unlistable sibling backup dir block too
    ('cannot prove', never '0 settled'); migrate --apply fails closed
I4  --rebind-state onto an existing key when the old key's state-dirs.json
    merges nothing (empty after --forget-state-dir, or only non-dict
    entries): rc 0, no traceback (was WinError 145 after the runs moved),
    rebound_from recorded, the moved unfinished run restores byte-exact; a
    re-run finishes the stub the old crash left; a failure mid-move exits 3
    (REBIND INCOMPLETE, grant already recorded) and a re-run finishes it
I5  --rebind-state onto a key that does not exist yet: the same grant-FIRST
    planned move (an exhausted vault.json grant save -> exit 3 REBIND
    INCOMPLETE, nothing moved, the re-run finishes); the stranded shape the
    pre-fix tool left (whole old dir renamed, old vault.json) is named by
    the dry run and repaired by a re-run; both runs restore 2/0/0 then 0/2/0
I6  one state dir per vault: a byte copy naming the original's --state-dir
    is refused (dry run / --apply / --restore / --verify, nothing written,
    the original's unfinished log + manifest untouched, it restores); a
    registered dir whose vault.json names another vault fails closed
L7  (Windows) in-vault spellings -- \\\\?\\<vault>\\state, \\\\localhost\\c$\\...,
    a trailing-space or trailing-dot component -- refused for --state-dir
    and --manifest, the vault hash tree unchanged
L8  (Windows) one outside --state-dir under the plain, \\\\?\\ and admin-share
    spellings: one registry entry, --restore works under each and without
D6  registering a --state-dir (even by --verify) creates it with its vault
    id, so the next --apply is not blocked; an unreachable registered dir
    names --forget-state-dir
U1  (in-process) the identity fallback never merges two paths on a volume
    without file IDs (st_ino 0); alternate spellings of one dir are equal

Run: python -B scripts/tests/test_alias_state.py      Exit 0 = pass.
Every run uses temp dirs; LOCALAPPDATA is pointed at a temp dir, so the real
%LOCALAPPDATA%/WIKIllm is never touched.
"""
from legacy_fixture import legacy_result
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "fix_wikilinks.py"
FAULT_ENV = "FIX_WIKILINKS_TEST_FAULT"
LEGACY_TOOL = "1c48b84"  # legacy in-vault-state release: the in-vault state layout
SOIL = "---\ntitle: Soil pH\naliases: [Soil pH]\n---\n# Soil pH\n"
FILES = {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": "a [[Soil pH]]\n",
         "wiki/b.md": "b [[Soil pH]]\n", "wiki/c.md": "c [[Soil pH]]\n"}


def run(root, *args, state=None, env_extra=None, script=SCRIPT, alias=True, env_drop=()):
    env = dict(os.environ)
    env.pop(FAULT_ENV, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(env_extra or {})
    for k in env_drop:
        env.pop(k, None)
    argv = [sys.executable, "-B", str(script), "--root", str(root)]
    argv += ["--alias-only"] if alias else []
    argv += list(args) + (["--state-dir", str(state)] if state is not None else [])
    return subprocess.run(argv, cwd=str(root), env=env, capture_output=True, text=True,
                          encoding="utf-8")


def out(p):
    return p.stdout + p.stderr


def write_vault(root, files):
    for rel, content in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content.encode("utf-8"))


def tree(root):
    """Every file (sha256) AND directory under `root`: the before/after
    evidence that nothing was created, changed or removed in the vault."""
    t = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        t[rel] = "<dir>" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
    return t


def pages(root):
    return {k: v for k, v in tree(root).items() if k.startswith("wiki/")}


def expected_default(lad, root):
    """Independent of the tool: <LOCALAPPDATA>/WIKIllm/alias-fix/<name>-<hash8>."""
    key = os.path.normcase(str(Path(root).resolve())).replace("\\", "/")
    h8 = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return Path(os.path.abspath(str(lad))) / "WIKIllm" / "alias-fix" / f"{Path(root).resolve().name}-{h8}"


def rid_of(state_dir):
    return json.loads((state_dir / "manifest.json").read_text(encoding="utf-8"))["run_id"]


def restore_line(text):
    for ln in text.splitlines():
        if ln.startswith("RESTORE: "):
            return ln[len("RESTORE: "):]
    raise AssertionError(text)


def run_cmdline(cmd, cwd):
    """Run a printed RESTORE command line exactly (quoted args)."""
    import shlex
    parts = shlex.split(cmd.replace("\\", "/"), posix=True)
    assert parts[0] == "python", parts
    return subprocess.run([sys.executable, "-B", *parts[1:]], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8")


def legacy_tool(base):
    sd = base / "legacy-tool" / "scripts"
    if not sd.is_dir():
        sd.mkdir(parents=True)
        for n in ("fix_wikilinks.py", "_wikilib.py", "maintenance_preflight.py"):
            blob = legacy_result(f"{LEGACY_TOOL}:scripts/{n}", check=True).stdout
            (sd / n).write_bytes(blob)
    assert b"--state-dir" not in (sd / "fix_wikilinks.py").read_bytes(), "legacy pin is not legacy"
    return sd / "fix_wikilinks.py"


WRAPPER = '''import sys
sys.dont_write_bytecode = True
sys.path.insert(0, {scripts!r})
import fix_wikilinks as fw
mode, arg = sys.argv[1], sys.argv[2]
if mode == "corrupt-copy":
    # the staging copy of run <arg> differs from its source -> verification fails
    orig = fw._copy_entry
    def bad(src, dst):
        orig(src, dst)
        if src.name == arg:
            (dst / "apply-log.json").write_bytes(b"{{}}")
    fw._copy_entry = bad
elif mode == "die-mid-delete":
    # the delete of run <arg> removes its apply logs, then the process dies
    orig = fw._remove_entry
    def half(p):
        if p.name == arg:
            for lg in sorted(p.glob("apply-log*.json")):
                lg.unlink()
            raise OSError("simulated crash mid-delete")
        return orig(p)
    fw._remove_entry = half
elif mode == "drop-current-log":
    # the delete of legacy run <arg> removes ONLY apply-log.json, then dies:
    # the prev-only remnant the old delete order (apply-log.json first) left
    orig = fw._remove_entry
    def drop(p):
        if p.name == arg and fw.LEGACY_BACKUP in p.parts:
            (p / "apply-log.json").unlink()
            raise OSError("simulated crash after deleting apply-log.json")
        return orig(p)
    fw._remove_entry = drop
elif mode == "fail-current-unlink":
    # unlinking legacy run <arg>'s apply-log.json fails (a lock that outlasts
    # the retry): whatever the delete removed before it is what an
    # interruption leaves -- pins the delete order
    from pathlib import Path
    orig_unlink = Path.unlink
    def unlink(self, *a, **k):
        if self.name == "apply-log.json" and self.parent.name == arg and fw.LEGACY_BACKUP in self.parts:
            raise OSError("simulated lock on the legacy apply-log.json")
        return orig_unlink(self, *a, **k)
    Path.unlink = unlink
elif mode == "proof-find":
    # discovery does not find run <arg> in the state dir after the copy
    orig = fw.State.find_run
    def find_run(self, rid, legacy=True):
        return [] if (rid == arg and not legacy) else orig(self, rid, legacy)
    fw.State.find_run = find_run
elif mode in ("proof-classify", "proof-unfinished"):
    # restore (or the unfinished-run check) sees the migrated copy -- any run
    # dir outside the vault -- differently from the legacy source
    fname = "_restore_view" if mode == "proof-classify" else "_unfinished_view"
    orig_view = getattr(fw, fname)
    def view(root, *a):
        v = orig_view(root, *a)
        run_dir = a[1] if fname == "_restore_view" else a[0]
        return v if fw._inside(run_dir, root) else v + (("perturbed",),)
    setattr(fw, fname, view)
elif mode == "touch-copy":
    # the staging copy of run <arg> has the same bytes but another mtime
    import os
    orig = fw._copy_entry
    def touch(src, dst):
        orig(src, dst)
        if src.name == arg:
            f = dst / "apply-log.json"
            s = f.stat()
            os.utime(f, ns=(s.st_atime_ns, s.st_mtime_ns + 5000000000))
    fw._copy_entry = touch
elif mode == "unlist-base":
    # the backup base <arg> (another key's state) cannot be listed -- an
    # ACL-denied or transiently failing dir, portable (no icacls)
    from pathlib import Path
    orig_rd = fw._run_dirs
    deny = fw._key_text(Path(arg))
    def run_dirs(b, strict=False):
        if strict and fw._key_text(b) == deny:
            raise PermissionError(13, "[test] listing denied", str(b))
        return orig_rd(b, strict)
    fw._run_dirs = run_dirs
elif mode == "fail-manifest-move":
    # --rebind-state: moving manifest.json out of the old key dir <arg> fails
    # (a lock that outlasts everything) AFTER its backup/<rid> run has moved
    import os
    from pathlib import Path
    orig_replace = os.replace
    deny = fw._key_text(Path(arg))
    def replace(src, dst, *a, **k):
        s = Path(src)
        if s.name == "manifest.json" and fw._key_text(s.parent) == deny:
            raise PermissionError(13, "[test] manifest move refused", str(s))
        return orig_replace(src, dst, *a, **k)
    os.replace = replace
elif mode == "msix-exe":
    # the interpreter looks like the Microsoft Store (MSIX) Python: its
    # sys.executable is the WindowsApps alias <arg>
    sys.executable = arg
    sys._base_executable = arg
elif mode == "msix-prefix":
    # a venv (or any interpreter) whose base install is a Store package
    # under Program Files\\WindowsApps: sys.executable looks normal
    sys.base_prefix = arg
sys.argv = ["fix_wikilinks.py"] + sys.argv[3:]
sys.exit(fw.main())
'''


def run_wrapped(base, root, mode, arg, *args, state=None):
    w = base / "state-wrapper.py"
    w.write_text(WRAPPER.format(scripts=str(SCRIPTS)), encoding="utf-8")
    argv = [sys.executable, "-B", str(w), mode, arg, "--root", str(root), "--alias-only", *args]
    argv += ["--state-dir", str(state)] if state is not None else []
    env = dict(os.environ)
    env.pop(FAULT_ENV, None)
    return subprocess.run(argv, cwd=str(root), env=env, capture_output=True, text=True, encoding="utf-8")


def l1_location(base, lad):
    root = base / "l1" / "Farm Vault"
    write_vault(root, FILES)
    t0 = tree(root)
    want = expected_default(lad, root)
    d = run(root)
    assert d.returncode == 0, out(d)
    assert f"STATE: {want}" in d.stdout.splitlines(), (want, d.stdout)
    assert "REGISTERED IN:" not in d.stdout, d.stdout
    assert (want / "manifest.json").is_file() and (want / "vault.json").is_file(), sorted(want.rglob("*"))
    assert tree(root) == t0, "dry run changed the vault tree"
    v = run(root, "--verify")
    assert v.returncode == 1 and f"STATE: {want}" in v.stdout, out(v)
    assert tree(root) == t0, "--verify changed the vault tree"
    mg = run(root, "--migrate-legacy-state")
    assert mg.returncode == 0 and "Nothing to migrate: the legacy scan is zero." in mg.stdout, out(mg)
    assert tree(root) == t0, "migrate dry run changed the vault tree"
    rid = rid_of(want)
    ap = run(root, "--apply")
    assert ap.returncode == 0 and "Applied: 3 edits" in ap.stdout, out(ap)
    assert f"STATE: {want}" in ap.stdout and f"Backups: {want / 'backup' / rid}" in ap.stdout, ap.stdout
    lg = json.loads((want / "backup" / rid / "apply-log.json").read_text(encoding="utf-8"))
    for rel, e in lg["files"].items():
        assert e["backup"] == f"backup/{rid}/{rel}", e
        assert (want / e["backup"]).read_bytes() == FILES[rel].encode(), rel
    assert set(tree(root)) == set(t0), "apply created or removed something in the vault"
    assert not (root / ".alias-fix-backup").exists() and not (root / "_meta").exists()
    r = run(root, "--restore", rid)
    assert r.returncode == 0 and "RESTORED = 3" in r.stdout and f"STATE: {want}" in r.stdout, out(r)
    assert tree(root) == t0, "restore not byte-exact"
    print(f"PASS L1 default state dir {want.name} (hash8 independently computed); STATE line; dry run, "
          f"--verify and migrate dry run leave the vault hash tree unchanged; backups + apply log in the "
          f"state dir with state-relative backup fields; restore byte-exact")


def l2_refusals(base, lad):
    root = base / "l2"
    write_vault(root, FILES)
    t0 = tree(root)
    lad_before = tree(lad) if lad.exists() else {}
    for args, needle in ((("--state-dir", str(root / "state")), "--state-dir"),
                         (("--state-dir", str(root)), "--state-dir"),
                         (("--manifest", str(root / "_meta" / "m.json")), "--manifest"),
                         (("--apply", "--manifest", str(root / "m.json")), "--manifest")):
        p = run(root, *args)
        assert p.returncode == 2 and needle in p.stderr and "inside the vault" in p.stderr \
            and "nothing written" in p.stderr, (args, out(p))
        assert tree(root) == t0, args
        assert (tree(lad) if lad.exists() else {}) == lad_before, ("wrote under LOCALAPPDATA", args)
    p = run(root, env_extra={"LOCALAPPDATA": str(root / "appdata")})
    assert p.returncode == 2 and "LOCALAPPDATA points into it" in p.stderr, out(p)
    assert tree(root) == t0
    print("PASS L2 --state-dir / --manifest / LOCALAPPDATA inside the vault -> exit 2, nothing written "
          "(vault tree and LOCALAPPDATA tree unchanged)")


def failed_apply(root, state=None, fault="wiki/b.md"):
    """dry run + an apply that fails at batch 2 of 3 (a.md, b.md written)."""
    assert run(root, state=state).returncode == 0
    p = run(root, "--apply", "--batch-size", "1", state=state, env_extra={FAULT_ENV: fault})
    assert p.returncode == 3 and "broken count rose" in p.stdout, out(p)
    return p


LEGACY_RID = "0123456789abcdef"


def plant_legacy(root):
    """A settled legacy-layout run + stale manifest in the vault, so
    --migrate-legacy-state --apply gets past 'Nothing to migrate' to its
    fail-closed gate. Returns a remover."""
    run_dir = root / ".alias-fix-backup" / LEGACY_RID
    run_dir.mkdir(parents=True)
    (run_dir / "apply-log.json").write_text(json.dumps(
        {"run_id": LEGACY_RID, "result": "complete", "files": {}}), encoding="utf-8")
    made_meta = not (root / "_meta").exists()
    (root / "_meta").mkdir(exist_ok=True)
    (root / "_meta/alias-fix-manifest.json").write_text(json.dumps({"run_id": LEGACY_RID}),
                                                        encoding="utf-8")

    def remove():
        shutil.rmtree(root / ".alias-fix-backup")
        (root / "_meta/alias-fix-manifest.json").unlink()
        if made_meta:
            (root / "_meta").rmdir()
    return remove


def migrate_fails_closed(root, why, state=None):
    """With legacy state planted and a fail-closed reason present: the migrate
    dry run proceeds (warning), --migrate-legacy-state --apply exits 2 and
    writes nothing -- legacy source + manifest kept, no copy, no registry
    change anywhere under LOCALAPPDATA."""
    lad = Path(os.environ["LOCALAPPDATA"])
    remove = plant_legacy(root)
    t0 = tree(root)
    d = run(root, "--migrate-legacy-state", state=state)
    assert d.returncode == 0 and why in d.stdout and "(Dry run -- nothing written" in d.stdout, out(d)
    assert tree(root) == t0, "a migrate dry run changed the vault"
    l0 = tree(lad)
    p = run(root, "--migrate-legacy-state", "--apply", state=state)
    assert p.returncode == 2 and "--migrate-legacy-state --apply fails closed" in p.stderr, out(p)
    assert why in p.stdout, out(p)
    assert tree(root) == t0, "a fail-closed migrate changed the vault (legacy state not kept)"
    assert tree(lad) == l0, "a fail-closed migrate wrote state (copy or registry)"
    remove()


def l3_backup_traversal(base, lad):
    root = base / "l3"
    write_vault(root, FILES)
    sd = expected_default(lad, root)
    failed_apply(root)
    rid = rid_of(sd)
    log = sd / "backup" / rid / "apply-log.json"
    lg = json.loads(log.read_text(encoding="utf-8"))
    # A backup "outside the run's backup dir": the vault page itself (absolute)
    # and a ../ escape into the state dir's manifest.
    lg["files"]["wiki/a.md"]["backup"] = str(root / "wiki/c.md")
    lg["files"]["wiki/b.md"]["backup"] = f"backup/{rid}/../../manifest.json"
    log.write_text(json.dumps(lg), encoding="utf-8")
    before = tree(root)
    r = run(root, "--restore", rid)
    assert r.returncode == 1, out(r)
    assert "wiki/a.md  (backup-missing)" in r.stdout and "wiki/b.md  (backup-missing)" in r.stdout, r.stdout
    assert "RESTORED = 0" in r.stdout, r.stdout
    assert tree(root) == before, "a backup outside its run dir was copied into the vault"
    print("PASS L3 a backup field naming a path outside the run's backup dir (absolute vault path, "
          "../ escape) -> REVIEW_REQUIRED backup-missing, nothing restored from it")


def l4_backup_in_vault(base, lad):
    """A --state-dir whose backup/ tree lands inside the vault (state dir =
    the vault's parent, vault folder named 'backup'): the state dir and
    manifest are outside, but backups and apply logs would be written into
    the vault -- refused, nothing written; a registered pointer to such a
    dir is not trusted."""
    parent = base / "l4"
    root = parent / "backup"
    write_vault(root, FILES)
    t0 = tree(root)
    for args in ((), ("--apply",), ("--verify",)):
        p = run(root, *args, state=parent)
        assert p.returncode == 2 and "backup tree" in p.stderr and "inside the vault" in p.stderr \
            and "nothing written" in p.stderr, (args, out(p))
        assert tree(root) == t0, args
    ok = base / "l4-state"
    assert run(root, state=ok).returncode == 0
    reg_p = expected_default(lad, root) / "state-dirs.json"
    reg = json.loads(reg_p.read_text(encoding="utf-8"))
    reg["state_dirs"].append({"path": str(parent.resolve()), "last_used": "2026-01-01T00:00:00+00:00"})
    reg_p.write_text(json.dumps(reg), encoding="utf-8")
    d = run(root)
    assert d.returncode == 0 and f"registered state dir {parent.resolve()} is inside the vault -- not " \
        "trusted" in d.stdout, out(d)
    p = run(root, "--apply")
    assert p.returncode == 2 and "fails closed" in p.stderr, out(p)
    assert tree(root) == t0
    print("PASS L4 a --state-dir whose backup/ tree resolves inside the vault is refused (exit 2, "
          "nothing written); a registered pointer to one is not trusted (apply fails closed)")


def l5_no_localappdata(base, lad):
    """Windows: an unset or empty LOCALAPPDATA is refused -- a silent
    ~/.local/state fallback would move the index of record and hide every
    run registered under the real %LOCALAPPDATA% (R1 limits the fallback to
    non-Windows). --state-dir does not bypass it (the registry lives in the
    default dir)."""
    if os.name != "nt":
        print("SKIP L5 (Windows-only: off Windows the ~/.local/state fallback is R1's rule)")
        return
    root = base / "l5"
    write_vault(root, FILES)
    home = base / "l5-home"
    home.mkdir()
    sd = base / "l5-state"
    t0 = tree(root)
    fake_home = {"USERPROFILE": str(home), "HOME": str(home)}
    for extra, drop in (({}, ("LOCALAPPDATA",)), ({"LOCALAPPDATA": ""}, ())):
        for args in ((), ("--apply",), ("--verify",), ("--restore", "0123456789abcdef"),
                     ("--state-dir", str(sd)), ("--apply", "--state-dir", str(sd))):
            p = run(root, *args, env_extra=dict(extra, **fake_home), env_drop=drop)
            assert p.returncode == 2 and "LOCALAPPDATA is not set" in p.stderr, (extra, args, out(p))
            assert tree(root) == t0, args
    assert not any(home.rglob("*")), ("wrote under the home fallback", sorted(home.rglob("*")))
    assert not sd.exists(), "wrote into --state-dir"
    print("PASS L5 Windows: unset / empty LOCALAPPDATA refused (exit 2) for dry run, --apply, "
          "--verify, --restore and with --state-dir; nothing written in the vault, the home "
          "fallback or the --state-dir")


# The two spellings seen on the target machine (2026-09-30): the Store
# Python's WindowsApps alias (sys.executable) and its package install dir
# (sys.base_prefix).
MSIX_EXE = (r"C:\Users\u\AppData\Local\Microsoft\WindowsApps"
            r"\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\python.exe")
MSIX_PREFIX = (r"C:\Program Files\WindowsApps"
               r"\PythonSoftwareFoundation.Python.3.11_3.11.2544.0_x64__qbz5n2kfra8p0")


def l6_msix_python(base, lad):
    """Windows: a Microsoft Store (MSIX) Python is refused. Its writes under
    %LOCALAPPDATA% are redirected to a private package folder, so a run it
    made would be invisible to any other interpreter -- the index of record
    would silently split (the same class as L5). --state-dir does not bypass
    it (the registry lives in the default dir)."""
    if os.name != "nt":
        print("SKIP L6 (Windows-only: MSIX file-system virtualization)")
        return
    root = base / "l6"
    write_vault(root, FILES)
    sd = base / "l6-state"
    t0 = tree(root)
    lad_before = tree(lad) if lad.exists() else {}
    for mode, fake in (("msix-exe", MSIX_EXE), ("msix-prefix", MSIX_PREFIX)):
        for args in ((), ("--apply",), ("--verify",), ("--restore", "0123456789abcdef"),
                     ("--migrate-legacy-state",), ("--state-dir", str(sd)),
                     ("--apply", "--state-dir", str(sd))):
            p = run_wrapped(base, root, mode, fake, *args)
            assert p.returncode == 2 and "Microsoft Store Python" in p.stderr \
                and "python -B" in p.stderr, (mode, args, out(p))
            assert tree(root) == t0, (mode, args)
            assert (tree(lad) if lad.exists() else {}) == lad_before, ("wrote under LOCALAPPDATA", mode, args)
    assert not sd.exists(), "wrote into --state-dir"
    # the real interpreter (not Store) is unaffected
    assert run(root).returncode == 0
    print("PASS L6 Windows: a Microsoft Store Python (WindowsApps sys.executable, or a Store "
          "base_prefix) refused (exit 2) for dry run, --apply, --verify, --restore, migrate and "
          "with --state-dir; nothing written in the vault, LOCALAPPDATA or the --state-dir")


def d1_registry(base, lad):
    root = base / "d1"
    x = base / "d1-state-x"
    write_vault(root, FILES)
    orig = tree(root)
    default = expected_default(lad, root)
    p = failed_apply(root, state=x)
    assert f"STATE: {x}" in p.stdout and f"REGISTERED IN: {default / 'state-dirs.json'}" in p.stdout, p.stdout
    rid = rid_of(x)
    reg = json.loads((default / "state-dirs.json").read_text(encoding="utf-8"))
    assert [e["path"] for e in reg["state_dirs"]] == [str(x.resolve())], reg
    assert "T" in reg["state_dirs"][0]["last_used"], reg
    assert not (default / "backup").exists(), "the run leaked into the default dir"
    # Without --state-dir: discovery through the registry.
    d = run(root)
    assert d.returncode == 0 and f"WARNING: unfinished apply {rid}" in d.stdout, out(d)
    cmd = restore_line(d.stdout)
    assert cmd.endswith(f'--restore {rid} --state-dir "{x.resolve()}"'), cmd
    ap = run(root, "--apply")
    assert ap.returncode == 2 and "did not complete and still have writes" in ap.stderr, out(ap)
    r = run_cmdline(cmd, root)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout and f"Run: {x.resolve() / 'backup' / rid}" in r.stdout, out(r)
    assert tree(root) == orig, "restore through the registry not byte-exact"
    print("PASS D1 --state-dir run registered in state-dirs.json (REGISTERED IN line, canonical path, "
          "last_used); a run without the flag finds its unfinished apply, RESTORE carries --state-dir, "
          "--apply refused, the printed RESTORE restores byte-exact")


def d2_fail_closed(base, lad):
    root = base / "d2"
    x = base / "d2-state-x"
    write_vault(root, FILES)
    default = expected_default(lad, root)
    failed_apply(root, state=x)
    rid = rid_of(x)
    hidden = base / "d2-state-x-unplugged"
    os.replace(x, hidden)  # the registered dir is now unreachable
    t0 = tree(root)
    d = run(root)
    assert d.returncode == 0 and f"registered state dir {x.resolve()} is unreachable -- cannot prove there " \
        "is no unfinished run" in d.stdout, out(d)
    for args, rc in ((("--apply",), 2), (("--restore", rid), 2), (("--verify",), 1)):
        p = run(root, *args)
        assert p.returncode == rc and "cannot prove there is no unfinished run" in out(p), (args, out(p))
        assert tree(root) == t0, args
    migrate_fails_closed(root, "cannot prove there is no unfinished run")
    assert tree(root) == t0
    os.replace(hidden, x)
    # A pointer INTO the vault is never trusted.
    reg_p = default / "state-dirs.json"
    good = reg_p.read_bytes()
    reg = json.loads(good)
    reg["state_dirs"].append({"path": str(root / "wiki"), "last_used": "2026-01-01T00:00:00+00:00"})
    reg_p.write_text(json.dumps(reg), encoding="utf-8")
    p = run(root, "--apply", state=x)
    assert p.returncode == 2 and "is inside the vault -- not trusted" in p.stdout, out(p)
    # A corrupt registry: dry run warns, apply/restore fail closed, and a new
    # --state-dir cannot be registered (never overwrite the pointers).
    reg_p.write_bytes(b"{not json")
    d = run(root)
    assert d.returncode == 0 and "unreadable or malformed" in d.stdout, out(d)
    for args in (("--apply",), ("--restore", rid)):
        p = run(root, *args)
        assert p.returncode == 2 and "fails closed" in p.stderr, (args, out(p))
    migrate_fails_closed(root, "unreadable or malformed")
    assert reg_p.read_bytes() == b"{not json"
    p = run(root, state=base / "d2-state-y")
    assert p.returncode == 2 and "cannot register --state-dir" in p.stderr, out(p)
    assert reg_p.read_bytes() == b"{not json" and not (base / "d2-state-y").exists()
    assert tree(root) == t0
    reg_p.write_bytes(good)
    r = run(root, "--restore", rid)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout, out(r)
    print("PASS D2 unreachable registered dir: dry run warns 'cannot prove', --apply / --restore exit 2, "
          "--verify 1, vault untouched; an in-vault pointer and a corrupt registry fail closed; a "
          "corrupt registry refuses a new --state-dir and is never overwritten")


def d3_forget(base, lad):
    root = base / "d3"
    x = base / "d3-state-x"
    write_vault(root, FILES)
    default = expected_default(lad, root)
    failed_apply(root, state=x)
    rid = rid_of(x)
    t0 = tree(root)
    p = run(root, "--forget-state-dir", str(base / "never-registered"))
    assert p.returncode == 2 and "is not registered" in p.stderr, out(p)
    p = run(root, "--forget-state-dir", str(x))
    assert p.returncode == 2 and "holds 1 unfinished run(s)" in p.stderr, out(p)
    assert f"WARNING: unfinished apply {rid}" in p.stdout, p.stdout
    hidden = base / "d3-hidden"
    os.replace(x, hidden)
    p = run(root, "--forget-state-dir", str(x))
    assert p.returncode == 2 and "unreachable" in p.stderr and "create it empty" in p.stderr, out(p)
    os.replace(hidden, x)
    assert tree(root) == t0, "--forget-state-dir wrote in the vault"
    reg = json.loads((default / "state-dirs.json").read_text(encoding="utf-8"))
    assert [e["path"] for e in reg["state_dirs"]] == [str(x.resolve())], reg  # pointer kept
    r = run(root, "--restore", rid)
    assert r.returncode == 0, out(r)
    t1 = tree(root)
    p = run(root, "--forget-state-dir", str(x))
    assert p.returncode == 0 and f"FORGOT: {x.resolve()}" in p.stdout, out(p)
    assert json.loads((default / "state-dirs.json").read_text(encoding="utf-8"))["state_dirs"] == []
    assert tree(root) == t1, "--forget-state-dir wrote in the vault"
    print("PASS D3 --forget-state-dir refuses an unregistered, an unfinished and an unreachable dir "
          "(pointer kept), removes a settled one; vault tree unchanged throughout")


def d4_one_location(base, lad):
    root = base / "d4"
    write_vault(root, FILES)
    default = expected_default(lad, root)
    assert run(root).returncode == 0
    rid = rid_of(default)
    assert run(root, "--apply").returncode == 0
    assert run(root, "--restore", rid).returncode == 0
    y = base / "d4-state-y"
    assert run(root, state=y).returncode == 0 and rid_of(y) == rid, "fixture: same plan, same run_id"
    t0 = tree(root)
    ap = run(root, "--apply", state=y)
    assert ap.returncode == 2 and f"already has apply logs in {default / 'backup' / rid}" in ap.stderr, out(ap)
    assert tree(root) == t0 and not (y / "backup").exists()
    shutil.copytree(default / "backup" / rid, y / "backup" / rid)
    r = run(root, "--restore", rid)
    assert r.returncode == 2 and "more than one location" in r.stderr, out(r)
    print("PASS D4 one run, one location: --apply of a run_id with logs in another state dir and "
          "--restore of a run_id found in two are refused (exit 2, nothing written)")


def d5_unlistable_backup(base, lad):
    """A registered state dir that is reachable but whose backup/ cannot be
    listed (ACL-denied) hides its runs as well as an unreachable one: dry
    run warns 'cannot prove', --apply / --restore fail closed, --verify 1,
    --forget-state-dir refuses; the unfinished-run scan itself reports it
    instead of reading it as empty."""
    import shutil as _sh
    if os.name != "nt" or not _sh.which("icacls"):
        print("SKIP D5 (needs Windows icacls)")
        return
    root = base / "d5"
    x = base / "d5-state-x"
    write_vault(root, FILES)
    orig = tree(root)
    failed_apply(root, state=x)
    rid = rid_of(x)
    b = x / "backup"
    who = subprocess.run(["whoami"], capture_output=True, text=True).stdout.strip()
    deny = subprocess.run(["icacls", str(b), "/deny", f"{who}:(RD)"], capture_output=True, text=True)
    try:
        try:
            os.listdir(b)
            denied = False
        except PermissionError:
            denied = True
        if deny.returncode != 0 or not denied:
            print(f"SKIP D5 (could not deny listing {b}: {deny.stdout.strip()} {deny.stderr.strip()})")
            return
        t0 = tree(root)
        d = run(root)
        assert d.returncode == 0 and "cannot be listed" in d.stdout and \
            "cannot prove there is no unfinished run" in d.stdout, out(d)
        for args, rc in ((("--apply",), 2), (("--restore", rid), 2), (("--verify",), 1)):
            p = run(root, *args)
            assert p.returncode == rc and "cannot prove there is no unfinished run" in out(p), (args, out(p))
        migrate_fails_closed(root, "cannot be listed")
        p = run(root, "--forget-state-dir", str(x))
        assert p.returncode == 2 and "cannot be listed" in p.stderr, out(p)
        reg = json.loads((expected_default(lad, root) / "state-dirs.json").read_text(encoding="utf-8"))
        assert [e["path"] for e in reg["state_dirs"]] == [str(x.resolve())], "pointer dropped"
        sys.path.insert(0, str(SCRIPTS))
        import fix_wikilinks as fw
        u = fw.unfinished_runs(root, bases=[b])
        assert len(u) == 1 and u[0].get("unlistable") and not u[0]["files"], u
        assert tree(root) == t0, "vault written while the backup dir was unlistable"
    finally:
        subprocess.run(["icacls", str(b), "/remove:d", who], capture_output=True, text=True)
    r = run(root, "--restore", rid)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout, out(r)
    assert tree(root) == orig
    print("PASS D5 an un-listable registered backup dir: dry run warns 'cannot prove', --apply / "
          "--restore exit 2, --verify 1, --forget-state-dir refused (pointer kept), the scan reports "
          "it (never empty); once readable, restore byte-exact")


def m1_migrate(base, lad):
    old = legacy_tool(base)
    root = base / "m1"
    write_vault(root, {k: v for k, v in FILES.items() if k != "wiki/c.md"})
    # run C: complete apply of a.md + b.md by the legacy in-vault-state tool
    assert run(root, script=old).returncode == 0
    rid_c = json.loads((root / "_meta/alias-fix-manifest.json").read_text(encoding="utf-8"))["run_id"]
    assert run(root, "--apply", script=old).returncode == 0
    # run F: c.md added, apply fails after writing it (unfinished)
    write_vault(root, {"wiki/c.md": FILES["wiki/c.md"]})
    before_f = tree(root)
    assert run(root, script=old).returncode == 0
    rid_f = json.loads((root / "_meta/alias-fix-manifest.json").read_text(encoding="utf-8"))["run_id"]
    p = run(root, "--apply", script=old, env_extra={FAULT_ENV: "wiki/c.md"})
    assert p.returncode == 3, out(p)
    # a set-aside run (legacy remedy shape)
    aside = root / ".alias-fix-backup" / "0123456789abcdef-inspected"
    (aside / "wiki").mkdir(parents=True)
    (aside / "wiki/x.md").write_bytes(b"inspected by hand\n")
    legacy_before = tree(root / ".alias-fix-backup")
    s = base / "m1-state"
    t0 = tree(root)
    d = run(root, "--migrate-legacy-state", state=s)
    assert d.returncode == 0 and "LEGACY STATE: 2 run(s), 1 other entry" in d.stdout, out(d)
    assert "manifest present" in d.stdout and "(Dry run -- nothing written." in d.stdout, d.stdout
    assert tree(root) == t0 and not (s / "backup").exists(), "migrate dry run wrote something"
    # the new tool sees the legacy failed run before migration
    d = run(root, state=s)
    assert f"WARNING: unfinished apply {rid_f}" in d.stdout and "NOTE: legacy in-vault state" in d.stdout, out(d)
    # partial failure: run F's copy does not verify -> F kept, manifest kept
    p = run_wrapped(base, root, "corrupt-copy", rid_f, "--migrate-legacy-state", "--apply", state=s)
    assert p.returncode == 1 and f"FAILED    {rid_f}: verification failed" in p.stdout, out(p)
    assert "MIGRATION INCOMPLETE" in p.stdout and "legacy source kept" in p.stdout, p.stdout
    assert (root / ".alias-fix-backup" / rid_f).is_dir() and (root / "_meta/alias-fix-manifest.json").is_file()
    assert tree(root / ".alias-fix-backup" / rid_f) == {k[len(rid_f) + 1:]: v for k, v in legacy_before.items()
                                                        if k.startswith(rid_f + "/")}, "source altered"
    assert not (root / ".alias-fix-backup" / rid_c).exists() and (s / "backup" / rid_c).is_dir()
    assert not aside.exists() and (s / "backup" / aside.name / "wiki/x.md").is_file()
    assert not (s / "backup" / rid_f).exists() and not (s / "backup" / (rid_f + ".migrating")).exists()
    # the clean re-run finishes: manifest deleted last, legacy scan zero
    p = run(root, "--migrate-legacy-state", "--apply", state=s)
    assert p.returncode == 0 and f"proved    {rid_f}" in p.stdout, out(p)
    assert "LEGACY STATE: 0 after migration -- zero" in p.stdout, p.stdout
    assert not (root / ".alias-fix-backup").exists() and not (root / "_meta/alias-fix-manifest.json").exists()
    after = tree(s / "backup")
    for k, v in legacy_before.items():
        assert after.get(k) == v, ("migrated copy differs", k)
    d = run(root, "--migrate-legacy-state", state=s)
    assert d.returncode == 0 and "Nothing to migrate: the legacy scan is zero." in d.stdout, out(d)
    # the failed run restores byte-exact from its new location, found by discovery
    d = run(root)  # no --state-dir: the registry leads to s
    assert d.returncode == 0 and f"WARNING: unfinished apply {rid_f}" in d.stdout, out(d)
    assert "NOTE: legacy" not in d.stdout, d.stdout
    r = run_cmdline(restore_line(d.stdout), root)
    assert r.returncode == 0 and "RESTORED = 1" in r.stdout and f"Run: {s.resolve() / 'backup' / rid_f}" \
        in r.stdout, out(r)
    assert pages(root) == {k: v for k, v in before_f.items() if k.startswith("wiki/")}, "restore not byte-exact"
    print("PASS M1 legacy state from the legacy in-vault-state tool (complete + failed run + <rid>-inspected + manifest): "
          "migrate dry run writes nothing; a copy that fails verification keeps its source AND the "
          "manifest (others migrated); the re-run proves discovery, deletes the manifest last, legacy "
          "scan 0; the failed run restores byte-exact from the new location via the registry")


def m2_resume(base, lad):
    old = legacy_tool(base)
    root = base / "m2"
    write_vault(root, FILES)
    assert run(root, script=old).returncode == 0
    rid = json.loads((root / "_meta/alias-fix-manifest.json").read_text(encoding="utf-8"))["run_id"]
    assert run(root, "--apply", script=old).returncode == 0
    s = base / "m2-state"
    p = run_wrapped(base, root, "die-mid-delete", rid, "--migrate-legacy-state", "--apply", state=s)
    assert p.returncode == 1 and "simulated crash mid-delete" in p.stdout, out(p)
    left = root / ".alias-fix-backup" / rid
    assert left.is_dir() and not list(left.glob("apply-log*.json")), "fixture: logs deleted, rest left"
    assert (root / "_meta/alias-fix-manifest.json").is_file(), "manifest deleted before every entry migrated"
    # the half-deleted legacy remnant is invisible to discovery (no logs)
    d = run(root, state=s)
    assert d.returncode == 0 and "WARNING" not in d.stdout, out(d)
    p = run(root, "--migrate-legacy-state", "--apply", state=s)
    assert p.returncode == 0 and f"resume    {rid}: already copied and verified" in p.stdout, out(p)
    assert "LEGACY STATE: 0 after migration -- zero" in p.stdout, p.stdout
    r = run(root, "--restore", rid, state=s)
    assert r.returncode == 0 and "RESTORED = 3" in r.stdout, out(r)
    want = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in FILES.items()}
    assert {k: v for k, v in pages(root).items() if v != "<dir>"} == want, "restore not byte-exact"
    print("PASS M2 a migration that dies mid-delete (logs gone, backups left) keeps the manifest, the "
          "remnant is invisible to discovery, and the re-run resumes to legacy scan 0; restore exact")


def m4_logless_remnant(base, lad):
    """A legacy <run_id> dir holding no apply log (the remnant an interrupted
    'rm -rf' left on the 2026-09-30 Dropbox repro copy: only wiki/concepts/)
    is not a run -- discovery never reads it -- so migration moves it as a
    plain entry on the FIRST run; it must not fail its discovery proof
    forever and keep the legacy scan above zero."""
    root = base / "m4"
    write_vault(root, FILES)
    rid = "27802563c069bb2a"
    rem = root / ".alias-fix-backup" / rid
    (rem / "wiki" / "concepts").mkdir(parents=True)
    (rem / "wiki" / "a.md").write_bytes(b"orphan backup bytes\n")
    (root / "_meta").mkdir(exist_ok=True)
    (root / "_meta/alias-fix-manifest.json").write_text('{"run_id": "%s"}\n' % rid, encoding="utf-8")
    s = base / "m4-state"
    d = run(root, "--migrate-legacy-state", state=s)
    assert d.returncode == 0 and f"  entry  {rid}" in d.stdout, out(d)
    p = run(root, "--migrate-legacy-state", "--apply", state=s)
    assert p.returncode == 0 and "LEGACY STATE: 0 after migration -- zero" in p.stdout, out(p)
    assert not (root / ".alias-fix-backup").exists() and not (root / "_meta/alias-fix-manifest.json").exists()
    moved = s / "backup" / rid
    assert (moved / "wiki" / "a.md").read_bytes() == b"orphan backup bytes\n", "remnant not preserved"
    assert (moved / "wiki" / "concepts").is_dir()
    q = run(root, state=s)
    assert q.returncode == 0 and "WARNING" not in q.stdout and "legacy in-vault state" not in q.stdout, out(q)
    # an earlier (pre-fix) attempt already left a log-less copy at the destination: resumes
    root = base / "m4b"
    write_vault(root, FILES)
    rem = root / ".alias-fix-backup" / rid
    (rem / "wiki").mkdir(parents=True)
    (rem / "wiki" / "a.md").write_bytes(b"orphan backup bytes\n")
    s = base / "m4b-state"
    shutil.copytree(rem, s / "backup" / rid, copy_function=shutil.copy2)
    p = run(root, "--migrate-legacy-state", "--apply", state=s)
    assert p.returncode == 0 and f"resume    {rid}" in p.stdout, out(p)
    assert "LEGACY STATE: 0 after migration -- zero" in p.stdout, out(p)
    print("PASS M4 a log-less legacy <run_id> remnant migrates as a plain entry on the first run "
          "(preserved byte-exact in the state dir), legacy scan 0, no warning; a log-less copy left "
          "by an earlier attempt resumes")


def m5_proof_gate(base, lad):
    """R3's gate between copy and delete: a failed discovery proof (run not
    found at the copy / restore classifies it differently / the
    unfinished-run check sees it differently) or a copy whose metadata alone
    differs (mtime) keeps the source AND the manifest, and removes the
    unproven copy this invocation made -- so the unfinished legacy run stays
    listed once and its RESTORE still works. A resumed copy (made by an
    earlier invocation) is never removed."""
    old = legacy_tool(base)
    root = base / "m5"
    write_vault(root, FILES)
    orig = pages(root)
    assert run(root, script=old).returncode == 0
    rid = json.loads((root / "_meta/alias-fix-manifest.json").read_text(encoding="utf-8"))["run_id"]
    p = run(root, "--apply", "--batch-size", "1", script=old, env_extra={FAULT_ENV: "wiki/b.md"})
    assert p.returncode == 3, out(p)
    legacy_before = tree(root / ".alias-fix-backup")
    s = base / "m5-state"
    for mode, needle in (("proof-find", f"discovery proof failed: run {rid} found at"),
                         ("proof-classify", "would classify the migrated copy differently"),
                         ("proof-unfinished", f"the unfinished-run check sees run {rid} differently"),
                         ("touch-copy", "verification failed")):
        p = run_wrapped(base, root, mode, rid, "--migrate-legacy-state", "--apply", state=s)
        assert p.returncode == 1 and f"FAILED    {rid}: " in p.stdout and needle in p.stdout, (mode, out(p))
        assert "MIGRATION INCOMPLETE" in p.stdout and "legacy source kept" in p.stdout, (mode, p.stdout)
        assert tree(root / ".alias-fix-backup") == legacy_before, (mode, "legacy source altered")
        assert (root / "_meta/alias-fix-manifest.json").is_file(), (mode, "manifest deleted")
        assert not (s / "backup" / rid).exists(), (mode, "unproven copy left discoverable")
        assert not (s / "backup" / (rid + ".migrating")).exists(), (mode, "staging copy left")
        if mode != "touch-copy":
            assert f"unproven copy at {s.resolve() / 'backup' / rid} removed" in p.stdout, (mode, p.stdout)
        d = run(root, state=s)
        assert d.stdout.count(f"WARNING: unfinished apply {rid}") == 1, (mode, d.stdout)
    # resume path: a verified copy an EARLIER invocation left is kept on a proof failure
    shutil.copytree(root / ".alias-fix-backup" / rid, s / "backup" / rid, copy_function=shutil.copy2)
    p = run_wrapped(base, root, "proof-classify", rid, "--migrate-legacy-state", "--apply", state=s)
    assert p.returncode == 1 and f"resume    {rid}" in p.stdout and "would classify" in p.stdout, out(p)
    assert (s / "backup" / rid / "apply-log.json").is_file(), "a resumed copy was removed"
    shutil.rmtree(s / "backup" / rid)
    # the unfinished legacy run's printed RESTORE works (one location)
    d = run(root, state=s)
    r = run_cmdline(restore_line(d.stdout), root)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout, out(r)
    assert pages(root) == orig, "restore not byte-exact"
    p = run(root, "--migrate-legacy-state", "--apply", state=s)
    assert p.returncode == 0 and "LEGACY STATE: 0 after migration -- zero" in p.stdout, out(p)
    print("PASS M5 a failed discovery proof (find / classify / unfinished) or an mtime-only copy "
          "mismatch keeps the source and the manifest and removes the unproven copy (run listed "
          "once, RESTORE works); a resumed copy is kept; the clean re-run reaches legacy scan 0")


def legacy_reapplied(base, name, old):
    """A legacy in-vault-state run holding apply-log.json AND a rotated
    apply-log.prev-*.json: apply -> restore -> re-apply of the same run_id,
    failing (wiki/b.md gets a broken link) -- unfinished, 3 pages live."""
    root = base / name
    write_vault(root, FILES)
    orig = pages(root)
    assert run(root, script=old).returncode == 0
    rid = json.loads((root / "_meta/alias-fix-manifest.json").read_text(encoding="utf-8"))["run_id"]
    assert run(root, "--apply", script=old).returncode == 0
    assert run(root, "--restore", rid, script=old).returncode == 0
    assert run(root, script=old).returncode == 0
    assert json.loads((root / "_meta/alias-fix-manifest.json").read_text(encoding="utf-8"))["run_id"] == rid
    p = run(root, "--apply", script=old, env_extra={FAULT_ENV: "wiki/b.md"})
    assert p.returncode == 3, out(p)
    logs = sorted(x.name for x in (root / ".alias-fix-backup" / rid).glob("apply-log*.json"))
    assert len(logs) == 2 and logs[0] == "apply-log.json" and logs[1].startswith("apply-log.prev-"), logs
    return root, rid, orig


def m6_interrupted_multilog_delete(base, lad):
    """An interrupted delete of a legacy run with a rotated log must never
    wedge migration or restore. (a) The remnant the old order left (only the
    prev log) resumes on re-run to legacy scan 0, and the printed RESTORE
    then works (one location). (b) The delete order: prev logs go first,
    apply-log.json LAST -- a failure on apply-log.json leaves exactly it,
    and the re-run resumes too."""
    old = legacy_tool(base)
    for case in ("a", "b"):
        root, rid, orig = legacy_reapplied(base, "m6" + case, old)
        s = base / f"m6{case}-state"
        mode = "drop-current-log" if case == "a" else "fail-current-unlink"
        p = run_wrapped(base, root, mode, rid, "--migrate-legacy-state", "--apply", state=s)
        assert p.returncode == 1 and "delete of the legacy copy was interrupted" in p.stdout, (case, out(p))
        assert "legacy source kept" not in p.stdout, (case, "misleading FAILED line")
        left = sorted(x.name for x in (root / ".alias-fix-backup" / rid).glob("apply-log*.json"))
        if case == "a":
            assert len(left) == 1 and left[0].startswith("apply-log.prev-"), left
        else:
            assert left == ["apply-log.json"], ("delete order: prev logs must go before apply-log.json", left)
        assert (root / "_meta/alias-fix-manifest.json").is_file(), "manifest deleted before every entry"
        p = run(root, "--migrate-legacy-state", "--apply", state=s)
        assert p.returncode == 0 and f"resume    {rid}" in p.stdout and "interrupted delete" in p.stdout, \
            (case, out(p))
        assert "LEGACY STATE: 0 after migration -- zero" in p.stdout, (case, p.stdout)
        assert not (root / ".alias-fix-backup").exists() and not (root / "_meta/alias-fix-manifest.json").exists()
        d = run(root)  # no --state-dir: the registry leads to s
        assert d.returncode == 0 and f"WARNING: unfinished apply {rid}" in d.stdout, (case, out(d))
        r = run_cmdline(restore_line(d.stdout), root)
        assert r.returncode == 0 and "RESTORED = 3" in r.stdout and "REVIEW_REQUIRED = 0" in r.stdout, \
            (case, out(r))
        assert pages(root) == orig, (case, "restore not byte-exact")
    print("PASS M6 interrupted delete of a two-log legacy run: a prev-only remnant resumes to legacy "
          "scan 0 (manifest deleted last) and the printed RESTORE restores 3 byte-exact; the delete "
          "removes prev logs before apply-log.json and that remnant resumes too")


def m3_moved_then_migrated(base, lad):
    """A legacy in-vault-state run of a vault that was MOVED afterwards (log root = the old
    path): in place, restore accepts it (legacy dir inside --root). After
    --migrate-legacy-state the run lives outside the vault -- restore must
    still accept exactly those roots, or migration strands the run."""
    old = legacy_tool(base)
    root = base / "m3-old"
    write_vault(root, FILES)
    orig = pages(root)
    assert run(root, script=old).returncode == 0
    rid = json.loads((root / "_meta/alias-fix-manifest.json").read_text(encoding="utf-8"))["run_id"]
    p = run(root, "--apply", "--batch-size", "1", script=old, env_extra={FAULT_ENV: "wiki/b.md"})
    assert p.returncode == 3, out(p)
    moved = base / "m3-moved"
    shutil.move(str(root), str(moved))
    s = base / "m3-state"
    mg = run(moved, "--migrate-legacy-state", "--apply", state=s)
    assert mg.returncode == 0 and "LEGACY STATE: 0 after migration -- zero" in mg.stdout, out(mg)
    r = run(moved, "--restore", rid, state=s)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout and "NOTE: vault moved from" in r.stdout, out(r)
    assert pages(moved) == orig, "restore not byte-exact"
    # the carried acceptance is exactly the migrated run's roots: another
    # vault pointing --state-dir at this state still cannot restore it
    other = base / "m3-other"
    write_vault(other, FILES)
    r = run(other, "--restore", rid, state=s)
    assert r.returncode == 2 and "belongs to vault" in r.stderr, out(r)  # one state dir per vault
    # and without the identity file the carried acceptance itself still
    # refuses the other vault (the per-run root gate, not only vault.json)
    vid = (s / "vault.json").read_bytes()
    (s / "vault.json").unlink()
    r = run(other, "--restore", rid, state=s)
    (s / "vault.json").write_bytes(vid)
    assert r.returncode == 2 and "is not --root" in r.stderr, out(r)
    print("PASS M3 a legacy run of a vault moved after its apply migrates and still restores "
          "byte-exact ('NOTE: vault moved from'); the carried root acceptance is per run and "
          "vault-bound (another vault is still refused)")


def i1_identity(base, lad):
    # settled same-name state: a warning only
    a = base / "i1a" / "v"
    b = base / "i1b" / "v"
    write_vault(a, FILES)
    write_vault(b, FILES)
    assert run(a).returncode == 0 and run(a, "--apply").returncode == 0
    d = run(b)
    assert d.returncode == 0 and "state for another vault path with the same folder name" in d.stdout, out(d)
    assert "(1 settled run(s))" in d.stdout and str(a.resolve()) in d.stdout, d.stdout
    ap = run(b, "--apply")
    assert ap.returncode == 0 and "Applied: 3 edits" in ap.stdout, out(ap)
    # unfinished same-name state blocks --apply / --restore
    a2 = base / "i2a" / "w"
    b2 = base / "i2b" / "w"
    write_vault(a2, FILES)
    write_vault(b2, FILES)
    orig = tree(a2)
    failed_apply(a2)
    rid = rid_of(expected_default(lad, a2))
    d = run(b2)
    assert d.returncode == 0 and f"has unfinished run(s) {rid}" in d.stdout, out(d)
    t0 = tree(b2)
    for args in (("--apply",), ("--restore", rid)):
        p = run(b2, *args)
        assert p.returncode == 2 and "same-folder-name state" in p.stdout, (args, out(p))
        assert tree(b2) == t0, args
    p = run(b2, "--rebind-state", str(a2))
    assert p.returncode == 2 and "still exists" in p.stderr and "may be a copy" in p.stderr, out(p)
    assert expected_default(lad, a2).is_dir(), "a refused rebind moved state"
    # the vault is MOVED: a2 -> c2 (same folder name); rebind attaches its state
    c2 = base / "i2c" / "w"
    c2.parent.mkdir(parents=True)
    shutil.move(str(a2), str(c2))
    d = run(c2)
    assert d.returncode == 0 and f"has unfinished run(s) {rid}" in d.stdout, out(d)
    assert "WARNING: unfinished apply" not in d.stdout, "a moved vault inherited state without a rebind"
    p = run(c2, "--rebind-state", str(a2))
    assert p.returncode == 0 and "REBOUND:" in p.stdout, out(p)
    assert not expected_default(lad, a2).exists()
    assert (expected_default(lad, c2) / "backup" / rid / "apply-log.json").is_file()
    d = run(c2)
    assert d.returncode == 0 and f"WARNING: unfinished apply {rid}" in d.stdout, out(d)
    r = run_cmdline(restore_line(d.stdout), c2)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout and "NOTE: vault moved from" in r.stdout, out(r)
    assert pages(c2) == {k: v for k, v in orig.items() if k.startswith("wiki/")}, "restore not byte-exact"
    ap = run(b2, "--apply")  # b2's block cleared: the old key's run is settled and gone
    assert ap.returncode == 0, out(ap)
    print("PASS I1 same-folder-name state: settled -> warning, apply allowed; unfinished -> --apply / "
          "--restore blocked (vault untouched); --rebind-state refused while OLD_PATH exists; after the "
          "move a vault does not inherit state until --rebind-state, then the old run restores "
          "byte-exact with 'NOTE: vault moved from'")


def i2_rebind_rename(base, lad):
    """--rebind-state when the new path has no state yet: the old key's whole
    directory (runs + a registered --state-dir pointer) is renamed onto the
    new key; the run in the registered dir restores byte-exact too."""
    a = base / "i3a" / "u"
    x = base / "i3-state-x"
    write_vault(a, FILES)
    orig = tree(a)
    failed_apply(a, state=x)
    rid = rid_of(x)
    old_default = expected_default(lad, a)
    c = base / "i3c" / "u"
    c.parent.mkdir(parents=True)
    shutil.move(str(a), str(c))
    p = run(c, "--rebind-state", str(a))
    assert p.returncode == 0 and "REBOUND:" in p.stdout, out(p)
    new_default = expected_default(lad, c)
    assert not old_default.exists() and (new_default / "state-dirs.json").is_file()
    vid = json.loads((new_default / "vault.json").read_text(encoding="utf-8"))
    assert vid["rebound_from"] == [os.path.normcase(os.path.normpath(str(a.resolve())))], vid
    d = run(c)
    assert d.returncode == 0 and f"WARNING: unfinished apply {rid}" in d.stdout, out(d)
    r = run_cmdline(restore_line(d.stdout), c)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout and "NOTE: vault moved from" in r.stdout, out(r)
    assert pages(c) == {k: v for k, v in orig.items() if k.startswith("wiki/")}, "restore not byte-exact"
    print("PASS I2 --rebind-state onto a path with no state renames the old key's dir (registry "
          "included); the unfinished run in its registered --state-dir restores byte-exact")


def i4_rebind_empty_registry(base, lad):
    """--rebind-state onto a path that already has state, when the old key
    holds a state-dirs.json that merges nothing -- the empty list
    --forget-state-dir leaves, or only non-dict entries. The old tool
    unlinked that registry only when it merged something, so old_dir.rmdir()
    crashed (WinError 145) AFTER the runs had moved and BEFORE rebound_from
    was recorded: the moved unfinished run was refused by --restore at the
    new path, and every re-run of the rebind crashed the same way. Also: a
    fixed rebind finishes the stub that old crash left behind."""
    def setup(tag, planted=None):
        a = base / f"i5a-{tag}" / "Farm"
        sd = base / f"i5-state-{tag}"
        write_vault(a, FILES)
        orig = tree(a)
        assert run(a, state=sd).returncode == 0
        assert run(a, "--forget-state-dir", str(sd)).returncode == 0
        old_default = expected_default(lad, a)
        assert json.loads((old_default / "state-dirs.json").read_text(encoding="utf-8"))["state_dirs"] == []
        failed_apply(a)
        rid = rid_of(old_default)
        c = base / f"i5c-{tag}" / "Farm"
        c.parent.mkdir(parents=True)
        shutil.move(str(a), str(c))
        d = run(c)  # the normal rollout: a dry run at the new path creates its key
        assert d.returncode == 0 and f"has unfinished run(s) {rid}" in d.stdout, out(d)
        new_default = expected_default(lad, c)
        assert new_default.is_dir()
        if planted is not None:
            (old_default / "state-dirs.json").write_text(json.dumps(planted), encoding="utf-8")
        return a, c, orig, rid, old_default, new_default

    def settled(a, c, orig, rid, old_default, new_default, label):
        assert not old_default.exists(), (label, sorted(old_default.rglob("*")))
        assert (new_default / "backup" / rid / "apply-log.json").is_file(), label
        vid = json.loads((new_default / "vault.json").read_text(encoding="utf-8"))
        assert os.path.normcase(os.path.normpath(str(a.resolve()))) in vid["rebound_from"], (label, vid)
        d = run(c)
        assert d.returncode == 0 and f"WARNING: unfinished apply {rid}" in d.stdout, (label, out(d))
        r = run_cmdline(restore_line(d.stdout), c)
        assert r.returncode == 0 and "RESTORED = 2" in r.stdout and "NOTE: vault moved from" in r.stdout \
            and "REVIEW_REQUIRED = 0" in r.stdout, (label, out(r))
        assert pages(c) == {k: v for k, v in orig.items() if k.startswith("wiki/")}, (label, "not byte-exact")
        r2 = run(c, "--restore", rid)
        assert r2.returncode == 0 and "RESTORED = 0" in r2.stdout and "ALREADY_ORIGINAL = 2" in r2.stdout \
            and "REVIEW_REQUIRED = 0" in r2.stdout, (label, out(r2))

    for tag, planted in (("empty", None), ("nondict", {"schema_version": 1, "state_dirs": [1, "x"]})):
        a, c, orig, rid, old_default, new_default = setup(tag, planted)
        p = run(c, "--rebind-state", str(a))
        assert p.returncode == 0 and "REBOUND:" in p.stdout and "Traceback" not in p.stderr, (tag, out(p))
        p2 = run(c, "--rebind-state", str(a))  # re-run: nothing left there -- a clean refusal
        assert p2.returncode == 2 and "Traceback" not in p2.stderr and "no state for" in p2.stderr, (tag, out(p2))
        settled(a, c, orig, rid, old_default, new_default, tag)
    # a failure mid-move (after the run moved, before manifest.json did):
    # the grant is already recorded, no traceback, exit 3, and a re-run
    # finishes the rebind
    a, c, orig, rid, old_default, new_default = setup("incomplete")
    p = run_wrapped(base, c, "fail-manifest-move", str(old_default), "--rebind-state", str(a))
    assert p.returncode == 3 and "REBIND INCOMPLETE" in p.stderr and "Traceback" not in p.stderr \
        and "1 of 2 move(s) done" in p.stderr, out(p)
    assert (new_default / "backup" / rid / "apply-log.json").is_file()
    assert (old_default / "manifest.json").is_file(), "the refused move lost the manifest"
    vid = json.loads((new_default / "vault.json").read_text(encoding="utf-8"))
    assert os.path.normcase(os.path.normpath(str(a.resolve()))) in vid["rebound_from"], vid
    p = run(c, "--rebind-state", str(a))
    assert p.returncode == 0 and "REBOUND:" in p.stdout, out(p)
    settled(a, c, orig, rid, old_default, new_default, "incomplete")
    # the stub the old tool's crash left: the old key holds ONLY its empty
    # state-dirs.json; the runs + manifest already sit in the new key, and
    # the new vault.json has no rebound_from
    a, c, orig, rid, old_default, new_default = setup("stub")
    (new_default / "backup").mkdir(exist_ok=True)
    os.replace(old_default / "backup" / rid, new_default / "backup" / rid)
    (old_default / "backup").rmdir()
    os.replace(old_default / "manifest.json",
               new_default / f"manifest.rebound-from-{old_default.name}.json")
    (old_default / "vault.json").unlink()
    assert sorted(x.name for x in old_default.iterdir()) == ["state-dirs.json"]
    r = run(c, "--restore", rid)
    assert r.returncode == 2 and "is not --root" in r.stderr, out(r)  # the wedge
    p = run(c, "--rebind-state", str(a))
    assert p.returncode == 0 and "REBOUND:" in p.stdout and "Traceback" not in p.stderr, out(p)
    settled(a, c, orig, rid, old_default, new_default, "stub")
    print("PASS I4 --rebind-state onto an existing key, old key's state-dirs.json merging nothing "
          "(empty after --forget-state-dir / only non-dict entries): rc 0, no traceback, old key "
          "gone, rebound_from recorded, the moved unfinished run restores byte-exact (2nd restore "
          "0/2/0), a re-run is a clean refusal; a mid-move failure exits 3 REBIND INCOMPLETE with the "
          "grant recorded and a re-run finishes it; the old crash's stub is finished by a re-run")


def i3_same_name_registered(base, lad):
    """R7 through the other key's REGISTERED --state-dir dirs (R2 discovery):
    (a) an unfinished run living in the sibling's reachable --state-dir
    blocks --apply / --restore here; (b) after the vault is moved, that
    dir unreachable, a corrupt sibling registry or a sibling pointer into
    this vault cannot prove the run settled -> still blocked (never '0
    settled'), and migrate --apply fails closed too; (c) a sibling backup
    dir that cannot be listed blocks even when its runs are settled."""
    a = base / "i4a" / "z"
    b = base / "i4b" / "z"
    x = base / "i4-state-x"
    write_vault(a, FILES)
    write_vault(b, FILES)
    failed_apply(a, state=x)
    rid = rid_of(x)
    old_default = expected_default(lad, a)
    assert not (old_default / "backup" / rid).exists(), "fixture: the run must live only in x"

    def blocked(v, why):
        d = run(v)
        assert d.returncode == 0 and "state for another vault path with the same folder name" in d.stdout \
            and why in d.stdout and "--apply and --restore are blocked" in d.stdout, out(d)
        t0 = tree(v)
        for args in (("--apply",), ("--restore", rid)):
            p = run(v, *args)
            assert p.returncode == 2 and "same-folder-name state" in p.stdout, (args, out(p))
            assert tree(v) == t0, args
        return d

    # (a) the sibling's run is in its registered, reachable --state-dir
    blocked(b, f"has unfinished run(s) {rid}")
    # (b) the vault is MOVED a -> c; then x goes offline
    c = base / "i4c" / "z"
    c.parent.mkdir(parents=True)
    shutil.move(str(a), str(c))
    blocked(c, f"has unfinished run(s) {rid}")
    hidden = base / "i4-state-x-offline"
    os.replace(x, hidden)
    d = blocked(c, f"({x.resolve()} unreachable -- cannot prove there is no unfinished run)")
    fail_open = f"(vault {a.resolve()}) (0 settled run(s))"  # the pre-fix reading
    assert fail_open not in d.stdout and "--rebind-state OLD_PATH then --forget-state-dir" \
        in d.stdout, d.stdout
    migrate_fails_closed(c, "cannot prove there is no unfinished run")
    os.replace(hidden, x)
    blocked(c, f"has unfinished run(s) {rid}")
    reg_p = old_default / "state-dirs.json"
    good = reg_p.read_bytes()
    reg_p.write_bytes(b"{corrupt")
    d = blocked(c, "unreadable or malformed registry -- cannot prove there is no unfinished run")
    assert fail_open not in d.stdout, d.stdout
    # only a pointer INTO this vault (x's pointer dropped): never trusted
    reg = json.loads(good)
    reg["state_dirs"] = [{"path": str(c.resolve() / "wiki"), "last_used": "2026-01-01T00:00:00+00:00"}]
    reg_p.write_text(json.dumps(reg), encoding="utf-8")
    blocked(c, "registered inside this vault -- not trusted")
    reg_p.write_bytes(good)
    # the documented way out for the moved vault: rebind, then the run restores
    p = run(c, "--rebind-state", str(a))
    assert p.returncode == 0 and "REBOUND:" in p.stdout, out(p)
    dd = run(c)
    r = run_cmdline(restore_line(dd.stdout), c)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout, out(r)
    # (c) a settled sibling whose backup dir cannot be listed still blocks
    sa = base / "i5a" / "y"
    sb = base / "i5b" / "y"
    write_vault(sa, FILES)
    write_vault(sb, FILES)
    assert run(sa).returncode == 0 and run(sa, "--apply").returncode == 0
    sib_backup = expected_default(lad, sa) / "backup"
    d = run(sb)
    assert d.returncode == 0 and "(1 settled run(s))" in d.stdout, out(d)
    d = run_wrapped(base, sb, "unlist-base", str(sib_backup))
    assert d.returncode == 0 and "cannot be listed -- cannot prove there is no unfinished run" in d.stdout \
        and "--apply and --restore are blocked" in d.stdout, out(d)
    t0 = tree(sb)
    for args in (("--apply",), ("--restore", rid_of(expected_default(lad, sa)))):
        p = run_wrapped(base, sb, "unlist-base", str(sib_backup), *args)
        assert p.returncode == 2 and "same-folder-name state" in p.stdout, (args, out(p))
        assert tree(sb) == t0, args
    print("PASS I3 same-folder-name state via the sibling's registered --state-dir: an unfinished run "
          "there blocks --apply / --restore; after a move, that dir unreachable, a corrupt sibling "
          "registry or a sibling pointer into this vault still block (never '0 settled'), migrate "
          "--apply fails closed; rebind then restore clears it; an unlistable settled sibling backup "
          "dir blocks too")


LOCKS_ENV = "FIX_WIKILINKS_TEST_LOCKS"


def moved_unfinished(base, lad, tag):
    """Vault <tag>a/v with an unfinished apply (2 pages written), then MOVED
    to <tag>c/v; the new path has no state yet (no run there)."""
    a = base / f"{tag}a" / "v"
    write_vault(a, FILES)
    orig = pages(a)
    failed_apply(a)
    old_default = expected_default(lad, a)
    rid = rid_of(old_default)
    c = base / f"{tag}c" / "v"
    c.parent.mkdir(parents=True)
    shutil.move(str(a), str(c))
    assert not expected_default(lad, c).exists()
    return a, c, orig, rid, old_default, expected_default(lad, c)


def restores_clean(c, orig, rid, label):
    """The moved run is discovered, its printed RESTORE reconciles (RESTORED 2,
    REVIEW 0) byte-exact, and a second restore is 0 / 2 / 0."""
    d = run(c)
    assert d.returncode == 0 and f"WARNING: unfinished apply {rid}" in d.stdout, (label, out(d))
    r = run_cmdline(restore_line(d.stdout), c)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout and "REVIEW_REQUIRED = 0" in r.stdout, (label, out(r))
    assert pages(c) == orig, (label, "restore not byte-exact")
    r2 = run(c, "--restore", rid)
    assert r2.returncode == 0 and "RESTORED = 0" in r2.stdout and "ALREADY_ORIGINAL = 2" in r2.stdout \
        and "REVIEW_REQUIRED = 0" in r2.stdout, (label, out(r2))


def i5_rebind_grant_first(base, lad):
    """--rebind-state onto a key that does not exist yet goes through the same
    grant-FIRST planned move as an existing key (review: it renamed the whole
    old dir, then wrote the grant; an exhausted vault.json save -- or a kill
    in between -- stranded the unfinished run: RESTORE refused 'is not
    --root', a re-run of the rebind refused 'no state', --apply blocked for
    good). (a) the grant save exhausted: exit 3 REBIND INCOMPLETE, no
    traceback, nothing moved; the re-run finishes; the run restores. (b)
    the stranded shape the pre-fix tool left (whole dir renamed, old
    vault.json inside): the dry run names it, a re-run of the rebind
    repairs it, the run restores."""
    a, c, orig, rid, old_default, new_default = moved_unfinished(base, lad, "i5-")
    p = run(c, "--rebind-state", str(a), env_extra={LOCKS_ENV: json.dumps({"state:vault.json": 20})})
    assert p.returncode == 3 and "REBIND INCOMPLETE" in p.stderr and "Traceback" not in p.stderr \
        and "0 of 2 move(s) done" in p.stderr, out(p)
    assert (old_default / "backup" / rid / "apply-log.json").is_file(), "a failed grant moved the run"
    p = run(c, "--rebind-state", str(a))
    assert p.returncode == 0 and "REBOUND:" in p.stdout, out(p)
    assert not old_default.exists() and (new_default / "backup" / rid / "apply-log.json").is_file()
    vid = json.loads((new_default / "vault.json").read_text(encoding="utf-8"))
    assert vid["key"] == new_default.name and vid["rebound_from"] == [
        os.path.normcase(os.path.normpath(str(a.resolve())))], vid
    restores_clean(c, orig, rid, "grant-exhausted")
    # (b) the stranded shape: old key dir renamed onto the new key as a whole
    a, c, orig, rid, old_default, new_default = moved_unfinished(base, lad, "i5s-")
    new_default.parent.mkdir(parents=True, exist_ok=True)
    os.replace(old_default, new_default)
    d = run(c)
    assert d.returncode == 0 and "an interrupted --rebind-state" in d.stdout, out(d)
    ap = run(c, "--apply")
    assert ap.returncode == 2, out(ap)
    p = run(c, "--rebind-state", str(a))
    assert p.returncode == 0 and "REBOUND:" in p.stdout and "Traceback" not in p.stderr, out(p)
    vid = json.loads((new_default / "vault.json").read_text(encoding="utf-8"))
    assert vid["key"] == new_default.name and os.path.normcase(os.path.normpath(str(a.resolve()))) \
        in vid["rebound_from"], vid
    d = run(c)
    assert "an interrupted --rebind-state" not in d.stdout, out(d)
    restores_clean(c, orig, rid, "stranded")
    print("PASS I5 --rebind-state onto an absent key: the grant goes first on the one planned-move path "
          "-- an exhausted vault.json save exits 3 REBIND INCOMPLETE (no traceback, nothing moved) and "
          "the re-run finishes; the pre-fix stranded shape is named by the dry run and repaired by a "
          "re-run; both runs restore 2/0/0 byte-exact, 2nd restore 0/2/0")


def i6_one_vault_per_state_dir(base, lad):
    """One state dir belongs to exactly one vault path (review: a byte copy
    sharing --state-dir X got the same run_id, and its --apply rotated away
    the original's unfinished apply log -- the failed batch vanished from
    the unfinished-run check and could no longer be restored). (a) the copy
    is refused on dry run and --apply, nothing written anywhere; the
    original's unfinished log is untouched and its restore works. (b) a
    registered dir whose vault.json names another vault is not trusted:
    dry run warns, --apply / --restore fail closed, the explicit flag is
    refused."""
    v1 = base / "i6" / "farm"
    v2 = base / "i6" / "farm-copy"
    x = base / "i6-state"
    write_vault(v1, FILES)
    write_vault(v2, FILES)
    orig = pages(v1)
    failed_apply(v1, state=x)
    rid = rid_of(x)
    log = x / "backup" / rid / "apply-log.json"
    log_bytes, man_bytes = log.read_bytes(), (x / "manifest.json").read_bytes()
    t2 = tree(v2)
    for args in ((), ("--apply",), ("--restore", rid), ("--verify",)):
        p = run(v2, *args, state=x)
        assert p.returncode == 2 and "belongs to vault" in p.stderr and "one state dir per vault" in p.stderr, \
            (args, out(p))
        assert tree(v2) == t2, args
    assert log.read_bytes() == log_bytes and (x / "manifest.json").read_bytes() == man_bytes
    assert not list(log.parent.glob("apply-log.prev-*.json")), "the original's log was rotated"
    assert not expected_default(lad, v2).exists(), "the refused copy wrote its default dir"
    d = run(v1)
    assert d.returncode == 0 and f"WARNING: unfinished apply {rid}" in d.stdout, out(d)
    r = run_cmdline(restore_line(d.stdout), v1)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout and "REVIEW_REQUIRED = 0" in r.stdout, out(r)
    assert pages(v1) == orig
    # (b) a registered dir whose identity names another vault
    v3 = base / "i6b" / "v"
    y = base / "i6b-state"
    write_vault(v3, FILES)
    assert run(v3, state=y).returncode == 0
    (y / "vault.json").write_text(json.dumps({"schema_version": 1, "tool": "alias-fix",
                                              "root": "c:\\elsewhere\\v", "root_display": "C:\\elsewhere\\v",
                                              "key": "v-0badf00d"}), encoding="utf-8")
    d = run(v3)
    assert d.returncode == 0 and "belongs to vault C:\\elsewhere\\v" in d.stdout and "not trusted" in d.stdout, out(d)
    t3 = tree(v3)
    for args in (("--apply",), ("--restore", rid_of(y))):
        p = run(v3, *args)
        assert p.returncode == 2 and "fails closed" in p.stderr, (args, out(p))
        assert tree(v3) == t3
    p = run(v3, state=y)
    assert p.returncode == 2 and "belongs to vault" in p.stderr, out(p)
    print("PASS I6 one state dir per vault: a byte copy naming the original's --state-dir is refused "
          "(dry run / --apply / --restore / --verify exit 2, nothing written, the original's unfinished "
          "log and manifest untouched, no prev rotation) and the original restores 2/0/0; a registered "
          "dir whose vault.json names another vault warns on dry run and fails --apply / --restore closed")


def _variants(p):
    """Spellings of path `p` that resolve() keeps apart from the plain one."""
    s = str(Path(p).resolve())
    out_ = {"extended": "\\\\?\\" + s}
    if len(s) > 2 and s[1] == ":":
        unc = "\\\\localhost\\" + s[0].lower() + "$" + s[2:]
        if os.path.isdir(str(Path(unc).parent)):  # admin shares enabled
            out_["admin-share"] = unc
    return out_


def l7_path_forms(base, lad):
    """The in-vault refusal holds for every spelling of an in-vault path
    (review: \\\\?\\<vault>\\state and \\\\localhost\\c$\\...\\<vault>\\state passed
    the text check -- dry run and --apply wrote manifest, vault.json, backups
    and the apply log INTO the vault; '<vault> \\state' wrote on the first
    run). Each form: exit 2, the vault hash tree unchanged, nothing written
    under LOCALAPPDATA; --manifest the same."""
    if os.name != "nt":
        print("SKIP L7 (Windows path forms)")
        return
    root = base / "l7" / "v"
    write_vault(root, FILES)
    t0 = tree(root)
    forms = {k: v + "\\state" for k, v in _variants(root).items()}
    forms["trailing-space"] = str(root.resolve()) + " \\state"
    forms["trailing-dot"] = str(root.resolve()) + ".\\state"
    for name, f in forms.items():
        for args in ((), ("--apply",)):
            p = run(root, *args, state=f)
            assert p.returncode == 2 and "nothing written" in p.stderr, (name, args, out(p))
            assert tree(root) == t0, (name, args, "wrote inside the vault")
        p = run(root, "--manifest", f + "\\m.json")
        assert p.returncode == 2, (name, out(p))
        assert tree(root) == t0, (name, "--manifest wrote inside the vault")
    print(f"PASS L7 in-vault state refused for every spelling ({', '.join(sorted(forms))}): exit 2 on dry "
          f"run and --apply, --manifest too; the vault hash tree unchanged")


def l8_one_dir_two_spellings(base, lad):
    """A \\\\?\\-prefixed --state-dir outside the vault is the SAME directory
    as its plain spelling (review: registered twice, --restore then refused
    'more than one location' for good): apply with the extended form,
    restore with the plain one -> rc 0, one registry entry."""
    if os.name != "nt":
        print("SKIP L8 (Windows path forms)")
        return
    root = base / "l8" / "v"
    s = base / "l8-state"
    write_vault(root, FILES)
    orig = pages(root)
    ext = _variants(s)
    assert run(root, state=ext["extended"]).returncode == 0
    ap = run(root, "--apply", state=ext["extended"])
    assert ap.returncode == 0 and "Applied: 3 edits" in ap.stdout, out(ap)
    rid = rid_of(s)
    for spelling in ["plain"] + sorted(ext):
        st_arg = s if spelling == "plain" else ext[spelling]
        r = run(root, "--restore", rid, state=st_arg)
        assert r.returncode == 0 and "more than one location" not in out(r), (spelling, out(r))
    assert pages(root) == orig
    reg = json.loads((expected_default(lad, root) / "state-dirs.json").read_text(encoding="utf-8"))
    assert [e["path"] for e in reg["state_dirs"]] == [str(s.resolve())], reg
    r = run(root, "--restore", rid)
    assert r.returncode == 0 and "ALREADY_ORIGINAL = 3" in r.stdout, out(r)
    print(f"PASS L8 one --state-dir, several spellings (plain, {', '.join(sorted(ext))}): one registry "
          f"entry in the plain spelling, --restore works under each and without the flag")


def u1_same_path_no_file_ids(base, lad):
    """In-process: the identity fallback of _same_path / _inside never
    merges paths on a volume without file IDs (st_ino 0 -- samestat would
    call every two paths there the same: two state dirs merged, one dropped
    from discovery, fail open); with real IDs an alternate spelling of one
    dir IS the same path."""
    sys.path.insert(0, str(SCRIPTS))
    import fix_wikilinks as fw
    a, b = base / "u1-a", base / "u1-b"
    a.mkdir()
    b.mkdir()
    assert not fw._same_path(a, b) and not fw._inside(b, a)
    real_stat = os.stat

    def zero_ino(p, *args, **kw):
        s = real_stat(p, *args, **kw)
        return os.stat_result((s.st_mode, 0, 1, s.st_nlink, s.st_uid, s.st_gid, s.st_size,
                               int(s.st_atime), int(s.st_mtime), int(s.st_ctime)))
    os.stat = zero_ino
    try:
        assert not fw._same_path(a, b), "st_ino 0 merged two different dirs"
        assert not fw._inside(b, a), "st_ino 0 put a dir inside another"
        assert fw._same_path(a, a) and fw._inside(a / "x", a)  # the text test still holds
    finally:
        os.stat = real_stat
    for name, spelling in _variants(a).items() if os.name == "nt" else ():
        assert fw._same_path(a, spelling) and fw._inside(spelling + "\\x", a), name
    print("PASS U1 _same_path / _inside identity fallback: never merges on st_ino 0 (text test only); "
          "alternate spellings of one dir are the same path")


def d6_register_creates(base, lad):
    """A --state-dir is created (with its vault id) when it is registered, so
    a write-free run (--verify) never leaves a pointer to a directory that
    does not exist (review: every later --apply / --restore then failed
    closed as 'unreachable'). A registered dir that DOES go away names the
    remedy (--forget-state-dir)."""
    root = base / "d6" / "v"
    typo = base / "d6-new-state"
    write_vault(root, FILES)
    v = run(root, "--verify", state=typo)
    assert v.returncode == 1 and "REGISTERED IN:" in v.stdout, out(v)
    assert (typo / "vault.json").is_file(), sorted(typo.parent.iterdir())
    d = run(root)
    assert d.returncode == 0 and "unreachable" not in d.stdout, out(d)
    ap = run(root, "--apply")
    assert ap.returncode == 0 and "Applied: 3 edits" in ap.stdout, out(ap)
    shutil.rmtree(typo)
    d = run(root)
    assert "is unreachable" in d.stdout and f'--forget-state-dir "{typo.resolve()}"' in d.stdout, out(d)
    print("PASS D6 registering a --state-dir creates it with its vault id (a --verify leaves a reachable "
          "dir, the next --apply is not blocked); an unreachable registered dir names --forget-state-dir")


def main():
    with tempfile.TemporaryDirectory(prefix="wikillm153-") as td:
        base = Path(td)
        lad = base / "localappdata"
        os.environ["LOCALAPPDATA"] = str(lad)  # never the real %LOCALAPPDATA%
        for case in (l1_location, l2_refusals, l3_backup_traversal, l4_backup_in_vault,
                     l5_no_localappdata, l6_msix_python, d1_registry, d2_fail_closed, d3_forget, d4_one_location,
                     d5_unlistable_backup, m1_migrate, m2_resume, m3_moved_then_migrated,
                     m4_logless_remnant, m5_proof_gate, m6_interrupted_multilog_delete,
                     i1_identity, i2_rebind_rename, i3_same_name_registered,
                     i4_rebind_empty_registry, i5_rebind_grant_first, i6_one_vault_per_state_dir,
                     l7_path_forms, l8_one_dir_two_spellings, d6_register_creates,
                     u1_same_path_no_file_ids):
            case(base, lad)
    print("OK -- ADR-0005 operational-state matrix passed (R1/R2/R3/R7)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
