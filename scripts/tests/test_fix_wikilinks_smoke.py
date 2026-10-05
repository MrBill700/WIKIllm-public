"""R151 rollout: fix_wikilinks.py --alias-only --apply --max-files N (smoke batch).

the owner's per-vault rollout is dry run -> smoke apply of 5-10 files -> click 2-3
rewritten links in Obsidian -> full run. This pins that path end to end:
the smoke apply writes only the first N applicable files (manifest order),
reconciles with links_deferred, records result 'partial' (settled, never a
wedge), a fresh dry run shows the remainder, the full apply and --verify exit
0, and --restore of the smoke run puts back exactly its N files. Plus the
usage errors (no --apply, no --alias-only, N = 0) with no writes.
Run: python -B scripts/tests/test_fix_wikilinks_smoke.py
All writes go to temporary fixture vaults; every run passes --root, and every
--alias-only run passes --state-dir <vault>.state (outside the vault, ADR-0005)
with LOCALAPPDATA pointed at a temp dir.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "fix_wikilinks.py"
FAULT_ENV = "FIX_WIKILINKS_TEST_FAULT"
SMOKE_LINE = ("SMOKE: {n} files applied; click 2-3 rewritten links in Obsidian, then re-run the "
              "dry run and --apply for the rest.")


SMOKE_ALL = ("SMOKE: {n} files applied (all ready files; nothing left to apply); click 2-3 "
             "rewritten links in Obsidian, then --verify.")
SMOKE_FAIL = ("SMOKE: apply did not verify (rc {rc}) -- do not click-test or continue; act on "
              "the messages above (RESTORE if printed).")
SETTLED_LABEL = ("links; applied by earlier apply of this run, settled -- counted as "
                 "links_previously_applied, not rewritten)")


def state_of(root):
    return Path(str(root) + ".state")


def bdir(root, rid):
    return state_of(root) / "backup" / rid


def run(root, *args, env_extra=None):
    env = dict(os.environ)
    env.pop(FAULT_ENV, None)
    env.update(env_extra or {})
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    extra = (["--state-dir", str(state_of(root))]
             if "--alias-only" in args and "--state-dir" not in args else [])
    return subprocess.run([sys.executable, "-B", str(SCRIPT), "--root", str(root), *args, *extra],
                          cwd=str(root), env=env, capture_output=True, text=True, encoding="utf-8")


def write_vault(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode("utf-8"))


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def manifest(root):
    return json.loads((state_of(root) / "manifest.json").read_text(encoding="utf-8"))


def safe_count(out):
    mm = re.search(r"^SAFE_REWRITES = (\d+)$", out, re.M)
    assert mm, out
    return int(mm.group(1))


FILES = {
    "wiki/concepts/soil-ph.md": "---\ntitle: Soil pH\naliases: [Soil pH]\n---\n# Soil pH\n",
    "wiki/concepts/compost.md": "---\naliases: [Hot compost]\n---\nbody\n",
    "wiki/p1.md": "One [[Soil pH]] and [[Hot compost]].\n",
    "wiki/p2.md": "Two [[Soil pH]].\n",
    "wiki/p3.md": "Three [[Hot compost]] and [[Soil pH|acidity]] and [[Soil pH]].\n",
    "wiki/p4.md": "Four [[Soil pH]].\n",
    "wiki/p5.md": "Five [[Hot compost]].\n",
    "wiki/p6.md": "Six [[Soil pH]] [[Hot compost]].\n",
    "wiki/log.md": "# Log\n\n[[Soil pH]]\n",
}


def main():
    passed = 0
    with tempfile.TemporaryDirectory(prefix="wikillm151-smoke-") as td:
        base = Path(td)
        os.environ["LOCALAPPDATA"] = str(base / "_localappdata")  # never the real one

        # 1. smoke apply: first N applicable files only, exact identity, 'partial'.
        root = base / "smoke"
        write_vault(root, FILES)
        before = snapshot(root)
        p = run(root, "--alias-only")
        assert p.returncode == 0, p.stdout + p.stderr
        m0 = manifest(root)
        order = sorted(m0["files"])
        assert order == list(m0["files"]) == ["wiki/p1.md", "wiki/p2.md", "wiki/p3.md",
                                               "wiki/p4.md", "wiki/p5.md", "wiki/p6.md"], order
        links = {r: len(v["edits"]) for r, v in m0["files"].items()}
        total = m0["planned_links"]
        assert total == sum(links.values()) == 10, (total, links)
        n = 2
        smoke, rest = order[:n], order[n:]
        applied = sum(links[r] for r in smoke)
        deferred = sum(links[r] for r in rest)
        p = run(root, "--alias-only", "--apply", "--max-files", str(n), "--batch-size", "1")
        assert p.returncode == 0, p.stdout + p.stderr
        assert (f"RECONCILE: manifest_safe_rewrites {total} = links_applied {applied} + "
                f"links_skipped_stale 0 + links_deferred {deferred}") in p.stdout, p.stdout
        assert applied + deferred == total
        assert "RECONCILE FAILED" not in p.stdout, p.stdout
        assert "MISMATCH" not in p.stdout and "LINT RECONCILIATION FAILED" not in p.stdout, p.stdout
        assert SMOKE_LINE.format(n=n) in p.stdout, p.stdout
        tail = p.stdout[p.stdout.index("SMOKE:"):]
        assert re.findall(r"^  smoke-applied  (\S+)$", tail, re.M) == smoke, tail
        assert f"deferred {len(rest)} files ({deferred} links)" in p.stdout, p.stdout
        now = snapshot(root)
        for r in smoke:
            assert now[r] != before[r], f"smoke file not written: {r}"
            assert b"[[soil-ph|Soil pH]]" in now[r] or b"[[compost|Hot compost]]" in now[r], now[r]
        for r in rest:
            assert now[r] == before[r], f"deferred file written: {r}"
        assert now["wiki/log.md"] == before["wiki/log.md"]
        rid_smoke = m0["run_id"]
        log = json.loads((bdir(root, rid_smoke) / "apply-log.json").read_text(encoding="utf-8"))
        assert not (root / ".alias-fix-backup").exists() and not (root / "_meta").exists()
        assert log["result"] == "partial" and sorted(log["files"]) == smoke, log
        assert log["max_files"] == n and log["deferred_files"] == rest, log
        passed += 1
        print(f"PASS smoke apply --max-files {n}: {applied} applied + {deferred} deferred = {total}, "
              f"result partial, SMOKE hint lists {smoke}")

        # 2. a partial run is SETTLED: unfinished_runs is empty, no WARNING.
        # In-process with no --state-dir: discovery reaches the run through
        # the default dir's state-dirs.json (ADR-0005), so this is not vacuous.
        sys.path.insert(0, str(SCRIPTS))
        import fix_wikilinks as fw
        assert fw.State(root).find_run(rid_smoke) == [bdir(root, rid_smoke)], "run not discovered"
        assert fw.unfinished_runs(root) == [], fw.unfinished_runs(root)
        assert "partial" in fw.SETTLED_RESULTS
        passed += 1
        print("PASS unfinished_runs treats a 'partial' smoke run as settled")

        # 3. fresh dry run: SAFE_REWRITES = original - applied, no unfinished warning.
        p = run(root, "--alias-only")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "WARNING: unfinished apply" not in p.stdout, p.stdout
        assert safe_count(p.stdout) == total - applied, p.stdout
        m1 = manifest(root)
        assert sorted(m1["files"]) == rest and m1["run_id"] != rid_smoke, m1["files"]
        passed += 1
        print(f"PASS fresh dry run after smoke: SAFE_REWRITES {total - applied} = {total} - {applied}")

        # 4. full apply rc 0 (the partial run blocks nothing), then --verify rc 0.
        p = run(root, "--alias-only", "--apply")
        assert p.returncode == 0, p.stdout + p.stderr
        assert (f"RECONCILE: manifest_safe_rewrites {total - applied} = links_applied "
                f"{total - applied} + links_skipped_stale 0") in p.stdout, p.stdout
        assert "links_deferred" not in p.stdout and "SMOKE:" not in p.stdout, p.stdout
        full_log = json.loads((bdir(root, m1["run_id"]) / "apply-log.json").read_text(encoding="utf-8"))
        assert full_log["result"] == "complete", full_log["result"]
        v = run(root, "--alias-only", "--verify")
        assert v.returncode == 0 and "VERIFY OK" in v.stdout, v.stdout + v.stderr
        passed += 1
        print("PASS full apply after the smoke run exits 0 and --verify exits 0")

        # 5. --restore of the smoke run restores exactly its N files.
        after_full = snapshot(root)
        p = run(root, "--alias-only", "--restore", rid_smoke)
        assert p.returncode == 0, p.stdout + p.stderr
        assert re.findall(r"^  restored          (\S+)$", p.stdout, re.M) == smoke, p.stdout
        assert "RESTORED = 2" in p.stdout and "REVIEW_REQUIRED = 0" in p.stdout, p.stdout
        now = snapshot(root)
        for r in smoke:
            assert now[r] == before[r], f"smoke file not restored: {r}"
        for r in rest:
            assert now[r] == after_full[r], f"restore of the smoke run touched {r}"
        passed += 1
        print(f"PASS --restore {rid_smoke} restores exactly the {n} smoke files")

        # 6. usage errors: no --apply, no --alias-only, N = 0 -> exit 2, no writes.
        u = base / "usage"
        write_vault(u, FILES)
        ub = snapshot(u)
        cases = [
            (("--alias-only", "--max-files", "2"), "--max-files requires --apply"),
            (("--alias-only", "--verify", "--max-files", "2"), "--max-files requires --apply"),
            (("--alias-only", "--restore", "0123456789abcdef", "--max-files", "2"),
             "--max-files requires --apply"),
            (("--max-files", "2"), "require --alias-only"),
            (("--apply", "--max-files", "2"), "require --alias-only"),
            (("--alias-only", "--apply", "--max-files", "0"), "--max-files must be >= 1"),
            (("--alias-only", "--apply", "--max-files", "-3"), "--max-files must be >= 1"),
        ]
        for args, msg in cases:
            p = run(u, *args)
            assert p.returncode == 2 and msg in p.stderr, (args, p.stdout, p.stderr)
            assert snapshot(u) == ub, f"usage error wrote something: {args}"
        passed += 1
        print("PASS --max-files without --apply / without --alias-only / N < 1 -> exit 2, no writes")

        # 7. re-apply of the SAME manifest after a settled smoke batch: the
        # smoke files are labelled settled (not 'restore first'), counted as
        # links_previously_applied, and the merged-undo NOTE is printed.
        ra = base / "reapply"
        write_vault(ra, FILES)
        assert run(ra, "--alias-only").returncode == 0
        rid = manifest(ra)["run_id"]
        p = run(ra, "--alias-only", "--apply", "--max-files", "2")
        assert p.returncode == 0, p.stdout + p.stderr
        p = run(ra, "--alias-only", "--apply")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "restore first" not in p.stdout, p.stdout
        for r in ("wiki/p1.md", "wiki/p2.md"):
            assert re.search(r"^  review  written-by-earlier-apply  " + re.escape(r) + r"  \(\d+ "
                             + re.escape(SETTLED_LABEL) + "$", p.stdout, re.M), p.stdout
        assert "links_previously_applied 3" in p.stdout, p.stdout
        assert (f"NOTE: this apply continues run_id {rid}; --restore {rid} now undoes the earlier "
                f"apply of this run and this apply together.") in p.stdout, p.stdout
        passed += 1
        print("PASS re-apply after a settled smoke batch: settled label, links_previously_applied, "
              "merged-undo NOTE, rc 0")

        # 8. re-apply after an UNFINISHED apply of the run: 'restore first',
        # refused (exit 2), nothing written.
        uf = base / "unfinished"
        write_vault(uf, FILES)
        assert run(uf, "--alias-only").returncode == 0
        p = run(uf, "--alias-only", "--apply", "--max-files", "2", env_extra={FAULT_ENV: "wiki/p1.md"})
        assert p.returncode == 3, p.stdout + p.stderr
        assert "SMOKE:" not in p.stdout, p.stdout
        # The failed smoke batch's write-ahead log still records max_files /
        # deferred_files (review g2-R2-C1-failed-smoke-log).
        flog = json.loads((bdir(uf, manifest(uf)["run_id"]) / "apply-log.json").read_text(encoding="utf-8"))
        assert flog["result"] == "postcondition-failed", flog["result"]
        assert flog["max_files"] == 2, flog
        assert flog["deferred_files"] == ["wiki/p3.md", "wiki/p4.md", "wiki/p5.md", "wiki/p6.md"], flog
        ub = snapshot(uf)
        p = run(uf, "--alias-only", "--apply")
        assert p.returncode == 2, p.stdout + p.stderr
        assert re.search(r"^  review  written-by-earlier-apply  wiki/p1\.md  \(\d+ links; restore "
                         r"first\)$", p.stdout, re.M), p.stdout
        assert "settled" not in p.stdout.split("ERROR")[0].split("written-by-earlier-apply")[1], p.stdout
        assert snapshot(uf) == ub, "refused re-apply wrote something"
        passed += 1
        print("PASS re-apply after an unfinished apply: 'restore first' label, exit 2, no writes")

        # 9. --max-files covering every ready file: result complete, SMOKE says
        # nothing is left (never 'for the rest').
        al = base / "all"
        write_vault(al, FILES)
        assert run(al, "--alias-only").returncode == 0
        p = run(al, "--alias-only", "--apply", "--max-files", "50")
        assert p.returncode == 0, p.stdout + p.stderr
        assert SMOKE_ALL.format(n=6) in p.stdout and "for the rest" not in p.stdout, p.stdout
        assert len(re.findall(r"^  smoke-applied  ", p.stdout, re.M)) == 6, p.stdout
        passed += 1
        print("PASS --max-files over every ready file: SMOKE says nothing left, lists all 6")

        # 10. rc != 0 (a file not in the manifest -> rc 6): SMOKE says do not
        # click-test, the smoke-applied list is still printed.
        r6 = base / "rc6"
        write_vault(r6, FILES)
        assert run(r6, "--alias-only").returncode == 0
        write_vault(r6, {"wiki/p7.md": "Seven [[Soil pH]].\n"})
        p = run(r6, "--alias-only", "--apply", "--max-files", "2")
        assert p.returncode == 6, p.stdout + p.stderr
        assert SMOKE_FAIL.format(rc=6) in p.stdout, p.stdout
        assert "for the rest" not in p.stdout and "then --verify" not in p.stdout, p.stdout
        tail = p.stdout[p.stdout.index("SMOKE:"):]
        assert re.findall(r"^  smoke-applied  (\S+)$", tail, re.M) == ["wiki/p1.md", "wiki/p2.md"], tail
        passed += 1
        print("PASS smoke apply with rc 6: SMOKE says do not click-test; smoke-applied list kept")

        # 11. --max-files covering every READY file while a manifest file went
        # stale since the dry run: something is left, so the hint is the
        # 'for the rest' form, never 'nothing left ... then --verify'
        # (review g2-R2-C1-hint-nothing-left).
        st = base / "stale-all"
        write_vault(st, FILES)
        assert run(st, "--alias-only").returncode == 0
        write_vault(st, {"wiki/p3.md": FILES["wiki/p3.md"] + "edited\n"})
        p = run(st, "--alias-only", "--apply", "--max-files", "99")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "links_skipped_stale 3" in p.stdout, p.stdout
        assert SMOKE_LINE.format(n=5) in p.stdout, p.stdout
        assert "nothing left" not in p.stdout and "then --verify" not in p.stdout, p.stdout
        assert run(st, "--alias-only", "--verify").returncode == 1
        passed += 1
        print("PASS --max-files over every ready file with a stale file: SMOKE says 'for the rest'")

    print(f"OK -- R151 smoke-batch rollout passed ({passed} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
