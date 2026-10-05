"""R151 acceptance invariants I1-I9 for fix_wikilinks.py (the owner's list,
2026-09-29). Each case is named after the invariant it pins and FAILS if that
invariant regresses.
Run: python -B scripts/tests/test_fix_wikilinks_invariants.py
All writes go to temporary fixture vaults; every run passes --root, and every
--alias-only run --state-dir <vault>.state with LOCALAPPDATA in a temp dir
(ADR-0005: operational state lives outside the vault).

  I1  three count lines, exact 'Nothing to do' sentence, --verify exit rule,
      docstring + README say review/exempt are legitimate
  I2  default mode == the PRE-PR script (pinned SHA, not main) with its own
      maintenance_preflight / _wikilib, dry + apply, in shared AND
      standalone-fallback mode, plain and BOM-prefixed append_only fixtures;
      no divergence (review h1-I2-1); the pre-PR abbreviated spellings
      (--r PATH, --a, --ap, --roo, --r=PATH) match too, and abbreviations
      of the new flags exit 2 (review h2-I2-argparse-abbrev-divergence)
  I3  --alias-only never un-backticks, default never rewrites aliases;
      alias-only flags without --alias-only are usage errors, no writes
  I4  manifest provenance fields; --apply rejects each tampered field
      (root, mode, schema_version, tool sha, planned_links, planned_files)
      and a changed script, with a byte-identical vault
  I5  adversarial syntax: embeds, headings, block ids, piped, table \\|,
      punctuation in display, / : . ( ) & + and non-ASCII names; the
      fragment is split off BEFORE alias lookup (decoy alias 'Soil pH#Liming')
  I6  ambiguous / name-collision / stub-collision / case-variant -> review,
      never rewritten; no similarity matching in the source
  I7  EXEMPT per file with the lint split; a vault with only EXEMPT links
      verifies exit 0
  I8  apply log (pre/post/backup), --restore: clean, file edited after apply
      refused, corrupted backup refused, failed-batch restore via the
      printed command; a refused rename leaves no *.tmp-alias-fix, a locked
      file mid-restore is REVIEW_REQUIRED and the rest restored (h2-R2-*)
  I9  manifest_safe_rewrites = links_applied + links_skipped_stale printed;
      lint's own ALIAS-ONLY / BROKEN drop equals the applied links lint filed
      there; an injected lint mismatch exits 4; lint absent -> NOT MEASURED;
      lint crashing after the writes exits 4; a code-strip-joined alias link
      is a review item so predicted ALIAS-ONLY == lint's
  review round h3-R3: a slug lint mangles (dotted, date) is held
  'lint-mangled-slug' (I9); unplanned safe rewrites with a ready file
  present exit 6 (I1); a failed apply of another run_id blocks --apply /
  fails --verify / warns in the dry run (I8)
  final verify: table context is a real GFM table block only (header +
  delimiter row); pipe-looking prose never escapes (I5)
  review round t1/t2 (I5): a pipe-less line after table rows is a GFM lazy
  row -> escaped (t2-R2-T2 reverses t1-LAZY-1); a row dedented below the
  open list item's content column ends the table (t2-R2-T1); only CommonMark
  HTML block starts end a table (t1-R1-T1); header cells split on every
  unescaped pipe -- a code-span / [[...]] pipe never MAKES table context on
  its own but does split a header row's cells (t1-R1-T2); backslash parity
  (t1-R1-T3); list-item content column and 4-column indent (arb-M2)
  review round t1 (I8): a clean restore settles its run (t1-DS1-a); an
  unreadable current log has its own wording and a working manual remedy,
  a corrupt prev log of a complete run blocks nothing (t1-DS1-b); a moved
  vault restores with a NOTE (arb-M1)
  review round t2 (I8): a RESTORE that cannot settle prints the SET ASIDE
  (<rid>-inspected) remedy, repeated by the dry run, and following it
  clears the guard; a corrupt prev log no longer wedges a settled run's
  same-run_id --apply (NOTE) nor refuses a failed run's RESTORE (it is a
  REVIEW_REQUIRED row instead) (t2-DS1-*)
"""
from legacy_fixture import legacy_result
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.dont_write_bytecode = True
import test_fix_wikilinks_alias as A  # noqa: E402  (shared fixture + helpers)

SCRIPTS = A.SCRIPTS
SCRIPT = A.SCRIPT
run, build, snapshot, manifest, write_vault = A.run, A.build, A.snapshot, A.manifest, A.write_vault
state_of, bdir, state_args = A.state_of, A.bdir, A.state_args
NOTHING = A.NOTHING
# The commit this PR branched from. NOT `main`: after the merge main's
# fix_wikilinks.py IS the new script and the comparison would be vacuous.
PRE_PR = "360cbcb"
EM = chr(0x2014)
E_ACUTE = chr(0xE9)
SOIL = A.PAGES["wiki/concepts/soil-ph.md"]


def sha(b):
    return hashlib.sha256(b).hexdigest()


def lint_counts(root):
    p = subprocess.run([sys.executable, "-B", str(SCRIPTS / "lint.py"), "--summary"], cwd=str(root),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    a = re.search(r"^=== ALIAS-ONLY LINKS[^\n]*=== (\d+) ", p.stdout, re.M)
    b = re.search(r"^=== BROKEN LINKS === (\d+)", p.stdout, re.M)
    assert a and b, p.stdout + p.stderr
    return int(a.group(1)), int(b.group(1))


def lines_with(text, needle):
    return [ln for ln in text.splitlines() if needle in ln]


def no_page_change(before, root, ignore=()):
    after = {k: v for k, v in snapshot(root).items() if k not in ignore}
    assert after == before, sorted(set(after.items()) ^ set(before.items()))[:4]


def i1(base):
    root = base / "i1"
    build(root, A.MAIN_EXTRA)
    p = run(root, "--alias-only")
    assert p.returncode == 0, p.stdout + p.stderr
    for line in ("SAFE_REWRITES = 18", "REVIEW_REQUIRED = 4", "EXEMPT = 3"):
        assert re.search("^" + re.escape(line) + "$", p.stdout, re.M), (line, p.stdout)
    assert "Nothing to do" not in p.stdout, p.stdout
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 1 and re.search(r"^SAFE_REWRITES = 18$", v.stdout, re.M), v.stdout
    assert "Nothing to do" not in v.stdout
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 0, ap.stdout + ap.stderr
    outs = [ap.stdout]
    d2 = run(root, "--alias-only")
    v2 = run(root, "--alias-only", "--verify")
    ap2 = run(root, "--alias-only", "--apply")  # applies the empty manifest d2 wrote
    outs += [d2.stdout, v2.stdout, ap2.stdout]
    assert d2.returncode == 0 and v2.returncode == 0 and ap2.returncode == 0, outs
    # Review and exempt remain, SAFE is 0, and verify still exits 0.
    for out in (d2.stdout, v2.stdout):
        for line in ("SAFE_REWRITES = 0", "REVIEW_REQUIRED = 4", "EXEMPT = 3"):
            assert re.search("^" + re.escape(line) + "$", out, re.M), (line, out)
    # Every 'Nothing to do' in alias mode is exactly the ruled sentence.
    hits = [ln for out in outs for ln in lines_with(out, "Nothing to do")]
    assert len(hits) == 3 and all(ln == NOTHING for ln in hits), hits
    # h1-I1-1: an EMPTY manifest must not mask safe rewrites in a file added
    # after the dry run -- no ruled sentence, exit 6, nothing written.
    e = base / "i1-empty"
    write_vault(e, {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": "canonical [[soil-ph]]\n"})
    d = run(e, "--alias-only")
    assert d.returncode == 0 and NOTHING in d.stdout and manifest(e)["planned_links"] == 0, d.stdout
    (e / "wiki/b.md").write_bytes(b"new [[Soil pH]]\n")
    eb = snapshot(e)
    ap = run(e, "--alias-only", "--apply")
    assert ap.returncode == 6, ap.stdout + ap.stderr
    assert "Nothing to do" not in ap.stdout, ap.stdout
    assert "1 safe rewrites in 1 files not in the manifest -- re-run the dry run." in ap.stdout, ap.stdout
    no_page_change(eb, e)
    v = run(e, "--alias-only", "--verify")
    assert v.returncode == 1 and "SAFE_REWRITES = 1" in v.stdout, v.stdout
    src = SCRIPT.read_text(encoding="utf-8")
    readme = (SCRIPTS / "README.md").read_text(encoding="utf-8")
    for text, name in ((src, "docstring"), (readme, "README")):
        flat = " ".join(text.split())
        assert "legitimate" in flat and "never be forced to zero" in flat, name
        assert NOTHING in flat, name
    print("PASS I1 SAFE_REWRITES / REVIEW_REQUIRED / EXEMPT lines; only the ruled 'Nothing to do' "
          "sentence (3 of 3); --verify exit 0 with REVIEW 4 + EXEMPT 3; docstring + README wording")


def git_show(sha_, path):
    return legacy_result(f"{sha_}:{path}", check=True).stdout


I2_FILES = {
    "wiki/concepts/soil-ph.md": SOIL,
    "wiki/tick.md": "a `[[Soil pH]]` b `[[soil-ph]]` and alias [[Soil pH]]\n",
    "wiki/crlf.md": "x `[[soil-ph|pH]]`\r\ny\r\n",
    "wiki/log.md": "# Log\n`[[soil-ph]]`\n",
    "wiki/append.md": "---\nappend_only: true\n---\n`[[soil-ph]]`\n",
    "wiki/autogen.md": "---\nauto_generated: true\n---\n`[[soil-ph]]`\n",
    "_meta/notes.md": "`[[soil-ph]]` and ``[[x]]``\n",
    "_meta/auto.md": "---\nauto_generated: true\n---\n`[[soil-ph]]`\n",
    "CLAUDE.md": "`[[x]]`\n",
    "wiki/plain.md": "nothing to fix [[Soil pH]]\n",
}
BOM_PAGE = {"wiki/bomhist.md": "\ufeff---\nappend_only: true\n---\nhist `[[x]]`\n"}


def i2_run(v, files):
    write_vault(v, files)
    res = []
    for args in ((), ("--apply",), ()):
        p = run(v, *args, script=v / "scripts/fix_wikilinks.py")
        res.append((p.returncode, p.stdout.replace(str(v), "<V>")))
    return res, {k: b for k, b in snapshot(v).items() if not k.startswith("scripts/")}


def i2(base):
    names = ("fix_wikilinks.py", "maintenance_preflight.py", "_wikilib.py")
    old = {n: git_show(PRE_PR, "scripts/" + n) for n in names}
    new = {n: (SCRIPTS / n).read_bytes() for n in names}
    # Non-vacuity: the pinned script really is the pre-PR one.
    assert old["fix_wikilinks.py"] != new["fix_wikilinks.py"], "PRE_PR pin is vacuous"
    assert b"--alias-only" not in old["fix_wikilinks.py"], "PRE_PR already has the alias mode"
    results = {}
    # shared: each script with its own maintenance_preflight + _wikilib;
    # fallback: _wikilib only, so the standalone fm_flag_true copy runs
    # (regression R151 review h1-I2-1: that path was never compared).
    for mode, with_names in (("shared", names), ("fallback", ("fix_wikilinks.py", "_wikilib.py"))):
        for fixture, files in (("plain", I2_FILES), ("bom", dict(I2_FILES, **BOM_PAGE))):
            for label, blobs in (("old", old), ("new", new)):
                v = base / f"i2-{mode}-{fixture}-{label}"
                (v / "scripts").mkdir(parents=True)
                for n in with_names:
                    (v / "scripts" / n).write_bytes(blobs[n])
                results[mode, fixture, label] = i2_run(v, files)
    for key in sorted({k[:2] for k in results}):
        o, n = results[key + ("old",)], results[key + ("new",)]
        assert o == n, f"default mode diverged from the pre-PR script ({key})\n" + repr(o[0]) + "\n" + repr(n[0])
        banner = "standalone fallback" if key[0] == "fallback" else "maintenance_preflight resolver"
        assert all(banner in out for _, out in n[0]), (key, n[0])
    n = results["shared", "plain", "new"]
    assert "Total: 6 replacements across 4 files." in n[0][0][1], n[0][0][1]
    # Pre-existing, frozen with the rest: default mode writes with
    # Path.write_text, so on Windows an LF page comes back CRLF (both scripts).
    nl = os.linesep.encode()
    assert n[1]["wiki/tick.md"] == b"a [[Soil pH]] b [[soil-ph]] and alias [[Soil pH]]" + nl, \
        n[1]["wiki/tick.md"]
    assert n[1]["wiki/plain.md"] == I2_FILES["wiki/plain.md"].encode()  # alias link untouched
    # No divergence on a BOM-prefixed append_only page either: default mode
    # keeps the pre-PR utf-8 flag read (the BOM hides `---`), exactly as
    # before -- BOM protection for default mode / the preflight gate is a
    # separate follow-up, not smuggled into R151 (review h1-I2-1).
    for mode in ("shared", "fallback"):
        sn = results[mode, "bom", "new"][1]
        assert sn["wiki/bomhist.md"] == "\ufeff---\nappend_only: true\n---\nhist [[x]]\n".replace(
            "\n", os.linesep).encode("utf-8"), (mode, sn["wiki/bomhist.md"])
    print(f"PASS I2 default mode == pre-PR {PRE_PR} (fix_wikilinks + maintenance_preflight + "
          "_wikilib) in shared AND standalone-fallback mode, plain and BOM fixtures: stdout, exit "
          "codes, bytes over dry/apply/dry -- no divergence")


def i3(base):
    files = {"wiki/concepts/soil-ph.md": SOIL,
             "wiki/mix.md": "tick `[[soil-ph]]` alias [[Soil pH]]\n"}
    a = base / "i3-alias"
    write_vault(a, files)
    assert run(a, "--alias-only").returncode == 0
    p = run(a, "--alias-only", "--apply")
    assert p.returncode == 0, p.stdout + p.stderr
    assert (a / "wiki/mix.md").read_text(encoding="utf-8") == \
        "tick `[[soil-ph]]` alias [[soil-ph|Soil pH]]\n", "alias mode touched the backtick"
    d = base / "i3-default"
    write_vault(d, files)
    p = run(d, "--apply")
    assert p.returncode == 0, p.stdout + p.stderr
    assert (d / "wiki/mix.md").read_text(encoding="utf-8") == "tick [[soil-ph]] alias [[Soil pH]]\n"
    u = base / "i3-usage"
    write_vault(u, files)
    before = snapshot(u)
    for flags in (("--verify",), ("--manifest", str(u / "m.json")), ("--batch-size", "5"),
                  ("--restore", "0123456789abcdef")):
        for extra in ((), ("--apply",)):
            p = run(u, *flags, *extra)
            assert p.returncode == 2 and "require --alias-only" in p.stderr, (flags, p.stderr)
            no_page_change(before, u)
    for flags in (("--restore", "0123456789abcdef", "--apply"), ("--restore", "0123456789abcdef", "--verify"),
                  ("--batch-size", "0")):
        p = run(u, "--alias-only", *flags)
        assert p.returncode == 2, (flags, p.stdout, p.stderr)
        no_page_change(before, u)
    print("PASS I3 --alias-only never un-backticks; default never rewrites aliases; --verify / "
          "--manifest / --batch-size / --restore without --alias-only exit 2 with no writes")


def i4(base):
    root = base / "i4"
    build(root)
    assert run(root, "--alias-only").returncode == 0
    m = manifest(root)
    assert m["schema_version"] == 2 and m["mode"] == "alias-only", m
    assert m["root"] == os.path.normcase(os.path.normpath(str(root.resolve()))), m["root"]
    assert m["tool_sha256"] == sha(SCRIPT.read_bytes())
    assert "tool_git_commit" in m
    gc = m["tool_git_commit"]
    if gc is not None:  # must BE the tool, not merely HEAD of a dirty checkout
        assert re.fullmatch(r"[0-9a-f]{40}", gc), gc
        assert subprocess.run(["git", "-C", str(SCRIPTS.parent), "diff", "--quiet", gc, "--",
                               "scripts/fix_wikilinks.py"]).returncode == 0, "tool_git_commit is not the tool"
    dirty = subprocess.run(["git", "-C", str(SCRIPTS.parent), "diff", "--quiet", "HEAD", "--",
                            "scripts/fix_wikilinks.py"]).returncode != 0
    assert not (dirty and gc is not None), "dirty working copy recorded as a commit"
    assert m["planned_links"] == 18 and m["planned_files"] == 3, (m["planned_links"], m["planned_files"])
    assert m["planned_links"] == sum(len(v["edits"]) for v in m["files"].values())
    assert set(m["totals"]["links"]) == {"canonical", "alias-unique", "alias-unique-held", "alias-ambiguous",
                                         "stub-collision", "name-collision", "broken", "attachment"}
    assert m["exclusions"]["path"] == ["wiki/log.md"] and "wiki/log.md" in m["edit_deny"], m["edit_deny"]
    assert re.match(r"\d{4}-\d\d-\d\dT", m["generated"])
    before = snapshot(root)
    alt = base / "i4-alt.json"
    tamper = {"root": str(base / "elsewhere"), "mode": "backtick", "schema_version": 1,
              "tool_sha256": "0" * 64, "planned_links": m["planned_links"] + 1,
              "planned_files": m["planned_files"] + 1}
    for field, value in tamper.items():
        bad = dict(m, **{field: value})
        alt.write_text(json.dumps(bad), encoding="utf-8")
        p = run(root, "--alias-only", "--apply", "--manifest", str(alt))
        assert p.returncode == 2 and field in p.stderr and "nothing written" in p.stderr, (field, p.stderr)
        no_page_change(before, root)
        assert not (root / ".alias-fix-backup").exists(), field
        assert not (state_of(root) / "backup").exists(), field
    # A real script change between dry run and apply (not a hand-edited field).
    sd = base / "i4-tool" / "scripts"
    sd.mkdir(parents=True)
    for f in SCRIPTS.glob("*.py"):
        shutil.copy2(f, sd / f.name)
    tv = base / "i4-tv"
    build(tv)
    assert run(tv, "--alias-only", script=sd / "fix_wikilinks.py").returncode == 0
    with open(sd / "fix_wikilinks.py", "ab") as fh:
        fh.write(b"\n# changed after the dry run\n")
    tb = snapshot(tv)
    p = run(tv, "--alias-only", "--apply", script=sd / "fix_wikilinks.py")
    assert p.returncode == 2 and "tool_sha256" in p.stderr, p.stdout + p.stderr
    no_page_change(tb, tv)
    # planned_links "stale files excepted": a file edited after the dry run
    # does not trip the check; the apply goes ahead without it.
    (root / "wiki/crlf.md").write_bytes(b"edited\r\n[[Soil pH]]\r\n")
    p = run(root, "--alias-only", "--apply")
    assert p.returncode == 0 and "links_skipped_stale 1" in p.stdout, p.stdout + p.stderr
    print("PASS I4 manifest schema/mode/root/tool sha/git commit/planned_links vs planned_files/"
          "totals/exclusions/edit_deny/generated; each tampered field and a changed script -> "
          "exit 2, vault byte-identical; stale files excepted from planned_links")


def i5(base):
    root = base / "i5"
    cafe = "Caf" + E_ACUTE + " " + EM + " notes"
    pages = {
        "wiki/concepts/soil-ph.md": SOIL,
        # Decoy: its alias spells the WHOLE link text including the fragment.
        # If the fragment reached the alias lookup, [[Soil pH#Liming]] would
        # resolve here instead of soil-ph#Liming.
        "wiki/concepts/decoy.md": "---\naliases:\n  - Soil pH#Liming\n  - Soil pH#^abc123\n---\nd\n",
        "wiki/concepts/ab-plan.md": "---\naliases: [\"A/B plan\"]\n---\nx\n",
        "wiki/concepts/cn.md": "---\naliases: [\"C:N\"]\n---\nx\n",
        "wiki/concepts/v2.md": "---\naliases: [\"v. 2.0 notes\"]\n---\nx\n",
        "wiki/concepts/foo-bar.md": "---\naliases: [\"Foo (bar)\"]\n---\nx\n",
        "wiki/concepts/rnd.md": "---\naliases: [\"R&D\"]\n---\nx\n",
        "wiki/concepts/cpp.md": "---\naliases: [\"C++ tips\"]\n---\nx\n",
        "wiki/concepts/cafe.md": "---\naliases:\n  - " + cafe + "\n---\nx\n",
    }
    before_lines = [
        "---",
        "related: \"[[Soil pH]]\"",
        "---",
        "E ![[Soil pH]] ![[Soil pH#Liming]] ![[Soil pH#Liming|shown]] ![[Soil pH|disp]]",
        "H [[Soil pH#Liming]] B [[Soil pH#^abc123]] HD [[Soil pH#^abc123|blk]]",
        "Piped [[Soil pH|already piped]]",
        "",
        "| h | h2 | h3 |",
        "|---|---|---|",
        "| t | [[Soil pH#Liming\\|lime]] | [[Soil pH]] |",
        "",
        "Disp [[Soil pH|a | b]] [[Soil pH|see #3]] [[Soil pH|x > y: z/w.v]]",
        "Bracket [[Soil pH|x]y]] end",
        "Inline `[[Soil pH]]` dbl ``[[Soil pH]]``",
        "```",
        "[[Soil pH]]",
        "```",
        "~~~",
        "[[Soil pH]]",
        "~~~",
        "Names [[A/B plan]] [[C:N]] [[v. 2.0 notes]] [[Foo (bar)]] [[R&D]] [[C++ tips]] [[" + cafe + "]]",
        "",
    ]
    after_lines = list(before_lines)
    after_lines[3] = ("E ![[soil-ph]] ![[soil-ph#Liming]] ![[soil-ph#Liming|shown]] ![[soil-ph|disp]]")
    after_lines[4] = ("H [[soil-ph#Liming|Soil pH > Liming]] B [[soil-ph#^abc123|Soil pH > ^abc123]] "
                      "HD [[soil-ph#^abc123|blk]]")
    after_lines[5] = "Piped [[soil-ph|already piped]]"
    after_lines[9] = "| t | [[soil-ph#Liming\\|lime]] | [[soil-ph\\|Soil pH]] |"
    after_lines[11] = "Disp [[soil-ph|a | b]] [[soil-ph|see #3]] [[soil-ph|x > y: z/w.v]]"
    after_lines[20] = ("Names [[ab-plan|A/B plan]] [[cn|C:N]] [[v2|v. 2.0 notes]] [[foo-bar|Foo (bar)]] "
                       "[[rnd|R&D]] [[cpp|C++ tips]] [[cafe|" + cafe + "]]")
    write_vault(root, dict(pages, **{"wiki/adv.md": "\n".join(before_lines)}))
    sys.path.insert(0, str(SCRIPTS))
    import _wikilib
    assert "Soil pH#Liming" in _wikilib.frontmatter_aliases(pages["wiki/concepts/decoy.md"]), \
        "decoy alias not indexed -- the fragment test would be vacuous"
    p = run(root, "--alias-only")
    assert p.returncode == 0, p.stdout + p.stderr
    m = manifest(root)
    news = [e["new"] for e in m["files"]["wiki/adv.md"]["edits"]]
    assert not any("decoy" in n for n in news), news
    review = sorted((r["line"], r["category"], r.get("reason", "")) for r in m["review"])
    assert review == [(2, "alias-unique-held", "in-frontmatter"), (19, "alias-unique-held", "in-code")], review
    p = run(root, "--alias-only", "--apply")
    assert p.returncode == 0, p.stdout + p.stderr
    got = (root / "wiki/adv.md").read_bytes().decode("utf-8")
    want = "\n".join(after_lines)
    if got != want:
        for x, y in zip(got.split("\n"), want.split("\n")):
            if x != y:
                print("GOT ", ascii(x), "\nWANT", ascii(y))
        raise AssertionError("I5 rewrite mismatch")
    print("PASS I5 embeds (+anchor/display), heading, block id, fragment+display, existing pipe, "
          "table \\|, display with | # ] > : / ., names with / : . ( ) & + and non-ASCII, "
          "frontmatter/inline/double-backtick/``` and ~~~ untouched; fragment split before alias "
          "lookup (decoy alias 'Soil pH#Liming' never chosen)")


def i6(base):
    root = base / "i6"
    links = ("[[Compost]] [[Mulch]] [[MULCH]] [[Cover crop]] [[Cover]] [[CASE]] [[Soil pH]]\n")
    files = {
        "wiki/concepts/soil-ph.md": SOIL,
        "wiki/concepts/compost-a.md": "---\naliases: [Compost]\n---\na\n",
        "wiki/concepts/compost-b.md": "---\naliases: [Compost]\n---\nb\n",
        "wiki/concepts/Mulch.md": "real mulch page\n",
        "wiki/concepts/straw.md": "---\naliases: [Mulch]\n---\ns\n",
        "wiki/entities/Cover crop.md": "",
        "wiki/concepts/cover-cropping.md": "---\naliases: [Cover crop]\n---\ncc\n",
        "wiki/concepts/cover-a.md": "---\naliases: [Cover]\n---\na\n",
        "wiki/concepts/cover-b.md": "---\naliases: [cover]\n---\nb\n",
        "wiki/a/Case.md": "one\n",
        "wiki/b/case.md": "two\n",
        "wiki/concepts/casing.md": "---\naliases: [CASE]\n---\nc\n",
        "wiki/links.md": links,
    }
    write_vault(root, files)
    assert run(root, "--alias-only").returncode == 0
    m = manifest(root)
    got = sorted((r["target"], r["category"], tuple(r["candidates"])) for r in m["review"])
    assert got == [
        ("CASE", "name-collision", ("wiki/a/Case.md", "wiki/b/case.md", "wiki/concepts/casing.md")),
        ("Compost", "alias-ambiguous", ("wiki/concepts/compost-a.md", "wiki/concepts/compost-b.md")),
        ("Cover", "alias-ambiguous", ("wiki/concepts/cover-a.md", "wiki/concepts/cover-b.md")),
        ("Cover crop", "stub-collision", ("wiki/concepts/cover-cropping.md", "wiki/entities/Cover crop.md")),
        ("MULCH", "name-collision", ("wiki/concepts/Mulch.md", "wiki/concepts/straw.md")),
        ("Mulch", "name-collision", ("wiki/concepts/Mulch.md", "wiki/concepts/straw.md")),
    ], got
    assert [e["old"] for e in m["files"]["wiki/links.md"]["edits"]] == ["[[Soil pH]]"]
    assert run(root, "--alias-only", "--apply").returncode == 0
    assert (root / "wiki/links.md").read_text(encoding="utf-8") == links.replace(
        "[[Soil pH]]", "[[soil-ph|Soil pH]]")
    # h1-I6-1: an alias spelled 'dir/foo' or 'C:bar' whose basename is a
    # real file -- lint opens that file (basename wins; ':' via
    # os.path.basename on Windows), so redirecting it to the alias
    # holder is competing semantics -> review, SAFE_REWRITES = 0.
    pv = base / "i6-path-alias"
    write_vault(pv, {
        "wiki/concepts/foo.md": "real foo\n",
        "wiki/concepts/bar.md": "real bar\n",
        "wiki/concepts/xpage.md": "---\naliases: [\"notes/foo\"]\n---\nx\n",
        "wiki/concepts/ypage.md": "---\naliases: [\"C:bar\"]\n---\ny\n",
        "wiki/links.md": "see [[notes/foo]] and [[C:bar]]\n",
    })
    pb = snapshot(pv)
    p = run(pv, "--alias-only")
    assert p.returncode == 0, p.stdout + p.stderr
    for line in ("SAFE_REWRITES = 0", "REVIEW_REQUIRED = 2"):
        assert re.search("^" + re.escape(line) + "$", p.stdout, re.M), (line, p.stdout)
    assert "SAFE PARITY: SAFE_REWRITES 0 = lint-alias-only 0 + lint-broken 0" in p.stdout, p.stdout
    m = manifest(pv)
    assert m["files"] == {}, m["files"]
    got = sorted((r["target"], r["category"], tuple(r["candidates"])) for r in m["review"])
    assert got == [
        ("C:bar", "name-collision", ("wiki/concepts/bar.md", "wiki/concepts/ypage.md")),
        ("notes/foo", "name-collision", ("wiki/concepts/foo.md", "wiki/concepts/xpage.md")),
    ], got
    if os.name == "nt":  # lint's own verdict: both links are canonical (basename wins)
        kinds = {r["target"]: r["lint_kind"] for r in m["review"]}
        assert kinds == {"notes/foo": "canonical", "C:bar": "canonical"}, kinds
    assert run(pv, "--alias-only", "--apply").returncode == 0
    no_page_change(pb, pv)  # the manifest is outside the vault (ADR-0005)
    # arb-I9-1 backstop, independent of classify(): an alias-unique link
    # lint calls canonical is held (reason lint-canonical), never an edit.
    sys.path.insert(0, str(SCRIPTS))
    import _wikilib
    import fix_wikilinks as fw
    bv = base / "i6-backstop"
    write_vault(bv, {"wiki/concepts/soil-ph.md": SOIL, "wiki/l.md": "x [[Soil pH]]\n"})
    inv = fw.Inventory(bv, _wikilib)
    fp = fw.plan_text("wiki/l.md", "x [[Soil pH]]\n", inv, _wikilib, lk=lambda t: "canonical")
    assert fp["edits"] == [] and len(fp["review"]) == 1, fp
    assert fp["review"][0]["reason"].startswith("lint-canonical"), fp["review"]
    assert fp["review"][0]["category"] == "alias-unique-held", fp["review"]
    # lint's verdict per target: the alias link is alias-only, the rewritten
    # slug canonical (the slug is also checked -- h3-R3-I9-dotted-slug).
    fp = fw.plan_text("wiki/l.md", "x [[Soil pH]]\n", inv, _wikilib,
                      lk=lambda t: "alias-only" if t == "Soil pH" else "canonical")
    assert [e["new"] for e in fp["edits"]] == ["[[soil-ph|Soil pH]]"], fp  # non-vacuity
    banned = ("difflib", "get_close_matches", "SequenceMatcher", "levenshtein", "jaro",
              "rapidfuzz", "thefuzz", "fuzzywuzzy", "jellyfish", "closest_match")
    for f in (SCRIPT, SCRIPTS / "_wikilib.py"):
        src = f.read_text(encoding="utf-8").lower()
        hit = [b for b in banned if b.lower() in src]
        assert not hit, (f.name, hit)
    print("PASS I6 ambiguous alias, alias = real filename (and its case variant), 0-byte stub, "
          "case-variant aliases, alias = two case-variant files -> review, never rewritten; "
          "no similarity matching in fix_wikilinks.py / _wikilib.py")


def i7(base):
    root = base / "i7"
    files = {
        "wiki/concepts/soil-ph.md": SOIL,
        "wiki/concepts/mass-selection.md": "---\naliases:\n  - Mass sel. vs family\n---\nx\n",
        "wiki/log.md": "# Log\n[[Soil pH]] then [[Soil pH|again]]\n[[Mass sel. vs family]]\n",
        "wiki/history.md": "---\nappend_only: true\n---\n[[Soil pH]]\n",
        "wiki/other.md": "canonical only [[soil-ph]]\n",
    }
    before = write_vault(root, files)
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 0, v.stdout + v.stderr
    for line in ("SAFE_REWRITES = 0", "REVIEW_REQUIRED = 0", "EXEMPT = 4",
                 "EXEMPT wiki/history.md = 1 (lint ALIAS-ONLY 1, lint BROKEN 0)",
                 "EXEMPT wiki/log.md = 3 (lint ALIAS-ONLY 2, lint BROKEN 1)", NOTHING):
        assert re.search("^" + re.escape(line) + "$", v.stdout, re.M), (line, v.stdout)
    p = run(root, "--alias-only")
    assert p.returncode == 0 and NOTHING in p.stdout, p.stdout
    m = manifest(root)
    assert m["exempt"] == {
        "wiki/history.md": {"links": 1, "lint_alias_only": 1, "lint_broken": 0, "lint_other": 0},
        "wiki/log.md": {"links": 3, "lint_alias_only": 2, "lint_broken": 1, "lint_other": 0}}, m["exempt"]
    assert m["totals"]["exempt"] == 4 and m["planned_links"] == 0
    p = run(root, "--alias-only", "--apply")
    assert p.returncode == 0 and NOTHING in p.stdout, p.stdout + p.stderr
    assert all((root / r).read_bytes() == b for r, b in before.items())
    ao, br = lint_counts(root)
    assert ao == A.exempt_ao(m) == 3 and br == 1, (ao, br)
    print("PASS I7 EXEMPT per file with lint split (log.md 3 = ALIAS-ONLY 2 + BROKEN 1); a vault "
          "whose only alias-only links are EXEMPT verifies exit 0 and applies nothing")


RESTORE_RE = re.compile(r'^(?:RESTORE|Undo): python "(.+?)" --alias-only --root "(.+?)" '
                        r'--restore ([0-9a-f]{16})(?: --state-dir "(.+?)")?$', re.M)


def run_printed_restore(out):
    mm = RESTORE_RE.search(out)
    assert mm, out
    sd = ["--state-dir", mm.group(4)] if mm.group(4) else []
    return subprocess.run([sys.executable, "-B", mm.group(1), "--alias-only", "--root", mm.group(2),
                           "--restore", mm.group(3), *sd], capture_output=True, text=True,
                          encoding="utf-8")


def i8(base):
    planned = ("wiki/bom.md", "wiki/concepts/links.md", "wiki/crlf.md")
    # clean restore via the printed Undo command, then idempotent.
    root = base / "i8-clean"
    before = build(root)
    assert run(root, "--alias-only").returncode == 0
    rid = manifest(root)["run_id"]
    p = run(root, "--alias-only", "--apply")
    assert p.returncode == 0, p.stdout + p.stderr
    log = json.loads((bdir(root, rid) / "apply-log.json").read_text(encoding="utf-8"))
    assert log["result"] == "complete" and sorted(log["files"]) == list(planned), log
    for rel, e in log["files"].items():
        assert e["pre_apply_sha256"] == sha(before[rel]) and e["status"] == "written"
        assert e["post_apply_sha256"] == sha((root / rel).read_bytes())
        # ADR-0005: the backup field is state-dir relative, never vault-relative.
        assert e["backup"] == f"backup/{rid}/{rel}", e["backup"]
        assert (state_of(root) / e["backup"]).read_bytes() == before[rel]
        assert not (root / e["backup"]).exists()
    # arb-I8-2: re-applying a COMPLETE apply labels the tool's own writes
    # (not 'stale-since-dry-run') in their own reconcile term; rc 0, no write.
    applied = snapshot(root)
    p2 = run(root, "--alias-only", "--apply")
    assert p2.returncode == 0, p2.stdout + p2.stderr
    for rel in planned:
        assert f"written-by-earlier-apply  {rel}" in p2.stdout, p2.stdout
    assert "stale-since-dry-run" not in p2.stdout, p2.stdout
    assert ("RECONCILE: manifest_safe_rewrites 18 = links_applied 0 + links_previously_applied 18 "
            "+ links_skipped_stale 0") in p2.stdout, p2.stdout
    assert snapshot(root) == applied
    r = run_printed_restore(p.stdout)
    assert r.returncode == 0 and "RESTORED = 3" in r.stdout and "REVIEW_REQUIRED = 0" in r.stdout, r.stdout
    assert all((root / k).read_bytes() == b for k, b in before.items())
    r = run(root, "--alias-only", "--restore", rid)
    assert r.returncode == 0 and "ALREADY_ORIGINAL = 3" in r.stdout, r.stdout
    # edited after apply -> refused, others restored.
    root = base / "i8-edited"
    before = build(root)
    assert run(root, "--alias-only").returncode == 0
    rid = manifest(root)["run_id"]
    assert run(root, "--alias-only", "--apply").returncode == 0
    with open(root / "wiki/concepts/links.md", "ab") as fh:
        fh.write(b"newer human work\n")
    human = (root / "wiki/concepts/links.md").read_bytes()
    r = run(root, "--alias-only", "--restore", rid)
    assert r.returncode == 1 and "RESTORED = 2" in r.stdout and "REVIEW_REQUIRED = 1" in r.stdout, r.stdout
    assert "wiki/concepts/links.md  (changed-since-apply" in r.stdout, r.stdout
    assert (root / "wiki/concepts/links.md").read_bytes() == human
    assert (root / "wiki/crlf.md").read_bytes() == before["wiki/crlf.md"]
    # corrupted backup -> refused, file keeps the applied bytes.
    root = base / "i8-corrupt"
    before = build(root)
    assert run(root, "--alias-only").returncode == 0
    rid = manifest(root)["run_id"]
    assert run(root, "--alias-only", "--apply").returncode == 0
    applied = (root / "wiki/crlf.md").read_bytes()
    (bdir(root, rid) / "wiki/crlf.md").write_bytes(b"corrupted")
    r = run(root, "--alias-only", "--restore", rid)
    assert r.returncode == 1 and "backup-corrupt" in r.stdout and "RESTORED = 2" in r.stdout, r.stdout
    assert (root / "wiki/crlf.md").read_bytes() == applied
    # bad run ids are rejected before any path is built.
    for bad in ("../../etc", "0123", "ZZZZZZZZZZZZZZZZ"):
        r = run(root, "--alias-only", "--restore", bad)
        assert r.returncode == 2, (bad, r.stdout, r.stderr)
    # failed batch k=3: batches 1-2 committed, batch 3 written; the printed
    # RESTORE command puts back all three.
    root = base / "i8-fail"
    before = build(root)
    assert run(root, "--alias-only").returncode == 0
    rid = manifest(root)["run_id"]
    p = run(root, "--alias-only", "--apply", "--batch-size", "1", env_extra={A.FAULT_ENV: "wiki/crlf.md"})
    assert p.returncode == 3, p.stdout + p.stderr
    assert "Committed batches (postcondition OK, still written): 1, 2 (2 files, 17 links" in p.stdout, p.stdout
    assert "batch file  wiki/crlf.md" in p.stdout and str(bdir(root, rid)) in p.stdout
    assert ("RECONCILE: manifest_safe_rewrites 18 = links_applied 17 + links_written_failed_batch 1 "
            "+ links_not_attempted 0 + links_skipped_stale 0") in p.stdout, p.stdout
    log = json.loads((bdir(root, rid) / "apply-log.json").read_text(encoding="utf-8"))
    assert [b["status"] for b in log["batches"]] == ["committed", "committed", "failed"], log["batches"]
    r = run_printed_restore(p.stdout)
    assert r.returncode == 0 and "RESTORED = 3" in r.stdout, r.stdout
    assert all((root / k).read_bytes() == b for k, b in before.items())
    # h1-I8-1 / h1-R1-1 / arb-I8-2: batch 2 of 3 fails; a re-apply with the
    # same manifest is REFUSED (rc 2, nothing written, no log rotated) and
    # names the tool's own writes 'written-by-earlier-apply'; the RESTORE
    # the failed run printed then puts back all three files byte-exact.
    root = base / "i8-reapply"
    before = build(root)
    assert run(root, "--alias-only").returncode == 0
    rid = manifest(root)["run_id"]
    bd = bdir(root, rid)
    p1 = run(root, "--alias-only", "--apply", "--batch-size", "1",
             env_extra={A.FAULT_ENV: "wiki/concepts/links.md"})
    assert p1.returncode == 3 and "batch file  wiki/concepts/links.md" in p1.stdout, p1.stdout
    assert b"__fault_injected" in (root / "wiki/concepts/links.md").read_bytes()
    failed_state = snapshot(root)
    p2 = run(root, "--alias-only", "--apply", "--batch-size", "1")
    assert p2.returncode == 2, p2.stdout + p2.stderr
    for rel in ("wiki/bom.md", "wiki/concepts/links.md"):
        assert f"written-by-earlier-apply  {rel}" in p2.stdout, p2.stdout
    assert "stale-since-dry-run" not in p2.stdout and "did not complete" in p2.stderr, p2.stdout + p2.stderr
    assert RESTORE_RE.search(p2.stdout), p2.stdout
    assert snapshot(root) == failed_state, "refused re-apply wrote something"
    assert not list(bd.glob("apply-log.prev-*.json"))
    r = run_printed_restore(p1.stdout)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout and "REVIEW_REQUIRED = 0" in r.stdout, r.stdout
    assert all((root / k).read_bytes() == b for k, b in before.items())
    # Once restored, a re-apply is allowed (the earlier log rotates aside);
    # the ORIGINAL printed RESTORE reads every apply-log*.json and undoes it.
    p3 = run(root, "--alias-only", "--apply", "--batch-size", "1")
    assert p3.returncode == 0 and "links_applied 18" in p3.stdout, p3.stdout + p3.stderr
    assert len(list(bd.glob("apply-log.prev-*.json"))) == 1
    # The rotated FAILED log must not block a re-apply of the now-complete
    # run: its post hashes equal the complete apply's (same plan).
    done = snapshot(root)
    p4 = run(root, "--alias-only", "--apply", "--batch-size", "1")
    assert p4.returncode == 0, p4.stdout + p4.stderr
    for rel in ("wiki/bom.md", "wiki/concepts/links.md", "wiki/crlf.md"):
        assert f"written-by-earlier-apply  {rel}" in p4.stdout, p4.stdout
    assert ("RECONCILE: manifest_safe_rewrites 18 = links_applied 0 + links_previously_applied 18 "
            "+ links_skipped_stale 0") in p4.stdout, p4.stdout
    assert snapshot(root) == done
    r = run_printed_restore(p1.stdout)
    assert r.returncode == 0 and "RESTORED = 3" in r.stdout and "Apply logs read:" in r.stdout, r.stdout
    assert all((root / k).read_bytes() == b for k, b in before.items())
    # Union, not just the latest log: writes recorded ONLY in a rotated log
    # (the pre-fix failure shape) are still restored, and a restore that
    # skipped them could not report REVIEW_REQUIRED = 0 with rc 0.
    root = base / "i8-union"
    before = build(root)
    assert run(root, "--alias-only").returncode == 0
    rid = manifest(root)["run_id"]
    bd = bdir(root, rid)
    p1 = run(root, "--alias-only", "--apply", "--batch-size", "1",
             env_extra={A.FAULT_ENV: "wiki/concepts/links.md"})
    assert p1.returncode == 3, p1.stdout + p1.stderr
    cur = bd / "apply-log.json"
    lg = json.loads(cur.read_text(encoding="utf-8"))
    os.replace(cur, bd / "apply-log.prev-20260101T000000000000.json")
    cur.write_text(json.dumps(dict(lg, files={}, batches=[], result="complete")), encoding="utf-8")
    r = run_printed_restore(p1.stdout)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout, r.stdout
    assert all((root / k).read_bytes() == b for k, b in before.items())
    # a write refused mid-batch (locked file) is a batch failure, not a
    # traceback: committed batches, reconcile terms and RESTORE are printed.
    root = base / "i8-lock"
    before = build(root)
    assert run(root, "--alias-only").returncode == 0
    w = base / "i8-wrapper.py"
    w.write_text(LINT_WRAPPER.format(scripts=str(SCRIPTS), mode="lock"), encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", str(w), "--root", str(root), "--alias-only", "--apply",
                        "--batch-size", "1", "--state-dir", str(state_of(root))], cwd=str(root),
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 3 and "Traceback" not in p.stderr, p.stdout + p.stderr
    assert "POSTCONDITION FAILED: write failed for wiki/crlf.md (PermissionError" in p.stdout, p.stdout
    assert ("RECONCILE: manifest_safe_rewrites 18 = links_applied 17 + links_written_failed_batch 0 "
            "+ links_not_attempted 1 + links_skipped_stale 0") in p.stdout, p.stdout
    assert (root / "wiki/crlf.md").read_bytes() == before["wiki/crlf.md"]
    r = run_printed_restore(p.stdout)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout and "ALREADY_ORIGINAL = 1" in r.stdout, r.stdout
    assert all((root / k).read_bytes() == b for k, b in before.items())
    print("PASS I8 apply log pre/post/backup per file; printed Undo/RESTORE commands run and restore "
          "byte-exact; edited-after-apply and corrupted-backup refused (REVIEW_REQUIRED, exit 1, "
          "bytes kept); bad run ids exit 2; failed batch 3 names committed batches 1, 2; a locked "
          "file mid-batch -> exit 3 with RESTORE, no traceback")


LINT_WRAPPER = '''import sys
sys.dont_write_bytecode = True
sys.path.insert(0, {scripts!r})
import fix_wikilinks as fw
orig = fw.run_lint
n = []
def skewed(root):
    n.append(1)
    got, why = orig(root)
    if got is not None and len(n) == 2:
        got = dict(got, alias_only=got["alias_only"] + 1)
    return got, why
if {mode!r} == "skew":
    fw.run_lint = skewed
elif {mode!r} == "lock":
    # A locked page (Dropbox / AV) refuses the write of wiki/crlf.md -- the
    # vault page only; its backup (outside the vault, ADR-0005) is written.
    from pathlib import Path as _P
    _vault = _P(sys.argv[sys.argv.index("--root") + 1]).resolve()
    orig_aw = fw._atomic_write
    def locked(path, data):
        if path.name == "crlf.md" and _P(path).resolve().is_relative_to(_vault):
            raise PermissionError("simulated lock")
        return orig_aw(path, data)
    fw._atomic_write = locked
else:
    # A shortfall the identity cannot explain (simulates a lost file).
    orig_rec = fw.reconcile
    fw.reconcile = lambda planned, terms, log_path=None: orig_rec(planned + 1, terms, log_path)
sys.argv = ["fix_wikilinks.py"] + sys.argv[1:]
sys.exit(fw.main())
'''


def i9(base):
    root = base / "i9"
    build(root)
    ao0, br0 = lint_counts(root)
    p = run(root, "--alias-only")
    assert p.returncode == 0
    m = manifest(root)
    pred = m["totals"]["predicted_lint_alias_only"]
    assert pred["total"] == ao0 == 19 and pred["safe"] == 14, (pred, ao0)
    # arb-I9-1: SAFE_REWRITES splits exactly into lint ALIAS-ONLY + BROKEN.
    assert pred["safe_lint_broken"] == 4 and pred["safe_other"] == 0, pred
    assert "SAFE PARITY: SAFE_REWRITES 18 = lint-alias-only 14 + lint-broken 4" in p.stdout, p.stdout
    p = run(root, "--alias-only", "--apply")
    assert p.returncode == 0, p.stdout + p.stderr
    ao1, br1 = lint_counts(root)
    assert ao0 - ao1 == 14 and br0 - br1 == 4, (ao0, ao1, br0, br1)
    for line in ("RECONCILE: manifest_safe_rewrites 18 = links_applied 18 + links_skipped_stale 0",
                 "LINT RECONCILIATION: links_applied 18 = lint-alias-only 14 + lint-broken 4 + other 0",
                 f"  ALIAS-ONLY {ao0} -> {ao1} (drop 14) vs links_applied lint-alias-only 14: OK"):
        assert line in p.stdout, (line, p.stdout)
    assert f"  BROKEN {br0} -> {br1} (drop 4) vs links_applied lint-broken 4: OK" in p.stdout, p.stdout
    # stale file: explicitly listed, moved to REVIEW_REQUIRED, identity holds.
    st = base / "i9-stale"
    build(st)
    assert run(st, "--alias-only").returncode == 0
    (st / "wiki/crlf.md").write_bytes(b"hand edit [[Soil pH]]\r\n")
    p = run(st, "--alias-only", "--apply")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "stale-since-dry-run  wiki/crlf.md  (1 links -> REVIEW_REQUIRED)" in p.stdout, p.stdout
    assert "RECONCILE: manifest_safe_rewrites 18 = links_applied 17 + links_skipped_stale 1" in p.stdout
    # an unexplained lint movement is not swallowed: exit 4 with a reason.
    sk = base / "i9-skew"
    build(sk)
    assert run(sk, "--alias-only").returncode == 0
    w = base / "i9-wrapper.py"
    w.write_text(LINT_WRAPPER.format(scripts=str(SCRIPTS), mode="skew"), encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", str(w), "--root", str(sk), "--alias-only", "--apply",
                        "--state-dir", str(state_of(sk))],
                       cwd=str(sk), capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 4 and ": MISMATCH" in p.stdout and "MISMATCH reason:" in p.stdout, p.stdout
    # an identity that does not hold is asserted: exit 5, never a quiet 0.
    sf = base / "i9-short"
    build(sf)
    assert run(sf, "--alias-only").returncode == 0
    w.write_text(LINT_WRAPPER.format(scripts=str(SCRIPTS), mode="short"), encoding="utf-8")
    p = subprocess.run([sys.executable, "-B", str(w), "--root", str(sf), "--alias-only", "--apply",
                        "--state-dir", str(state_of(sf))],
                       cwd=str(sf), capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 5 and "RECONCILE FAILED: 19 != 18 (unexplained shortfall" in p.stdout, p.stdout
    # no lint.py beside the fixer: loudly NOT MEASURED, never a silent pass.
    sd = base / "i9-nolint" / "scripts"
    sd.mkdir(parents=True)
    for n in ("fix_wikilinks.py", "_wikilib.py", "maintenance_preflight.py"):
        shutil.copy2(SCRIPTS / n, sd / n)
    nl = base / "i9-nl"
    build(nl)
    assert run(nl, "--alias-only", script=sd / "fix_wikilinks.py").returncode == 0
    p = run(nl, "--alias-only", "--apply", script=sd / "fix_wikilinks.py")
    assert p.returncode == 0 and "LINT RECONCILIATION: NOT MEASURED" in p.stdout, p.stdout + p.stderr
    print("PASS I9 manifest_safe_rewrites = links_applied + links_skipped_stale (stale listed as "
          "REVIEW_REQUIRED); lint ALIAS-ONLY drop 14 = applied lint-alias-only, BROKEN drop 4 = "
          "applied dotted/slash/colon names; predicted lint ALIAS-ONLY == real lint; skewed lint "
          "-> exit 4; no lint.py -> NOT MEASURED")


def i2_abbrev(base):
    """h2-I2-argparse-abbrev-divergence: the pre-PR parser (only --apply /
    --root / --help) accepted every unambiguous prefix. The new parser must
    give the same rc, stdout and bytes for those spellings, and must not
    accept abbreviations of the new flags."""
    names = ("fix_wikilinks.py", "maintenance_preflight.py", "_wikilib.py")
    old = {n: git_show(PRE_PR, "scripts/" + n) for n in names}
    new = {n: (SCRIPTS / n).read_bytes() for n in names}
    spellings = ((("--r", "{v}"),), (("--ro", "{v}"),), (("--roo", "{v}"),),
                 (("--r", "{v}"), ("--a",)), (("--a",), ("--r", "{v}")),
                 (("--ap",), ("--roo", "{v}")), (("--appl",), ("--r={v}",)))
    res = {}
    for i, sp in enumerate(spellings):
        for label, blobs in (("old", old), ("new", new)):
            v = base / f"i2ab-{i}-{label}"
            (v / "scripts").mkdir(parents=True)
            for n in names:
                (v / "scripts" / n).write_bytes(blobs[n])
            write_vault(v, I2_FILES)
            argv = [a.format(v=str(v)) for grp in sp for a in grp]
            env = dict(os.environ)
            env.pop(A.FAULT_ENV, None)
            p = subprocess.run([sys.executable, "-B", str(v / "scripts/fix_wikilinks.py"), *argv],
                               cwd=str(v), env=env, capture_output=True, text=True, encoding="utf-8")
            snap = {k: b for k, b in snapshot(v).items() if not k.startswith("scripts/")}
            res[i, label] = (p.returncode, p.stdout.replace(str(v), "<V>"),
                             p.stderr.replace(str(v), "<V>"), snap)
        o, n = res[i, "old"], res[i, "new"]
        assert o[0] == 0, ("pre-PR rejected a spelling it should accept", sp, o)
        assert o == n, ("abbreviated spelling diverged from pre-PR", sp, o[:3], n[:3])
    # --a really applied (bytes changed), so the comparison is not vacuous.
    assert res[3, "new"][3]["wiki/tick.md"] != I2_FILES["wiki/tick.md"].encode(), res[3, "new"][3]
    # New flags are never abbreviated: rc 2, nothing written.
    for bad in (("--alias",), ("--al",), ("--ver",), ("--rest", "0" * 16), ("--man", "x.json"),
                ("--batch", "3")):
        v = base / ("i2ab-bad" + bad[0])
        before = write_vault(v, dict(I2_FILES, **{"wiki/concepts/soil-ph.md": SOIL}))
        p = run(v, *bad)
        assert p.returncode == 2 and "unrecognized arguments" in p.stderr, (bad, p.stdout, p.stderr)
        assert {k: b for k, b in snapshot(v).items()} == before, bad
    print(f"PASS I2 abbreviated flags: {len(spellings)} pre-PR spellings (--r/--ro/--roo PATH, --a, "
          "--ap, --appl, --r=PATH) give the pre-PR rc, stdout and bytes; abbreviations of the new "
          "flags exit 2 with no write")


# Patches os.replace in-process so the REAL _atomic_write tmp path runs and
# the rename onto <name> fails, as a Windows open handle makes it.
REPLACE_LOCK_WRAPPER = '''import os, sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, {scripts!r})
import fix_wikilinks as fw
orig = os.replace
# Lock only the vault page: backups and logs live outside the vault (ADR-0005).
_vault = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
def locked(src, dst, *a, **k):
    if Path(dst).name == {name!r} and Path(dst).resolve().is_relative_to(_vault):
        raise PermissionError(13, "simulated lock", str(dst))
    return orig(src, dst, *a, **k)
os.replace = locked
sys.argv = ["fix_wikilinks.py"] + sys.argv[1:]
sys.exit(fw.main())
'''


def run_replace_locked(base, root, name, *args):
    w = base / "replace-lock-wrapper.py"
    w.write_text(REPLACE_LOCK_WRAPPER.format(scripts=str(SCRIPTS), name=name), encoding="utf-8")
    env = dict(os.environ)
    env.pop(A.FAULT_ENV, None)
    return subprocess.run([sys.executable, "-B", str(w), "--root", str(root), *args,
                           *state_args(root, args)], cwd=str(root),
                          env=env, capture_output=True, text=True, encoding="utf-8")


def tmp_leftovers(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*.tmp-alias-fix"))


def i8_locks(base):
    """h2-R2-TMP-LEAK + h2-R2-RESTORE-LOCK: a rename refused by the OS
    leaves no tmp file (apply and restore), and restore keeps going past a
    locked file, records it, and recovers on a re-run."""
    # apply: the rename onto wiki/crlf.md fails -> exit 3, no tmp left.
    root = base / "i8-tmpleak"
    before = build(root)
    assert run(root, "--alias-only").returncode == 0
    rid = manifest(root)["run_id"]
    p = run_replace_locked(base, root, "crlf.md", "--alias-only", "--apply")
    assert p.returncode == 3 and "Traceback" not in p.stderr, p.stdout + p.stderr
    assert "write failed for wiki/crlf.md (PermissionError" in p.stdout, p.stdout
    assert tmp_leftovers(root) == [], tmp_leftovers(root)
    assert (root / "wiki/crlf.md").read_bytes() == before["wiki/crlf.md"]
    # a leftover from an older tool is swept by --restore (and recorded).
    stray = root / "wiki/crlf.md.tmp-alias-fix"
    stray.write_bytes(b"planned bytes [[soil-ph|Soil pH]]\n")
    r = run(root, "--alias-only", "--restore", rid)
    assert r.returncode == 0 and "removed leftover  wiki/crlf.md.tmp-alias-fix" in r.stdout, r.stdout
    assert tmp_leftovers(root) == [] and all((root / k).read_bytes() == b for k, b in before.items())
    log = json.loads((bdir(root, rid) / "apply-log.json").read_text(encoding="utf-8"))
    assert log["restores"][-1]["tmp_removed"] == ["wiki/crlf.md.tmp-alias-fix"], log["restores"]
    # restore: wiki/crlf.md is locked -> the others are restored, the locked
    # one is REVIEW_REQUIRED restore-write-failed, ledger written, exit 1.
    root = base / "i8-restorelock"
    before = build(root)
    assert run(root, "--alias-only").returncode == 0
    rid = manifest(root)["run_id"]
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 0, ap.stdout + ap.stderr
    applied = snapshot(root)
    r = run_replace_locked(base, root, "crlf.md", "--alias-only", "--restore", rid)
    assert r.returncode == 1 and "Traceback" not in r.stderr, r.stdout + r.stderr
    assert "RESTORED = 2" in r.stdout and "REVIEW_REQUIRED = 1" in r.stdout, r.stdout
    assert "wiki/crlf.md  (restore-write-failed (PermissionError" in r.stdout, r.stdout
    for rel in ("wiki/bom.md", "wiki/concepts/links.md"):
        assert (root / rel).read_bytes() == before[rel], rel
    assert (root / "wiki/crlf.md").read_bytes() == applied["wiki/crlf.md"] != before["wiki/crlf.md"]
    assert tmp_leftovers(root) == [], tmp_leftovers(root)
    log = json.loads((bdir(root, rid) / "apply-log.json").read_text(encoding="utf-8"))
    last = log["restores"][-1]
    assert sorted(last["restored"]) == ["wiki/bom.md", "wiki/concepts/links.md"], last
    assert [x["file"] for x in last["review_required"]] == ["wiki/crlf.md"], last
    assert last["review_required"][0]["reason"].startswith("restore-write-failed"), last
    # A lock is transient: no SET ASIDE from the restore, nor from a dry run
    # over the same ledger marked unfinished (one predicate; t2-DS1).
    assert "SET ASIDE" not in r.stdout, r.stdout
    lp = bdir(root, rid) / "apply-log.json"
    lp.write_text(json.dumps(dict(log, result="postcondition-failed")), encoding="utf-8")
    d = run(root, "--alias-only")
    assert d.returncode == 0 and f"WARNING: unfinished apply {rid}" in d.stdout, d.stdout
    assert "RESTORE:" in d.stdout and "SET ASIDE" not in d.stdout, d.stdout
    # the lock clears: the same command finishes the job.
    r2 = run(root, "--alias-only", "--restore", rid)
    assert r2.returncode == 0 and "RESTORED = 1" in r2.stdout and "ALREADY_ORIGINAL = 2" in r2.stdout, r2.stdout
    assert all((root / k).read_bytes() == b for k, b in before.items())
    print("PASS I8 locks: a refused rename leaves no *.tmp-alias-fix (apply exit 3); --restore sweeps "
          "a planted leftover; a locked file mid-restore is REVIEW_REQUIRED restore-write-failed, the "
          "others restored, ledger written, exit 1, and a re-run recovers")


LINT_CRASH_GATE = '''import os as _gate_os
_gate_n = _gate_os.path.join(_gate_os.path.dirname(_gate_os.path.abspath(__file__)), "lint-calls.txt")
_gate_c = (int(open(_gate_n).read()) if _gate_os.path.exists(_gate_n) else 0) + 1
open(_gate_n, "w").write(str(_gate_c))
if _gate_c == 2:
    raise RuntimeError("injected lint crash after the writes")
'''


def i9_extra(base):
    """h2-I9-lint-after-unmeasured-exit0 and h2-I9-strip-join-invisible-alias."""
    fixture = {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": "one [[Soil pH]] two\n"}
    # lint measured before the writes, crashes after them: exit 4, loud.
    sd = base / "i9-crash-scripts" / "scripts"
    sd.mkdir(parents=True)
    for n in ("fix_wikilinks.py", "_wikilib.py", "maintenance_preflight.py"):
        shutil.copy2(SCRIPTS / n, sd / n)
    src = (SCRIPTS / "lint.py").read_text(encoding="utf-8")
    head, sep, rest = src.partition("from __future__ import annotations\n")
    assert sep, "lint.py lost its __future__ line; move the gate"
    (sd / "lint.py").write_text(head + sep + LINT_CRASH_GATE + rest, encoding="utf-8")
    v = base / "i9-crash"
    write_vault(v, fixture)
    d = run(v, "--alias-only", script=sd / "fix_wikilinks.py")
    assert d.returncode == 0 and "SAFE_REWRITES = 1" in d.stdout, d.stdout + d.stderr
    p = run(v, "--alias-only", "--apply", script=sd / "fix_wikilinks.py")
    assert (sd / "lint-calls.txt").read_text() == "2", "gate did not see two lint runs"
    assert (v / "wiki/a.md").read_bytes() == b"one [[soil-ph|Soil pH]] two\n", (v / "wiki/a.md").read_bytes()
    assert p.returncode == 4, (p.returncode, p.stdout, p.stderr)
    assert "LINT RECONCILIATION FAILED: NOT MEASURED after the writes -- lint.py exited 1:" in p.stdout, p.stdout
    assert "injected lint crash after the writes" in p.stdout, p.stdout
    assert "lint predates alias-only support?" not in p.stdout, p.stdout
    # a code-strip join: lint deletes `x` and counts [[Soil pH]] as ALIAS-ONLY;
    # the fixer lists it for review so predicted == lint, no false 0.
    v = base / "i9-join"
    write_vault(v, {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": "one [[Soil p`x`H]] two\n"})
    ao, _ = lint_counts(v)
    assert ao == 1, ao
    p = run(v, "--alias-only")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "Predicted lint.py ALIAS-ONLY now: 1 = safe 0 + review 1 + EXEMPT 0" in p.stdout, p.stdout
    for line in ("SAFE_REWRITES = 0", "REVIEW_REQUIRED = 1", "EXEMPT = 0"):
        assert re.search("^" + re.escape(line) + "$", p.stdout, re.M), (line, p.stdout)
    m = manifest(v)
    assert m["totals"]["predicted_lint_alias_only"]["total"] == ao, m["totals"]
    (item,) = m["review"]
    assert item["reason"] == "lint-only (code-strip join)" and item["lint_kind"] == "alias-only", item
    assert (item["file"], item["line"], item["col"], item["target"]) == ("wiki/a.md", 1, 5, "Soil pH"), item
    assert item["category"] == "alias-unique-held" and item["candidates"] == ["wiki/concepts/soil-ph.md"], item
    # the same alias also written normally in the file, either order: the
    # review item is the joined link (col 5 / col 21), the edit the other.
    for name, body, rcol, ecol in (("join-first", "one [[Soil p`x`H]] two [[Soil pH]]\n", 5, 24),
                                   ("join-second", "one [[Soil pH]] two [[Soil p`x`H]]\n", 21, 5)):
        v = base / ("i9-" + name)
        write_vault(v, {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": body})
        p = run(v, "--alias-only")
        assert p.returncode == 0, p.stdout + p.stderr
        for line in ("SAFE_REWRITES = 1", "REVIEW_REQUIRED = 1"):
            assert re.search("^" + re.escape(line) + "$", p.stdout, re.M), (name, line, p.stdout)
        m = manifest(v)
        (item,) = m["review"]
        assert (item["line"], item["col"], item["reason"]) == (1, rcol, "lint-only (code-strip join)"), \
            (name, item)
        (edit,) = m["files"]["wiki/a.md"]["edits"]
        assert (edit["col"], edit["old"]) == (ecol, "[[Soil pH]]"), (name, edit)
        assert m["totals"]["predicted_lint_alias_only"]["total"] == lint_counts(v)[0] == 2, name
    # the same join in an append_only page is EXEMPT, still matching lint.
    v = base / "i9-join-exempt"
    write_vault(v, {"wiki/concepts/soil-ph.md": SOIL,
                    "wiki/h.md": "---\nappend_only: true\n---\nold [[Soil p`x`H]]\n"})
    p = run(v, "--alias-only")
    assert p.returncode == 0 and re.search(r"^EXEMPT = 1$", p.stdout, re.M), p.stdout
    assert manifest(v)["totals"]["predicted_lint_alias_only"]["total"] == lint_counts(v)[0] == 1
    print("PASS I9 extra: lint crashing after the writes -> exit 4 naming 'lint.py exited 1' (files "
          "written, no 'lint predates alias-only support' guess); a code-strip-joined alias link is REVIEW_REQUIRED "
          "'lint-only (code-strip join)' at wiki/a.md:1:5 and predicted ALIAS-ONLY 1 == lint's 1 "
          "(EXEMPT in an append_only page)")


def ghost_count(root):
    p = subprocess.run([sys.executable, "-B", str(SCRIPTS / "lint.py"), "--summary"], cwd=str(root),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    g = re.search(r"^=== GHOST/PLACEHOLDER LINKS === (\d+)", p.stdout, re.M)
    assert g, p.stdout + p.stderr
    return int(g.group(1))


def i1_unplanned(base):
    """h3-R3-I1-unplanned-exit0: with a READY file present, safe rewrites in a
    file added after the dry run still exit 6 with the dry-run hint; the
    ready file is applied, the unplanned one is not touched."""
    root = base / "i1-unplanned"
    write_vault(root, {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": "see [[Soil pH]]\n"})
    d = run(root, "--alias-only")
    assert d.returncode == 0 and "SAFE_REWRITES = 1" in d.stdout, d.stdout
    new = b"new [[Soil pH]] and [[Soil pH]]\n"
    (root / "wiki/b.md").write_bytes(new)
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 6, (ap.returncode, ap.stdout, ap.stderr)
    assert "2 safe rewrites in 1 files not in the manifest -- re-run the dry run." in ap.stdout, ap.stdout
    assert "review  not-in-manifest      wiki/b.md  (2 links)" in ap.stdout, ap.stdout
    assert "Nothing to do" not in ap.stdout, ap.stdout
    assert (root / "wiki/a.md").read_bytes() == b"see [[soil-ph|Soil pH]]\n"
    assert (root / "wiki/b.md").read_bytes() == new
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 1 and "SAFE_REWRITES = 2" in v.stdout, v.stdout
    print("PASS I1 unplanned: ready file applied, 2 safe rewrites in a file added after the dry run "
          "-> exit 6 with the 're-run the dry run' line and a per-file link count; file untouched")


def i5_lazy_table(base):
    """Table context end to end. review t2-R2-T2 (reverses t1-LAZY-1): a
    pipe-less line after a table's rows is a GFM lazy continuation ROW ->
    escaped pipe (safe under either renderer reading); a blank line ends
    the table. t2-R2-T1: a row dedented below the open list item's content
    column is outside the table -> plain pipe. Final-verify defect: only a REAL table
    block (header + delimiter row) is table context -- a code-span '|', a
    list item after a table, or pipe-looking prose with no delimiter row ->
    plain pipe. Review t1-R1-T1/T2/T3 and arb-M2: an inline tag / autolink
    row stays in the table and an inline-tag header opens one; header cells
    split on EVERY unescaped pipe (code span and [[...]] too); an escaped
    backslash before a pipe does not escape it; a delimiter row that is a
    lazy line under a list item, or 4-space indented, opens no table."""
    esc, plain = "[[soil-ph\\|Soil pH]]", "[[soil-ph|Soil pH]]"
    cases = (("lazy", "| a |\n|---|\n| x |\n[[Soil pH]]\n", f"| a |\n|---|\n| x |\n{esc}\n"),
             # t2-R2-T2: a line whose only pipe is escaped is a lazy row too.
             ("escaped-only", "| a |\n|---|\n| x |\nx \\| [[Soil pH]]\n",
              f"| a |\n|---|\n| x |\nx \\| {esc}\n"),
             ("blank", "| a |\n|---|\n| x |\n\n[[Soil pH]]\n", "| a |\n|---|\n| x |\n\n[[soil-ph|Soil pH]]\n"),
             ("callout", "> | a |\n> |---|\n> [[Soil pH]]\n>\n> [[Soil pH]]\n",
              f"> | a |\n> |---|\n> {esc}\n>\n> [[soil-ph|Soil pH]]\n"),
             # t2-R2-T1: a row dedented below the list item's content column
             # closes the item and so the table -- plain '|' at column 0.
             ("list-dedent", "- item\n\n  | h | g |\n  |---|---|\n| [[Soil pH]] | y |\n",
              f"- item\n\n  | h | g |\n  |---|---|\n| {plain} | y |\n"),
             ("nested-dedent", "- a\n  - b\n\n    | h |\n    |---|\n| [[Soil pH]] |\n",
              f"- a\n  - b\n\n    | h |\n    |---|\n| {plain} |\n"),
             # t1-R1-T1: autolink row + inline-tag row in body position, and
             # the 2-col row after them, all stay table rows.
             ("html-inline-body", "| a | b |\n|---|---|\n| x | y |\n<https://example.com> | [[Soil pH]]\n"
                                  "<b>bold</b> | [[Soil pH]]\n| z | [[Soil pH]] |\n",
              f"| a | b |\n|---|---|\n| x | y |\n<https://example.com> | {esc}\n"
              f"<b>bold</b> | {esc}\n| z | {esc} |\n"),
             ("html-inline-header", "<b>Name</b> | Link\n--- | ---\n| z | [[Soil pH]] |\n",
              f"<b>Name</b> | Link\n--- | ---\n| z | {esc} |\n"),
             # t1-R1-T2: header cells split on every unescaped pipe.
             ("code-header-3col", "| `a|b` | c |\n|---|---|---|\n| 1 | 2 | [[Soil pH]] |\n",
              f"| `a|b` | c |\n|---|---|---|\n| 1 | 2 | {esc} |\n"),
             ("code-header-2delim", "| `a|b` | c |\n|---|---|\n| 1 | [[Soil pH]] |\n",
              f"| `a|b` | c |\n|---|---|\n| 1 | {plain} |\n"),
             ("wikilink-header", "[[x|y]] | c\n---|---\n[[Soil pH]] | d\n",
              f"[[x|y]] | c\n---|---\n{plain} | d\n"),
             # t1-R1-T3: '\\\\|' is an escaped backslash then a separator;
             # '\\|' stays an escaped pipe.
             ("escaped-backslash", "a \\\\| b\n---|---\n[[Soil pH]] | x\n",
              f"a \\\\| b\n---|---\n{esc} | x\n"),
             ("escaped-pipe", "a \\| b\n---|---\n[[Soil pH]] | x\n",
              f"a \\| b\n---|---\n{plain} | x\n"),
             # arb-M2: delimiter row not in the list item / indented code.
             ("list-lazy-delim", "- | a |\n|---|\n[[Soil pH]] | x\n",
              f"- | a |\n|---|\n{plain} | x\n"),
             ("indented-delim", "| a |\n    |---|\n[[Soil pH]] | x\n",
              f"| a |\n    |---|\n{plain} | x\n"),
             ("list-table", "- | a |\n  |---|\n  | [[Soil pH]] |\n",
              f"- | a |\n  |---|\n  | {esc} |\n"),
             # A table in a nested bullet's continuation (4-space indent, no
             # marker on the header line) is still a table.
             ("nested-list-table", "- a\n  - b\n\n    | h |\n    |---|\n    | [[Soil pH]] |\n",
              f"- a\n  - b\n\n    | h |\n    |---|\n    | {esc} |\n"),
             # Final-verify defect (an open-loops ledger list continuation): a '|'
             # inside an inline code span on a list item is no table row, so
             # the next list item's link keeps a plain pipe.
             ("openloops", "- [ ] [added 2026-07-02] use `slug|Display` form\n"
                           "- [ ] [added 2026-07-02] see [[Soil pH]] now\n",
              "- [ ] [added 2026-07-02] use `slug|Display` form\n"
              "- [ ] [added 2026-07-02] see [[soil-ph|Soil pH]] now\n"),
             ("real", "| h | g |\n|:--|--:|\n| [[Soil pH]] | x |\n",
              "| h | g |\n|:--|--:|\n| [[soil-ph\\|Soil pH]] | x |\n"),
             ("list-after", "| h |\n|---|\n| x |\n- [[Soil pH]]\n",
              "| h |\n|---|\n| x |\n- [[soil-ph|Soil pH]]\n"),
             ("prose-pipes", "a | b\nc | [[Soil pH]] | d\n",
              "a | b\nc | [[soil-ph|Soil pH]] | d\n"),
             ("callout-row", "> [!note]\n> | h |\n> |---|\n> | [[Soil pH]] |\n>\n> [[Soil pH]]\n",
              "> [!note]\n> | h |\n> |---|\n> | [[soil-ph\\|Soil pH]] |\n>\n> [[soil-ph|Soil pH]]\n"),
             # arb-F (pinned, no fix): 4-space table after a paragraph line
             # is a table; after a blank line it is indented code (untouched).
             ("para-4sp", "para\n    | a |\n    |---|\n    | [[Soil pH]] |\n",
              f"para\n    | a |\n    |---|\n    | {esc} |\n"),
             ("blank-4sp-code", "para\n\n    | a |\n    |---|\n    | [[Soil pH]] |\n",
              "para\n\n    | a |\n    |---|\n    | [[Soil pH]] |\n"),
             ("lazy-mid", "| h |\n|---|\n| x |\nlazy [[Soil pH]]\n| y |\n",
              f"| h |\n|---|\n| x |\nlazy {esc}\n| y |\n"))
    for name, body, want in cases:
        root = base / ("i5-table-" + name)
        write_vault(root, {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": body})
        assert run(root, "--alias-only").returncode == 0
        p = run(root, "--alias-only", "--apply")
        assert p.returncode == 0, (name, p.stdout, p.stderr)
        got = (root / "wiki/a.md").read_bytes().decode("utf-8")
        assert got == want, (name, ascii(got), ascii(want))
    print("PASS I5 table context = real GFM table block only: header + delimiter row -> \\| on rows "
          "(also inside a callout, after inline-tag / autolink rows, under an inline-tag header, "
          "under a code-span-pipe header whose GFM cell count matches, after '\\\\|', and on a "
          "pipe-less or escaped-pipe-only lazy row); plain '|' on a row dedented out of its list "
          "item, after a blank / '>' blank line, on a list item after a table, "
          "on pipe-looking prose, after a code-span '|' (open-loops.md:44-45 shape), under a header "
          "whose GFM cell count differs, after '\\|', and when the delimiter row is a lazy line under "
          "a list item or 4-space indented")


def i5_table_flags(base):
    """Line-level table flags for every review probe, including the positive
    controls that must still END a table (a block-level HTML start, any
    case; a lone tag line; a comment) and the list-item / indent rules."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("fix_wikilinks_under_test", SCRIPT)
    F = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(F)
    finally:
        sys.path.remove(str(SCRIPTS))
    probes = (
        (["| a | b |", "|---|---|", "| x | y |", "<https://example.com> | [[A]]",
          "<b>bold</b> | [[A]]", "| z | [[A]] |"], "TTTTTT"),
        (["<b>Name</b> | Link", "--- | ---", "| z | [[A]] |"], "TTT"),
        (["| a | b |", "|---|---|", "| z | [[A]] |", ""], "TTT."),
        # Each HTML control line carries an unescaped pipe, so only the
        # HTML-start test can end the table.
        (["| a |", "|---|", "| x |", '<div class="a|b">', "| y |"], "TTT.."),
        (["| a |", "|---|", "| x |", "<DIV> | y", "| y |"], "TTT.."),
        (["| a |", "|---|", "| x |", "</table> |", "| y |"], "TTT.."),
        (["| a |", "|---|", "| x |", '<span title="a|b">', "| y |"], "TTT.."),
        (["| a |", "|---|", "| x |", "<span> | x", "| y |"], "TTTTT"),
        (["| a |", "|---|", "| x |", "<!-- c | d -->"], "TTT."),
        (["| a |", "|---|", "| x |", "<textarea> |"], "TTT."),
        (["| a |", "|---|", "| x |", "<?php | ?>"], "TTT."),
        (["| a |", "|---|", "| x |", "<![CDATA[ | ]]>"], "TTT."),
        (["| `a|b` | c |", "|---|---|---|", "| 1 | 2 | [[A]] |"], "TTT"),
        (["| `a|b` | c |", "|---|---|"], ".."),
        (["[[x|y]] | c", "---|---"], ".."),
        (["a \\\\| b", "---|---", "[[A]] | x"], "TTT"),
        (["a \\| b", "---|---", "[[A]] | x"], "..."),
        (["| a \\\\| b \\\\|", "|---|---|", "| x | y |"], "TTT"),
        # t2-R2-T2: a pipe-less line after rows is a GFM lazy row.
        (["| a |", "|---|", "| x |", "[[A]]"], "TTTT"),
        (["| a |", "|---|", "| x |", "x \\| [[A]]", ""], "TTTT."),
        # t2-R2-T1: a row dedented below the open item's content column
        # closes the item and ends the table (trailing '' = final newline).
        (["- item", "", "  | a | b |", "  |---|---|", "| [[A]] | y |", ""], "..TT.."),
        (["- a", "  - b", "", "    | h |", "    |---|", "| [[A]] |", ""], "...TT.."),
        (["1.  item", "", "    | h |", "    |---|", "| [[A]] |", ""], "..TT.."),
        # Control: a header opening the item sets the floor itself.
        (["- | a | b |", "  |---|---|", "| x |", ""], "TT.."),
        # A sibling item at column 0 closes the earlier one: floor back to 0.
        (["- a", "- b", "", "| h |", "|---|", "| [[A]] |"], "...TTT"),
        # arb-F (pinned, no fix): a 4-space table right after a paragraph
        # line is a table (the header interrupts the paragraph).
        (["para", "    | a |", "    |---|", "    | [[A]] |"], ".TTT"),
        (["- | a |", "|---|", "[[A]] | x"], "..."),
        (["| a |", "    |---|", "[[A]] | x"], "..."),
        (["- | a |", "  |---|", "  | [[A]] |", "| y |"], "TTT."),
        (["| a |", "|---|", "    | x |"], "TT."),
        # No marker on the header line: rows are measured from the header's
        # own indent, floored at the open list item's content column (a
        # table in a list continuation).
        (["1.  item", "", "    | h |", "    |---|", "    | [[A]] |"], "..TTT"),
        (["- a", "  - b", "", "    | h |", "    |---|", "    | [[A]] |"], "...TTT"),
        (["  | a |", "|---|", "| x |"], "TTT"),
    )
    for lines, want in probes:
        got = "".join("T" if f else "." for f in F._table_line_flags("\n".join(lines)))
        assert got == want, (lines, got, want)
    print(f"PASS I5 table flags: {len(probes)} probes (CommonMark HTML starts 1-7 only, GFM cell "
          "split on every unescaped pipe with backslash parity, pipe-less line is a lazy row, "
          "list-item content column as floor for delimiter and rows, 4-column indent)")


def i5_openloops_verbatim(base):
    """Final-verify defect: an open-loops ledger list continuation holding the
    code span `slug|Display`, a plain continuation, then a list item linking
    [[60/30/10 mini-farm design]]. The old lazy-row
    heuristic escaped that link's pipe; no table is present, so it is plain."""
    lines = [
        "- [ ] [added 2026-01-02, trigger: 2026-01-16] Categorize moved pages.",
        "  - UPDATE: rewrite alias links to `slug|Display` form first; see regression R151.",
        "  - Keep the stubs until the remediation is applied, then delete them.",
        "- [ ] Review unresolved link targets ([[Double-digging]] x90, [[60/30/10 mini-farm design]] x82, ...).",
        "",
    ]
    before = "\n".join(lines)
    want = before.replace("[[60/30/10 mini-farm design]]",
                          "[[60-30-10-mini-farm-design|60/30/10 mini-farm design]]")
    assert want != before
    root = base / "i5-openloops"
    write_vault(root, {"wiki/concepts/60-30-10-mini-farm-design.md":
                       "---\naliases:\n  - 60/30/10 mini-farm design\n---\nbody\n",
                       "wiki/b.md": before})
    assert run(root, "--alias-only").returncode == 0
    p = run(root, "--alias-only", "--apply")
    assert p.returncode == 0, p.stdout + p.stderr
    got = (root / "wiki/b.md").read_bytes().decode("utf-8")
    assert got == want, ascii(got)
    print("PASS I5 open-loops ledger shape: code-span 'slug|Display' two lines up -> "
          "[[60-30-10-mini-farm-design|60/30/10 mini-farm design]] with a plain '|'")


def i9_slug(base):
    """h3-R3-I9-dotted-slug: a holder whose SLUG lint mangles (dotted name,
    or the ghost names date / published) is held for review, never written."""
    for name, holder, alias, kind in (("dotted", "python-3.12-notes", "Python notes", "broken"),
                                      ("ghost", "date", "Date page", "ghost")):
        root = base / ("i9-slug-" + name)
        write_vault(root, {f"wiki/concepts/{holder}.md": f"---\naliases: [{alias}]\n---\nx\n",
                           "wiki/a.md": f"see [[{alias}]] here\n"})
        ao0, br0 = lint_counts(root)
        gh0 = ghost_count(root)
        d = run(root, "--alias-only")
        assert d.returncode == 0, (name, d.stdout, d.stderr)
        for line in ("SAFE_REWRITES = 0", "REVIEW_REQUIRED = 1"):
            assert re.search("^" + re.escape(line) + "$", d.stdout, re.M), (name, line, d.stdout)
        (item,) = manifest(root)["review"]
        want = f"lint-mangled-slug (dotted, '/' or ':' name, or ghost: lint would file the rewritten link as {kind})"
        assert item["reason"] == want and item["category"] == "alias-unique-held", (name, item)
        before = snapshot(root)
        ap = run(root, "--alias-only", "--apply")
        assert ap.returncode == 0, (name, ap.returncode, ap.stdout, ap.stderr)
        assert snapshot(root) == before, name
        assert (root / "wiki/a.md").read_bytes() == f"see [[{alias}]] here\n".encode()
        assert lint_counts(root) == (ao0, br0) and ghost_count(root) == gh0, name
    print("PASS I9 slug: dotted slug python-3.12-notes and ghost slug 'date' -> REVIEW "
          "'lint-mangled-slug', SAFE 0, dry run + apply rc 0, no bytes changed, lint BROKEN/GHOST unchanged")


def i8_cross_run(base):
    """h3-R3-DS-1: a failed apply stays visible after a new dry run issues a
    new run_id -- the dry run warns, --apply refuses (2), --verify fails (1);
    after --restore of the OLD run_id all three pass again."""
    root = base / "i8-cross"
    write_vault(root, {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": "a [[Soil pH]]\n",
                       "wiki/b.md": "b [[Soil pH]]\n", "wiki/c.md": "c [[Soil pH]]\n"})
    orig = snapshot(root)
    assert run(root, "--alias-only").returncode == 0
    rid1 = manifest(root)["run_id"]
    p = run(root, "--alias-only", "--apply", "--batch-size", "1", env_extra={A.FAULT_ENV: "wiki/b.md"})
    assert p.returncode == 3 and "broken count rose" in p.stdout, p.stdout + p.stderr
    p = run(root, "--alias-only", "--apply")
    assert p.returncode == 2 and "did not complete" in p.stderr, p.stdout + p.stderr
    d = run(root, "--alias-only")
    assert d.returncode == 0, d.stdout + d.stderr
    rid2 = manifest(root)["run_id"]
    assert rid2 != rid1, "fixture did not produce a new run_id"
    assert (f"WARNING: unfinished apply {rid1} (postcondition-failed) still has 2 files as it "
            f"wrote them") in d.stdout, d.stdout
    mm = RESTORE_RE.search(d.stdout)
    assert mm and mm.group(3) == rid1, d.stdout
    failed_state = snapshot(root)
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 2, (ap.returncode, ap.stdout, ap.stderr)
    assert "did not complete and still have writes" in ap.stderr and rid1 in ap.stdout, ap.stdout + ap.stderr
    assert snapshot(root) == failed_state, "refused apply wrote something"
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 1 and "VERIFY FAIL" in v.stdout and rid1 in v.stdout, v.stdout
    r = run(root, "--alias-only", "--restore", rid1)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout, r.stdout
    assert all(snapshot(root)[k] == b for k, b in orig.items()), "restore not byte-exact"
    d = run(root, "--alias-only")
    assert d.returncode == 0 and "WARNING: unfinished" not in d.stdout, d.stdout
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 0 and "Applied: 3 edits" in ap.stdout, ap.stdout + ap.stderr
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 0 and "VERIFY OK" in v.stdout, v.stdout
    print("PASS I8 cross-run: after a failed apply a new dry run (new run_id) warns naming the old "
          "run_id + its RESTORE, --apply exits 2 with no writes, --verify exits 1; after --restore "
          "of the old run_id dry run / apply / verify all pass")


def pages(root):
    return {k: v for k, v in snapshot(root).items() if k.startswith("wiki/")}


def cross_vault(root):
    write_vault(root, {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": "a [[Soil pH]]\n",
                       "wiki/b.md": "b [[Soil pH]]\n", "wiki/c.md": "c [[Soil pH]]\n"})
    orig = snapshot(root)
    assert run(root, "--alias-only").returncode == 0
    rid = manifest(root)["run_id"]
    p = run(root, "--alias-only", "--apply", "--batch-size", "1", env_extra={A.FAULT_ENV: "wiki/b.md"})
    assert p.returncode == 3 and "broken count rose" in p.stdout, p.stdout + p.stderr
    return orig, rid, p


def i8_restore_settles(base):
    """t1-DS1-a: a clean restore settles its run (result 'restored'), so a
    LATER apply of another run_id writing the same bytes to the same page is
    never reported as the old run's unfinished write, and the old RESTORE is
    never offered against it. Belt and braces: even a log left unsettled
    (pre-fix ledger) is not live for a page a later complete apply wrote."""
    root = base / "i8-settle"
    orig, rid_a, _ = cross_vault(root)
    r = run(root, "--alias-only", "--restore", rid_a)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout, r.stdout
    log_a = bdir(root, rid_a) / "apply-log.json"
    assert json.loads(log_a.read_text(encoding="utf-8"))["result"] == "restored"
    (root / "wiki/d.md").write_bytes(b"d [[Soil pH]]\n")
    d = run(root, "--alias-only")
    rid_b = manifest(root)["run_id"]
    assert d.returncode == 0 and rid_b != rid_a and "WARNING" not in d.stdout, d.stdout
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 0 and "Applied: 4 edits" in ap.stdout, ap.stdout + ap.stderr
    applied = pages(root)
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 0 and "VERIFY OK" in v.stdout and "WARNING" not in v.stdout, v.stdout
    d = run(root, "--alias-only")
    assert d.returncode == 0 and "WARNING" not in d.stdout and "RESTORE:" not in d.stdout, d.stdout
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 0 and NOTHING in ap.stdout and "WARNING" not in ap.stdout, ap.stdout
    assert pages(root) == applied
    # Belt and braces: un-settle A's log by hand (the pre-fix ledger shape);
    # B's later complete log records the same post hashes -> still not live.
    lg = json.loads(log_a.read_text(encoding="utf-8"))
    log_a.write_text(json.dumps(dict(lg, result="postcondition-failed")), encoding="utf-8")
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 0 and "WARNING" not in v.stdout, v.stdout
    assert pages(root) == applied
    print("PASS I8 restore settles: fail A, restore A (result 'restored'), new file -> run B "
          "applies 4, verify OK, a second dry run + apply is 'Nothing to do' with no WARNING; an "
          "unsettled A log is not live for pages a later complete apply wrote")


def i8_unreadable_log(base):
    """t1-DS1-b: an unreadable CURRENT log blocks with its own message and a
    working manual remedy (no RESTORE that would refuse); a corrupt rotated
    prev log of a COMPLETE run blocks nothing."""
    root = base / "i8-unreadable"
    write_vault(root, {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": "a [[Soil pH]]\n"})
    assert run(root, "--alias-only").returncode == 0
    rid = manifest(root)["run_id"]
    assert run(root, "--alias-only", "--apply").returncode == 0
    bd = bdir(root, rid)
    log = bd / "apply-log.json"
    log.write_bytes(log.read_bytes()[:40])
    (root / "wiki/b.md").write_bytes(b"b [[Soil pH]]\n")
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 1 and f"WARNING: apply {rid} has an unreadable log" in v.stdout, v.stdout
    assert "VERIFY FAIL: 1 apply run(s) have an unreadable apply log" in v.stdout, v.stdout
    assert "still have writes" not in v.stdout and "RESTORE:" not in v.stdout, v.stdout
    d = run(root, "--alias-only")
    assert d.returncode == 0 and "unreadable log" in d.stdout and "RESTORE:" not in d.stdout, d.stdout
    assert f"{rid}-inspected" in d.stdout, d.stdout
    before = snapshot(root)
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 2 and "unreadable apply log -- nothing written" in ap.stderr, ap.stdout + ap.stderr
    assert "did not complete" not in ap.stderr and snapshot(root) == before
    # The printed remedy, exactly: rename the run directory aside.
    assert f"renaming that directory to {rid}-inspected (or moving it out of {bd.parent})" in d.stdout, d.stdout
    os.replace(bd, bd.with_name(f"{rid}-inspected"))
    d = run(root, "--alias-only")
    assert d.returncode == 0 and "WARNING" not in d.stdout, d.stdout
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 0 and "Applied: 1 edits" in ap.stdout, ap.stdout + ap.stderr
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 0 and "VERIFY OK" in v.stdout, v.stdout
    # A corrupt rotated prev log beside a COMPLETE current log: not read.
    root = base / "i8-corrupt-prev"
    write_vault(root, {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": "a [[Soil pH]]\n"})
    assert run(root, "--alias-only").returncode == 0
    rid = manifest(root)["run_id"]
    assert run(root, "--alias-only", "--apply").returncode == 0
    (bdir(root, rid) / "apply-log.prev-20260101T000000000000.json").write_bytes(b"{corrupt")
    (root / "wiki/b.md").write_bytes(b"b [[Soil pH]]\n")
    d = run(root, "--alias-only")
    assert d.returncode == 0 and "WARNING" not in d.stdout and manifest(root)["run_id"] != rid, d.stdout
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 0 and "Applied: 1 edits" in ap.stdout, ap.stdout + ap.stderr
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 0 and "VERIFY OK" in v.stdout, v.stdout
    print("PASS I8 unreadable log: truncated current log -> verify 1 / apply 2 with the "
          "unreadable-log wording, no RESTORE line; renaming the run dir to <rid>-inspected (the "
          "printed remedy) clears it and apply / verify pass; a corrupt prev log of a complete run "
          "blocks nothing")


def i8_set_aside(base):
    """t2-DS1-unsettleable-live-run + arb-F-readme-remedy-undocumented: a
    failed apply whose backup went missing cannot be settled by RESTORE.
    RESTORE exits 1 'backup-missing' AND prints the set-aside remedy; the
    next dry run repeats RESTORE plus the remedy; following the remedy
    (rename to <rid>-inspected) clears the guard: dry run no WARNING,
    --apply rc 0, --verify OK."""
    root = base / "i8-set-aside"
    _, rid, _ = cross_vault(root)
    bd = bdir(root, rid)
    lg = json.loads((bd / "apply-log.json").read_text(encoding="utf-8"))
    (state_of(root) / lg["files"]["wiki/a.md"]["backup"]).unlink()
    for _ in range(2):
        r = run(root, "--alias-only", "--restore", rid)
        assert r.returncode == 1 and "backup-missing" in r.stdout, r.stdout + r.stderr
        assert "SET ASIDE:" in r.stdout and f"{rid}-inspected" in r.stdout, r.stdout
    d = run(root, "--alias-only")
    assert d.returncode == 0 and f"WARNING: unfinished apply {rid}" in d.stdout, d.stdout
    assert "RESTORE:" in d.stdout and f"{rid}-inspected" in d.stdout, d.stdout
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 2 and f"{rid}-inspected" in ap.stdout, ap.stdout + ap.stderr
    # The remedy, exactly as printed: a.md keeps the (correct) committed write.
    assert f"renaming {bd} to {rid}-inspected (or moving it out of {bd.parent})" in ap.stdout, ap.stdout
    os.replace(bd, bd.with_name(f"{rid}-inspected"))
    d = run(root, "--alias-only")
    assert d.returncode == 0 and "WARNING" not in d.stdout, d.stdout
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 0 and "Applied: 2 edits" in ap.stdout, ap.stdout + ap.stderr
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 0 and "VERIFY OK" in v.stdout, v.stdout
    # arb-F-readme-remedy-undocumented: README item 3 names the remedy.
    readme = (SCRIPTS / "README.md").read_text(encoding="utf-8")
    assert ("When RESTORE cannot settle a run" in readme
            and "rename `<state dir>/backup/<run_id>` to `<run_id>-inspected`" in readme
            and "`SET ASIDE:`" in readme), "README item 3 lacks the set-aside remedy"
    print("PASS I8 set aside: failed apply + deleted backup -> RESTORE rc 1 'backup-missing' with the "
          "SET ASIDE remedy (twice), dry run repeats RESTORE + remedy, apply 2; renaming to "
          "<rid>-inspected -> dry run clean, apply 2 edits, verify OK")


def i8_corrupt_prev(base):
    """t2-DS1-same-rid-corrupt-prev-silent-wedge: a complete + restored run
    with a corrupt rotated prev log no longer wedges --apply of the same
    run_id (NOTE, rc 0). t2-DS1-printed-restore-refuses: a failed apply with
    a corrupt prev log -- the dry run's printed RESTORE restores the live
    files, lists the unreadable log REVIEW_REQUIRED (rc 1) with the remedy,
    and the remedy clears the guard."""
    prev = "apply-log.prev-20260101T000000000000.json"
    root = base / "i8-same-rid-prev"
    write_vault(root, {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": "a [[Soil pH]]\n"})
    assert run(root, "--alias-only").returncode == 0
    rid = manifest(root)["run_id"]
    assert run(root, "--alias-only", "--apply").returncode == 0
    r = run(root, "--alias-only", "--restore", rid)
    assert r.returncode == 0 and "RESTORED = 1" in r.stdout, r.stdout
    (bdir(root, rid) / prev).write_bytes(b"{corrupt")
    d = run(root, "--alias-only")
    assert d.returncode == 0 and "WARNING" not in d.stdout and manifest(root)["run_id"] == rid, d.stdout
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 0 and "Applied: 1 edits" in ap.stdout, ap.stdout + ap.stderr
    assert f"NOTE: unreadable rotated log {prev} skipped" in ap.stdout, ap.stdout
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 0 and "VERIFY OK" in v.stdout, v.stdout
    root = base / "i8-restore-prev"
    orig, rid, _ = cross_vault(root)
    bd = bdir(root, rid)
    (bd / prev).write_bytes(b"{corrupt")
    d = run(root, "--alias-only")
    assert d.returncode == 0 and f"WARNING: unfinished apply {rid}" in d.stdout, d.stdout
    r = run_printed_restore(d.stdout)
    assert r.returncode == 1 and "RESTORED = 2" in r.stdout, r.stdout + r.stderr
    assert f"REVIEW_REQUIRED   {prev}  (unreadable rotated apply log" in r.stdout, r.stdout
    assert "SET ASIDE:" in r.stdout and f"{rid}-inspected" in r.stdout, r.stdout
    assert all(snapshot(root)[k] == b for k, b in orig.items()), "restore not byte-exact"
    assert json.loads((bd / "apply-log.json").read_text(encoding="utf-8"))["result"] != "restored"
    os.replace(bd, bd.with_name(f"{rid}-inspected"))
    d = run(root, "--alias-only")
    assert d.returncode == 0 and "WARNING" not in d.stdout, d.stdout
    ap = run(root, "--alias-only", "--apply")
    assert ap.returncode == 0 and "Applied: 3 edits" in ap.stdout, ap.stdout + ap.stderr
    v = run(root, "--alias-only", "--verify")
    assert v.returncode == 0 and "VERIFY OK" in v.stdout, v.stdout
    print("PASS I8 corrupt prev: settled run + corrupt prev -> same-run_id --apply rc 0 with the "
          "NOTE; failed apply + corrupt prev -> printed RESTORE restores 2, rc 1 with the "
          "unreadable-log review row + SET ASIDE remedy, and the remedy clears the guard")


# The legacy in-vault-state tool (in-vault state): the source of real legacy state fixtures.
LEGACY_TOOL = "1c48b84"


def legacy_tool(base):
    """The pinned legacy in-vault-state scripts in their own dir (not in any vault)."""
    sd = base / "legacy-tool" / "scripts"
    if not sd.is_dir():
        sd.mkdir(parents=True)
        for n in ("fix_wikilinks.py", "_wikilib.py", "maintenance_preflight.py", "lint.py"):
            (sd / n).write_bytes(git_show(LEGACY_TOOL, "scripts/" + n))
    assert b"--state-dir" not in (sd / "fix_wikilinks.py").read_bytes(), "legacy pin is not legacy"
    return sd / "fix_wikilinks.py"


def i8_moved_vault(base):
    """arb-M1-moved-vault-wedge, with LEGACY in-vault state (the only state
    that moves with the folder): the legacy in-vault-state tool fails an apply, then the
    vault folder is renamed. The RESTORE printed by the new tool's dry run
    (new --root) restores from the legacy location with a NOTE; the same
    run_id cannot be applied until --migrate-legacy-state moves it (one run,
    one location); after migration dry run / apply / verify pass."""
    old_tool = legacy_tool(base)
    root = base / "i8-moved-v1"
    write_vault(root, {"wiki/concepts/soil-ph.md": SOIL, "wiki/a.md": "a [[Soil pH]]\n",
                       "wiki/b.md": "b [[Soil pH]]\n", "wiki/c.md": "c [[Soil pH]]\n"})
    orig = snapshot(root)
    assert run(root, "--alias-only", script=old_tool, state=False).returncode == 0
    rid = json.loads((root / "_meta/alias-fix-manifest.json").read_text(encoding="utf-8"))["run_id"]
    p = run(root, "--alias-only", "--apply", "--batch-size", "1", script=old_tool, state=False,
            env_extra={A.FAULT_ENV: "wiki/b.md"})
    assert p.returncode == 3 and (root / ".alias-fix-backup" / rid).is_dir(), p.stdout + p.stderr
    orig = {k: v for k, v in orig.items()}
    moved = base / "i8-moved-v1-moved"
    shutil.move(str(root), str(moved))
    d = run(moved, "--alias-only")
    assert d.returncode == 0 and f"WARNING: unfinished apply {rid}" in d.stdout, d.stdout
    assert "NOTE: legacy in-vault state" in d.stdout, d.stdout
    r = run_printed_restore(d.stdout)
    assert r.returncode == 0 and "RESTORED = 2" in r.stdout and "REVIEW_REQUIRED = 0" in r.stdout, \
        r.stdout + r.stderr
    old_root = json.loads((moved / ".alias-fix-backup" / rid / "apply-log.json")
                          .read_text(encoding="utf-8"))["root"]
    assert old_root == os.path.normcase(os.path.normpath(os.path.abspath(str(root)))), old_root
    assert f"NOTE: vault moved from {old_root} " in r.stdout, r.stdout
    pages_now = {k: v for k, v in snapshot(moved).items() if k.startswith("wiki/")}
    assert all(pages_now[k] == b for k, b in orig.items() if k.startswith("wiki/")), \
        "restore not byte-exact"
    d = run(moved, "--alias-only")
    assert d.returncode == 0 and "WARNING" not in d.stdout, d.stdout
    assert manifest(moved)["run_id"] == rid, "fixture: the restored vault re-plans the same run_id"
    ap = run(moved, "--alias-only", "--apply")
    assert ap.returncode == 2 and "--migrate-legacy-state --apply first" in ap.stderr, ap.stdout + ap.stderr
    mg = run(moved, "--alias-only", "--migrate-legacy-state", "--apply")
    assert mg.returncode == 0 and "LEGACY STATE: 0 after migration -- zero" in mg.stdout, mg.stdout + mg.stderr
    assert not (moved / ".alias-fix-backup").exists() and not (moved / "_meta/alias-fix-manifest.json").exists()
    ap = run(moved, "--alias-only", "--apply")
    assert ap.returncode == 0 and "Applied: 3 edits" in ap.stdout, ap.stdout + ap.stderr
    v = run(moved, "--alias-only", "--verify")
    assert v.returncode == 0 and "VERIFY OK" in v.stdout, v.stdout
    print("PASS I8 moved vault (legacy state from the legacy in-vault-state tool): failed apply, vault renamed, the "
          "dry run's printed RESTORE restores 2 from the legacy location with 'NOTE: vault moved "
          "from'; the same run_id is refused until --migrate-legacy-state, then apply 3 / verify pass")


def main():
    with tempfile.TemporaryDirectory(prefix="wikillm151inv-") as td:
        base = Path(td)
        A.isolate_state(base)
        for case in (i1, i1_unplanned, i2, i2_abbrev, i3, i4, i5, i5_lazy_table, i5_table_flags,
                     i5_openloops_verbatim, i6, i7, i8, i8_cross_run, i8_restore_settles,
                     i8_unreadable_log, i8_set_aside, i8_corrupt_prev, i8_moved_vault, i8_locks, i9, i9_extra, i9_slug):
            case(base)
    print("OK -- R151 acceptance invariants I1-I9 passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
