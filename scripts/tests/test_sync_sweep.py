"""Regression test for regression R119 -- sync_from_template.py sweeps scripts/__pycache__.

A sibling import leaves scripts/__pycache__/*.pyc in a cloud-synced vault
(regression R83). `sync_from_template.py --apply` now deletes that directory on a
SUCCESSFUL run and prints `pycache : swept N files`; a dry run lists what it
would sweep and deletes nothing; a refused --apply sweeps nothing.

Builds a throwaway git template (main == refs/remotes/origin/main, so the
R38 source gate passes with --allow-noncanonical) and a throwaway vault
holding a copy of the script (it resolves the instance from __file__).

Pins:
  S1 dry run: lists each file (`would sweep:`), deletes none, exit 0
  S2 --apply with drift: installs the file AND sweeps
  S3 --apply on an already-in-sync vault ("Nothing to do.") still sweeps --
     this is the path a vault's SECOND sync lands on, after the first
     installed the sweeping version
  S4 --apply refused by the source gate (dirty template): pycache survives
  S5 --apply refused by the _meta adoption guard: pycache survives
  S6 zero residue: `pycache : swept 0 files` / `would sweep 0 files`
  S7 a subdirectory inside __pycache__ is left alone and named in a WARNING;
     the regular files are still swept and the sync still exits 0
  S8 a __pycache__ that is a junction to a dir OUTSIDE the vault is refused
     and its target's files survive (Windows; SKIP elsewhere). Run it under
     the oldest installed Python too (e.g. `py -3.11`): the junction check
     must not depend on os.path.isjunction (3.12+).
  R1 RED control: the pre-R119 script (legacy fixture pre-sync-sweep, label 4d11a71) on the S3 path
     leaves the pycache in place -- the pins can go red.

Run:  python scripts/tests/test_sync_sweep.py      Exit 0 = pass.
"""

from __future__ import annotations

from legacy_fixture import legacy_result
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parent
PRE_FIX = "4d11a71"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
ENV.pop("WIKILLM_TEMPLATE", None)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "core.autocrlf=false", *args], check=True, capture_output=True)


def make_template(root: Path, sync_bytes: bytes) -> Path:
    t = root / "template"
    (t / "scripts").mkdir(parents=True)
    (t / "_meta").mkdir()
    (t / "scripts" / "check_raw.py").write_text("# template check_raw v2\n", encoding="utf-8")
    (t / "scripts" / "sync_from_template.py").write_bytes(sync_bytes)
    (t / "_meta" / "fleet-conventions.md").write_text("# fleet v2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(t)], check=True, capture_output=True)
    git(t, "add", "-A")
    git(t, "commit", "-q", "-m", "init")
    git(t, "update-ref", "refs/remotes/origin/main", "HEAD")
    return t


def make_vault(root: Path, name: str, sync_bytes: bytes, in_sync: bool,
               pyc: int = 2, meta_conflict: bool = False) -> Path:
    v = root / name
    (v / "scripts").mkdir(parents=True)
    (v / "_meta").mkdir()
    (v / "scripts" / "sync_from_template.py").write_bytes(sync_bytes)
    (v / "scripts" / "check_raw.py").write_text(
        "# template check_raw v2\n" if in_sync else "# old check_raw v1\n", encoding="utf-8")
    (v / "_meta" / "fleet-conventions.md").write_text(
        "# local edit\n" if meta_conflict else "# fleet v2\n", encoding="utf-8")
    if pyc:
        c = v / "scripts" / "__pycache__"
        c.mkdir()
        for i in range(pyc):
            (c / f"mod{i}.cpython-314.pyc").write_bytes(b"\x00bytecode")
    return v


def sync(vault: Path, template: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", str(vault / "scripts" / "sync_from_template.py"),
                           "--template", str(template), "--allow-noncanonical", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=ENV, cwd=vault)


def cache(v: Path) -> Path:
    return v / "scripts" / "__pycache__"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wikillm119-"))
    new = (SCRIPTS / "sync_from_template.py").read_bytes()
    try:
        t = make_template(tmp, new)

        v1 = make_vault(tmp, "v1", new, in_sync=False)
        p = sync(v1, t)
        n = sum(1 for ln in p.stdout.splitlines() if ln.startswith("  would sweep: scripts/__pycache__/"))
        check("S1 dry run lists files, deletes none",
              p.returncode == 0 and "pycache : would sweep 2 files" in p.stdout and n == 2
              and len(list(cache(v1).iterdir())) == 2,
              f"rc={p.returncode}; listed={n}; on disk={len(list(cache(v1).iterdir()))}")

        p = sync(v1, t, "--apply")
        check("S2 --apply with drift installs and sweeps",
              p.returncode == 0 and "synced  : scripts/check_raw.py" in p.stdout
              and "pycache : swept 2 files" in p.stdout and not cache(v1).exists(),
              f"rc={p.returncode}; cache exists={cache(v1).exists()}; err={p.stderr.strip()[-200:]!r}")

        v2 = make_vault(tmp, "v2", new, in_sync=True)
        p = sync(v2, t, "--apply")
        check("S3 --apply on an in-sync vault ('Nothing to do.') still sweeps",
              p.returncode == 0 and "Nothing to do." in p.stdout
              and "pycache : swept 2 files" in p.stdout and not cache(v2).exists(),
              f"rc={p.returncode}; cache exists={cache(v2).exists()}")

        v3 = make_vault(tmp, "v3", new, in_sync=False)
        (t / "scripts" / "check_raw.py").write_text("# uncommitted edit\n", encoding="utf-8")
        p = sync(v3, t, "--apply")
        git(t, "checkout", "--", "scripts/check_raw.py")
        check("S4 gate-refused --apply leaves pycache untouched",
              p.returncode == 1 and "REFUSED" in p.stderr and "swept" not in p.stdout
              and len(list(cache(v3).iterdir())) == 2,
              f"rc={p.returncode}; on disk={len(list(cache(v3).iterdir()))}")

        v4 = make_vault(tmp, "v4", new, in_sync=True, meta_conflict=True)
        p = sync(v4, t, "--apply")
        check("S5 _meta-adoption-refused --apply leaves pycache untouched",
              p.returncode == 1 and "fleet doc(s) differ" in p.stderr and "swept" not in p.stdout
              and len(list(cache(v4).iterdir())) == 2,
              f"rc={p.returncode}; on disk={len(list(cache(v4).iterdir()))}")

        v5 = make_vault(tmp, "v5", new, in_sync=True, pyc=0)
        d = sync(v5, t)
        a = sync(v5, t, "--apply")
        check("S6 zero residue prints 'would sweep 0' / 'swept 0'",
              "pycache : would sweep 0 files" in d.stdout and a.returncode == 0
              and "pycache : swept 0 files" in a.stdout,
              f"dry={d.returncode}; apply={a.returncode}")

        v6 = make_vault(tmp, "v6", new, in_sync=True)
        (cache(v6) / "nested").mkdir()
        p = sync(v6, t, "--apply")
        left = sorted(x.name for x in cache(v6).iterdir()) if cache(v6).exists() else []
        check("S7 subdirectory left + WARNING; files swept; exit 0",
              p.returncode == 0 and "pycache : swept 2 files" in p.stdout
              and left == ["nested"] and "nested (not a regular file" in p.stderr,
              f"rc={p.returncode}; left={left}")

        # S8: a __pycache__ that is a JUNCTION to a directory outside the
        # vault must be refused, never followed (review finding: below Python
        # 3.12 os.path.isjunction does not exist and is_symlink() is False).
        v8 = make_vault(tmp, "v8", new, in_sync=True, pyc=0)
        outside = tmp / "outside"
        outside.mkdir()
        (outside / "precious.txt").write_text("not bytecode\n", encoding="utf-8")
        mk = subprocess.run(["cmd", "/c", "mklink", "/J", str(cache(v8)), str(outside)],
                            capture_output=True) if os.name == "nt" else None
        if mk is not None and mk.returncode == 0:
            p = sync(v8, t, "--apply")
            check("S8 junction __pycache__ refused, target untouched",
                  (outside / "precious.txt").exists() and "not a plain directory" in p.stderr
                  and "swept" not in p.stdout,
                  f"rc={p.returncode}; precious survives={(outside / 'precious.txt').exists()}")
            subprocess.run(["cmd", "/c", "rmdir", str(cache(v8))], capture_output=True)
        else:
            print("  [SKIP] S8 junction case: mklink /J unavailable (non-Windows or refused)")

        old = legacy_result(f"{PRE_FIX}:scripts/sync_from_template.py")
        if old.returncode == 0:
            t_old = make_template(tmp / "old", old.stdout)
            vr = make_vault(tmp, "vr", old.stdout, in_sync=True)
            p = sync(vr, t_old, "--apply")
            check("R1 control: pre-R119 script leaves the pycache",
                  p.returncode == 0 and "Nothing to do." in p.stdout
                  and len(list(cache(vr).iterdir())) == 2,
                  f"rc={p.returncode}; on disk={len(list(cache(vr).iterdir()))}")
        else:
            print(f"  [SKIP] R1 control: legacy fixture {PRE_FIX} unavailable")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
