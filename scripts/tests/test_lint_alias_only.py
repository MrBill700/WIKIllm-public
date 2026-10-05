#!/usr/bin/env python3
"""Regression test for regression R151 -- lint.py's ALIAS-ONLY LINKS section.

Obsidian does NOT resolve a bare [[alias]] link (a click test: clicking one creates a
new empty note). But _wikilib.build_index
indexes frontmatter aliases and lint counted alias-matched links as resolved
AND credited them inbound -- in one large vault over 90% of lint-resolved
links were alias-only, i.e. grey in Obsidian, while lint reported health.
The fix keeps build_index/resolve unchanged and classifies each resolved hit
with _wikilib.canonical_paths(): basename hits are edges; alias-only hits get
their own section and no inbound credit.

Cases (one fixture vault, the CURRENT scripts copied in, lint run from the
vault root exactly as the fleet runs it):
  C1  canonical [[soil-ph]], [[Soil-PH]] (case), [[soil-ph.md]],
      [[concepts/soil-ph]], [[soil-ph#Liming|pH]] -> not ALIAS-ONLY, not
      BROKEN, soil-ph.md is not an orphan (inbound credited)
  A1  alias-only [[Soil Acidity]] x2, [[Soil Acidity|acid]],
      [[Soil Acidity#Liming]] -> ALIAS-ONLY, NOT BROKEN, and its holder
      acidity-notes.md gets NO inbound -> it is an ORPHAN
  A2  collision: widgets.md exists AND gadget-notes.md carries alias
      "widgets" -> [[widgets]] credits only widgets.md; gadget-notes.md is
      an orphan; the link is NOT alias-only (Obsidian opens widgets.md)
  N1  backticked `[[Soil Acidity]]`, ``[[Soil Acidity]]`` and a fenced
      block holding [[Soil Acidity]] are documentation -> not counted
  B1  a truly broken [[nowhere-page]] stays in BROKEN, unchanged format
  S1  --summary prints the ALIAS-ONLY header with counts and no rows
  H1  exact header text
  P1  maintenance_preflight.parse_lint reads alias_only_links /
      alias_only_link_targets; with the line removed both are None and the
      verdict (reason) is unchanged -- an older lint is never a blocker
      A partial parse (ORPHANS line missing) still carries both keys as None
  L1  library: link_kind / canonical_paths / basename_keys, and resolve()
      still reports an alias-only hit as "resolved" (return values unchanged);
      vault_md_basenames prunes dot-directories and, passed to link_kind,
      makes an off-inventory basename that is also an alias 'canonical'
  R1-R4 (V2 fixture) raw/ + templates/ basenames canonical; alias-only ../
      link not rescued, off-inventory ../ basename IS rescued; BOM holder
      indexed; a .trash/ basename does not rescue an alias-only link

RED control (automated): lint.py + _wikilib.py from c376aaa (pre-alias-only) run on
the V1 fixture must FAIL H1 (no ALIAS-ONLY header) and A1 (the alias holder
earns inbound, so it is not an orphan). Skipped with a printed note when git
or the commit is unavailable.

Run: python -B scripts/tests/test_lint_alias_only.py    Exit 0 = pass.
"""
from __future__ import annotations

from legacy_fixture import legacy_result
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
fails: list[str] = []
HEADER = ("ALIAS-ONLY LINKS (resolve only via frontmatter aliases -- grey in Obsidian,"
          " regression R151)")
ORPH = "ORPHANS (zero inbound wikilinks)"


def check(cond: bool, msg: str) -> None:
    if not cond:
        fails.append(msg)


def page(title: str, aliases: list[str] | None = None, body: str = "") -> str:
    fm = f"---\ntitle: {title}\nupdated: 2026-09-29\n"
    if aliases:
        fm += "aliases:\n" + "".join(f"  - {a}\n" for a in aliases)
    return fm + f"---\n\n# {title}\n\n## Liming\n\n{body}"


def build(base: Path) -> Path:
    v = base / "Vault"
    for d in ("scripts", "_meta", "raw", "wiki/concepts", "wiki/entities"):
        (v / d).mkdir(parents=True, exist_ok=True)
    for s in ("lint.py", "_wikilib.py", "audit_claims.py", "suggest_anchors.py"):
        shutil.copy2(SCRIPTS / s, v / "scripts" / s)
    (v / "CLAUDE.md").write_text("# Vault\n\n[[index]]\n", encoding="utf-8")
    w = v / "wiki"
    (w / "index.md").write_text(page(
        "Index", body=(
            "C1 [[soil-ph]] [[Soil-PH]] [[soil-ph.md]] [[concepts/soil-ph]]"
            " [[soil-ph#Liming|pH]]\n"
            "A1 [[Soil Acidity]] and [[Soil Acidity|acid]] and [[Soil Acidity#Liming]]\n"
            "A2 [[widgets]]\n"
            "B1 [[nowhere-page]]\n"
            "N1 `[[Soil Acidity]]` and ``[[Soil Acidity]]``\n"
            "```\n[[Soil Acidity]]\n```\n"
            "back to [[log]] [[overview]]\n")), encoding="utf-8")
    (w / "log.md").write_text(page("Log", body="A1 [[Soil Acidity]]\n[[index]]\n"),
                              encoding="utf-8")
    (w / "overview.md").write_text(page("Overview", body="[[index]]\n"), encoding="utf-8")
    (w / "concepts" / "soil-ph.md").write_text(page("Soil pH", body="[[index]]\n"),
                                              encoding="utf-8")
    (w / "concepts" / "acidity-notes.md").write_text(
        page("Acidity notes", aliases=["Soil Acidity"], body="[[index]]\n"),
        encoding="utf-8")
    (w / "entities" / "widgets.md").write_text(page("Widgets", body="[[index]]\n"),
                                              encoding="utf-8")
    (w / "entities" / "gadget-notes.md").write_text(
        page("Gadget notes", aliases=["widgets"], body="[[index]]\n"), encoding="utf-8")
    return v


def run_lint(root: Path, *extra: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, "-B", str(root / "scripts" / "lint.py"), *extra],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(root))
    return r.returncode, r.stdout + r.stderr


def header_line(out: str, section: str) -> str:
    m = re.search(r"^=== " + re.escape(section) + r"[^\n]*$", out, re.M)
    return m.group(0) if m else ""


def count(out: str, section: str) -> int:
    m = re.search(r"^=== " + re.escape(section) + r" === (\d+)", out, re.M)
    return int(m.group(1)) if m else -1


def block(out: str, section: str) -> str:
    m = re.search(r"^=== " + re.escape(section) + r"[^\n]*\n(.*?)(?=^===|\Z)",
                  out, re.M | re.S)
    return m.group(1) if m else ""


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wikillm151-"))
    try:
        v = build(tmp)
        rc, out = run_lint(v)
        check(rc == 0, f"exit code {rc} (contract: 0 once started)\n{out}")
        ao = block(out, HEADER)
        orph = block(out, ORPH)
        broken = block(out, "BROKEN LINKS")

        # H1 -- exact header, and it sits right after BROKEN LINKS.
        # Counted per occurrence: index has 3 (plain, |display, #anchor), log
        # has 1 -- all one distinct target once anchor/display are split off.
        check(header_line(out, HEADER) == f"=== {HEADER} === 4 (1 distinct targets)",
              f"H1: header line wrong: {header_line(out, HEADER)!r}\n{out}")
        heads = [l for l in out.splitlines() if l.startswith("=== ")]
        pos = [i for i, l in enumerate(heads) if l.startswith("=== BROKEN LINKS ===")]
        check(len(pos) == 1 and pos[0] + 1 < len(heads)
              and heads[pos[0] + 1].startswith("=== ALIAS-ONLY LINKS"),
              f"H1: ALIAS-ONLY must follow BROKEN LINKS directly: {heads}")

        # A1 -- listed, grouped by target like BROKEN (target text verbatim;
        # iter_wikilinks already split #anchor and |display off).
        check(re.search(r"^  x4\s+\[\[Soil Acidity\]\]  \(wiki/index\.md \+3 more\)$",
                        ao, re.M) is not None,
              f"A1: expected 'x4 [[Soil Acidity]] (wiki/index.md +3 more)' row:\n{ao}")
        check("[[Soil Acidity]]" not in broken and "Soil Acidity" not in broken,
              f"A1: alias-only link leaked into BROKEN:\n{broken}")
        check("wiki/concepts/acidity-notes.md" in orph,
              f"A1: alias holder earned inbound (should be an orphan):\n{orph}")

        # A2 -- basename+alias collision: only the basename file is credited.
        check("[[widgets]]" not in ao, f"A2: [[widgets]] wrongly alias-only:\n{ao}")
        check("wiki/entities/widgets.md" not in orph,
              f"A2: basename file widgets.md lost its inbound:\n{orph}")
        check("wiki/entities/gadget-notes.md" in orph,
              f"A2: alias holder gadget-notes.md double-credited (R150):\n{orph}")

        # C1 -- canonical forms.
        for t in ("soil-ph", "Soil-PH", "soil-ph.md", "concepts/soil-ph"):
            check(f"[[{t}]]" not in ao and f"[[{t}]]" not in broken,
                  f"C1: canonical [[{t}]] misreported:\n{out}")
        check("wiki/concepts/soil-ph.md" not in orph,
              f"C1: canonical target lost inbound:\n{orph}")

        # B1 -- BROKEN unchanged in format and content.
        check(header_line(out, "BROKEN LINKS") == "=== BROKEN LINKS === 1 (1 distinct targets)",
              f"B1: BROKEN header changed: {header_line(out, 'BROKEN LINKS')!r}")
        check(re.search(r"^  x1\s+\[\[nowhere-page\]\]  \(wiki/index\.md\)$", broken, re.M)
              is not None, f"B1: broken row missing:\n{broken}")

        # Orphans exactly: the two alias holders plus CLAUDE.md (nothing links
        # to it; index is linked from CLAUDE.md, everything else from index).
        check(count(out, ORPH) == 3, f"orphan count {count(out, ORPH)}, expected 3\n{orph}")

        # S1 -- --summary: header with counts, no rows.
        rc2, summ = run_lint(v, "--summary")
        check(rc2 == 0, f"S1: --summary exit {rc2}")
        check(header_line(summ, HEADER) == f"=== {HEADER} === 4 (1 distinct targets)"
              and "[[" not in block(summ, HEADER),
              f"S1: --summary header/rows wrong:\n{summ}")

        # P1 -- preflight parses the optional keys; absent -> None, no new reason.
        sys.path.insert(0, str(SCRIPTS))
        sys.dont_write_bytecode = True
        from maintenance_preflight import parse_lint
        counts, why = parse_lint(summ)
        check(counts.get("alias_only_links") == 4
              and counts.get("alias_only_link_targets") == 1,
              f"P1: parse_lint alias_only = {counts.get('alias_only_links')}/"
              f"{counts.get('alias_only_link_targets')} (why={why!r})")
        check(why is None, f"P1: fixture lint output did not parse cleanly: {why!r}\n{summ}")
        stripped = "\n".join(l for l in summ.splitlines()
                             if not l.startswith("=== ALIAS-ONLY"))
        counts2, why2 = parse_lint(stripped)
        check(why2 == why and "alias_only_links" in counts2
              and counts2["alias_only_links"] is None
              and counts2["alias_only_link_targets"] is None,
              f"P1: older-lint output: why {why!r} -> {why2!r}, counts {counts2}")
        # Partial parse: a required line (ORPHANS) missing returns early; the
        # optional R151 keys must still be present, as None (review 2-R2-T6).
        no_orph = "\n".join(l for l in summ.splitlines() if not l.startswith("=== ORPHANS"))
        counts3, why3 = parse_lint(no_orph)
        check(why3 is not None and "ORPHANS" in why3
              and "alias_only_links" in counts3 and counts3["alias_only_links"] is None
              and "alias_only_link_targets" in counts3
              and counts3["alias_only_link_targets"] is None,
              f"P1: partial parse lost the optional keys: why={why3!r} counts={counts3}")

        # L1 -- library surface; resolve() return values unchanged.
        from _wikilib import (basename_keys, build_index, canonical_paths,
                              link_kind, resolve)
        paths = ["wiki/a/real-page.md", "wiki/b/holder.md", "wiki/c/Other.md"]
        texts = {"wiki/b/holder.md": "---\naliases: [Real Page Alias, other]\n---\n"}
        idx = build_index(paths, texts)
        st, cands = resolve("Real Page Alias", "wiki/x.md", idx)
        check(st == "resolved" and cands == ["wiki/b/holder.md"],
              f"L1: resolve() changed for an alias hit: {st} {cands}")
        check(link_kind("Real Page Alias", cands) == "alias-only",
              "L1: alias hit not classified alias-only")
        st, cands = resolve("REAL-PAGE.md", "wiki/x.md", idx)
        check(link_kind("REAL-PAGE.md", cands) == "canonical",
              "L1: case/.md basename hit not canonical")
        st, cands = resolve("other", "wiki/x.md", idx)
        check(sorted(cands) == ["wiki/b/holder.md", "wiki/c/Other.md"]
              and canonical_paths("other", cands) == ["wiki/c/Other.md"],
              f"L1: collision canonical_paths wrong: {cands}")
        check(basename_keys(paths) == {"real-page", "holder", "other"},
              f"L1: basename_keys {basename_keys(paths)}")
        # L1b (review 2-R2-L1): the documented API gives lint's answer for an
        # off-inventory basename that is also an alias, once vault_basenames
        # is passed; the default call is unchanged (inventory only).
        from _wikilib import vault_md_basenames
        lv = tmp / "L1v"
        for rel in ("raw/ingested/rawname.md", "wiki/concepts/soil-ph.md",
                    ".trash/trashed.md", ".obsidian/x.md", "wiki/chart.png"):
            (lv / rel).parent.mkdir(parents=True, exist_ok=True)
            (lv / rel).write_text("x\n", encoding="utf-8")
        vb = vault_md_basenames(str(lv))
        check(vb == {"rawname", "soil-ph"}, f"L1b: vault_md_basenames {vb}")
        holder = ["wiki/concepts/soil-ph.md"]  # carries alias 'rawname'
        check(link_kind("rawname", holder) == "alias-only",
              "L1b: default link_kind changed (must ignore off-index files)")
        check(link_kind("rawname", holder, vb) == "canonical",
              "L1b: link_kind(vault_basenames) not canonical for a raw/ basename")
        check(link_kind("trashed", holder, vb) == "alias-only",
              "L1b: a .trash/ basename made an alias-only link canonical")

        # R1 (review lint 1-F1) -- an alias that is also the basename of an
        # off-inventory .md (raw/, templates/) is canonical in Obsidian, not
        # alias-only. R2 (lint 1-F2) -- a ../ link matched only via an alias
        # is not "basename-rescued". M1 -- a BOM before `---` does not hide an
        # alias holder's frontmatter (parity with fix_wikilinks' Inventory).
        v2 = tmp / "V2"
        for d in ("scripts", "wiki", "raw", "templates"):
            (v2 / d).mkdir(parents=True, exist_ok=True)
        for s in ("lint.py", "_wikilib.py", "audit_claims.py", "suggest_anchors.py"):
            shutil.copy2(SCRIPTS / s, v2 / "scripts" / s)
        (v2 / "raw" / "Rawnote.md").write_text("clip\n", encoding="utf-8")
        (v2 / "templates" / "Tmplnote.md").write_text("tmpl\n", encoding="utf-8")
        # R4: a dot-directory file is not a vault page (Obsidian's .trash/,
        # the fixer's legacy in-vault .alias-fix-backup/ -- legacy in-vault layout,
        # readable until --migrate-legacy-state; new state lives outside the
        # vault, ADR-0005): its basename must not rescue.
        (v2 / ".trash").mkdir()
        (v2 / ".trash" / "Ghost.md").write_text("deleted\n", encoding="utf-8")
        (v2 / "wiki" / "holder.md").write_text(
            page("Holder", aliases=["Rawnote", "Tmplnote", "Holder Name", "Ghost"]),
            encoding="utf-8")
        (v2 / "wiki" / "bomholder.md").write_bytes(
            b"\xef\xbb\xbf---\naliases: [Zed]\n---\nbody\n")
        (v2 / "wiki" / "index.md").write_text(
            "[[Rawnote]] [[Tmplnote]] [[../nowhere/Holder Name]] [[Zed]] [[Ghost]]"
            " [[../nowhere/Rawnote]]\n", encoding="utf-8")
        rc3, out3 = run_lint(v2)
        ao3 = block(out3, HEADER)
        check(rc3 == 0 and count(out3, HEADER) == 3, f"R1/M1: ALIAS-ONLY count {count(out3, HEADER)} != 3\n{out3}")
        check("[[Rawnote]]" not in ao3 and "[[Tmplnote]]" not in ao3
              and "[[../nowhere/Rawnote]]" not in ao3,
              f"R1: raw/ or templates/ basename reported alias-only:\n{ao3}")
        check("[[Zed]]" in ao3 and count(out3, "BROKEN LINKS") == 0,
              f"M1: BOM-prefixed alias holder not indexed (Zed broken):\n{out3}")
        check("[[Ghost]]" in ao3, f"R4: .trash/ basename made [[Ghost]] canonical:\n{ao3}")
        rd3 = block(out3, "RELATIVE-DEPTH LINKS")
        check("[[../nowhere/Holder Name]]" in ao3
              and re.search(r"^\s*x1\s+\[\[\.\./nowhere/Holder Name\]\][^\n]*also broken\]$",
                            rd3, re.M) is not None,
              f"R2: alias-only ../ link reported as basename-rescued:\n{rd3}")
        # R3 (review 2-R2-T6): a ../ link whose last segment is an off-
        # inventory basename (raw/) that is ALSO an alias IS rescued --
        # Obsidian opens raw/Rawnote.md.
        check(re.search(r"^\s*x1\s+\[\[\.\./nowhere/Rawnote\]\][^\n]*basename-rescued\]$",
                        rd3, re.M) is not None,
              f"R3: off-inventory ../ basename not basename-rescued:\n{rd3}")

        # BOM1 (review 3-R3-L3): lint now reads pages as utf-8-sig, so a BOM
        # no longer hides a page's frontmatter. That moves ALL FILES, BROKEN
        # and ORPHANS for BOM-prefixed pages (documented in README), not only
        # ALIAS-ONLY: a BOM-prefixed _meta/ auto_generated page is now
        # dropped from the file set. Pinned: pre-alias-only lint 3/1/1 (below, in
        # the RED block), this lint 2/0/0.
        v3 = tmp / "V3"
        for d in ("scripts", "wiki", "_meta"):
            (v3 / d).mkdir(parents=True, exist_ok=True)
        for s in ("lint.py", "_wikilib.py", "audit_claims.py", "suggest_anchors.py"):
            shutil.copy2(SCRIPTS / s, v3 / "scripts" / s)
        (v3 / "_meta" / "sugg.md").write_bytes(
            b"\xef\xbb\xbf---\nauto_generated: true\n---\n[[Nope]] [[b]]\n")
        (v3 / "wiki" / "a.md").write_text("[[b]]\n", encoding="utf-8")
        (v3 / "wiki" / "b.md").write_text("[[a]]\n", encoding="utf-8")
        rc4, out4 = run_lint(v3, "--summary")
        bom_counts = (count(out4, "ALL FILES"), count(out4, "BROKEN LINKS"), count(out4, ORPH))
        check(rc4 == 0 and bom_counts == (2, 0, 0),
              f"BOM1: BOM-prefixed auto_generated _meta page not dropped: {bom_counts}\n{out4}")

        # RED control (review 2-R2-D1): the pre-alias-only lint + _wikilib on the V1
        # fixture must fail H1 and A1, or this test pins nothing.
        red_src = {}
        for s in ("lint.py", "_wikilib.py"):
            g = legacy_result(f"c376aaa:scripts/{s}")
            if g.returncode != 0:
                red_src = None
                break
            red_src[s] = g.stdout
        if red_src is None:
            print("NOTE: RED control skipped (git or commit c376aaa unavailable)")
        else:
            vr = tmp / "red" / "Vault"
            shutil.copytree(v, vr)
            for s, b in red_src.items():
                (vr / "scripts" / s).write_bytes(b)
            rcr, outr = run_lint(vr)
            check(rcr == 0, f"RED: pre-alias-only lint did not run:\n{outr}")
            check(header_line(outr, HEADER) == "",
                  f"RED: pre-alias-only lint already prints the H1 header?\n{outr}")
            check("wiki/concepts/acidity-notes.md" not in block(outr, ORPH),
                  f"RED: pre-alias-only lint already orphans the alias holder (A1 pins nothing):\n{outr}")
            print("RED control: pre-alias-only lint fails H1 (no header) and A1 (holder credited)")
            v3r = tmp / "red" / "V3"
            shutil.copytree(v3, v3r)
            for s, b in red_src.items():
                (v3r / "scripts" / s).write_bytes(b)
            rc5, out5 = run_lint(v3r, "--summary")
            old_counts = (count(out5, "ALL FILES"), count(out5, "BROKEN LINKS"), count(out5, ORPH))
            check(rc5 == 0 and old_counts == (3, 1, 1),
                  f"BOM1 RED: pre-alias-only lint on the BOM fixture gave {old_counts}, pinned (3, 1, 1)\n{out5}")
            print(f"BOM1: pre-alias-only lint {old_counts} -> this lint {bom_counts} (ALL FILES, BROKEN, ORPHANS)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print(f"FAIL ({len(fails)})\n  " + "\n  ".join(fails))
        return 1
    print("PASS: alias-only links get their own section after BROKEN (exact header), "
          "earn no inbound (holders become orphans), a basename+alias collision credits "
          "only the basename file, canonical/case/.md/path/anchor forms stay healthy, "
          "code spans ignored, BROKEN unchanged, --summary keeps the header, preflight "
          "reads the optional keys (None when absent), resolve() return values unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
