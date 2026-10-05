"""R151: fix_wikilinks.py --alias-only -- rewrite uniquely-resolvable alias-only
links to [[slug|Alias]]; everything else untouched or queued for review.
Run: python -B scripts/tests/test_fix_wikilinks_alias.py
All writes go to temporary fixture vaults; every run passes --root, and every
--alias-only run passes --state-dir <vault>.state (a sibling temp dir, outside
the vault -- ADR-0005) with LOCALAPPDATA pointed at a temp dir, so no test
ever touches the real %LOCALAPPDATA%/WIKIllm.
"""
from legacy_fixture import legacy_result
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "fix_wikilinks.py"
FAULT_ENV = "FIX_WIKILINKS_TEST_FAULT"


def state_of(root):
    """The per-vault state dir every test run passes as --state-dir: a
    sibling of the vault, so it is outside it (ADR-0005)."""
    return Path(str(root) + ".state")


def bdir(root, rid):
    """Where run `rid` of vault `root` keeps its apply log + page backups."""
    return state_of(root) / "backup" / rid


def state_args(root, args):
    """--state-dir for an --alias-only call that does not name one."""
    if "--alias-only" in args and "--state-dir" not in args:
        return ["--state-dir", str(state_of(root))]
    return []


def isolate_state(base):
    """Point LOCALAPPDATA (the default state root) at a temp dir for this
    process and every child: the registry and default dirs land there."""
    os.environ["LOCALAPPDATA"] = str(Path(base) / "_localappdata")


def run(root, *args, env_extra=None, script=SCRIPT, state=True):
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop(FAULT_ENV, None)
    env.update(env_extra or {})
    extra = state_args(root, args) if state else []
    return subprocess.run([sys.executable, str(script), "--root", str(root), *args, *extra],
                          cwd=str(root), env=env, capture_output=True, text=True, encoding="utf-8")


PAGES = {
    "wiki/concepts/soil-ph.md": "---\ntitle: Soil pH\naliases: [Soil pH]\n---\n# Soil pH\n## Liming\nx ^abc123\n",
    "wiki/concepts/mini-farm.md": "---\ntitle: Mini farm\naliases:\n  - 60/30/10 mini-farm design\n---\nbody\n",
    "wiki/concepts/cn-ratio.md": "---\naliases: [\"C:N ratio in compost\"]\n---\nbody\n",
    "wiki/concepts/mass-selection.md": ("---\naliases:\n  - Mass selection vs. family selection\n"
                                        "  - Jeavons (How to Grow More Vegetables, 9th ed., 2017)\n---\nbody\n"),
    "wiki/concepts/compost-a.md": "---\naliases: [Compost]\n---\na\n",
    "wiki/concepts/compost-b.md": "---\naliases: [Compost]\n---\nb\n",
    "wiki/entities/Cover crop.md": "",
    "wiki/concepts/cover-cropping.md": "---\naliases: [Cover crop]\n---\ncc\n",
    "wiki/concepts/foo.md": "concept foo\n",
    "wiki/entities/foo.md": "---\naliases: [Foo Entity]\n---\nentity foo\n",
    # [[Cover crop]] in log.md: a stub-collision link in an excluded page must
    # not count toward EXEMPT (lint calls it canonical).
    "wiki/log.md": "# Log\n\n[[Soil pH]]\n[[Cover crop]]\n",
    "wiki/history.md": "---\nappend_only: true\n---\n[[Soil pH]]\n",
    # aliases: [Compost] on a _meta/ auto_generated page: lint drops the page,
    # so it must not become a third alias holder (Compost stays 2-way).
    "_meta/auto.md": "---\nauto_generated: true\naliases: [Compost]\n---\n[[Soil pH]]\n",
    "wiki/crlf.md": "---\r\ntitle: crlf\r\n---\r\nSee [[Soil pH]].\r\n",
    "wiki/bom.md": "\ufeffBOM [[Soil pH]]\n",
}

LINKS_BEFORE = "\n".join([
    "---",
    "title: Links",
    "related: \"[[Soil pH]]\"",
    "---",
    "Plain [[Soil pH]] here.",
    "Display [[Soil pH|acidity]].",
    "Heading [[Soil pH#Liming]].",
    "Block [[Soil pH#^abc123]].",
    "HeadDisp [[Soil pH#Liming|lime it]].",
    "Embed ![[Soil pH]] and ![[Soil pH#Liming]] and ![[Soil pH|shown]].",
    "Slash [[60/30/10 mini-farm design]] colon [[C:N ratio in compost]].",
    "Dotted [[Mass selection vs. family selection]] ed [[Jeavons (How to Grow More Vegetables, 9th ed., 2017)]].",
    "Existing [[soil-ph|Soil pH]] canonical [[soil-ph]] md [[soil-ph.md]] path [[concepts/soil-ph]].",
    "Inline `[[Soil pH]]` double ``x [[Soil pH]] y`` end.",
    "Ambiguous [[Compost]] stub [[Cover crop]] broken [[Nowhere page]] attach [[chart.png]].",
    "Collide [[Foo Entity]].",
    "```",
    "[[Soil pH]]",
    "```",
    "~~~python",
    "[[Soil pH]]",
    "~~~",
    "| a | b |",
    "|---|---|",
    "| [[Soil pH]] | [[Soil pH\\|pH]] |",
    "| [[Soil pH#Liming\\|lime]] | x |",
    "",
])

LINKS_AFTER = "\n".join([
    "---",
    "title: Links",
    "related: \"[[Soil pH]]\"",
    "---",
    "Plain [[soil-ph|Soil pH]] here.",
    "Display [[soil-ph|acidity]].",
    "Heading [[soil-ph#Liming|Soil pH > Liming]].",
    "Block [[soil-ph#^abc123|Soil pH > ^abc123]].",
    "HeadDisp [[soil-ph#Liming|lime it]].",
    "Embed ![[soil-ph]] and ![[soil-ph#Liming]] and ![[soil-ph|shown]].",
    "Slash [[mini-farm|60/30/10 mini-farm design]] colon [[cn-ratio|C:N ratio in compost]].",
    "Dotted [[mass-selection|Mass selection vs. family selection]] ed "
    "[[mass-selection|Jeavons (How to Grow More Vegetables, 9th ed., 2017)]].",
    "Existing [[soil-ph|Soil pH]] canonical [[soil-ph]] md [[soil-ph.md]] path [[concepts/soil-ph]].",
    "Inline `[[Soil pH]]` double ``x [[Soil pH]] y`` end.",
    "Ambiguous [[Compost]] stub [[Cover crop]] broken [[Nowhere page]] attach [[chart.png]].",
    "Collide [[wiki/entities/foo|Foo Entity]].",
    "```",
    "[[Soil pH]]",
    "```",
    "~~~python",
    "[[Soil pH]]",
    "~~~",
    "| a | b |",
    "|---|---|",
    "| [[soil-ph\\|Soil pH]] | [[soil-ph\\|pH]] |",
    "| [[soil-ph#Liming\\|lime]] | x |",
    "",
])

# A BOM hides `---` from a plain utf-8 read: the flag must still protect it
# (review 1-F4). Kept out of PAGES so case 8's pre-151 pin stays comparable.
MAIN_EXTRA = {"wiki/bomhist.md": "\ufeff---\nappend_only: true\n---\nhist [[Soil pH]]\n"}
PROTECTED = ("wiki/log.md", "wiki/history.md", "_meta/auto.md", "wiki/bomhist.md")


def build(root, extra=None):
    files = dict(PAGES)
    files["wiki/concepts/links.md"] = LINKS_BEFORE
    files.update(extra or {})
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode("utf-8"))
    return {rel: (root / rel).read_bytes() for rel in files}


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def manifest(root):
    return json.loads((state_of(root) / "manifest.json").read_text(encoding="utf-8"))


def no_tool_state_in_vault(root):
    """ADR-0005: nothing the fixer owns is ever created inside the vault."""
    assert not (root / "_meta/alias-fix-manifest.json").exists(), root
    assert not (root / ".alias-fix-backup").exists(), root


def lint_alias_only(root):
    """lint.py's ALIAS-ONLY count for `root`, run from the vault root as the
    fleet runs it."""
    p = subprocess.run([sys.executable, "-B", str(SCRIPTS / "lint.py"), "--summary"], cwd=str(root),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    mm = re.search(r"^=== ALIAS-ONLY LINKS[^\n]*=== (\d+) ", p.stdout, re.M)
    assert mm, p.stdout + p.stderr
    return int(mm.group(1))


def lint_masked(text):
    """Independent twin of _wikilib.strip_code's three patterns, masking
    instead of deleting, so an offset tells whether lint counts a link."""
    for rx in (r"```[\s\S]*?```", r"``[^\n]+?``", r"`[^`\n]+`"):
        text = re.sub(rx, lambda mm: "\x00" * len(mm.group(0)), text)
    return text


def check_edits_lint_live(root, m, texts):
    """ARB-1: every planned edit sits at an offset lint also counts, and its
    'old' text is exactly there. `texts` = {rel: pre-apply text}."""
    for rel, v in m["files"].items():
        text = texts[rel] if rel in texts else (root / rel).read_bytes().decode("utf-8-sig")
        starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
        lv = lint_masked(text)
        for e in v["edits"]:
            off = starts[e["line"] - 1] + e["col"] - 1
            assert text[off:off + len(e["old"])] == e["old"], (rel, e)
            assert "\x00" not in lv[off:off + len(e["old"])], f"edit inside lint-masked code: {rel} {e}"


def parity_queue(m):
    """Review items lint counts as ALIAS-ONLY, by the lint_kind the fixer
    recorded from _wikilib.lint_link_kind: collisions are canonical to lint
    (it opens the file of that name), lint_hidden items are 'masked'."""
    return [r for r in m["review"] if r.get("lint_kind") == "alias-only"]


def exempt_ao(m):
    """EXEMPT links lint files under ALIAS-ONLY (the rest are lint-BROKEN
    dotted / slash / colon names)."""
    return sum(v["lint_alias_only"] for v in m["exempt"].values())


NOTHING = ("Nothing to do: nothing safely auto-remediable remains "
           "(review-only findings may remain)")


def write_vault(root, files):
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode("utf-8"))
    return {rel: (root / rel).read_bytes() for rel in files}


# Hooks run in-process around fix_wikilinks.main() (no production test hook):
#   toctou <rel>          after batch 1's write, append a human line to <rel>
#   recount-error -       recount() raises OSError (Dropbox placeholder etc.)
#   notemp <dir>          tempfile.gettempdir() returns <dir> (vault not under it)
WRAPPER = '''import sys
sys.dont_write_bytecode = True
sys.path.insert(0, {scripts!r})
import tempfile
import fix_wikilinks as fw
mode, arg = sys.argv[1], sys.argv[2]
orig = fw.recount
calls = []
def hooked(root, rels, wl):
    calls.append(rels)
    if mode == "recount-error":
        raise OSError("simulated unreadable file")
    if mode == "toctou" and len(calls) == 1:
        with open(root / arg, "ab") as fh:
            fh.write(b"HUMAN EDIT\\n")
    return orig(root, rels, wl)
fw.recount = hooked
if mode == "codewrap":
    # Wrap every planned new link in backticks: the alias count still drops
    # by the edit count and broken stays flat, so ONLY the canonical-rise
    # postcondition can catch it (review 3-R3-3 / ARB-1).
    orig_bp = fw.build_plan
    def bp(*a, **k):
        plan = orig_bp(*a, **k)
        for rel, (bom, nt, raw) in list(plan["new"].items()):
            for e in plan["files"][rel]["edits"]:
                nt = nt.replace(e["new"], "`" + e["new"] + "`")
            plan["new"][rel] = (bom, nt, raw)
        return plan
    fw.build_plan = bp
if mode == "notemp":
    tempfile.gettempdir = lambda: arg
sys.argv = ["fix_wikilinks.py"] + sys.argv[3:]
sys.exit(fw.main())
'''


def run_wrapped(base, root, mode, arg, *args, env_extra=None):
    w = base / "wrapper.py"
    w.write_text(WRAPPER.format(scripts=str(SCRIPTS)), encoding="utf-8")
    env = dict(os.environ)
    env.pop(FAULT_ENV, None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-B", str(w), mode, arg, "--root", str(root), *args,
                           *state_args(root, args)],
                          cwd=str(root), env=env, capture_output=True, text=True, encoding="utf-8")


def main():
    passed = 0
    with tempfile.TemporaryDirectory(prefix="wikillm151-") as td:
        base = Path(td)
        isolate_state(base)

        # 1. dry run: plans, writes manifest only, touches no page.
        root = base / "main"
        before = build(root, MAIN_EXTRA)
        v = run(root, "--alias-only", "--verify")
        assert v.returncode == 1 and "VERIFY FAIL" in v.stdout, v.stdout + v.stderr
        assert not (state_of(root) / "manifest.json").exists(), "--verify must not write a manifest"
        no_tool_state_in_vault(root)
        p = run(root, "--alias-only")
        assert p.returncode == 0, p.stdout + p.stderr
        print(p.stdout)
        after = snapshot(root)
        assert all(after[r] == b for r, b in before.items()), "dry run wrote a page"
        assert set(after) == set(before), sorted(set(after) - set(before))  # nothing added either
        no_tool_state_in_vault(root)
        m = manifest(root)
        t = m["totals"]["links"]
        assert t["alias-ambiguous"] == 1 and t["stub-collision"] == 1, t
        assert t["broken"] == 1 and m["broken"] == {"Nowhere page": 1}, (t, m["broken"])
        assert t["attachment"] == 1, t
        assert set(m["files"]) == {"wiki/concepts/links.md", "wiki/crlf.md", "wiki/bom.md"}, m["files"]
        assert len(m["files"]["wiki/concepts/links.md"]["edits"]) == 16, len(m["files"]["wiki/concepts/links.md"]["edits"])
        cats = sorted((r["category"], r.get("reason", "")) for r in m["review"])
        # frontmatter `related:` and the ~~~ fence are lint-visible but masked
        # here: listed for review, never rewritten (review 1-F3 README residue).
        assert cats == [("alias-ambiguous", ""), ("alias-unique-held", "in-code"),
                        ("alias-unique-held", "in-frontmatter"), ("stub-collision", "")], cats
        assert m["exclusions"] == {"append_only": ["wiki/bomhist.md", "wiki/history.md"],
                                   "auto_generated": ["_meta/auto.md"],
                                   "path": ["wiki/log.md"]}, m["exclusions"]
        # log + history + bomhist; _meta/auto.md is not counted (lint drops it).
        assert m["totals"]["exempt"] == 3 and exempt_ao(m) == 3, m["totals"]
        # _meta/auto.md (auto_generated) also carries aliases: [Compost]; lint
        # drops that page, so it is not a candidate (review 2-R2-T5).
        amb = [r for r in m["review"] if r["category"] == "alias-ambiguous"]
        assert [r["candidates"] for r in amb] == [["wiki/concepts/compost-a.md",
                                                   "wiki/concepts/compost-b.md"]], amb
        assert len(m["run_id"]) == 16 and "T" in m["generated"] and len(m["tool_sha256"]) == 64
        e0 = m["files"]["wiki/concepts/links.md"]["edits"][0]
        assert e0 == {"line": 5, "col": 7, "old": "[[Soil pH]]", "new": "[[soil-ph|Soil pH]]",
                      "category": "alias-unique", "lint_kind": "alias-only"}, e0
        assert "Files with safe rewrites: 3 (18 edits)" in p.stdout and "Review queue: 4 items" in p.stdout
        p2 = run(root, "--alias-only")
        assert manifest(root)["run_id"] == m["run_id"], "run_id must be a stable hash of the plan"
        passed += 1
        print("PASS dry run: manifest shape, categories, exclusions, no page writes, stable run_id")

        # 2. apply: exact rewrites, protected pages byte-identical, CRLF/BOM kept, backup.
        p = run(root, "--alias-only", "--apply")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "Applied: 18 edits across 3 files." in p.stdout, p.stdout
        got = (root / "wiki/concepts/links.md").read_bytes().decode("utf-8")
        if got != LINKS_AFTER:
            for a, b in zip(got.split("\n"), LINKS_AFTER.split("\n")):
                if a != b:
                    print("GOT ", a, "\nWANT", b)
            raise AssertionError("links.md rewrite mismatch")
        assert (root / "wiki/crlf.md").read_bytes() == b"---\r\ntitle: crlf\r\n---\r\nSee [[soil-ph|Soil pH]].\r\n"
        assert (root / "wiki/bom.md").read_bytes() == "\ufeffBOM [[soil-ph|Soil pH]]\n".encode("utf-8")
        for rel in PROTECTED:
            assert (root / rel).read_bytes() == before[rel], rel
        for rel, b in before.items():
            if rel not in ("wiki/concepts/links.md", "wiki/crlf.md", "wiki/bom.md"):
                assert (root / rel).read_bytes() == b, f"unplanned write: {rel}"
        bd = bdir(root, m["run_id"])
        for rel in ("wiki/concepts/links.md", "wiki/crlf.md", "wiki/bom.md"):
            assert (bd / rel).read_bytes() == before[rel], f"backup {rel}"
        no_tool_state_in_vault(root)
        passed += 1
        print("PASS apply: every rewrite form, table escape, code/frontmatter/fences untouched, "
              "log/append_only/auto untouched, CRLF + BOM preserved, backups = original bytes")

        # 3. idempotence + verify.
        p = run(root, "--alias-only")
        assert p.returncode == 0 and NOTHING in p.stdout, p.stdout
        v = run(root, "--alias-only", "--verify")
        assert v.returncode == 0 and "Residual: review=4, broken=1" in v.stdout, v.stdout
        # Residue parity (review 1-F3 README): after apply, lint's ALIAS-ONLY
        # is exactly the review queue plus the excluded-page residue.
        # stub-collision is left out: lint treats the stub's basename as
        # canonical (it opens the 0-byte file), so it is not alias-only there.
        m = manifest(root)
        lint_ao = lint_alias_only(root)
        queue = parity_queue(m)
        assert lint_ao == 6 and lint_ao == len(queue) + exempt_ao(m), \
            (lint_ao, queue, m["totals"])
        passed += 1
        print("PASS idempotent second dry run ('Nothing to do'); --verify exit 0 after, 1 before; "
              "lint ALIAS-ONLY == review queue + excluded residue")

        # 4. apply refuses without a manifest.
        fresh = base / "fresh"
        fb = build(fresh)
        p = run(fresh, "--alias-only", "--apply")
        assert p.returncode == 2 and "no manifest" in p.stderr, p.stdout + p.stderr
        assert all((fresh / r).read_bytes() == b for r, b in fb.items())
        passed += 1
        print("PASS --apply without manifest: exit 2, nothing written")

        # 5. a file modified after the dry run is skipped, others applied.
        p = run(fresh, "--alias-only")
        assert p.returncode == 0
        modified = b"---\r\ntitle: crlf\r\n---\r\nSee [[Soil pH]].\r\nEdited by hand.\r\n"
        (fresh / "wiki/crlf.md").write_bytes(modified)
        p = run(fresh, "--alias-only", "--apply")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "stale-since-dry-run  wiki/crlf.md" in p.stdout, p.stdout
        assert (fresh / "wiki/crlf.md").read_bytes() == modified
        assert (fresh / "wiki/concepts/links.md").read_bytes().decode() == LINKS_AFTER
        passed += 1
        print("PASS file changed after dry run -> review queue, not written; others applied")

        # 6. batch stop on a failed postcondition (test-only fault hook).
        bs = base / "batch"
        bb = build(bs)
        assert run(bs, "--alias-only").returncode == 0
        rid = manifest(bs)["run_id"]
        p = run(bs, "--alias-only", "--apply", "--batch-size", "1",
                env_extra={FAULT_ENV: "wiki/bom.md"})
        assert p.returncode == 3, p.stdout + p.stderr
        assert "POSTCONDITION FAILED" in p.stdout and "broken count rose" in p.stdout, p.stdout
        assert "2 remaining batches untouched" in p.stdout, p.stdout
        assert (bs / "wiki/concepts/links.md").read_bytes() == bb["wiki/concepts/links.md"]
        assert (bs / "wiki/crlf.md").read_bytes() == bb["wiki/crlf.md"]
        assert (bdir(bs, rid) / "wiki/bom.md").read_bytes() == bb["wiki/bom.md"]
        passed += 1
        print("PASS batch stop: postcondition failure exits 3, later batches untouched, backup intact")

        # 7. 0-byte stub collision + ambiguous never rewritten, even alone.
        solo = base / "solo"
        build(solo, {"wiki/concepts/links.md": "[[Cover crop]] [[Compost]]\n"})
        assert run(solo, "--alias-only").returncode == 0
        p = run(solo, "--alias-only", "--apply")
        assert p.returncode == 0 and (solo / "wiki/concepts/links.md").read_text() == "[[Cover crop]] [[Compost]]\n"
        passed += 1
        print("PASS ambiguous alias + 0-byte stub collision refused")

        # 8. default (backtick) mode is unchanged: same stdout + bytes as HEAD's script.
        old = legacy_result("c376aaa:scripts/fix_wikilinks.py", check=True).stdout
        outs = []
        for name, script_bytes in (("new", SCRIPT.read_bytes()), ("old", old)):
            v = base / ("default-" + name)
            (v / "scripts").mkdir(parents=True)
            (v / "scripts/fix_wikilinks.py").write_bytes(script_bytes)
            build(v, {"wiki/tick.md": "a `[[Soil pH]]` b\n", "CLAUDE.md": "`[[x]]`\n"})
            res = []
            for args in ((), ("--apply",), ()):
                p = run(v, *args, script=v / "scripts/fix_wikilinks.py")
                res.append((p.returncode, p.stdout.replace(str(v), "<V>"), p.stderr))
            snap = {k: b for k, b in snapshot(v).items() if not k.startswith("scripts/")}
            outs.append((res, snap))
        assert outs[0] == outs[1], "default mode diverged from pinned pre-151 c376aaa"
        assert "Nothing to do." in outs[0][0][2][1]
        passed += 1
        print("PASS default backtick mode: stdout, exit codes and bytes identical to pinned pre-151 c376aaa")

        # 9. mode-flag misuse is refused in default mode.
        p = run(root, "--verify")
        assert p.returncode == 2 and "require --alias-only" in p.stderr
        passed += 1
        print("PASS --verify without --alias-only -> exit 2")

        # 10. unsafe slug held for review; an alias that is also some file's
        # basename anywhere in the vault (raw/ included) is canonical in Obsidian.
        edge = base / "edge"
        build(edge, {"wiki/concepts/links.md": "[[C sharp]] [[Raw note]]\n",
                     "wiki/concepts/C# basics.md": "---\naliases: [C sharp]\n---\nx\n",
                     "wiki/concepts/raw-alias.md": "---\naliases: [Raw note]\n---\nx\n",
                     "raw/Raw note.md": "clip\n"})
        p = run(edge, "--alias-only")
        assert p.returncode == 0, p.stdout + p.stderr
        m = manifest(edge)
        assert "wiki/concepts/links.md" not in m["files"], m["files"]  # crlf/bom still planned
        # [[Raw note]] names raw/Raw note.md (Obsidian opens it) AND is the
        # alias of raw-alias.md: competing semantics -> name-collision review
        # (I6), never rewritten, and canonical to lint.
        assert m["totals"]["links"]["canonical"] == 0, m["totals"]
        assert sorted(r["category"] for r in m["review"]) == ["alias-unique-held", "name-collision"], m["review"]
        held = [r for r in m["review"] if r["category"] == "alias-unique-held"]
        assert "unsafe-slug" in held[0]["reason"]
        nc = [r for r in m["review"] if r["category"] == "name-collision"][0]
        assert nc["candidates"] == ["raw/Raw note.md", "wiki/concepts/raw-alias.md"], nc
        assert nc["lint_kind"] == "canonical", nc
        passed += 1
        print("PASS unsafe slug held for review; vault-wide basename + alias = name-collision review")

        # 11. adversarial forms (review 1-F2, 1-F3, lint 1-F1, README residue):
        # real GFM tables (header + delimiter row) without a leading pipe
        # and inside callouts get \|; fences in
        # callouts / nested at any indent and code spans crossing a line are
        # never rewritten; raw/ and templates/ basenames are canonical;
        # path-form, ~~~, multi-line-code and scripts/*.md alias links are
        # review items, so lint's residue equals the queue.
        forms = base / "forms"
        forms_before = "\n".join([
            "x | [[Alias One]]",
            "--- | ---",
            "> | y | [[Alias Two]] | q |",
            "> |---|---|---|",
            "> ```",
            "> [[Alias One]]",
            "> ```",
            "> ~~~",
            "> [[Alias Two]]",
            "> ~~~",
            # A blank line inside the nested fence: the paragraph-scoped
            # code-span matcher cannot pair the ``` runs, so only the fence
            # rule (opener at any indent) protects these (review 2-R2-T1).
            "- item",
            "    ```",
            "    [[Alias Two]]",
            "",
            "    [[Alias Two]]",
            "    ```",
            "Code `span opens",
            "[[Alias One]] and closes` here.",
            "",
            "Topic [[Topic]] tmpl [[Tmpl]] path [[concepts/Alias One]] up [[../nowhere/Holder Name]]",
            "~~~",
            "[[Alias Two]]",
            "~~~",
            "",
        ])
        fb = write_vault(forms, {
            "wiki/concepts/target-page.md": ("---\naliases: [Alias One, Alias Two, Topic, Tmpl, Holder Name]\n"
                                             "---\nbody\n"),
            "raw/Topic.md": "clip\n",
            "templates/Tmpl.md": "tmpl\n",
            "scripts/notes.md": "example [[Alias One]]\n",
            "wiki/forms.md": forms_before,
        })
        p = run(forms, "--alias-only")
        assert p.returncode == 0, p.stdout + p.stderr
        m = manifest(forms)
        assert [e["line"] for e in m["files"]["wiki/forms.md"]["edits"]] == [1, 3], m["files"]
        reasons = sorted((r["file"], r["line"], r.get("reason", "")[:9]) for r in m["review"])
        assert reasons == [("scripts/notes.md", 1, "out-of-sc"), ("wiki/forms.md", 9, "in-code"),
                           ("wiki/forms.md", 18, "in-code"), ("wiki/forms.md", 20, ""),
                           ("wiki/forms.md", 20, ""), ("wiki/forms.md", 20, "path-form"),
                           ("wiki/forms.md", 20, "path-form"), ("wiki/forms.md", 22, "in-code")], reasons
        # [[Topic]] / [[Tmpl]]: raw/ and templates/ basenames that are also
        # target-page's aliases -> name-collision (I6), not canonical.
        assert m["totals"]["links"]["canonical"] == 0 and m["totals"]["links"]["name-collision"] == 2, \
            m["totals"]
        p = run(forms, "--alias-only", "--apply")
        assert p.returncode == 0, p.stdout + p.stderr
        want = forms_before.replace("x | [[Alias One]]", "x | [[target-page\\|Alias One]]").replace(
            "> | y | [[Alias Two]] | q |", "> | y | [[target-page\\|Alias Two]] | q |")
        assert (forms / "wiki/forms.md").read_bytes().decode("utf-8") == want
        assert (forms / "scripts/notes.md").read_bytes() == fb["scripts/notes.md"]
        assert run(forms, "--alias-only").returncode == 0
        m = manifest(forms)
        lint_ao = lint_alias_only(forms)
        assert lint_ao == 6 and lint_ao == len(parity_queue(m)) + exempt_ao(m), \
            (lint_ao, m["review"])
        passed += 1
        print("PASS callout/no-leading-pipe tables escaped; callout, nested and multi-line code "
              "untouched; residue parity with lint on the adversarial fixture")

        # 12. TOCTOU (review 1-F1): a file edited after the plan but before its
        # batch is written keeps the human bytes and is listed, not written.
        tt = base / "toctou"
        tb = build(tt)
        assert run(tt, "--alias-only").returncode == 0
        rid = manifest(tt)["run_id"]
        p = run_wrapped(base, tt, "toctou", "wiki/crlf.md", "--alias-only", "--apply", "--batch-size", "1")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "changed-during-apply  wiki/crlf.md" in p.stdout, p.stdout
        assert (tt / "wiki/crlf.md").read_bytes() == tb["wiki/crlf.md"] + b"HUMAN EDIT\n"
        assert not (bdir(tt, rid) / "wiki/crlf.md").exists()
        assert (tt / "wiki/concepts/links.md").read_bytes().decode() == LINKS_AFTER
        assert "Applied: 17 edits across 2 files. Skipped: 1 files" in p.stdout, p.stdout
        passed += 1
        print("PASS TOCTOU: file changed during apply keeps the human edit, is listed, not backed up")

        # 13. recount failure after a write (review M2) -> STOP line, exit 3.
        rc_v = base / "recount"
        build(rc_v)
        assert run(rc_v, "--alias-only").returncode == 0
        p = run_wrapped(base, rc_v, "recount-error", "-", "--alias-only", "--apply", "--batch-size", "1")
        assert p.returncode == 3, p.stdout + p.stderr
        assert "POSTCONDITION FAILED: recount could not re-read the batch" in p.stdout, p.stdout
        assert "batch file  wiki/bom.md" in p.stdout and str(bdir(rc_v, manifest(rc_v)["run_id"])) in p.stdout, p.stdout
        assert "Traceback" not in p.stderr, p.stderr
        passed += 1
        print("PASS recount error -> POSTCONDITION FAILED, batch files + backup named, exit 3")

        # 14. fault hook is inert outside the temp dir (review 1-F6).
        nt = base / "notemp"
        build(nt)
        elsewhere = base / "elsewhere"
        elsewhere.mkdir()
        assert run(nt, "--alias-only").returncode == 0
        p = run_wrapped(base, nt, "notemp", str(elsewhere), "--alias-only", "--apply",
                        env_extra={FAULT_ENV: "wiki/bom.md"})
        assert p.returncode == 0 and "ignored outside a temp vault" in p.stdout, p.stdout + p.stderr
        assert b"__fault_injected" not in (nt / "wiki/bom.md").read_bytes()
        passed += 1
        print("PASS fault hook ignored (with a warning) when the vault is not under the temp dir")

        # 15. mutation gaps (review 1-F4).
        ma, mb = base / "mut-a", base / "mut-b"
        build(ma)
        bbytes = build(mb)
        assert run(ma, "--alias-only").returncode == 0
        p = run(mb, "--alias-only", "--apply", "--manifest", str(state_of(ma) / "manifest.json"))
        assert p.returncode == 2 and "manifest root" in p.stderr, p.stdout + p.stderr
        assert all((mb / r).read_bytes() == b for r, b in bbytes.items())
        # (b) an edit reverted after the write -> alias-unique drop postcondition.
        assert run(mb, "--alias-only").returncode == 0
        p = run(mb, "--alias-only", "--apply", "--batch-size", "1",
                env_extra={FAULT_ENV: "revert:wiki/bom.md"})
        assert p.returncode == 3 and "alias-unique drop" in p.stdout, p.stdout + p.stderr
        # (c) a page added after the dry run is listed, not written.
        mc = base / "mut-c"
        build(mc)
        assert run(mc, "--alias-only").returncode == 0
        (mc / "wiki/newpage.md").write_bytes(b"new [[Soil pH]]\n")
        p = run(mc, "--alias-only", "--apply")
        # exit 6: the new page holds a safe rewrite the manifest never listed
        # (review h3-R3-I1-unplanned-exit0).
        assert p.returncode == 6 and "not-in-manifest      wiki/newpage.md" in p.stdout, p.stdout
        assert (mc / "wiki/newpage.md").read_bytes() == b"new [[Soil pH]]\n"
        # (d) --verify with --apply is refused.
        p = run(mc, "--alias-only", "--verify", "--apply")
        assert p.returncode == 2 and "mutually exclusive" in p.stderr, p.stdout + p.stderr
        # (e) a slug that does not parse back to its page -> self-check hold.
        me = base / "mut-e"
        write_vault(me, {"wiki/concepts/x.md.md": "---\naliases: [Double Ext]\n---\nx\n",
                         "wiki/links.md": "see [[Double Ext]]\n"})
        assert run(me, "--alias-only").returncode == 0
        m = manifest(me)
        assert not m["files"] and len(m["review"]) == 1, m
        assert m["review"][0]["category"] == "alias-unique-held" and "self-check" in m["review"][0]["reason"], m
        passed += 1
        print("PASS manifest-root mismatch exit 2; reverted edit trips alias-unique drop; "
              "new page not-in-manifest; --verify --apply exit 2; self-check hold")

        # 16. edit-list half of the stale gate (review 2-R2-T3): the file's
        # bytes are unchanged, but a second page gains the same alias after
        # the dry run, so a planned rewrite became ambiguous. --apply must
        # list the file stale and leave it byte-identical.
        st = base / "stale-edits"
        sb = write_vault(st, {
            "wiki/concepts/soil-ph.md": PAGES["wiki/concepts/soil-ph.md"],
            "wiki/concepts/x-one.md": "---\naliases: [Alias X]\n---\none\n",
            "wiki/t3.md": "[[Soil pH]] and [[Alias X]]\n",
        })
        assert run(st, "--alias-only").returncode == 0
        assert len(manifest(st)["files"]["wiki/t3.md"]["edits"]) == 2, manifest(st)
        write_vault(st, {"wiki/concepts/x-two.md": "---\naliases: [Alias X]\n---\ntwo\n"})
        p = run(st, "--alias-only", "--apply")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "stale-since-dry-run  wiki/t3.md" in p.stdout, p.stdout
        assert (st / "wiki/t3.md").read_bytes() == sb["wiki/t3.md"]
        passed += 1
        print("PASS same bytes, changed edit list (alias became ambiguous) -> stale, not written")

        # 17. odd forms (review 2-R2-T4): an empty display or empty anchor is
        # held as odd-form -- the self-check alone would accept [[soil-ph|]].
        od = base / "odd"
        ob = write_vault(od, {
            "wiki/concepts/soil-ph.md": PAGES["wiki/concepts/soil-ph.md"],
            "wiki/odd.md": "[[Soil pH|]] and [[Soil pH#]]\n",
        })
        assert run(od, "--alias-only").returncode == 0
        m = manifest(od)
        assert m["files"] == {}, m["files"]
        assert [(r["category"], r["reason"][:8]) for r in m["review"]] == \
            [("alias-unique-held", "odd-form")] * 2, m["review"]
        assert run(od, "--alias-only", "--apply").returncode == 0
        assert (od / "wiki/odd.md").read_bytes() == ob["wiki/odd.md"]
        passed += 1
        print("PASS [[Soil pH|]] and [[Soil pH#]] held as odd-form, bytes unchanged")

        # 18. code regions Obsidian renders literally (review 2-2-F1, 2-2-F2):
        # (c1) a quoted ``` inside an unquoted fence does not close it; (c2) a
        # fence left open in a callout ends with the callout, and the later
        # unquoted ``` opens a NEW block; (c4) a closing run indented 4+ past
        # the opener is content; (c3) indented code (spaces and tab); (pre)
        # a <pre> block. Each is lint-counted, so it is an in-code review
        # item. Every file also holds a positive control that MUST still be
        # rewritten (a mask that ran to EOF would pass the negatives), and a
        # list-continuation paragraph indented 4 stays live.
        cf = base / "codeforms"
        code_files = {
            "wiki/c1.md": "```markdown\n> ```\n> [[Alias One]] inside code sample\n> ```\n```\n"
                          "after [[Alias One]]\n",
            "wiki/c2.md": "> ```\n> code\n\n```\n[[Alias One]] still code\n```\nafter [[Alias One]]\n",
            "wiki/c3.md": "Para.\n\n    [[Alias One]] indented code\n\n\t[[Alias One]] tab code\n"
                          "live [[Alias One]]\n",
            "wiki/c4.md": "```text\n    ```\n[[Alias One]] still code\n```\nafter [[Alias One]]\n",
            "wiki/pre.md": "<pre>\n[[Alias One]]\n</pre>\nafter [[Alias One]]\n",
            "wiki/list.md": "- item\n\n    [[Alias One]] continuation\n",
            "wiki/head.md": "## Head `[[Alias One]]`\n    [[Alias One]] code after heading\n\n"
                            "after [[Alias One]]\n",
        }
        write_vault(cf, dict(code_files, **{
            "wiki/concepts/target-page.md": "---\naliases: [Alias One]\n---\nbody\n"}))
        p = run(cf, "--alias-only")
        assert p.returncode == 0, p.stdout + p.stderr
        m = manifest(cf)
        got_edits = sorted((f, e["line"]) for f, v in m["files"].items() for e in v["edits"])
        assert got_edits == [("wiki/c1.md", 6), ("wiki/c2.md", 7), ("wiki/c3.md", 6),
                             ("wiki/c4.md", 5), ("wiki/head.md", 4), ("wiki/list.md", 3),
                             ("wiki/pre.md", 4)], got_edits
        got_review = sorted((r["file"], r["line"], r["category"], r.get("reason")) for r in m["review"])
        assert got_review == [("wiki/c1.md", 3, "alias-unique-held", "in-code"),
                              ("wiki/c2.md", 5, "alias-unique-held", "in-code"),
                              ("wiki/c3.md", 3, "alias-unique-held", "in-code"),
                              ("wiki/c3.md", 5, "alias-unique-held", "in-code"),
                              ("wiki/c4.md", 3, "alias-unique-held", "in-code"),
                              ("wiki/head.md", 2, "alias-unique-held", "in-code"),
                              ("wiki/pre.md", 2, "alias-unique-held", "in-code")], got_review
        m0 = m
        check_edits_lint_live(cf, m0, code_files)  # ARB-1
        p = run(cf, "--alias-only", "--apply")
        assert p.returncode == 0, p.stdout + p.stderr
        new = "[[target-page|Alias One]]"
        for rel, before_text in code_files.items():
            lines = before_text.split("\n")
            for f, ln in got_edits:
                if f == rel:
                    lines[ln - 1] = lines[ln - 1].replace("[[Alias One]]", new)
            assert (cf / rel).read_bytes().decode("utf-8") == "\n".join(lines), rel
        assert run(cf, "--alias-only").returncode == 0
        m = manifest(cf)
        # ARB-1: every edit landed as a live canonical link.
        assert (m["totals"]["links"]["canonical"] - m0["totals"]["links"]["canonical"]
                == m0["planned_links"] == 7), (m["totals"], m0["totals"])
        lint_ao = lint_alias_only(cf)
        assert lint_ao == 7 and lint_ao == len(parity_queue(m)) + exempt_ao(m), \
            (lint_ao, m["review"])
        passed += 1
        print("PASS quoted-fence-in-fence, callout-fence-then-fence, over-indented close, "
              "indented/tab code and <pre> never rewritten (in-code review); controls after "
              "each block and a list continuation rewritten; lint parity holds; every edit "
              "lint-live and canonical rose by the edit count (ARB-1)")

        # 19. block boundaries and fence openers (regression R151 review round 3).
        # Every file ends with a live control link that MUST be rewritten, so
        # a mask that swallowed the rest of the file would fail the pin.
        #   b1-b3  (3-R3-1, first list) inline code must not pair across a
        #          list item / thematic break / setext underline. lint masks
        #          these links too, so a correct mask leaves NO review item --
        #          the backstop alone would list them as lint-hidden.
        #   f1-f2  (3-R3-2, first list) ```inline``` is not a fence; a
        #          4-indented ``` after a blank line is indented code.
        #   r1-r3  (3-R3-1, second list) a col-0 fence / <pre> / thematic
        #          break ends the list, so a 4-indented line after it is code.
        #   t1-t4  (3-R3-2, second list) trailing text does not close a fence;
        #          a shorter run does not close a longer fence; list content
        #          column (gap > 4 -> 1); one-line <pre> closes itself.
        #   lh     (3-R3-4 second list / ARB-1) a mid-line ``` lint pairs as a
        #          fence: live to Obsidian and the fixer, masked by lint ->
        #          review 'lint-hidden', never rewritten; in wiki/log.md it
        #          is not counted in EXEMPT.
        #   esc    (3-R3-4, first list) \![[A]] is a link, not an embed.
        #   tick   (3-R3-3, first list) a slug holding '`' is unsafe.
        #   wrong  (3-R3-3, second list) [[wrongdir/foo]] with foo.md present
        #          is canonical (lint parity), not path-form.
        bk = base / "blocks"
        ctl = "\n\nctl [[Alias One]]\n"
        lh_text = "a ``` b\n[[Alias One]]\n\n```\ncode\n```" + ctl
        block_files = {
            "wiki/b1.md": "- don`t\n- `[[Alias One]]` is code" + ctl,
            "wiki/b2.md": "a ` b\n***\n`[[Alias One]]` c" + ctl,
            "wiki/b3.md": "Title ` x\n---\n`[[Alias One]]`" + ctl,
            "wiki/f1.md": "```inline```\n```\ncode [[Alias One]]\n```" + ctl,
            "wiki/f2.md": "para\n\n    ```\n    x\n\ntext\n\n```\n[[Alias One]] in code\n```" + ctl,
            "wiki/r1.md": "- item\n\n```\nx\n```\n\n    code [[Alias One]]" + ctl,
            "wiki/r2.md": "- item\n\n<pre>x</pre>\n\n    code [[Alias One]]" + ctl,
            "wiki/r3.md": "- item\n\n* * *\n\n    code [[Alias One]]" + ctl,
            "wiki/t1.md": "```python\n[[Alias One]]\n``` not a close\n[[Alias One]] still code\n```" + ctl,
            "wiki/t2.md": "````\n```\n[[Alias One]]\n````" + ctl,
            "wiki/t3a.md": "1.     item\n\n    [[Alias One]] continuation" + ctl,
            "wiki/t3b.md": "1. item\n\n       [[Alias One]] code" + ctl,
            "wiki/t4.md": "<pre>x</pre>\n[[Alias One]] live" + ctl,
            "wiki/lh.md": lh_text,
            # "2." cannot interrupt a paragraph (CommonMark), so no flush:
            # the code span pairs from line 1 and the link is live to the
            # fixer but masked by lint -> lint-hidden (pins the rule).
            "wiki/li2.md": "text `x\n2. `[[Alias One]]` y" + ctl,
            "wiki/esc.md": "Esc \\![[Alias One]] end ![[Alias One]] and \\\\![[Alias One]]\n",
            "wiki/tick.md": "See [[FB]] and `code` here.\n",
            "wiki/wrong.md": "[[wrongdir/foo]]\n",
        }
        write_vault(bk, dict(block_files, **{
            "wiki/concepts/holder.md": "---\naliases: [Alias One]\n---\nbody\n",
            "wiki/Foo`Bar.md": "---\naliases: [FB]\n---\nx\n",
            "wiki/concepts/foo.md": "foo\n",
            "wiki/concepts/acidity-notes.md": "---\naliases: [Soil Acidity, foo]\n---\nx\n",
            "wiki/log.md": lh_text,
        }))
        p = run(bk, "--alias-only")
        assert p.returncode == 0, p.stdout + p.stderr
        m0 = manifest(bk)
        got_edits = sorted((f, e["line"], e["old"], e["new"]) for f, v in m0["files"].items()
                           for e in v["edits"])
        hl, eb = "[[holder|Alias One]]", "![[holder]]"
        want_edits = sorted([(f, block_files[f].count("\n"), "[[Alias One]]", hl)
                             for f in block_files if block_files[f].endswith(ctl)]
                            + [("wiki/t3a.md", 3, "[[Alias One]]", hl),
                               ("wiki/t4.md", 2, "[[Alias One]]", hl),
                               ("wiki/esc.md", 1, "[[Alias One]]", hl),
                               ("wiki/esc.md", 1, "![[Alias One]]", eb),
                               ("wiki/esc.md", 1, "![[Alias One]]", eb)])
        assert got_edits == want_edits, (got_edits, want_edits)
        got_review = sorted((r["file"], r["line"], r["category"], r["reason"][:9],
                             bool(r.get("lint_hidden"))) for r in m0["review"])
        assert got_review == [("wiki/f2.md", 9, "alias-unique-held", "in-code", False),
                              ("wiki/lh.md", 2, "alias-unique-held", "lint-hidd", True),
                              ("wiki/li2.md", 2, "alias-unique-held", "lint-hidd", True),
                              ("wiki/r1.md", 7, "alias-unique-held", "in-code", False),
                              ("wiki/r2.md", 5, "alias-unique-held", "in-code", False),
                              ("wiki/r3.md", 5, "alias-unique-held", "in-code", False),
                              ("wiki/t1.md", 4, "alias-unique-held", "in-code", False),
                              ("wiki/t2.md", 3, "alias-unique-held", "in-code", False),
                              ("wiki/t3b.md", 3, "alias-unique-held", "in-code", False),
                              ("wiki/tick.md", 1, "alias-unique-held", "unsafe-sl", False)], got_review
        # log.md holds lh_text: its control counts, its lint-hidden link not.
        assert m0["totals"]["exempt"] == 1, m0["totals"]
        check_edits_lint_live(bk, m0, block_files)
        p = run(bk, "--alias-only", "--apply")
        assert p.returncode == 0 and "canonical +" in p.stdout, p.stdout + p.stderr
        esc_after = (bk / "wiki/esc.md").read_bytes().decode("utf-8")
        assert esc_after == ("Esc \\![[holder|Alias One]] end ![[holder]] and \\\\![[holder]]\n"), esc_after
        assert (bk / "wiki/tick.md").read_bytes().decode("utf-8") == block_files["wiki/tick.md"]
        assert (bk / "wiki/log.md").read_bytes().decode("utf-8") == lh_text
        assert run(bk, "--alias-only").returncode == 0
        m = manifest(bk)
        assert not m["files"], m["files"]
        assert (m["totals"]["links"]["canonical"] - m0["totals"]["links"]["canonical"]
                == m0["planned_links"]), (m["totals"], m0["totals"])
        lint_ao = lint_alias_only(bk)
        assert lint_ao == len(parity_queue(m)) + exempt_ao(m) == 9, \
            (lint_ao, m["review"], m["totals"])
        passed += 1
        print("PASS list item / thematic break / setext end inline code; ```inline``` not a fence; "
              "indented ``` is code; col-0 fence/<pre>/break end the list; fence close/length/"
              "list-gap/one-line <pre> pinned; lint-hidden link held and not counted as excluded; "
              "\\![[A]] keeps its display; '`' slug held; [[wrongdir/foo]] canonical; lint parity")

        # 20. canonical-rise postcondition (3-R3-3 / ARB-1): a write that turns
        # each new link into a code span drops the alias count by exactly the
        # edit count and leaves broken flat -- only the canonical check fires.
        cw = base / "codewrap"
        cb = build(cw)
        assert run(cw, "--alias-only").returncode == 0
        p = run_wrapped(base, cw, "codewrap", "-", "--alias-only", "--apply", "--batch-size", "1")
        assert p.returncode == 3, p.stdout + p.stderr
        assert "POSTCONDITION FAILED: canonical rise 0 != applied edits 1" in p.stdout, p.stdout
        assert "alias-unique drop" not in p.stdout and "broken count rose" not in p.stdout, p.stdout
        assert (cw / "wiki/concepts/links.md").read_bytes() == cb["wiki/concepts/links.md"]
        passed += 1
        print("PASS canonical-rise postcondition alone stops a batch whose rewrites became code")

        # 21. table escape normalization: a rewritten link on a real GFM table
        # row always gets '\|', even when the original had a bare '|' (which
        # splits the cell in Obsidian); outside a table the separator is kept
        # as written; another link on the same row is untouched.
        te = base / "tablesep"
        tb_before = "\n".join([
            "| a | b |",
            "|---|---|",
            "| [[Soil pH|acid]] | [[soil-ph|x]] |",
            "| ![[Soil pH|shown]] | [[Soil pH#Liming|lime]] |",
            "",
            "> [!note]",
            "> | h | i |",
            "> |---|---|",
            "> | [[Soil pH|callout]] | y |",
            "",
            "Outside [[Soil pH|plain]] and [[Soil pH\\|esc]] here.",
            "",
        ])
        tb_after = "\n".join([
            "| a | b |",
            "|---|---|",
            "| [[soil-ph\\|acid]] | [[soil-ph|x]] |",
            "| ![[soil-ph\\|shown]] | [[soil-ph#Liming\\|lime]] |",
            "",
            "> [!note]",
            "> | h | i |",
            "> |---|---|",
            "> | [[soil-ph\\|callout]] | y |",
            "",
            "Outside [[soil-ph|plain]] and [[soil-ph\\|esc]] here.",
            "",
        ])
        write_vault(te, {"wiki/concepts/soil-ph.md": PAGES["wiki/concepts/soil-ph.md"],
                         "wiki/tb.md": tb_before})
        p = run(te, "--alias-only")
        assert p.returncode == 0, p.stdout + p.stderr
        m = manifest(te)
        assert m["planned_links"] == 6, m["files"]
        p = run(te, "--alias-only", "--apply")
        assert p.returncode == 0, p.stdout + p.stderr
        got = (te / "wiki/tb.md").read_bytes().decode("utf-8")
        assert got == tb_after, got
        assert run(te, "--alias-only", "--verify").returncode == 0
        passed += 1
        print("PASS table rows (plain and callout) normalize a bare '|' to '\\|'; outside a table "
              "the separator is kept as written; a second link on the row is untouched")

        # 21b. table shape is never changed (review g1-C4-header-row-breaks-table,
        # g1-R1-C4-display-inner-pipe, arb-M2): each fixture is held for review
        # with its bytes untouched and the apply writes nothing.
        sys.path.insert(0, str(SCRIPTS))
        import fix_wikilinks as fw
        shape_cases = [
            # header row bare pipe: escaping it drops the header to 2 cells
            ("hdr", "\n".join(["| [[Soil pH|acid]] | b |", "|---|---|---|",
                               "| x | y | z |", ""]),
             "table-header-bare-pipe"),
            # display with its own bare pipe in a body row stays cell-split
            ("dpipe", "\n".join(["| a | b |", "|---|---|", "| [[Soil pH|x|y]] | c |", ""]),
             "odd-form (display contains a bare pipe inside a table)"),
            # not a table (2 cells vs 3): the bare '|' the rewrite adds would
            # MAKE one -- only the per-file table postcondition catches this
            ("mk", "\n".join(["| [[Soil pH]] | b |", "|---|---|---|", ""]),
             "table-structure-changed"),
        ]
        for name, body, reason in shape_cases:
            flags0 = fw._table_line_flags(body)
            sv = base / f"tshape-{name}"
            write_vault(sv, {"wiki/concepts/soil-ph.md": PAGES["wiki/concepts/soil-ph.md"],
                             "wiki/t.md": body})
            sb = snapshot(sv)
            p = run(sv, "--alias-only")
            assert p.returncode == 0, (name, p.stdout + p.stderr)
            mm = manifest(sv)
            assert mm["planned_links"] == 0, (name, mm["files"])
            assert "REVIEW_REQUIRED" in p.stdout and reason in p.stdout, (name, p.stdout)
            p = run(sv, "--alias-only", "--apply")
            assert p.returncode == 0, (name, p.stdout + p.stderr)
            got = (sv / "wiki/t.md").read_bytes()
            assert got == sb["wiki/t.md"], (name, got)
            assert fw._table_line_flags(got.decode("utf-8")) == flags0, name
        # The reviewers' exact fixture: the header link is held, the body-row
        # link is still rewritten (escaped), and the table survives.
        body = "\n".join(["| [[Soil pH|acid]] | b |", "|---|---|---|",
                          "| [[Soil pH|x]] | y | z |", ""])
        sv = base / "tshape-mixed"
        write_vault(sv, {"wiki/concepts/soil-ph.md": PAGES["wiki/concepts/soil-ph.md"],
                         "wiki/t.md": body})
        p = run(sv, "--alias-only")
        assert p.returncode == 0 and manifest(sv)["planned_links"] == 1, p.stdout + p.stderr
        assert "table-header-bare-pipe" in p.stdout, p.stdout
        p = run(sv, "--alias-only", "--apply")
        assert p.returncode == 0, p.stdout + p.stderr
        got = (sv / "wiki/t.md").read_bytes().decode("utf-8")
        assert got == body.replace("[[Soil pH|x]]", "[[soil-ph\\|x]]"), got
        assert fw._table_line_flags(got) == fw._table_line_flags(body) == [True, True, True, False]
        assert run(sv, "--alias-only", "--verify").returncode == 0
        passed += 1
        print("PASS table shape: header-row bare pipe, display inner pipe and a table-making "
              "rewrite are each held for review, bytes and table flags unchanged")

        # 21c. the batch postcondition re-asserts table shape on the WRITTEN
        # bytes: a faulted write that turns a line into a lazy table row stops
        # the batch (exit 3) naming the file.
        bf = base / "tshape-batch"
        tf_body = "\n".join(["| a | b |", "|---|---|", "| [[Soil pH]] | y |"])  # no final newline
        write_vault(bf, {"wiki/concepts/soil-ph.md": PAGES["wiki/concepts/soil-ph.md"],
                         "wiki/tf.md": tf_body})
        p = run(bf, "--alias-only")
        assert p.returncode == 0 and manifest(bf)["planned_links"] == 1, p.stdout + p.stderr
        p = run(bf, "--alias-only", "--apply", env_extra={FAULT_ENV: "wiki/tf.md"})
        assert p.returncode == 3, p.stdout + p.stderr
        assert "table structure changed in wiki/tf.md" in p.stdout, p.stdout
        passed += 1
        print("PASS batch postcondition fails (exit 3) when the written bytes change table shape")

    print(f"OK -- R151 alias-only matrix passed ({passed} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
