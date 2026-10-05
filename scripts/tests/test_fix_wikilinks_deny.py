"""R107: edit-deny enforcement, standalone fallback, and bytecode-free imports.
Run: python -B scripts/tests/test_fix_wikilinks_deny.py
The pinned pre-fix script is the RED control; all writes use temporary fixtures.
"""
from legacy_fixture import legacy_result
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SCRIPTS = Path(__file__).resolve().parents[1]
SUMMARY = "Skipped: 3 files (path=1, append_only=1, auto_generated=1, unreadable=0, edit_deny=0)."


def run(script, root, *args):
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    # --alias-only keeps its state outside the vault (ADR-0005): a sibling
    # temp dir, never the real %LOCALAPPDATA%.
    extra = ["--state-dir", str(Path(str(root) + ".state"))] if "--alias-only" in args else []
    return subprocess.run([sys.executable, str(script), *args, *extra], cwd=root,
                          env=env, capture_output=True, text=True, encoding="utf-8")


def build(root, script, shared):
    (root / "scripts").mkdir(parents=True)
    (root / "wiki").mkdir()
    (root / "_meta").mkdir()
    (root / "scripts/fix_wikilinks.py").write_bytes(script)
    if shared:
        shutil.copy2(SCRIPTS / "maintenance_preflight.py", root / "scripts")
    fixtures = {
        "wiki/log.md": "# History\r\n\r\n\x60[[x]]\x60\r\n",
        "wiki/append.md": "---\nappend_only: true\n---\n\x60[[x]]\x60\n",
        "_meta/auto.md": "---\nauto_generated: true\n---\n\x60[[x]]\x60\n",
        "wiki/normal.md": "---\nappend_only: false\nauto_generated: false\n---\n\x60[[x]]\x60\n",
    }
    for name, content in fixtures.items():
        (root / name).write_bytes(content.encode())
    return {name: (root / name).read_bytes() for name in fixtures}


def assert_protected(root, before):
    for name, content in before.items():
        if name != "wiki/normal.md":
            assert (root / name).read_bytes() == content, name


def main():
    with tempfile.TemporaryDirectory(prefix="wikillm107-") as td:
        base = Path(td)
        os.environ["LOCALAPPDATA"] = str(base / "_localappdata")
        current = (SCRIPTS / "fix_wikilinks.py").read_bytes()
        for shared in (True, False):
            root = base / ("shared" if shared else "standalone")
            before = build(root, current, shared)
            script = root / "scripts/fix_wikilinks.py"
            p = run(script, root)
            assert p.returncode == 0, p.stderr
            assert SUMMARY in p.stdout and "Total: 1 replacements across 1 files." in p.stdout, p.stdout
            assert all((root / n).read_bytes() == b for n, b in before.items())
            assert ("standalone fallback" in p.stdout) == (not shared), p.stdout
            p = run(script, root, "--apply")
            assert p.returncode == 0 and SUMMARY in p.stdout, p.stdout + p.stderr
            assert_protected(root, before)
            assert (root / "wiki/normal.md").read_text() == before["wiki/normal.md"].decode().replace("`", "")
            assert not list(root.rglob("__pycache__")), "import guard must work without -B"
            p = run(script, root)
            assert SUMMARY in p.stdout and "Nothing to do." in p.stdout
            print(f"PASS {'shared' if shared else 'fallback'}: dry-run 3 skipped / 1 candidate; apply only normal; history byte-identical; no bytecode")

        # Root selection is explicit; changing cwd alone never redirects writes.
        target = base / "target"
        before = build(target, current, False)
        p = run(SCRIPTS / "fix_wikilinks.py", base, "--root", str(target))
        assert p.returncode == 0 and SUMMARY in p.stdout, p.stdout + p.stderr
        assert "Total: 1 replacements across 1 files." in p.stdout
        assert all((target / n).read_bytes() == b for n, b in before.items())
        p = run(script, target)  # No flag: script's own vault, NOT cwd/target.
        assert "Nothing to do." in p.stdout and "Total:" not in p.stdout, p.stdout
        assert all((target / n).read_bytes() == b for n, b in before.items())
        p = run(SCRIPTS / "fix_wikilinks.py", base, "--root", str(target), "--apply")
        assert p.returncode == 0 and SUMMARY in p.stdout, p.stdout + p.stderr
        assert_protected(target, before)
        assert b"`" not in (target / "wiki/normal.md").read_bytes()
        p = run(script, base, "--root", str(base / "missing"))
        assert p.returncode == 2 and "not a vault" in p.stderr
        # Additional target CLAUDE.md also honors flags; never just wiki/.
        (target / "CLAUDE.md").write_text("---\nauto_generated: true\n---\n`[[x]]`\n", encoding="utf-8")
        p = run(SCRIPTS / "fix_wikilinks.py", base, "--root", str(target), "--apply")
        assert "auto_generated=2" in p.stdout
        assert "`[[x]]`" in (target / "CLAUDE.md").read_text()
        print("PASS --root dry-run from different cwd, unchanged default, invalid root exit 2, protected CLAUDE.md")

        if os.name == "nt":
            (target / "wiki/log.md").rename(target / "wiki/LOG.md")
            p = run(SCRIPTS / "fix_wikilinks.py", base, "--root", str(target), "--apply")
            assert p.returncode == 0 and "path=1" in p.stdout, p.stdout + p.stderr
            assert (target / "wiki/LOG.md").read_bytes() == before["wiki/log.md"]
            print("PASS Windows log path casing cannot bypass the deny set")

        # BOM before `---` (review 2-R2-T2), --alias-only mode: build_plan
        # re-reads the flags on the BOM-stripped text, so the page is skipped
        # in shared and fallback mode, dry run and --apply alike, bytes
        # identical. Default mode deliberately keeps the pre-PR utf-8 flag
        # read (I2: identical to the pre-PR script, regression R151 review
        # h1-I2-1); BOM protection there is a separate follow-up.
        for shared in (True, False):
            bv = base / ("bom-shared" if shared else "bom-standalone")
            (bv / "scripts").mkdir(parents=True)
            (bv / "wiki").mkdir()
            (bv / "scripts/fix_wikilinks.py").write_bytes(current)
            shutil.copy2(SCRIPTS / "_wikilib.py", bv / "scripts")
            if shared:
                shutil.copy2(SCRIPTS / "maintenance_preflight.py", bv / "scripts")
            (bv / "wiki/holder.md").write_bytes(b"---\naliases: [Soil pH]\n---\nh\n")
            hist = b"\xef\xbb\xbf---\nappend_only: true\n---\nhist [[Soil pH]]\n"
            (bv / "wiki/hist.md").write_bytes(hist)
            bom_skip = "append_only=1"
            for args in (("--alias-only",), ("--alias-only", "--apply")):
                p = run(bv / "scripts/fix_wikilinks.py", bv, *args)
                assert p.returncode == 0, p.stdout + p.stderr
                assert "SAFE_REWRITES" not in p.stdout or "SAFE_REWRITES = 0" in p.stdout, p.stdout
                if len(args) == 1:
                    assert bom_skip in p.stdout and "EXEMPT wiki/hist.md = 1" in p.stdout, p.stdout
                assert (bv / "wiki/hist.md").read_bytes() == hist
                assert not (bv / "_meta").exists() and not (bv / ".alias-fix-backup").exists()
            print(f"PASS --alias-only: BOM-prefixed append_only page skipped "
                  f"({'shared' if shared else 'fallback'}), dry + apply, bytes identical")

        empty = base / "empty"
        (empty / "wiki").mkdir(parents=True)
        p = run(script, base, "--root", str(empty))
        assert "Skipped: 0 files (path=0, append_only=0, auto_generated=0, unreadable=0, edit_deny=0)." in p.stdout
        print("PASS zero skip reasons are explicit")

        old = legacy_result("c13763d:scripts/fix_wikilinks.py", check=True).stdout
        red = base / "red"
        before = build(red, old, False)
        p = run(red / "scripts/fix_wikilinks.py", red, "--apply")
        assert p.returncode == 0, p.stderr
        try:
            assert_protected(red, before)
        except AssertionError:
            pass
        else:
            raise AssertionError("RED control did not fail history byte-identity assertion")
        assert "4 replacements across 4 files" in p.stdout
        print("PASS RED: pre-fix rewrites all four; protected-history assertion fails")
    print("OK -- R107 regression matrix passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
